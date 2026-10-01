"""``/graph`` traversal — connected browse, depth-2 neighbourhoods, path, search, components, reload.

Built over a SMALL SYNTHETIC topology written into a temp SQLite file through the real graph
package (`GraphStore` + `upsert_node` / `upsert_edge`), never over `var/graph`. The topology is
declared once below as an adjacency list, and every expectation (component count, largest
component, shortest-path length, isolated count) is COMPUTED from that list by an independent
reference BFS in this file — nothing is a number written down (CLAUDE.md rule 6).

Kinds are not named here either: the test takes the first node kind and the first non-attested
edge kind the bound deployment declares. The routes under test never interpret a kind.
"""
from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway import graph_routes
from databridge_gateway.graph_routes import register_graph_routes
from databridge_graph.clinic_seed import SEED_SYSTEM_TIME
from databridge_graph.packs import active_vocabulary, deployment

from conftest import AUTH_HEADERS

# ---- the topology -------------------------------------------------------------------------------
# A hub with six spokes, a six-node chain hanging off the hub, a separate pair, and two isolated
# nodes. Every id is synthetic.
HUB = "t:hub"
SPOKES = [f"t:spoke-{i}" for i in range(6)]
CHAIN = [f"t:chain-{i}" for i in range(6)]
PAIR = ["t:pair-a", "t:pair-b"]
ISOLATED = ["t:alone-1", "t:alone-2"]
EDGES: list[tuple[str, str]] = (
    [(HUB, s) for s in SPOKES]
    + [(HUB, CHAIN[0])]
    + [(CHAIN[i], CHAIN[i + 1]) for i in range(len(CHAIN) - 1)]
    + [(PAIR[0], PAIR[1])]
)
ALL_NODES = [HUB, *SPOKES, *CHAIN, *PAIR, *ISOLATED]


def _adjacency() -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {n: set() for n in ALL_NODES}
    for a, b in EDGES:
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _ref_distance(src: str, dst: str) -> int | None:
    adj = _adjacency()
    seen = {src: 0}
    q = deque([src])
    while q:
        cur = q.popleft()
        if cur == dst:
            return seen[cur]
        for nxt in sorted(adj[cur]):
            if nxt not in seen:
                seen[nxt] = seen[cur] + 1
                q.append(nxt)
    return None


def _ref_components() -> list[set[str]]:
    adj = _adjacency()
    seen: set[str] = set()
    comps = []
    for n in ALL_NODES:
        if n in seen:
            continue
        comp = {n}
        q = deque([n])
        while q:
            cur = q.popleft()
            for nxt in adj[cur]:
                if nxt not in comp:
                    comp.add(nxt)
                    q.append(nxt)
        seen |= comp
        comps.append(comp)
    return comps


def _kinds():
    vocab = active_vocabulary()
    from databridge_graph.meta import ATTESTED_META_KINDS

    node_kind = sorted(vocab.node_kinds)[0]
    edge_kind = next(name for name, decl in sorted(vocab.edge_kinds.items())
                     if decl.binds_to not in ATTESTED_META_KINDS)
    return node_kind, edge_kind


def _build_store(path: str, nodes=ALL_NODES, edges=EDGES):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.merge import upsert_edge, upsert_node
    from databridge_graph.schema import GraphEdge, GraphNode
    from databridge_graph.store import GraphStore

    node_kind, edge_kind = _kinds()
    store = GraphStore(path)
    for i, nid in enumerate(nodes):
        ptr = SourcePointer(source_id="synthetic_topology.csv", kind="table_cell",
                            table="topology", column="name", row_id=str(i))
        upsert_node(store, GraphNode(
            id=nid, kind=NodeKind(node_kind), label=f"Node {nid.split(':', 1)[1]}",
            attrs={"name": nid}, attr_sources={"name": ptr}, sources=(ptr,),
            valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))
    for j, (a, b) in enumerate(edges):
        ptr = SourcePointer(source_id="synthetic_topology.csv", kind="table_cell",
                            table="links", column="dst", row_id=str(j))
        upsert_edge(store, GraphEdge(
            id=f"{a}|{edge_kind}|{b}", src_id=a, dst_id=b, kind=EdgeKind(edge_kind),
            sources=(ptr,), valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))
    return store


@pytest.fixture()
def bound():
    with deployment("healthcare"):
        yield


@pytest.fixture()
def store(tmp_path, bound):
    return _build_store(str(tmp_path / "topology.db"))


