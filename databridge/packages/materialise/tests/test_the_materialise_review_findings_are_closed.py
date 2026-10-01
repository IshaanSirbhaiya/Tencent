"""R-014 — one test per finding in `wiki/40-reviews/raw/phase4-materialise.md`.

Dispositions and reasoning live in `wiki/40-reviews/R-014-materialise-dispositions.md`. This file
is the proof: each test below is traced by hand through the pre-fix code path and confirmed to
fail on it (see the disposition doc for how each was verified) before being landed against the
fix. Findings 6 and 7 are FALSE POSITIVES and are pinned here rather than fixed — see their
docstrings for why.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from databridge_core import GroundedValue, SourcePointer, Trust, Withheld, WithholdingRecord
from databridge_adapter import Verdict
from databridge_materialise import (
    AirGapUnpinned,
    Column,
    ColumnSource,
    DeterminismIncomplete,
    LabelAxisRefused,
    NoSystemTimeAxis,
    PolicyNotInHash,
    UngroundedCell,
    compile_plan,
    materialise,
)

from plans import POLICY_BUNDLE_VERSION, SNAPSHOT, covenant_plan, facility_notes_corpus


# =============================================================================================
# Finding 1 (Critical) — `_decision_tables` swallowed a KeyError instead of failing loud
# =============================================================================================
def test_a_decision_table_lookup_failure_is_not_silently_swallowed():
    """Before the fix, `except KeyError: continue` made a lookup failure for one entity vanish —
    the O1b pre-screen quietly lost coverage for that entity instead of the mistake surfacing.
    `_decision_tables` is tested directly, in isolation from the real adapter, so the assertion is
    about THIS function's own control flow rather than about whether some downstream read happens
    to fail too."""
    from databridge_materialise.executor import _decision_tables

    class _BoomReader:
        def request(self, entity, fields, **kw):
            raise KeyError(f"source does not describe entity {entity!r}")

        def decision_table(self, req):                            # pragma: no cover - unreachable
            raise AssertionError("request() already raised; decision_table() must not be reached")

    with pytest.raises(KeyError):
        _decision_tables(covenant_plan(), _BoomReader())


# =============================================================================================
# Finding 2 (Critical) — a DRIFT_MATERIAL verdict from verify_sample was tallied, not refused
# =============================================================================================
def test_a_drift_material_verdict_from_verify_sample_refuses_materialisation(reader):
    """`Verdict.must_escalate` is True for exactly one state: a CITED value has changed since it
    was read. Before the fix this was tallied into `receipt.verdicts` and shipped; invariant 2 is
    that bytes no longer matching what they cite are refused, not counted."""

    class _AlwaysDrifted:
        def __getattr__(self, name):
            return getattr(reader, name)

        def verify(self, pointer):
            return Verdict.DRIFT_MATERIAL

    with pytest.raises(UngroundedCell, match="drift_material"):
        materialise(covenant_plan(), _AlwaysDrifted(), verify_sample=3)


def test_an_unverifiable_verdict_from_verify_sample_is_tallied_not_refused(reader):
    """The counterpart: UNVERIFIABLE (and every state besides DRIFT_MATERIAL) says 'we could not
    check this one', not 'this citation is wrong'. Only the one state that must_escalate refuses."""

    class _AlwaysUnverifiable:
        def __getattr__(self, name):
            return getattr(reader, name)

        def verify(self, pointer):
            return Verdict.UNVERIFIABLE

    built = materialise(covenant_plan(), _AlwaysUnverifiable(), verify_sample=3)
    assert built.receipt.verdicts.get("unverifiable") == 3


# =============================================================================================
# Finding 3 (Critical) — the runtime policy bundle was outside plan_hash
# =============================================================================================
def test_a_plan_with_no_policy_bundle_pin_is_refused_once_a_real_bundle_exists(reader):
    plan = covenant_plan(policy=covenant_plan().policy.model_copy(update={"bundle_version": ""}))
    with pytest.raises(PolicyNotInHash, match="bundle_version"):
        materialise(plan, reader)


