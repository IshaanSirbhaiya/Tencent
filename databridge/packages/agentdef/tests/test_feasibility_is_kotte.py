"""The abstention feasibility test is arithmetic, and the arithmetic is Kotte's.

`arXiv:2606.29054`: for base risk mu above target alpha, any distribution-free selective predictor
must abstain on at least ``(mu - alpha) / (1 - alpha)`` of examples; and alpha = 0.10 was found
**uncertifiable** for NER-class tasks across six models and eight datasets.

These tests pin the bound itself, so a later "simplification" of the formula fails here rather than
silently admitting a budget nobody can hold.
"""
from __future__ import annotations

import pytest

from databridge_agentdef import (
    KNOWN_TASK_CLASSES,
    KOTTE_CITATION,
    UNCERTIFIABLE_ALPHA,
    assess,
    minimum_abstention,
)


@pytest.mark.parametrize("mu, alpha, expected", [
    (0.05, 0.10, 0.0),                        # already under target: no forced abstention
    (0.10, 0.10, 0.0),                        # exactly at target: still none
    (0.11, 0.08, (0.11 - 0.08) / 0.92),       # the finance agent's numbers
    (0.19, 0.15, (0.19 - 0.15) / 0.85),       # the government agent's numbers
    (0.60, 0.08, (0.60 - 0.08) / 0.92),
    (1.00, 0.00, 1.0),                        # always wrong, zero tolerance: abstain always
])
def test_the_bound_is_mu_minus_alpha_over_one_minus_alpha(mu, alpha, expected):
    assert minimum_abstention(mu, alpha) == pytest.approx(expected)


@pytest.mark.parametrize("mu, alpha", [(-0.01, 0.1), (1.01, 0.1), (0.1, 1.0), (0.1, -0.1)])
def test_the_bound_refuses_values_that_are_not_probabilities(mu, alpha):
    with pytest.raises(ValueError):
        minimum_abstention(mu, alpha)


def test_alpha_of_one_is_refused_by_name():
    """alpha = 1 is 'be wrong always', which is not a risk target - and the bound divides by
    (1 - alpha)."""
    with pytest.raises(ValueError, match=r"be wrong always"):
        minimum_abstention(0.5, 1.0)


def test_ten_percent_is_uncertifiable_for_span_shaped_tasks():
    assert UNCERTIFIABLE_ALPHA["ner"] == 0.10
    assert UNCERTIFIABLE_ALPHA["span_extraction"] == 0.10
    for task_class in ("ner", "span_extraction"):
        report = assess(task_class=task_class, target_alpha=0.10, base_risk=0.11,
                        max_abstention_rate=0.9)
        assert not report.certifiable and not report.feasible
        assert KOTTE_CITATION in report.reasoning
        assert "six models and eight datasets" in report.reasoning
        # And it is not a rounding argument: a tighter target is refused too.
        assert not assess(task_class=task_class, target_alpha=0.02, base_risk=0.11,
                          max_abstention_rate=0.9).certifiable


def test_just_above_the_uncertifiable_level_is_certifiable():
    report = assess(task_class="span_extraction", target_alpha=0.15, base_risk=0.19,
                    max_abstention_rate=0.30)
    assert report.certifiable and report.feasible


def test_a_task_class_with_no_finding_is_not_gated_by_the_table():
    """The table records a measured finding, not a general prior. A class Kotte did not test is
    not refused on its behalf - it is just not excused from the arithmetic either."""
    assert "document_qa" not in UNCERTIFIABLE_ALPHA
    report = assess(task_class="document_qa", target_alpha=0.02, base_risk=0.01,
                    max_abstention_rate=0.0)
    assert report.certifiable and report.feasible and report.minimum_abstention == 0.0


def test_an_unknown_task_class_is_a_loud_failure_not_a_skip():
    """A safety table that can be skipped by misspelling a key is not a safety table."""
    assert "nre" not in KNOWN_TASK_CLASSES
    with pytest.raises(ValueError, match="not one this build knows"):
        assess(task_class="nre", target_alpha=0.10, base_risk=0.11, max_abstention_rate=0.9)


def test_infeasible_means_the_operator_cannot_carry_the_forced_abstention():
    report = assess(task_class="document_qa", target_alpha=0.08, base_risk=0.60,
                    max_abstention_rate=0.25, measured_on="2026-08-14")
    assert report.certifiable and not report.feasible
    assert "0.5652" in report.reasoning
    assert "Raise max_abstention_rate to at least" in report.reasoning


def test_certifiable_and_feasible_fail_for_different_reasons_and_are_separate_fields():
    uncertifiable = assess(task_class="ner", target_alpha=0.05, base_risk=0.05,
                           max_abstention_rate=1.0)
    infeasible = assess(task_class="document_qa", target_alpha=0.05, base_risk=0.50,
                        max_abstention_rate=0.10)
    assert (uncertifiable.certifiable, uncertifiable.feasible) == (False, False)
    assert (infeasible.certifiable, infeasible.feasible) == (True, False)
    assert "NOT CERTIFIABLE" in uncertifiable.reasoning
    assert "NOT FEASIBLE" in infeasible.reasoning


def test_the_report_records_what_it_was_measured_on(finance_declaration):
    """A base risk with no provenance is a number, and a provenance gap in a risk budget is
    exactly the shape of the problem the rest of this system exists to close."""
    abst = finance_declaration.abstention
    report = assess(task_class=abst.task_class, target_alpha=abst.target_alpha,
                    base_risk=abst.measured_base_risk,
                    max_abstention_rate=abst.max_abstention_rate,
                    measured_on=abst.measured_on, golden_set_digest=abst.golden_set_digest)
    record = report.as_record()
    assert record["citation"] == KOTTE_CITATION
    assert record["measured_on"] == "2026-08-14"
    assert record["golden_set_digest"] == abst.golden_set_digest