def _app(store=None, **state):
    application = FastAPI()
    if store is not None:
        application.state.graph_store = store
        application.state.brain = SimpleNamespace(store=store)
    for k, v in state.items():
        setattr(application.state, k, v)
    register_graph_routes(application)
    return application


@pytest.fixture()
def client(store):
    graph_routes._clear_stats_cache()
    return TestClient(_app(store), headers=AUTH_HEADERS)


# ---- store resolution ---------------------------------------------------------------------------


def test_graph_store_is_preferred_and_named_with_its_file(tmp_path, bound):
    shared = _build_store(str(tmp_path / "shared.db"))
    other = _build_store(str(tmp_path / "other.db"), nodes=[HUB], edges=[])
    application = FastAPI()
    application.state.graph_store = shared
    application.state.brain = SimpleNamespace(store=other)
    register_graph_routes(application)
    body = TestClient(application, headers=AUTH_HEADERS).get("/graph/facets").json()
    assert body["counts"] == shared.counts()
    assert "shared.db" in body["graph_source"]
    assert "app.state.graph_store" in body["graph_source"]


# ---- facets: component stats --------------------------------------------------------------------


def test_facets_component_stats_match_a_reference_bfs(client):
    comps = _ref_components()
    body = client.get("/graph/facets").json()["components"]
    assert body["computed"] is True
    assert body["count"] == len(comps)
    assert body["largest"] == max(len(c) for c in comps)
    assert body["isolated_nodes"] == sum(1 for c in comps if len(c) == 1)
    assert body["isolated_nodes"] > 0 and body["count"] > 1, "non-vacuous topology"


def test_facets_per_kind_edge_counts_add_up(client, store):
    body = client.get("/graph/facets").json()
    # every open edge contributes one incidence at each end, whatever its endpoints' kinds
    assert sum(r["incident_edges"] for r in body["node_kinds"]) == 2 * store.counts()["edges"]
    assert sum(r["count"] for r in body["node_kinds"]) == store.counts()["nodes"]


def test_component_stats_are_recomputed_after_a_write(tmp_path, bound):
    graph_routes._clear_stats_cache()
    store = _build_store(str(tmp_path / "grow.db"))
    c = TestClient(_app(store), headers=AUTH_HEADERS)
    before = c.get("/graph/facets").json()["components"]["count"]
    # join the pair onto the chain: one component fewer
    from databridge_core import SourcePointer
    from databridge_graph.kinds import EdgeKind
    from databridge_graph.merge import upsert_edge
    from databridge_graph.schema import GraphEdge

    _, edge_kind = _kinds()
    ptr = SourcePointer(source_id="synthetic_topology.csv", kind="table_cell",
                        table="links", column="dst", row_id="late")
    upsert_edge(store, GraphEdge(
        id="late-link", src_id=CHAIN[-1], dst_id=PAIR[0], kind=EdgeKind(edge_kind),
        sources=(ptr,), valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))
    after = c.get("/graph/facets").json()["components"]["count"]
    assert after == before - 1


def test_component_stats_over_the_cap_say_unknown(client, monkeypatch):
    monkeypatch.setattr(graph_routes, "COMPONENT_EDGE_CAP", 1)
    graph_routes._clear_stats_cache()
    body = client.get("/graph/facets").json()["components"]
    assert body["computed"] is False
    assert body["count"] is None and body["largest"] is None
    assert body["reason"]


# ---- default browse: connected, hub-seeded ------------------------------------------------------


def test_default_browse_is_hub_seeded_and_connected(client, store):
    body = client.get("/graph/neighborhood").json()
    assert body["mode"] == "hubs"
    ids = {n["id"] for n in body["nodes"]}
    degrees = {n: len(v) for n, v in _adjacency().items()}
    top = max(degrees, key=lambda n: (degrees[n], n))
    assert top in ids, "the best-connected node must be in the default view"
    touched = {e["src_id"] for e in body["edges"]} | {e["dst_id"] for e in body["edges"]}
    assert ids and ids <= touched, "no isolated node may be drawn"
    assert not ids & set(ISOLATED)
    iso = sum(1 for c in _ref_components() if len(c) == 1)
    assert body["omitted"]["isolated_nodes"] == iso
    assert body["matched_total"] == store.counts()["nodes"]
    assert body["omitted"]["nodes_not_drawn"] == body["matched_total"] - body["returned"]


