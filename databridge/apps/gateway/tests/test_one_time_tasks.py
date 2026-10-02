"""FEAT8 (founder 2026-10-02): a one-time task on the Intelligence page.

English -> the AGENT compiler (``run_once``) -> a plan shown before anything runs -> ``run_agent``
once -> reads run, every write is HELD -> approve / reject inline -> the cited answer and the
outcome block -> an owner-only Activity record. No agent is persisted.

Both estates, each from its own manifest (``infra/mcp_servers.yaml`` and
``infra/mcp_servers.finance.yaml``): the ingest database it reads and the files write connection
it writes follow-up notes to. Ids, tool names and the pack's purpose and golden set are READ, never
written down here. The planner is the real harness over a scripted ``FakeBackend``; the live
source gateway is a fake that records every call, so "the write landed" means the fake saw it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import auth_headers

ROOT = Path(__file__).resolve().parents[3]                          # databridge/
MANIFESTS = {"workspace": ROOT / "infra" / "mcp_servers.yaml",
             "finance": ROOT / "infra" / "mcp_servers.finance.yaml"}
OWNER, OTHER = "analyst-7", "fcc-3"


def _servers(estate: str) -> list[dict]:
    doc = yaml.safe_load(MANIFESTS[estate].read_text(encoding="utf-8"))
    return [s for s in doc.get("servers") or [] if isinstance(s, dict)]


def estate_sources(estate: str) -> tuple[dict, dict]:
    """(the ingest database, the files write connection) as the estate's manifest declares them."""
    servers = _servers(estate)
    db = sorted((s for s in servers if s.get("source_class") == "database"
                 and s.get("purpose", "ingest") != "agent_write"), key=lambda s: s["id"])[0]
    fu = sorted((s for s in servers if s.get("source_class") == "files"
                 and (s.get("tools_hint") or {}).get("write")), key=lambda s: s["id"])[0]
    return db, fu


# ------------------------------------------------------------------------------------------------
# fakes
# ------------------------------------------------------------------------------------------------
class FakeGateway:
    def __init__(self, db: dict, fu: dict) -> None:
        self.db, self.fu = db, fu
        self.query_tool = (db.get("tools_hint") or {}).get("query") or "query"
        self.write_tool = fu["tools_hint"]["write"]
        self.calls: list[tuple] = []
        self.rows = [{"record_no": "R-1042", "days_overdue": 21}]

    def advertise(self, connection):
        from databridge_agent_runtime import ConnectionAdvert, ToolAdvert
        if connection == self.db["id"]:
            return ConnectionAdvert(connection=connection, declared_in="var/sources/db.yaml",
                                    tools=(ToolAdvert(self.query_tool, writes=False),), sql=True,
                                    sql_tool=self.query_tool)
        if connection == self.fu["id"]:
            return ConnectionAdvert(connection=connection, declared_in="var/sources/fu.yaml",
                                    tools=(ToolAdvert(self.write_tool, writes=True),))
        return ConnectionAdvert(connection=connection, reachable=False, problem="unknown")

    def call_tool(self, connection, tool, arguments):
        from databridge_agent_runtime import GatewayResult
        self.calls.append(("call_tool", connection, tool, dict(arguments)))
        return GatewayResult(text=f"wrote {arguments.get('path')}")

    def query(self, connection, sql):
        from databridge_agent_runtime import GatewayResult
        self.calls.append(("query", connection, sql))
        return GatewayResult(text=json.dumps(self.rows))

    def close(self):
        pass

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "call_tool" and c[2] == self.write_tool]


class _Retriever:
    def search(self, *a, **k):
        return []


def _refs(obj) -> list[str]:
    if isinstance(obj, dict):
        out = [obj["ref"]] if isinstance(obj.get("ref"), str) else []
        for k, v in obj.items():
            if k != "ref":
                out += _refs(v)
        return out
    if isinstance(obj, list):
        return [r for v in obj for r in _refs(v)]
    return []


def scripted_harness(script):
    from databridge_harness import FakeBackend, Harness
    state = {"i": 0}

    def respond(model, messages, guided_json):
        last = messages[-1]["content"]
        obs = json.loads(last[len("Observations:\n"):]) \
            if isinstance(last, str) and last.startswith("Observations:\n") else []
        turn = script(state["i"], obs)
        state["i"] += 1
        return json.dumps(turn)

    config = {"routing": {"agent": "planner"}, "aliases": {"planner": {"model": "fake/planner"}},
              "profiles": {"cloud": {}}}
    return Harness(FakeBackend(respond, tokens_in=5, tokens_out=5), config=config,
                   profile="cloud", cache=None)


