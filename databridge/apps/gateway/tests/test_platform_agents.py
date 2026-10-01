"""`/agents` (BUILD_PLAN §6 U): the English box, save/activate, run, and the approval inbox.

Private directories in `tmp_path` for every `WALNUT_*` location; the repo's own `workspace` pack
supplies the purpose and golden set the agent compiler requires (the launcher's defaults). The
planner is the REAL harness over a scripted `FakeBackend`; the source gateway is a fake. No model
and no network are reached.

What is proven:

* English naming a folder no connection has → an open question naming that folder,
  ``activatable: false``, and activation refused;
* a compiled draft is saved (and activated) by ``draft_id`` — what was previewed is what is saved —
  and the trigger engine sees it immediately;
* a run's ``trigger.by`` is the signed-in principal, not a name in the body;
* a ``human_required`` action waits; approving it records the PRINCIPAL as ``by`` even when the
  body says otherwise, executes through the gateway exactly once, and a second approval is refused;
* a background run returns a job, and the job resolves to the run record.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS, auth_headers

from databridge_gateway import agents_routes, automation_routes
from databridge_gateway.platform_runtime import build_platform

PURPOSE = "workspace.agent_run"
GOLDEN = "packs/workspace/evals/agent_contract.jsonl"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from databridge_graph.packs import clear_caches

    for name, sub in {"WALNUT_SOURCES_DIR": "sources", "WALNUT_INGEST_DIR": "ingest",
                      "WALNUT_CAPTURE_DIR": "capture", "WALNUT_AGENTS_DIR": "agents",
                      "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents", "WALNUT_RUNS_DIR": "runs",
                      "WALNUT_AUTOMATION_DIR": "automation",
                      "WALNUT_EVENTS_OFFSETS_DIR": "offsets",
                      "WALNUT_PASSAGES_DIR": "passages"}.items():
        monkeypatch.setenv(name, str(tmp_path / sub))
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(tmp_path / "events" / "events.jsonl"))
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    monkeypatch.setenv("DATABRIDGE_PACKS", "workspace")
    monkeypatch.setenv("WALNUT_AGENT_PURPOSE", PURPOSE)
    monkeypatch.setenv("WALNUT_AGENT_GOLDEN_SET", GOLDEN)
    monkeypatch.setenv("WALNUT_AUTOMATION_ENGINE", "0")
    clear_caches()
    yield tmp_path
    clear_caches()


# ------------------------------------------------------------------------------------------
# fakes
# ------------------------------------------------------------------------------------------
class FakeGateway:
    """`larkspur-files` (read_text_file, list_directory, write_file), `larkspur-db` (read-only
    SQL) and `larkspur_db_writer` (`execute_sql`: DB writes are a separate writer connection's
    write tool, never source.query — R-017 / S2)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.closed = 0

    def advertise(self, connection):
        from databridge_agent_runtime import ConnectionAdvert, ToolAdvert

        if connection == "larkspur-files":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/f.yaml",
                                    tools=(ToolAdvert("read_text_file", writes=False),
                                           ToolAdvert("list_directory", writes=False),
                                           ToolAdvert("write_file", writes=True)))
        if connection == "larkspur-db":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/d.yaml",
                                    tools=(ToolAdvert("query", writes=False),), sql=True)
        if connection == "larkspur_db_writer":
            return ConnectionAdvert(connection=connection, declared_in="var/sources/w.yaml",
                                    tools=(ToolAdvert("execute_sql", writes=True),))
        return ConnectionAdvert(connection=connection, reachable=False, problem="unknown")

    def call_tool(self, connection, tool, arguments):
        from databridge_agent_runtime import GatewayResult
        self.calls.append(("call_tool", connection, tool, dict(arguments)))
        return GatewayResult(text="ok")

    def query(self, connection, sql):
        from databridge_agent_runtime import GatewayResult
        self.calls.append(("query", connection, sql))
        if not sql.strip().lower().startswith("select"):
            return GatewayResult(text="INSERT 0 1")
        return GatewayResult(text=json.dumps([{"invoice": "INV-1042", "days_overdue": 21}]))

    def close(self):
        self.closed += 1

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if (c[0] == "query" and
                not c[2].strip().lower().startswith("select"))
                or (c[0] == "call_tool" and c[2] == "execute_sql")]