def test_default_browse_small_window_still_never_draws_a_lone_dot(client):
    body = client.get("/graph/neighborhood", params={"limit": 3}).json()
    assert 0 < body["returned"] <= 3
    ids = {n["id"] for n in body["nodes"]}
    touched = {e["src_id"] for e in body["edges"]} | {e["dst_id"] for e in body["edges"]}
    assert ids <= touched
    assert body["truncated"] is True


def test_edgeless_graph_browse_is_empty_with_a_reason(tmp_path, bound):
    store = _build_store(str(tmp_path / "flat.db"), nodes=ISOLATED, edges=[])
    body = TestClient(_app(store), headers=AUTH_HEADERS).get("/graph/neighborhood").json()
    assert body["nodes"] == []
    assert body["matched_total"] == len(ISOLATED)
    assert body["note"]


def test_empty_store_browse_is_empty_not_an_error(tmp_path, bound):
    from databridge_graph.store import GraphStore

    store = GraphStore(str(tmp_path / "empty.db"))
    c = TestClient(_app(store), headers=AUTH_HEADERS)
    body = c.get("/graph/neighborhood").json()
    assert body["nodes"] == [] and body["matched_total"] == 0
    facets = c.get("/graph/facets").json()
    assert facets["counts"]["nodes"] == 0
    assert facets["components"]["count"] == 0


def test_filtered_browse_ranks_best_connected_first(client):
    body = client.get("/graph/neighborhood", params={"q": "Node", "limit": 1}).json()
    degrees = {n: len(v) for n, v in _adjacency().items()}
    assert body["nodes"][0]["id"] == max(degrees, key=lambda n: (degrees[n], -ALL_NODES.index(n)))


# ---- neighbourhood: default depth ---------------------------------------------------------------


def test_neighbourhood_defaults_to_depth_two(client):
    body = client.get("/graph/neighborhood", params={"node": CHAIN[2]}).json()
    assert body["depth"] == graph_routes.DEFAULT_DEPTH == 2
    ids = {n["id"] for n in body["nodes"]}
    within = {n for n in ALL_NODES
              if (d := _ref_distance(CHAIN[2], n)) is not None and d <= 2}
    assert ids == within
    assert len(within) > 3, "depth 2 must reach beyond the immediate neighbours"


# ---- path ---------------------------------------------------------------------------------------


def _assert_is_a_walk(body, src, dst):
    ids = [n["id"] for n in body["nodes"]]
    assert ids[0] == src and ids[-1] == dst
    assert len(body["edges"]) == len(ids) - 1 == body["hops"]
    for i, e in enumerate(body["edges"]):
        assert {e["src_id"], e["dst_id"]} == {ids[i], ids[i + 1]}


def test_path_is_shortest_and_walkable(client):
    src, dst = CHAIN[-1], SPOKES[0]
    body = client.get("/graph/path",
                      params={"from": src, "to": dst, "max_depth": graph_routes.MAX_PATH_DEPTH}).json()
    assert body["found"] is True
    assert body["hops"] == _ref_distance(src, dst)
    _assert_is_a_walk(body, src, dst)


def test_path_respects_max_depth_and_says_so(client):
    src, dst = CHAIN[-1], SPOKES[0]
    need = _ref_distance(src, dst)
    body = client.get("/graph/path",
                      params={"from": src, "to": dst, "max_depth": need - 1}).json()
    assert body["found"] is False
    assert body["reason"] == "no_path_within_max_depth"
    assert body["message"] and body["nodes"] == []


def test_path_between_components_is_disconnected_not_out_of_hops(client):
    body = client.get("/graph/path", params={"from": HUB, "to": PAIR[0], "max_depth": 10}).json()
    assert _ref_distance(HUB, PAIR[0]) is None
    assert body["found"] is False
    assert body["reason"] == "disconnected"


def test_path_budget_exhaustion_is_unknown_not_absent(client, monkeypatch):
    monkeypatch.setattr(graph_routes, "PATH_EDGE_BUDGET", 1)
    body = client.get("/graph/path", params={"from": CHAIN[-1], "to": SPOKES[0]}).json()
    assert body["found"] is False
    assert body["reason"] == "search_budget_exhausted"


def test_path_to_self_is_zero_hops(client):
    body = client.get("/graph/path", params={"from": HUB, "to": HUB}).json()
    assert body["found"] is True and body["hops"] == 0
    assert [n["id"] for n in body["nodes"]] == [HUB]


def test_path_unknown_node_is_404(client):
    resp = client.get("/graph/path", params={"from": HUB, "to": "t:nope"})
    assert resp.status_code == 404
    assert resp.json()["refusal"]["code"] == "UNKNOWN_NODE"