def read_script(db_id: str, rows: list[dict]):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.query:{db_id}",
                                    "args": {"sql": "SELECT record_no, days_overdue FROM records"}}]}
        ref = _refs(obs)[0]
        return {"final": {"claims": [{"text": f"{rows[0]['record_no']} is "
                                              f"{rows[0]['days_overdue']} days overdue.",
                                      "citations": [ref]}]}}
    return script


def write_script(db_id: str, action: str, rows: list[dict]):
    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": f"source.query:{db_id}",
                                    "args": {"sql": "SELECT record_no, days_overdue FROM records"}}]}
        if i == 1:
            return {"actions": [{"name": action, "args": {
                "path": f"{rows[0]['record_no']}.md",
                "content": f"Follow up {rows[0]['record_no']}."},
                "derived_from": _refs(obs)[:1]}]}
        return {"final": {"claims": []}}
    return script


# ------------------------------------------------------------------------------------------------
# the estate
# ------------------------------------------------------------------------------------------------
@pytest.fixture(params=["workspace", "finance"])
def estate(request, tmp_path, monkeypatch):
    from databridge_automation.catalogue import (default_agent_purpose, default_golden_set,
                                                 load_pack_purposes)
    from databridge_graph.packs import clear_caches

    pack = request.param
    for name, sub in {"WALNUT_SOURCES_DIR": "sources", "WALNUT_INGEST_DIR": "ingest",
                      "WALNUT_CAPTURE_DIR": "capture", "WALNUT_AGENTS_DIR": "agents",
                      "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents", "WALNUT_RUNS_DIR": "runs",
                      "WALNUT_AUTOMATION_DIR": "automation", "WALNUT_ONE_TIME_DIR": "one_time",
                      "WALNUT_EVENTS_OFFSETS_DIR": "offsets",
                      "WALNUT_PASSAGES_DIR": "passages"}.items():
        monkeypatch.setenv(name, str(tmp_path / sub))
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(tmp_path / "events" / "events.jsonl"))
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    monkeypatch.delenv("WALNUT_OPERATORS", raising=False)
    monkeypatch.setenv("DATABRIDGE_PACKS", pack)
    monkeypatch.setenv("WALNUT_AUTOMATION_ENGINE", "0")
    clear_caches()
    # the pack's own purpose for compiled agents (or its `*.agent_run` one) and golden set
    purpose = default_agent_purpose(pack) or next(
        p for p in load_pack_purposes(pack) if p.endswith(".agent_run"))
    golden = default_golden_set(pack)
    assert purpose and golden, (pack, purpose, golden)
    monkeypatch.setenv("WALNUT_AGENT_PURPOSE", purpose)
    monkeypatch.setenv("WALNUT_AGENT_GOLDEN_SET", golden)

    db, fu = estate_sources(pack)
    src = tmp_path / "sources"
    src.mkdir(parents=True)
    for s in (db, fu):
        body = {k: s[k] for k in ("id", "title", "source_class", "aliases", "purpose",
                                  "writes_for", "tools_hint") if k in s}
        body.update(scope=s.get("scope") or {"roots": ["."]},
                    transport={"type": "stdio", "command": "npx"})
        (src / f"{s['id']}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    query = (db.get("tools_hint") or {}).get("query") or "query"
    (src / f"{db['id']}.plan.yaml").write_text(yaml.safe_dump({
        "source_id": db["id"], "status": "approved", "tools_seen": [query],
        "steps": [{"id": "db", "strategy": "tabular", "sql": {"tool": query, "arg": "sql"}}]}),
        encoding="utf-8")

    from databridge_gateway import activity_routes, agents_routes, intelligence_routes
    from databridge_gateway.platform_runtime import build_platform
    from databridge_graph.store import GraphStore

    app = FastAPI()
    app.state.graph_store = GraphStore()
    rt = build_platform(app)
    gw = FakeGateway(db, fu)
    rt.harness = None
    rt.gateway_factory = lambda: gw
    rt.retriever_factory = lambda: _Retriever()        # advertised, never called by the scripts
    app.state.platform = rt
    intelligence_routes._register_intelligence_routes(app)
    agents_routes.register_agents_routes(app)
    activity_routes.register_activity_routes(app)
    yield {"pack": pack, "app": app, "rt": rt, "gw": gw, "db": db, "fu": fu, "tmp": tmp_path,
           "purpose": purpose, "golden": golden}
    clear_caches()


def _write_text(e) -> str:
    return (f"Find every overdue record in {e['db']['id']}. Draft a follow-up note for each "
            f"into {e['fu']['id']}.")


def _read_text(e) -> str:
    return f"Which records in {e['db']['id']} are overdue?"


def _client(e, who=OWNER) -> TestClient:
    return TestClient(e["app"], headers=auth_headers(who))


def _no_agent_persisted(e, c: TestClient) -> None:
    agents = e["tmp"] / "agents"
    assert not agents.is_dir() or list(agents.iterdir()) == []
    assert c.get("/agents").json()["agents"] == []
    from databridge_gateway.agents_routes import existing_ids
    assert existing_ids() == []


# ------------------------------------------------------------------------------------------------
def test_a_read_only_task_shows_its_plan_runs_once_and_answers_with_citations(estate):
    e = estate
    e["rt"].harness = scripted_harness(read_script(e["db"]["id"], e["gw"].rows))
    with _client(e) as c:
        r = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["runnable"] is True, body
        plan = body["plan"]
        # the plan BEFORE anything runs: sources, no writes, the compiler's own grants
        assert e["db"]["id"] in plan["sources_read"] and plan["writes"] == []
        assert plan["trigger"] == "manual" and plan["mode"] == "read_only"
        assert plan["grants"]["purpose"] == e["purpose"]
        assert plan["grants"]["golden_set"] == e["golden"]
        assert {g["source_id"] for g in plan["grants"]["capabilities"]} >= \
            {e["db"]["id"].replace("-", "_")}
        assert e["gw"].calls == []                                  # nothing read yet

        r = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        assert r.status_code == 200, r.text
        task = r.json()
        assert task["state"] == "done", task["run"]["answer"]["markdown"]
        assert task["held"] == 0 and task["actions"] == []
        run = task["run"]
        assert run["trigger"]["type"] == "one_time" and run["trigger"]["by"] == OWNER
        claims = run["answer"]["claims"]
        assert claims and all(cl["supported"] and cl["citations"] for cl in claims), claims
        assert run["answer"]["citations"], run["answer"]
        assert run["outcome"]["reason_code"] == "answered", run["outcome"]
        assert [x[0] for x in e["gw"].calls] == ["query"] and e["gw"].writes() == []
        # the draft is consumed: one plan, one run
        again = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        assert again.status_code == 404
        _no_agent_persisted(e, c)


def test_a_write_is_held_and_lands_only_after_its_owner_approves(estate):
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        assert body["runnable"] is True, body
        writes = body["plan"]["writes"]
        assert writes and {w["automation_mode"] for w in writes} == {"human_required"}
        assert {w["connection"] for w in writes} == {e["fu"]["id"]}
        action = writes[0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))

        task = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()
        assert task["state"] == "waiting_for_approval", task      # never "done" while held
        assert task["held"] == 1 and task["executed"] == 0
        [a] = task["actions"]
        assert (a["name"], a["status"], a["automation_mode"]) == \
            (action, "proposed", "human_required")
        assert e["gw"].writes() == []                             # held: nothing written
        tid = task["task_id"]

        # another principal: the same 404 as a task that does not exist, and nothing decided
        other = auth_headers(OTHER)
        missing = c.get("/intelligence/one-time/once_nope", headers=other)
        for path in (f"/intelligence/one-time/{tid}",):
            got = c.get(path, headers=other)
            assert got.status_code == 404 and got.json() == missing.json()
        r = c.post(f"/intelligence/one-time/{tid}/actions/a1/approve", headers=other)
        assert r.status_code == 404 and e["gw"].writes() == []

        r = c.post(f"/intelligence/one-time/{tid}/actions/a1/approve",
                   json={"by": "mallory"})
        assert r.status_code == 200, r.text
        done = r.json()
        assert done["decided_by"] == OWNER
        assert done["state"] == "done" and done["held"] == 0 and done["executed"] == 1
        assert done["actions"][0]["status"] == "executed" and done["actions"][0]["by"] == OWNER
        assert e["gw"].writes() == [("call_tool", e["fu"]["id"], e["gw"].write_tool,
                                     a["args"])]
        assert c.post(f"/intelligence/one-time/{tid}/actions/a1/approve").status_code == 409
        assert len(e["gw"].writes()) == 1                           # exactly once
        _no_agent_persisted(e, c)


