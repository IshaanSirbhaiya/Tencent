"""FIN-02 / FIN-16 / FIN-19 (finance dry run, 2026-10-02): approving a database plan from the
page, marking approvals made with the dev token, and serving a second frontend.

* **FIN-02.** A database plan's SQL step calls a generic executor (``execute_sql``) that nothing
  classifies: the catalogue reports it ``write: true, basis: "unknown"`` and the plan records
  ``unclassified:unknown``. The page offered no way to approve it; it now offers a per-tool
  "vouch" that sends ``vouch_read_tools``. These tests pin the CONTRACT the page relies on (the
  classifier's basis and the plan's class for such a tool) and drive the route with and without
  the vouch — a write is still never vouchable.
* **FIN-16.** An approval made with the public dev token was recorded ``dev_only: false,
  authenticated_by: "gateway"``. It is now ``dev_only: true, authenticated_by:
  "gateway:dev-token"``, and ``GET /auth/whoami`` says the same to the page.
* **FIN-19.** The browser origins the gateway admits come from ``WALNUT_CORS_ORIGINS``, default
  the :3000 pair; a wildcard or a path is never accepted.

Two estates throughout: the workspace estate's database source and the finance estate's core
banking source, as the connection ids their fixtures' manifests declare.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from databridge_gateway import auth

from conftest import auth_headers
from test_platform_routes import (_RealApproveIngest, _cmd, _mount, approve, bound_notes,  # noqa: F401
                                  env)

ROOT = Path(__file__).resolve().parents[3]                          # databridge/


def _estate_db_sources() -> dict[str, str]:
    """The database connection id each estate's own manifest declares — read, not written down."""
    out = {}
    ws = yaml.safe_load((ROOT / "infra" / "mcp_servers.yaml").read_text(encoding="utf-8"))
    fin = yaml.safe_load((ROOT / "infra" / "mcp_servers.finance.yaml").read_text(encoding="utf-8"))
    for estate, doc in (("workspace", ws), ("finance", fin)):
        ids = [srv["id"] for srv in doc.get("servers") or []
               if isinstance(srv, dict) and srv.get("source_class") == "database"
               and srv.get("purpose", "ingest") == "ingest"]
        assert ids, f"{estate}: no ingest database source in its manifest"
        out[estate] = sorted(ids)[0]
    return out


DB_SOURCES = _estate_db_sources()
#: The generic executor a postgres MCP server publishes — the tool the finance run had to vouch.
EXECUTOR = "execute_sql"


def _sql_plan(source_id: str, tool: str, monkeypatch, *, cls: str | None = None) -> str:
    """A proposed TABULAR plan whose SQL step calls ``tool``, classified by the REAL shared rule
    (`plans.classify_plan_tools`) against the live tool list the approval will see."""
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import CrawlPlan, SqlSpec, StepSpec, save_plan

    tools = [ToolInfo(name=tool, description="", input_schema={"type": "object"})]
    live = dict(getattr(plans._live_tools, "_int3_tools", {}))
    live[source_id] = tools

    def _fake_live(conn, client, _live=live):
        return list(_live[conn.id])
    _fake_live._int3_tools = live
    monkeypatch.setattr(plans, "_live_tools", _fake_live)
    steps = [StepSpec(id="db", strategy="tabular",
                      sql=SqlSpec(tool=tool, arg="sql", dialect="postgres"))]
    classes = plans.classify_plan_tools(steps, tools, [])
    save_plan(CrawlPlan(source_id=source_id, status="proposed", steps=steps,
                        tool_classes=cls and {tool: cls} or classes,
                        tools_digest=plans.tools_digest(tools), notes=bound_notes(source_id)))
    return classes[tool]


