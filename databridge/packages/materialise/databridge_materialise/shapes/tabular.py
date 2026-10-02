"""The tabular shape — SYSTEM_DESIGN §6.6(a), executed.

One row per cohort row. Features cut on **system time**, per row, at that row's own decision
instant. Label cut on **valid time**, read at the latest system time so corrections are included,
and gated on maturity so an outcome that has not yet been recorded is *rejected* rather than
labelled negative.

**The join that everyone gets wrong.** `as_of_prior` finds the most recent earlier row for the
same key **that had been RECORDED by T_d**. Feast's default point-in-time join constrains only the
feature's event timestamp, which admits a value backfilled or corrected after the entity
timestamp; closing it requires opting in to `filter_by_created_timestamp=True`. Here it is not an
option: the system-time filter runs first and there is no parameter that removes it. Against the
synthetic finance estate the difference is measurable — see
`test_the_valid_time_cut_would_have_leaked`.

**Nothing here produces a value.** Every emitted cell is a cell the adapter returned, or a
`derived` pointer over cells the adapter returned whose rule is registered and replayable, or an
`Absent` naming the question that went unanswered. There is no branch that computes a substitute
for a missing feature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from databridge_adapter import assert_provenance_present, canonical_json
from databridge_core import (
    Absent,
    GroundedValue,
    SourcePointer,
    Withheld,
    trust_from_parents,
)

from ..compiler import CompiledPlan
from ..dataset import Cell, Dataset
from ..derivations import rows_digest
from ..errors import UngroundedCell
from ..plan import Column, MaterialisationPlan
from ..reader import EntityRead, EstateReader
from ..durations import parse_duration
from ..temporal import (
    in_force_at,
    known_at,
    label_in_window,
    label_horizon,
    label_is_mature,
    label_window_closed,
    to_instant,
    within_lookback,
)


@dataclass
class TabularBuild:
    """The rectangle plus the accounting that makes the receipt honest."""

    dataset: Dataset
    emitted: int = 0
    rejected_by_policy: int = 0
    rejected_by_maturity: int = 0
    rejected_incomplete: int = 0
    #: E2E run 4 (Q-07): the label's window has CLOSED and no outcome was recorded inside it. Not
    #: immature (waiting will not help) and not a negative example (nothing was observed), so it
    #: is counted apart from maturity — the run-4 receipt called such a row "immature".
    rejected_unlabelled: int = 0
    #: Per feature column: how many emitted rows had NO value known at their own decision instant
    #: T_d because every version the store holds was recorded after T_d (the system-time cut), as
    #: opposed to the source holding no value at all.
    unknown_at_decision: dict[str, int] = field(default_factory=dict)
    #: Feature columns refused because the cut left them empty on EVERY emitted row, each with
    #: its count and reason. Such a column is excluded from the rectangle: an all-null column
    #: whose companion says `not_in_source` would tell a trainer the source lacks the value, when
    #: in fact the source has it and it simply was not known at T_d.
    columns_refused: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    reads: tuple[EntityRead, ...] = ()
    #: Diagnostics the temporal contract produced against this estate. Reported, because "how many
    #: rows would a valid-time cut have changed" is the measurement that shows the rule earning
    #: its keep rather than being asserted.
    leak_probe: dict[str, int] = field(default_factory=dict)
    #: FIN-13 (finance dry run): WHY rows were not labelled, computed from the label versions the
    #: store holds, and the window / re-run instant that WOULD label rows. `None` when no row was
    #: rejected for a label reason.
    label_diagnosis: Optional[dict] = None


def _needed_fields(plan: MaterialisationPlan) -> dict[str, set[str]]:
    """Which fields each entity must project. One read per entity, not one per column."""
    needed: dict[str, set[str]] = {}

    def want(entity: str, *fields: Optional[str]) -> None:
        bucket = needed.setdefault(entity, set())
        for f in fields:
            if f:
                bucket.add(f)

    cohort = plan.selection.entity
    want(cohort, plan.selection.cohort.decision_time_field)
    for column in plan.shape.columns:
        s = column.source
        want(s.entity, s.field, s.system_time_field, s.valid_time_field, s.join_key)
        if s.relation in ("as_of_prior", "aggregate_count") and s.join_key:
            want(cohort, s.join_key)
    label = plan.shape.label
    if label is not None:
        want(label.source.entity, label.source.field, label.source.valid_time_field,
             label.source.system_time_field, label.source.join_key)
        want(cohort, label.source.join_key)
    return needed


def _index_by_key(read: EntityRead, join_key: str) -> dict[Any, list[Mapping[str, Cell]]]:
    out: dict[Any, list[Mapping[str, Cell]]] = {}
    for row in read.rows:
        value = read.value(row, join_key)
        if value is None:
            # A row whose join key was withheld or is NULL cannot participate in a keyed join.
            # It is not dropped silently: it simply never matches, and the counts below say so.
            continue
        out.setdefault(value, []).append(row)
    return out


def _absent(plan: MaterialisationPlan, column: Column, snapshot) -> Absent:
    """The honest answer for a feature the organisation had not learned by T_d.

    `Absent` carries the snapshot the question was asked of, so the claim "this was not in the
    source" is itself re-executable rather than an assertion.
    """
    return Absent(field_path=f"{column.source.entity}.{column.source.field}", snapshot=snapshot)


def _aggregate_cell(column: Column, anchor: Cell, contributing: Sequence[str],
                    snapshot, predicate: str) -> Cell:
    """A count over prior rows, emitted as a **compressed `derived` pointer** (§17.1).

    The compression drops the per-row pointers and keeps the trust-bearing root: `derived_from`
    holds the cohort row's own key pointer, so the trust join has something to propagate from and
    the value cannot be laundered to TRUSTED over untrusted inputs. `predicate`, `row_count` and
    `rows_digest` are what make it verifiable by RE-EXECUTION rather than by trust — re-run the
    predicate against the pinned snapshot and compare the digest.
    """
    if not isinstance(anchor, GroundedValue):
        # No anchor means no trust-bearing root and no citable row. An aggregate over rows nobody
        # can characterise is exactly what §17.1 refuses, so the cell is absent instead.
        return Absent(field_path=f"{column.source.entity}.{column.source.field}",
                      snapshot=snapshot)
    parent = anchor.pointer
    pointer = SourcePointer(
        source_id=parent.source_id,
        kind="derived",
        derivation=(f"count_rows@v1(entity={column.source.entity},"
                    f"key={column.source.join_key},cut=system_time_le_decision)"),
        derived_from=[parent],
        predicate=predicate,
        row_count=len(contributing),
        rows_digest=rows_digest(contributing),
        sample_row_ids=list(contributing[:20]) or None,
        snapshot=snapshot,
        trust=trust_from_parents([parent]),
    )
    cell = GroundedValue(value=len(contributing), pointer=pointer, confidence=1.0)
    # The WEAKER check, deliberately and with the reason: the parents are compressed away, so the
    # wall cannot replay the rule over them. What is established here is that the provenance is
    # structurally complete; what establishes the VALUE is re-running the predicate, which
    # `verify_compressed_aggregate` does.
    assert_provenance_present(cell)
    return cell


def verify_compressed_aggregate(cell: GroundedValue, contributing_row_ids: Sequence[str]) -> None:
    """Re-execution check for a compressed aggregate: same rows, same count, same digest.

    This is the half of the guarantee `assert_provenance_present` deliberately does not give. A
    caller that can re-run the predicate against the pinned snapshot calls this with the row ids it
    got back; a mismatch means the value no longer describes the rows it claims to summarise.
    """
    pointer = cell.pointer
    if pointer.kind != "derived" or pointer.predicate is None:
        raise UngroundedCell(
            f"verify_compressed_aggregate expects a compressed derived pointer, got "
            f"{pointer.kind!r} with predicate={pointer.predicate!r}")
    observed = rows_digest(contributing_row_ids)
    if pointer.rows_digest != observed:
        raise UngroundedCell(
            f"compressed aggregate cites rows_digest {pointer.rows_digest[:12]}… and the "
            f"re-execution produced {observed[:12]}…: the value no longer summarises the rows it "
            f"claims to. This is the compression path's whole check — a count with no per-row "
            f"pointers is verifiable only by re-running the predicate")
    if pointer.row_count != len(contributing_row_ids):
        raise UngroundedCell(
            f"compressed aggregate cites row_count {pointer.row_count} and the re-execution "
            f"returned {len(contributing_row_ids)}")
    if cell.value != pointer.row_count:
        raise UngroundedCell(
            f"compressed aggregate emits {cell.value!r} beside row_count {pointer.row_count}: the "
            f"value and its own provenance disagree")


def build_tabular(compiled: CompiledPlan, reader: EstateReader) -> TabularBuild:
    """Execute an accepted tabular plan. Deterministic: no model, no clock beyond the pinned T_b."""
    plan = compiled.plan
    cohort_entity = plan.selection.entity
    decision_field = plan.selection.cohort.decision_time_field or ""
    build_time = plan.temporal.build_time
    label = plan.shape.label

    reads: dict[str, EntityRead] = {}
    for entity, fields in _needed_fields(plan).items():
        reads[entity] = reader.read(entity, sorted(fields))
    cohort_read = reads[cohort_entity]
    snapshot = cohort_read.snapshot

    # Prior-row indexes, one per (entity, join_key) actually used.
    indexes: dict[tuple[str, str], dict[Any, list[Mapping[str, Cell]]]] = {}
    for column in plan.feature_columns:
        s = column.source
        if s.relation in ("as_of_prior", "aggregate_count") and s.join_key:
            key = (s.entity, s.join_key)
            if key not in indexes:
                indexes[key] = _index_by_key(reads[s.entity], s.join_key)
    if label is not None:
        key = (label.source.entity, label.source.join_key)
        if key not in indexes:
            indexes[key] = _index_by_key(reads[label.source.entity], label.source.join_key)

    columns = tuple(c.path for c in plan.shape.columns)
    label_path = label.path if label is not None else None
    out_columns = columns + ((label_path,) if label_path else ())

    label_is_state = label is not None and _label_is_row_state(plan)

    probe_column = next((c.path for c in plan.feature_columns
                         if c.source.relation == "as_of_prior"), None)

    rows: list[tuple[Cell, ...]] = []
    #: Per emitted row, the feature columns whose cell is Absent BECAUSE OF the T_d cut.
    row_cuts: list[set[str]] = []
    unknown_at_decision: dict[str, int] = {}
    rejected_policy = rejected_maturity = rejected_incomplete = rejected_unlabelled = 0
    #: Per cohort row that reached the label read: (T_d, outcome, label version instants).
    label_obs: list[tuple[datetime, str, list[datetime]]] = []
    leak_probe = {"rows": 0, "system_time_cut_has_prior": 0, "valid_time_cut_has_prior": 0,
                  "cut_disagrees": 0}

    for cohort_row in cohort_read.rows:
        leak_probe["rows"] += 1
        decision_cell = cohort_row.get(decision_field)
        if isinstance(decision_cell, Withheld):
            # The decision instant itself is not disclosed to this principal, so no point-in-time
            # cut can be computed for the row at all. Counted as a POLICY rejection, because that
            # is what it is, and reported apart from maturity.
            rejected_policy += 1
            continue
        decision_time = to_instant(decision_cell.value
                                   if isinstance(decision_cell, GroundedValue) else None)
        if decision_time is None:
            rejected_incomplete += 1
            continue

        # --- label first: a row with no mature label is never emitted -----------------
        label_cell: Optional[Cell] = None
        if label is not None:
            candidates = indexes[(label.source.entity, label.source.join_key)].get(
                cohort_read.value(cohort_row, label.source.join_key), [])
            outcome, label_cell = _read_label(plan, candidates, decision_time, build_time,
                                              state=label_is_state)
            label_obs.append((decision_time, outcome, _label_instants(plan, candidates)))
            if outcome == "immature":
                rejected_maturity += 1
                continue
            if outcome == "unlabelled":
                rejected_unlabelled += 1
                continue
            if isinstance(label_cell, Withheld):
                rejected_policy += 1
                continue

        # --- features -----------------------------------------------------------------
        cells: list[Cell] = []
        cut_here: set[str] = set()
        for column in plan.shape.columns:
            # The probe is recorded on the FIRST prior-join column only: it measures a property of
            # the CUT, not of each column, and counting it once per column would multiply the
            # number by however many features happen to share the join.
            probe = column.path == probe_column
            cut: list[bool] = []
            cells.append(_feature_cell(plan, column, cohort_read, cohort_row, reads, indexes,
                                       decision_time, snapshot,
                                       leak_probe if probe else None, cut))
            if cut:
                cut_here.add(column.path)
                unknown_at_decision[column.path] = unknown_at_decision.get(column.path, 0) + 1
        row_cuts.append(cut_here)
        if label_cell is not None:
            cells.append(label_cell)
        rows.append(tuple(cells))

    dataset = Dataset(columns=out_columns, rows=tuple(rows),
                      float_scale=plan.determinism.float_scale)
    if plan.determinism.row_order:
        dataset = dataset.sorted_by(plan.determinism.row_order)
    dataset = _assign_splits(plan, dataset)

    # --- features the T_d cut emptied on every emitted row: refused, by name -----------
    refused_paths = [c.path for c in plan.feature_columns
                     if row_cuts and all(c.path in cut for cut in row_cuts)]
    refused: list[str] = []
    for path in refused_paths:
        refused.append(
            f"{path}: not known at the decision instant on any of {len(row_cuts)} emitted "
            f"rows — the store holds the value, but every version of it was first recorded "
            f"after T_d{_first_recorded(plan, path, reads)}. Excluded rather than emitted as an "
            f"all-null column whose missing reason would read `not_in_source`")
    if refused_paths:
        keep = [i for i, name in enumerate(dataset.columns) if name not in refused_paths]
        dataset = Dataset(columns=tuple(dataset.columns[i] for i in keep),
                          rows=tuple(tuple(row[i] for i in keep) for row in dataset.rows),
                          splits=dataset.splits, float_scale=dataset.float_scale)

    notes: list[str] = []
    for path, n in sorted(unknown_at_decision.items()):
        if path in refused_paths:
            continue
        notes.append(
            f"temporal cut: {path} was not yet recorded at the decision instant on {n} of "
            f"{len(row_cuts)} emitted rows; those cells are Absent because of the cut, not "
            f"because the source lacks the value")
    if rejected_unlabelled:
        notes.append(
            f"{rejected_unlabelled} cohort rows were excluded because their label window has "
            f"closed and no outcome was recorded inside it: not immature, and not negative "
            f"examples")
    diagnosis = (_diagnose_label(plan, label_obs, build_time, state=label_is_state)
                 if rejected_unlabelled or rejected_maturity else None)
    if diagnosis is not None:
        notes.append(diagnosis["explanation"])
    if leak_probe["cut_disagrees"]:
        notes.append(
            f"temporal contract, measured: {leak_probe['cut_disagrees']} of {leak_probe['rows']} "
            f"cohort rows would have received a DIFFERENT prior-value feature under a valid-time "
            f"cut (the Feast default) than under the system-time cut this plan compiled to")
    return TabularBuild(dataset=dataset, emitted=dataset.n_rows,
                        rejected_by_policy=rejected_policy,
                        rejected_by_maturity=rejected_maturity,
                        rejected_incomplete=rejected_incomplete,
                        rejected_unlabelled=rejected_unlabelled,
                        unknown_at_decision=dict(sorted(unknown_at_decision.items())),
                        columns_refused=tuple(refused),
                        notes=tuple(notes), reads=tuple(reads.values()),
                        leak_probe=leak_probe, label_diagnosis=diagnosis)


def _label_instants(plan: MaterialisationPlan,
                    candidates: Sequence[Mapping[str, Cell]]) -> list[datetime]:
    """The valid-time instants of the label versions the store holds for one cohort row."""
    label = plan.shape.label
    out: list[datetime] = []
    for candidate in candidates:
        cell = candidate.get(label.source.valid_time_field)
        instant = to_instant(cell.value if isinstance(cell, GroundedValue) else None)
        if instant is not None:
            out.append(instant)
    return sorted(out)


def _days_ceil(delta: timedelta) -> int:
    whole = delta.days
    return whole + (1 if delta - timedelta(days=whole) > timedelta(0) else 0)


def _diagnose_label(plan: MaterialisationPlan,
                    observed: Sequence[tuple[datetime, str, list[datetime]]],
                    build_time: datetime, *, state: bool) -> Optional[dict]:
    """FIN-13: say exactly WHY rows went unlabelled or immature, and what WOULD label rows.

    Everything here is computed from the label versions the store holds and the plan's own
    declarations (decision instants, window, lag, T_b). Nothing is recalled.

    * **unlabelled** (state label): no version of the label was in force at the row's horizon
      ``T_d + within``; the earliest version the store holds came into force later. The diagnosis
      names that instant and the horizons, so the gap is visible.
    * **immature**: the horizon has not arrived by ``T_b - lag``. The diagnosis names the instant
      from which a re-run labels the first of them, and the instant by which all would be.
    * **best window**: the whole-day ``within`` that labels the most rows against THIS store,
      found by sweeping every row's feasible interval. For a state label a row is labelled iff its
      earliest version ``v0`` satisfies ``v0 <= T_d + within <= T_b - lag``; for an event label
      iff an outcome ``o`` with ``T_d < o <= T_b - lag`` falls inside the window.
    """
    if not observed:
        return None
    label = plan.shape.label
    within = plan.temporal.label_window.within
    lag = parse_duration(plan.temporal.label_maturity_lag)
    cutoff = build_time - lag
    field_name = f"{label.source.entity}.{label.source.field}"
    versions = [v for _, _, vs in observed for v in vs]
    unlabelled = [(td, vs) for td, outcome, vs in observed if outcome == "unlabelled"]
    immature = [(td, vs) for td, outcome, vs in observed if outcome == "immature"]
    labelled = sum(1 for _, outcome, _ in observed if outcome == "ok")

    # --- the whole-day window that labels the most rows ----------------------------------
    intervals: list[tuple[int, Optional[int]]] = []
    for td, _, vs in observed:
        if state:
            if not vs:
                continue
            lo, hi = max(_days_ceil(vs[0] - td), 1), (cutoff - td).days
            if lo <= hi:
                intervals.append((lo, hi))
        else:
            after = [v for v in vs if td < v <= cutoff]
            if after:
                intervals.append((max(_days_ceil(after[0] - td), 1), None))
    best_days, best_rows = None, 0
    for lo, _ in intervals:
        n = sum(1 for a, b in intervals if a <= lo and (b is None or lo <= b))
        if n > best_rows or (n == best_rows and best_days is not None and lo < best_days):
            best_days, best_rows = lo, n

    per_row: dict[int, int] = {}
    for _, _, vs in observed:
        per_row[len(vs)] = per_row.get(len(vs), 0) + 1
    single_version = bool(versions) and max(per_row) <= 1

    diagnosis: dict[str, Any] = {
        "label": label.path,
        "reading": "state" if state else "event",
        "window": within,
        "maturity_lag": plan.temporal.label_maturity_lag,
        "build_time": build_time.isoformat(),
        "rows_read": len(observed),
        "labelled": labelled,
        "unlabelled": len(unlabelled),
        "immature": len(immature),
        "label_versions": len(versions),
        "label_versions_in_force_from": min(versions).isoformat() if versions else None,
        "label_versions_in_force_to": max(versions).isoformat() if versions else None,
        "max_versions_per_row": max(per_row) if per_row else 0,
        "best_window": ({"within": f"P{best_days}D", "rows": best_rows, "of": len(observed)}
                        if best_days is not None else None),
    }
    parts: list[str] = [f"label diagnosis for {field_name} ({diagnosis['reading']} label, window "
                        f"{within}, lag {plan.temporal.label_maturity_lag}):"]
    if unlabelled:
        horizons = [label_horizon(td, within) for td, _ in unlabelled]
        earliest_known = [vs[0] for _, vs in unlabelled if vs]
        diagnosis["unlabelled_horizons"] = [min(horizons).isoformat(), max(horizons).isoformat()]
        if state and earliest_known:
            parts.append(
                f"{len(unlabelled)} row(s) are unlabelled because their horizons T_d+{within} "
                f"fall between {min(horizons).isoformat()} and {max(horizons).isoformat()}, and "
                f"the earliest version of {field_name} the store holds for any of them came into "
                f"force at {min(earliest_known).isoformat()}, so the store does not know the "
                f"value at those horizons.")
        else:
            parts.append(
                f"{len(unlabelled)} row(s) are unlabelled because their windows (horizons "
                f"{min(horizons).isoformat()} to {max(horizons).isoformat()}) closed with no "
                f"version of {field_name} recorded inside them.")
    if immature:
        due = sorted(label_horizon(td, within) + lag for td, _ in immature)
        diagnosis["immature_mature_from"] = due[0].isoformat()
        diagnosis["immature_mature_by"] = due[-1].isoformat()
        parts.append(
            f"{len(immature)} row(s) are immature: their horizons have not arrived by T_b - lag "
            f"({cutoff.isoformat()}). Re-running on or after {due[0].isoformat()} labels the "
            f"first of them; on or after {due[-1].isoformat()}, all of them.")
    if single_version:
        lo_v, hi_v = min(versions), max(versions)
        parts.append(
            f"The store holds ONE version of {field_name} per row, in force from "
            f"{lo_v.isoformat()}" + (f" to {hi_v.isoformat()}" if hi_v != lo_v else "")
            + ": the source carries no history for it, so it answers only horizons on or after "
              "that instant. History accrues as the source is re-read over time; a source that "
              "records when each value took effect would answer earlier horizons.")
    if best_days is not None:
        parts.append(f"Suggestion: label window P{best_days}D would label {best_rows} of "
                     f"{len(observed)} row(s) against this store.")
    else:
        parts.append(f"Suggestion: no whole-day label window labels any of the {len(observed)} "
                     f"row(s) against this store"
                     + (f"; re-run on or after {diagnosis['immature_mature_from']} instead."
                        if immature else "."))
    diagnosis["explanation"] = " ".join(parts)
    return diagnosis


def _feature_cell(plan: MaterialisationPlan, column: Column, cohort_read: EntityRead,
                  cohort_row: Mapping[str, Cell], reads: dict[str, EntityRead],
                  indexes: dict[tuple[str, str], dict[Any, list[Mapping[str, Cell]]]],
                  decision_time: datetime, snapshot,
                  leak_probe: Optional[dict[str, int]],
                  cut: Optional[list[bool]] = None) -> Cell:
    """One feature cell. When the cell is Absent BECAUSE OF the T_d cut — the store holds a value
    but no version of it was recorded by T_d — `True` is appended to ``cut``, so the build can
    count it and say so, instead of reporting it as a value the source does not have."""
    s = column.source

    def cut_absent() -> Cell:
        if cut is not None:
            cut.append(True)
        return _absent(plan, column, snapshot)

    if s.relation in ("cohort_key", "decision_time"):
        # Axis and identity columns are what the cut is computed FROM. Cutting them by it would
        # remove the row's own identity from the row.
        return cohort_row.get(s.field, Absent(field_path=f"{s.entity}.{s.field}",
                                              snapshot=snapshot))

    if s.relation == "cohort_row":
        held = isinstance(cohort_row.get(s.field), GroundedValue)
        if not known_at(cohort_read.value(cohort_row, s.system_time_field), decision_time):
            return cut_absent() if held else _absent(plan, column, snapshot)
        if (plan.temporal.feature_valid_filter == "in_force_at_decision"
                and s.valid_time_field
                and not in_force_at(cohort_read.value(cohort_row, s.valid_time_field),
                                    decision_time)):
            return cut_absent() if held else _absent(plan, column, snapshot)
        return cohort_row.get(s.field, _absent(plan, column, snapshot))

    source_read = reads[s.entity]
    bucket = indexes[(s.entity, s.join_key)].get(
        cohort_read.value(cohort_row, s.join_key), [])

    if s.relation == "as_of_prior":
        system_ok = [r for r in bucket
                     if known_at(source_read.value(r, s.system_time_field), decision_time)
                     and _strictly_before(source_read.value(r, s.valid_time_field),
                                          decision_time)]
        valid_only = [r for r in bucket
                      if _strictly_before(source_read.value(r, s.valid_time_field), decision_time)]
        if leak_probe is not None:
            _record_probe(leak_probe, source_read, s, system_ok, valid_only)
        if not system_ok:
            # Cut, not missing: a prior row exists on VALID time, but none was recorded by T_d.
            held = any(isinstance(r.get(s.field), GroundedValue) for r in valid_only)
            return cut_absent() if held else _absent(plan, column, snapshot)
        system_ok.sort(key=lambda r: to_instant(source_read.value(r, s.valid_time_field)))
        return system_ok[-1].get(s.field, _absent(plan, column, snapshot))

    if s.relation == "aggregate_count":
        contributing = [r for r in bucket
                        if within_lookback(source_read.value(r, s.system_time_field),
                                           decision_time, s.window)]
        row_ids = sorted(rid for rid in (source_read.row_id(r) for r in contributing)
                         if rid is not None)
        predicate = canonical_json({
            "entity": s.entity, "join_key": s.join_key,
            "join_value": str(cohort_read.value(cohort_row, s.join_key)),
            "system_time_field": s.system_time_field,
            "cut": f"system_time <= {decision_time.isoformat()}",
            "window": s.window,
        })
        anchor = cohort_row.get(s.join_key)
        return _aggregate_cell(column, anchor, row_ids, snapshot, predicate)

    raise ValueError(f"unhandled relation {s.relation!r}")       # pragma: no cover - Literal-bound


def _label_is_row_state(plan: MaterialisationPlan) -> bool:
    """Is the label an attribute OF THE COHORT ROW ITSELF (a state), rather than a later event row?

    Structural and declared by the plan, never by what the entity is: the label reads the cohort
    entity, joined on the cohort's own key column. Such a label has one value per VERSION of the
    row, and its valid-time field is when that version came into force — not when an outcome
    happened. "The status 30 days after issue" is then the version in force AT `T_d + within`.

    E2E run 4 (Q-07): read as an event, a version stamped at the ingest instant fell inside every
    still-open window and became "the outcome", while a row whose window had closed before the
    ingest found no "event" in it and was counted immature.
    """
    label = plan.shape.label
    if label is None or label.source.entity != plan.selection.entity:
        return False
    return any(c.source.relation == "cohort_key" and c.source.field == label.source.join_key
               for c in plan.shape.columns)


def _read_label(plan: MaterialisationPlan, candidates: Sequence[Mapping[str, Cell]],
                decision_time: datetime, build_time: datetime, *,
                state: bool) -> tuple[str, Optional[Cell]]:
    """`("ok", cell)`, `("immature", None)` or `("unlabelled", None)` for one cohort row.

    *Immature* means waiting could still produce or change the label: its business instant has not
    arrived by `T_b - lag`, or a version/outcome that bears on it was not yet recorded by then.
    *Unlabelled* means the window has closed and the store records nothing that answers it — not a
    negative example, and not something waiting will fix.
    """
    label = plan.shape.label
    within = plan.temporal.label_window.within
    lag = plan.temporal.label_maturity_lag
    cutoff_open = not label_window_closed(decision_time, within, build_time, lag)

    def coords(candidate: Mapping[str, Cell]) -> tuple[Optional[datetime], Any]:
        valid = candidate.get(label.source.valid_time_field)
        system = candidate.get(label.source.system_time_field)
        return (to_instant(valid.value if isinstance(valid, GroundedValue) else None),
                system.value if isinstance(system, GroundedValue) else None)

    if state:
        # The label's business instant is the horizon itself. Not arrived => immature, whatever
        # the store holds now (that would be "today's status", not the status at the horizon).
        if cutoff_open:
            return "immature", None
        horizon = label_horizon(decision_time, within)
        in_force = [(v, sys, c) for c in candidates for v, sys in [coords(c)]
                    if v is not None and v <= horizon]
        if not in_force:
            # No version of the row was in force at the horizon: the earliest one the store holds
            # came into force later (e.g. the row was first observed after the window closed).
            return "unlabelled", None
        in_force.sort(key=lambda t: t[0])
        _, system_value, chosen = in_force[-1]
        if not label_is_mature(system_value, build_time, lag):
            return "immature", None
        return "ok", chosen.get(label.source.field)

    # Event label: the earliest outcome in (T_d, T_d + within], recorded by T_b - lag, whose own
    # business instant has arrived by T_b - lag.
    matured: list[tuple[datetime, Mapping[str, Cell]]] = []
    pending = 0
    for candidate in candidates:
        valid, system_value = coords(candidate)
        if valid is None or not label_in_window(valid, decision_time, within):
            continue
        if (not label_is_mature(system_value, build_time, lag)
                or not label_is_mature(valid, build_time, lag)):
            # Inside the window, but not yet recorded by T_b - lag — or an outcome dated in the
            # future of the cutoff. Without this guard it silently becomes a negative example.
            pending += 1
            continue
        matured.append((valid, candidate))
    if matured:
        matured.sort(key=lambda pair: pair[0])
        return "ok", matured[0][1].get(label.source.field)
    if pending or cutoff_open:
        # Either an outcome is waiting to be recorded, or the window is still open and one may
        # yet occur: right-censored, never a negative.
        return "immature", None
    return "unlabelled", None


def _first_recorded(plan: MaterialisationPlan, path: str,
                    reads: dict[str, EntityRead]) -> str:
    """`" (earliest recording: <instant>)"` for a refused column, from the rows' own system-time
    cells, so the refusal names the instant that would have had to precede T_d."""
    column = next((c for c in plan.feature_columns if c.path == path), None)
    if column is None or not column.source.system_time_field:
        return ""
    read = reads.get(column.source.entity)
    if read is None:
        return ""
    instants = [to_instant(read.value(r, column.source.system_time_field)) for r in read.rows
                if isinstance(r.get(column.source.field), GroundedValue)]
    instants = [i for i in instants if i is not None]
    return f" (earliest recording: {min(instants).isoformat()})" if instants else ""


def _strictly_before(valid_time: Any, decision_time: datetime) -> bool:
    instant = to_instant(valid_time)
    return instant is not None and instant < decision_time


def _record_probe(leak_probe: dict[str, int], read: EntityRead, source,
                  system_ok: Sequence[Mapping[str, Cell]],
                  valid_only: Sequence[Mapping[str, Cell]]) -> None:
    """Measure what the two cuts disagree about, on the first prior-join column of each row.

    This is a diagnostic, not a control — the control is that only `system_ok` is ever used. It is
    recorded because "the valid-time cut would have leaked" is a claim, and a claim about a real
    estate should come with a number.
    """
    if system_ok:
        leak_probe["system_time_cut_has_prior"] += 1
    if valid_only:
        leak_probe["valid_time_cut_has_prior"] += 1
    latest_system = (max((to_instant(read.value(r, source.valid_time_field)) for r in system_ok),
                         default=None))
    latest_valid = (max((to_instant(read.value(r, source.valid_time_field)) for r in valid_only),
                        default=None))
    if latest_system != latest_valid:
        leak_probe["cut_disagrees"] += 1


def _assign_splits(plan: MaterialisationPlan, dataset: Dataset) -> Dataset:
    """A TEMPORAL split, never a random one — and never computed from an untrusted column.

    D3 and the plan's own `trust_policy`: untrusted values may be features, and may never compute
    a split boundary. The split here reads the decision-time column, which is a first-party
    recorded instant; a plan that pointed it at free text would be computing control flow from
    attacker-writable content.
    """
    split = plan.determinism.split
    if split.strategy == "none" or not dataset.rows:
        return dataset
    if split.strategy == "random":                               # pragma: no cover - refused above
        raise ValueError("a random split over time-ordered rows leaks the future into training")

    decision_column = next((c.path for c in plan.shape.columns
                            if c.source.relation == "decision_time"), None)
    if decision_column is None:
        return dataset
    i = dataset.index(decision_column)
    labels: list[str] = []
    for row in dataset.rows:
        instant = to_instant(row[i].value if isinstance(row[i], GroundedValue) else None)
        if instant is None:
            labels.append("none")
            continue
        if split.train_before is not None and instant.date() < split.train_before:
            labels.append("train")
        elif split.val and instant.strftime("%Y-%m") == split.val:
            labels.append("val")
        elif split.test and instant.strftime("%Y-%m") == split.test:
            labels.append("test")
        else:
            labels.append("none")
    return Dataset(columns=dataset.columns, rows=dataset.rows, splits=tuple(labels),
                   float_scale=dataset.float_scale)


__all__ = ["TabularBuild", "build_tabular", "verify_compressed_aggregate"]