def test_a_rejected_write_never_lands(estate):
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        task = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()
        r = c.post(f"/intelligence/one-time/{task['task_id']}/actions/a1/reject",
                   json={"reason": "not now"})
        assert r.status_code == 200, r.text
        assert r.json()["actions"][0]["status"] == "rejected"
        assert c.post(f"/intelligence/one-time/{task['task_id']}/actions/a1/approve"
                      ).status_code == 409
        assert e["gw"].writes() == []


@pytest.mark.parametrize("channel", ["Slack", "email"])
def test_an_unconnected_channel_is_refused_honestly_and_nothing_runs(estate, channel):
    e = estate
    with _client(e) as c:
        text = f"Send a {channel} message listing every overdue record in {e['db']['id']}"
        body = c.post("/intelligence/one-time/compile", json={"text": text}).json()
        assert body["runnable"] is False and body["plan"] is None
        # the agent compiler's own questions for the same words (not re-worded here)
        agent = c.post("/agents/compile", json={"text": text}).json()
        want = {q["id"] for q in agent["open_questions"] if not q["id"].startswith("mode:")}
        assert want and want <= {q["id"] for q in body["open_questions"]}
        r = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NOT_RUNNABLE"
    assert e["gw"].calls == []
    root = e["tmp"] / "one_time"
    assert not root.is_dir() or list(root.iterdir()) == []


