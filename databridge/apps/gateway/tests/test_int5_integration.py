"""INT5 — final integration pass before end-to-end run 3 (gateway side).

Item 2 (AA3 cross-track 1): the gateway hands the adapter-agent manager the AUTHENTICATED principal
OBJECT, not its id string. A name records who acted but seals no delegation, so before this an
`auto_additive` agent activated from the app held every additive change for a person.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from conftest import auth_headers

from test_platform_routes import FakeService, _aa_spec, _mount, env  # noqa: F401

OWNER = "analyst-7"


class _ScopedService(FakeService):
    """The fake ingest service plus the connection declaration a delegation's scope binds to."""

    def read_config(self, source_id, kind):
        return {"id": source_id, "scope": {"roots": [f"/data/{source_id}"]}}


def _mount_scoped(env):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry

    app, rt, extra = _mount(env)
    rt.manager = AdapterAgentManager(service=_ScopedService([]), registry=AdapterAgentRegistry(),
                                     store=app.state.graph_store)
    return app, rt


def _seal_of(rt, agent_id):
    return rt.manager.delegation_status(rt.manager.get(agent_id))


def test_int5_2_creating_an_active_agent_from_the_app_seals_a_delegation(env):
    app, rt = _mount_scoped(env)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        r = c.post("/adapter-agents",
                   json={"spec": _aa_spec("sealed_a", ["files-a"],
                                          adapt={"policy": "auto_additive"})})
        assert r.status_code == 200, r.text
    seal, why = _seal_of(rt, "sealed_a")
    assert seal is not None, why
    assert seal["principal_id"] == OWNER and seal["authenticated_by"] == "gateway"


def test_int5_2_pause_then_resume_from_the_app_reseals(env):
    from databridge_workflow_spec import load_spec

    app, rt = _mount_scoped(env)
    # An agent activated BEFORE AA3 (a typed name): it holds no seal, so only the app's resume can
    # mint one — the route under test.
    rt.manager.create(load_spec(_aa_spec("sealed_b", ["files-b"],
                                         adapt={"policy": "auto_additive"})), by=OWNER)
    assert _seal_of(rt, "sealed_b")[0] is None                       # non-vacuous
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        assert c.post("/adapter-agents/sealed_b/pause").status_code == 200
        assert _seal_of(rt, "sealed_b")[0] is None                 # paused: nothing in force
        assert c.post("/adapter-agents/sealed_b/resume").status_code == 200
    seal, why = _seal_of(rt, "sealed_b")
    assert seal is not None and seal["principal_id"] == OWNER, why


def test_int5_2_a_draft_activated_from_the_app_seals_and_an_update_revokes(env):
    app, rt = _mount_scoped(env)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        assert c.post("/adapter-agents", json={"spec": _aa_spec(
            "sealed_c", ["files-c"], adapt={"policy": "auto_additive"},
            status="draft")}).status_code == 200
        assert _seal_of(rt, "sealed_c")[0] is None
        r = c.post("/adapter-agents/sealed_c/activate")
        assert r.status_code == 200, r.text
        assert _seal_of(rt, "sealed_c")[0] is not None
        r = c.patch("/adapter-agents/sealed_c", json={"connections": ["files-c", "files-d"]})
        assert r.status_code == 200, r.text
    seal, why = _seal_of(rt, "sealed_c")
    assert seal is None and why                                     # scope widened: revoked


# --- item 4: platform_runtime registration (FXJ3, R-022 sources cross-track 1 + 6) -----------------
def test_int5_4_http_registration_is_remote_and_leaves_the_advert_to_the_route(env, monkeypatch):
    import databridge_mcp_ingest as mi

    from databridge_gateway.platform_runtime import IngestFacade

    seen: list[dict] = []
    monkeypatch.setattr(mi, "register_connection",
                        lambda spec, **kw: seen.append(kw) or SimpleNamespace(id=spec["id"]))
    monkeypatch.setattr(mi, "import_declarations",
                        lambda path, **kw: seen.append(kw) or [])
    f = IngestFacade()
    f.register_connection({"id": "x"}, overwrite=True)
    f.import_declarations(None)
    assert seen[0]["origin"] == "remote" and seen[0]["advertise"] is False
    assert seen[0]["overwrite"] is True
    assert seen[1]["advertise"] is False and "origin" not in seen[1]   # the operator's manifest


def test_int5_4_get_sources_shows_a_refused_stored_connection_with_its_reason(env, monkeypatch):
    import yaml
    from databridge_mcp_ingest.connections import connection_path, connection_problems
    from databridge_mcp_ingest.secret_scan import DEV_ALLOW_DEFAULTS_ENV

    from databridge_gateway.platform_runtime import IngestFacade

    app, rt, _ = _mount(env)
    rt.ingest = IngestFacade()                         # the real listing and problem report
    monkeypatch.delenv(DEV_ALLOW_DEFAULTS_ENV, raising=False)
    bad = connection_path("leaky")             # a defaulted credential, stored before FXJ3 (N-10)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(yaml.safe_dump({"id": "leaky", "transport": {
        "type": "stdio", "command": "npx", "env": {
            "DATABASE_URI": "postgresql://r:${PG_PW:-sk-live-abcdef0123456789}@h/db"}}}),
        encoding="utf-8")

    expected = connection_problems()
    assert [p["id"] for p in expected] == ["leaky"]                      # non-vacuous
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        r = c.get("/sources")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "leaky" not in [s.get("id") for s in body["sources"]]
    shown = [p for p in body["problems"] if "leaky" in str(p)]
    assert shown and expected[0]["error"][:60] in str(shown[0])
    assert "sk-live-abcdef0123456789" not in r.text                      # never the value


# --- item 8: the /brain router has its own task class (BRAIN cross-track) ------------------------
def test_int5_8_the_brain_router_routes_through_its_own_task_class_on_the_brain_alias():
    from databridge_core.config import load_model_config

    from databridge_gateway import brain_delegation as bd

    routing = load_model_config()["routing"]
    assert bd.ROUTER_TASK_CLASS == "brain_routing"
    assert bd.ROUTER_TASK_CLASS in routing
    # the same alias as the answer, so the reachable-alternate remap (llm._brain_route) covers it
    assert routing[bd.ROUTER_TASK_CLASS] == routing["brain_query"]