def test_a_plan_pinned_to_the_wrong_policy_bundle_is_refused(reader):
    plan = covenant_plan(policy=covenant_plan().policy.model_copy(
        update={"bundle_version": "walnut-finance-synthetic@v99"}))
    with pytest.raises(PolicyNotInHash, match="v99"):
        materialise(plan, reader)


def test_the_correctly_pinned_bundle_version_compiles(reader):
    """The control: the reference plan's own pin (`POLICY_BUNDLE_VERSION`) is the estate's real
    bundle version, so materialisation is not refused by this check."""
    assert POLICY_BUNDLE_VERSION  # sanity: the constant is not accidentally blank
    built = materialise(covenant_plan(), reader)
    assert built.dataset.n_rows > 0


# =============================================================================================
# Finding 4 (Critical) — an empty/incoherent source pin compiled cleanly
# =============================================================================================
def test_a_source_snapshot_with_a_blank_content_hash_is_refused():
    plan = covenant_plan()
    broken = covenant_plan(temporal=plan.temporal.model_copy(update={
        "source_snapshots": (SNAPSHOT.model_copy(update={"content_hash": ""}),)}))
    with pytest.raises(DeterminismIncomplete, match="content_hash"):
        compile_plan(broken)


def test_exact_pin_strength_with_no_snapshots_is_refused():
    plan = covenant_plan()
    broken = covenant_plan(temporal=plan.temporal.model_copy(
        update={"source_snapshots": (), "pin_strength": "exact"}))
    with pytest.raises(DeterminismIncomplete, match="exact"):
        compile_plan(broken)


def test_observational_pin_strength_with_no_snapshots_still_compiles():
    """The counterpart: 'observational' is the honest, weaker claim, and an empty snapshot list is
    consistent with it — only 'exact' with nothing pinned is an incoherent claim."""
    plan = covenant_plan()
    ok = covenant_plan(temporal=plan.temporal.model_copy(
        update={"source_snapshots": (), "pin_strength": "observational"}))
    compile_plan(ok)


# =============================================================================================
# Finding 5 (Critical) — a label disguised as an axis column bypassed the leak check
# =============================================================================================
def test_a_label_disguised_as_an_axis_column_is_still_refused():
    """Declaring the label's own entity.field with relation='cohort_key' (an axis/identity
    relation, exempt from `feature_columns`) used to slip past the leak check entirely — and
    `_feature_cell` emits axis columns straight off the cohort row with no cut at all, so the
    label really would have reached the rectangle."""
    plan = covenant_plan()
    leaky = covenant_plan(shape=plan.shape.model_copy(update={
        "columns": plan.shape.columns + (
            Column(path="covenant_test.breached_leaked", type="bool",
                   source=ColumnSource(entity="covenant_test", field="breached",
                                       relation="cohort_key")),)}))
    with pytest.raises(LabelAxisRefused, match="label in the feature matrix"):
        compile_plan(leaky)


# =============================================================================================
# Finding 6 (Critical) — FALSE POSITIVE. See disposition doc.
# =============================================================================================
def test_a_withheld_cohort_row_feature_is_returned_withheld_not_dropped():
    """Pins the reasoning behind the false-positive disposition: a feature cell that is
    individually withheld is emitted as a `Withheld` cell WITHIN an otherwise-built row — it is
    never dropped. Row-level rejection (`RowCounts.rejected_by_policy`) only fires when the ROW's
    own decision instant or (for a supervised shape) its own label is undisclosed, both
    structurally required for this shape and both transparently counted, exactly like
    `rejected_by_maturity`."""
    from databridge_materialise.shapes.tabular import _feature_cell

    column = Column(path="covenant_test.observed_value", type="float64",
                    source=ColumnSource(entity="covenant_test", field="observed_value",
                                        relation="cohort_row", system_time_field="recorded_at"))
    system_ptr = SourcePointer(source_id="s", kind="table_cell", table="t", column="recorded_at",
                               row_id="r", trust=Trust.TRUSTED)
    withheld = Withheld(record=WithholdingRecord(field_path="t.observed_value", policy_id="p",
                                                 reason_code="POLICY_COLUMN_DENIED",
                                                 principal="u"))
    cohort_row = {
        "recorded_at": GroundedValue(value=datetime(2025, 12, 1, tzinfo=timezone.utc),
                                     pointer=system_ptr),
        "observed_value": withheld,
    }

    class _FakeRead:
        def value(self, row, col):
            cell = row.get(col)
            return cell.value if isinstance(cell, GroundedValue) else None

    decision_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cell = _feature_cell(covenant_plan(), column, _FakeRead(), cohort_row, {}, {},
                         decision_time, None, None)
    assert cell is withheld