def test_an_unauthenticated_caller_is_refused_everywhere(estate):
    e = estate
    c = TestClient(e["app"])
    for method, path, body in (
            ("post", "/intelligence/one-time/compile", {"text": _read_text(e)}),
            ("post", "/intelligence/one-time/run", {"draft_id": "x"}),
            ("get", "/intelligence/one-time/once_x", None),
            ("post", "/intelligence/one-time/once_x/actions/a1/approve", None),
            ("post", "/intelligence/one-time/once_x/actions/a1/reject", None)):
        r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 401, (path, r.status_code)
    assert e["gw"].calls == []


def test_a_plan_is_run_only_by_the_principal_who_previewed_it(estate):
    """R-027 ONE (Codex round 1 #4): another principal's draft and a draft that does not exist get
    the SAME status and the byte-identical body, so a draft id is not an existence oracle."""
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        r = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]},
                   headers=auth_headers(OTHER))
        missing = c.post("/intelligence/one-time/run", json={"draft_id": "once-draft-nope"},
                         headers=auth_headers(OTHER))
        assert r.status_code == missing.status_code == 404
        assert r.content == missing.content
        assert body["draft_id"] not in r.text
        assert e["gw"].calls == []
        # ...and the owner's draft is still there to run: the refusal consumed nothing
        ok = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        assert ok.status_code == 200, ok.text


def test_a_one_time_draft_cannot_be_saved_as_an_agent_by_its_id(estate):
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        r = c.post("/agents", json={"draft_id": body["draft_id"]})
        assert r.status_code == 404
        _no_agent_persisted(e, c)


def test_the_run_is_in_activity_as_a_one_time_task_for_its_owner_only(estate, monkeypatch):
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        task = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()
        tid, run_id = task["task_id"], task["run_id"]

        def rows(who):
            items = c.get("/activity", headers=auth_headers(who)).json()["items"]
            # R-028 CONS #8: a one-time task's run is its own kind, never a plain agent run
            return [i for i in items if i.get("run_id") == run_id
                    and i["kind"] == "one_time_task"]

        mine = rows(OWNER)
        assert len(mine) == 1, mine
        row = mine[0]
        assert row["trigger"]["type"] == "one_time"
        assert row["run_owner"] == tid and row["started_by"]["principal_id"] == OWNER
        assert row["task_id"] == tid
        assert row["actions"], row
        assert rows(OTHER) == []                                    # owner-only
        assert [i for i in c.get("/activity", headers=auth_headers(OTHER)).json()["items"]
                if i.get("run_id") == run_id] == []                 # not even its approval
        held = [i for i in c.get("/activity").json()["items"]
                if i.get("run_id") == run_id and i["kind"] == "approval"]
        assert held, "the held write is an Activity row too"
        one = c.get(f"/activity/runs/{run_id}", params={"kind": "agent", "owner": tid})
        assert one.status_code == 200, one.text
        got = one.json()
        assert "delegation_tree" in got
        assert [e_["event"] for e_ in got["action_history"]] == ["proposed"]
        assert c.get(f"/activity/runs/{run_id}", params={"kind": "agent", "owner": tid},
                     headers=auth_headers(OTHER)).status_code == 404
        # approving shows up in the same record
        c.post(f"/intelligence/one-time/{tid}/actions/a1/approve")
        got = c.get(f"/activity/runs/{run_id}", params={"kind": "agent", "owner": tid}).json()
        assert [e_["event"] for e_ in got["action_history"]] == ["proposed", "approved",
                                                                  "executed"]


