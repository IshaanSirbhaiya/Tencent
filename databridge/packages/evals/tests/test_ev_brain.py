"""EV-BRAIN — Company-Brain eval gates (T08 Part 4, 04_PRD §10)."""
from __future__ import annotations

from databridge_evals.e_brain import gold_harness, run_ev_brain


def test_ev_brain_all_gates_pass():
    res = run_ev_brain(gold_harness())
    gates = res.gates()
    assert gates["extraction_f1>=0.80"], f"F1={res.entity_f1}"
    assert gates["merge_correctness_all"], res.merge_cases
    assert gates["provenance_completeness_100"], res.provenance_ratio
    assert gates["anti_fabrication_canary"]
    assert gates["nl_factuality_0_fabrications"]
    assert res.passed()


def test_extraction_f1_is_perfect_on_gold():
    res = run_ev_brain(gold_harness())
    assert res.entity_f1 == 1.0 and res.relation_f1 == 1.0   # plumbing proof (like E1)


def test_all_five_merge_cases_resolve_correctly():
    res = run_ev_brain(gold_harness())
    assert len(res.merge_cases) == 5
    assert all(c["pass"] for c in res.merge_cases)


def test_fabrication_canary_refused():
    res = run_ev_brain(gold_harness())
    assert res.fabrication_canary_refused is True


def test_nl_factuality_zero_fabrications():
    res = run_ev_brain(gold_harness())
    assert res.nl_fabrications == 0 and res.nl_all_grounded
