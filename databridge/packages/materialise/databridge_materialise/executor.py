"""The materialiser: an accepted plan in, a dataset and a `Receipt` out.

**No model runs here.** AGENT_PLANE D14 is explicit about why: the plan hash is the dataset's
identity and the bytes are a cache, so if a model shaped the output, re-running one plan would not
reproduce one dataset and reproducibility — the entire claim — would be gone. Every decision this
module makes is a function of the plan and the rows.

The order of operations is deliberate and each step is a gate:

1. **compile** — refuse a leaky plan before anything is read (`compiler.compile_plan`);
2. **screen for O1b** — refuse a projection whose policy would emit a *transformed* disclosure,
   from the compiled decision table, before rows are fetched;
3. **build** — the shape's own point-in-time join;
4. **screen again, at emission** — a transformed cell that reached the rectangle anyway;
5. **check every cell carries a pointer or is a named non-answer**;
6. **run the missingness leak check** — refuse if suppression correlates with the label;
7. **emit the receipt**, with `shaped_by`.

Step 4 exists because step 2 is only available when a `PolicyEngine` was supplied. Belt and
braces on the one screen whose absence would put a dishonest receipt into a ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, Sequence, runtime_checkable

from databridge_adapter import CompiledDecisionTable, Verdict, parse_derivation, pointer_set_digest
from databridge_core import Absent, GroundedValue, SourcePointer, Withheld

from .compiler import CompiledPlan, bundle_version_of, compile_plan
from .dataset import Dataset, pointer_of, write_jsonl, write_parquet
from .errors import (
    DeterminismIncomplete,
    MissingnessLeaks,
    ShapeNotImplemented,
    TransformedDisclosureUnrepresentable,
    UngroundedCell,
)
from .plan import MaterialisationPlan
from .reader import EstateReader
from .receipt import (
    CellCounts,
    ExecutorRecord,
    LeakCheckResult,
    PointerManifest,
    Receipt,
    RowCounts,
    ShapedBy,
    verdict_counts,
)
from .shapes import build_chunk_corpus, build_conversational, build_graph_tensor, build_tabular

#: The version this executor stamps into every receipt. A digest is executor-relative, so the
#: receipt has to say which executor produced it.
EXECUTOR_VERSION = "walnut-mat 0.1.0"


@runtime_checkable
class EmissionSink(Protocol):
    """Where a materialised dataset registers itself with the reverse pointer index.

    A `Protocol`, not an import of `databridge_graph`: this package has no dependency on the graph
    substrate today (not even transitively — it depends on core, adapter and harness only), and a
    dataset-shaping package should not have to acquire one just to log a citation.
    `ReverseIndex.record_emission` already has exactly this shape, so a real
    `GraphStore.reverse_index()` satisfies it with no import in either direction; composition
    happens wherever `materialise()` is called.
    """

    def record_emission(self, kind: str, ref: str, pointers: Iterable[SourcePointer], *,
                        plan_hash: Optional[str] = None, ledger_bound: bool = False,
                        emitted_at: Optional[datetime] = None) -> int: ...

#: The derivation rules THIS package applies while shaping. Any other `derived` pointer arriving
#: from below was produced by the adapter applying a policy `transform` to a disclosure — which is
#: the O1b case. Keeping the screen as "not one of ours" rather than "not one of these named
#: masking rules" means a new masking rule added to the estate is caught the day it lands, instead
#: of the day somebody remembers to add it to a list here.
_SHAPER_DERIVATIONS: frozenset[str] = frozenset({"text_slice", "count_rows"})

_BUILDERS = {
    "tabular": build_tabular,
    "chunk_corpus": build_chunk_corpus,
    "graph_tensor": build_graph_tensor,
    "conversational": build_conversational,
}


@dataclass(frozen=True)
class Materialisation:
    """What a build produced: the rectangle, the receipt, and the compiled plan behind both."""

    compiled: CompiledPlan
    dataset: Dataset
    receipt: Receipt

    @property
    def plan(self) -> MaterialisationPlan:
        return self.compiled.plan


# ---------------------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------------------
def screen_transformed_cells(dataset: Dataset) -> None:
    """The O1b screen, at emission. See `compiler._check_transformed_disclosure` for the argument.

    A `derived` pointer whose rule is not one of this package's own shaping rules is a
    transformation the POLICY applied to a value on its way out — a masked, generalised or
    otherwise partial disclosure. The three cell states cannot represent it: calling it grounded
    claims a disclosure that did not happen, calling it withheld hides that a value reached the
    consumer. Refuse, and name the gap.
    """
    for row in dataset.rows:
        for cell in row:
            pointer = pointer_of(cell)
            if pointer is None or pointer.kind != "derived":
                continue
            call = parse_derivation(pointer.derivation or "")
            if call.name in _SHAPER_DERIVATIONS:
                continue
            raise TransformedDisclosureUnrepresentable(
                f"a cell reached the dataset carrying derivation {call.render()!r}, which this "
                f"materialiser did not apply — so policy transformed the value on its way out of "
                f"the adapter. That is a TRANSFORMED DISCLOSURE, and spine §9 **O1b** is that the "
                f"cell vocabulary has no state for one: it is neither GroundedValue (nothing was "
                f"fully disclosed), nor Withheld (a value did reach the consumer), nor Absent. A "
                f"Receipt over this dataset would have to claim one of the two, and both are "
                f"false. The fourth state exists — `Transformed`, in "
                f"databridge_core.provenance since 2026-08-16 — but the adapter does not emit "
                f"one: its Cell union is a narrower three-state type that fails closed, so what "
                f"arrived here is a partial disclosure wearing a full disclosure's type. "
                f"Materialisation refused rather than guessed")


def screen_pointer_requirement(dataset: Dataset) -> None:
    """Every cell is a `GroundedValue` with a pointer, a `Withheld`, or an `Absent`. Nothing else."""
    for r, row in enumerate(dataset.rows):
        for column, cell in zip(dataset.columns, row):
            if isinstance(cell, GroundedValue):
                if cell.pointer is None:                         # pragma: no cover - type-bound
                    raise UngroundedCell(f"row {r} column {column!r}: value with no pointer")
                continue
            if isinstance(cell, (Withheld, Absent)):
                continue
            raise UngroundedCell(
                f"row {r} column {column!r} is a bare {type(cell).__name__}. A cell is "
                f"GroundedValue | Withheld | Absent and nothing else; a bare value is a number "
                f"nobody can trace to a source")


#: The non-grounded cell states whose per-class RATE the leak check compares (R-021 round 2,
#: Finding 5), each its own channel, never summed. `Withheld` is policy suppression. `Absent` is
#: "no value the read could cite" — and on this platform that includes READ ARTEFACTS (a version
#: superseded between the decision instant and the read instant, or an attribute stored without a
#: pointer of its own, reads Absent: see `platform_reader`), so an Absent mask can carry the label
#: exactly as a suppression mask can.
MASK_STATES: tuple[tuple[str, type], ...] = (("policy_suppressed", Withheld),
                                             ("absent", Absent))

#: Relations whose Absent is the join's own DEFINED value, not a missing one: "no earlier row for
#: this key was recorded by T_d" (`as_of_prior`) or nothing to count (`aggregate_count`). Serving
#: computes the identical point-in-time join and sees the identical Absent, so its correlation
#: with the label is signal, not leakage; it is MEASURED and reported, not gated. Every other
#: column's Absent — a cohort row's own field, or a column whose relation is unknown (a shaped or
#: hand-built rectangle) — is gated, because the read cannot tell its artefacts apart.
ABSENT_IS_DEFINED_FOR: frozenset[str] = frozenset({"as_of_prior", "aggregate_count"})


def absent_exempt_columns(plan) -> frozenset[str]:
    """The output columns whose Absent is the join's defined value (`ABSENT_IS_DEFINED_FOR`)."""
    return frozenset(c.path for c in plan.shape.columns
                     if c.source.relation in ABSENT_IS_DEFINED_FOR)