# ------------------------------------------------------------------------------------------------
# R-027 ONE, Codex round 1: a failed run, the draft store
# ------------------------------------------------------------------------------------------------
SECRET = "C:/secret/connector-path token=sk-test-0000"


def _boom(*_a, **_k):
    raise RuntimeError(f"connector exploded at {SECRET}")


def test_a_failed_run_is_recorded_failed_with_a_fixed_public_error(estate, monkeypatch, caplog):
    """#3: a run that raises used to leave the task `not_run` (and un-retryable) and returned
    `str(exc)`. It is now `failed`, the caller gets a fixed message, and the detail is logged."""
    import logging

    import databridge_agent_runtime as ar

    e = estate
    monkeypatch.setattr(ar, "run_agent", _boom)
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        tid = body["plan"]["task_id"]
        with caplog.at_level(logging.ERROR, logger="databridge.gateway.one_time"):
            r = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        assert r.status_code == 500
        assert r.json()["refusal"]["code"] == "RUN_FAILED"
        assert SECRET not in r.text and "RuntimeError" not in r.text
        assert SECRET in caplog.text                     # the detail is on the server, not the wire
        task = c.get(f"/intelligence/one-time/{tid}").json()
        assert task["state"] == "failed", task
        assert task["error_ref"] and task["error_ref"] in r.text and task["error_ref"] in caplog.text
        assert SECRET not in json.dumps(task)
        # the same public message whatever the exception said
        monkeypatch.setattr(ar, "run_agent", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
        body2 = c.post("/intelligence/one-time/compile", json={"text": _read_text(e) + " Now."}).json()
        r2 = c.post("/intelligence/one-time/run", json={"draft_id": body2["draft_id"]})
        assert r2.status_code == 500
        assert r2.json()["refusal"]["message"] == r.json()["refusal"]["message"]


def test_a_task_is_running_while_it_runs(estate, monkeypatch):
    import databridge_agent_runtime as ar

    e = estate
    seen = {}
    with _client(e) as c:
        def peek(*_a, **_k):
            seen["during"] = c.get(f"/intelligence/one-time/{tid}").json()["state"]
            raise RuntimeError("stop here")

        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        tid = body["plan"]["task_id"]
        monkeypatch.setattr(ar, "run_agent", peek)
        c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
    assert seen["during"] == "running"


def test_a_failed_run_can_be_retried_once_safely_by_its_owner_only(estate, monkeypatch):
    import databridge_agent_runtime as ar

    e = estate
    real = ar.run_agent
    e["rt"].harness = scripted_harness(read_script(e["db"]["id"], e["gw"].rows))
    monkeypatch.setattr(ar, "run_agent", _boom)
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        tid = body["plan"]["task_id"]
        assert c.post("/intelligence/one-time/run",
                      json={"draft_id": body["draft_id"]}).status_code == 500
        monkeypatch.setattr(ar, "run_agent", real)
        # another principal: the same 404 as a task that does not exist
        other = auth_headers(OTHER)
        foreign = c.post(f"/intelligence/one-time/{tid}/retry", headers=other)
        missing = c.post("/intelligence/one-time/once_nope/retry", headers=other)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.content == missing.content
        assert e["gw"].calls == []
        r = c.post(f"/intelligence/one-time/{tid}/retry")
        assert r.status_code == 200, r.text
        task = r.json()
        assert task["state"] == "done" and task["run_id"], task
        assert task["run"]["trigger"]["by"] == OWNER
        assert [x[0] for x in e["gw"].calls] == ["query"]
        # a task that did not fail is not retried
        again = c.post(f"/intelligence/one-time/{tid}/retry")
        assert again.status_code == 409 and again.json()["refusal"]["code"] == "NOT_RETRYABLE"


def test_a_run_that_fails_after_proposing_keeps_its_run_id_and_is_not_retried(estate, monkeypatch):
    """A crash AFTER the runtime allocated a run and proposed a write: the run id is kept so the
    held write can still be decided, and a retry is refused (it would propose the write twice)."""
    import databridge_agent_runtime as ar

    e = estate
    real = ar.run_agent

    def run_then_crash(*a, **k):
        real(*a, **k)
        raise RuntimeError(f"crashed after the run at {SECRET}")

    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        monkeypatch.setattr(ar, "run_agent", run_then_crash)
        r = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        assert r.status_code == 500 and SECRET not in r.text
        tid = body["plan"]["task_id"]
        task = c.get(f"/intelligence/one-time/{tid}").json()
        assert task["state"] == "failed" and task["run_id"], task
        assert [a["status"] for a in task["actions"]] == ["proposed"]
        assert task["held"] == 1
        retry = c.post(f"/intelligence/one-time/{tid}/retry")
        assert retry.status_code == 409 and retry.json()["refusal"]["code"] == "RETRY_UNSAFE"
        # the held write is still the owner's to decide
        monkeypatch.setattr(ar, "run_agent", real)
        ok = c.post(f"/intelligence/one-time/{tid}/actions/a1/reject", json={"reason": "no"})
        assert ok.status_code == 200, ok.text
        assert e["gw"].writes() == []


def test_a_missing_run_record_is_refused_without_a_server_path(estate):
    """`find_run`'s FileNotFoundError names the runs directory; it used to be returned verbatim."""
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        task = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()
        [record] = list((e["tmp"] / "runs").rglob(f"{task['run_id']}.json"))
        record.unlink()
        r = c.post(f"/intelligence/one-time/{task['task_id']}/actions/a1/approve")
        assert r.status_code == 404 and r.json()["refusal"]["code"] == "UNKNOWN_ACTION"
        for form in (str(e["tmp"]), str(e["tmp"]).replace("\\", "/"), str(e["tmp"]).replace("\\", "\\\\")):
            assert form not in r.text
        assert "runs" not in r.json()["refusal"]["message"]


def test_one_principal_cannot_evict_another_principals_drafts():
    """#5: the cap is PER PRINCIPAL. Filling your own quota evicts only your own oldest draft."""
    from databridge_gateway import one_time_routes as otr

    store = otr._Drafts()
    theirs = store.keep(object(), OTHER)
    mine = [store.keep(object(), OWNER) for _ in range(otr.MAX_DRAFTS_PER_PRINCIPAL + 3)]
    assert store.get(theirs) is not None, "another principal's draft was evicted"
    assert store.get(mine[0]) is None, "the cap is not enforced on the principal who filled it"
    assert all(store.get(d) is not None for d in mine[-otr.MAX_DRAFTS_PER_PRINCIPAL:])


def test_a_draft_expires():
    from databridge_gateway import one_time_routes as otr

    clock = {"t": 1_000_000.0}
    store = otr._Drafts(clock=lambda: clock["t"])
    d = store.keep(object(), OWNER)
    clock["t"] += otr.DRAFT_TTL_SECONDS - 1
    assert store.get(d) is not None
    clock["t"] += 2
    assert store.get(d) is None and store.pop(d) is None


# ------------------------------------------------------------------------------------------------
# R-027 ONE, Codex round 2
# ------------------------------------------------------------------------------------------------
def _crash_after_proposing(e, c, monkeypatch, *, drop_record=True, strip_pointers=False):
    """A write task whose run proposes a write and then fails, optionally before its run record
    was written (the action log is then the only trace)."""
    import databridge_agent_runtime as ar

    real = ar.run_agent

    def run_then_crash(*a, **k):
        rec = real(*a, **k)
        if drop_record:
            [p] = list((e["tmp"] / "runs").rglob(f"{rec.run_id}.json"))
            p.unlink()
        if strip_pointers:
            [log] = list((e["tmp"] / "runs").rglob(f"{rec.run_id}.actions.jsonl"))
            lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
            for ev in lines:
                ev.pop("derived_from", None)
            log.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        raise RuntimeError("crashed after proposing")

    body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
    action = body["plan"]["writes"][0]["name"]
    e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
    monkeypatch.setattr(ar, "run_agent", run_then_crash)
    assert c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).status_code == 500
    monkeypatch.setattr(ar, "run_agent", real)
    return body["plan"]["task_id"]


