"""``/graph`` — standalone tests over the real clinic graph seed.

Same shape as ``test_evidence_routes.py`` / ``test_impact_route.py``: its own tiny FastAPI app,
built over ``fresh_clinic_store()`` so assertions exercise real data rather than a mock. The clinic
seed is 358 nodes / 111 edges — small enough to assert exact shapes, and (via the low `limit=`
knobs below) enough to exercise the truncation/"say what was left out" behaviour that matters at
estate scale without building a 46k-node graph in a unit test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.graph_routes import register_graph_routes
from databridge_graph.clinic_seed import SEED_SYSTEM_TIME, fresh_clinic_store
from databridge_graph.packs import deployment

from conftest import AUTH_HEADERS

DOCUMENT_ID = "document:chit_01_meridian_referral.txt"
UNRESOLVED_NODE_ID = "coverage_rule:rule-meridian-referral"


@pytest.fixture(scope="module")
def store():
    with deployment("healthcare"):
        return fresh_clinic_store()


@pytest.fixture()
def client(store, monkeypatch):
    monkeypatch.delenv("WALNUT_GRAPH_DB_PATH", raising=False)
    application = FastAPI()
    application.state.brain = SimpleNamespace(store=store)
    register_graph_routes(application)
    # R-015: every route here now requires a credential.
    return TestClient(application, headers=AUTH_HEADERS)


# --- facets -----------------------------------------------------------------------------------


def test_facets_report_real_counts_not_a_hardcoded_list(client, store):
    body = client.get("/graph/facets").json()
    # An in-memory store attached as `app.state.brain.store` (no `graph_store` on this test app).
    assert body["graph_source"] == "in-memory (app.state.brain.store)"
    counts = store.counts()
    assert body["counts"] == counts
    kinds = {row["kind"] for row in body["node_kinds"]}
    expected = {r["kind"] for r in store.fetchall(
        "SELECT DISTINCT kind FROM nodes WHERE system_to IS NULL")}
    assert expected, "the seed must hold at least one kind for this test to mean anything"
    assert kinds == expected
    for row in body["node_kinds"]:
        assert row["count"] > 0
        assert row["incident_edges"] >= 0
    assert body["sources"]["total_distinct"] > 0
    assert body["sources"]["returned"] == len(body["sources"]["items"])
    # every declared field is present, whether or not this store happens to trip it
    assert "truncated" in body["sources"] and "left_out" in body["sources"]
    assert body["reverse_index_coverage"]["answer"] in ("known", "unknown")


def test_facets_never_hardcodes_a_kind_the_store_does_not_hold(client):
    """A kind absent from the clinic seed (a government-pack kind, say) must not appear — the
    whole point of reading facets off the store rather than a fixed vocabulary list."""
    body = client.get("/graph/facets").json()
    kinds = {row["kind"] for row in body["node_kinds"]}
    assert "citizen" not in kinds and "voter_registration" not in kinds


# --- neighborhood: browse mode ------------------------------------------------------------------


def test_browse_by_kind_is_windowed_and_says_what_was_left_out(client):
    body = client.get("/graph/neighborhood", params={"kind": "document", "limit": 3}).json()
    assert body["returned"] == 3
    assert body["matched_total"] is not None and body["matched_total"] > 3
    assert body["truncated"] is True
    assert body["left_out"] == body["matched_total"] - 3
    assert all(n["kind"] == "document" for n in body["nodes"])


def test_browse_with_no_filter_still_windows(client, store):
    body = client.get("/graph/neighborhood", params={"limit": 5}).json()
    assert 0 < body["returned"] <= 5
    assert body["mode"] == "hubs"
    assert body["matched_total"] == store.counts()["nodes"]
    assert body["truncated"] is True
    assert body["omitted"]["nodes_not_drawn"] == body["matched_total"] - body["returned"]


def test_browse_by_label_search(client):
    body = client.get("/graph/neighborhood",
                      params={"kind": "document", "q": "chit_01", "limit": 10}).json()
    ids = [n["id"] for n in body["nodes"]]
    assert DOCUMENT_ID in ids
    assert all("chit_01" in n["id"].lower() or "chit_01" in n["label"].lower()
              for n in body["nodes"])


def test_an_untruncated_browse_reports_an_exact_total(client):
    body = client.get("/graph/neighborhood", params={"kind": "clinic", "limit": 50}).json()
    assert body["truncated"] is False
    assert body["matched_total"] == body["returned"]
    assert body["left_out"] == 0


def test_browse_includes_edges_among_the_returned_nodes(client):
    # No filter: the default view is seeded from the best-connected nodes, so it has edges by
    # construction — and every node drawn touches at least one of them.
    body = client.get("/graph/neighborhood", params={"limit": 60}).json()
    node_ids = {n["id"] for n in body["nodes"]}
    assert body["edges"], "a 60-node window of the seed must include at least one edge"
    for e in body["edges"]:
        assert e["src_id"] in node_ids and e["dst_id"] in node_ids
    touched = {e["src_id"] for e in body["edges"]} | {e["dst_id"] for e in body["edges"]}
    assert node_ids <= touched, "the default browse must never draw an isolated node"


def test_browse_by_single_kind_omits_cross_kind_edges(client):
    # coverage_rule --authorises--> package: filtering to ONLY coverage_rule nodes must not draw
    # an edge to a package node that is not itself in the window.
    body = client.get("/graph/neighborhood", params={"kind": "coverage_rule", "limit": 50}).json()
    node_ids = {n["id"] for n in body["nodes"]}
    assert all(k == "coverage_rule" for k in {n["kind"] for n in body["nodes"]})
    for e in body["edges"]:
        assert e["src_id"] in node_ids and e["dst_id"] in node_ids


# --- neighborhood: seed / BFS mode --------------------------------------------------------------


def test_seed_expansion_returns_a_connected_neighborhood(client):
    body = client.get("/graph/neighborhood",
                      params={"node": DOCUMENT_ID, "depth": 1, "limit": 60}).json()
    assert body["seed"] == DOCUMENT_ID
    assert body["depth"] == 1
    ids = {n["id"] for n in body["nodes"]}
    assert DOCUMENT_ID in ids
    assert body["truncated"] is False
    assert body["matched_total"] == body["returned"]
    assert body["note"] is None


def test_seed_expansion_unknown_node_is_refused(client):
    resp = client.get("/graph/neighborhood", params={"node": "patient:does-not-exist"})
    body = resp.json()
    assert resp.status_code == 404
    assert body["refusal"]["code"] == "UNKNOWN_NODE"


def test_seed_expansion_truncation_reports_unknown_total_honestly(client):
    """A BFS that stops at its node cap has not counted what lies beyond the frontier — the
    response must say `matched_total: null`, never a number it cannot back up."""
    body = client.get("/graph/neighborhood",
                      params={"node": DOCUMENT_ID, "depth": 2, "limit": 2}).json()
    assert body["returned"] == 2
    assert body["truncated"] is True
    assert body["matched_total"] is None
    assert body["left_out"] is None
    assert body["note"] is not None


# --- neighborhood: source filter (reverse index) -------------------------------------------------


def test_source_filter_uses_the_reverse_index(client, store):
    row = store.fetchone("SELECT source_id FROM ptr_index WHERE source_id LIKE ? LIMIT 1",
                         (f"%{DOCUMENT_ID.split(':', 1)[1]}%",))
    assert row is not None, "the document's own text must be indexed as a source in this store"
    body = client.get("/graph/neighborhood",
                      params={"source": row["source_id"], "limit": 60}).json()
    assert body["coverage"] is not None
    assert body["coverage"]["answer"] in ("known", "unknown")
    assert body["returned"] >= 1


def test_source_filter_unknown_source_returns_empty_not_an_error(client):
    body = client.get("/graph/neighborhood",
                      params={"source": "no-such-source.csv", "limit": 10}).json()
    assert body["nodes"] == []
    assert body["returned"] == 0


# --- node provenance ------------------------------------------------------------------------------


def test_node_detail_carries_label_kind_attrs_and_pointers(client):
    body = client.get(f"/graph/node/{DOCUMENT_ID}").json()
    assert body["id"] == DOCUMENT_ID
    assert body["kind"] == "document"
    assert body["label"]
    assert body["attrs"]["document_type"] == "insurer_referral_letter"
    assert set(body["attribute_sources"].keys()) >= {"document_type"}
    for pointer in body["attribute_sources"].values():
        assert pointer["source_id"]
        assert pointer["kind"]
    assert body["sources"], "the entity itself must carry at least one grounding pointer"
    assert body["evidence_trace_url"] == f"/evidence?entity={DOCUMENT_ID}"
    assert body["versions"], "at least the current version must be listed"
    assert any(v["is_open"] for v in body["versions"])


def test_node_detail_names_unresolved_fields_rather_than_hiding_them(client):
    body = client.get(f"/graph/node/{UNRESOLVED_NODE_ID}").json()
    assert "package_code" in body["unresolved"]
    assert "package_code" not in body["attribute_sources"], (
        "an unresolved field must carry no pointer — a citation for nothing")


def test_node_detail_lists_incident_edges_bounded(client):
    body = client.get("/graph/node/clinic:meridian-panel-clinic-novena").json()
    assert body["edges"]["window"] > 0
    assert body["edges"]["returned"] <= body["edges"]["window"]
    assert body["edges"]["matched_total"] >= body["edges"]["returned"]


def test_unknown_node_is_404_not_a_500(client):
    resp = client.get("/graph/node/patient:does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["refusal"]["code"] == "UNKNOWN_NODE"


def test_edge_detail_round_trips_an_id_from_a_neighborhood_response(client):
    nb = client.get("/graph/neighborhood", params={"node": DOCUMENT_ID, "depth": 1}).json()
    assert nb["edges"], "the document seed has incident edges in the clinic fixture"
    edge_id = nb["edges"][0]["id"]
    body = client.get(f"/graph/edge/{edge_id}").json()
    assert body["id"] == edge_id
    assert body["src_id"] and body["dst_id"]
    assert body["evidence_trace_url"] == f"/evidence?entity={edge_id}"


def test_unknown_edge_is_404_not_a_500(client):
    resp = client.get("/graph/edge/does-not-exist|nope|also-not")
    assert resp.status_code == 404
    assert resp.json()["refusal"]["code"] == "UNKNOWN_EDGE"


# --- no store attached -------------------------------------------------------------------------


def test_no_store_attached_refuses_rather_than_500s():
    application = FastAPI()
    register_graph_routes(application)
    with TestClient(application, headers=AUTH_HEADERS) as c:
        resp = c.get("/graph/facets")
        assert resp.status_code == 503
        assert resp.json()["refusal"]["code"] == "GRAPH_UNAVAILABLE"


# --- R-015: no credential, no graph -------------------------------------------------------------


def test_every_route_here_refuses_an_unauthenticated_caller(client):
    """R-015. Every route on this surface used to answer with no credential at all
    (`phase5-gw-graph-trace.md` #1)."""
    bare = TestClient(client.app)
    for path in ("/graph/facets", "/graph/neighborhood",
                 f"/graph/node/{DOCUMENT_ID}", f"/graph/edge/does-not-matter",
                 f"/graph/path?from={DOCUMENT_ID}&to={DOCUMENT_ID}", "/graph/search?q=a"):
        resp = bare.get(path)
        assert resp.status_code == 401, path
        assert resp.json()["refusal"]["code"] == "UNAUTHENTICATED", path
    resp = bare.post("/graph/reload")
    assert resp.status_code == 401
    assert resp.json()["refusal"]["code"] == "UNAUTHENTICATED"


# --- a store from a DIFFERENT pack than this process has bound ---------------------------------
#
# The shared workspace store can carry rows written under a pack binding this process does not
# currently hold — a government- or finance-pack graph, while the gateway's own deployment binding
# (`packs/deployment.yaml`) is something else. `row_to_node`/`row_to_edge`
# (`databridge_graph.store`) construct a `GraphNode`/`GraphEdge`, whose `kind` field is a
# `NodeKind`/`EdgeKind` validated against WHICHEVER pack is bound RIGHT NOW — not the pack that
# wrote the row. Using them here crashed with `UnknownKind` the moment a node's kind (`person`,
# government) was not in the bound vocabulary (`healthcare`). These tests pin the fix: the node
# and edge detail routes read the row directly and never construct either enum.


def _government_node_store():
    from databridge_core import SourcePointer
    from databridge_graph.merge import upsert_node
    from databridge_graph.schema import GraphNode
    from databridge_graph.store import GraphStore

    with deployment("government"):
        from databridge_graph.kinds import NodeKind

        ptr = SourcePointer(source_id="citizens.csv", kind="table_cell", table="citizens",
                            column="full_name", row_id="1")
        node = GraphNode(
            id="person:gov-test-1", kind=NodeKind("person"), label="A Government Person",
            attrs={"full_name": "A Government Person"},
            attr_sources={"full_name": ptr}, sources=(ptr,),
            valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME)
        store = GraphStore()
        upsert_node(store, node)
    return store


def test_node_detail_survives_a_kind_this_processs_binding_never_declared():
    """The regression: reading a government-pack node while the process is bound to healthcare
    alone must not raise `UnknownKind` — it must simply describe the row."""
    store = _government_node_store()
    application = FastAPI()
    application.state.brain = SimpleNamespace(store=store)
    register_graph_routes(application)
    with deployment("healthcare"), TestClient(application, headers=AUTH_HEADERS) as c:
        resp = c.get("/graph/node/person:gov-test-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "person"
        assert body["attrs"]["full_name"] == "A Government Person"
        assert "full_name" in body["attribute_sources"]


def test_facets_survive_a_kind_this_processs_binding_never_declared():
    store = _government_node_store()
    application = FastAPI()
    application.state.brain = SimpleNamespace(store=store)
    register_graph_routes(application)
    with deployment("healthcare"), TestClient(application, headers=AUTH_HEADERS) as c:
        body = c.get("/graph/facets").json()
        assert [(r["kind"], r["count"]) for r in body["node_kinds"]] == [("person", 1)]


def test_neighborhood_browse_survives_a_kind_this_processs_binding_never_declared():
    store = _government_node_store()
    application = FastAPI()
    application.state.brain = SimpleNamespace(store=store)
    register_graph_routes(application)
    with deployment("healthcare"), TestClient(application, headers=AUTH_HEADERS) as c:
        body = c.get("/graph/neighborhood", params={"kind": "person"}).json()
        assert body["returned"] == 1
        assert body["nodes"][0]["id"] == "person:gov-test-1"
