"""R-029 TIDY2 item 6: `link_entities`, `neighbors`, `path` and `as_of` read by index, with results
IDENTICAL to the whole-graph reads they replace.

E2E run 5 F-05: each of these typed EVERY open node or edge (two pointer queries each) to keep a
handful — 6-8 s for `/brain/neighbors` and 40-50 s for linking on the 50k-node finance graph. They
now read through the indexes and type only what they return.

The reference is the whole-graph algorithm itself, run over a `_WholeGraph` wrapper (a store-like
object that is not a `GraphStore`, so `query` takes its generic path — the original code, byte for
byte). Every expectation is the reference's answer on the same store; nothing is written down.
The graph is built on BOTH packs from their own vocabularies (every bound non-document node kind,
every edge kind the store accepts), with the shapes that break naive rewrites: several open
versions of one id, closed versions and edges, a self-loop, a legacy uncited attribute, and
labels that tie under BM25.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import random

import pytest

from databridge_core import SourcePointer
from databridge_graph.packs import clear_caches, deployment

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


class _WholeGraph:
    """A store-like object that is NOT a `GraphStore`: `query` reads it the original way (every
    open row through ``open_nodes()``/``open_edges()``)."""

    def __init__(self, store) -> None:
        self._s = store

    def __getattr__(self, name):
        return getattr(self._s, name)


def _ptr(n: int, col: str = "name") -> SourcePointer:
    return SourcePointer(source_id="db", kind="table_cell", table="t", column=col, row_id=str(n))


def _build(vocab, store, rng: random.Random) -> list[str]:
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.schema import GraphEdge, GraphNode

    kinds = sorted(k for k in vocab.node_kinds if not vocab.is_document_kind(k))
    words = ["north", "harbour", "freight", "ledger", "pellucid", "atlas", "cedar", "delta",
             "fairmont", "orbit"]
    ids: list[str] = []
    for i in range(60):
        kind = kinds[i % len(kinds)]
        label = " ".join(rng.sample(words, 2)) if i % 7 else "atlas cedar"   # ties under BM25
        nid = f"{kind}:n{i}"
        node = GraphNode(id=nid, kind=NodeKind(kind), label=label,
                         canonical_id=f"K-{i:03d}" if i % 3 == 0 else None,
                         attrs={"name": label, "city": rng.choice(words)},
                         attr_sources={"name": _ptr(i), "city": _ptr(i, "city")},
                         sources=(_ptr(i, "id"),), valid_from=T0 + dt.timedelta(days=i),
                         system_from=T0 + dt.timedelta(days=i))
        store.add_node(node)
        ids.append(nid)
    # a second OPEN version of one id, a closed version of another
    store.add_node(GraphNode(id=ids[5], kind=NodeKind(kinds[5 % len(kinds)]), label="orbit delta",
                             attrs={"name": "orbit delta"}, attr_sources={"name": _ptr(500)},
                             sources=(_ptr(500, "id"),), valid_from=T0, system_from=T0))
    store.close_node(ids[9], at=T0 + dt.timedelta(days=90))
    # a LEGACY uncited attribute (written before per-attribute pointers): read-time blanked
    row = store.open_node_row(ids[11])
    attrs = json.loads(row["attrs_json"]) | {"legacy_note": "harbour harbour harbour"}
    store.conn.execute("UPDATE nodes SET attrs_json = ? WHERE rowid_pk = ?",
                       (json.dumps(attrs), row["rowid_pk"]))
    store.conn.commit()
    edge_kinds = sorted(vocab.edge_kinds)
    n = 0
    usable: list[str] = []
    for ek in edge_kinds:
        a, b = ids[0], ids[1]
        try:
            store.add_edge(GraphEdge(id=f"probe:{ek}", src_id=a, dst_id=b, kind=EdgeKind(ek),
                                     sources=(_ptr(900 + n, "fk"),), valid_from=T0,
                                     system_from=T0))
            usable.append(ek)
        except Exception:                                                # noqa: BLE001
            continue
        n += 1
    assert len(usable) >= 2, usable                                        # non-vacuous
    for j in range(140):
        a, b = rng.choice(ids), rng.choice(ids)
        if j == 7:
            b = a                                                          # a self-loop
        store.add_edge(GraphEdge(id=f"e{j}", src_id=a, dst_id=b, kind=EdgeKind(usable[j % len(usable)]),
                                 sources=(_ptr(1000 + j, "fk"),),
                                 valid_from=T0 + dt.timedelta(days=j % 50),
                                 system_from=T0 + dt.timedelta(days=j % 50)))
    for j in (3, 17, 33):
        store.close_edge(f"e{j}", at=T0 + dt.timedelta(days=100))
    return ids


@pytest.fixture(params=["finance", "workspace"])
def graph(request, tmp_path):
    from databridge_graph.store import GraphStore

    clear_caches()
    with deployment(request.param) as vocab:
        store = GraphStore(str(tmp_path / "g.db"))
        ids = _build(vocab, store, random.Random(29))
        yield vocab, store, ids
    clear_caches()


def _dump(objs) -> list:
    return [o.model_dump(mode="json") for o in objs]


def test_neighbors_by_index_equals_the_whole_graph_read(graph):
    from databridge_graph import query

    vocab, store, ids = graph
    ref = _WholeGraph(store)
    kinds = [None, *sorted({r["kind"] for r in store.open_edges()})[:2]]
    checked = 0
    for nid in ids[:24] + ["nobody:here"]:
        for depth in (1, 2):
            for ek in kinds:
                from databridge_graph.kinds import EdgeKind
                k = EdgeKind(ek) if ek else None
                got = query.neighbors(store, nid, edge_kind=k, depth=depth)
                want = query.neighbors(ref, nid, edge_kind=k, depth=depth)
                assert _dump(got.nodes) == _dump(want.nodes), (nid, depth, ek)
                assert _dump(got.edges) == _dump(want.edges), (nid, depth, ek)
                checked += bool(want.edges)
    assert checked > 30                                                    # non-vacuous


def test_path_by_endpoints_equals_the_whole_graph_read(graph):
    from databridge_graph import query

    _vocab, store, ids = graph
    ref = _WholeGraph(store)
    found = raised = 0

    def outcome(s, a, b):
        try:
            return query.path(s, a, b)
        except KeyError as exc:            # a path step through a closed node: as before
            return ("KeyError", exc.args)

    for a, b in itertools.islice(itertools.permutations(ids[:14], 2), 120):
        got, want = outcome(store, a, b), outcome(ref, a, b)
        if isinstance(want, tuple):
            assert got == want, (a, b)
            raised += 1
            continue
        assert (got.found, got.src_id, got.dst_id) == (want.found, want.src_id, want.dst_id)
        assert [(s.edge.model_dump(mode="json"), s.to_node.model_dump(mode="json"))
                for s in got.steps] == \
            [(s.edge.model_dump(mode="json"), s.to_node.model_dump(mode="json"))
             for s in want.steps], (a, b)
        found += got.found and len(got.steps) > 1
    assert found > 10                                                      # non-vacuous


def test_linking_by_index_equals_the_whole_graph_read(graph):
    from databridge_graph import query

    _vocab, store, ids = graph
    ref = _WholeGraph(store)
    labels = sorted({r["label"] for r in store.open_nodes()})
    questions = [*labels, "what about atlas cedar?", "harbour", "K-003", "orbit", "nothing at all",
                 "the meaning of life"]
    hits = 0
    for q in questions:
        for k in (1, 3, 8):
            got, want = query.link_entities(store, q, top_k=k), query.link_entities(ref, q, top_k=k)
            assert _dump(got) == _dump(want), (q, k)
            hits += bool(want)
    assert hits > 20                                                       # non-vacuous
    # the cache follows the store: a new node is linkable at once
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode
    kind = store.open_node_row(ids[0])["kind"]
    store.add_node(GraphNode(id=f"{kind}:zephyr", kind=NodeKind(kind), label="zephyr quartz",
                             attrs={"name": "zephyr quartz"}, attr_sources={"name": _ptr(777)},
                             sources=(_ptr(777, "id"),), valid_from=T0, system_from=T0))
    assert [n.id for n in query.link_entities(store, "zephyr quartz")] == \
        [n.id for n in query.link_entities(ref, "zephyr quartz")] == [f"{kind}:zephyr"]


def test_as_of_by_rowid_equals_the_whole_graph_read(graph):
    from databridge_graph import query

    _vocab, store, _ids = graph
    ref = _WholeGraph(store)
    now = store.now()
    nonempty = 0
    for vt in (T0 - dt.timedelta(days=1), T0 + dt.timedelta(days=20), T0 + dt.timedelta(days=95),
               now):
        for st in (T0 + dt.timedelta(days=10), T0 + dt.timedelta(days=95), now):
            got, want = query.as_of(store, vt, st), query.as_of(ref, vt, st)
            assert _dump(got.nodes) == _dump(want.nodes) and _dump(got.edges) == _dump(want.edges)
            nonempty += bool(want.edges)
    assert nonempty >= 3                                                   # non-vacuous


def test_no_whole_graph_typing_on_a_large_graph(graph, monkeypatch):
    """The point of the change: on a GraphStore, `neighbors` and `link_entities` type only what
    they return (a filler of open nodes and edges far from the root is never typed)."""
    from databridge_graph import query

    _vocab, store, ids = graph
    query.link_entities(store, "warm the cache")
    typed = {"n": 0, "e": 0}
    real_n, real_e = query.row_to_node, query.row_to_edge

    def count_n(s, r):
        typed["n"] += 1
        return real_n(s, r)

    def count_e(s, r):
        typed["e"] += 1
        return real_e(s, r)

    monkeypatch.setattr(query, "row_to_node", count_n)
    monkeypatch.setattr(query, "row_to_edge", count_e)
    open_n, open_e = len(store.open_nodes()), len(store.open_edges())
    sub = query.neighbors(store, ids[0], depth=1)
    assert typed["e"] == len(sub.edges) < open_e
    assert typed["n"] == len(sub.nodes) < open_n
    typed.update(n=0, e=0)
    got = query.link_entities(store, "atlas cedar", top_k=3)
    assert typed["n"] == len(got) and typed["e"] == 0


# --- R-029 Codex round 1 (TIDY2) ---------------------------------------------------------------
def _concurrent_after_first_read(store, monkeypatch, write):
    """Run ``write(other)`` — another connection to the same file — right after the operation's
    FIRST statement, as a concurrent writer would. A writer blocked by the reader's snapshot is
    a correct outcome too (it is recorded, not raised)."""
    from databridge_graph.store import GraphStore

    other = GraphStore(store.path)
    other.conn.execute("PRAGMA busy_timeout = 100")
    real = store.fetchall
    state = {"done": False, "blocked": False}

    def spy(sql, params=()):
        out = real(sql, params)
        if not state["done"]:
            state["done"] = True
            try:
                write(other)
            except Exception:                                            # noqa: BLE001
                state["blocked"] = True
                other.conn.rollback()
            finally:
                other.close()
        return out

    monkeypatch.setattr(store, "fetchall", spy)
    return state


def test_as_of_never_returns_a_row_outside_its_own_interval(graph, monkeypatch):
    """f1 #5: a row selected live, then closed by a concurrent supersession BEFORE the asked
    system time, must not come back in the snapshot."""
    from databridge_graph import query
    from databridge_graph.query import _live_at

    _vocab, store, ids = graph
    vt = st = store.now() + dt.timedelta(days=1)
    _concurrent_after_first_read(
        store, monkeypatch, lambda o: o.close_node(ids[2], at=store.now()))
    snap = query.as_of(store, vt, st)
    rows = {r["rowid_pk"]: r for r in store._s.fetchall("SELECT * FROM nodes")} \
        if hasattr(store, "_s") else None
    for n in snap.nodes:
        assert n.system_to is None or n.system_to > st, n.id
    assert snap.nodes                                                     # non-vacuous


def test_path_and_neighbors_hydrate_from_the_snapshot_they_searched(graph, monkeypatch):
    """f1 #5: an edge found open must not be hydrated after a concurrent close."""
    from databridge_graph import query

    _vocab, store, ids = graph
    probe = query.path(store, ids[0], ids[13])
    assert probe.found and probe.steps                                    # non-vacuous
    first = probe.steps[0].edge.id
    _concurrent_after_first_read(store, monkeypatch,
                                 lambda o: o.close_edge(first, at=store.now()))
    got = query.path(store, ids[0], ids[13])
    assert all(s.edge.system_to is None for s in got.steps)
    monkeypatch.undo()
    sub_edge = query.neighbors(store, ids[0], depth=2).edges[0].id
    _concurrent_after_first_read(store, monkeypatch,
                                 lambda o: o.close_edge(sub_edge, at=store.now()))
    sub = query.neighbors(store, ids[0], depth=2)
    assert all(e.system_to is None for e in sub.edges)