# =============================================================================================
# Finding 7 (Critical) — FALSE POSITIVE. See disposition doc.
# =============================================================================================
def test_cohort_filter_is_never_read_by_the_field_planner():
    """`Cohort.filter` is a declared string field, included in `plan_hash` for identity purposes,
    but NO code path in this package interprets it as a predicate. `_needed_fields` — which
    determines both the O1b pre-screen's projection AND the actual read — is identical whether or
    not `filter` is set, and to whatever content."""
    from databridge_materialise.shapes.tabular import _needed_fields

    baseline = covenant_plan()
    attack = covenant_plan(selection=baseline.selection.model_copy(update={
        "cohort": baseline.selection.cohort.model_copy(update={
            "filter": "1=1' OR untrusted_notes LIKE '%anything%"})}))
    assert _needed_fields(baseline) == _needed_fields(attack)


def test_cohort_filter_never_changes_which_rows_are_built(reader):
    """The end-to-end proof: two plans that differ ONLY in `Cohort.filter`'s (attacker-shaped)
    content build the identical row set. They are different plan_hashes — filter is part of the
    hashed selection block — but the SAME dataset, because nothing executes the string."""
    baseline = materialise(covenant_plan(), reader)
    plan = covenant_plan()
    attack = covenant_plan(selection=plan.selection.model_copy(update={
        "cohort": plan.selection.cohort.model_copy(update={
            "filter": "party.legal_name = 'this-should-have-zero-effect'"})}))
    filtered = materialise(attack, reader)
    assert filtered.dataset.n_rows == baseline.dataset.n_rows
    assert filtered.plan.plan_hash != baseline.plan.plan_hash


# =============================================================================================
# Finding 8 (High) — air-gapped egress was a note, not a refusal
# =============================================================================================
def test_air_gapped_egress_with_no_pinned_snapshots_is_refused():
    plan = covenant_plan()
    broken = covenant_plan(
        policy=plan.policy.model_copy(update={"egress": "air_gapped"}),
        temporal=plan.temporal.model_copy(update={"source_snapshots": ()}))
    with pytest.raises(AirGapUnpinned, match="empty"):
        compile_plan(broken)


def test_air_gapped_egress_with_an_unpinned_t2_source_is_refused():
    plan = covenant_plan()
    unpinned = SNAPSHOT.model_copy(update={"tier": "T2", "capture_ref": None})
    broken = covenant_plan(
        policy=plan.policy.model_copy(update={"egress": "air_gapped"}),
        temporal=plan.temporal.model_copy(update={"source_snapshots": (unpinned,)}))
    with pytest.raises(AirGapUnpinned, match="capture_ref"):
        compile_plan(broken)


def test_air_gapped_egress_with_a_captured_snapshot_compiles():
    plan = covenant_plan()
    captured = SNAPSHOT.model_copy(update={"tier": "T2", "capture_ref": "capture://core_banking/1"})
    ok = covenant_plan(
        policy=plan.policy.model_copy(update={"egress": "air_gapped"}),
        temporal=plan.temporal.model_copy(update={"source_snapshots": (captured,)}))
    compile_plan(ok)


