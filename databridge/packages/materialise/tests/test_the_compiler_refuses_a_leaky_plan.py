"""One test per refusal. This file is the argument that delegation is safe.

AGENT_PLANE §8a: asked for "training data", a model produces a leaky split *with confidence*, and
every wrong answer here is the intuitive one. The compiler is not a formality; it is the reason a
model may be trusted to draft a plan at all. Each test below takes the reference plan and breaks
exactly one thing, so a passing suite says which rules are actually enforced rather than that
"the compiler ran".
"""
from __future__ import annotations

import pytest

from databridge_materialise import (
    AbstentionNotFeasible,
    Abstention,
    DeterminismIncomplete,
    FeatureAxisRefused,
    ImputationRefused,
    LabelAxisRefused,
    MaturityLagMissing,
    NoSystemTimeAxis,
    PointerRequirementDisabled,
    PolicyNotInHash,
    compile_plan,
    kotte_min_abstention,
)

from plans import covenant_plan


def test_the_reference_plan_compiles():
    """The control. Without it every refusal below could be passing for the wrong reason."""
    compiled = compile_plan(covenant_plan())
    assert compiled.plan_hash == covenant_plan().plan_hash
    assert len(compiled.feature_columns) == 3
    assert len(compiled.axis_columns) == 2


# --- invariant 1: features are cut on system time --------------------------------------
def test_features_cut_on_valid_time_are_refused():
    """The Feast default, refused. This is *the* leak: a value backfilled or corrected after the
    decision instant appears in the row as though the organisation had it."""
    plan = covenant_plan()
    leaky = covenant_plan(temporal=plan.temporal.model_copy(update={"feature_axis": "valid_time"}))
    with pytest.raises(FeatureAxisRefused) as err:
        compile_plan(leaky)
    assert err.value.invariant == "features_on_system_time"
    assert "backfilled" in str(err.value) or "retroactive corrections" in str(err.value)


def test_a_per_row_cut_with_no_decision_instant_is_refused():
    """A point-in-time join with no per-row instant is point-in-no-time: every feature would be
    read at build time, which is the leak in its purest form."""
    plan = covenant_plan()
    from databridge_materialise import Cohort, Selection

    broken = covenant_plan(selection=Selection(entity=plan.selection.entity, cohort=Cohort()))
    with pytest.raises(FeatureAxisRefused):
        compile_plan(broken)


def test_a_feature_over_a_source_with_no_system_time_is_refused():
    """The corollary that catches the *plausible* version of the mistake.

    `covenant` is a dimension table overwritten in place: it records when a covenant is true, never
    when the bank learned it. A model asked for "covenant threshold as a feature" will happily
    nominate `next_test_date` as the axis. An axis a source does not have cannot be cut on, and
    substituting the nearest available date is exactly how a confident leaky split is produced.
    """
    plan = covenant_plan()
    from databridge_materialise import Column, ColumnSource

    with_static = covenant_plan(shape=plan.shape.model_copy(update={
        "columns": plan.shape.columns + (
            Column(path="covenant.threshold_value", type="float64",
                   source=ColumnSource(entity="covenant", field="threshold_value",
                                       relation="as_of_prior", join_key="covenant_id",
                                       valid_time_field="next_test_date")),)}))
    with pytest.raises(NoSystemTimeAxis) as err:
        compile_plan(with_static)
    assert "system_time_field" in str(err.value)
    assert err.value.invariant == "features_on_system_time"


def test_the_label_field_cannot_also_be_a_cohort_row_feature():
    """The two blocks have separate types so they cannot be unified by config — but a plan can
    still NAME the same column in both, and that is the label in the feature matrix."""
    plan = covenant_plan()
    from databridge_materialise import Column, ColumnSource

    leaky = covenant_plan(shape=plan.shape.model_copy(update={
        "columns": plan.shape.columns + (
            Column(path="covenant_test.breached_now", type="bool",
                   source=ColumnSource(entity="covenant_test", field="breached",
                                       relation="cohort_row", system_time_field="recorded_at")),)}))
    with pytest.raises(LabelAxisRefused) as err:
        compile_plan(leaky)
    assert "label in the feature matrix" in str(err.value)


# --- invariant 2: labels on valid time, with a maturity lag ------------------------------
def test_labels_cut_on_system_time_are_refused():
    """Cutting labels on system time trains the model on how fast a clerk types."""
    plan = covenant_plan()
    broken = covenant_plan(temporal=plan.temporal.model_copy(update={"label_axis": "system_time"}))
    with pytest.raises(LabelAxisRefused) as err:
        compile_plan(broken)
    assert "RECORDING LATENCY" in str(err.value)


