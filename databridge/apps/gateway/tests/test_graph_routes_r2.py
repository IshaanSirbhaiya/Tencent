"""Review R-019 (Codex batch 2, track t07 round 2): `/graph` routes. One test per finding, each
failing before its fix. Topologies are synthetic, built through the real graph package; every
expectation is derived from the topology declared in the test, never written down.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from databridge_gateway import graph_routes
from test_graph_routes_traversal import (  # noqa: F401  (fixtures re-used)
    HUB, SPOKES, _app, _build_store, _kinds, bound, client, store)

from conftest import AUTH_HEADERS


def _client(s):
    graph_routes._clear_stats_cache()
    return TestClient(_app(s), headers=AUTH_HEADERS)


def _resolves(ptr: dict) -> bool:
    return bool(ptr) and bool(ptr.get("source_id")) and bool(ptr.get("kind"))


# --- H1: BFS edges only between retained nodes ---------------------------------------------------
def test_a_seed_window_never_returns_an_edge_to_a_node_it_left_out(client):
    body = client.get("/graph/neighborhood", params={"node": HUB, "depth": 1, "limit": 2}).json()
    ids = {n["id"] for n in body["nodes"]}
    assert len(ids) == 2 and body["nodes_truncated"] is True          # the cap really bit
    assert body["edges"], "non-vacuous: the one retained neighbour's edge is drawn"
    for e in body["edges"]:
        assert e["src_id"] in ids and e["dst_id"] in ids, e
    assert body["edges_returned"] == len(body["edges"])


# --- H2: the path budget is charged for RAW rows read ---------------------------------------------
def _dense(tmp_path):
    """S – A1..A4 (a clique among the A's, each with one leaf D), and T – B1..B6.
    Expanding the A-level reads every clique edge twice (once per chunk)."""
    a = [f"t:a{i}" for i in range(4)]
    d = [f"t:d{i}" for i in range(4)]
    b = [f"t:b{i}" for i in range(6)]
    edges = ([("t:s", x) for x in a] + [(a[i], a[j]) for i in range(4) for j in range(i + 1, 4)]
             + list(zip(a, d)) + [("t:t", y) for y in b])
    nodes = ["t:s", "t:t", *a, *d, *b]
    return _build_store(str(tmp_path / "dense.db"), nodes=nodes, edges=edges), edges


def test_the_path_edge_budget_bounds_raw_rows_read(tmp_path, bound, monkeypatch):
    s, edges = _dense(tmp_path)
    monkeypatch.setattr(graph_routes, "_EDGE_PARAM_CHUNK", 1)
    read = {"rows": 0}
    real = s.fetchall

    def spy(sql, params=()):
        rows = real(sql, params)
        if sql.startswith("SELECT * FROM edges WHERE (src_id IN"):
            read["rows"] += len(rows)
        return rows

    monkeypatch.setattr(s, "fetchall", spy)
    unbounded = graph_routes._shortest_path(s, "t:s", "t:t", max_depth=10)
    assert unbounded["reason"] == "disconnected"
    full = read["rows"]
    assert full > len(edges), "non-vacuous: some edge rows are read more than once"
    budget = full - 3
    monkeypatch.setattr(graph_routes, "PATH_EDGE_BUDGET", budget)
    read["rows"] = 0
    result = graph_routes._shortest_path(s, "t:s", "t:t", max_depth=10)
    assert read["rows"] <= budget, (read["rows"], budget)
    assert result["reason"] == "search_budget_exhausted"


# --- C1: emitted grounded fields carry their pointers --------------------------------------------
def test_list_routes_carry_a_pointer_for_every_grounded_field(client):
    bodies = [client.get("/graph/neighborhood", params={"node": HUB}).json(),
              client.get("/graph/neighborhood").json(),
              client.get("/graph/search", params={"q": "spoke"}).json(),
              client.get("/graph/path", params={"from": SPOKES[0], "to": SPOKES[1]}).json()]
    seen_nodes = seen_edges = 0
    for body in bodies:
        for n in body["nodes"]:
            seen_nodes += 1
            assert _resolves(n["pointers"]["label"]), n
            assert n["sources_total"] >= 1
            if n["canonical_id"] is not None:
                assert _resolves(n["pointers"]["canonical_id"])
            else:
                assert "canonical_id" not in n["pointers"]
        for e in body.get("edges", []):
            seen_edges += 1
            assert _resolves(e["pointers"]["edge"]), e
            assert e["sources_total"] >= 1
    assert seen_nodes and seen_edges


def test_node_detail_incident_edges_and_versions_carry_pointers(client):
    body = client.get(f"/graph/node/{HUB}").json()
    incident = body["edges"]["outgoing"] + body["edges"]["incoming"]
    assert incident
    for e in incident:
        assert _resolves(e["pointers"]["edge"])
        assert set(e["attribute_sources"]) >= {k for k, v in e["attrs"].items() if v is not None}
    assert body["versions"]
    for v in body["versions"]:
        assert _resolves(v["pointers"]["label"])


# --- H3: provenance lists are bounded and say so -------------------------------------------------
def test_detail_provenance_is_windowed_with_an_exact_total(tmp_path, bound, monkeypatch):
    from databridge_core import SourcePointer

    s = _build_store(str(tmp_path / "cited.db"), nodes=[HUB, SPOKES[0]], edges=[(HUB, SPOKES[0])])
    extra = [SourcePointer(source_id="many.csv", kind="table_cell", table="t", column="c",
                           row_id=str(i)) for i in range(7)]
    s.append_sources(HUB, extra)
    edge_id = f"{HUB}|{_kinds()[1]}|{SPOKES[0]}"
    s.append_sources(edge_id, extra)
    monkeypatch.setattr(graph_routes, "SOURCE_LIMIT", 3)
    c = _client(s)
    for body in (c.get(f"/graph/node/{HUB}").json(), c.get(f"/graph/edge/{edge_id}").json()):
        total = len(extra) + 1
        assert len(body["sources"]) == 3
        assert body["sources_total"] == total and body["sources_truncated"] is True
        assert "attribute_sources_total" in body and "attribute_sources_truncated" in body
    # control: a small list is complete and says so
    body = c.get(f"/graph/node/{SPOKES[0]}").json()
    assert body["sources_truncated"] is False and len(body["sources"]) == body["sources_total"]


# --- M1: the source-filtered browse folds case like the SQL branches ------------------------------
def _labelled(tmp_path, labels):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode
    from databridge_graph.store import GraphStore
    from databridge_graph.clinic_seed import SEED_SYSTEM_TIME

    kind = _kinds()[0]
    s = GraphStore(str(tmp_path / "labels.db"))
    for i, label in enumerate(labels):
        ptr = SourcePointer(source_id="labels.csv", kind="table_cell", table="t",
                            column="name", row_id=str(i))
        upsert_node(s, GraphNode(id=f"t:l{i}", kind=NodeKind(kind), label=label, sources=(ptr,),
                                 valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))
    return s


def test_source_browse_folds_unicode_case(tmp_path, bound):
    c = _client(_labelled(tmp_path, ["Straße Nord", "Unrelated"]))
    body = c.get("/graph/neighborhood", params={"source": "labels.csv", "q": "STRASSE"}).json()
    assert [n["label"] for n in body["nodes"]] == ["Straße Nord"]
    # control: the SQL browse branch agrees
    body = c.get("/graph/neighborhood", params={"q": "STRASSE"}).json()
    assert [n["label"] for n in body["nodes"]] == ["Straße Nord"]


# --- M2: a degraded fold is never silent ---------------------------------------------------------
class _NoConn:
    """A store exposing reads but no `conn`: the Unicode fold cannot be registered."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name == "conn":
            raise AttributeError(name)
        return getattr(self._inner, name)


def test_search_says_when_case_folding_is_ascii_only(tmp_path, bound):
    s = _labelled(tmp_path, ["Ångström"])
    body = _client(_NoConn(s)).get("/graph/search", params={"q": "ångström"}).json()
    assert body["case_folding"] == "ascii_only" and "ASCII" in (body.get("note") or "")
    body = _client(s).get("/graph/search", params={"q": "ÅNGSTRÖM"}).json()
    assert body["case_folding"] == "unicode" and body["returned"] == 1        # control


# --- M3: include_closed browse is one entry per node ----------------------------------------------
def test_include_closed_kind_browse_counts_nodes_not_versions(tmp_path, bound):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode

    s = _labelled(tmp_path, ["Alpha", "Beta"])
    kind = _kinds()[0]
    for i, label in enumerate(["Alpha 2", "Alpha 3"]):
        ptr = SourcePointer(source_id="labels.csv", kind="table_cell", table="t",
                            column="name", row_id=f"v{i}")
        # INT5 (graph merge, R-022 build cross-track 2a): a relabel is a changed fact only when
        # the new label carries its OWN pointer; an ungrounded spelling corroborates. The label is
        # grounded here so each upsert really opens a version.
        upsert_node(s, GraphNode(id="t:l0", kind=NodeKind(kind), label=label, sources=(ptr,),
                                 attr_sources={"label": ptr},
                                 valid_from=dt.datetime(2026, 5, 1 + i, tzinfo=dt.timezone.utc),
                                 system_from=dt.datetime(2026, 5, 1 + i, tzinfo=dt.timezone.utc)))
    versions = s.fetchone("SELECT COUNT(*) c FROM nodes WHERE id = 't:l0'")["c"]
    assert versions == 3, "non-vacuous: the node really has several versions"
    body = _client(s).get("/graph/neighborhood", params={"kind": kind, "include_closed": True,
                                                         "limit": 2}).json()
    ids = [n["id"] for n in body["nodes"]]
    assert len(ids) == len(set(ids)) == 2
    assert body["matched_total"] == 2 and body["left_out"] == 0 and body["truncated"] is False
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id["t:l0"]["label"] == "Alpha 3"                        # the newest version


# --- handed over (R-019 ui deferred #6): a pointerless attribute is withheld server-side ---------
def test_a_node_attribute_without_a_pointer_is_withheld_not_returned(tmp_path, bound):
    import json

    s = _build_store(str(tmp_path / "legacy.db"), nodes=[HUB, SPOKES[0]], edges=[(HUB, SPOKES[0])])
    # a legacy row: a value written with no pointer of its own (the constructor would refuse it)
    row = s.fetchone("SELECT rowid_pk, attrs_json FROM nodes WHERE id = ?", (HUB,))
    attrs = {**json.loads(row["attrs_json"]), "ungrounded_note": "said nowhere"}
    s.conn.execute("UPDATE nodes SET attrs_json = ?, canonical_id = ? WHERE rowid_pk = ?",
                   (json.dumps(attrs), "CID-1", row["rowid_pk"]))
    s.conn.commit()
    c = _client(s)
    body = c.get(f"/graph/node/{HUB}").json()
    assert "ungrounded_note" not in body["attrs"]
    assert body["canonical_id"] is None
    assert set(body["withheld_ungrounded"]) == {"ungrounded_note", "canonical_id"}
    grounded = [k for k, v in body["attrs"].items() if v is not None]
    assert grounded, "non-vacuous: grounded attributes are still returned"
    assert set(grounded) <= set(body["attribute_sources"])
    for listing in (c.get("/graph/neighborhood", params={"node": HUB}).json(),
                    c.get("/graph/search", params={"q": "hub"}).json()):
        (n,) = [n for n in listing["nodes"] if n["id"] == HUB]
        assert n["canonical_id"] is None and "canonical_id" in n["withheld_ungrounded"]


# --- Codex round 3 (Critical, via the coordinator): every emitted value is pointer-backed,
# --- withheld by name, or explicitly labelled structure-not-a-claim ------------------------------
def _classified(row: dict) -> None:
    backed = set(row["pointers"])
    for p in row["pointers"].values():
        backed |= set((p or {}).get("grounds", ()))
    structure = set(row["structure_not_a_claim"])
    withheld = set(row["withheld_ungrounded"])
    meta = set(graph_routes.PROVENANCE_KEYS)
    for key, value in row.items():
        if value is None:
            continue                  # an absent value carries no claim (e.g. no canonical id)
        assert key in backed | structure | withheld | meta, (key, row["id"])
    assert not (backed & structure), "a value is either a claim or structure, never both"


def test_every_value_a_list_row_emits_is_backed_withheld_or_structure(client):
    bodies = [client.get("/graph/neighborhood", params={"node": HUB}).json(),
              client.get("/graph/search", params={"q": "spoke"}).json(),
              client.get("/graph/path", params={"from": SPOKES[0], "to": SPOKES[1]}).json()]
    rows = [r for b in bodies for r in [*b["nodes"], *b.get("edges", [])]]
    assert any("src_id" in r for r in rows) and any("label" in r for r in rows)
    for row in rows:
        _classified(row)


def test_detail_routes_label_their_structure(client):
    node = client.get(f"/graph/node/{HUB}").json()
    assert {"id", "kind"} <= set(node["structure_not_a_claim"])
    for e in node["edges"]["outgoing"] + node["edges"]["incoming"]:
        assert set(e["pointers"]["edge"]["grounds"]) >= {"kind"}
    edge_id = node["edges"]["outgoing"][0]["id"]
    edge = client.get(f"/graph/edge/{edge_id}").json()
    assert "src_id" not in edge["structure_not_a_claim"] and "id" in edge["structure_not_a_claim"]


# --- Coordinator addition (R-019): a STRUCTURAL value shown as a fact needs a pointer too ---------
# A legacy store can hold a row whose entity-level pointer is gone (the constructors refuse this
# today; the table predates them). Its label, and an edge's src/kind/dst, are then claims with
# nothing behind them — they must be withheld by name, never shown as facts.
def _strip_entity_pointers(s, entity_id: str) -> None:
    s.conn.execute("DELETE FROM sources WHERE entity_id = ? AND field IS NULL", (entity_id,))
    s.conn.commit()


def _pointerless(tmp_path):
    s = _build_store(str(tmp_path / "bare.db"), nodes=[HUB, *SPOKES[:2]],
                     edges=[(HUB, SPOKES[0]), (HUB, SPOKES[1])])
    bare_edge = s.fetchone("SELECT id FROM edges WHERE dst_id = ?", (SPOKES[0],))["id"]
    kept_edge = s.fetchone("SELECT id FROM edges WHERE dst_id = ?", (SPOKES[1],))["id"]
    for eid in (bare_edge, SPOKES[0]):
        _strip_entity_pointers(s, eid)
    return s, bare_edge, kept_edge


def test_an_edge_without_a_pointer_is_withheld_from_every_listing(tmp_path, bound):
    s, bare_edge, kept_edge = _pointerless(tmp_path)
    c = _client(s)
    bodies = [c.get("/graph/neighborhood", params={"node": HUB}).json(),
              c.get("/graph/neighborhood").json(),
              c.get("/graph/path", params={"from": SPOKES[0], "to": SPOKES[1]}).json()]
    for b in bodies:
        edge_ids = {e["id"] for e in b["edges"]}
        assert bare_edge not in edge_ids, b.get("mode")
        assert b["edges_withheld_ungrounded"] >= 1
        for row in [*b["nodes"], *b["edges"]]:
            _classified(row)
    seeded = bodies[0]
    assert kept_edge in {e["id"] for e in seeded["edges"]}, "non-vacuous: grounded edges stay"
    assert seeded["edges_returned"] == len(seeded["edges"])
    node = c.get(f"/graph/node/{HUB}").json()
    shown = {e["id"] for e in node["edges"]["outgoing"] + node["edges"]["incoming"]}
    assert bare_edge not in shown and kept_edge in shown
    assert node["edges"]["withheld_ungrounded"] == 1


def test_edge_detail_without_a_pointer_withholds_its_endpoints_and_kind(tmp_path, bound):
    s, bare_edge, kept_edge = _pointerless(tmp_path)
    c = _client(s)
    bare = c.get(f"/graph/edge/{bare_edge}").json()
    for f in graph_routes.EDGE_POINTER_GROUNDS:
        assert bare[f] is None and f in bare["withheld_ungrounded"], f
    kept = c.get(f"/graph/edge/{kept_edge}").json()
    assert kept["src_id"] == HUB and not (set(graph_routes.EDGE_POINTER_GROUNDS)
                                          & set(kept["withheld_ungrounded"]))


def test_node_detail_withholds_a_label_nothing_grounds(tmp_path, bound):
    s, _bare_edge, _kept = _pointerless(tmp_path)
    c = _client(s)
    bare = c.get(f"/graph/node/{SPOKES[0]}").json()
    assert bare["label"] is None and "label" in bare["withheld_ungrounded"]
    assert bare["versions"], "non-vacuous"
    for v in bare["versions"]:
        assert v["label"] is None and "label" in v["withheld_ungrounded"]
    kept = c.get(f"/graph/node/{SPOKES[1]}").json()
    assert kept["label"] is not None and "label" not in kept["withheld_ungrounded"]
    # the list row for the same node agrees
    row = [n for n in c.get("/graph/neighborhood", params={"node": HUB}).json()["nodes"]
           if n["id"] == SPOKES[0]]
    assert row and row[0]["label"] is None and "label" in row[0]["withheld_ungrounded"]


def test_facets_mark_their_kinds_and_counts_as_structure(client):
    body = client.get("/graph/facets").json()
    marked = set(body["structure_not_a_claim"])
    values = {k for k, v in body.items() if k not in ("graph_source", "structure_not_a_claim")}
    assert values and values <= marked