def test_a_refreshed_store_never_links_against_the_previous_files_text(tmp_path):
    """f1 #6: `refresh()` swaps the file under the same store object; equal counts and rowids
    must not keep the old BM25 index."""
    import os
    import shutil

    from databridge_graph import query
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode
    from databridge_graph.store import GraphStore

    clear_caches()
    with deployment("workspace") as vocab:
        kind = sorted(k for k in vocab.node_kinds if not vocab.is_document_kind(k))[0]

        def one(path, label):
            s = GraphStore(str(path))
            for i, lab in enumerate([label, "north ledger", "cedar delta", "orbit atlas"]):
                s.add_node(GraphNode(id=f"{kind}:n{i}", kind=NodeKind(kind), label=lab,
                                     attrs={"name": lab}, attr_sources={"name": _ptr(i)},
                                     sources=(_ptr(i, "id"),), valid_from=T0, system_from=T0))
            s.close()

        one(tmp_path / "a.db", "amber quarry")
        one(tmp_path / "b.db", "zircon bay")
        live = tmp_path / "live.db"
        shutil.copy(tmp_path / "a.db", live)
        store = GraphStore(str(live))
        assert [n.label for n in query.link_entities(store, "amber quarry")] == ["amber quarry"]
        store.conn.close()
        os.replace(tmp_path / "b.db", live)
        store.refresh()
        assert [n.label for n in query.link_entities(store, "zircon bay")] == ["zircon bay"]
    clear_caches()


