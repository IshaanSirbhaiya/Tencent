"""The plan compiler — the part that makes delegation safe (AGENT_PLANE §8a / D14).

A model asked for "training data" produces a leaky split with confidence, and it does so because
every wrong answer here is the *intuitive* answer: "as of the event" reads like the careful
choice, and it is the one that leaks. Feast's own default point-in-time join admits values
backfilled after the event (R05 §1.2), which is the same mistake shipped as a product default. The
compiler is therefore not ceremony around a model's output — it is the reason a model may be
trusted to draft a plan at all.

Each rule below refuses exactly one thing a model gets wrong:

| Invariant | Refused when |
|---|---|
| features cut on **system time** | any other axis is named for a feature |
| labels on **valid time**, read at latest system time, with a **maturity lag** | lag missing, or labels cut on system time |
| **policy is inside `plan_hash`** | the same query under a different policy would share a name |
| every cell carries a pointer | a plan tries to disable it |
| abstention budget feasibility-tested | a budget is asserted without the test |

Plus three that fall out of the same reasoning and cost nothing to check: no imputation, a
re-executable determinism block, and — the known blocker — a projection whose policy would emit a
*transformed* disclosure, which no receipt can currently describe honestly (spine §9 O1b).

**What the compiler does not do.** It does not read data, it does not resolve a pointer and it
never calls a model. It takes a plan and, optionally, the policy's own compiled decision table for
the reads that plan implies, and returns a `CompiledPlan` or raises. That is what lets the same
function guard both pathways: the deterministic materialiser compiles before it reads, and the
agent pathway compiles the plan it authored before it shapes anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from databridge_adapter import CompiledDecisionTable

from .errors import (
    AbstentionNotFeasible,
    AirGapUnpinned,
    DeterminismIncomplete,
    FeatureAxisRefused,
    ImputationRefused,
    LabelAxisRefused,
    MaturityLagMissing,
    NoSystemTimeAxis,
    PointerRequirementDisabled,
    PolicyNotInHash,
    TransformedDisclosureUnrepresentable,
)
from .feasibility import Feasibility, assess
from .plan import LABELLED_SHAPES, Column, MaterialisationPlan

#: Membership shapes have no per-row decision instant, so `feature_as_of: per_row` is meaningless
#: for them and `corpus_as_of` carries the cut instead.
MEMBERSHIP_SHAPES: frozenset[str] = frozenset({"chunk_corpus", "olap"})


@dataclass(frozen=True)
class CompiledPlan:
    """An accepted plan, plus what accepting it established.

    Held separately from the plan so that "this plan compiles" is a fact with a provenance rather
    than a flag someone could set. `plan_hash` is copied here because every downstream artefact —
    dataset, receipt, Croissant file — cites it, and recomputing it in four places is four chances
    to compute it differently.
    """

    plan: MaterialisationPlan
    plan_hash: str
    feature_columns: tuple[Column, ...]
    axis_columns: tuple[Column, ...]
    #: Non-fatal, declared consequences of accepting. E.g. that a feature's system-time cut will
    #: be empty for most rows against a current-state table. Surfaced, never swallowed.
    notes: tuple[str, ...] = ()
    feasibility: Optional[Feasibility] = None

    @property
    def emitted_columns(self) -> tuple[Column, ...]:
        return self.plan.shape.columns


# ---------------------------------------------------------------------------------------
# The invariants
# ---------------------------------------------------------------------------------------
def _check_feature_axis(plan: MaterialisationPlan) -> list[str]:
    """Invariant 1: features are cut on SYSTEM time. No other value compiles."""
    notes: list[str] = []
    axis = plan.temporal.feature_axis
    if axis != "system_time":
        raise FeatureAxisRefused(
            f"temporal.feature_axis is {axis!r}. For any shape that trains or retrieves, the "
            f"only value that compiles is 'system_time'. Cutting features on valid time is the "
            f"Feast default and it admits retroactive corrections — a value backfilled after the "
            f"decision instant appears in the row as though the organisation had it, the offline "
            f"metric inflates, and the online model collapses. That leak is the entire reason "
            f"two axes are stored (R05 §1.2, SYSTEM_DESIGN §6.3 rule 1)")

    if plan.shape.kind in MEMBERSHIP_SHAPES:
        if plan.temporal.corpus_as_of is None:
            raise FeatureAxisRefused(
                f"shape {plan.shape.kind!r} cuts membership on system time and no "
                f"temporal.corpus_as_of was given. Without an instant there is no cut, and the "
                f"corpus would silently mean 'whatever was on file when this ran'")
        if plan.shape.kind == "chunk_corpus" and plan.shape.text_field is not None:
            # `text_field` is a SEPARATE field from `shape.columns` (a chunk corpus declares no
            # columns of its own — CHUNK_COLUMNS is fixed), so it was never reached by the loop
            # below over `plan.feature_columns`. Without this, a corpus compiled with no
            # system_time_field on its text: `known_at(None, corpus_as_of)` is always False, so
            # every row is rejected by membership and the corpus silently builds EMPTY instead of
            # the compiler refusing a plan that named an axis its source cannot answer
            # (R-014 Finding 9).
            tf = plan.shape.text_field
            if not (tf.system_time_field or "").strip():
                raise NoSystemTimeAxis(
                    f"shape.text_field reads {tf.entity}.{tf.field} and names no "
                    f"system_time_field. Membership in this corpus is cut on system time — "
                    f"whether the document was on file by the corpus instant — and a source that "
                    f"does not record when it learned the text cannot answer that. Without the "
                    f"field, `known_at` treats every row as 'not yet known' and the corpus "
                    f"silently builds with zero documents rather than the plan being refused for "
                    f"naming an axis this source does not have")
    else:
        if plan.temporal.feature_as_of == "per_row":
            if not (plan.selection.cohort.decision_time_field or "").strip():
                raise FeatureAxisRefused(
                    "temporal.feature_as_of is 'per_row' and selection.cohort.decision_time_field "
                    "is not set. A per-row cut needs a per-row instant; without one the join is "
                    "point-in-no-time and every feature is read at build time, which is the leak "
                    "in its purest form")
        elif plan.temporal.feature_as_of_fixed is None:
            raise FeatureAxisRefused(
                "temporal.feature_as_of is 'fixed' and temporal.feature_as_of_fixed is unset: a "
                "fixed cut with no instant is not a cut")

    for column in plan.feature_columns:
        source = column.source
        if not (source.system_time_field or "").strip():
            raise NoSystemTimeAxis(
                f"feature {column.path!r} reads {source.entity}.{source.field} and names no "
                f"system_time_field. The feature axis is system time, and an axis this source "
                f"does not record cannot be cut on. Substituting the nearest available date — a "
                f"valid-time column, an effective date, the decision date itself — is precisely "
                f"the substitution that produces a confident, leaky split. Either the source "
                f"records when it learned this, or the column is not admissible as a feature")
        if source.relation in ("as_of_prior", "aggregate_count") and not (source.join_key or ""):
            raise FeatureAxisRefused(
                f"feature {column.path!r} uses relation {source.relation!r} without a join_key: "
                f"there is no key on which to find the prior rows, so the 'prior' is undefined")
        if source.relation == "as_of_prior" and not (source.valid_time_field or ""):
            raise FeatureAxisRefused(
                f"feature {column.path!r} is an as-of-prior join with no valid_time_field. "
                f"'Prior' is an ordering in the world, and system time alone cannot supply it — "
                f"two facts recorded in one batch would be simultaneous")
    return notes


def _check_label_axis(plan: MaterialisationPlan) -> list[str]:
    """Invariant 2: labels on VALID time, read at the latest system time, with a maturity lag."""
    notes: list[str] = []
    label = plan.shape.label
    if plan.shape.kind in LABELLED_SHAPES and label is None:
        raise LabelAxisRefused(
            f"shape {plan.shape.kind!r} is a supervised shape and the plan declares no label. A "
            f"training set with no label block is not a training set; if the intent is an "
            f"unlabelled extract, say so with a shape that has no label")
    if label is None:
        if plan.temporal.label_axis != "none":
            raise LabelAxisRefused(
                f"temporal.label_axis is {plan.temporal.label_axis!r} with no label declared: a "
                f"cut over nothing")
        return notes

    if plan.temporal.label_axis != "valid_time":
        raise LabelAxisRefused(
            f"temporal.label_axis is {plan.temporal.label_axis!r}. Labels are cut on VALID time. "
            f"Cutting them on system time encodes RECORDING LATENCY as signal — the model learns "
            f"how fast a clerk types, not what happened (R05 §1.3, SYSTEM_DESIGN §6.3 rule 2)")
    if plan.temporal.label_read_at_system_time != "build_time":
        raise LabelAxisRefused(
            f"temporal.label_read_at_system_time is "
            f"{plan.temporal.label_read_at_system_time!r}. A label must be read at the LATEST "
            f"system time so that corrections are included; a label read at the decision instant "
            f"is the uncorrected one, and training on it teaches the model the organisation's "
            f"first guess rather than the outcome")
    if plan.temporal.build_time is None:
        raise DeterminismIncomplete(
            "temporal.label_read_at_system_time is 'build_time' and temporal.build_time is "
            "unpinned. 'Whenever this ran' is not re-executable and the plan hash would not "
            "record which instant produced the bytes")
    if plan.temporal.label_window is None:
        raise LabelAxisRefused(
            "a label with no window admits an outcome from any distance after the decision, "
            "including one caused by something that happened years later. Declare the window")
    if not (plan.temporal.label_maturity_lag or "").strip():
        raise MaturityLagMissing(
            "temporal.label_maturity_lag is not declared. Without it, an outcome that occurred "
            "inside the window but has not yet been RECORDED becomes a negative example, "
            "silently. That is right-censoring, it always biases in the same direction, and it "
            "is invisible in every metric the training run reports (R05 §1.3). 'P0D' is a legal "
            "answer if the source records outcomes synchronously — but it has to be stated")

    # The two blocks must not share a field: unifying them by config is the failure mode the
    # separate types exist to prevent, and a plan CAN still name the same column in both. Checked
    # over every column whose relation reads THIS row's own value directly — `cohort_row`, plus the
    # axis relations `cohort_key`/`decision_time` (R-014 Finding 5): a column mislabelled
    # `cohort_key` or `decision_time` is excluded from `feature_columns` by definition (that set
    # exists to exempt genuine axis/identity columns from the temporal cut), which used to let the
    # label's own field slip through disguised as an axis column — never checked here, and still
    # emitted into the row by `_feature_cell`, which reads axis columns straight off the cohort row
    # with no cut at all. Deliberately NOT extended to `as_of_prior`/`aggregate_count`: those read a
    # DIFFERENT row (the prior test, a join over history) through the SAME field name — "the
    # covenant's own prior breach status" is a legitimate lagged feature, not the label leaking.
    SAME_ROW_RELATIONS = ("cohort_row", "cohort_key", "decision_time")
    for column in plan.shape.columns:
        if (column.source.relation in SAME_ROW_RELATIONS
                and column.source.entity == label.source.entity
                and column.source.field == label.source.field):
            raise LabelAxisRefused(
                f"column {column.path!r} reads {label.source.entity}.{label.source.field} via "
                f"relation {column.source.relation!r} — the label's own field, off THIS row. "
                f"Whatever axis it is cut on, and whatever the column is declared to be, this is "
                f"the label in the feature matrix")
    return notes


def _check_policy_in_hash(plan: MaterialisationPlan) -> list[str]:
    """Invariant 3: the same query under a different policy is a different dataset."""
    body = plan._payload()
    if "policy" not in body:
        raise PolicyNotInHash(                                   # pragma: no cover - structural
            "the hashed payload carries no policy block: the same query under a different policy "
            "would share a dataset name with one it is not")
    if not (plan.policy.purpose or "").strip():
        raise PolicyNotInHash(
            "policy.purpose is blank. Purpose travels with derived data (AI Act Art.10 §2(b)) and "
            "it is part of the dataset's identity; a blank one makes two datasets collected for "
            "different reasons hash alike")
    if not (plan.policy.lawful_basis or "").strip():
        raise PolicyNotInHash(
            "policy.lawful_basis is blank. It is a required part of the record for a derived "
            "dataset, and it is inside the hash so that re-deriving under a different basis "
            "produces a different dataset rather than overwriting the first")
    if plan.policy.perturbation.grounded_columns != "forbidden":
        raise PolicyNotInHash(
            "policy.perturbation.grounded_columns is 'allowed'. A DP-noised value is not the "
            "value that was in the source: it cannot carry a pointer that cites the source, and "
            "it will correctly fail the grounding wall's re-slice check. Perturbation applies to "
            "declared AGGREGATES only, and those change pointer kind to `derived` carrying the "
            "mechanism, epsilon, delta and clipping bounds (SYSTEM_DESIGN §6.3 rule 4)")
    if plan.policy.pii.quasi_identifiers.action != "suppress":
        raise PolicyNotInHash(
            "policy.pii.quasi_identifiers.action is 'generalise'. Generalising '1983-04-12' to "
            "'1980s' produces a value that is not in the source and therefore cannot be cited. "
            "Suppression is allowed for exactly this reason: it removes a value rather than "
            "inventing one")
    # A pack bump changes the closed vocabulary the columns are named in, so the dataset is a
    # different dataset. Refusing an unpinned pack set keeps that honest.
    if not plan.packs:
        raise PolicyNotInHash(
            "the plan pins no pack versions. Column paths and category vocabularies are named in "
            "the enabled packs' closed vocabulary; a pack bump changes what those names mean, so "
            "a plan that does not pin them cannot claim its hash identifies a dataset")
    return []


def bundle_version_of(decision_tables: Sequence[CompiledDecisionTable]) -> str:
    """The compiled policy bundle version(s) behind these tables, sorted and comma-joined.

    Shared by the compiler (to check a plan's `policy.bundle_version` pin against it) and the
    executor (to report it on the receipt), so there is exactly one place this is computed.
    """
    versions = sorted({t.policy_version for t in decision_tables if t.policy_version})
    return ",".join(versions)


def _check_policy_bundle_pinned(plan: MaterialisationPlan,
                                decision_tables: Sequence[CompiledDecisionTable]) -> list[str]:
    """Invariant 3, continued: the RUNTIME policy bundle is pinned too, not only the plan's own
    declared disclosure rules (R-014 Finding 3).

    `policy.purpose`/`lawful_basis`/`pii`/`perturbation`, checked above, are the plan's OWN
    declaration and are already inside `plan_hash`. But whether a column is actually disclosed or
    withheld to a given principal is ALSO a function of `roles.yaml`'s content, which is
    independently versioned (`RoleModel.bundle_version`) and can change without the plan changing
    at all. Re-running an unchanged plan against a bumped bundle can silently change which cells
    are grounded and which are `Withheld` while `plan_hash` claims to name the same dataset. Pinned
    exactly like `packs`, for the same reason.

    Only checked when `decision_tables` were supplied — the same tiering `_check_transformed_
    disclosure` (O1b) uses. A draft-time compile with no reader bound yet cannot know a bundle
    version nothing has computed; `materialise()` always supplies `decision_tables`, so the real
    execution path is never exempt.
    """
    if not decision_tables:
        return []
    actual = bundle_version_of(decision_tables)
    declared = (plan.policy.bundle_version or "").strip()
    if not declared:
        raise PolicyNotInHash(
            f"the plan pins no policy.bundle_version, and this estate's compiled policy is "
            f"versioned {actual!r}. A roles.yaml bump changes what is disclosed the same way a "
            f"pack bump changes what a column path means; a plan that does not pin the bundle "
            f"cannot claim its hash identifies the dataset a re-run would produce")
    if declared != actual:
        raise PolicyNotInHash(
            f"the plan pins policy.bundle_version={declared!r} and this read would run under "
            f"{actual!r}. Re-executing this plan_hash today would not run under the policy that "
            f"produced any prior content_hash for it — the same query under a different policy "
            f"bundle is a different dataset")
    return []


def _check_source_snapshots(plan: MaterialisationPlan) -> list[str]:
    """Invariant 3's data-side twin: a pin that pins nothing is not a pin (R-014 Finding 4).

    `SourceSnapshot` is how a plan claims "these are the bytes this hash was computed against". An
    entry with a blank `content_hash` asserts that with nothing to check the read against, and
    `pin_strength: "exact"` with zero snapshots claims full byte-level reproducibility while
    pinning nothing at all. Both compiled before this check existed, and both defeat the identity
    claim `plan_hash` exists to make: re-running the same `plan_hash` after the live source changes
    can silently produce different training bytes with no way to detect it.

    Deliberately narrower than "every source actually read must have a snapshot": correlating
    `plan.shape.columns`' entities against `source_snapshots.source_id` would need a source-to-
    entity map this single-adapter architecture does not yet carry, and that broader claim is
    recorded as a residual in R-014 rather than guessed at here.
    """
    for snap in plan.temporal.source_snapshots:
        if not (snap.content_hash or "").strip():
            raise DeterminismIncomplete(
                f"temporal.source_snapshots names {snap.source_id!r} with a blank content_hash. A "
                f"snapshot with nothing to check the read against is not a pin — re-running this "
                f"plan_hash after the live source changes could silently produce different bytes "
                f"with no way to detect it")
    if plan.temporal.pin_strength == "exact" and not plan.temporal.source_snapshots:
        raise DeterminismIncomplete(
            "temporal.pin_strength is 'exact' and temporal.source_snapshots is empty. 'exact' "
            "claims this plan can be re-executed to reproduce the same bytes byte-for-byte; that "
            "claim is empty with nothing pinned to replay against")
    return []


def _check_pointer_requirement(plan: MaterialisationPlan) -> list[str]:
    """Invariant 4: `require_pointer_for_every_cell: false` is not a value."""
    if not plan.provenance.require_pointer_for_every_cell:
        raise PointerRequirementDisabled(
            "provenance.require_pointer_for_every_cell is false. There is no such value. A model "
            "asked for 'clean data' will offer to drop the awkward cells, and this field is where "
            "that offer arrives; every emitted cell resolves to a pointer, or to a `derived` "
            "pointer carrying a re-executable predicate with row_count and rows_digest. A cell "
            "that can do neither is `Withheld` or `Absent`, which are non-answers rather than "
            "values, and both are counted apart in the receipt")
    return []


def _check_abstention(plan: MaterialisationPlan) -> tuple[list[str], Optional[Feasibility]]:
    """Invariant 5: an abstention budget is feasibility-tested, never asserted."""
    budget = plan.policy.abstention
    if budget is None:
        return [], None
    if not budget.feasibility_tested:
        raise AbstentionNotFeasible(
            f"policy.abstention declares alpha={budget.alpha} and feasibility_tested is false. A "
            f"model will accept any alpha it is given, and the number then travels into a "
            f"receipt and a data card as though it had been established. Kotte "
            f"(arXiv:2606.29054) found alpha=0.10 UNCERTIFIABLE for NER-class tasks across six "
            f"models and eight datasets; the budget is refused until it is tested, not accepted "
            f"until it is challenged")
    if budget.base_risk is None:
        raise AbstentionNotFeasible(
            f"policy.abstention claims feasibility_tested with no base_risk. The test needs a "
            f"MEASURED base risk over the golden set "
            f"({budget.golden_set or 'none declared'}); without one, 'tested' records that "
            f"somebody ticked a box")
    result = assess(budget.base_risk, budget.alpha, budget.max_abstention_rate)
    if not result.feasible:
        raise AbstentionNotFeasible(f"policy.abstention: {result.reason}")
    return [f"abstention: {result.reason}"], result


def _check_no_imputation(plan: MaterialisationPlan) -> list[str]:
    """SYSTEM_DESIGN §6.3 rule 8. `None` plus a reason code is the honest AND correct answer."""
    offenders = [c for c in plan.shape.columns if c.null_policy != "keep"]
    if offenders:
        names = ", ".join(f"{c.path} ({c.null_policy})" for c in offenders)
        raise ImputationRefused(
            f"null_policy is not 'keep' for: {names}. No imputation, ever. XGBoost, LightGBM and "
            f"CatBoost all learn a default branch direction for missing values, so imputing "
            f"destroys information the model would otherwise use — and it erases the difference "
            f"between 'not in the source', 'dropped by the grounding wall' and 'withheld from "
            f"this principal', which are three different facts and must never be summed")
    return []


def _check_determinism(plan: MaterialisationPlan) -> list[str]:
    """SYSTEM_DESIGN §6.4 — a plan is re-executable iff all of these hold."""
    d = plan.determinism
    missing: list[str] = []
    if d.seed is None:
        missing.append("determinism.seed")
    if not d.row_order:
        missing.append("determinism.row_order")
    if d.executor is None:
        missing.append("determinism.executor (a digest is executor-relative)")
    elif not (d.executor.materialiser_version or "").strip():
        # A digest is executor-relative in a second sense beyond backend/threads: re-executing an
        # UNCHANGED plan against pinned sources after upgrading this package itself can shape bytes
        # differently under the same plan_hash. `materialise()` checks the declared version against
        # the build actually running; this half only checks that something was declared at all —
        # compile-only callers have no running executor to compare against (R-014 Finding 11).
        missing.append("determinism.executor.materialiser_version")
    if missing:
        raise DeterminismIncomplete(
            f"the plan is not re-executable: {', '.join(missing)} unset. A content hash over rows "
            f"in an undefined order names nothing, and a plan that cannot be replayed cannot be "
            f"the dataset's identity")

    # A chunk corpus does not declare columns: its shape is fixed by the corpus vocabulary
    # (`shapes.chunked.CHUNK_COLUMNS`), because a corpus whose rectangle varied per plan would make
    # every retriever consuming one learn a new schema. Its unique column is `chunk_id`.
    if plan.shape.kind == "chunk_corpus":
        from .shapes.chunked import CHUNK_COLUMNS

        declared = set(CHUNK_COLUMNS)
        keys = {"chunk_id"}
    else:
        declared = {c.path for c in plan.shape.columns}
        # A TOTAL order. The key columns are the only ones guaranteed unique, so requiring that
        # the order contains the cohort key is the cheap structural form of "total".
        keys = {c.path for c in plan.shape.columns if c.source.relation == "cohort_key"}

    unknown = [name for name in d.row_order if name not in declared]
    if unknown:
        raise DeterminismIncomplete(
            f"determinism.row_order names {unknown}, which no declared column emits. An order "
            f"over columns the dataset does not contain is not an order over the dataset")

    if not keys:
        raise DeterminismIncomplete(
            "no column has relation 'cohort_key': without one, no column is guaranteed unique per "
            "row, so a TOTAL order cannot be established and the content hash becomes a property "
            "of the sort's stability rather than of the data")
    if not keys.issubset(set(d.row_order)):
        # A NON-EMPTY INTERSECTION is not enough for a composite key: `row_order` containing
        # `tenant_id` but not `subject_id` for a key of `(tenant_id, subject_id)` used to pass this
        # check while leaving two rows sharing a tenant free to tie (R-014 Finding 10).
        gap = sorted(keys - set(d.row_order))
        raise DeterminismIncomplete(
            f"determinism.row_order {list(d.row_order)} does not include cohort key column(s) "
            f"{gap} (full key: {sorted(keys)}). THE WHOLE KEY is required, not merely part of it: "
            f"two rows sharing every column that IS in row_order would still tie, and the content "
            f"hash becomes a property of the sort's stability rather than of the data")
    if plan.shape.kind == "chunk_corpus" and plan.shape.chunking is None:
        raise DeterminismIncomplete(
            "a chunk corpus with no chunking block cannot be re-sliced: the boundaries would be "
            "whatever the chunker defaulted to on the day, and each chunk IS a pointer")
    return []


def _check_transformed_disclosure(
        plan: MaterialisationPlan,
        decision_tables: Sequence[CompiledDecisionTable]) -> list[str]:
    """Refused honestly: spine §9 **O1b**, and what changed on 2026-08-16.

    A masked or otherwise transformed cell is a *partial* disclosure. The adapter's Policy layer
    compiles a `transform` onto every disclosed column the source contract marks sensitive, and
    the Core then emits a `derived` pointer carrying the redaction. A Receipt over a dataset
    containing those cells would have to count them as fully disclosed or fully withheld, and both
    are false.

    **The vocabulary gap has since closed; the plumbing gap has not, and that is why this refusal
    still stands.** `databridge_core.provenance` now defines `Transformed` — a fourth cell state
    that wraps a `GroundedValue` whose pointer is `derived`, carrying the policy reference and the
    transformation alongside the citation. So the *word* exists. What does not exist is anyone
    saying it: `databridge_adapter.results.Cell` is a deliberately narrower three-state union that
    fails closed on a `Transformed` until it opts in, and the Policy layer still emits a masked
    column as an ordinary `GroundedValue`. This package therefore **cannot tell** a transformed
    cell from a fully disclosed one at the point it would have to be counted, and a receipt that
    guessed would be exactly the dishonest artefact the state was introduced to prevent.

    Four things were considered and three rejected:

    * count them as grounded — claims a disclosure that did not happen;
    * count them as withheld — claims a denial that did not happen, and hides that a value did
      reach the consumer;
    * infer "transformed" here from the pointer kind — rejected in core for the same reason it is
      rejected here: `derived` is already the honest kind for many FULL disclosures (unit
      conversions, normalisations, §17.1 aggregation compression, the registered `identity` rule),
      so using it as the discriminator would make a mask and a full disclosure indistinguishable
      at exactly the place downstream code looks;
    * **refuse, and name O1b.** Taken.

    The refusal is deliberately at COMPILE time, from the policy's own table, and therefore does
    not depend on whether any particular row happened to contain a value the redaction matched.
    Whether a dataset is honestly describable is a property of the projection, not of the sample.

    **When this can be deleted:** when `databridge_adapter` widens its `Cell` union and its Policy
    layer emits `Transformed` for a masked column. At that point this screen becomes a *counter* —
    a fifth line in `CellCounts` — rather than a refusal, because the receipt will be able to say
    truthfully how many cells were partially disclosed and under which rule.
    """
    for table in decision_tables:
        transformed = sorted(r.column for r in table.columns if r.transform and not r.withheld)
        if transformed:
            raise TransformedDisclosureUnrepresentable(
                f"policy would emit a TRANSFORMED disclosure for {table.entity}."
                f"{{{', '.join(transformed)}}} "
                f"(rule: {next(r.transform for r in table.columns if r.transform)!r}). "
                f"Materialisation is refused, and the reason is spine §9 **O1b**: the cell "
                f"vocabulary has three states — GroundedValue, Withheld, Absent — and a "
                f"transformed disclosure is none of them. A Receipt for this dataset would have "
                f"to count these cells as fully disclosed or as fully withheld, and both are "
                f"false; the first claims a disclosure that did not happen, the second hides "
                f"that a value reached the consumer.\n\nThe fourth state now EXISTS — "
                f"`Transformed` landed in databridge_core.provenance on 2026-08-16 — but nothing "
                f"in the read path emits one yet: databridge_adapter.results.Cell is a narrower "
                f"three-state union that fails closed on it, and Policy still hands a masked "
                f"column back as an ordinary GroundedValue. This package cannot distinguish the "
                f"two at the point it would have to count them, so it refuses rather than "
                f"guesses. Until the adapter opts in, drop these columns from the projection or "
                f"read them under a principal for whom policy discloses them verbatim")
    return []


# ---------------------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------------------
def _check_air_gap(plan: MaterialisationPlan) -> None:
    """SYSTEM_DESIGN §6.3 rule 6, actually enforced: air-gapped is a REFUSAL, not a note.

    Before R-014 Finding 8 this appended an advisory note and let the plan compile anyway — a
    runtime hope wearing a compile-time sentence, and its own comment said the opposite ("a
    compile-time property, not a runtime hope"). Checked against the snapshot pins the plan
    carries: an adapter that needs the network to serve one of them cannot be admitted later
    without having failed this first.
    """
    if plan.policy.egress != "air_gapped":
        return
    if not plan.temporal.source_snapshots:
        raise AirGapUnpinned(
            "policy.egress is 'air_gapped' and temporal.source_snapshots is empty. An air-gapped "
            "build claims it needs no live network access to replay; with nothing pinned there is "
            "nothing to replay against, which makes the claim untestable rather than true")
    remote = [s.source_id for s in plan.temporal.source_snapshots
              if s.capture_ref is None and s.tier in ("T2", "T3")]
    if remote:
        raise AirGapUnpinned(
            f"policy.egress is 'air_gapped' and source(s) {remote} are T2/T3 with no capture_ref: "
            f"these must resolve against the capture store, because an air-gapped build cannot "
            f"reach the live source to re-read them")


def compile_plan(plan: MaterialisationPlan, *,
                 decision_tables: Iterable[CompiledDecisionTable] = ()) -> CompiledPlan:
    """Accept the plan, or refuse with the invariant that fired and why it exists.

    ``decision_tables`` are the policy's compiled tables for the reads this plan implies — one per
    projection. They are optional so that a plan can be checked before any adapter is bound (the
    agent authors a plan before the reads are admitted); when they are supplied, the O1b screen and
    the policy-bundle pin (Finding 3) run, and a projection carrying a transformed disclosure, or a
    plan pinned to the wrong bundle, is refused before anything is read.
    """
    tables = tuple(decision_tables)
    notes: list[str] = []
    notes += _check_feature_axis(plan)
    notes += _check_label_axis(plan)
    notes += _check_policy_in_hash(plan)
    notes += _check_policy_bundle_pinned(plan, tables)
    notes += _check_source_snapshots(plan)
    notes += _check_pointer_requirement(plan)
    abstention_notes, feasibility = _check_abstention(plan)
    notes += abstention_notes
    notes += _check_no_imputation(plan)
    notes += _check_determinism(plan)
    notes += _check_transformed_disclosure(plan, tables)
    _check_air_gap(plan)

    return CompiledPlan(plan=plan, plan_hash=plan.plan_hash,
                        feature_columns=plan.feature_columns,
                        axis_columns=plan.axis_columns,
                        notes=tuple(notes), feasibility=feasibility)


__all__ = ["CompiledPlan", "compile_plan", "MEMBERSHIP_SHAPES"]