def test_a_label_read_at_the_decision_instant_is_refused():
    """A label read at T_d is the UNCORRECTED one: the organisation's first guess, not the
    outcome."""
    plan = covenant_plan()
    broken = covenant_plan(temporal=plan.temporal.model_copy(
        update={"label_read_at_system_time": "decision_time"}))
    with pytest.raises(LabelAxisRefused) as err:
        compile_plan(broken)
    assert "corrections are included" in str(err.value)


def test_a_missing_maturity_lag_is_refused():
    """Right-censoring. Without the lag, an outcome that occurred but has not been RECORDED
    silently becomes a negative example, always in the same direction, invisible in every metric."""
    plan = covenant_plan()
    broken = covenant_plan(temporal=plan.temporal.model_copy(
        update={"label_maturity_lag": None}))
    with pytest.raises(MaturityLagMissing) as err:
        compile_plan(broken)
    assert err.value.invariant == "label_maturity_lag_declared"
    assert "P0D" in str(err.value), "the refusal has to say what a legitimate zero looks like"


def test_a_declared_zero_lag_compiles():
    """The counterpart. 'Declared as zero' and 'not declared' are different facts and only the
    second is refused — a source that records outcomes synchronously is entitled to say so."""
    plan = covenant_plan()
    compile_plan(covenant_plan(temporal=plan.temporal.model_copy(
        update={"label_maturity_lag": "P0D"})))


def test_a_calendar_month_duration_is_refused():
    """`P1M` is not a fixed length of time, so the same plan would cut differently in February and
    in July while the hash said nothing had changed."""
    plan = covenant_plan()
    with pytest.raises(ValueError, match="calendar unit"):
        covenant_plan(temporal=plan.temporal.model_copy(update={"label_maturity_lag": "P1M"}))


def test_a_supervised_shape_with_no_label_is_refused():
    plan = covenant_plan()
    broken = covenant_plan(
        shape=plan.shape.model_copy(update={"label": None}),
        temporal=plan.temporal.model_copy(update={"label_axis": "none"}))
    with pytest.raises(LabelAxisRefused, match="supervised shape"):
        compile_plan(broken)


# --- invariant 3: policy is inside the plan hash ----------------------------------------
def test_a_blank_lawful_basis_is_refused():
    plan = covenant_plan()
    broken = covenant_plan(policy=plan.policy.model_copy(update={"lawful_basis": "  "}))
    with pytest.raises(PolicyNotInHash, match="lawful_basis"):
        compile_plan(broken)


def test_unpinned_packs_are_refused():
    """A pack bump changes the closed vocabulary the columns are NAMED in. A plan that does not
    pin them cannot claim its hash identifies a dataset."""
    with pytest.raises(PolicyNotInHash, match="pack"):
        compile_plan(covenant_plan(packs=()))


def test_perturbing_a_grounded_column_is_refused():
    """A DP-noised value is not the value that was in the source: it cannot carry a pointer that
    cites the source, and it will correctly fail the grounding wall's re-slice check."""
    plan = covenant_plan()
    broken = covenant_plan(policy=plan.policy.model_copy(update={
        "perturbation": plan.policy.perturbation.model_copy(
            update={"grounded_columns": "allowed"})}))
    with pytest.raises(PolicyNotInHash, match="cannot carry a pointer"):
        compile_plan(broken)


def test_generalising_a_quasi_identifier_is_refused():
    """Generalising '1983-04-12' to '1980s' produces a value that is not in the source. Suppression
    is allowed for exactly the opposite reason: it removes a value rather than inventing one."""
    plan = covenant_plan()
    broken = covenant_plan(policy=plan.policy.model_copy(update={
        "pii": plan.policy.pii.model_copy(update={
            "quasi_identifiers": plan.policy.pii.quasi_identifiers.model_copy(
                update={"action": "generalise"})})}))
    with pytest.raises(PolicyNotInHash, match="not in the source"):
        compile_plan(broken)


# --- invariant 4: every cell carries a pointer ------------------------------------------
def test_disabling_the_pointer_requirement_is_refused():
    """`require_pointer_for_every_cell: false` is not a value. A model asked for 'clean data' will
    offer to drop the awkward cells, and this field is where that offer arrives."""
    plan = covenant_plan()
    broken = covenant_plan(provenance=plan.provenance.model_copy(
        update={"require_pointer_for_every_cell": False}))
    with pytest.raises(PointerRequirementDisabled) as err:
        compile_plan(broken)
    assert "There is no such value" in str(err.value)


# --- invariant 5: the abstention budget is feasibility-tested ----------------------------
def test_an_untested_abstention_budget_is_refused():
    """A model will accept any alpha it is given, and the number then travels into a receipt and a
    data card as though it had been established."""
    plan = covenant_plan()
    broken = covenant_plan(policy=plan.policy.model_copy(update={
        "abstention": Abstention(alpha=0.08, feasibility_tested=False)}))
    with pytest.raises(AbstentionNotFeasible, match="feasibility_tested is false"):
        compile_plan(broken)