def missingness_leak_check(dataset: Dataset, *, label_column: Optional[str],
                           max_class_delta: float,
                           absent_exempt: Iterable[str] = ()) -> LeakCheckResult:
    """Refuse a training set in which a missingness mask correlates with the label.

    For each non-grounded state (`MASK_STATES`: `policy_suppressed` and `absent`, measured
    separately), if its rate in any column differs across label classes by more than
    `max_class_delta`, the MASK leaks the label — a model reads it off which cells are missing.
    ``absent_exempt`` names columns whose Absent is the join's defined value (see
    `ABSENT_IS_DEFINED_FOR`): their Absent delta is reported in ``detail`` and not gated. It is a
    first cut: a permutation test on the mask is probably the statistically correct form, and that
    is stated rather than implied.

    A row whose LABEL is not grounded is not a class: the check fails naming how many (R-021
    round 2, Finding 5) rather than pooling every unresolved label into one ``None`` class.
    """
    if label_column is None or not dataset.rows:
        return LeakCheckResult(max_class_delta_observed=0.0, threshold=max_class_delta,
                               passed=True,
                               detail="no label column, so there are no classes to differ across")
    li = dataset.index(label_column)
    classes: dict[Any, list[int]] = {}
    unresolved = 0
    for r, row in enumerate(dataset.rows):
        cell = row[li]
        if not isinstance(cell, GroundedValue):
            unresolved += 1
            continue
        classes.setdefault(cell.value, []).append(r)
    if unresolved:
        return LeakCheckResult(
            max_class_delta_observed=1.0, threshold=max_class_delta, passed=False,
            worst_column=label_column,
            detail=f"{unresolved} row(s) have no grounded label ({label_column!r} is Withheld or "
                   f"Absent). An unresolved label is not a class, so the mask cannot be compared "
                   f"across classes for those rows; a training set with unlabelled rows is "
                   f"refused rather than pooled into a `None` class")
    if len(classes) < 2:
        return LeakCheckResult(max_class_delta_observed=0.0, threshold=max_class_delta,
                               passed=True,
                               detail=f"one label class ({list(classes)}), nothing to compare")

    exempt = frozenset(absent_exempt)
    worst, worst_column, worst_state = 0.0, None, ""
    reported, reported_column = 0.0, None
    for c, column in enumerate(dataset.columns):
        if column == label_column:
            continue
        for state, kind in MASK_STATES:
            rates = []
            for rows in classes.values():
                missing = sum(1 for r in rows if isinstance(dataset.rows[r][c], kind))
                rates.append(missing / len(rows))
            delta = max(rates) - min(rates)
            if kind is Absent and column in exempt:
                if delta > reported:
                    reported, reported_column = delta, column
                continue
            if delta > worst:
                worst, worst_column, worst_state = delta, column, state

    passed = worst <= max_class_delta
    detail = (f"worst missingness rate difference across label classes is {worst:.4f}"
              + (f" ({worst_state} mask) on column {worst_column!r}" if worst_column else "")
              + f", threshold {max_class_delta:.4f}; masks measured separately: "
                f"{', '.join(s for s, _ in MASK_STATES)}"
              + (f". Not gated (the join's defined 'no prior row' value, identical at serving): "
                 f"absent mask delta {reported:.4f} on {reported_column!r}"
                 if reported_column else "")
              + ". First cut: a permutation test on the mask is probably the statistically "
                "correct form")
    return LeakCheckResult(max_class_delta_observed=worst, threshold=max_class_delta,
                           passed=passed, worst_column=worst_column, detail=detail)


