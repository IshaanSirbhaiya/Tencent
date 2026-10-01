"""The missingness leak check, the abstention feasibility test, and the duration subset.

Three small guards that each close a way a training set can be quietly wrong.

**The leak check** is the one nobody else ships, and it falls straight out of keeping three missing
reasons apart: if the `policy_suppressed` rate differs across label classes, the mask itself
carries the signal suppression was meant to remove. A model reads the label off which cells are
missing, the offline metric looks excellent, and the thing is worthless online.
"""
from __future__ import annotations

import pytest

from databridge_core import (
    Absent,
    GroundedValue,
    SourcePointer,
    Trust,
    Withheld,
    WithholdingRecord,
)
from databridge_materialise import (
    Dataset,
    DurationError,
    MissingnessLeaks,
    assess,
    kotte_min_abstention,
    assert_no_missingness_leak,
    materialise,
    missingness_leak_check,
    parse_duration,
)

from plans import covenant_plan


def _grounded(value):
    return GroundedValue(value=value, pointer=SourcePointer(
        source_id="s", kind="table_cell", table="t", column="c", row_id="r",
        trust=Trust.TRUSTED))


def _withheld(column):
    return Withheld(record=WithholdingRecord(field_path=f"t.{column}", policy_id="p",
                                             reason_code="POLICY_COLUMN_DENIED", principal="u"))


def _dataset(pattern):
    """`pattern` is a list of `(feature_is_withheld, label)` pairs."""
    rows = tuple((_withheld("f") if withheld else _grounded(1.0), _grounded(label))
                 for withheld, label in pattern)
    return Dataset(columns=("t.f", "t.label"), rows=rows)


# --- the leak check ----------------------------------------------------------------------
def test_suppression_uncorrelated_with_the_label_passes():
    dataset = _dataset([(True, True), (False, True), (True, False), (False, False)])
    result = missingness_leak_check(dataset, label_column="t.label", max_class_delta=0.02)
    assert result.passed and result.max_class_delta_observed == 0.0


def test_suppression_that_predicts_the_label_is_caught():
    """Every positive row has the feature withheld and no negative row does. The mask alone is a
    perfect classifier, and no amount of column-level policy makes that safe."""
    dataset = _dataset([(True, True), (True, True), (False, False), (False, False)])
    result = missingness_leak_check(dataset, label_column="t.label", max_class_delta=0.02)
    assert not result.passed
    assert result.max_class_delta_observed == 1.0
    assert result.worst_column == "t.f"
    assert "permutation test" in result.detail, (
        "the check is a first cut and should say so rather than implying statistical rigour it "
        "does not have")


def test_the_gate_refuses_rather_than_warns():
    """`assert_no_missingness_leak` is the branch `materialise` runs, called directly.

    Directly, because on this estate policy suppresses nothing for the materialising principal, so
    a test that only ever went through `materialise` could never reach the refusal — and an
    untested refusal is a refusal nobody has seen fire. The function under test is the same one
    the build calls, not a re-implementation of it.
    """
    leaking = _dataset([(True, True), (True, True), (False, False), (False, False)])
    with pytest.raises(MissingnessLeaks, match="Suppression correlates with the label"):
        assert_no_missingness_leak(leaking, label_column="t.label", max_class_delta=0.02)

    clean = _dataset([(True, True), (False, True), (True, False), (False, False)])
    assert assert_no_missingness_leak(clean, label_column="t.label",
                                      max_class_delta=0.02).passed


def test_the_real_build_carries_the_check_in_its_receipt(reader):
    """And the estate's own answer: this principal is denied nothing in this projection, so the
    suppression rate is zero in both classes and the check passes on the merits."""
    built = materialise(covenant_plan(), reader)
    assert built.receipt.missingness_leak_check.passed
    assert built.receipt.cells.policy_suppressed == 0
    assert built.receipt.missingness_leak_check.threshold == 0.02