def test_a_tested_budget_with_no_measured_base_risk_is_refused():
    """'Tested' with nothing measured records that somebody ticked a box."""
    plan = covenant_plan()
    broken = covenant_plan(policy=plan.policy.model_copy(update={
        "abstention": Abstention(alpha=0.08, feasibility_tested=True)}))
    with pytest.raises(AbstentionNotFeasible, match="MEASURED base risk"):
        compile_plan(broken)


def test_an_uncertifiable_budget_is_refused_with_the_kotte_floor():
    """Kotte: base risk 0.36 against target 0.08 forces abstention on (0.36-0.08)/(1-0.08) = 30.4%
    of examples. A plan declaring a 10% ceiling is asking for something no model can deliver, and
    the refusal says so with the number rather than with 'infeasible'."""
    plan = covenant_plan()
    broken = covenant_plan(policy=plan.policy.model_copy(update={
        "abstention": Abstention(alpha=0.08, base_risk=0.36, feasibility_tested=True,
                                 max_abstention_rate=0.10,
                                 golden_set="packs/finance/evals/covenant_v1.jsonl")}))
    with pytest.raises(AbstentionNotFeasible) as err:
        compile_plan(broken)
    assert "UNCERTIFIABLE" in str(err.value)
    assert f"{kotte_min_abstention(0.36, 0.08):.1%}" in str(err.value)


def test_a_feasible_budget_compiles_and_reports_its_coverage_cost():
    """Feasible is not free: the compiler reports the floor so nobody discovers after deployment
    that the certified budget abstains on a third of the queue."""
    plan = covenant_plan()
    ok = covenant_plan(policy=plan.policy.model_copy(update={
        "abstention": Abstention(alpha=0.40, base_risk=0.36, feasibility_tested=True,
                                 max_abstention_rate=0.10)}))
    compiled = compile_plan(ok)
    assert compiled.feasibility is not None and compiled.feasibility.feasible
    assert compiled.feasibility.min_abstention_rate == 0.0
    assert any("abstention" in note for note in compiled.notes)


# --- the three that cost nothing to check ------------------------------------------------
def test_imputation_is_refused_by_name():
    """XGBoost, LightGBM and CatBoost each learn a default branch direction for missing values, so
    imputing destroys information they would otherwise use — and erases the difference between
    three kinds of missing."""
    plan = covenant_plan()
    columns = list(plan.shape.columns)
    columns[2] = columns[2].model_copy(update={"null_policy": "impute_mean"})
    broken = covenant_plan(shape=plan.shape.model_copy(update={"columns": tuple(columns)}))
    with pytest.raises(ImputationRefused) as err:
        compile_plan(broken)
    assert "impute_mean" in str(err.value)
    assert "never be summed" in str(err.value)


def test_an_unpinned_build_time_is_refused():
    """'Whenever this ran' is not re-executable, and the plan hash would not record which instant
    produced the bytes."""
    plan = covenant_plan()
    broken = covenant_plan(temporal=plan.temporal.model_copy(update={"build_time": None}))
    with pytest.raises(DeterminismIncomplete, match="unpinned"):
        compile_plan(broken)


def test_a_row_order_with_no_unique_key_is_refused():
    """Two rows that compare equal then sort by whatever the sort happened to do, and the content
    hash becomes a property of the sort rather than of the data."""
    plan = covenant_plan()
    broken = covenant_plan(determinism=plan.determinism.model_copy(
        update={"row_order": ("covenant_test.test_date",)}))
    with pytest.raises(DeterminismIncomplete, match="cohort key"):
        compile_plan(broken)


def test_a_row_order_over_columns_that_do_not_exist_is_refused():
    plan = covenant_plan()
    broken = covenant_plan(determinism=plan.determinism.model_copy(
        update={"row_order": ("covenant_test.test_id", "covenant_test.nonexistent")}))
    with pytest.raises(DeterminismIncomplete, match="nonexistent"):
        compile_plan(broken)


def test_a_missing_seed_or_executor_is_refused():
    """A digest is executor-relative, so a receipt that does not name the executor cannot be
    checked against a re-run."""
    plan = covenant_plan()
    with pytest.raises(DeterminismIncomplete, match="seed"):
        compile_plan(covenant_plan(determinism=plan.determinism.model_copy(update={"seed": None})))
    with pytest.raises(DeterminismIncomplete, match="executor"):
        compile_plan(covenant_plan(determinism=plan.determinism.model_copy(
            update={"executor": None})))
