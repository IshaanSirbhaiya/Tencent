"""query.py — as_of snapshots, neighbors, path, timeline, grounded nl_query, over the clinic seed.

`link_entities` and the `check_claims` anti-fabrication gate are unchanged by ADR-0009 — they are
vocabulary-independent and are what make an answer trustworthy. What changed is the intent
templates, so the tests below exercise them against real clinic questions.
"""
from __future__ import annotations

import datetime as dt

from databridge_graph import (
    EdgeKind,
    GraphStore,
    NodeKind,
    as_of,
    neighbors,
    nl_query,
    node_id,
    path,
    timeline,
)
from databridge_graph.clinic_seed import (
    SEED_SYSTEM_TIME,
    load_clinic_seed,
    org_node_id,
    package_node_id,
    catalogue_test_node_id,
)

_UTC = dt.timezone.utc

MERIDIAN = org_node_id("Meridian Life Assurance Pte Ltd")
WELL2 = package_node_id("WELL2", "BLPHS")


def _seeded():
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_clinic_seed(s)
    return s


# --- bi-temporal as_of ------------------------------------------------------------
def test_as_of_system_time_before_learning_is_empty():
    # audit/replay: "what did the graph believe before we ingested it?" → nothing
    s = _seeded()
    before = as_of(s, dt.datetime(2026, 5, 1, tzinfo=_UTC),
                   dt.datetime(2026, 5, 1, tzinfo=_UTC))   # system_time < learn (2026-08-11)
    assert before.nodes == [] and before.edges == []


def test_as_of_valid_time_before_the_rules_took_effect_is_empty():
    s = _seeded()
    early = as_of(s, dt.datetime(2025, 12, 31, tzinfo=_UTC), SEED_SYSTEM_TIME)
    assert early.nodes == []


def test_as_of_at_the_seed_instant_sees_the_whole_graph():
    s = _seeded()
    now = as_of(s, SEED_SYSTEM_TIME, SEED_SYSTEM_TIME)
    assert len(now.nodes) == s.counts()["nodes"]
    assert any(n.kind == NodeKind.PATIENT for n in now.nodes)
    assert any(n.kind == NodeKind.COVERAGE_RULE for n in now.nodes)


# --- neighbors / path -------------------------------------------------------------
def test_neighbors_one_hop():
    s = _seeded()
    sub = neighbors(s, MERIDIAN, depth=1)
    assert any(n.kind == NodeKind.DOCUMENT for n in sub.nodes)      # ISSUED
    assert all(e.src_id == MERIDIAN or e.dst_id == MERIDIAN for e in sub.edges)


def test_neighbors_edge_kind_filter():
    s = _seeded()
    sub = neighbors(s, WELL2, edge_kind=EdgeKind.INCLUDES_TEST, depth=1)
    assert sub.edges
    assert all(e.kind == EdgeKind.INCLUDES_TEST for e in sub.edges)


def test_path_carries_provenance():
    """An insurer reaches a test it paid for through the document that ordered it — and every
    step of that path is a grounded edge, so the path IS the provenance."""
    s = _seeded()
    gp = path(s, MERIDIAN, catalogue_test_node_id("chest_xray"))
    assert gp.found and gp.steps
    assert gp.pointers()


def test_path_absent_returns_not_found():
    s = _seeded()
    gp = path(s, MERIDIAN, "patient:nobody")
    assert gp.found is False and gp.steps == []


# --- timeline ---------------------------------------------------------------------
def test_timeline_has_node_and_edges():
    s = _seeded()
    evs = timeline(s, MERIDIAN)
    kinds = {e.kind for e in evs}
    assert "node" in kinds and "edge" in kinds


# --- grounded nl_query ------------------------------------------------------------
def test_nl_query_package_lists_its_tests_with_citations():
    s = _seeded()
    a = nl_query(s, "What does the WELL2 package include?")
    assert a.grounded, a.refusal
    assert "WELL2" in a.answer
    assert "includes" in a.answer
    assert a.pointers()
    assert len(a.citations) > 1          # the package AND each test it cites


def test_nl_query_package_answer_carries_its_age_band():
    """The band is a quantity, so it only survives `check_claims` because it is an attribute of a
    cited node — which is exactly the guarantee under test."""
    s = _seeded()
    a = nl_query(s, "What does the WELL2 package include?")
    assert a.grounded and "40-59" in a.answer


def test_nl_query_org_resolves_and_names_its_documents():
    s = _seeded()
    a = nl_query(s, "Who is Meridian Life Assurance?")
    assert a.grounded, a.refusal
    assert a.answer.startswith("Meridian Life Assurance")
    assert "insurer" in a.answer


def test_nl_query_test_resolves_to_the_packages_containing_it():
    s = _seeded()
    a = nl_query(s, "Which package includes the Chest X-Ray?")
    assert a.grounded, a.refusal
    assert "Chest X-Ray" in a.answer


def test_a_patient_answer_never_claims_identity_is_verified():
    """The load-bearing one. A document names a patient; the graph must present that as a CLAIM
    and say so in the answer text, because the answer is what staff read
    (CLAUDE.md §Hard constraints 1)."""
    s = _seeded()
    a = nl_query(s, "Who is Loh Wei Ming?")
    assert a.grounded, a.refusal
    assert "not a verified identity" in a.answer
    assert "in person" in a.answer
    for forbidden in ("verified identity is", "identity confirmed", "is confirmed to be"):
        assert forbidden not in a.answer.lower()


def test_nl_query_no_match_refuses():
    s = _seeded()
    a = nl_query(s, "What is the airspeed velocity of an unladen swallow?")
    assert a.grounded is False and a.refusal


def test_every_clinic_answer_is_claim_checked_clean():
    # the anti-fabrication guarantee: every grounded answer's quantities trace to a cited node
    s = _seeded()
    for q in ["What does the WELL2 package include?",
              "What does the PEE226 package include?",
              "Who is Meridian Life Assurance?",
              "Which package includes the Chest X-Ray?"]:
        a = nl_query(s, q)
        assert a.grounded, f"{q!r} should be grounded, got refusal: {a.refusal}"
        assert a.pointers(), f"{q!r} produced no citations"


def test_documents_are_provenance_never_the_answer_entity():
    """A DOCUMENT node is what an answer CITES, not what it is about — otherwise asking about a
    patient returns "chit_01_meridian_referral.txt." and calls it an answer."""
    s = _seeded()
    a = nl_query(s, "chit_01_meridian_referral.txt")
    assert not a.answer.startswith("chit_01")