# =============================================================================================
# Finding 9 (High) — a chunk corpus's text_field bypassed the system-time-axis check
# =============================================================================================
def test_a_chunk_corpus_text_field_with_no_system_time_field_is_refused():
    """`text_field` is separate from `shape.columns` (a chunk corpus declares no columns of its
    own), so the loop that checks `system_time_field` over `plan.feature_columns` never reached
    it. Without the field, `known_at` treats every row as 'not yet known' and the corpus used to
    silently build EMPTY rather than the plan being refused."""
    plan = facility_notes_corpus()
    broken = facility_notes_corpus(shape=plan.shape.model_copy(update={
        "text_field": plan.shape.text_field.model_copy(update={"system_time_field": None})}))
    with pytest.raises(NoSystemTimeAxis, match="text_field"):
        compile_plan(broken)


# =============================================================================================
# Finding 10 (High) — row_order's cohort-key coverage was an intersection, not a subset
# =============================================================================================
def test_a_composite_key_only_partially_covered_by_row_order_is_refused():
    plan = covenant_plan()
    composite = covenant_plan(
        shape=plan.shape.model_copy(update={
            "columns": plan.shape.columns + (
                Column(path="covenant_test.covenant_id_key", type="string",
                       source=ColumnSource(entity="covenant_test", field="covenant_id",
                                           relation="cohort_key")),)}),
        determinism=plan.determinism.model_copy(update={
            "row_order": ("covenant_test.test_date", "covenant_test.test_id")}))
    with pytest.raises(DeterminismIncomplete, match="covenant_id_key"):
        compile_plan(composite)


def test_no_cohort_key_column_at_all_is_refused():
    plan = covenant_plan()
    columns = tuple(c for c in plan.shape.columns if c.source.relation != "cohort_key")
    no_key = covenant_plan(
        shape=plan.shape.model_copy(update={"columns": columns}),
        determinism=plan.determinism.model_copy(update={"row_order": ("covenant_test.test_date",)}))
    with pytest.raises(DeterminismIncomplete, match="cohort_key"):
        compile_plan(no_key)


# =============================================================================================
# Finding 11 (High) — the executor's own version was outside the dataset's identity
# =============================================================================================
def test_a_plan_with_no_materialiser_version_pinned_is_refused_at_compile_time():
    plan = covenant_plan()
    broken = covenant_plan(determinism=plan.determinism.model_copy(update={
        "executor": plan.determinism.executor.model_copy(update={"materialiser_version": ""})}))
    with pytest.raises(DeterminismIncomplete, match="materialiser_version"):
        compile_plan(broken)


def test_a_plan_pinned_to_the_wrong_materialiser_version_is_refused_at_materialise_time(reader):
    plan = covenant_plan()
    wrong = covenant_plan(determinism=plan.determinism.model_copy(update={
        "executor": plan.determinism.executor.model_copy(
            update={"materialiser_version": "walnut-mat 0.0.1-stale"})}))
    with pytest.raises(DeterminismIncomplete, match="0.0.1-stale"):
        materialise(wrong, reader)


# =============================================================================================
# Finding 12 (Medium) — an unimplemented output format silently wrote JSONL bytes
# =============================================================================================
def test_an_unimplemented_output_format_is_refused_not_silently_written_as_jsonl(reader, tmp_path):
    plan = covenant_plan(output=covenant_plan().output.model_copy(update={"format": "csv"}))
    with pytest.raises(RuntimeError, match="no writer"):
        materialise(plan, reader, out_dir=tmp_path)
    assert not any(tmp_path.iterdir()), (
        "a refused write must not leave a wrongly-named file on disk for the next reader to trust")


# =============================================================================================
# Finding 13 (Medium) — a truncated id could collide and silently overwrite another dataset
# =============================================================================================
def test_output_files_are_named_from_the_full_hash_not_the_truncated_id(reader, tmp_path):
    plan = covenant_plan()
    assert plan.output_stem == f"mp_{plan.plan_hash}"
    assert len(plan.plan_hash) == 64, "sha256 hex digest: the whole point is that this is not 12"

    materialise(plan, reader, out_dir=tmp_path)
    assert (tmp_path / f"{plan.output_stem}.jsonl").is_file()
    assert (tmp_path / f"{plan.output_stem}.croissant.json").is_file()