def test_a_logged_write_is_shown_only_with_its_pointers(estate, monkeypatch):
    """Round 2 #1 (CRITICAL): with no run record, the write comes from the action log. Its args
    are shown only with the pointers the log recorded for them, never with an empty list."""
    e = estate
    with _client(e) as c:
        tid = _crash_after_proposing(e, c, monkeypatch)
        task = c.get(f"/intelligence/one-time/{tid}").json()
        [a] = task["actions"]
        assert a["status"] == "proposed"
        assert a["args"], a
        assert a["derived_from"], "args were emitted without the pointers behind them"
        assert a.get("args_withheld") is not True
        for p in a["derived_from"]:
            assert p["source_id"] and p["kind"]


def _is_withheld(value, reason: str) -> bool:
    """The standard envelope (R-028 #12): ``{status, reason, pointers}``, never ``null`` + flag."""
    return (isinstance(value, dict) and value.get("status") == "withheld"
            and value.get("reason") == reason and value.get("pointers") == [])


def test_a_logged_write_without_pointers_is_withheld(estate, monkeypatch):
    e = estate
    with _client(e) as c:
        tid = _crash_after_proposing(e, c, monkeypatch, strip_pointers=True)
        [a] = c.get(f"/intelligence/one-time/{tid}").json()["actions"]
        assert _is_withheld(a["args"], "no_valid_pointer"), a["args"]
        assert "args_withheld" not in a and a["derived_from"] == []
        assert _is_withheld(a["ungrounded_values"], "no_valid_pointer")