def test_path_expands_its_frontier_by_index_and_as_of_filters_in_sql(graph, monkeypatch):
    """f1 #9: a near target is found without reading every edge's endpoints, and `as_of` reads
    only the rows live at the asked time (filtered by SQL, not by scanning history in Python)."""
    from databridge_graph import query
    from databridge_graph.query import _live_at

    _vocab, store, ids = graph
    near = next(e for e in store.open_edges() if e["src_id"] != e["dst_id"])
    seen: list[tuple[str, int]] = []
    real = store.fetchall

    def spy(sql, params=()):
        out = real(sql, params)
        seen.append((" ".join(sql.split()), len(out)))
        return out

    monkeypatch.setattr(store, "fetchall", spy)
    got = query.path(store, near["src_id"], near["dst_id"])
    assert got.found and len(got.steps) == 1
    whole = ("SELECT rowid_pk, src_id, dst_id FROM edges WHERE system_to IS NULL "
             "ORDER BY rowid_pk")
    assert seen and not any(s == whole for s, _ in seen), seen
    seen.clear()
    vt = st = T0 + dt.timedelta(days=20)
    snap = query.as_of(store, vt, st)
    live_nodes = sum(1 for r in real("SELECT * FROM nodes") if _live_at(r, vt, st))
    node_reads = [n for s, n in seen if s.startswith("SELECT") and "FROM nodes" in s
                  and "sources" not in s]
    assert len(snap.nodes) == live_nodes and max(node_reads) <= live_nodes


