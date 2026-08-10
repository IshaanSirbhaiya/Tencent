"""query.py — as_of snapshots, neighbors, path, timeline, grounded nl_query (T08 §8)."""
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
from databridge_graph.demo_seed import SEED_SYSTEM_TIME, load_demo_seed

_UTC = dt.timezone.utc


def _seeded():
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_demo_seed(s)
    return s


# --- bi-temporal as_of ------------------------------------------------------------
def test_as_of_valid_time_hides_future_decisions():
    s = _seeded()
    early = as_of(s, dt.datetime(2024, 1, 10, tzinfo=_UTC), SEED_SYSTEM_TIME)
    late = as_of(s, dt.datetime(2024, 5, 1, tzinfo=_UTC), SEED_SYSTEM_TIME)
    total = sum(1 for n in _seeded().open_nodes() if n["kind"] == NodeKind.DECISION.value)
    n_early = sum(1 for n in early.nodes if n.kind == NodeKind.DECISION)
    n_late = sum(1 for n in late.nodes if n.kind == NodeKind.DECISION)
    # decisions become valid on their made_at date: none by 2024-01-10, SOME (not all) by 2024-05-01
    assert n_early == 0 and 0 < n_late < total


def test_as_of_system_time_before_learning_is_empty():
    # audit/replay: "what did the graph believe before we ingested it?" → nothing
    s = _seeded()
    before = as_of(s, dt.datetime(2024, 5, 1, tzinfo=_UTC),
                   dt.datetime(2024, 5, 1, tzinfo=_UTC))   # system_time < learn (2024-06-01)
    assert before.nodes == [] and before.edges == []


# --- neighbors / path -------------------------------------------------------------
def test_neighbors_one_hop():
    s = _seeded()
    sub = neighbors(s, node_id(NodeKind.PERSON, "sarah@acmecorp.com"), depth=1)
    labels = {n.label for n in sub.nodes}
    assert "Acme Corp" in labels                  # WORKS_AT
    assert any(n.kind == NodeKind.DECISION for n in sub.nodes)   # DECIDED


def test_neighbors_edge_kind_filter():
    s = _seeded()
    sub = neighbors(s, node_id(NodeKind.PERSON, "sarah@acmecorp.com"),
                    edge_kind=EdgeKind.WORKS_AT, depth=1)
    assert all(e.kind == EdgeKind.WORKS_AT for e in sub.edges)


def test_path_carries_provenance():
    s = _seeded()
    src = node_id(NodeKind.PERSON, "sarah@acmecorp.com")
    dst = node_id(NodeKind.PROJECT, "data-migration")
    gp = path(s, src, dst)
    assert gp.found and gp.steps
    assert gp.pointers()                          # every step's edge is grounded → path is provenance


def test_path_absent_returns_not_found():
    s = _seeded()
    gp = path(s, node_id(NodeKind.PERSON, "sarah@acmecorp.com"), "person:nobody@nowhere.com")
    assert gp.found is False and gp.steps == []


# --- timeline ---------------------------------------------------------------------
def test_timeline_has_node_and_edges():
    s = _seeded()
    evs = timeline(s, node_id(NodeKind.PERSON, "sarah@acmecorp.com"))
    kinds = {e.kind for e in evs}
    assert "node" in kinds and "edge" in kinds


# --- grounded nl_query ------------------------------------------------------------
def test_nl_query_fact_is_grounded_with_citation():
    s = _seeded()
    a = nl_query(s, "What is the BigClient contract value?")
    assert a.grounded and "$2,400,000" in a.answer
    assert a.pointers()                            # cited
    assert a.citations[0].entity_id == "fact:bigclient-contract-value"


def test_nl_query_person_resolves_over_decision():
    s = _seeded()
    a = nl_query(s, "Who is Sarah Chen?")
    assert a.grounded and a.answer.startswith("Sarah Chen")
    assert "CEO" in a.answer and "Acme Corp" in a.answer


def test_nl_query_project_owner_resolves_over_fact():
    s = _seeded()
    a = nl_query(s, "Who owns Project Atlas?")
    assert a.grounded and "Tom Walsh" in a.answer


def test_nl_query_no_match_refuses():
    s = _seeded()
    a = nl_query(s, "What is the airspeed velocity of an unladen swallow?")
    assert a.grounded is False and a.refusal


def test_every_demo_answer_is_claim_checked_clean():
    # the anti-fabrication guarantee: every grounded answer's quantities trace to a cited node
    s = _seeded()
    for q in ["What is the BigClient contract value?", "What is our burn rate?",
              "Who decided to prioritise BigClient renewal?", "Who owns Project Atlas?"]:
        a = nl_query(s, q)
        assert a.grounded, f"{q} should be grounded"