def assert_no_missingness_leak(dataset: Dataset, *, label_column: Optional[str],
                               max_class_delta: float,
                               absent_exempt: Iterable[str] = ()) -> LeakCheckResult:
    """The gate `materialise` runs. Returns the result, or refuses.

    A separate function from the measurement so that the refusal itself is testable: on an estate
    where policy happens to suppress nothing, a test that only ever called `materialise` could not
    reach this branch, and an untested refusal is a refusal nobody has seen fire.
    """
    result = missingness_leak_check(dataset, label_column=label_column,
                                    max_class_delta=max_class_delta,
                                    absent_exempt=absent_exempt)
    if not result.passed:
        raise MissingnessLeaks(
            f"missingness leak check FAILED: {result.detail}. Suppression correlates with the "
            f"label (or an absence the read cannot vouch for does), so the mask itself carries "
            f"the signal suppression was meant to remove. "
            f"Refused rather than shipped with a warning — a training set whose null pattern "
            f"predicts the target looks excellent offline and is worthless online")
    return result


# ---------------------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------------------
def materialise(plan: MaterialisationPlan, reader: EstateReader, *,
                shaped_by: ShapedBy = "plan",
                out_dir: Optional[Path] = None,
                verify_sample: int = 0,
                shaping_directives: Sequence[str] = (),
                extra_notes: Sequence[str] = (),
                emission_sink: Optional[EmissionSink] = None) -> Materialisation:
    """Compile, screen, build, screen again, and emit the receipt.

    ``shaped_by`` defaults to ``"plan"`` because this function IS the deterministic pathway. The
    data agent passes ``"agent"`` when it applied its own shaping directives on top, and the
    receipt then reports the dataset as not reproducible — see `receipt.Receipt.reproducible`.

    ``emission_sink``, given, registers the built dataset with the reverse pointer index under its
    ``plan_hash`` — the dataset's identity — citing every pointer in it. `None` by default, so
    every existing call site builds exactly as it always has.
    """
    decision_tables = _decision_tables(plan, reader)
    compiled = compile_plan(plan, decision_tables=decision_tables)

    # The compiler (`_check_determinism`, R-014 Finding 11) only checks that a
    # `materialiser_version` was DECLARED — it has no access to this module's own constant without
    # an import cycle (`executor` already imports `compiler`). This is the other half: the
    # declaration must match the build actually running, or re-executing an unchanged plan against
    # pinned sources after upgrading this package could shape bytes differently under one identity.
    pinned_version = (compiled.plan.determinism.executor.materialiser_version
                      if compiled.plan.determinism.executor else "")
    if pinned_version != EXECUTOR_VERSION:
        raise DeterminismIncomplete(
            f"the plan pins determinism.executor.materialiser_version={pinned_version!r} and "
            f"this build runs {EXECUTOR_VERSION!r}. Re-executing this plan_hash today would not "
            f"run the same materialiser that produced any prior content_hash for it — a different "
            f"executor implementation can shape bytes differently under one dataset identity")

    builder = _BUILDERS.get(plan.shape.kind)
    if builder is None:
        raise ShapeNotImplemented(
            f"shape {plan.shape.kind!r} has no builder in this package. Implemented: "
            f"{sorted(_BUILDERS)}")
    build = builder(compiled, reader)
    dataset: Dataset = build.dataset

    screen_transformed_cells(dataset)
    screen_pointer_requirement(dataset)

    label_column = plan.shape.label.path if plan.shape.label is not None else None
    leak = assert_no_missingness_leak(
        dataset, label_column=label_column,
        max_class_delta=plan.policy.missingness_leak_check.max_class_delta,
        absent_exempt=absent_exempt_columns(plan))

    counts = dataset.cell_counts()
    pointers = dataset.pointers()
    verdicts = _verify_sample(reader, pointers, verify_sample)
    material_drift = verdicts.get(Verdict.DRIFT_MATERIAL.value, 0)
    if material_drift:
        # `Verdict.must_escalate` is True for exactly one state, and this is it: a CITED value has
        # changed since it was read. Before R-014 Finding 2 this was only tallied into the receipt
        # — invariant 2 requires that bytes no longer matching what they cite are refused, not
        # shipped with a count attached. UNVERIFIABLE and the other six states stay tallied only:
        # none of them says a citation is WRONG, only that this build did not (or could not)
        # re-check it.
        raise UngroundedCell(
            f"verify_sample re-resolved a prefix of this dataset's pointers and found "
            f"{material_drift} in {Verdict.DRIFT_MATERIAL.value!r} — a CITED value has changed "
            f"since it was read. Refused rather than shipped with a verdict count attached: "
            f"invariant 2 is that bytes differing from cited bytes are never emitted")

    notes: list[str] = [*compiled.notes, *getattr(build, "notes", ()), *extra_notes]
    if verify_sample <= 0:
        notes.append(
            "no pointer was re-resolved during this build: the cells are as-read under the "
            "snapshots pinned above, and `verdicts` is empty because nothing was verified. An "
            "empty verdict tally is not a claim that everything verified")
    elif any(p.predicate is not None for p in pointers):
        notes.append(
            "verify_sample excludes compressed-aggregate pointers (those carrying a `predicate`): "
            "their derived_from deliberately omits the per-row pointers that were counted, so "
            "there is nothing for a generic derivation replay to check them against. Verifiable "
            "only by re-running the predicate (`verify_compressed_aggregate`), not sampled here")

    output_uri = None
    croissant_uri = None
    if out_dir is not None:
        out_dir = Path(out_dir)
        output_uri = str(_write(dataset, plan, out_dir))
    receipt = Receipt(
        plan_hash=compiled.plan_hash,
        shaped_by=shaped_by,
        executed_at=datetime.now(timezone.utc),
        executor=ExecutorRecord(version=EXECUTOR_VERSION,
                                backend=(plan.determinism.executor.backend
                                         if plan.determinism.executor else "unknown"),
                                airgapped=plan.policy.egress == "air_gapped"),
        rows=RowCounts(emitted=getattr(build, "emitted", dataset.n_rows),
                       rejected_by_policy=getattr(build, "rejected_by_policy", 0),
                       rejected_by_maturity=getattr(build, "rejected_by_maturity", 0),
                       rejected_incomplete=getattr(build, "rejected_incomplete", 0),
                       rejected_unlabelled=getattr(build, "rejected_unlabelled", 0)),
        cells=CellCounts(**counts),
        verdicts=verdicts,
        missingness_leak_check=leak,
        pointer_manifest=PointerManifest(entries=len(pointers),
                                         digest=pointer_set_digest(pointers)),
        content_hash=dataset.content_hash(),
        policy_applied=plan.policy.purpose,
        policy_bundle_version=bundle_version_of(decision_tables),
        purpose=plan.authored_by.purpose,
        columns_fully_absent=dataset.fully_absent_columns(),
        columns_refused=tuple(getattr(build, "columns_refused", ())),
        features_unknown_at_decision=dict(getattr(build, "unknown_at_decision", {}) or {}),
        output_uri=output_uri,
        notes=tuple(notes),
        shaping_directives=tuple(shaping_directives),
    )
    if out_dir is not None and plan.output.metadata == "croissant/1.1":
        from .croissant import write_croissant

        croissant_uri = str(write_croissant(plan, receipt, dataset,
                                            out_dir / f"{plan.output_stem}.croissant.json"))
        receipt = receipt.model_copy(update={"croissant_uri": croissant_uri})
    if emission_sink is not None:
        # NOT inside a transaction — there is not one to be inside. Materialisation writes files
        # (`_write` above, Parquet or JSONL) and a receipt object; neither is a database row, so
        # unlike `ledger.commit` or `outbox.record_draft` there is no `with conn:` block whose
        # rollback this call could ride. Registration happens synchronously, immediately after the
        # receipt is built and after any file write, which is the closest analogue available: the
        # dataset is fully formed and its identity (`plan_hash`) and its full pointer set are both
        # final by this point, so nothing about the registration can be stale relative to what was
        # just produced. If the sink raises, the exception propagates out of `materialise()` and
        # the caller gets no `Materialisation` back — but any file already written by `_write`
        # above is NOT rolled back (the filesystem is not transactional either), so a failure here
        # can leave bytes on disk with no corresponding index entry. That gap is real and is not
        # papered over: it is the cost of registering a dataset whose own identity is a plan hash
        # over content, not a database row.
        #
        # `ref=compiled.plan_hash`: the dataset's identity, per spec. Every pointer the dataset
        # cites (`pointers`, computed above for the pointer manifest) registers under it, not only
        # a sample — a materialised dataset that cited a pointer the index never saw would make the
        # cascade's fan-out silently short of the truth for exactly the artefact this feature is
        # named after materialising.
        emission_sink.record_emission(
            kind="materialised_dataset", ref=compiled.plan_hash, pointers=pointers,
            plan_hash=compiled.plan_hash, ledger_bound=False, emitted_at=receipt.executed_at)
    return Materialisation(compiled=compiled, dataset=dataset, receipt=receipt)


