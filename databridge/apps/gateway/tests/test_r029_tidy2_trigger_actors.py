"""R-029 TIDY2 item 5: a run's trigger carries the FULL common actor object, not a bare name.

`trigger.by` was a bare string on run records (the agent runtime's "run now", the one-time task,
an adapter agent's sync, a setup step), so Activity served ``authenticated_by`` / ``dev_only`` as
``not_recorded_by_producer``. Each producer now stores ``trigger.actor`` — the actor object of the
signed-in principal (`public_errors.actor_of`, CONS standard 1) — next to ``by``, and Activity
reads it. Expectations are derived from the principal the request signed in as.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import auth_headers
from test_one_time_tasks import (OWNER, _client, _read_text, estate,  # noqa: F401  (fixture)
                                 read_script, scripted_harness as ot_harness)
from test_platform_agents import (_mount, env, followup_script,  # noqa: F401  (fixture)
                                  scripted_harness, write_followup_agent)


def _expected(pid: str, dev: bool = False) -> dict:
    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.public_errors import actor_of

    return actor_of(AuthenticatedPrincipal(principal_id=pid, dev_mode=dev))


def test_run_now_stores_the_full_actor_on_the_trigger(env):  # noqa: F811
    from databridge_gateway.activity_routes import _started_by
    from databridge_gateway.agent_ownership import record_owner

    app, rt, _gw = _mount(env)
    agent_id = write_followup_agent(env / "agents")
    record_owner(agent_id, "analyst-7")
    rt.harness = scripted_harness(followup_script)
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        r = c.post(f"/agents/{agent_id}/run", json={"wait": True, "by": "mallory"})
    assert r.status_code == 200, r.text
    trig = r.json()["record"]["trigger"]
    want = _expected("analyst-7")
    assert want["authenticated_by"] and want["principal_type"]               # non-vacuous
    assert trig["by"] == "analyst-7" and trig["actor"] == want
    # Activity serves it, not "not recorded"
    started = _started_by(trig)
    assert started["authenticated_by"] == want["authenticated_by"]
    assert started["dev_only"] is False


def test_a_one_time_task_stores_the_full_actor_on_the_trigger(estate):  # noqa: F811
    e = estate
    e["rt"].harness = ot_harness(read_script(e["db"]["id"], e["gw"].rows))
    with _client(e) as c:
        body = c.post("/intelligence/one-time/compile", json={"text": _read_text(e)}).json()
        r = c.post("/intelligence/one-time/run", json={"draft_id": body["draft_id"]})
    assert r.status_code == 200, r.text
    trig = r.json()["run"]["trigger"]
    assert trig["by"] == OWNER and trig["actor"] == _expected(OWNER)


def test_an_adapter_sync_stores_the_full_actor_on_the_trigger(env, monkeypatch):  # noqa: F811
    from databridge_gateway import adapter_agents_routes as aar
    from databridge_gateway.platform_runtime import platform_of

    app, _rt, _gw = _mount(env)
    seen: list[dict] = []

    class _Summary:
        def as_dict(self):
            return {"status": "ok"}

    rt = platform_of(app)
    monkeypatch.setattr(rt.manager, "sync", lambda agent_id, trigger=None, **_k:
                        (seen.append(dict(trigger)), _Summary())[1])
    job = rt.jobs.start("adapter_sync", "keeper-x", by="analyst-7")
    aar._sync_job(app, "keeper-x", job["job_id"], "analyst-7", actor=_expected("analyst-7"))
    assert seen and seen[0]["by"] == "analyst-7" and seen[0]["actor"] == _expected("analyst-7")


def test_a_setup_step_stores_the_requesters_full_actor_on_the_trigger(tmp_path):
    from types import SimpleNamespace

    from databridge_adapter_agents.setup import record_step
    from databridge_core.decision_ledger import DecisionRefused

    spec = SimpleNamespace(id="keeper-x", model_dump=lambda **_k: {"id": "keeper-x"})
    want = _expected("analyst-7")
    # an agent step taken at a person's request: the record's actor is the agent, the trigger's
    # actor is the person who asked
    rec = record_step(tmp_path, spec, step="discover", status="started", by="analyst-7",
                      reason="r", requested_by=want)
    assert rec["trigger"]["by"] == "analyst-7" and rec["trigger"]["actor"] == want
    assert rec["actor"]["principal_id"] != "analyst-7"                     # the agent's own
    # a person's decision: the trigger names that person too
    rec = record_step(tmp_path, spec, step="approve_plan", status="completed", by="analyst-7",
                      reason="r", actor="person", who=want)
    assert rec["trigger"]["actor"] == want == rec["actor"]
    # the requester must be the named person
    try:
        record_step(tmp_path, spec, step="discover", status="started", by="analyst-7",
                    reason="r", requested_by=_expected("mallory"))
    except DecisionRefused as exc:
        assert exc.code == "APPROVER_NOT_AUTHENTICATED"
    else:
        raise AssertionError("a requester other than `by` was recorded")