def test_as_of_agrees_with_the_rule_on_legacy_timestamp_spellings(graph):
    """f1 #5/#9: `as_of` filters in SQL, then re-checks every row by `_live_at`. A row written in
    an older spelling (no microseconds) must be judged by the rule, never by string order: one
    the strings call live but the rule does not, and one the strings would wrongly drop."""
    from databridge_graph import query

    _vocab, store, ids = graph
    vt = T0 + dt.timedelta(days=200)                       # a whole second: .000000
    st = T0 + dt.timedelta(days=200, microseconds=500000)
    sec = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S") + "Z"          # noqa: E731
    a = store.open_node_row(ids[20])["rowid_pk"]
    b = store.open_node_row(ids[21])["rowid_pk"]
    # closed at st's second, i.e. BEFORE st (rule: not live); as strings 'Z' sorts after '.5'
    store.conn.execute("UPDATE nodes SET system_to = ? WHERE rowid_pk = ?", (sec(st), a))
    # valid from exactly vt (rule: live); as strings 'Z' sorts after '.000000'
    store.conn.execute("UPDATE nodes SET valid_from = ? WHERE rowid_pk = ?", (sec(vt), b))
    store.conn.commit()
    got, want = query.as_of(store, vt, st), query.as_of(_WholeGraph(store), vt, st)
    assert _dump(got.nodes) == _dump(want.nodes)
    ids_got = {n.id for n in got.nodes}
    assert ids[20] not in ids_got and ids[21] in ids_got                  # non-vacuous