@pytest.mark.parametrize("pointers", [[], [{"not": "a pointer"}]], ids=["none", "invalid"])
def test_a_recorded_write_without_valid_pointers_is_withheld(estate, pointers):
    """R-028 #1 (CRITICAL): the RECORD-backed path returned the proposal's args whatever pointers
    the run record carried. Both paths now validate: no pointers, or any invalid one, withholds
    the args with the standard envelope."""
    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        task = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()
        [shown] = task["actions"]
        assert isinstance(shown["args"], dict) and "status" not in shown["args"]
        assert shown["derived_from"], "the healthy path shows args WITH their pointers"
        [rec] = list((e["tmp"] / "runs").rglob(f"{task['run_id']}.json"))
        data = json.loads(rec.read_text(encoding="utf-8"))
        data["actions"][0]["pointers"] = pointers
        rec.write_text(json.dumps(data), encoding="utf-8")
        [a] = c.get(f"/intelligence/one-time/{task['task_id']}").json()["actions"]
        assert _is_withheld(a["args"], "no_valid_pointer"), a["args"]
        assert a["derived_from"] == [] and "args_withheld" not in a
        for leak in (e["gw"].rows[0]["record_no"],):
            assert leak not in json.dumps(a["args"])


def test_a_compile_error_is_a_fixed_message_with_a_reference(estate, monkeypatch, caplog):
    """R-028 #6: the compiler's ValueError text never reaches the caller."""
    import logging

    import databridge_automation

    def boom(*_a, **_k):
        raise ValueError(f"cannot read {SECRET}")

    monkeypatch.setattr(databridge_automation, "compile_english", boom)
    with _client(estate) as c, caplog.at_level(logging.INFO, logger="databridge.gateway.one_time"):
        r = c.post("/intelligence/one-time/compile", json={"text": "anything"})
    assert r.status_code == 422
    assert SECRET not in r.text
    body = r.json()
    assert body["refusal"]["code"] == "CANNOT_COMPILE"
    assert body["error_ref"] and body["error_ref"] in caplog.text and SECRET in caplog.text


@pytest.mark.parametrize("runtime_code,code", [
    ("principal_not_authenticated", "APPROVER_NOT_AUTHENTICATED"),
    ("principal_not_a_person", "MACHINE_ACTOR"),
    ("authority_changed", "DECISION_STALE"),
    ("not_claimable", "DECISION_ALREADY_FINAL"),
    ("run_locked", "ACTION_REFUSED"),
])
def test_a_refused_decision_uses_the_standard_codes(estate, monkeypatch, runtime_code, code):
    """R-028 #11: the runtime's closed reason maps onto the platform's standard refusal codes;
    one with no standard equivalent stays ACTION_REFUSED (the runtime reason rides beside it)."""
    import databridge_agent_runtime as ar

    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        tid = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()["task_id"]

        def raiser(*_a, **_k):
            raise ar.ActionRefused("detail", code=runtime_code)

        monkeypatch.setattr(ar, "approve", raiser)
        r = c.post(f"/intelligence/one-time/{tid}/actions/a1/approve")
    assert r.status_code == 409
    assert r.json()["refusal"]["code"] == code
    assert r.json()["reason_code"] == runtime_code


