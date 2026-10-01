"""The `MaterialisationPlan` — the declarative artefact (SYSTEM_DESIGN §6.2).

**The inversion this file encodes.** A dataset is normally a pile of bytes with metadata attached.
Here the plan is the dataset's identity and the bytes are a cache: `plan_hash` names the dataset,
and re-executing the plan against the pinned snapshots must reproduce `content_hash`. That makes
"this training set = this query + this as-of + this policy" a first-class object.

Three consequences visible in the types below:

* **The feature block and the label block are separate objects with separate axes.** They cannot
  be unified by configuration, because the whole leak this system exists to prevent is a single
  `as_of` applied to both (R05 §1.3). `Column.source` is a `ColumnSource`; `Label.source` is a
  `LabelSource`; they share no type and no field.
* **`policy` is inside the plan.** The same query under a different policy is a different dataset
  and must not share a name (SYSTEM_DESIGN §6.3 rule 5). It is a field of the hashed document, not
  a runtime argument.
* **Nothing here has a "just this once" escape.** `null_policy` accepts imputation *as a spelling*
  so the compiler can refuse it by name — a schema that made the mistake unspellable would produce
  a Pydantic `ValidationError` mentioning a field, where the compiler produces a refusal
  explaining that XGBoost, LightGBM and CatBoost all learn a default branch direction for missing
  values and imputation destroys information they would otherwise use.

A note on `plan_hash` and Pydantic. `databridge_adapter.canonical` builds its payload by hand
precisely so a Pydantic serialisation change cannot move `read_hash`. That argument applies here
too, but the plan is a fifteen-model tree and a hand-built payload for it would be its own bug
farm. The trade is taken deliberately and pinned instead: `SCHEME_VERSION` below is part of the
hashed document, and `test_the_plan_hash_is_pinned` fixes the digest of a fixed plan, so a
serialisation change fails loudly at the golden rather than silently re-identifying every dataset.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_adapter import canonical_json, sha256_of

from .durations import DurationError, parse_duration

#: The canonicalisation scheme for `plan_hash`. Bumping it makes a deliberate change to plan
#: identity visible rather than silent: datasets built under v1 keep their names.
#: v2 (R-014): `MaterialisationPolicy.bundle_version` and `Executor.materialiser_version` were
#: added — both real dependencies of what gets emitted (Findings 3 and 11) that were previously
#: outside the hash — so a v1 plan_hash and a v2 plan_hash for what looks like the same query must
#: never collide.
SCHEME_VERSION = 2

#: The shapes the vocabulary knows. Whether one is *produced* is a different question, answered by
#: `shapes/`; a shape this build refuses still has to be nameable, or the refusal cannot name it.
ShapeKind = Literal["tabular", "relational", "graph_tensor", "chunk_corpus",
                    "conversational", "preference", "olap"]

ColumnType = Literal["int32", "int64", "float64", "bool", "category", "string", "date",
                     "timestamp", "text"]

#: How a column is obtained. The set is closed and small on purpose: every relation here is
#: point-in-time expressible against a bi-temporal source, and a relation that is not cannot be
#: added by writing prose in a plan.
#:
#: * ``cohort_key``     — the cohort row's identity. Not a feature; exempt from the feature axis.
#: * ``decision_time``  — the per-row decision instant T_d. Defines the axis; cannot leak into it.
#: * ``cohort_row``     — a field of the cohort row itself, admitted only where its own system
#:                        time is at or before T_d. Frequently that is *never*, and the receipt
#:                        says so rather than the plan pretending otherwise.
#: * ``as_of_prior``    — last-known-good join: the most recent earlier row for the same key that
#:                        had been RECORDED by T_d. This is the join Feast gets wrong by default.
#: * ``aggregate_count``— a count over rows recorded by T_d, emitted with a compressed `derived`
#:                        pointer carrying the predicate, row_count and rows_digest.
Relation = Literal["cohort_key", "decision_time", "cohort_row", "as_of_prior", "aggregate_count"]

#: The relations that are FEATURES and therefore bound by the system-time cut.
FEATURE_RELATIONS: frozenset[str] = frozenset({"cohort_row", "as_of_prior", "aggregate_count"})

#: Shapes that carry a label block and are therefore subject to the label-axis invariants.
LABELLED_SHAPES: frozenset[str] = frozenset({"tabular", "relational", "graph_tensor",
                                             "conversational", "preference"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------------------
# Authorship and selection
# ---------------------------------------------------------------------------------------
class AuthoredBy(_Frozen):
    """Who asked for this dataset, on whose behalf, and for what purpose.

    ``at`` is excluded from `plan_hash`: when a plan was written is not a property of the dataset
    it describes. Everything else here is included, because *who* and *why* are.
    """

    agent: str
    on_behalf_of: Optional[str]
    purpose: str
    at: datetime


class Cohort(_Frozen):
    """Which rows the dataset has one of, and when each one's decision was taken.

    ``decision_time_field`` is the per-row T_d and is required for any `per_row` feature cut —
    without it there is no instant to cut at and a "point-in-time" join is point-in-no-time.
    """

    filter: Optional[str] = None
    decision_time_field: Optional[str] = None


class Selection(_Frozen):
    entity: str
    cohort: Cohort = Cohort()
    #: The graph paths features may cross, declared so the compiler can refuse one that reaches
    #: outside them. Prose paths, e.g. ``"Subject -[covered_by]-> Org"``.
    traverse: tuple[str, ...] = ()


# ---------------------------------------------------------------------------------------
# The temporal contract — the load-bearing part
# ---------------------------------------------------------------------------------------
class SourceSnapshot(_Frozen):
    """The third axis: which source-side snapshot the values were read from.

    Walnut is tri-temporal — source as-of, graph system time, valid time (R05 §1.3). The binding
    cut for replay is system time; this is what makes re-execution *verifiable*.
    """

    source_id: str
    tier: Literal["T0", "T1", "T2", "T3"]
    as_of: datetime
    content_hash: str
    schema_hash: str = ""
    version_id: Optional[str] = None
    capture_ref: Optional[str] = None


class LabelWindow(_Frozen):
    """`(T_d, T_d + within]` on VALID time. The outcome must postdate the decision or it is not
    an outcome."""

    after: Literal["T_d"] = "T_d"
    within: str

    @model_validator(mode="after")
    def _within_is_a_fixed_length(self) -> "LabelWindow":
        parse_duration(self.within)
        return self


class TemporalContract(_Frozen):
    """The dual-axis contract. Read `R05` §1.3 beside this; every field below is one line of it.

    | Component | Filter | Why |
    |---|---|---|
    | Features | `system_from <= T_d < system_to` | replays what the organisation KNEW at T_d |
    | Labels | valid time `> T_d`, read at latest system time | best-known truth, corrections in |
    | Maturity | `label.system_from <= T_b - lag` | outcomes not yet RECORDED are not negatives |
    """

    feature_axis: Literal["system_time", "valid_time", "source_as_of", "none"]
    feature_as_of: Literal["per_row", "fixed"] = "per_row"
    #: Optional secondary filter: "what did we then believe was true ABOUT T_d", as against "what
    #: did we then believe, about any time". Omit when history is itself a feature.
    feature_valid_filter: Literal["none", "in_force_at_decision"] = "none"
    feature_as_of_fixed: Optional[datetime] = None

    label_axis: Literal["valid_time", "system_time", "none"] = "none"
    label_window: Optional[LabelWindow] = None
    label_read_at_system_time: Literal["build_time", "decision_time", "none"] = "none"
    label_maturity_lag: Optional[str] = None

    #: T_b. Pinned, not `now()`: a label block read at "whenever this ran" is not re-executable,
    #: and the plan hash would not record which instant produced the bytes.
    build_time: Optional[datetime] = None

    #: For membership shapes (`chunk_corpus`), the system-time instant a document must have been
    #: on file by. Same axis as the feature cut, different name because there is no decision.
    corpus_as_of: Optional[datetime] = None

    source_snapshots: tuple[SourceSnapshot, ...] = ()
    pin_strength: Literal["exact", "mixed", "observational"] = "observational"

    @model_validator(mode="after")
    def _durations_are_fixed_lengths(self) -> "TemporalContract":
        if self.label_maturity_lag is not None:
            try:
                parse_duration(self.label_maturity_lag)
            except DurationError as err:
                raise ValueError(f"label_maturity_lag: {err}") from err
        for name in ("build_time", "corpus_as_of", "feature_as_of_fixed"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(
                    f"{name} must be timezone-aware: a naive instant means a different moment in "
                    f"every deployment, and a dataset's cut cannot depend on the reader's clock")
        return self


# ---------------------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------------------
class ColumnSource(_Frozen):
    """Where one FEATURE comes from, and on which axis it is admissible.

    ``system_time_field`` is the column recording *when the organisation learned it*. It has no
    default and no fallback to a valid-time column: a source that does not record when it learned
    something cannot answer "what did we know at T_d", and substituting the nearest available
    date is exactly the substitution that produces a leaky split with confidence.
    """

    entity: str
    field: str
    relation: Relation
    join_key: Optional[str] = None
    system_time_field: Optional[str] = None
    valid_time_field: Optional[str] = None
    #: `aggregate_count` only: the lookback, relative to T_d. `None` means unbounded history.
    window: Optional[str] = None

    @model_validator(mode="after")
    def _window_is_a_fixed_length(self) -> "ColumnSource":
        if self.window is not None:
            parse_duration(self.window)
        return self


class Column(_Frozen):
    """One emitted column.

    ``path`` is a SEMANTIC name (`lending.covenant.prior_observed_value`), never `f_17`: CARTE,
    TARTE and KumoRFM consume column names as signal (R05 §2), and a receipt whose columns are
    numbered is not documentation of anything.
    """

    path: str
    type: ColumnType
    source: ColumnSource
    #: Only `keep` compiles. The other spellings exist so the refusal can name what was asked for.
    null_policy: Literal["keep", "impute_mean", "impute_zero", "impute_median", "drop_row"] = "keep"
    pointer_mode: Literal["full", "derived_compressed"] = "full"
    vocabulary: Optional[str] = None
    #: An explicit `unknown` level, because a re-coder cannot invent a code for a category never
    #: seen in training.
    unknown_level: bool = False


class LabelSource(_Frozen):
    """Where the LABEL comes from. A different type from `ColumnSource`, on purpose.

    Both time fields are required and neither is optional-with-a-default. The label's *validity*
    must postdate the decision (valid time) and its *recording* is what the maturity gate tests
    (system time); a label block that names only one of the two cannot express the rule.
    """

    entity: str
    field: str
    join_key: str
    valid_time_field: str
    system_time_field: str


class Label(_Frozen):
    path: str
    type: ColumnType
    source: LabelSource


class Consumer(_Frozen):
    framework: str
    version: str


class Embedding(_Frozen):
    """Pinned by id + revision + pooling + normalisation + dimension (SYSTEM_DESIGN §6.4).

    Re-embedding with a different revision produces a different `plan_hash`, because the model is
    part of the dataset's identity rather than a runtime detail.
    """

    model_id: str
    revision: str
    pooling: str
    normalisation: str
    dimension: int


class Chunking(_Frozen):
    """Late chunking by default: boundaries are computed on the ORIGINAL text, so
    `(row_id, column, char_start, char_end)` offsets are exact and re-sliceable."""

    strategy: Literal["late"] = "late"
    max_chars: int = Field(gt=0)
    overlap_chars: int = Field(default=0, ge=0)
    embedding: Optional[Embedding] = None

    @model_validator(mode="after")
    def _overlap_fits(self) -> "Chunking":
        if self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars, or chunking does not "
                             "advance and the corpus is infinite")
        return self


class Shape(_Frozen):
    kind: ShapeKind
    consumer: Consumer
    columns: tuple[Column, ...] = ()
    label: Optional[Label] = None
    #: `*_missing_reason` companion columns. `not_in_source`, `grounding_dropped` and
    #: `policy_suppressed` are three different facts and are never summed (SYSTEM_DESIGN §6.5).
    emit_missing_reason: bool = True
    semantic_names: bool = True
    #: `chunk_corpus` only.
    text_field: Optional[ColumnSource] = None
    chunking: Optional[Chunking] = None


# ---------------------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------------------
class PolicyPrincipal(_Frozen):
    """The principal the reads run as. Purpose lives on `MaterialisationPolicy.purpose` because it
    is a property of the dataset, not only of the reader."""

    actor: str
    on_behalf_of: Optional[str] = None
    grant_id: Optional[str] = None


class QuasiIdentifiers(_Frozen):
    min_group_size: int = Field(default=20, ge=1)
    #: Suppression only. Generalising "1983-04-12" to "1980s" produces a value that is not in the
    #: source, which cannot carry a pointer that cites the source (SYSTEM_DESIGN §6.3 rule 4).
    action: Literal["suppress", "generalise"] = "suppress"


class PiiPolicy(_Frozen):
    direct_identifiers: Literal["suppress", "retain"] = "suppress"
    quasi_identifiers: QuasiIdentifiers = QuasiIdentifiers()


class Aggregates(_Frozen):
    mechanism: Literal["none", "laplace", "gaussian"] = "none"
    epsilon: Optional[float] = None
    delta: Optional[float] = None


class Perturbation(_Frozen):
    grounded_columns: Literal["forbidden", "allowed"] = "forbidden"
    aggregates: Aggregates = Aggregates()


class TrustPolicy(_Frozen):
    """Untrusted values may be FEATURES. They may never compute a cohort filter or a split
    boundary — that is control flow, and D3 says untrusted content never reaches it."""

    untrusted_columns: Literal["allow_as_data", "forbid"] = "allow_as_data"


class Abstention(_Frozen):
    """A conformal risk budget. `alpha` is the target risk; `base_risk` is measured, not wished.

    Kotte (arXiv:2606.29054): where base risk μ exceeds target α, any distribution-free method
    must abstain on at least (μ−α)/(1−α) of examples. The Builder runs the test and refuses a
    budget it cannot certify, rather than accepting a number the operator would like to be true.
    """

    alpha: float = Field(gt=0.0, lt=1.0)
    base_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    feasibility_tested: bool = False
    max_abstention_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    golden_set: Optional[str] = None


class LeakCheck(_Frozen):
    max_class_delta: float = Field(default=0.02, ge=0.0, le=1.0)


class MaterialisationPolicy(_Frozen):
    purpose: str
    principal: PolicyPrincipal
    lawful_basis: str
    purpose_compatibility_check: Literal["strict", "permissive"] = "strict"
    trust_policy: TrustPolicy = TrustPolicy()
    pii: PiiPolicy = PiiPolicy()
    perturbation: Perturbation = Perturbation()
    egress: Literal["air_gapped", "internal", "restricted_internal", "public"] = "internal"
    missingness_leak_check: LeakCheck = LeakCheck()
    abstention: Optional[Abstention] = None
    #: The compiled role-model/purpose-binding bundle this plan is pinned against
    #: (`RoleModel.bundle_version`, e.g. `"walnut-finance-synthetic@v2"`). `purpose`/`lawful_basis`/
    #: `pii`/`perturbation` above are the plan's OWN declared disclosure rules and are already
    #: inside `plan_hash`; but whether a given column is disclosed or withheld to a given principal
    #: is ALSO a function of `roles.yaml`'s content, which is versioned and can change without the
    #: plan changing at all. Pinned exactly like `packs`, for the same reason: re-running an
    #: unchanged plan against a bumped bundle can silently change which cells are grounded and
    #: which are withheld while `plan_hash` claims to name the same dataset (R-014 Finding 3).
    bundle_version: str = ""


# ---------------------------------------------------------------------------------------
# Determinism, provenance, output
# ---------------------------------------------------------------------------------------
class Split(_Frozen):
    strategy: Literal["temporal", "random", "none"] = "temporal"
    train_before: Optional[date] = None
    val: Optional[str] = None
    test: Optional[str] = None


class Executor(_Frozen):
    backend: str
    threads: int = Field(default=1, ge=1)
    #: Which build of THIS package produced the dataset (`executor.EXECUTOR_VERSION`). A digest is
    #: executor-relative in a second sense beyond `backend`/`threads`: re-executing an unchanged
    #: plan against pinned sources after upgrading `walnut-mat` itself can shape bytes differently
    #: under the SAME `plan_hash` unless the version that is expected to run is part of the plan's
    #: own identity. Checked twice: the compiler refuses a blank declaration, and `materialise()`
    #: refuses a declaration that does not match the build actually running (R-014 Finding 11).
    materialiser_version: str = ""


class Determinism(_Frozen):
    seed: Optional[int] = None
    split: Split = Split(strategy="none")
    #: A TOTAL order over declared columns. Not "usually unique": if two rows compare equal the
    #: content hash depends on the sort's stability, which is a property of the sort, not the data.
    row_order: tuple[str, ...] = ()
    executor: Optional[Executor] = None
    float_scale: int = Field(default=6, ge=0, le=18)


class Provenance(_Frozen):
    mode: Literal["full", "compressed"] = "compressed"
    #: `false` IS NOT AN ALLOWED VALUE (SYSTEM_DESIGN §6.3 rule 3). Typed `bool` rather than
    #: `Literal[True]` so a plan that tries to disable it is REFUSED with the reason, instead of
    #: rejected by a schema error that names a field and explains nothing.
    require_pointer_for_every_cell: bool = True


class Output(_Frozen):
    format: Literal["parquet", "jsonl", "csv"] = "parquet"
    metadata: Literal["croissant/1.1", "none"] = "croissant/1.1"
    storage: Optional[str] = None


class Hashes(_Frozen):
    query_hash: str
    policy_hash: str
    temporal_hash: str
    pack_hash: str
    plan_hash: str


# ---------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------
class MaterialisationPlan(_Frozen):
    """The whole document. `plan_hash` is the dataset's identity."""

    api_version: Literal["walnut.materialisation/v1"] = "walnut.materialisation/v1"
    kind: Literal["MaterialisationPlan"] = "MaterialisationPlan"
    scheme_version: int = SCHEME_VERSION

    #: A human name. Excluded from the hash — a label is not identity.
    label: str = ""
    authored_by: AuthoredBy
    selection: Selection
    temporal: TemporalContract
    shape: Shape
    policy: MaterialisationPolicy
    determinism: Determinism = Determinism()
    provenance: Provenance = Provenance()
    output: Output = Output()

    #: The enabled pack versions. A pack bump is a DIFFERENT dataset, because the closed
    #: vocabulary the columns are named in changed underneath them.
    packs: tuple[str, ...] = ()

    # --- identity ---------------------------------------------------------------------
    def _payload(self) -> dict[str, Any]:
        """The hashed document: everything except `label` and `authored_by.at`.

        `id` and the `hashes` block are not fields of this model at all — they are derived — so
        the four exclusions SYSTEM_DESIGN §6.2 names reduce to two exclusions here.
        """
        body = self.model_dump(mode="json")
        body.pop("label", None)
        authored = dict(body.get("authored_by") or {})
        authored.pop("at", None)
        body["authored_by"] = authored
        return body

    @property
    def query_hash(self) -> str:
        """Over selection + shape. What was asked for, independent of when and under what rules."""
        body = self._payload()
        return sha256_of(canonical_json({"selection": body["selection"], "shape": body["shape"]}))

    @property
    def policy_hash(self) -> str:
        return sha256_of(canonical_json(self._payload()["policy"]))

    @property
    def temporal_hash(self) -> str:
        """Over the temporal block INCLUDING `source_snapshots` — the pins are part of the cut."""
        return sha256_of(canonical_json(self._payload()["temporal"]))

    @property
    def pack_hash(self) -> str:
        return sha256_of(canonical_json({"packs": list(self.packs)}))

    @property
    def plan_hash(self) -> str:
        return sha256_of(canonical_json(self._payload()))

    @property
    def id(self) -> str:
        return f"mp_{self.plan_hash[:12]}"

    @property
    def output_stem(self) -> str:
        """The on-disk file stem. The FULL `plan_hash`, never truncated.

        `id` is a short, human-friendly form for display — 12 hex characters, 48 bits — and is not
        guaranteed collision-free at scale. Two distinct plans sharing an `id` prefix must never
        silently overwrite each other's output file, so a file name is built from this instead
        (R-014 Finding 13). `id` itself is left alone: shortened for reading, not for writing.
        """
        return f"mp_{self.plan_hash}"

    @property
    def hashes(self) -> Hashes:
        return Hashes(query_hash=self.query_hash, policy_hash=self.policy_hash,
                      temporal_hash=self.temporal_hash, pack_hash=self.pack_hash,
                      plan_hash=self.plan_hash)

    # --- convenience ------------------------------------------------------------------
    @property
    def feature_columns(self) -> tuple[Column, ...]:
        return tuple(c for c in self.shape.columns if c.source.relation in FEATURE_RELATIONS)

    @property
    def axis_columns(self) -> tuple[Column, ...]:
        """Identity and decision-time columns: they DEFINE the cut and are not cut by it."""
        return tuple(c for c in self.shape.columns if c.source.relation not in FEATURE_RELATIONS)

    def column(self, path: str) -> Column:
        for c in self.shape.columns:
            if c.path == path:
                return c
        raise KeyError(path)

    def to_yaml(self) -> str:
        import yaml

        body = self.model_dump(mode="json")
        body["id"] = self.id
        body["hashes"] = self.hashes.model_dump(mode="json")
        return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


__all__ = [
    "SCHEME_VERSION", "ShapeKind", "ColumnType", "Relation", "FEATURE_RELATIONS",
    "LABELLED_SHAPES", "AuthoredBy", "Cohort", "Selection", "SourceSnapshot", "LabelWindow",
    "TemporalContract", "ColumnSource", "Column", "LabelSource", "Label", "Consumer",
    "Embedding", "Chunking", "Shape", "PolicyPrincipal", "QuasiIdentifiers", "PiiPolicy",
    "Aggregates", "Perturbation", "TrustPolicy", "Abstention", "LeakCheck",
    "MaterialisationPolicy", "Split", "Executor", "Determinism", "Provenance", "Output",
    "Hashes", "MaterialisationPlan",
]