def _decision_tables(plan: MaterialisationPlan,
                     reader: EstateReader) -> tuple[CompiledDecisionTable, ...]:
    """The policy tables for the reads this plan implies — obtained WITHOUT reading anything.

    Returns empty when the reader was built without a `PolicyEngine`; the O1b screen then falls
    back to the emission-time check, which is later and therefore worse, but never absent.
    """
    from .shapes.tabular import _needed_fields

    tables: list[CompiledDecisionTable] = []
    if plan.shape.kind == "chunk_corpus" and plan.shape.text_field is not None:
        s = plan.shape.text_field
        wanted = {s.entity: {f for f in (s.field, s.system_time_field, s.valid_time_field) if f}}
    else:
        wanted = {k: set(v) for k, v in _needed_fields(plan).items()}
    for entity, fields in wanted.items():
        # No `except KeyError: continue` here (R-014 Finding 1): every entity in `wanted` is one
        # the builder reads moments later through this SAME reader, so swallowing a lookup failure
        # here does not avoid it — it only loses the O1b pre-screen for that entity silently and
        # defers the same error to a less informative crash a few lines further on. Fail loud, at
        # the earliest point the mistake is known, per CLAUDE.md invariant 7.
        req = reader.request(entity, sorted(fields))
        table = reader.decision_table(req)
        if table is not None:
            tables.append(table)
    return tuple(tables)