def test_retry_is_refused_for_good_once_an_attempt_proposed_a_write(estate, monkeypatch):
    """Round 2 #6: the policy is permanent, and the message says so: deciding the write does not
    make the task retryable (that would propose it again); the user describes a new task."""
    from databridge_gateway import one_time_routes as otr

    e = estate
    with _client(e) as c:
        tid = _crash_after_proposing(e, c, monkeypatch, drop_record=False)
        first = c.post(f"/intelligence/one-time/{tid}/retry")
        assert c.post(f"/intelligence/one-time/{tid}/actions/a1/reject",
                      json={"reason": "no"}).status_code == 200
        after = c.post(f"/intelligence/one-time/{tid}/retry")
        for r in (first, after):
            assert r.status_code == 409
            assert r.json()["refusal"] == {"code": "RETRY_UNSAFE", "message": otr.RETRY_UNSAFE_MESSAGE,
                                           "nearest_alternative": None}
        assert "new" in otr.RETRY_UNSAFE_MESSAGE and "first" not in otr.RETRY_UNSAFE_MESSAGE


def test_two_retries_at_once_run_the_task_once(estate, monkeypatch):
    """Round 2 #4: the failed -> running claim is atomic. A second retry arriving between the
    first one's read and its claim is refused, and the runtime is entered once."""
    import databridge_agent_runtime as ar
    from databridge_gateway import one_time_routes as otr

    e = estate
    real = ar.run_agent
    e["rt"].harness = scripted_harness(read_script(e["db"]["id"], e["gw"].rows))
    with _client(e) as c:
        monkeypatch.setattr(ar, "run_agent", _boom)
        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        tid = body["plan"]["task_id"]
        c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
        calls, nested = [], {}

        def counting(*a, **k):
            calls.append(1)
            return real(*a, **k)

        monkeypatch.setattr(ar, "run_agent", counting)
        real_check = otr._has_proposals

        def interleave(task):
            if "status" not in nested:                     # the second request lands right here
                nested["status"] = None
                nested["status"] = c.post(f"/intelligence/one-time/{tid}/retry").status_code
            return real_check(task)

        monkeypatch.setattr(otr, "_has_proposals", interleave)
        r = c.post(f"/intelligence/one-time/{tid}/retry")
        assert r.status_code == 200, r.text
        assert nested["status"] == 409
        assert len(calls) == 1


def test_task_files_are_written_through_distinct_temp_names(tmp_path, monkeypatch):
    from databridge_gateway import one_time_routes as otr

    seen = []
    real = otr.os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real(src, dst)

    monkeypatch.setattr(otr.os, "replace", spy)
    otr._write_task(tmp_path, {"task_id": "x"})
    otr._write_task(tmp_path, {"task_id": "x"})
    assert len(seen) == 2 and seen[0] != seen[1]


def test_a_refused_decision_returns_a_fixed_message_and_logs_the_detail(estate, monkeypatch, caplog):
    """Round 2 #7: runtime exception text (a lock-file path, say) is logged under a reference;
    the caller gets a fixed message and the runtime's closed reason code."""
    import logging

    import databridge_agent_runtime as ar

    e = estate
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _write_text(e)}).json()
        action = body["plan"]["writes"][0]["name"]
        e["rt"].harness = scripted_harness(write_script(e["db"]["id"], action, e["gw"].rows))
        tid = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]}).json()["task_id"]
        bodies = []
        for exc in (ar.ActionRefused(f"lock file {SECRET}", code="run_locked"),
                    ValueError(f"bad value at {SECRET}")):
            def raiser(*_a, _exc=exc, **_k):
                raise _exc
            monkeypatch.setattr(ar, "approve", raiser)
            with caplog.at_level(logging.INFO, logger="databridge.gateway.one_time"):
                r = c.post(f"/intelligence/one-time/{tid}/actions/a1/approve")
            assert r.status_code == 409
            assert SECRET not in r.text
            ref = r.json()["error_ref"]
            assert ref and ref in caplog.text and SECRET in caplog.text
            bodies.append(r.json())
        assert bodies[0]["refusal"]["code"] == "ACTION_REFUSED"
        assert bodies[0]["reason_code"] == "run_locked"
        assert bodies[1]["reason_code"] == "refused"