# --- FIN-02: the contract the page's vouch control is built on ---------------------------------
def test_an_executor_is_unclassified_by_the_shared_rule_and_a_write_verb_is_not():
    """The page offers a vouch only for a tool whose catalogue basis is ``unknown`` AND whose plan
    class is ``unclassified:unknown``. Both are the shared classifier's outputs."""
    from databridge_agent_runtime.classify import classify_tool
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.plans import SqlSpec, StepSpec

    c = classify_tool(EXECUTOR)
    assert (c.writes, c.basis) == (True, "unknown")
    steps = [StepSpec(id="db", strategy="tabular", sql=SqlSpec(tool=EXECUTOR))]
    cls = plans.classify_plan_tools(steps, [ToolInfo(name=EXECUTOR, description="",
                                                     input_schema={})], [])[EXECUTOR]
    assert cls.startswith("unclassified:unknown"), cls
    w = classify_tool("write_rows")
    assert (w.writes, w.basis) == (True, "name")      # never offered for a vouch


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_database_plan_is_approvable_only_with_the_principals_vouch(env, monkeypatch, estate):
    source = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, source).status_code == 200
        _sql_plan(source, EXECUTOR, monkeypatch)
        refused = approve(c, source)
        assert refused.status_code == 409
        assert "vouch_read_tools" in refused.json()["refusal"]["message"]
        ok = approve(c, source, vouch_read_tools=[EXECUTOR], approved_by="mallory")
        assert ok.status_code == 200, ok.text
        plan = ok.json()["plan"]
    assert plan["approved_by"] == "analyst-7"                 # the principal, never the body
    assert plan["tool_classes"][EXECUTOR] == "read:sql_guarded:vouched_by:analyst-7"
    assert plan["approval"]["vouched_read_tools"] == [EXECUTOR]


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_a_write_is_never_vouchable_and_an_unauthenticated_vouch_is_refused(env, monkeypatch,
                                                                             estate):
    source = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, source).status_code == 200
        _sql_plan(source, "write_rows", monkeypatch)
        r = approve(c, source, vouch_read_tools=["write_rows"])
        assert r.status_code == 409
    with TestClient(app) as anon:
        r = anon.post(f"/sources/{source}/plan/approve", json={"vouch_read_tools": [EXECUTOR]})
        assert r.status_code == 401