def _verify_sample(reader: EstateReader, pointers: Sequence, sample: int) -> dict[str, int]:
    """Re-resolve a DETERMINISTIC prefix of the pointer set and tally the verdicts.

    A prefix under the dataset's own row order rather than a random draw: a sample whose members
    change between runs makes the receipt's verdict tally unreproducible, and a receipt field that
    moves without the data moving is a field nobody trusts.

    Pointers carrying a `predicate` (compressed aggregates, `_aggregate_cell` in
    `shapes/tabular.py`) are excluded from the prefix. Their `derived_from` deliberately holds only
    the cohort row's own key pointer — the per-row pointers that were counted are compressed away
    on purpose (§17.1) — so a generic adapter `verify()` that replays the derivation rule over
    `derived_from` has nothing to replay it over and reports a false `DRIFT_MATERIAL` (confirmed:
    `count_rows@v1` replayed against one parent value returns `1`, which almost never equals the
    row count the pointer actually cites). The honest re-verification for this shape is
    `verify_compressed_aggregate`, re-running the PREDICATE against the pinned snapshot — a
    heavier, caller-driven check this sampling loop does not perform. Excluding them here is
    routing around a known limitation of a path this package does not own
    (`packages/adapter`), not weakening what R-014 Finding 2 refuses: every pointer that CAN be
    honestly verified by `reader.verify()` still is, and a real `DRIFT_MATERIAL` among THOSE still
    refuses materialisation below.
    """
    if sample <= 0:
        return {}
    verdicts = []
    verifiable = [p for p in pointers if p.predicate is None]
    for pointer in verifiable[:sample]:
        try:
            verdicts.append(reader.verify(pointer))
        except Exception:                                        # pragma: no cover - adapter-bound
            verdicts.append(Verdict.UNVERIFIABLE)
    return verdict_counts(verdicts)


