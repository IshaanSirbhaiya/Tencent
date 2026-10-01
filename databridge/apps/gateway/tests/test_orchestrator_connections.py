"""`/orchestrate` on the DEFAULT fleet — the sources this deployment actually connected.

Built from a temp registry of fake connections (no MCP server is started: a question reads what
ingestion already captured — the shared graph and the passage index — and never calls a tool),
a temp ingest ledger, an in-memory graph store bound to the `workspace` pack, and an injected
passage search. Every expectation below is derived from those fixtures, not written down.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
for _p in ("packages/core", "packages/graph", "packages/mcp_ingest", "apps/mcp_server"):
    _path = str(DATABRIDGE_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from databridge_gateway.orchestrator_fleet import ConnectionsFleet, fleet_for  # noqa: E402
from databridge_gateway.orchestrator_routes import register_orchestrator_routes  # noqa: E402
from databridge_gateway.trace_routes import register_trace_routes  # noqa: E402

from conftest import auth_headers  # noqa: E402

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
INGESTED, NEVER_INGESTED = "alpha-files", "beta-db"


@pytest.fixture()
def workspace():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    from databridge_mcp_ingest import register_connection

    sources, ingest = tmp_path / "sources", tmp_path / "ingest"
    for cid in (INGESTED, NEVER_INGESTED):
        register_connection({"id": cid, "title": f"Fake {cid}",
                             "transport": {"type": "stdio", "command": "never-run"}},
                            root=sources)
    run = ingest / INGESTED / "run-0001"
    run.mkdir(parents=True)
    (run / "report.json").write_text('{"status": "completed"}', encoding="utf-8")
    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(sources))
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(ingest))
    monkeypatch.setenv("WALNUT_AGENT_PURPOSE", "workspace.agent_run")
    monkeypatch.delenv("WALNUT_FLEET", raising=False)
    # R-019 round 2, finding 10: reading connected sources needs the operator's grant. The test
    # identities are granted every connection; the refusal tests narrow or remove it.
    monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps({"staff": "*", "analyst-7": "*"}))
    return sources


def _ptr(source_id: str, start: int = 0, end: int = 5):
    from databridge_core import SourcePointer

    return SourcePointer(source_id=source_id, kind="doc_span", document_id="d1",
                         char_start=start, char_end=end, capture_digest="ab" * 32)


@pytest.fixture()
def store(workspace):
    from databridge_graph import GraphNode, GraphStore
    from databridge_graph.kinds import NodeKind

    s = GraphStore()
    org_kind = next(k for k in workspace.node_kinds
                    if not workspace.is_subject(k) and workspace.node_kinds[k].binds_to.value
                    == "organisation")
    s.add_node(GraphNode(id="org:quillfeather", kind=NodeKind(org_kind),
                         label="Quillfeather Logistics", attrs={"domain": "quillfeather.test"},
                         attr_sources={"domain": _ptr(INGESTED, 6, 23)},
                         sources=[_ptr(INGESTED)], valid_from=T0, system_from=T0))
    # BM25 needs a corpus: with one node every term is in every document and no IDF clears the
    # linker's floor. Unrelated neighbours make the store a realistic (tiny) one.
    for i, name in enumerate(("Marrowgate Analytics", "Tessellate Foundry", "Orbweaver Studio")):
        s.add_node(GraphNode(id=f"org:other{i}", kind=NodeKind(org_kind), label=name,
                             sources=[_ptr(INGESTED)], valid_from=T0, system_from=T0))
    return s


class _Passage:
    def __init__(self, text: str, source_id: str):
        self.chunk_id, self.doc_id, self.source_id = "c1", "d1", source_id
        self.text, self.locator, self.approx_locator, self.score = text, "page 2", True, 0.5
        self.pointer = _ptr(source_id, 0, len(text))


def _fake_search(passages):
    calls = []

    def search(query, *, k=8, sources=None, kinds=None, mode="hybrid"):
        calls.append({"query": query, "sources": sources})
        return [p for p in passages if sources is None or p.source_id in sources]
    search.calls = calls
    return search


@pytest.fixture()
def app(registry, store):
    a = FastAPI()
    register_orchestrator_routes(a)
    register_trace_routes(a)
    a.state.graph_store = store
    a.state.passage_search = _fake_search(
        [_Passage("Quillfeather Logistics pays on net-30 terms.", INGESTED)])
    return a


@pytest.fixture()
def client(app):
    with TestClient(app, headers=auth_headers("staff")) as c:
        yield c


def test_the_default_fleet_is_built_from_the_registered_connections(client, app, registry):
    from databridge_mcp_ingest import list_connections

    assert isinstance(fleet_for(app), ConnectionsFleet)
    body = client.get("/orchestrate/fleet").json()
    expected = {c.id for c in list_connections(registry)}
    assert expected, "the temp registry is empty — the assertion below would be vacuous"
    assert {s["source_id"] for s in body["sources"]} == expected
    ingested = {s["source_id"]: s["ingested"] for s in body["sources"]}
    assert ingested == {INGESTED: True, NEVER_INGESTED: False}


def test_the_principal_is_the_signed_in_identity_and_nobody_else(client):
    for who in ("staff", "analyst-7"):
        body = client.get("/orchestrate/fleet", headers=auth_headers(who)).json()
        assert body["principal"]["id"] == who
        assert set(body["principals"]) == {who}, "the directory is never offered as a menu"
        assert body["can_ask"] is True


def test_asking_cites_graph_values_and_passages_with_pointers(client):
    r = client.post("/orchestrate/ask", json={"question": "What are Quillfeather's terms?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["principal"]["on_behalf_of"] == "staff"
    assert body["principal"]["purpose"] == "workspace.agent_run"
    by_source = {o["source_id"]: o for o in body["sources"]}
    cells = by_source[INGESTED]["cells"]
    assert by_source[INGESTED]["status"] == "answered" and cells
    for c in cells:
        assert c["state"] == "grounded" and c["pointer"]["source_id"] == INGESTED
    # both channels reached the answer: a graph value and a passage
    assert any(c["value"] == "quillfeather.test" for c in cells), body["coverage"]
    assert any("net-30" in str(c["value"]) for c in cells)
    assert body["trace_id"]


def test_a_source_never_ingested_is_reported_as_reduced_coverage_not_dropped(client):
    body = client.post("/orchestrate/ask", json={"question": "Quillfeather terms"}).json()
    by_source = {o["source_id"]: o for o in body["sources"]}
    assert NEVER_INGESTED in by_source, "an unsearched source must not silently disappear"
    assert by_source[NEVER_INGESTED]["status"] == "escalated"
    assert "reduced coverage" in by_source[NEVER_INGESTED]["message"]
    assert NEVER_INGESTED in body["coverage"]["not_searched"]


def test_retrieval_is_scoped_to_the_registered_connections(client, app, registry):
    from databridge_mcp_ingest import list_connections

    client.post("/orchestrate/ask", json={"question": "Quillfeather"})
    calls = app.state.passage_search.calls
    assert calls and set(calls[-1]["sources"]) == {c.id for c in list_connections(registry)}


def test_the_body_still_cannot_carry_an_identity(client):
    r = client.post("/orchestrate/ask",
                    json={"question": "x", "on_behalf_of": "fcc-3"})
    assert r.status_code == 422


def test_no_sources_and_an_empty_graph_disables_asking(tmp_path, monkeypatch, workspace):
    from databridge_graph import GraphStore

    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(tmp_path / "none"))
    monkeypatch.delenv("WALNUT_FLEET", raising=False)
    a = FastAPI()
    register_orchestrator_routes(a)
    a.state.graph_store = GraphStore()
    with TestClient(a, headers=auth_headers("staff")) as c:
        body = c.get("/orchestrate/fleet").json()
    assert body["sources"] == [] and body["can_ask"] is False
    assert body["ask_disabled_reason"]


def test_suggested_questions_come_from_the_bound_packs_not_this_module(client, workspace):
    import yaml

    from databridge_graph.packs import packs_root

    declared: list[str] = []
    for name in workspace.packs:
        data = yaml.safe_load((packs_root() / name / "ui.yaml").read_text(encoding="utf-8"))
        trials = data.get("trials") or {}
        declared += [q if isinstance(q, str) else q.get("question")
                     for q in (trials.get("orchestrate") or [])]
        if data.get("demo_question"):
            declared.append(data["demo_question"])
    body = client.get("/orchestrate/fleet").json()
    assert body["suggested_questions"] == [q for q in declared if q][:6]


# ---------------------------------------------------------------------------------------------
# R-019 (Codex batch 2): coverage honesty, the trace, and the stated access model
# ---------------------------------------------------------------------------------------------
def test_no_channel_searched_is_reduced_coverage_never_nothing_matched(registry, workspace):
    """Finding 6: with no graph store attached and the passage index unavailable, an INGESTED
    source used to come back `answered` / "searched; nothing in this source matched" — an
    infrastructure absence rendered as a clean miss."""
    a = FastAPI()
    register_orchestrator_routes(a)

    def down(query, **_kw):
        raise ConnectionError("index offline")
    a.state.passage_search = down                 # no graph_store attached at all
    with TestClient(a, headers=auth_headers("staff")) as c:
        body = c.post("/orchestrate/ask", json={"question": "Quillfeather terms"}).json()
    assert body["coverage"]["graph_searched"] is False
    assert body["coverage"]["passages_searched"] is False
    by_source = {o["source_id"]: o for o in body["sources"]}
    assert INGESTED in by_source, "the ingested source must still be reported"
    assert by_source[INGESTED]["status"] == "escalated", by_source[INGESTED]
    assert "nothing in this source matched" not in by_source[INGESTED]["message"]
    assert INGESTED in body["coverage"]["not_searched"]
    assert body["outcome"] == "escalated"


def test_one_channel_down_is_named_even_when_the_other_found_nothing(registry, workspace):
    """Finding 6, the partial case: the graph searched and found nothing for this source, the
    passage index was down — "nothing matched" must say which half never ran."""
    from databridge_graph import GraphStore

    a = FastAPI()
    register_orchestrator_routes(a)
    a.state.graph_store = GraphStore()

    def down(query, **_kw):
        raise ConnectionError("index offline")
    a.state.passage_search = down
    with TestClient(a, headers=auth_headers("staff")) as c:
        body = c.post("/orchestrate/ask", json={"question": "Quillfeather terms"}).json()
    ingested = {o["source_id"]: o for o in body["sources"]}[INGESTED]
    assert body["coverage"]["channels_not_searched"] == ["the passage index"]
    assert "the passage index" in ingested["message"]
    assert "reduced coverage" in ingested["message"]


def test_every_emitted_cell_has_its_pointer_in_the_trace(client):
    """Finding 12: only the entity-label pointers were recorded; attribute values and passages
    reached the response with no pointer step behind them."""
    body = client.post("/orchestrate/ask",
                       json={"question": "What are Quillfeather's terms?"}).json()
    emitted = [c for o in body["sources"] for c in o["cells"]]
    assert emitted, "no cell was emitted — the count comparison below would be vacuous"
    detail = client.get(f"/trace/{body['trace_id']}").json()
    pointer_steps = [s for s in detail["step_list"] if s["kind"] == "pointer"]
    assert len(pointer_steps) == len(emitted), (len(pointer_steps), len(emitted))
    kinds_emitted = sorted(str(c["pointer"].get("kind")) for c in emitted)
    kinds_traced = sorted(str((s.get("detail") or s).get("kind")) for s in pointer_steps)
    assert kinds_traced == kinds_emitted


def test_the_answer_states_its_access_model(client):
    """Finding 7, round 2 finding 10: the access model is stated in every answer — and it is now
    a per-principal connection grant, not "deployment-wide"."""
    from databridge_gateway.orchestrator_fleet import ACCESS_MODEL

    body = client.post("/orchestrate/ask", json={"question": "Quillfeather"}).json()
    assert body["coverage"]["access"] == ACCESS_MODEL
    assert "deployment-wide" not in ACCESS_MODEL and "grant" in ACCESS_MODEL


# ---------------------------------------------------------------------------------------------
# R-019 ROUND 2 (Codex gpt-5.6-sol)
# ---------------------------------------------------------------------------------------------
def _down(query, **_kw):
    raise ConnectionError("index offline at db.internal.example password=hunter2")


def test_cells_from_the_graph_alone_never_claim_the_passage_index(registry, store, workspace):
    """Round 2 finding 9: with graph cells found and the passage index down, the source said
    "from the graph and the passage index" and the note said both channels were searched."""
    a = FastAPI()
    register_orchestrator_routes(a)
    a.state.graph_store = store
    a.state.passage_search = _down
    with TestClient(a, headers=auth_headers("staff")) as c:
        body = c.post("/orchestrate/ask", json={"question": "Quillfeather terms"}).json()
    ingested = {o["source_id"]: o for o in body["sources"]}[INGESTED]
    assert ingested["cells"], "premise: the graph found something"
    assert {c["channel"] for c in ingested["cells"]} == {"the knowledge graph"}
    assert "from the knowledge graph" in ingested["message"]
    assert "and the passage index" not in ingested["message"].split(";")[0]
    assert "partial answer" in ingested["message"]
    assert INGESTED in body["coverage"]["partial"]
    assert "searched the knowledge graph over" in body["selection_note"]
    assert "searched the knowledge graph and the passage index" not in body["selection_note"]
    assert ingested["capability"] == "graph.search"


def test_a_source_the_passage_index_skipped_is_partial_even_with_graph_cells(registry, store,
                                                                            workspace):
    def skipping(query, *, k=8, sources=None, kinds=None, mode="hybrid"):
        return []

    a = FastAPI()
    register_orchestrator_routes(a)
    a.state.graph_store = store
    a.state.passage_search = skipping
    import brain_server

    real = brain_server.search_passages

    def search_passages(question, *, search=None, k=8, sources=None):
        return [], {"available": True, "coverage": {"skipped": [
            {"source_id": INGESTED, "reason": "index not built for this source"}]}}
    brain_server.search_passages = search_passages
    try:
        with TestClient(a, headers=auth_headers("staff")) as c:
            body = c.post("/orchestrate/ask", json={"question": "Quillfeather terms"}).json()
    finally:
        brain_server.search_passages = real
    ingested = {o["source_id"]: o for o in body["sources"]}[INGESTED]
    assert ingested["cells"] and "partial answer" in ingested["message"], ingested
    assert "index not built" in ingested["message"]


@pytest.mark.parametrize("grants", [None, {"somebody-else": "*"}])
def test_an_authenticated_identity_without_a_grant_reads_nothing(app, monkeypatch, grants):
    """Round 2 finding 10: `principal_id` was only recorded; every signed-in identity read the
    whole shared graph and passage index."""
    if grants is None:
        monkeypatch.delenv("WALNUT_FEDERATED_GRANTS", raising=False)
    else:
        monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps(grants))
    with TestClient(app, headers=auth_headers("staff")) as c:
        r = c.post("/orchestrate/ask", json={"question": "What are Quillfeather's terms?"})
        status = c.get("/orchestrate/fleet").json()
    assert r.status_code == 403, r.text
    assert r.json()["refusal"]["code"] == "NOT_GRANTED"
    assert "quillfeather.test" not in r.text and "net-30" not in r.text
    assert status["can_ask"] is False and status["sources"] == [] and status["graph"] is None
    assert app.state.passage_search.calls == [], "nothing is searched for an ungranted caller"


def test_a_narrowed_grant_emits_only_the_granted_sources(app, monkeypatch):
    monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps({"staff": [NEVER_INGESTED]}))
    with TestClient(app, headers=auth_headers("staff")) as c:
        body = c.post("/orchestrate/ask",
                      json={"question": "What are Quillfeather's terms?"}).json()
        status = c.get("/orchestrate/fleet").json()
    assert {o["source_id"] for o in body["sources"]} == {NEVER_INGESTED}
    assert not any(o["cells"] for o in body["sources"])
    assert "quillfeather.test" not in json.dumps(body) and "net-30" not in json.dumps(body)
    assert body["coverage"]["withheld_outside_grant"] > 0
    assert set(app.state.passage_search.calls[-1]["sources"]) == {NEVER_INGESTED}
    assert {s["source_id"] for s in status["sources"]} == {NEVER_INGESTED}


def test_a_graph_failure_reaches_the_caller_as_a_code_and_a_reference(registry, workspace):
    """Round 2 finding 13 (orchestrator half): the exception's type name was the caller's
    `graph_error`; now a stable code and a reference, the detail in the server log."""
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("sqlite:///C:/secret/path.db is locked password=hunter2")

    a = FastAPI()
    register_orchestrator_routes(a)
    a.state.graph_store = Broken()
    a.state.passage_search = _down
    with TestClient(a, headers=auth_headers("staff")) as c:
        r = c.post("/orchestrate/ask", json={"question": "Quillfeather terms"})
    assert r.status_code == 200, r.text
    assert "hunter2" not in r.text and "RuntimeError" not in r.text
    err = r.json()["coverage"]["graph_error"]
    assert err["code"] == "GRAPH_SEARCH_FAILED" and err["ref"].startswith("ERR-")


def test_an_unknown_fleet_name_never_selects_a_fixture(registry, monkeypatch):
    """Round 2 finding 4: the fixture is selected by a declaration's `mode`, read from its YAML;
    a name no declaration carries is the default connections fleet."""
    from databridge_gateway.orchestrator_fleet import CONNECTIONS_MODE, fleet_mode
    from databridge_gateway.verify_fleet import fixture_fleet_decls

    declared = {d.mode for d in fixture_fleet_decls() if d.orchestrate_adapters}
    assert declared, "no declaration serves /orchestrate — the check below would be vacuous"
    monkeypatch.setenv("WALNUT_FLEET", "no-such-estate-fixture")
    assert fleet_mode() == CONNECTIONS_MODE
    for mode in declared:
        monkeypatch.setenv("WALNUT_FLEET", mode)
        assert fleet_mode() == mode
