"""demo_seed.py — Acme Corp investor seed: counts, <1s load, idempotency, provenance (T08 Part 2)."""
from __future__ import annotations

import time

from databridge_graph import GraphStore, NodeKind
from databridge_graph.demo_seed import (
    SEED_SYSTEM_TIME,
    build_seed_edges,
    build_seed_nodes,
    load_demo_seed,
)


def _store():
    return GraphStore(clock=lambda: SEED_SYSTEM_TIME)


def test_seed_counts_match_builders():
    # the dense investor seed: ~100 nodes / ~180 edges. Assert the store matches the builders
    # (seed-size-robust — no hardcoded count to break when the roster grows).
    s = _store()
    stats = load_demo_seed(s)
    n_nodes, n_edges = len(build_seed_nodes()), len(build_seed_edges())
    assert stats["nodes_created"] == n_nodes >= 100    # dense graph (investor demo)
    assert stats["edges_created"] == n_edges >= 150
    assert s.counts() == {"nodes": n_nodes, "edges": n_edges, "sources": stats["sources_attached"]}


def test_node_kind_breakdown():
    s = _store()
    load_demo_seed(s)
    by_kind = {}
    for r in s.open_nodes():
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    # full roster: people / orgs / projects / decisions / facts / source documents
    assert by_kind == {NodeKind.PERSON.value: 31, NodeKind.ORG.value: 8,
                       NodeKind.PROJECT.value: 12, NodeKind.DECISION.value: 20,
                       NodeKind.FACT.value: 16, NodeKind.DOCUMENT.value: 15}


def test_seed_loads_under_two_seconds():
    s = _store()
    t0 = time.perf_counter()
    load_demo_seed(s)
    assert (time.perf_counter() - t0) < 2.0           # demo requirement: <2s load


def test_no_orphan_nodes():
    # every node has ≥1 edge (the graph is fully connected — no floating dots in the render)
    s = _store()
    load_demo_seed(s)
    touched = set()
    for r in s.open_edges():
        touched.add(r["src_id"])
        touched.add(r["dst_id"])
    orphans = [r["id"] for r in s.open_nodes() if r["id"] not in touched]
    assert orphans == [], orphans


def test_seed_is_idempotent():
    s = _store()
    load_demo_seed(s)
    first = s.counts()
    load_demo_seed(s)                                  # second load must not duplicate
    assert s.counts() == first


def test_provenance_completeness_is_100pct():
    # HARD gate (P4): every node AND every edge carries ≥1 SourcePointer
    s = _store()
    load_demo_seed(s)
    for r in s.open_nodes():
        assert s.sources_for(r["id"]), f"node {r['id']} ungrounded"
    for r in s.open_edges():
        assert s.sources_for(r["id"]), f"edge {r['id']} ungrounded"


def test_source_pointer_kinds_are_valid():
    s = _store()
    load_demo_seed(s)
    kinds = set()
    for r in s.open_nodes():
        kinds |= {p.kind for p in s.sources_for(r["id"])}
    assert kinds <= {"doc_span", "email_part"}        # the three fake docs' valid pointer kinds
    assert "email_part" in kinds and "doc_span" in kinds