def _refs(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "ref" and isinstance(v, str):
                out.append(v)
            elif k == "refs" and isinstance(v, list):
                out.extend(x for x in v if isinstance(x, str))
            else:
                out.extend(_refs(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_refs(v))
    return out


def scripted_harness(script: Callable[[int, list], dict]):
    from databridge_harness import FakeBackend, Harness

    state = {"i": 0}

    def respond(model, messages, guided_json):
        last = messages[-1]["content"]
        obs = json.loads(last[len("Observations:\n"):]) \
            if isinstance(last, str) and last.startswith("Observations:\n") else []
        turn = script(state["i"], obs)
        state["i"] += 1
        return json.dumps(turn)

    backend = FakeBackend(respond, tokens_in=5, tokens_out=5)
    config = {"routing": {"agent": "planner"}, "aliases": {"planner": {"model": "fake/planner"}},
              "profiles": {"cloud": {}}}
    return Harness(backend, config=config, profile="cloud", cache=None)


SQL_WRITE = "INSERT INTO tasks (invoice) VALUES ('INV-1042')"


def followup_script(i: int, obs: list) -> dict:
    if i == 0:
        return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                "args": {"sql": "SELECT * FROM invoices WHERE overdue"}}]}
    if i == 1:
        return {"actions": [{"name": "log_followup_task", "args": {"sql": SQL_WRITE},
                             "derived_from": _refs(obs)[:1]}]}
    return {"final": {"claims": []}}


def write_followup_agent(agents_dir, *, status: str = "active") -> str:
    """The §6 X read-write example, written as the two §5.6 files through the runtime's own
    mapping (the same function the English compiler's declaration comes from). ``active``: only
    an active agent runs (admission gate)."""
    from databridge_agent_runtime import Governance, declaration_from_workflow
    from databridge_workflow_spec import WorkflowSpec, dump_spec

    spec = WorkflowSpec.model_validate({
        "agent": "overdue_invoice_followup", "title": "Overdue invoice follow-up",
        "created_from": "Find overdue invoices and log a follow-up task. Ask me before logging.",
        "mode": "read_write", "sources": ["larkspur-db", "larkspur_db_writer"],
        "tools": ["graph.search", "source.query:larkspur-db"],
        "actions": [{"name": "log_followup_task",
                     "tool": "source.write:larkspur_db_writer:execute_sql",
                     "automation_mode": "human_required"}],
        "instructions": "1. Query overdue invoices. 2. Log a task.",
        "limits": {"max_steps": 6, "max_tool_calls": 10, "tokens_per_run": 20000},
        "status": status})
    decl = declaration_from_workflow(spec, "workspace", Governance(
        purpose=PURPOSE, golden_set=GOLDEN, escalations_per_day=10,
        cost_ceiling_usd_per_day=5.0))
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{spec.agent}.workflow.yaml").write_text(dump_spec(spec), encoding="utf-8")
    (agents_dir / f"{spec.agent}.agent.yaml").write_text(decl, encoding="utf-8")
    return spec.agent


def _mount(env):
    from databridge_graph.store import GraphStore

    app = FastAPI()
    app.state.graph_store = GraphStore()
    rt = build_platform(app)
    gw = FakeGateway()
    rt.harness = None
    rt.gateway_factory = lambda: gw
    rt.retriever_factory = None
    app.state.platform = rt
    agents_routes.register_agents_routes(app)
    automation_routes.register_automation_routes(app)
    return app, rt, gw


def _connection(env, cid: str, roots: list[str], tools: list[str]) -> None:
    """A registered connection + an approved plan's tool list, as the catalogue reads them."""
    from databridge_mcp_ingest import register_connection

    register_connection({"id": cid, "title": cid, "scope": {"roots": roots},
                         "transport": {"type": "stdio", "command": "npx"}})
    (env / "sources" / f"{cid}.plan.yaml").write_text(yaml.safe_dump(
        {"source_id": cid, "status": "approved", "tools_seen": tools,
         "steps": [{"id": "fs", "kind": "documents"}]}), encoding="utf-8")