def test_path_needs_both_ends(client):
    resp = client.get("/graph/path", params={"from": HUB})
    assert resp.status_code == 422
    assert resp.json()["refusal"]["code"] == "PATH_NEEDS_TWO_NODES"


# ---- search -------------------------------------------------------------------------------------


def test_search_finds_by_label_and_ranks_exact_id_first(client):
    body = client.get("/graph/search", params={"q": HUB}).json()
    assert body["nodes"][0]["id"] == HUB
    body = client.get("/graph/search", params={"q": "chain"}).json()
    ids = {n["id"] for n in body["nodes"]}
    assert ids == set(CHAIN)
    assert body["matched_total"] == len(CHAIN) and body["truncated"] is False


def test_search_is_windowed_and_says_so(client):
    body = client.get("/graph/search", params={"q": "spoke", "limit": 2}).json()
    assert body["returned"] == 2
    assert body["matched_total"] == len(SPOKES)
    assert body["truncated"] is True


def test_search_treats_like_wildcards_literally(client):
    body = client.get("/graph/search", params={"q": "%"}).json()
    assert body["nodes"] == [] and body["matched_total"] == 0


def test_search_refuses_empty_text(client):
    resp = client.get("/graph/search", params={"q": "  "})
    assert resp.status_code == 422
    assert resp.json()["refusal"]["code"] == "SEARCH_NEEDS_TEXT"


# ---- reload -------------------------------------------------------------------------------------


def test_reload_without_a_hook_says_unsupported(client):
    resp = client.post("/graph/reload")
    assert resp.status_code == 501
    assert resp.json()["refusal"]["code"] == "RELOAD_UNSUPPORTED"


def test_reload_calls_the_hook_and_answers_about_the_new_store(tmp_path, bound):
    first = _build_store(str(tmp_path / "first.db"))
    second = _build_store(str(tmp_path / "second.db"), nodes=PAIR, edges=[tuple(PAIR)])
    application = _app(first)

    def reload_graph():
        application.state.graph_store = second
        application.state.brain = SimpleNamespace(store=second)

    application.state.reload_graph = reload_graph
    c = TestClient(application, headers=AUTH_HEADERS)
    assert c.get("/graph/facets").json()["counts"] == first.counts()
    body = c.post("/graph/reload").json()
    assert body["reloaded"] is True
    assert body["counts"] == second.counts()
    assert "second.db" in body["graph_source"]
    assert c.get("/graph/facets").json()["counts"] == second.counts()


# ---- R-017 (Codex t07) ---------------------------------------------------------------------------


def test_edge_probe_reports_a_skipped_chunk_as_incomplete(store, monkeypatch):
    """H2. An edge joining two chunks is read twice and charged twice; when the budget then runs
    out before the last chunk, fewer UNIQUE rows than the budget came back — which used to read as
    "nothing left unread". The probe must say it is incomplete."""
    monkeypatch.setattr(graph_routes, "_EDGE_PARAM_CHUNK", 1)
    # SPOKE0's one edge is to HUB, so HUB's chunk re-reads it; PAIR[0]'s chunk is then skipped.
    budget = 1 + len(SPOKES) + 1 - 1          # SPOKE0 (1 row) + HUB capped short of its 7
    rows, complete = graph_routes._edges_touching_chunk(
        store, [SPOKES[0], HUB, PAIR[0]], include_closed=False, budget=budget)
    assert len(rows) < budget, "the precondition: unique rows fall short of the budget"
    assert complete is False
    rows, complete = graph_routes._edges_touching_chunk(
        store, [SPOKES[0], HUB, PAIR[0]], include_closed=False, budget=10_000)
    assert complete is True and {r["src_id"] for r in rows} >= {HUB, PAIR[0]}


def test_edge_probe_chunk_honours_the_parameter_margin():
    """M3. The id list is bound twice plus a LIMIT: one statement must stay inside 999 bindings."""
    assert 2 * graph_routes._EDGE_PARAM_CHUNK + 1 <= 999