# --- FIN-16: the dev token is recorded, and shown, as what it is -------------------------------
@pytest.fixture()
def dev_auth(monkeypatch):
    monkeypatch.setenv("WALNUT_DEV_AUTH", "1")
    auth.reset_auth_state_for_tests()
    yield {"Authorization": f"Bearer {auth.DEV_TOKEN}"}
    monkeypatch.delenv("WALNUT_DEV_AUTH", raising=False)
    auth.reset_auth_state_for_tests()


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_an_approval_with_the_dev_token_is_recorded_dev_only(env, monkeypatch, dev_auth, estate):
    source = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=dev_auth) as c:
        assert _cmd(c, source).status_code == 200
        _sql_plan(source, EXECUTOR, monkeypatch)
        r = approve(c, source, vouch_read_tools=[EXECUTOR])
        assert r.status_code == 200, r.text
        rec = r.json()["plan"]["approval"]
        me = c.get("/auth/whoami").json()
    assert rec["principal_id"] == auth.DEV_PRINCIPAL_ID
    assert rec["dev_only"] is True and rec["authenticated_by"] == auth.DEV_MECHANISM
    assert me == {"principal_id": auth.DEV_PRINCIPAL_ID, "dev_only": True,
                  "authenticated_by": auth.DEV_MECHANISM}
    # the approval log line says the same as the plan (hash-chained, append-only)
    from databridge_mcp_ingest.plans import plan_approvals_path
    import json
    lines = [json.loads(x) for x in
             plan_approvals_path(source).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines and lines[-1]["dev_only"] is True
    assert lines[-1]["authenticated_by"] == auth.DEV_MECHANISM


@pytest.mark.parametrize("estate", sorted(DB_SOURCES))
def test_an_approval_with_a_real_token_is_not_marked(env, monkeypatch, dev_auth, estate):
    """Dev mode ON for the process, but this caller holds a REAL token for the same principal
    name: only the dev TOKEN is marked, not the name it maps to."""
    source = DB_SOURCES[estate]
    app, rt, _ = _mount(env)
    rt.ingest = _RealApproveIngest(tools=[])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert _cmd(c, source).status_code == 200
        _sql_plan(source, EXECUTOR, monkeypatch)
        rec = approve(c, source, vouch_read_tools=[EXECUTOR]).json()["plan"]["approval"]
        me = c.get("/auth/whoami").json()
    assert rec["dev_only"] is False and rec["authenticated_by"] == auth.GATEWAY_MECHANISM
    assert me["dev_only"] is False and me["principal_id"] == "analyst-7"


def test_whoami_needs_a_credential(env):
    app, _rt, _ = _mount(env)
    with TestClient(app) as anon:
        assert anon.get("/auth/whoami").status_code == 401


# --- FIN-19: the allowed browser origins are configuration --------------------------------------
def test_cors_origins_default_to_the_local_frontend():
    assert auth.cors_allowed_origins("") == list(auth.DEFAULT_CORS_ORIGINS)


def _launcher_port(estate: str, first_digit: str) -> int:
    """The port an estate's launcher uses most often for a service (gateway 8xxx, frontend 3xxx).
    Read from the launcher, not written here; the finance launcher ALSO names the workspace ports
    (to refuse them), which is why the most frequent one is taken, not the first."""
    import re
    from collections import Counter
    script = {"workspace": "start-databridge.ps1", "finance": "start-walnut-finance.ps1"}[estate]
    text = (ROOT / script).read_text(encoding="utf-8", errors="replace")
    found = Counter(int(p) for p in re.findall(rf"\b({first_digit}\d{{3}})\b", text))
    assert found, f"{script} names no {first_digit}xxx port"
    return found.most_common(1)[0][0]


@pytest.mark.parametrize("estate", ["workspace", "finance"])
def test_cors_origins_come_from_the_environment(monkeypatch, estate):
    """Each estate's launcher serves its own frontend. The workspace one is the default; the
    finance one (another port) is admitted only when the environment names it."""
    port = _launcher_port(estate, "3")
    origins = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    monkeypatch.delenv(auth.CORS_ORIGINS_ENV, raising=False)
    default_fits = auth.cors_allowed_origins() == origins
    assert default_fits is (estate == "workspace"), (estate, port)
    monkeypatch.setenv(auth.CORS_ORIGINS_ENV, ", ".join(origins))
    assert auth.cors_allowed_origins() == origins


@pytest.mark.parametrize("bad", ["*", "http://*.example", "http://localhost:3100/app",
                                 "ftp://localhost:3100", "localhost:3100",
                                 "http://user:pw@localhost:3100", "http://localhost:99999"])
def test_a_wildcard_or_a_path_is_never_an_origin(bad):
    assert auth.cors_allowed_origins(bad) == list(auth.DEFAULT_CORS_ORIGINS)
    assert auth.cors_allowed_origins(f"{bad} http://localhost:3100") == ["http://localhost:3100"]


# --- FIN-16 in Activity: a decision made with the dev token is marked on the row ---------------
@pytest.mark.parametrize("dev", [True, False])
def test_activity_marks_a_decision_made_with_the_dev_token(dev):
    """The agent runtime records ``authenticated_by`` (and ``dev_only: true`` for the dev-token
    mechanism) on every decision event; Activity carries it to the row instead of dropping it."""
    from databridge_agent_runtime.runtime import _decided_by

    from databridge_gateway.activity_routes import approval_items
    from databridge_gateway.platform_runtime import (GATEWAY_DEV_MECHANISM, GATEWAY_MECHANISM,
                                                     approver_for, register_gateway_authenticators)

    register_gateway_authenticators()
    who = approver_for(auth.AuthenticatedPrincipal(principal_id="analyst-7", dev_mode=dev))
    decided = _decided_by(who)                  # what the runtime writes on a decision event
    events = [{"event": "proposed", "action_id": "a1", "name": "file a follow-up",
               "at": "2026-10-02T09:00:00+00:00"},
              {"event": "approved", "action_id": "a1", "by": who.principal_id,
               "at": "2026-10-02T09:01:00+00:00", **decided}]
    [row] = approval_items("run-1", "agent-x", events, {"actions": []})
    assert row["decided_by"] == "analyst-7"
    assert row["decided_by_dev_only"] is dev
    # R-028 CONS #7: the common actor object — `dev_only` true OR false as the event recorded it,
    # the principal id never decorated
    assert row["actor"]["dev_only"] is dev and row["actor"]["principal_id"] == "analyst-7"
    # E2E run 5 W-09: the runtime's name for the gateway's mechanism is served under the auth
    # layer's canonical name, on the flat field and the actor alike
    from databridge_gateway.activity_actor import canonical_mechanism
    recorded = GATEWAY_DEV_MECHANISM if dev else GATEWAY_MECHANISM
    assert row["decided_by_authenticated_by"] == canonical_mechanism(recorded)
    assert row["actor"]["authenticated_by"] == canonical_mechanism(recorded)


# --- FIN-19: the launcher's port is configuration too ------------------------------------------
def _gateway_port_fn():
    """`serve.gateway_port`, lifted out of the launcher without running it (importing serve.py
    boots path setup and announces the graph store)."""
    import ast
    import os

    src = (ROOT / "apps" / "gateway" / "serve.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "gateway_port")
    ns: dict = {"os": os}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "serve.py", "exec"), ns)  # noqa: S102
    return ns["gateway_port"]


@pytest.mark.parametrize("estate", ["workspace", "finance"])
def test_the_gateway_port_comes_from_the_environment(estate):
    """Each estate's launcher names its gateway port; `serve.py` honours it."""
    want = _launcher_port(estate, "8")
    port = _gateway_port_fn()
    assert port(str(want)) == want
    assert (port("") == want) is (estate == "workspace")
    for bad in ("80a", "0", "70000", "-1"):
        with pytest.raises(SystemExit):
            port(bad)