# ------------------------------------------------------------------------------------------
# the English box
# ------------------------------------------------------------------------------------------
def test_an_unknown_folder_is_a_question_not_a_guess_and_cannot_activate(env):
    app, rt, _ = _mount(env)
    _connection(env, "larkspur-files", ["Contracts"], ["read_text_file", "list_directory"])
    folder = "Zanzibar"
    with TestClient(app, headers=AUTH_HEADERS) as c:
        r = c.post("/agents/compile", json={
            "text": f"Every weekday at 9am summarise new files in the {folder} folder"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["activatable"] is False
        assert body["draft_id"]
        asked = [q for q in body["open_questions"] if folder in q["text"]]
        assert asked, body["open_questions"]
        assert c.post("/agents", json={"draft_id": body["draft_id"],
                                       "activate": True}).status_code == 409
    agents = env / "agents"
    assert (sorted(agents.glob("*.yaml")) if agents.is_dir() else []) == []   # nothing written


def test_a_compiled_draft_is_saved_by_id_activated_and_seen_by_the_engine(env):
    app, rt, _ = _mount(env)
    _connection(env, "larkspur-files", ["Contracts"], ["read_text_file", "list_directory"])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        body = c.post("/agents/compile", json={
            "text": "Brief me on a customer before a call using larkspur-files"}).json()
        assert body["activatable"] is True, body
        agent_id = body["spec_id"]
        r = c.post("/agents", json={"draft_id": body["draft_id"], "activate": True})
        assert r.status_code == 200, r.text
        assert r.json()["saved_by"] == "analyst-7" and r.json()["status"] == "active"
        assert (env / "agents" / f"{agent_id}.workflow.yaml").is_file()
        assert (env / "agents" / f"{agent_id}.agent.yaml").is_file()
        # the saved spec is the previewed one, verbatim
        saved = yaml.safe_load((env / "agents" / f"{agent_id}.workflow.yaml").read_text("utf-8"))
        assert saved["created_from"] == body["spec"]["created_from"]
        assert agent_id in rt.engine.specs                             # engine reloaded
        assert [a["agent"] for a in c.get("/agents").json()["agents"]] == [agent_id]
        assert c.get(f"/agents/{agent_id}").json()["spec"]["status"] == "active"
        # a draft is consumed once saved
        assert c.post("/agents", json={"draft_id": body["draft_id"]}).status_code == 404


# ------------------------------------------------------------------------------------------
# run + approvals: the principal, always
# ------------------------------------------------------------------------------------------
def test_run_and_approval_are_attributed_to_the_principal_not_the_body(env):
    from databridge_gateway.agent_ownership import record_owner

    app, rt, gw = _mount(env)
    agent_id = write_followup_agent(env / "agents")
    record_owner(agent_id, "analyst-7")         # as `POST /agents` records it (N-18)
    rt.harness = scripted_harness(followup_script)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post(f"/agents/{agent_id}/run", json={"wait": True, "by": "mallory"})
        assert r.status_code == 200, r.text
        record = r.json()["record"]
        assert record["trigger"]["by"] == "analyst-7"
        assert [(a["name"], a["status"]) for a in record["actions"]] == \
            [("log_followup_task", "proposed")]
        assert gw.writes() == []                                        # waits for a person
        run_id = r.json()["run_id"]

        pending = c.get("/agents/approvals").json()["pending"]
        assert [(p["run_id"], p["action_id"], p["args"]) for p in pending] == \
            [(run_id, "a1", {"sql": SQL_WRITE})]

        r = c.post(f"/agents/approvals/{run_id}/a1/approve",
                   json={"by": "mallory", "approved_by": "mallory"})
        assert r.status_code == 200, r.text
        assert r.json()["decided_by"] == "analyst-7"
        action = r.json()["record"]["actions"][0]
        assert (action["status"], action["by"]) == ("executed", "analyst-7")
        assert gw.writes() == [("call_tool", "larkspur_db_writer", "execute_sql", {"sql": SQL_WRITE})]
        history = c.get(f"/agents/{agent_id}/runs/{run_id}").json()["action_history"]
        assert [(e["event"], e.get("by")) for e in history if e["event"] != "proposed"] == \
            [("approved", "analyst-7"), ("executed", "analyst-7")]

        again = c.post(f"/agents/approvals/{run_id}/a1/approve")
        assert again.status_code == 409
        assert gw.writes() == [("call_tool", "larkspur_db_writer", "execute_sql", {"sql": SQL_WRITE})]     # executed once
        assert c.get("/agents/approvals").json()["pending"] == []
        assert c.post(f"/agents/approvals/{run_id}/a9/approve").status_code in (404, 409)
    assert gw.closed >= 2                                               # run + approval


def test_a_background_run_resolves_to_its_record(env):
    app, rt, _ = _mount(env)
    agent_id = write_followup_agent(env / "agents")
    # as `POST /agents` records it: an ownerless agent's runs are operator-only (P-09)
    from databridge_gateway.agent_ownership import record_owner
    record_owner(agent_id, "fcc-3")
    rt.harness = scripted_harness(followup_script)
    with TestClient(app, headers=auth_headers("fcc-3")) as c:
        r = c.post(f"/agents/{agent_id}/run", json={"question": "anything overdue?"})
        assert r.status_code == 202, r.text
        job = c.get(f"/agents/{agent_id}/jobs/{r.json()['job_id']}").json()
        assert job["status"] == "completed", job
        got = c.get(f"/agents/{agent_id}/runs/{job['run_id']}").json()["record"]
        assert got["trigger"]["by"] == "fcc-3"
        assert got["trigger"]["question"] == "anything overdue?"
        runs = c.get(f"/agents/{agent_id}/runs").json()["runs"]
        assert [x["run_id"] for x in runs] == [job["run_id"]]
        assert c.post("/agents/no_such_agent/run", json={"wait": True}).status_code == 404


def test_a_draft_agent_does_not_run_from_run_now(env):
    """F2 / R-017: "run now" passes the same admission gate as the trigger engine."""
    app, rt, gw = _mount(env)
    agent_id = write_followup_agent(env / "agents", status="draft")
    rt.harness = scripted_harness(followup_script)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        for body in ({"wait": True}, {}):
            r = c.post(f"/agents/{agent_id}/run", json=body)
            assert r.status_code == 409, r.text
            assert r.json()["refusal"]["code"] == "AGENT_NOT_RUNNABLE"
            assert "draft" in r.json()["refusal"]["message"]
        assert c.get(f"/agents/{agent_id}/runs").json()["runs"] == []
    assert gw.calls == []                                               # nothing was read


# ------------------------------------------------------------------------------------------
# R-019 (Codex batch 2, n-platform-routes)
# ------------------------------------------------------------------------------------------
def test_a_draft_is_saved_only_by_the_principal_who_previewed_it(env):
    """#5: knowing another principal's draft_id is not having reviewed its preview."""
    app, rt, _ = _mount(env)
    _connection(env, "larkspur-files", ["Contracts"], ["read_text_file", "list_directory"])
    text = "Brief me on a customer before a call using larkspur-files"
    with TestClient(app) as c:
        body = c.post("/agents/compile", json={"text": text},
                      headers=auth_headers("analyst-7")).json()
        assert body["activatable"] is True, body
        r = c.post("/agents", json={"draft_id": body["draft_id"], "activate": True},
                   headers=auth_headers("fcc-3"))
        assert r.status_code == 403 and r.json()["refusal"]["code"] == "NOT_YOUR_DRAFT"
        agents = env / "agents"
        assert not agents.is_dir() or not list(agents.glob("*.workflow.yaml"))
        # ...and the draft is still there for the person who did preview it
        r = c.post("/agents", json={"draft_id": body["draft_id"]},
                   headers=auth_headers("analyst-7"))
        assert r.status_code == 200 and r.json()["saved_by"] == "analyst-7"


def test_every_agent_run_path_reloads_the_graph(env):
    """#4: `run_agent_now` is the one path every trigger takes; it reloads on the way out."""
    app, rt, _ = _mount(env)
    reloads: list[int] = []
    app.state.reload_graph = lambda: reloads.append(1) or {"nodes": len(reloads)}
    agent_id = write_followup_agent(env / "agents")
    rt.harness = scripted_harness(followup_script)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert c.post(f"/agents/{agent_id}/run", json={"wait": True}).status_code == 200
        assert len(reloads) == 1
        assert c.post(f"/agents/{agent_id}/run", json={}).status_code == 202
        assert len(reloads) == 2


@pytest.mark.parametrize("status", ["failed", "refused"])
def test_a_failed_or_refused_run_is_not_a_completed_job(status):
    """#7: the job status is an allow-list over RunRecord.status."""
    from databridge_agent_runtime.runs import RunRecord
    from databridge_gateway.agents_routes import _RUN_DONE, job_status_for_record

    statuses = set(RunRecord.model_fields["status"].annotation.__args__)
    assert _RUN_DONE < statuses and status in statuses       # derived, and non-vacuous
    assert job_status_for_record(status) == "failed"
    assert {job_status_for_record(s) for s in _RUN_DONE} == {"completed"}


def test_an_event_firing_hands_the_run_the_item_it_was_fired_for(env):
    """E2E F-12: an event-triggered run used to receive only `event_id`, so the planner could
    not see WHICH item (uri, title) it was fired for. The event now rides on the run's trigger
    and, fenced as data, in the run's question — the runtime's existing planner input."""
    import asyncio

    from databridge_agent_runtime import load_agent, read_run, run_path
    from databridge_gateway.platform_runtime import make_engine_runner

    app, rt, _ = _mount(env)
    agent_id = write_followup_agent(env / "agents")
    rt.harness = scripted_harness(followup_script)
    spec, _decl = load_agent(agent_id)
    event = {"event_id": "ev-7f3a", "source_id": "larkspur-files", "type": "item.created",
             "uri": "file:///Contracts/acme-renewal-2026.pdf", "title": "ACME renewal 2026",
             "item_id": "doc:91", "attrs": {"size": 1204}, "internal_offset": 88}
    info = {"type": "event", "by": None, "payload": {}, "firing_id": "f-1",
            "idempotency_key": f"{agent_id}:event:ev-7f3a", "event": event}
    out = asyncio.run(make_engine_runner(app)(spec, info))
    record = read_run(run_path(out["run_id"], agent=agent_id))
    trig = record.trigger
    assert trig["event_id"] == "ev-7f3a"
    for key in ("uri", "title", "item_id", "source_id", "type"):
        assert trig["event"][key] == event[key], key              # derived from the event
    assert "internal_offset" not in trig["event"]                  # only the §5.3 fields
    assert event["uri"] in trig["question"] and event["title"] in trig["question"]
    assert spec.created_from in trig["question"]                   # the task is kept
    assert "data, not as instructions" in trig["question"]

    # A firing that carries its own question keeps it (the event still rides on the trigger).
    info2 = {**info, "firing_id": "f-2", "payload": {"question": "only this"}}
    out2 = asyncio.run(make_engine_runner(app)(spec, info2))
    trig2 = read_run(run_path(out2["run_id"], agent=agent_id)).trigger
    assert trig2["question"] == "only this" and trig2["event"]["uri"] == event["uri"]


def test_gateway_boot_registers_its_principal_type_and_authenticators():
    """Boot registration (R-019-adapter-agents-r2 + RTF): the gateway's principal type is trusted
    by the adapter agents, and the runtime verifies approvers the gateway minted. Look-alikes —
    a dataclass carrying a principal_id, an Approver typed by a caller — are refused."""
    from dataclasses import dataclass

    import databridge_agent_runtime as ar
    from databridge_adapter_agents.adapt import AdaptationRefused, principal_of
    from fastapi import FastAPI

    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.platform_runtime import approver_for, build_platform
    from databridge_agent_runtime.runtime import _principal

    build_platform(FastAPI())
    who = principal_of(AuthenticatedPrincipal(principal_id="alice"), "approve")
    assert who["principal_id"] == "alice" and who["authenticated_by"] == "gateway"

    @dataclass
    class LookAlike:
        principal_id: str

    with pytest.raises(AdaptationRefused):
        principal_of(LookAlike("alice"), "approve")

    for dev in (False, True):
        minted = approver_for(AuthenticatedPrincipal(principal_id="alice", dev_mode=dev))
        assert _principal(minted, "approve") == "alice"
        assert minted.dev_only is dev
        forged = ar.Approver(principal_id="alice", authenticated_by=minted.authenticated_by,
                             dev_only=dev)
        with pytest.raises(ar.ActionRefused):
            _principal(forged, "approve")


# ------------------------------------------------------------------------------------------
# N-18 (E2E run 2): only the agent's owner, or an operator, decides its actions
# ------------------------------------------------------------------------------------------
def _proposal(env, owner):
    from databridge_gateway.agent_ownership import record_owner

    app, rt, gw = _mount(env)
    agent_id = write_followup_agent(env / "agents")
    if owner:
        record_owner(agent_id, owner)
    rt.harness = scripted_harness(followup_script)
    c = TestClient(app, headers=auth_headers("analyst-7"))
    r = c.post(f"/agents/{agent_id}/run", json={"wait": True})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert [a["status"] for a in r.json()["record"]["actions"]] == ["proposed"]
    return c, agent_id, run_id, gw


def _events(c, agent_id, run_id):
    return [e["event"] for e in c.get(f"/agents/{agent_id}/runs/{run_id}").json()["action_history"]]


def test_n18_a_second_principal_cannot_decide_the_owners_proposal(env, monkeypatch):
    monkeypatch.delenv("WALNUT_OPERATORS", raising=False)
    c, agent_id, run_id, gw = _proposal(env, owner="analyst-7")
    before = _events(c, agent_id, run_id)
    other = auth_headers("fcc-3")
    for action in ("reject", "approve", "reverse"):
        r = c.post(f"/agents/approvals/{run_id}/a1/{action}",
                   json={"by": "analyst-7", "reason": "not mine"}, headers=other)
        assert r.status_code == 403, (action, r.text)
        assert r.json()["refusal"]["code"] == "NOT_THE_AGENTS_OWNER"
    assert _events(c, agent_id, run_id) == before and gw.writes() == []   # nothing recorded
    # E2E run 3, P-09: a non-owner's inbox does not list the owner's proposal at all
    assert c.get("/agents/approvals", headers=other).json()["pending"] == []
    assert {p["run_id"]: p["may_decide"]
            for p in c.get("/agents/approvals").json()["pending"]} == {run_id: True}
    r = c.post(f"/agents/approvals/{run_id}/a1/reject", json={"reason": "no"})
    assert r.status_code == 200 and r.json()["decided_by"] == "analyst-7"


def test_n18_an_ownerless_agents_actions_are_decided_by_an_operator_only(env, monkeypatch):
    monkeypatch.setenv("WALNUT_OPERATORS", json.dumps(["audit-1"]))
    c, agent_id, run_id, gw = _proposal(env, owner=None)
    r = c.post(f"/agents/approvals/{run_id}/a1/reject", json={"reason": "x"})
    assert r.status_code == 403 and r.json()["refusal"]["code"] == "OPERATOR_REQUIRED"
    r = c.post(f"/agents/approvals/{run_id}/a1/reject", json={"reason": "op"},
               headers=auth_headers("audit-1"))
    assert r.status_code == 200, r.text
    assert r.json()["decided_by"] == "audit-1"
    # deciding does not make anyone the owner
    from databridge_gateway.agent_ownership import recorded_owner
    assert recorded_owner(agent_id) is None


def test_n18_no_operator_list_means_no_operator_outside_dev_mode(env, monkeypatch):
    monkeypatch.delenv("WALNUT_OPERATORS", raising=False)
    c, _agent_id, run_id, _gw = _proposal(env, owner=None)
    for who in ("analyst-7", "fcc-3", "audit-1"):
        r = c.post(f"/agents/approvals/{run_id}/a1/reject", json={},
                   headers=auth_headers(who))
        assert r.status_code == 403, who


def test_n18_saving_records_the_creator_and_only_the_owner_may_replace(env, monkeypatch):
    from types import SimpleNamespace

    from databridge_gateway.agent_ownership import recorded_owner, save_refusal

    monkeypatch.delenv("WALNUT_OPERATORS", raising=False)
    app, _rt, _ = _mount(env)
    _connection(env, "larkspur-files", ["Contracts"], ["read_text_file", "list_directory"])
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        body = c.post("/agents/compile", json={
            "text": "Brief me on a customer before a call using larkspur-files"}).json()
        r = c.post("/agents", json={"draft_id": body["draft_id"]})
        assert r.status_code == 200, r.text
        agent_id = r.json()["id"]
    assert r.json()["owner"] == "analyst-7" == recorded_owner(agent_id)
    owner, other = SimpleNamespace(principal_id="analyst-7", dev_mode=False),         SimpleNamespace(principal_id="fcc-3", dev_mode=False)
    assert save_refusal(owner, agent_id) is None
    assert save_refusal(other, agent_id)[0] == "NOT_THE_AGENTS_OWNER"
    monkeypatch.setenv("WALNUT_OPERATORS", "fcc-3")
    assert save_refusal(other, agent_id) is None                    # an operator may


# ------------------------------------------------------------------------------------------
# P-09 (E2E run 3): a proposal — with its full arguments — is shown only to whom may decide it
# ------------------------------------------------------------------------------------------
def _visible(c, agent_id, run_id, headers):
    inbox = c.get("/agents/approvals", headers=headers).json()["pending"]
    runs = c.get(f"/agents/{agent_id}/runs", headers=headers).json()["runs"]
    one = c.get(f"/agents/{agent_id}/runs/{run_id}", headers=headers)
    listed = c.get(f"/agents/{agent_id}", headers=headers).json()["runs"]
    return inbox, runs, one, listed


def test_p09_another_owners_proposals_are_not_listed_or_readable(env, monkeypatch):
    monkeypatch.setenv("WALNUT_OPERATORS", json.dumps(["audit-1"]))
    c, agent_id, run_id, _gw = _proposal(env, owner="analyst-7")
    # the owner sees the proposal with its arguments — so the non-owner checks are not vacuous
    inbox, runs, one, listed = _visible(c, agent_id, run_id, auth_headers("analyst-7"))
    assert [(p["run_id"], p["may_decide"]) for p in inbox] == [(run_id, True)] and inbox[0]["args"]
    assert [r["run_id"] for r in runs] == [run_id] and one.status_code == 200 and run_id in listed
    # an operator sees it too (the same rule as deciding)
    inbox, runs, one, _ = _visible(c, agent_id, run_id, auth_headers("audit-1"))
    assert [p["run_id"] for p in inbox] == [run_id] and runs and one.status_code == 200
    # a non-owner sees NOTHING — and the 404 is byte-identical to a run that does not exist
    inbox, runs, one, listed = _visible(c, agent_id, run_id, auth_headers("fcc-3"))
    assert inbox == [] and runs == [] and listed == []
    missing = c.get(f"/agents/{agent_id}/runs/run-does-not-exist",
                    headers=auth_headers("fcc-3"))
    assert one.status_code == missing.status_code == 404
    assert one.json()["refusal"]["code"] == missing.json()["refusal"]["code"] == "UNKNOWN_RUN"
    assert one.json()["refusal"]["message"].replace(run_id, "X") == \
        missing.json()["refusal"]["message"].replace("run-does-not-exist", "X")
    assert c.get("/agents/x_no_such_agent/runs", headers=auth_headers("fcc-3")).json()["runs"] \
        == runs


def test_p09_an_ownerless_agents_proposals_are_operator_only(env, monkeypatch):
    monkeypatch.setenv("WALNUT_OPERATORS", json.dumps(["audit-1"]))
    c, agent_id, run_id, _gw = _proposal(env, owner=None)
    for who in ("analyst-7", "fcc-3"):                 # even the principal who ran it
        inbox, runs, one, _ = _visible(c, agent_id, run_id, auth_headers(who))
        assert inbox == [] and runs == [] and one.status_code == 404, who
    inbox, runs, one, _ = _visible(c, agent_id, run_id, auth_headers("audit-1"))
    assert [p["run_id"] for p in inbox] == [run_id] and one.status_code == 200