def test_a_capped_closing_pass_is_reported_as_edge_truncation(client, monkeypatch):
    """H3. Every node in view is present, but the pass that reads the edges BETWEEN them was
    capped: that used to be silent (`truncated: false`)."""
    monkeypatch.setattr(graph_routes, "EDGE_BUDGET_PER_HOP", 2)
    monkeypatch.setattr(graph_routes, "CLOSING_EDGES_PER_NODE", 0)
    body = client.get("/graph/neighborhood", params={"q": "chain"}).json()
    internal = sum(1 for a, b in EDGES if a in CHAIN and b in CHAIN)
    assert internal > 2, "the chain must have more internal edges than the cap"
    assert body["matched_total"] == body["returned"] == len(CHAIN)
    assert body["nodes_truncated"] is False
    assert body["edges_truncated"] is True and body["truncated"] is True
    assert body["edges_returned"] == len(body["edges"]) < internal
    assert body["note"]


def test_node_detail_counts_incident_edges_in_sql_and_windows_them(client, monkeypatch):
    """H4. The incident-edge list is a COUNT plus a LIMIT, never every edge materialised."""
    monkeypatch.setattr(graph_routes, "EDGE_TOUCH_LIMIT", 2)
    body = client.get(f"/graph/node/{HUB}").json()
    degree = len(_adjacency()[HUB])
    assert body["edges"]["matched_total"] == degree > 2
    assert body["edges"]["returned"] == 2 and body["edges"]["truncated"] is True


def test_node_detail_windows_its_versions_and_says_so(client, store, monkeypatch):
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode

    row = store.open_node_row(HUB)
    ptr = store.sources_for(HUB)[0]
    store.replace_node(GraphNode(id=HUB, kind=NodeKind(row["kind"]), label="Node hub v2",
                                 sources=(ptr,), valid_from=SEED_SYSTEM_TIME,
                                 system_from=SEED_SYSTEM_TIME),
                       at=SEED_SYSTEM_TIME.replace(year=SEED_SYSTEM_TIME.year + 1))
    total = len(store.all_node_versions(HUB))
    assert total >= 2
    monkeypatch.setattr(graph_routes, "VERSION_LIMIT", 1)
    body = client.get(f"/graph/node/{HUB}").json()
    assert body["versions_total"] == total and body["versions_truncated"] is True
    assert len(body["versions"]) == 1 and body["versions"][0]["is_open"] is True
    assert body["label"] == "Node hub v2"


def test_path_node_budget_is_a_bound_not_an_after_the_fact_check(client, monkeypatch):
    """M1. The budget is checked before each discovery, so the search never holds more nodes than
    it advertises — and running out is 'unknown', never 'absent'."""
    monkeypatch.setattr(graph_routes, "PATH_NODE_BUDGET", 4)
    src, dst = CHAIN[-1], SPOKES[0]
    assert _ref_distance(src, dst) > 3, "the path must need more nodes than the budget"
    body = client.get("/graph/path", params={"from": src, "to": dst,
                                             "max_depth": graph_routes.MAX_PATH_DEPTH}).json()
    assert body["found"] is False and body["reason"] == "search_budget_exhausted"
    assert body["explored"] <= graph_routes.PATH_NODE_BUDGET


def test_a_closed_only_seed_is_not_a_current_node(client, store):
    """M2. Seed validation and the walk now read the same version scope."""
    from databridge_graph.clinic_seed import SEED_SYSTEM_TIME as T

    store.close_node(ISOLATED[0], at=T.replace(year=T.year + 1))
    r = client.get("/graph/neighborhood", params={"node": ISOLATED[0]})
    assert r.status_code == 404 and r.json()["refusal"]["code"] == "UNKNOWN_NODE"
    ok = client.get("/graph/neighborhood", params={"node": ISOLATED[0], "include_closed": True})
    assert ok.status_code == 200 and {n["id"] for n in ok.json()["nodes"]} == {ISOLATED[0]}


def test_search_folds_case_beyond_ascii(tmp_path, bound):
    """M4. SQLite's LOWER folds ASCII only; a differently-cased non-ASCII label must still match."""
    label = "Ångström Grüße"
    store = _build_store(str(tmp_path / "unicode.db"), nodes=["t:unicode"], edges=[])
    store.conn.execute("UPDATE nodes SET label = ? WHERE id = ?", (label, "t:unicode"))
    store.conn.commit()
    c = TestClient(_app(store), headers=AUTH_HEADERS)
    for query in (label.upper(), "ÅNGSTRÖM", "GRÜSSE"):
        body = c.get("/graph/search", params={"q": query}).json()
        assert [n["id"] for n in body["nodes"]] == ["t:unicode"], query
    browse = c.get("/graph/neighborhood", params={"q": "ÅNGSTRÖM"}).json()
    assert browse["matched_total"] == 1