def test_a_single_label_class_has_nothing_to_compare():
    dataset = _dataset([(True, True), (False, True)])
    result = missingness_leak_check(dataset, label_column="t.label", max_class_delta=0.02)
    assert result.passed and "one label class" in result.detail


def test_an_absent_mask_is_its_own_channel_and_is_gated_unless_the_join_defines_it():
    """`not_in_source` and `policy_suppressed` are different facts, and they are never SUMMED:
    each is its own mask. R-021 round 2 (Finding 5) changed what this test once asserted — that
    an Absent mask is ignored. On this platform an Absent can be a read artefact (a version
    superseded between T_d and the read instant, an attribute stored without its own pointer), so
    a label-correlated Absent mask is gated too. The one exception is a column whose relation
    DEFINES Absent as "no earlier row by T_d" (`as_of_prior`, `aggregate_count`): serving
    computes the same join and sees the same Absent, so that correlation is signal — reported,
    not gated."""
    rows = ((Absent(field_path="t.f"), _grounded(True)),
            (Absent(field_path="t.f"), _grounded(False)),
            (_grounded(1.0), _grounded(True)))
    ds = Dataset(columns=("t.f", "t.label"), rows=rows)
    gated = missingness_leak_check(ds, label_column="t.label", max_class_delta=0.0)
    assert not gated.passed and gated.worst_column == "t.f" and "absent mask" in gated.detail
    exempt = missingness_leak_check(ds, label_column="t.label", max_class_delta=0.0,
                                    absent_exempt=("t.f",))
    assert exempt.passed and exempt.max_class_delta_observed == 0.0
    assert "Not gated" in exempt.detail and "t.f" in exempt.detail


def test_an_unresolved_label_is_refused_not_pooled_into_a_none_class():
    """R-021 round 2, Finding 5: rows whose label is Withheld/Absent are not a class."""
    rows = ((_grounded(1.0), _grounded(True)), (_grounded(1.0), _grounded(False)),
            (_grounded(1.0), Absent(field_path="t.label")))
    result = missingness_leak_check(Dataset(columns=("t.f", "t.label"), rows=rows),
                                    label_column="t.label", max_class_delta=0.5)
    assert not result.passed and "1 row(s) have no grounded label" in result.detail


# --- the Kotte feasibility test ----------------------------------------------------------
def test_the_kotte_floor_is_zero_when_the_base_risk_already_meets_the_target():
    assert kotte_min_abstention(0.05, 0.10) == 0.0


def test_the_kotte_floor_is_the_published_formula():
    assert kotte_min_abstention(0.30, 0.10) == pytest.approx((0.30 - 0.10) / (1 - 0.10))


def test_a_budget_below_the_floor_is_uncertifiable_and_says_what_to_change():
    result = assess(base_risk=0.36, alpha=0.08, max_abstention_rate=0.10)
    assert not result.feasible
    assert "higher alpha or a higher ceiling, not a better model" in result.reason


def test_a_budget_with_no_ceiling_is_feasible_and_reports_its_cost():
    """Feasible is not free. Abstaining on 40% of a queue is a feasible budget and an unusable
    product, and those are different conversations."""
    result = assess(base_risk=0.50, alpha=0.10)
    assert result.feasible
    assert "at least 44.4%" in result.reason


# --- the duration subset ------------------------------------------------------------------
def test_calendar_units_are_refused_because_they_are_not_fixed_lengths():
    for text in ("P1M", "P1Y", "P2M"):
        with pytest.raises(DurationError, match="calendar unit"):
            parse_duration(text)


def test_an_empty_duration_is_not_a_zero_duration():
    with pytest.raises(DurationError, match="P0D"):
        parse_duration("")


def test_the_supported_subset_parses():
    assert parse_duration("P14D").days == 14
    assert parse_duration("P0D").total_seconds() == 0
    assert parse_duration("PT12H").total_seconds() == 12 * 3600
    assert parse_duration("P2W").days == 14
