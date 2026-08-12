"""EV-INTAKE — clinic graph eval gates (ADR-0009)."""
from __future__ import annotations

from databridge_evals.e_intake import gold_harness, run_ev_intake


def test_ev_intake_all_gates_pass():
    res = run_ev_intake(gold_harness())
    gates = res.gates()
    assert gates["extraction_f1>=0.80"], f"F1={res.entity_f1}"
    assert gates["merge_correctness_all"], res.merge_cases
    assert gates["provenance_completeness_100"], res.provenance_ratio
    assert gates["anti_fabrication_canary"]
    assert gates["nl_factuality_0_fabrications"]
    assert res.passed()


def test_extraction_f1_is_perfect_on_gold():
    res = run_ev_intake(gold_harness())
    assert res.entity_f1 == 1.0 and res.relation_f1 == 1.0   # plumbing proof (like E1)


def test_all_five_merge_cases_resolve_correctly():
    res = run_ev_intake(gold_harness())
    assert len(res.merge_cases) == 5
    assert all(c["pass"] for c in res.merge_cases)


def test_fabrication_canary_refused():
    res = run_ev_intake(gold_harness())
    assert res.fabrication_canary_refused is True


def test_nl_factuality_zero_fabrications():
    res = run_ev_intake(gold_harness())
    assert res.nl_fabrications == 0 and res.nl_all_grounded


def test_the_ingest_fixture_asserts_no_patient_facts():
    """`FIXTURE_TEXT` is the DEFAULT registered ingest source for the gateway and the MCP server,
    so whatever it extracts is written into the live graph by `brain_ingest("demo_sample.txt")`.

    It must therefore obey the same rule as the seed: no edge that states a fact about a specific
    patient, because no human has attested to anyone's identity (ADR-0009). A demo fixture is
    exactly the sort of back door this constraint gets lost through."""
    from databridge_evals.e_intake import (FIXTURE_ENTITIES, FIXTURE_RELATIONS,
                                           PATIENT_FACT_EDGE_KINDS)

    offenders = [r["rel_type"] for r in FIXTURE_RELATIONS
                 if r["rel_type"] in PATIENT_FACT_EDGE_KINDS]
    assert offenders == [], f"the ingest fixture emits patient-fact edge(s): {offenders}"
    assert not [e for e in FIXTURE_ENTITIES if e["kind"] == "patient"], (
        "the ingest fixture extracts a PATIENT; patient nodes must enter via the registry seed, "
        "where they are keyed on patient_id and linked only by CLAIM edges")


def test_ingesting_the_fixture_writes_no_patient_fact_edge():
    """The property above, asserted end-to-end through the real ingest path rather than by
    inspecting the fixture table."""
    from brain_server import BrainTools
    from databridge_evals.e_intake import (FIXTURE_TEXT, PATIENT_FACT_EDGE_KINDS,
                                           gold_harness as gh)

    tools = BrainTools(harness=gh())
    tools.load_seed()
    tools.brain_ingest("demo.txt", text=FIXTURE_TEXT)
    from databridge_graph import row_to_edge

    kinds = {row_to_edge(tools.store, r).kind.value for r in tools.store.open_edges()}
    assert not (kinds & PATIENT_FACT_EDGE_KINDS), sorted(kinds & PATIENT_FACT_EDGE_KINDS)