def _write(dataset: Dataset, plan: MaterialisationPlan, out_dir: Path) -> Path:
    """Write the dataset under its FULL-hash `output_stem`, in the plan's own declared format.

    Two things R-014 fixed here. **Finding 13:** file names use `plan.output_stem` (the full
    `plan_hash`), not the short `plan.id` (12 hex characters, 48 bits) — two distinct plans sharing
    an `id` prefix must never silently overwrite each other's output. **Finding 12:** every
    unimplemented `output.format` used to fall into the JSONL branch and write JSONL bytes under a
    name that did not say so; `output.format` is inside `plan_hash`, so writing the wrong bytes
    under the plan's own declared format is exactly the dishonest artefact `write_parquet`'s own
    missing-pyarrow refusal already exists to prevent for parquet specifically. Refused by name for
    every other undeclared format instead of guessed at.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    emit_reason = plan.shape.emit_missing_reason
    fmt = plan.output.format
    if fmt == "parquet":
        path = out_dir / f"{plan.output_stem}.parquet"
        write_parquet(dataset, path, emit_missing_reason=emit_reason)
    elif fmt == "jsonl":
        path = out_dir / f"{plan.output_stem}.jsonl"
        write_jsonl(dataset, path, emit_missing_reason=emit_reason)
    else:
        raise RuntimeError(
            f"output.format is {fmt!r} and this package has no writer for it. Implemented: "
            f"parquet, jsonl. Refused rather than silently written under the wrong format's bytes "
            f"and name: output.format is inside plan_hash, so writing jsonl for a plan that names "
            f"{fmt!r} would produce a file that does not match the dataset the plan identifies")
    return path


__all__ = ["EXECUTOR_VERSION", "Materialisation", "assert_no_missingness_leak",
           "absent_exempt_columns", "MASK_STATES", "ABSENT_IS_DEFINED_FOR",
           "materialise", "missingness_leak_check",
           "screen_pointer_requirement", "screen_transformed_cells"]
