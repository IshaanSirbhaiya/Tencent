"""`/activity` — one read-only feed merged from what the producers already wrote.

Every fixture is written by the PRODUCER'S OWN writer where one exists (`write_run` + `ActionLog`
for agent runs, `FiringLog.append` for firings, `AdapterAgentRegistry.create` for adapter-agent
ownership, `agent_ownership.record_owner` for workflow-agent ownership, `AdaptStore.append` for
drift, `ProposalLog.append` for ontology proposals), and the federated record is produced by running
`databridge_federated`'s service offline. Every expectation is DERIVED from the fixtures written
here — never a number typed into an assertion.

Codex f1 (backend half, ACT2): ownership fails closed (an ownerless or ambiguous record is
operator-only), redaction is an allow-list, reads are bounded by the page, joins keep the run's
namespace, and a record you may not see is indistinguishable from one that does not exist.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import auth_headers

_KIT = Path(__file__).resolve().parents[3] / "packages" / "federated" / "tests"
for _p in (str(_KIT), str(_KIT.parent)):
    if _p not in sys.path:
        sys.path.append(_p)          # appended: never shadow this package's own `conftest`

from fed_testkit import (  # noqa: E402
    CRM,
    FOUNDER_PLAN,
    FOUNDER_Q,
    env,  # noqa: F401 - fixture
    scripted_harness,
    world,  # noqa: F401 - fixture
)

from databridge_gateway import activity_routes as act  # noqa: E402
from databridge_gateway.activity_routes import (  # noqa: E402
    ACTIVITY_KINDS,
    RUN_FEED_KINDS,
    ActivitySources,
    delegation_tree,
    register_activity_routes,
    sort_key,
)
from databridge_gateway.activity_actor import (  # noqa: E402
    FREE_TEXT,
    MACHINE_ACTOR,
    NOT_IN_ALLOW_LIST,
    SENSITIVE_FIELD,
    SQL_WITH_LITERALS,
    absent,
    make_actor,
    withheld,
)
from databridge_gateway.trace_recorder import recorder_for  # noqa: E402

OWNER = "analyst-7"
OTHER = "fcc-3"
OPERATOR = "audit-1"
T0 = dt.datetime(2026, 9, 20, 8, 0, tzinfo=dt.timezone.utc)
#: what the runtime metered for run-owner (the record header EF4 added) — deliberately NOT the
#: trace's figure, so a test can tell which of the two the feed served
OWNER_METERED = {"tokens": 456, "cost_usd": 0.0123}
NOBODY: frozenset[str] = frozenset()       # an ownerless / ambiguous record: operator-only


def _iso(minutes: int) -> str:
    return (T0 + dt.timedelta(minutes=minutes)).isoformat(timespec="milliseconds")


def _epoch(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _cite_nothing(_payload):
    return {"claims": []}


# ------------------------------------------------------------------------------------------------
# fixtures written by the producers' own writers
# ------------------------------------------------------------------------------------------------
def _agent_run(runs: Path, agent: str, run_id: str, *, started: int, ended: int | None,
               by: str | None, trig_type: str, answer_by: dict | None,
               actions: list[tuple[str, list[dict]]] = (),
               metered: dict | None = None) -> Path:
    from databridge_agent_runtime import ActionLog, RunRecord, run_path
    from databridge_agent_runtime.runs import actions_path, write_run

    entries = []
    for name, events in actions:
        status = events[-1]["event"] if events[-1]["event"] in (
            "approved", "executed", "rejected", "reversed") else "proposed"
        entries.append({"name": name, "automation_mode": "human_required", "status": status,
                        "by": next((e.get("by") for e in reversed(events) if e.get("by")), None)})
    rec = RunRecord.model_validate({
        "run_id": run_id, "spec_digest": "sha256:test",
        "trigger": {"type": trig_type, "by": by, "question": f"question for {run_id}"},
        "started": _iso(started), "ended": _iso(ended) if ended is not None else None,
        "status": "completed", "models_used": ["fake/agent"] if answer_by else [],
        "actions": entries,
        "answer": {"markdown": "an answer", "produced_by": answer_by},
        **(metered or {})})          # EF4 §5.8 header: the runtime's own tokens / cost_usd
    path = run_path(run_id, agent=agent, runs_dir=runs)
    write_run(rec, path)
    # a record is written when its run ends: its file time is that, not the time the test ran
    os.utime(path, (_epoch(_iso(ended if ended is not None else started)),) * 2)
    log = ActionLog(actions_path(path))
    for i, (name, events) in enumerate(actions):
        for e in events:
            log.append({"action_id": f"a{i + 1}", "name": name, **e})
    if actions:
        last = max(e["at"] for _n, evs in actions for e in evs)
        os.utime(actions_path(path), (_epoch(last),) * 2)
    return path


def _w(written: dict, kind: str, ident: str, at: str, *viewers: str) -> None:
    written[kind].append({"id": ident, "at": at, "viewers": frozenset(viewers)})


@pytest.fixture()
def fx(world, monkeypatch):                             # noqa: F811 - pytest fixture
    """Every record kind, for two principals plus ownerless and ambiguous records.
    Returns (app, what-was-written, handles)."""
    from databridge_adapter_agents.adapt import AdaptStore
    from databridge_adapter_agents.proposals import ProposalLog
    from databridge_adapter_agents.registry import AdapterAgentRegistry
    from databridge_automation.engine import FiringLog
    from databridge_workflow_spec import AdapterAgentSpec

    from databridge_gateway import agent_ownership

    monkeypatch.setenv("WALNUT_AGENTS_DIR", str(world.root / "agents"))
    monkeypatch.delenv(agent_ownership.OPERATORS_ENV, raising=False)
    runs = world.runs_dir
    aa_dir = world.root / "aa_registry"
    firings_path = world.root / "automation" / "firings.jsonl"
    # this fixture names only the run stores (`ActivitySources` without the decision roots), so
    # it covers the run-feed kinds; the decision kinds are covered in test_activity_consistency
    written: dict[str, list[dict]] = {k: [] for k in RUN_FEED_KINDS}

    # -- ownership: OWNER saved the workflow agent `followup` (BRAIN's owner record) and created
    #    the adapter agent `owned_aa`. `legacy` and `orphan_aa` have no recorded owner. ----------
    agent_ownership.record_owner("followup", OWNER)
    reg = AdapterAgentRegistry(aa_dir)
    reg.create(AdapterAgentSpec.model_validate({
        "id": "owned_aa", "title": "owned", "created_from": "keep crm right",
        "connections": [CRM], "extract": {"pack": "workspace"}, "status": "active"}), by=OWNER)

    # -- agent runs of OWNER's agent: OWNER's manual run with an approval chain; OTHER's manual
    #    run (OTHER started it, so OTHER sees it too); a scheduled run nobody started (the agent's
    #    owner sees it). And a scheduled run of an agent with NO recorded owner: operator-only. ----
    approval_events = [
        {"event": "proposed", "at": _iso(11), "tool": "source.write:crm.insert",
         "automation_mode": "human_required",
         "produced_by": {"agent": "followup", "model": "fake/agent", "run_id": "run-owner"}},
        {"event": "approved", "by": OWNER, "at": _iso(12), "idempotency_key": "run-owner/a1"},
        {"event": "executed", "by": OWNER, "at": _iso(13), "detail": "executed",
         "result_pointers": []},
    ]
    _agent_run(runs, "followup", "run-owner", started=10, ended=11, by=OWNER, trig_type="manual",
               answer_by={"agent": "followup", "model": "fake/agent", "task_class": "agent"},
               actions=[("insert_follow_up_task", approval_events)],
               metered=OWNER_METERED)
    _w(written, "agent_run", "followup/run-owner", _iso(10), OWNER)
    _w(written, "approval", "followup/run-owner/a1", _iso(13), OWNER)
    written["approval"][-1].update(decided_by=OWNER, outcome="executed")
    _agent_run(runs, "followup", "run-other", started=20, ended=21, by=OTHER, trig_type="manual",
               answer_by={"agent": "followup", "model": "fake/agent"})
    _w(written, "agent_run", "followup/run-other", _iso(20), OWNER, OTHER)
    _agent_run(runs, "followup", "run-scheduled", started=30, ended=None, by=None,
               trig_type="schedule", answer_by=None)
    _w(written, "agent_run", "followup/run-scheduled", _iso(30), OWNER)
    _agent_run(runs, "legacy", "run-legacy", started=35, ended=36, by=None,
               trig_type="schedule", answer_by=None)
    _w(written, "agent_run", "legacy/run-legacy", _iso(35))

    # -- adapter-agent sync records (the manager's `_write_record` shape): one of OWNER's adapter
    #    agent, one of an adapter agent whose creator was never recorded ------------------------
    for agent, run_id, minute, viewers in (
            ("owned_aa", "20260920T084000Z-abc12345", 40, (OWNER,)),
            ("orphan_aa", "20260920T084500Z-def67890", 45, ())):
        sync_dir = runs / "adapter_agent" / agent
        sync_dir.mkdir(parents=True, exist_ok=True)
        (sync_dir / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "kind": "adapter_agent", "agent_id": agent,
            "trigger": {"type": "schedule"}, "started": _iso(minute), "ended": _iso(minute + 2),
            "status": "partial", "reason": "one connection failed", "steps": [], "actions": [],
            "answer": None, "produced_by": {"agent": f"adapter_agent:{agent}",
                                            "at": _iso(minute + 2)},
            "models_used": [], "coverage": {"searched": [CRM], "skipped": [], "reasons": {}},
            "proposals": {"filed": [{"evidence": [{"text": "secret excerpt"}]}]}}),
            encoding="utf-8")
        _w(written, "adapter_sync", f"{agent}/{run_id}", _iso(minute), *viewers)

    # -- R-028 CONS #8: Set-up steps (written by `setup.record_step` beside the syncs) are their
    #    OWN kind, never `adapter_sync`: a step OWNER decided on the owned agent, and a step the
    #    orphan agent proposed (no recorded creator: operator-only) ------------------------------
    from databridge_adapter_agents.setup import record_step
    for agent, actor, viewers in (("owned_aa", "person", (OWNER,)), ("orphan_aa", "agent", ())):
        spec = AdapterAgentSpec.model_validate({
            "id": agent, "title": agent, "created_from": "set up crm", "connections": [CRM],
            "extract": {"pack": "workspace"}, "status": "draft"})
        rec = record_step(runs, spec, step="approve_plan" if actor == "person" else "propose",
                          status="completed", by=OWNER, reason="the crawl plan", actor=actor)
        _w(written, "adapter_setup", f"{agent}/{rec['run_id']}", rec["started"], *viewers)

    # -- R-028 CONS #8: one-time task runs (`trigger.type: one_time`) are their own kind: OWNER's
    #    task, and a task whose runner was not recorded (operator-only) -------------------------
    _agent_run(runs, "once_owned", "run-once-owned", started=56, ended=57, by=OWNER,
               trig_type="one_time", answer_by=None)
    _w(written, "one_time_task", "once_owned/run-once-owned", _iso(56), OWNER)
    _agent_run(runs, "once_orphan", "run-once-orphan", started=58, ended=59, by=None,
               trig_type="one_time", answer_by=None)
    _w(written, "one_time_task", "once_orphan/run-once-orphan", _iso(58))

    # -- automation firings: a manual one by OWNER that produced run-owner, a scheduled skip of
    #    OWNER's adapter agent, and a scheduled firing of the ownerless agent ---------------------
    log = FiringLog(firings_path)
    key = "followup:manual:k1"
    log.append({"firing_id": "f1", "idempotency_key": key, "spec_id": "followup",
                "spec_kind": "agent", "trigger": "manual", "fired_at": _iso(9),
                "recorded_at": _iso(9), "status": "pending", "attempt": 1,
                "trigger_info": {"by": OWNER, "type": "manual"}})
    log.append({"firing_id": "f1", "idempotency_key": key, "spec_id": "followup",
                "spec_kind": "agent", "trigger": "manual", "fired_at": _iso(9),
                "recorded_at": _iso(11), "status": "succeeded", "attempt": 1,
                "result": "run-owner"})
    _w(written, "firing", f"agent/followup/{key}", _iso(9), OWNER)
    skey = "owned_aa:sha:schedule:x"
    log.append({"firing_id": "f2", "idempotency_key": skey, "spec_id": "owned_aa",
                "spec_kind": "adapter_agent", "trigger": "schedule", "fired_at": _iso(50),
                "recorded_at": _iso(50), "status": "skipped", "reason": "missed"})
    _w(written, "firing", f"adapter_agent/owned_aa/{skey}", _iso(50), OWNER)
    lkey = "legacy:sha:schedule:y"
    log.append({"firing_id": "f3", "idempotency_key": lkey, "spec_id": "legacy",
                "spec_kind": "agent", "trigger": "schedule", "fired_at": _iso(55),
                "recorded_at": _iso(55), "status": "succeeded", "result": "run-legacy"})
    _w(written, "firing", f"agent/legacy/{lkey}", _iso(55))

    # -- drift + ontology proposals of the owned and the orphan adapter agent ---------------------
    store = AdaptStore(lambda: aa_dir / "_adapt")
    proposals = ProposalLog(aa_dir / "_proposals" / "ontology_proposals.jsonl")
    for agent, sync_id, n, viewers in (("owned_aa", "20260920T084000Z-abc12345", 1, (OWNER,)),
                                       ("orphan_aa", "20260920T084500Z-def67890", 2, ())):
        store.append(agent, {"adaptation_id": f"ad_{n}", "agent_id": agent,
                             "agent_run_id": sync_id, "connection": CRM,
                             "status": "pending", "at": _iso(40 + n), "created_at": _iso(40 + n),
                             "drifts": [{"kind": "renamed", "unit": "table:invoices",
                                         "detail": "column renamed", "subject_touching": False}],
                             "classification": {"d1": {"asked": True}},
                             "produced_by": {"agent": "rules", "model": None}})
        _w(written, "drift_proposal", f"{agent}/ad_{n}", _iso(40 + n), *viewers)
        proposals.append({
            "proposal_id": f"aap_{n}", "filed_at": _iso(42 + n), "status": "filed",
            "adapter_agent": agent, "axis": "node", "name": "ticket", "occurrences": 3,
            "document_count": 2, "evidence": [{"text": "secret excerpt"}],
            "produced_by": {"agent": f"adapter_agent:{agent}"}})
        _w(written, "ontology_proposal", f"aap_{n}", _iso(42 + n), *viewers)

    # -- REAL federated runs: OWNER's, and OTHER's with a refused sub-task ------------------------
    from databridge_federated import CatalogueAdmission
    app = FastAPI()
    recorder = recorder_for(app)
    h, _ = scripted_harness(FOUNDER_PLAN, _cite_nothing)
    trace = recorder.begin(surface="federated", agent="federated", owner=OWNER)
    full = world.service(harness=h).answer(FOUNDER_Q, admission=CatalogueAdmission(OWNER),
                                           trace=trace)
    h2, _ = scripted_harness(FOUNDER_PLAN, _cite_nothing)
    narrow = world.service(harness=h2).answer(
        FOUNDER_Q, admission=CatalogueAdmission(OTHER, connections={CRM}))
    for ans, who in ((full, OWNER), (narrow, OTHER)):
        rec = json.loads(next(runs.rglob(f"{ans.run_id}.json")).read_text(encoding="utf-8"))
        _w(written, "federated_run", f"{who}/{ans.run_id}", rec["started"], who)
    # -- an AMBIGUOUS federated record: written under OWNER's directory, but its asker is OTHER.
    #    Neither of them owns it for certain, so only an operator sees it. ---------------------
    narrow_rec = json.loads(next(runs.rglob(f"{narrow.run_id}.json")).read_text(encoding="utf-8"))
    ambiguous_id = f"{narrow.run_id}-amb"
    (runs / "federated" / OWNER).mkdir(parents=True, exist_ok=True)
    (runs / "federated" / OWNER / f"{ambiguous_id}.json").write_text(
        json.dumps({**narrow_rec, "run_id": ambiguous_id}), encoding="utf-8")
    _w(written, "federated_run", f"{OWNER}/{ambiguous_id}", narrow_rec["started"])

    # -- a trace that MEASURED tokens for run-owner (the runtime notes `run_record`), and two
    #    decoys written AFTER it that must never join: OTHER's trace naming the same record, and
    #    OWNER's trace naming a run with the same id in ANOTHER namespace --------------------------
    t = recorder.begin(surface="agents", agent="followup", owner=OWNER)
    t.note(key="run_record", text=str(runs / "agent" / "followup" / "run-owner.json"))
    t.cost(llm_tokens=123, wall_ms=5)
    t.close("answered")
    decoy_owner = recorder.begin(surface="agents", agent="followup", owner=OTHER)
    decoy_owner.note(key="run_record", text=str(runs / "agent" / "followup" / "run-owner.json"))
    decoy_owner.cost(llm_tokens=999, wall_ms=5)
    decoy_owner.close("answered")
    decoy_ns = recorder.begin(surface="agents", agent="legacy", owner=OWNER)
    decoy_ns.note(key="run_record", text=str(runs / "agent" / "legacy" / "run-owner.json"))
    decoy_ns.cost(llm_tokens=777, wall_ms=5)
    decoy_ns.close("answered")

    app.state.activity_sources = ActivitySources(runs, firings_path, aa_dir)
    register_activity_routes(app)
    return app, written, {"full": full, "narrow": narrow, "token_trace": t.request_id,
                          "tokens": 123, "fed_trace": trace.request_id,
                          "decoys": {decoy_owner.request_id: 999, decoy_ns.request_id: 777},
                          "ambiguous": ambiguous_id}


def _visible_to(written: dict, who: str, *, operator: bool = False) -> dict[str, set[str]]:
    return {k: {w["id"] for w in rows if operator or who in w["viewers"]}
            for k, rows in written.items()}


def _all(client, **params) -> list[dict]:
    items, before = [], None
    for _ in range(500):
        q = {"limit": 3, **params, **({"before": before} if before else {})}
        body = client.get("/activity", params=q).json()
        items.extend(body["items"])
        before = body["next_cursor"]
        if not before:
            return items
    raise AssertionError("pagination did not terminate")


def _key(item: dict) -> str:
    return item["id"].split(":", 1)[1]


def _run_id_of(ident: str) -> str:
    return ident.rsplit("/", 1)[1]


@pytest.fixture()
def operators(monkeypatch):
    """Name operators in configuration, the only place an operator comes from."""
    from databridge_gateway import agent_ownership

    def set_(*pids: str) -> None:
        monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps(list(pids)))
    return set_


@pytest.fixture()
def opened(monkeypatch):
    """Every run record the feed opens, in order (Codex f1 #6: work is counted, not output)."""
    paths: list[Path] = []
    real = act._read_json

    def counting(path, *a, **kw):
        paths.append(Path(path))
        return real(path, *a, **kw)
    monkeypatch.setattr(act, "_read_json", counting)
    return paths


# ------------------------------------------------------------------------------------------------
# auth
# ------------------------------------------------------------------------------------------------
def test_every_route_refuses_an_unauthenticated_caller(fx):
    app, _w, runs = fx
    with TestClient(app) as c:
        for path in ("/activity", f"/activity/runs/{runs['full'].run_id}",
                     "/activity/automations"):
            assert c.get(path).status_code == 401, path


def test_there_is_no_write_route(fx):
    app, _w, _r = fx
    writes = [r.path for r in app.routes if getattr(r, "path", "").startswith("/activity")
              and {"POST", "PUT", "PATCH", "DELETE"} & set(getattr(r, "methods", ()))]
    assert writes == []


# ------------------------------------------------------------------------------------------------
# the merge, ordering, pagination
# ------------------------------------------------------------------------------------------------
def test_feed_merges_every_record_kind_the_viewer_may_see(fx):
    app, written, _r = fx
    expected = _visible_to(written, OWNER)
    assert all(expected[k] for k in RUN_FEED_KINDS), "every kind must have a fixture"
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        items = _all(c)
    got: dict[str, set[str]] = {k: set() for k in RUN_FEED_KINDS}
    for it in items:
        got[it["kind"]].add(_key(it))
    assert got == expected


def test_newest_first_and_pages_neither_skip_nor_repeat(fx):
    app, written, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        first = c.get("/activity", params={"limit": 200}).json()
        paged = _all(c)
    items = first["items"]
    keys = [(i["sort_key"], i["id"]) for i in items]
    assert keys == sorted(keys, reverse=True)
    assert [i["id"] for i in paged] == [i["id"] for i in items]
    assert first["total_visible"] == len(items) == sum(len(v) for v in
                                                       _visible_to(written, OWNER).values())
    # each row sorts by the time its producer recorded
    at_of = {(k, w["id"]): w["at"] for k, rows in written.items() for w in rows}
    for it in items:
        assert it["sort_key"] == sort_key(at_of[(it["kind"], _key(it))])


def test_limit_is_bounded_and_kinds_filter_and_unknown_kind_refused(fx):
    app, written, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        assert len(c.get("/activity", params={"limit": 1}).json()["items"]) == 1
        huge = c.get("/activity", params={"limit": 10_000}).json()
        assert huge["returned"] <= act.MAX_LIMIT
        only = c.get("/activity", params={"kinds": "approval,firing"}).json()["items"]
        assert {i["kind"] for i in only} == {"approval", "firing"}
        assert c.get("/activity", params={"kinds": "nope"}).status_code == 422


# ------------------------------------------------------------------------------------------------
# ownership: fail closed (Codex f1 #2)
# ------------------------------------------------------------------------------------------------
_RUN_KINDS = {"agent_run": "agent", "federated_run": "federated", "adapter_sync": "adapter_agent",
              "adapter_setup": "adapter_agent", "one_time_task": "agent"}


@pytest.mark.parametrize("who", [OWNER, OTHER, OPERATOR, "staff"])
def test_principal_isolation_across_every_record_kind(fx, who):
    """Each viewer sees exactly the records whose `viewers` name them — in the feed, and on the
    run-detail route for every run kind. Nobody is an operator here (WALNUT_OPERATORS unset and
    no dev principal), so ownerless and ambiguous records are seen by NO ONE."""
    app, written, _r = fx
    expected = _visible_to(written, who)
    with TestClient(app, headers=auth_headers(who)) as c:
        got = {(i["kind"], _key(i)) for i in _all(c)}
        assert got == {(k, i) for k, ids in expected.items() for i in ids}
        for kind, ns_kind in _RUN_KINDS.items():
            for w in written[kind]:
                owner_dir, run_id = w["id"].split("/", 1)
                status = c.get(f"/activity/runs/{run_id}").status_code
                assert status == (200 if w["id"] in expected[kind] else 404), (who, w["id"])
    if who in (OWNER, OTHER):
        assert got, "non-vacuous: this viewer owns something"
    else:
        assert got == set()


def test_ownerless_and_ambiguous_records_are_hidden_from_everyone_but_an_operator(fx):
    app, written, runs = fx
    ownerless = {(k, w["id"]) for k, rows in written.items() for w in rows if not w["viewers"]}
    # non-vacuous: every kind that CAN be ownerless has one here, and one record is ambiguous
    assert {k for k, _i in ownerless} == set(RUN_FEED_KINDS) - {"approval"}
    assert ("federated_run", f"{OWNER}/{runs['ambiguous']}") in ownerless
    for who in (OWNER, OTHER):
        with TestClient(app, headers=auth_headers(who)) as c:
            seen = {(i["kind"], _key(i)) for i in _all(c)}
        assert not (seen & ownerless), who


def test_an_operator_sees_every_record_including_ownerless(fx, operators):
    app, written, _r = fx
    operators(OPERATOR)
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        got = {(i["kind"], _key(i)) for i in _all(c)}
        body = c.get("/activity", params={"limit": 1}).json()
        for kind in _RUN_KINDS:
            for w in written[kind]:
                assert c.get(f"/activity/runs/{_run_id_of(w['id'])}").status_code == 200
    assert got == {(k, w["id"]) for k, rows in written.items() for w in rows}
    assert "operator" in body["visibility"]


@pytest.mark.parametrize("config", ['["someone-else"]', '{"audit-1": true}', "not json at all ["])
def test_operator_status_comes_only_from_configuration(fx, monkeypatch, config):
    """Another principal named, a malformed value, or garbage: `audit-1` is not an operator, and
    with WALNUT_OPERATORS unset only the dev principal is (BRAIN's rule) — so it sees nothing."""
    app, written, _r = fx
    monkeypatch.setenv("WALNUT_OPERATORS", config)
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        assert _all(c) == []
        assert c.get(f"/activity/runs/{_run_id_of(written['agent_run'][-1]['id'])}"
                     ).status_code == 404


# ------------------------------------------------------------------------------------------------
# no existence leaks (Codex f1 #8)
# ------------------------------------------------------------------------------------------------
def test_missing_anothers_and_ownerless_runs_get_byte_identical_404s(fx):
    app, written, runs = fx
    others = [w for w in written["agent_run"] + written["federated_run"] + written["adapter_sync"]
              if OTHER not in w["viewers"]]
    assert any(w["viewers"] for w in others) and any(not w["viewers"] for w in others)
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        bodies = [c.get(f"/activity/runs/{_run_id_of(w['id'])}") for w in others]
        bodies += [c.get(f"/activity/runs/{_run_id_of(w['id'])}",
                         params={"kind": _RUN_KINDS[k], "owner": w["id"].split("/")[0]})
                   for k in _RUN_KINDS for w in written[k] if OTHER not in w["viewers"]]
        missing = c.get("/activity/runs/no-such-run")
    assert missing.status_code == 404
    for r in bodies:
        assert (r.status_code, r.content) == (missing.status_code, missing.content)


def test_list_counts_and_problems_are_the_viewers_own(fx, operators):
    """Corrupt records in namespaces the viewer does not own are not reported to the viewer —
    no file name, no count. An operator is told exactly which."""
    app, written, _r = fx
    runs = app.state.activity_sources.runs_dir
    corrupt = [runs / "agent" / "legacy" / "run-corrupt-legacy.json",
               runs / "federated" / OWNER / "fed-corrupt-owner.json"]
    for p in corrupt:
        p.write_text("{not json", encoding="utf-8")
    expected = _visible_to(written, OTHER)
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        body = c.get("/activity", params={"limit": 200}).json()
    assert body["counts"] == {k: len(v) for k, v in expected.items() if v}
    assert body["total_visible"] == sum(len(v) for v in expected.values())
    text = json.dumps(body["problems"])
    assert body["problems"] == [] and "corrupt" not in text
    operators(OPERATOR)
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        op = c.get("/activity", params={"limit": 200}).json()
    named = {p["file"] for p in op["problems"]}
    assert {p.name for p in corrupt} <= named                 # non-vacuous: they were reported


# ------------------------------------------------------------------------------------------------
# honesty: missing stays null; tokens only where measured
# ------------------------------------------------------------------------------------------------
def test_missing_fields_stay_null_never_zero(fx):
    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        items = {i["id"]: i for i in _all(c)}
    sched = items["agent_run:followup/run-scheduled"]
    assert sched["ended"] is None and sched["duration_ms"] is None
    assert sched["produced_by"] is None and sched["tokens"] is None
    assert sched["started_by"] == make_actor("schedule", "automation")
    # R-028 CONS #7: the common actor object, never decorated; a machine is never authenticated
    assert sched["started_by"]["authenticated_by"] == absent(MACHINE_ACTOR)
    # tokens appear only on a run whose OWN record measured some, or whose trace did
    measured = set()
    for it in items.values():
        if it["kind"] in _RUN_KINDS and it.get("run_id"):
            rec = json.loads(next(runs.rglob(f"{it['run_id']}.json")).read_text("utf-8"))
            if (rec.get("tokens") or 0) > 0 or it.get("usage_source") == "trace":
                measured.add(it["id"])
    assert "agent_run:followup/run-owner" in measured                          # non-vacuous
    assert {i for i, it in items.items() if it["tokens"] is not None} == measured


def test_missing_collections_are_not_recorded_rather_than_empty(fx):
    """Codex f1 #3 (backend): no `steps` list is "not recorded", not "0 steps"; no `actions` is
    not "no actions"; no coverage is not "not reduced"; no citations list is not "0 citations"."""
    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    bare = runs / "agent" / "followup" / "run-bare.json"
    bare.write_text(json.dumps({"run_id": "run-bare", "trigger": {"by": OWNER},
                                "started": _iso(5), "status": "completed"}), encoding="utf-8")
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        body = c.get("/activity/runs/run-bare").json()
    item = body["item"]
    assert item["steps"] is None and item["actions"] is None
    assert item["coverage_reduced"] is None and item["delegations"] is None
    tree = delegation_tree({"run_id": "x", "steps": [{"tool": "federated.plan"}],
                            "answer": {"markdown": "m"}})
    assert tree["coverage"] == {"searched": None, "skipped": None, "reasons": None,
                                "reduced": None}
    assert tree["answer"]["citations"] is None and tree["answer"]["unsupported_claims"] is None
    # and an EMPTY recorded list is a measurement: it stays a count of 0 / "not reduced"
    tree2 = delegation_tree({"run_id": "y", "steps": [{"tool": "federated.plan"}],
                             "coverage": {"searched": [], "skipped": [], "reasons": []},
                             "answer": {"citations": [], "unsupported_claims": []}})
    assert tree2["coverage"]["reduced"] is False and tree2["answer"]["citations"] == []


def test_tokens_and_cost_come_from_the_run_record_when_it_carries_them(fx):
    """INT1 / EF4: the runtime now writes `tokens` and `cost_usd` on the run header. Those are
    the run's own metered totals, so they win over the trace join (which only ever had tokens)."""
    app, _w, runs = fx
    assert OWNER_METERED["tokens"] != runs["tokens"]           # non-vacuous: the two differ
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        items = {i["id"]: i for i in _all(c)}
    owner = items["agent_run:followup/run-owner"]
    assert owner["tokens"] == OWNER_METERED["tokens"]
    assert owner["cost_usd"] == OWNER_METERED["cost_usd"]
    assert owner["usage_source"] == "run_record"
    assert owner["trace_request_id"] == runs["token_trace"]    # the trace link is still joined
    # a record whose header is the writer's default zero measured nothing it can vouch for: it
    # stays null (never a typed-in 0), exactly as before the header existed
    sched = items["agent_run:followup/run-scheduled"]
    assert sched["tokens"] is None and sched["cost_usd"] is None and sched["usage_source"] is None


def _strip_header(app, run_id: str) -> None:
    rec_path = next(app.state.activity_sources.runs_dir.rglob(f"{run_id}.json"))
    legacy = json.loads(rec_path.read_text(encoding="utf-8"))
    for key in OWNER_METERED:
        legacy.pop(key, None)
    rec_path.write_text(json.dumps(legacy), encoding="utf-8")


def test_tokens_and_request_id_are_joined_from_the_recorder(fx):
    """A record written before the header existed (no `tokens` / `cost_usd` keys) falls back to
    the tokens the trace measured; the trace carries no cost, so cost stays null."""
    app, _w, runs = fx
    _strip_header(app, "run-owner")
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        items = {i["id"]: i for i in _all(c)}
    owner = items["agent_run:followup/run-owner"]
    assert owner["tokens"] == runs["tokens"]
    assert owner["cost_usd"] is None and owner["usage_source"] == "trace"
    assert owner["trace_request_id"] == runs["token_trace"]
    assert owner["duration_ms"] == 60_000
    assert owner["produced_by"]["model"] == "fake/agent"
    fed = items[f"federated_run:{OWNER}/{runs['full'].run_id}"]
    assert fed["trace_request_id"] == runs["fed_trace"]
    # the federated record's own header decides its tokens (N-19 made the service meter them):
    # its figure when it measured one, else null — never a 0 typed in
    rec = json.loads(next(app.state.activity_sources.runs_dir.rglob(
        f"{runs['full'].run_id}.json")).read_text(encoding="utf-8"))
    assert fed["tokens"] == (rec.get("tokens") or None)


def test_a_trace_joins_only_its_own_namespace_and_owner(fx):
    """Codex f1 #7: two decoy traces, written AFTER the real one, name the same run id — one
    owned by another principal, one in another agent's namespace. Neither joins."""
    app, _w, runs = fx
    _strip_header(app, "run-owner")
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        owner = {i["id"]: i for i in _all(c)}["agent_run:followup/run-owner"]
    assert owner["trace_request_id"] == runs["token_trace"]
    assert owner["trace_request_id"] not in runs["decoys"]
    assert owner["tokens"] not in runs["decoys"].values()


def test_a_viewer_who_can_open_every_trace_still_joins_only_the_runs_own(fx, operators,
                                                                          monkeypatch):
    """An operator who also holds `trace_admin` can open BOTH OWNER's trace and OTHER's decoy
    (newer, same namespace). The recorder no longer filters for them, so the join's own owner
    rule is what keeps OTHER's trace off OWNER's run."""
    app, _w, runs = fx
    _strip_header(app, "run-owner")
    operators(OPERATOR)
    monkeypatch.setenv("WALNUT_AUDIT_ROLES", json.dumps({OPERATOR: ["trace_admin"]}))
    from types import SimpleNamespace

    from databridge_gateway.trace_routes import has_audit_role
    admin = has_audit_role(SimpleNamespace(principal_id=OPERATOR), "trace_admin")
    # non-vacuous: this viewer CAN open the decoy traces (the `/trace/{id}` rule)
    assert all(app.state.traces.get_for(rid, OPERATOR, admin=admin) is not None
               for rid in runs["decoys"])
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        owner = {i["id"]: i for i in _all(c)}["agent_run:followup/run-owner"]
    assert owner["trace_request_id"] == runs["token_trace"]
    assert owner["tokens"] == runs["tokens"]


def test_duplicate_run_ids_in_two_namespaces_are_never_confused(fx):
    from databridge_gateway import agent_ownership

    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    agent_ownership.record_owner("second", OWNER)
    for agent, minute in (("followup", 60), ("second", 61)):
        _agent_run(runs, agent, "run-dup", started=minute, ended=minute + 1, by=OWNER,
                   trig_type="manual", answer_by=None)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        ids = {i["id"] for i in _all(c, kinds="agent_run")}
        amb = c.get("/activity/runs/run-dup")
        one = c.get("/activity/runs/run-dup", params={"kind": "agent", "owner": "second"})
    assert {"agent_run:followup/run-dup", "agent_run:second/run-dup"} <= ids
    assert amb.status_code == 409 and amb.json()["refusal"]["code"] == "AMBIGUOUS_RUN"
    assert {(n["kind"], n["owner"]) for n in amb.json()["refusal"]["nearest_alternative"]} == \
        {("agent", "followup"), ("agent", "second")}
    assert one.status_code == 200
    assert one.json()["owner"] == "second" and one.json()["item"]["started"] == _iso(61)


def test_approval_names_the_principal_and_outcome_and_links_the_run(fx):
    app, written, _r = fx
    want = written["approval"][0]
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        body = c.get("/activity", params={"kinds": "approval"}).json()
        detail = c.get("/activity/runs/run-owner").json()
    [ap] = body["items"]
    assert ap["decided_by"] == want["decided_by"] and ap["outcome"] == want["outcome"]
    # the fixture's decision event records no authentication: the actor says so (absent),
    # rather than presenting the decider as signed in
    assert ap["run_id"] == "run-owner" and ap["actor"] == make_actor(OWNER, "person")
    assert ap["actor"]["authenticated_by"] == absent()
    assert (ap["run_kind"], ap["run_owner"]) == ("agent", "followup")
    assert [h["event"] for h in ap["history"]] == ["proposed", "approved", "executed"]
    assert detail["approvals"][0]["outcome"] == want["outcome"]
    assert [e["event"] for e in detail["action_history"]] == ["proposed", "approved", "executed"]


def _append_actions(app, run_id: str, events: list[dict]) -> None:
    """Append to a run's action log with the runtime's own writer (as `_log_refusal` does)."""
    from databridge_agent_runtime import ActionLog
    from databridge_agent_runtime.runs import actions_path

    rec = next(app.state.activity_sources.runs_dir.rglob(f"{run_id}.json"))
    log = ActionLog(actions_path(rec))
    for e in events:
        log.append(e)


def test_a_refused_decision_shows_as_refused_with_its_reason(fx):
    """INT1 item 8: agent_runtime now logs `decision_refused` for every refused approve / reject /
    reverse (who asked, reason code, time). A pending action whose latest attempt was refused
    shows as REFUSED with the reason; an unverified asker is a claim, never the decider."""
    app, _w, _r = fx
    refusal = {"event": "decision_refused", "decision": "approve", "action_id": "a2",
               "reason_code": "principal_not_authenticated", "detail": "not an issued principal",
               "at": _iso(25), "by": None, "claimed_by": "someone"}
    _append_actions(app, "run-other", [
        {"action_id": "a2", "event": "proposed", "name": "insert_follow_up_task",
         "tool": "source.write:crm.insert", "automation_mode": "human_required", "at": _iso(22)},
        refusal])
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        items = c.get("/activity", params={"kinds": "approval"}).json()["items"]
    [ap] = [i for i in items if i["action_id"] == "a2"]
    assert ap["outcome"] == ap["status"] == "refused" and ap["loud"] is True
    assert ap["refusal"]["reason_code"] == refusal["reason_code"]
    assert ap["refusal"]["decision"] == refusal["decision"]
    assert ap["refusal"]["claimed_by"] == refusal["claimed_by"] and ap["refusal"]["by"] is None
    assert ap["decided_by"] is None                          # a refused ask decided nothing
    assert [h["event"] for h in ap["history"]] == ["proposed", "decision_refused"]
    assert ap["history"][-1]["reason_code"] == refusal["reason_code"]


def test_a_refusal_after_a_terminal_outcome_does_not_hide_it(fx):
    """A reject refused on an action that already EXECUTED (already decided) leaves it executed;
    the refusal is listed beside it, not shown as the action's state."""
    app, written, _r = fx
    _append_actions(app, "run-owner", [
        {"event": "decision_refused", "decision": "reject", "action_id": "a1",
         "reason_code": "already_decided", "detail": "a1 is executed", "at": _iso(14),
         "by": OWNER}])
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        [ap] = c.get("/activity", params={"kinds": "approval"}).json()["items"]
    assert ap["outcome"] == written["approval"][0]["outcome"]
    assert [r["reason_code"] for r in ap["refusals"]] == ["already_decided"]
    assert ap["refusal"] is None


# ------------------------------------------------------------------------------------------------
# redaction: an allow-list, recursive, masked by default (Codex f1 #5)
# ------------------------------------------------------------------------------------------------
def test_adapter_sync_detail_does_not_carry_proposal_evidence(fx):
    app, _w, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        body = c.get("/activity/runs/20260920T084000Z-abc12345").json()
        feed = c.get("/activity").text
    assert body["record"]["proposals"] == {"filed": 1}
    assert "secret excerpt" not in json.dumps(body) and "secret excerpt" not in feed
    assert body["item"]["loud"] is True                      # a partial sync is loud


def test_unknown_fields_are_masked_recursively_and_listed(fx):
    """A producer adds fields this feed has never heard of — at the top, inside the trigger,
    inside a step, inside a citation, inside an action event. None is served; each is listed."""
    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    s = [f"SENTINEL-{i}" for i in range(8)]
    rec = {"run_id": "run-leaky", "spec_digest": "sha256:x", "status": "completed",
           "started": _iso(70), "ended": _iso(71), "models_used": ["fake/agent"],
           "trigger": {"type": "manual", "by": OWNER, "question": "q", "api_key": s[0]},
           "secret_blob": {"deep": [{"deeper": s[1]}]},
           "steps": [{"tool": "facts.query", "ms": 3, "args": {"password": s[2]},
                      "raw_model_output": s[3],
                      "policy_decision": {"decision": "permit", "reason_code": "rules",
                                          "evidence": s[4]},
                      "result_pointers": [{"source_id": "crm", "kind": "query_cell",
                                           "value": s[5]}]}],
           "actions": [], "answer": {"markdown": "an answer", "citations": [
               {"source_id": "crm", "kind": "query_cell", "row_ordinal": 1, "excerpt": s[6]}]},
           "outcome": {"reason_code": "identity_candidate", "candidates": [{"value": s[7]}]}}
    path = runs / "agent" / "followup" / "run-leaky.json"
    path.write_text(json.dumps(rec), encoding="utf-8")
    from databridge_agent_runtime import ActionLog
    from databridge_agent_runtime.runs import actions_path
    ActionLog(actions_path(path)).append({"action_id": "a1", "event": "proposed", "at": _iso(70),
                                          "name": "n", "payload": {"row": s[1]},
                                          "args": {"secret": s[2]}})
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        detail = c.get("/activity/runs/run-leaky")
        feed = c.get("/activity", params={"limit": 200})
    assert detail.status_code == 200
    for sentinel in s:
        assert sentinel not in detail.text and sentinel not in feed.text, sentinel
    masked = set(detail.json()["record"]["masked_fields"])
    assert {"secret_blob", "trigger.api_key", "steps[].args", "steps[].raw_model_output",
            "steps[].policy_decision.evidence"} <= masked
    # what is allowed still arrives: the record is projected, not emptied
    body = detail.json()["record"]
    assert body["trigger"]["question"] == "q" and body["steps"][0]["tool"] == "facts.query"
    assert body["steps"][0]["result_pointers"] == 1 and body["outcome"]["candidates"] == 1
    # the delegation tree goes through the same allow-list: pointer fields only, specs by name
    tree = delegation_tree({
        "run_id": "t", "steps": [
            {"tool": "federated.plan"},
            {"tool": "delegate.ask:crm_sources",
             "args": {"id": "inv", "filters": [{"field": "status", "op": "eq", "value": "open",
                                                "hint": s[0]}], "mystery": s[1]}},
            {"tool": "federated.combine", "args": {"base": "inv", "secret_join": s[2]},
             "policy_decision": {"detail": json.dumps([{"step": "group", "rows": [[s[3]]]}])}}],
        "answer": {"citations": [{"source_id": "crm", "kind": "query_cell", "row_ordinal": 1,
                                  "excerpt": s[4], "cell_text": s[5]}],
                   "unsupported_claims": [{"text": "c", "evidence": s[6]}]}})
    assert not any(x in json.dumps(tree) for x in s)
    # R-028 CONS #12: what is not served is withheld IN PLACE (status + reason), never dropped
    assert tree["answer"]["citations"] == [{"source_id": "crm", "kind": "query_cell",
                                            "row_ordinal": 1,
                                            "excerpt": withheld(SENSITIVE_FIELD),
                                            "cell_text": withheld(NOT_IN_ALLOW_LIST)}]
    assert tree["subtasks"][0]["request"]["filters"] == [{"field": "status", "op": "eq",
                                                          "value": "open",
                                                          "hint": withheld(NOT_IN_ALLOW_LIST)}]
    assert {"answer.citations[].excerpt", "answer.citations[].cell_text",
            "combine.spec.secret_join", "combine.steps[].rows",
            "subtasks[0].request.filters[].hint"} <= set(tree["masked_fields"])


# ------------------------------------------------------------------------------------------------
# bounded work (Codex f1 #6)
# ------------------------------------------------------------------------------------------------
def _bulk_federated(runs: Path, who: str, n: int, after: dt.datetime) -> list[str]:
    """`n` federated runs of `who`, ten minutes apart, all newer than `after`."""
    out = []
    for i in range(n):
        when = after + dt.timedelta(minutes=10 * (i + 1))
        run_id = f"fed-{when.strftime('%Y%m%dT%H%M%S')}-{i:08x}"
        d = runs / "federated" / who
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id, "status": "completed",
            "trigger": {"type": "manual", "by": who, "question": f"q{i}"},
            "started": when.isoformat(timespec="milliseconds"),
            "ended": when.isoformat(timespec="milliseconds"), "steps": [], "actions": [],
            "answer": {"markdown": "", "citations": [], "unsupported_claims": []},
            "coverage": {"searched": [], "skipped": [], "reasons": []}, "models_used": []}),
            encoding="utf-8")
        out.append(f"{who}/{run_id}")
    return out


def _newest(written: dict) -> dt.datetime:
    return max(dt.datetime.fromisoformat(w["at"].replace("Z", "+00:00"))
               for rows in written.values() for w in rows)


def test_reads_are_bounded_by_the_page_and_paging_still_reaches_everything(fx, opened):
    app, written, _r = fx
    runs = app.state.activity_sources.runs_dir
    bulk = _bulk_federated(runs, OWNER, 30, _newest(written))
    limit = 3
    assert len(bulk) > 3 * limit                                     # non-vacuous
    # ten minutes apart is far more than the name-time slack, so the page needs exactly `limit`
    assert dt.timedelta(minutes=10) > 2 * act.NAME_TIME_SLACK
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        first = c.get("/activity", params={"limit": limit, "kinds": "federated_run"}).json()
        assert len(opened) == limit
        assert [_key(i) for i in first["items"]] == bulk[::-1][:limit]
        assert first["next_cursor"] and first["total_visible"] is None
        assert "federated_run" in first["counts_incomplete"]
        opened.clear()
        pages, got = 0, []
        before = None
        while True:
            q = {"limit": limit, "kinds": "federated_run", **({"before": before} if before else {})}
            body = c.get("/activity", params=q).json()
            got.extend(_key(i) for i in body["items"])
            pages += 1
            before = body["next_cursor"]
            if not before or pages > 100:
                break
    expected = _visible_to(written, OWNER)["federated_run"] | set(bulk)
    assert sorted(got) == sorted(expected) and len(got) == len(set(got))
    files = sum(1 for _ in (runs / "federated" / OWNER).glob("*.json"))
    # no page re-reads the whole kind: at most every file once, plus one look-ahead per page
    assert len(opened) <= files + pages
    assert pages > 1


def test_filters_are_pushed_down_to_the_reads(fx, opened):
    app, written, _r = fx
    runs = app.state.activity_sources.runs_dir
    _bulk_federated(runs, OWNER, 5, _newest(written))
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        c.get("/activity", params={"kinds": "federated_run", "limit": 50})
        # OTHER's federated runs are read; OWNER's directory is never opened
        assert opened and all(p.parent.name == OTHER for p in opened)
        opened.clear()
        c.get("/activity", params={"kinds": "firing,ontology_proposal,drift_proposal"})
        assert opened == []                                   # no run record for these kinds
        opened.clear()
        c.get("/activity", params={"kinds": "adapter_sync"})
        assert opened == []                                   # OTHER owns no adapter agent
        opened.clear()
        c.get(f"/activity/runs/{_run_id_of(written['federated_run'][0]['id'])}")
        assert all(p.parent.parent.name != "federated" or p.parent.name == OTHER for p in opened)


def test_an_early_stop_returns_a_cursor_and_paging_completes(fx, opened, monkeypatch):
    """A viewer who owns almost nothing in a busy namespace: the per-request read budget stops
    the scan, the page says so with a cursor, and following the cursor reaches every record the
    viewer may see — each request opening at most the budget."""
    from databridge_gateway import agent_ownership

    app, written, _r = fx
    runs = app.state.activity_sources.runs_dir
    agent_ownership.record_owner("bulk", OWNER)
    base = _newest(written)
    mine = set()
    for i in range(24):
        when = base + dt.timedelta(minutes=10 * (i + 1))
        run_id = f"run-{when.strftime('%Y%m%dT%H%M%S')}-{i:08x}"
        by = OTHER if i % 4 == 0 else None
        _agent_run(runs, "bulk", run_id, started=int((when - T0).total_seconds() // 60),
                   ended=None, by=by, trig_type="manual" if by else "schedule", answer_by=None)
        if by:
            mine.add(f"bulk/{run_id}")
    budget = 5
    monkeypatch.setattr(act, "MAX_RECORDS_OPENED", budget)
    expected = _visible_to(written, OTHER)["agent_run"] | mine
    assert len(expected) > 2 and 24 > 2 * budget                       # non-vacuous
    got, per_page, short_with_cursor = [], [], 0
    before = None
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        for _ in range(200):
            opened.clear()
            q = {"limit": 2, "kinds": "agent_run", **({"before": before} if before else {})}
            body = c.get("/activity", params=q).json()
            per_page.append(len(opened))
            got.extend(_key(i) for i in body["items"])
            before = body["next_cursor"]
            if before and len(body["items"]) < 2:
                short_with_cursor += 1
            if not before:
                break
    assert sorted(got) == sorted(expected) and len(got) == len(set(got))
    assert max(per_page) <= budget
    assert short_with_cursor >= 1        # the budget stopped a page early and it said so


# ------------------------------------------------------------------------------------------------
# automations
# ------------------------------------------------------------------------------------------------
def test_automations_link_firings_to_runs_and_approvals(fx):
    app, written, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        body = c.get("/activity/automations").json()
    specs = {s["spec_id"]: s for s in body["automations"]}
    [manual] = specs["followup"]["firings"]
    assert manual["idempotency_key"] == written["firing"][0]["id"].rsplit("/", 1)[1]
    assert manual["status"] == "succeeded" and manual["started_by"]["principal_id"] == OWNER
    assert [e["status"] for e in manual["events"]] == ["pending", "succeeded"]
    assert manual["run"]["run_id"] == "run-owner"
    assert (manual["run_kind"], manual["run_owner"]) == ("agent", "followup")
    assert [a["outcome"] for a in manual["approvals"]] == ["executed"]
    [skip] = specs["owned_aa"]["firings"]
    assert skip["status"] == "skipped" and skip["run"] is None
    assert body["engine"]["available"] is False and body["engine"]["note"]
    assert "legacy" not in specs                         # ownerless: operator-only
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        other = {s["spec_id"] for s in c.get("/activity/automations").json()["automations"]}
    assert other == {w["id"].split("/")[1] for w in written["firing"] if OTHER in w["viewers"]}


# ------------------------------------------------------------------------------------------------
# the delegation tree, from a real federated run
# ------------------------------------------------------------------------------------------------
def test_delegation_tree_shape_from_a_real_run(fx):
    app, _w, runs = fx
    ans = runs["full"]
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        body = c.get(f"/activity/runs/{ans.run_id}").json()
    tree = body["delegation_tree"]
    assert tree["question"] == FOUNDER_Q and tree["asked_by"] == OWNER
    assert tree["plan"]["produced_by"]["model"] in body["record"]["models_used"]
    # E2E3 P-08: the plan's assumptions and questions are served, equal to what the run planned
    assert ans.plan["assumptions"]                                       # non-vacuous
    assert tree["plan"]["assumptions"] == list(ans.plan["assumptions"])
    assert tree["plan"]["questions"] == list(ans.questions)
    assert tree["plan"]["questions_note"] and tree["plan"]["assumptions_note"] is None
    assert [t["id"] for t in tree["subtasks"]] == [s["id"] for s in FOUNDER_PLAN["subtasks"]]
    assert [t["adapter_agent"] for t in tree["subtasks"]] == \
        [s["adapter_agent"] for s in FOUNDER_PLAN["subtasks"]]
    # each sub-task's status is DERIVED from the coverage the service itself reported: a delegate
    # the service names in `coverage.skipped` (e.g. a non-exhaustive passage search) is "reduced"
    skipped_tools = {s.get("tool") for s in ans.coverage["skipped"]}
    for t, s in zip(tree["subtasks"], FOUNDER_PLAN["subtasks"]):
        tool = f"delegate.ask:{s['adapter_agent']}"
        assert t["status"] == ("reduced" if tool in skipped_tools else "answered"), t
        assert len(t["skipped"]) == sum(1 for k in ans.coverage["skipped"] if k.get("tool") == tool)
    assert any(t["status"] == "answered" for t in tree["subtasks"])        # non-vacuous
    assert set(ans.coverage["searched"]) == {t["connection"] for t in tree["subtasks"]}
    tabular = tree["subtasks"][0]
    assert tabular["query"] and tabular["query"].startswith("SELECT")
    assert tabular["rows_returned"] is not None and tabular["produced_by"]["by"] == "rules"
    combine = FOUNDER_PLAN["combine"]
    assert tree["combine"]["spec"]["group_by"] == combine["group_by"]
    assert tree["combine"]["steps"] == _combine_steps(app, ans.run_id) and tree["combine"]["steps"]
    assert tree["coverage"]["reduced"] is bool(ans.coverage["skipped"] or ans.coverage["reasons"])
    assert tree["refused_subtasks"] == 0
    assert tree["answer"]["markdown"] == body["record"]["answer"]["markdown"]


def _combine_steps(app, run_id: str) -> list[dict]:
    """The combine log as the service RECORDED it (read from the file: the served record does not
    carry policy-decision text, Codex f2 #9)."""
    rec = json.loads(next(app.state.activity_sources.runs_dir.rglob(f"{run_id}.json"))
                     .read_text(encoding="utf-8"))
    step = next(s for s in rec["steps"] if s["tool"] == "federated.combine")
    return json.loads(step["policy_decision"]["detail"])


def test_a_refused_subtask_and_reduced_coverage_are_loud(fx):
    app, _w, runs = fx
    ans = runs["narrow"]
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        body = c.get(f"/activity/runs/{ans.run_id}").json()
        feed = {i["id"]: i for i in _all(c)}
    tree = body["delegation_tree"]
    refused = [t for t in tree["subtasks"] if t["status"] == "refused"]
    skipped_conns = {s["connection"] for s in ans.coverage["skipped"] if s.get("connection")}
    assert {t["connection"] for t in refused} == skipped_conns and refused
    for t in refused:
        assert t["skipped"] and all(s["reason"] for s in t["skipped"])
    assert tree["coverage"]["reduced"] is True and tree["refused_subtasks"] == len(refused)
    assert tree["coverage"]["reasons"] == ans.coverage["reasons"]
    assert tree["combine"]["failed"]
    item = feed[f"federated_run:{OTHER}/{ans.run_id}"]
    assert item["loud"] is True and item["coverage_reduced"] is True
    assert item["refused_delegations"] == len(refused)


def test_bad_run_id_is_refused(fx):
    app, _w, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        assert c.get("/activity/runs/..%2Fx").status_code in (400, 404)
        assert c.get("/activity/runs/.hidden").status_code == 400
        assert c.get("/activity/runs/no-such-run").status_code == 404
        assert c.get("/activity/runs/run-owner", params={"kind": "nope"}).status_code == 400
        assert c.get("/activity/runs/run-owner", params={"owner": "../x"}).status_code == 400


def test_default_sources_are_the_producers_env_paths_and_no_platform_is_built(fx):
    """Without an override the feed reads the packages' env-resolved locations (the `env` fixture
    points WALNUT_RUNS_DIR at the same runs directory) and never builds a platform runtime."""
    app, written, _r = fx
    del app.state.activity_sources
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        runs = {_key(i) for i in _all(c, kinds="agent_run,federated_run")}
    assert runs == _visible_to(written, OWNER)["agent_run"] | \
        _visible_to(written, OWNER)["federated_run"]
    assert getattr(app.state, "platform", None) is None


# ================================================================================================
# Codex f2 (final) — ACT3. Each test is the regression for one finding; `scratchpad/act3/mutate.py`
# puts each fix back and checks that the test named for it fails.
# ================================================================================================
def _fire(app, *lines: dict) -> None:
    from databridge_automation.engine import FiringLog
    log = FiringLog(app.state.activity_sources.firings_path)
    for line in lines:
        log.append(line)


def test_f2_01_a_shared_idempotency_key_never_merges_two_owners_firings(fx):
    """Codex f2 #1: OWNER's and OTHER's specs write the SAME idempotency key; an adapter agent and
    a workflow agent share an id. Each owner sees only their own firing's lines."""
    from databridge_gateway import agent_ownership
    app, _w, _r = fx
    agent_ownership.record_owner("other_agent", OTHER)
    agent_ownership.record_owner("owned_aa", OTHER)        # a WORKFLOW agent named like OWNER's AA
    k = "shared-key"
    _fire(app,
          {"firing_id": "fa", "idempotency_key": k, "spec_id": "followup", "spec_kind": "agent",
           "trigger": "manual", "fired_at": _iso(60), "recorded_at": _iso(60),
           "status": "pending"},
          {"firing_id": "fb", "idempotency_key": k, "spec_id": "other_agent",
           "spec_kind": "agent", "trigger": "schedule", "fired_at": _iso(61),
           "recorded_at": _iso(61), "status": "failed", "error": "RuntimeError: OTHER-ONLY",
           "result": "run-other"},
          {"firing_id": "fc", "idempotency_key": k, "spec_id": "owned_aa",
           "spec_kind": "agent", "trigger": "schedule", "fired_at": _iso(62),
           "recorded_at": _iso(62), "status": "dead_lettered"},
          # same namespace, contradicting firing id: not merged, listed as a conflict
          {"firing_id": "fz", "idempotency_key": k, "spec_id": "followup", "spec_kind": "agent",
           "trigger": "manual", "fired_at": _iso(60), "recorded_at": _iso(63),
           "status": "succeeded"})
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        mine = [i for i in _all(c, kinds="firing") if i["idempotency_key"] == k]
        autos = c.get("/activity/automations").json()["automations"]
    assert [(i["spec_kind"], i["spec_id"]) for i in mine] == [("agent", "followup")]
    [row] = mine
    assert row["id"] == f"firing:agent/followup/{k}"
    assert [e["status"] for e in row["events"]] == ["pending"]          # nobody else's lines
    assert row["status"] == "pending" and row["run_id"] is None
    assert row["conflicts"] == [{"recorded_at": _iso(63), "fields": ["firing_id"]}]
    assert row["loud"] is True
    assert "OTHER-ONLY" not in json.dumps(mine) and "dead_lettered" not in json.dumps(mine)
    assert not any(s["spec_id"] == "other_agent" for s in autos)
    # OWNER's adapter agent `owned_aa` and OTHER's workflow agent `owned_aa` are two automations
    assert [s["spec_kind"] for s in autos if s["spec_id"] == "owned_aa"] == ["adapter_agent"]
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        theirs = {(i["spec_kind"], i["spec_id"]): i for i in _all(c, kinds="firing")
                  if i["idempotency_key"] == k}
    assert set(theirs) == {("agent", "other_agent"), ("agent", "owned_aa")}   # non-vacuous
    assert theirs[("agent", "other_agent")]["status"] == "failed"


def test_f2_03_a_firing_log_not_read_whole_gives_no_firing_count(fx, monkeypatch):
    """Codex f2 #3: the firing log is read from its tail; when lines were cut, no spec's
    `firing_count` is a total. It is null, and every viewer is told the log was not read whole."""
    app, _w, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        whole = c.get("/activity/automations").json()
    assert whole["firings_complete"] is True
    assert all(isinstance(s["firing_count"], int) for s in whole["automations"])   # non-vacuous
    size = app.state.activity_sources.firings_path.stat().st_size
    monkeypatch.setattr(act, "MAX_JSONL_BYTES", size // 2)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        cut = c.get("/activity/automations").json()
    assert cut["firings_complete"] is False and cut["automations"]
    assert all(s["firing_count"] is None for s in cut["automations"])
    assert any("not read in full" in p["error"] and p["file"] is None for p in cut["problems"])


def test_f2_05_what_a_record_does_not_hold_is_null_never_empty_or_none_text():
    """Codex f2 #5: a delegate with no recorded args has no request (not `{}`); with no recorded
    skipped list a sub-task's skips are not `[]` and its status is not "answered"; a combine with
    no args has no spec; and no title says "None"."""
    tree = delegation_tree({"run_id": "t", "steps": [
        {"tool": "federated.plan"}, {"tool": "delegate.ask:crm_sources"},
        {"tool": "federated.combine"}]})
    [sub] = tree["subtasks"]
    assert sub["request"] is None and sub["skipped"] is None and sub["status"] == "not_recorded"
    assert tree["combine"]["spec"] is None
    # recorded-but-empty is still a measurement
    tree2 = delegation_tree({"run_id": "t", "coverage": {"skipped": []}, "steps": [
        {"tool": "delegate.ask:crm_sources", "args": {}},
        {"tool": "federated.combine", "args": {}}]})
    assert tree2["subtasks"][0]["request"] == {} and tree2["subtasks"][0]["skipped"] == []
    assert tree2["subtasks"][0]["status"] == "answered" and tree2["combine"]["spec"] == {}
    row = act.firing_item(act.fold_firings([{"idempotency_key": "k", "status": "pending"}])[0])
    drift = act.drift_items("aa", {"ad": {"status": "pending"}})[0]
    onto = act.ontology_proposal_items([{"proposal_id": "p"}])[0]
    for title in (row["title"], drift["title"], onto["title"]):
        assert "None" not in title and "(not recorded)" in title, title


def test_f2_06_bytes_read_per_request_are_bounded_and_paging_still_completes(fx, opened,
                                                                            monkeypatch):
    """Codex f2 #6: a request's reads are bounded in BYTES per kind, not only in files opened; a
    page cut by the byte budget returns a cursor and paging reaches every record."""
    app, written, _r = fx
    runs = app.state.activity_sources.runs_dir
    who = "staff"                           # a principal whose directory holds only these runs
    bulk = _bulk_federated(runs, who, 12, _newest(written))
    sizes = [(runs / "federated" / f"{b}.json").stat().st_size for b in bulk]
    monkeypatch.setattr(act, "MAX_BYTES_PER_KIND", 3 * max(sizes))
    got, per_page, before = [], [], None
    with TestClient(app, headers=auth_headers(who)) as c:
        for _ in range(200):
            opened.clear()
            q = {"limit": 50, "kinds": "federated_run", **({"before": before} if before else {})}
            body = c.get("/activity", params=q).json()
            per_page.append(sum(p.stat().st_size for p in opened))
            got.extend(_key(i) for i in body["items"])
            before = body["next_cursor"]
            if not before:
                break
    assert sorted(got) == sorted(bulk) and len(got) == len(set(got))
    assert max(per_page) <= act.MAX_BYTES_PER_KIND
    assert len(per_page) > 1                                     # the budget did cut a page


def test_f2_06_the_listing_is_bounded_and_says_its_counts_are_not_totals(fx, monkeypatch,
                                                                          operators):
    app, written, _r = fx
    monkeypatch.setattr(act, "MAX_DIR_ENTRIES", 2)
    operators(OPERATOR)
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        body = c.get("/activity", params={"limit": 200, "kinds": "agent_run"}).json()
    assert body["total_visible"] is None and "agent_run" in body["counts_incomplete"]
    assert len(body["items"]) < len(written["agent_run"])            # non-vacuous: it was cut
    assert any("more run files" in p["error"] for p in body["problems"])


def test_f2_06_a_run_lookup_without_owner_is_bounded_and_discloses_nothing(fx, monkeypatch):
    app, _w, _r = fx
    monkeypatch.setattr(act, "MAX_OWNER_DIRS", 1)                     # two agent directories
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        real = c.get("/activity/runs/run-owner", params={"kind": "agent"})
        missing = c.get("/activity/runs/no-such-run", params={"kind": "agent"})
        named = c.get("/activity/runs/run-owner", params={"kind": "agent", "owner": "followup"})
    assert real.status_code == 400 and real.json()["refusal"]["code"] == "OWNER_REQUIRED"
    assert (real.status_code, real.content) == (missing.status_code, missing.content)
    assert named.status_code == 200


def test_f2_06_drift_logs_share_one_byte_budget(fx, monkeypatch):
    app, _w, _r = fx
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        assert c.get("/activity", params={"kinds": "drift_proposal"}).json()["items"]
        monkeypatch.setattr(act, "MAX_BYTES_PER_KIND", 1)
        body = c.get("/activity", params={"kinds": "drift_proposal"}).json()
    assert body["items"] == [] and "drift_proposal" in body["counts_incomplete"]


def test_f2_08_a_record_derived_id_never_becomes_a_path_outside_its_store(fx):
    """Codex f2 #8: a firing names spec `../evil`; `<aa>/evil.jsonl` (OUTSIDE `_history`) records
    OTHER as its creator. The id is not a name, so its owner is unknown: operator-only."""
    app, _w, _r = fx
    aa_dir = app.state.activity_sources.adapter_agents_dir
    (aa_dir / "evil.jsonl").write_text(json.dumps({"action": "create", "by": OTHER}) + "\n",
                                       encoding="utf-8")
    _fire(app, {"firing_id": "fe", "idempotency_key": "trav", "spec_id": "../evil",
                "spec_kind": "adapter_agent", "trigger": "schedule", "fired_at": _iso(64),
                "recorded_at": _iso(64), "status": "succeeded"})
    viewer = act.Viewer(OTHER, app.state.activity_sources)
    assert viewer.adapter_agent_owner("../evil") is None
    assert viewer.agent_owner("../evil") is None
    assert viewer.adapter_agent_owner("owned_aa") == OWNER          # non-vacuous: lookups work
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        assert not [i for i in _all(c, kinds="firing") if i["idempotency_key"] == "trav"]


def test_f2_09_free_text_is_not_served_codes_are(fx):
    """Codex f2 #9: error text, refusal sentences, drift details and problems, policy-decision
    sentences, delegate notes and SQL carrying a literal are free text. None is served; the code
    beside each one is."""
    from databridge_adapter_agents.adapt import AdaptStore
    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    s = [f"FREE-TEXT-{i}" for i in range(8)]
    _fire(app, {"firing_id": "fx", "idempotency_key": "txt", "spec_id": "followup",
                "spec_kind": "agent", "trigger": "manual", "fired_at": _iso(65),
                "recorded_at": _iso(65), "status": "failed", "attempt": 1,
                "error": f"ValueError: {s[0]}", "reason": f"because {s[1]}"})
    _append_actions(app, "run-owner", [
        {"event": "decision_refused", "decision": "reject", "action_id": "a1",
         "reason_code": "already_decided", "detail": s[2], "at": _iso(14), "by": OWNER}])
    AdaptStore(lambda: app.state.activity_sources.adapter_agents_dir / "_adapt").append(
        "owned_aa", {"adaptation_id": "ad_txt", "agent_id": "owned_aa", "connection": CRM,
                     "status": "pending", "at": _iso(66), "created_at": _iso(66),
                     "drifts": [{"kind": "renamed", "unit": "t", "detail": s[3]}],
                     "problems": [s[4]]})
    sql_with_literal = "[SQL] SELECT * FROM t WHERE name = '" + s[5] + "'"
    (runs / "federated" / OWNER).mkdir(parents=True, exist_ok=True)
    rid = "fed-20260920T090000-textonly"
    (runs / "federated" / OWNER / f"{rid}.json").write_text(json.dumps({
        "run_id": rid, "status": "completed", "started": _iso(67),
        "trigger": {"type": "manual", "by": OWNER, "question": "q"},
        "steps": [{"tool": "federated.plan", "policy_decision": {"decision": "permit",
                                                                 "reason_code": "model",
                                                                 "detail": s[6]}},
                  {"tool": "delegate.ask:crm_sources", "args": {"id": "a"},
                   "policy_decision": {"decision": "permit", "reason_code": "facts_sql_rows",
                                       "detail": sql_with_literal}},
                  {"tool": "delegate.ask:notes_sources", "args": {"id": "b"},
                   "policy_decision": {"decision": "permit", "reason_code": "graph_passages",
                                       "detail": s[7]}}]}), encoding="utf-8")
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        feed = c.get("/activity", params={"limit": 200})
        detail = c.get(f"/activity/runs/{rid}")
        approvals = c.get("/activity/runs/run-owner")
    assert detail.status_code == 200 and approvals.status_code == 200
    text = feed.text + detail.text + approvals.text
    for sentinel in s:
        assert sentinel not in text, sentinel
    firing = next(i for i in feed.json()["items"] if i.get("idempotency_key") == "txt")
    assert firing["events"][0]["error_type"] == "ValueError"          # the code IS served
    assert firing["events"][0]["status"] == "failed"
    ap = approvals.json()["approvals"][0]
    assert ap["refusals"][-1]["reason_code"] == "already_decided"
    drift = next(i for i in feed.json()["items"] if i["id"].endswith("/ad_txt"))
    assert drift["drifts"] == [{"kind": "renamed", "unit": "t", "subject_touching": None}]
    assert drift["problem_count"] == 1
    tree = detail.json()["delegation_tree"]
    # SQL with a literal is withheld in place; a delegate that recorded no SQL has none
    assert [t["query"] for t in tree["subtasks"]] == [withheld(SQL_WITH_LITERALS), None]
    assert [t["delegate_note"] for t in tree["subtasks"]] == [withheld(FREE_TEXT)] * 2
    assert {"subtasks[0].query", "subtasks[0].delegate_note", "subtasks[1].delegate_note",
            "plan.questions_text"} <= set(tree["masked_fields"])
    assert "steps[].policy_decision.detail" in detail.json()["record"]["masked_fields"]
    # a combine operation serves only the keys the combiner writes, not any scalar a producer adds
    ops = delegation_tree({"run_id": "t", "steps": [{"tool": "federated.plan"},
                                                    {"tool": "federated.combine", "args": {},
                                                     "policy_decision": {"detail": json.dumps([
                                                         {"step": "filter", "rows_in": 3,
                                                          "note_text": s[0]}])}}]})
    assert ops["combine"]["steps"] == [{"step": "filter", "rows_in": 3,
                                        "note_text": withheld(NOT_IN_ALLOW_LIST)}]
    assert "combine.steps[].note_text" in ops["masked_fields"]


def test_f2_09_sql_is_served_only_when_every_value_is_a_bound_parameter():
    bound = ('SELECT "a", COUNT(*) FROM "t" WHERE "s" = ? LIMIT ? with 2 bound parameter(s) '
             '(sha256 f518feb29ece…)')
    assert act._literal_free_sql(bound) == bound
    assert act._literal_free_sql("SELECT \"a\" FROM \"t\" WHERE \"s\" = 'x'") is None
    assert act._literal_free_sql('SELECT "a" FROM "t" WHERE "n" = 42') is None
    assert act._literal_free_sql('SELECT "col2" FROM "t9" WHERE "x" = ?') is not None


def test_f2_10_a_record_that_names_another_run_is_not_served(fx):
    """Codex f2 #10: `run-imposter.json` holding `"run_id": "run-owner"` is neither run."""
    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    real = json.loads((runs / "agent" / "followup" / "run-owner.json").read_text("utf-8"))
    (runs / "agent" / "followup" / "run-imposter.json").write_text(json.dumps(real), "utf-8")
    sync = runs / "adapter_agent" / "owned_aa"
    sync_rec = json.loads(next(sync.glob("*.json")).read_text("utf-8"))
    bad_sync = "20260920T085000Z-feedbeef"
    (sync / f"{bad_sync}.json").write_text(json.dumps(
        {**sync_rec, "run_id": bad_sync, "agent_id": "orphan_aa"}), "utf-8")
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        ids = [i["id"] for i in _all(c)]
        assert c.get("/activity/runs/run-imposter").status_code == 404
        assert c.get(f"/activity/runs/{bad_sync}").status_code == 404
        assert c.get("/activity/runs/run-owner").status_code == 200        # non-vacuous
    assert not any("run-imposter" in i or bad_sync in i for i in ids)
    assert ids.count("agent_run:followup/run-owner") == 1


def test_f2_11_activity_operator_is_not_a_role_and_grants_nothing(fx, monkeypatch):
    """Codex f2 #11: the documented-but-unconsulted `activity_operator` role is gone. Naming it in
    WALNUT_AUDIT_ROLES grants nothing; operators are WALNUT_OPERATORS (BRAIN's rule)."""
    from databridge_gateway.auth import AuthenticatedPrincipal
    from databridge_gateway.trace_routes import AUDIT_ROLES, has_audit_role
    assert "activity_operator" not in AUDIT_ROLES
    with pytest.raises(ValueError):
        has_audit_role(AuthenticatedPrincipal(principal_id=OPERATOR), "activity_operator")
    app, _w, _r = fx
    monkeypatch.setenv("WALNUT_AUDIT_ROLES", json.dumps({OPERATOR: ["activity_operator"]}))
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        assert _all(c) == []


def test_e2e3_p08_plan_questions_and_assumptions_are_served_detail_is_not(fx):
    """E2E3 P-08: a STOPPED run's plan step records its questions and assumptions as lists; the
    tree serves both (the asker can see why nothing was read), while the step's free-text
    ``detail`` sentence stays unserved (f2 #9) and ``args`` beyond those two lists is masked."""
    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    asked = [f"QUESTION-{i}?" for i in range(2)]
    assumed = [f"ASSUMPTION-{i}" for i in range(3)]
    secret = "DETAIL-SENTINEL-not-served"
    (runs / "federated" / OWNER).mkdir(parents=True, exist_ok=True)
    rid = "fed-20260920T091000-p08stop"
    (runs / "federated" / OWNER / f"{rid}.json").write_text(json.dumps({
        "run_id": rid, "status": "stopped", "started": _iso(68),
        "trigger": {"type": "manual", "by": OWNER, "question": "q"},
        "steps": [{"tool": "federated.plan",
                   "args": {"question": "q", "assumptions": assumed, "questions": asked,
                            "answers": {"fed:x": "ANSWER-SENTINEL"}},
                   "policy_decision": {"decision": "permit", "reason_code": "model",
                                       "detail": secret}}]}), encoding="utf-8")
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        res = c.get(f"/activity/runs/{rid}")
    assert res.status_code == 200
    plan = res.json()["delegation_tree"]["plan"]
    assert plan["questions"] == asked and plan["assumptions"] == assumed
    assert plan["questions_text"] == "; ".join(asked)
    assert secret not in res.text and "ANSWER-SENTINEL" not in res.text
    # a record without the lists says "not recorded", never an empty claim
    tree = delegation_tree({"run_id": "t", "steps": [{"tool": "federated.plan"}]})
    assert tree["plan"]["questions"] is None and tree["plan"]["assumptions"] is None
    assert "not recorded" in tree["plan"]["questions_note"]
    assert "not recorded" in tree["plan"]["assumptions_note"]


def test_r29_f1_01_an_unknown_firing_namespace_is_never_guessed(fx, monkeypatch):
    """R-029 Codex f1 #1: a firing whose `spec_kind` is missing or unrecognised was coerced into the
    workflow-agent namespace, so the owner of a same-named agent saw it, its run was resolved
    there, and it merged with a genuine firing sharing the key. Now: owner unknown
    (operator-only), never folded with a known namespace, and no run resolved."""
    from databridge_gateway import agent_ownership
    app, _w, _r = fx
    k = "followup:manual:k1"                       # the SAME key as OWNER's genuine firing
    _fire(app,
          {"firing_id": "fx1", "idempotency_key": k, "spec_id": "followup",
           "trigger": "manual", "fired_at": _iso(70), "recorded_at": _iso(70),
           "status": "failed", "result": "run-owner"},
          {"firing_id": "fx2", "idempotency_key": "odd-key", "spec_id": "followup",
           "spec_kind": "workflow_v9", "trigger": "schedule", "fired_at": _iso(71),
           "recorded_at": _iso(71), "status": "succeeded", "result": "run-owner"})
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        mine = [i for i in _all(c, kinds="firing") if i["spec_id"] == "followup"]
        autos = c.get("/activity/automations").json()["automations"]
    # OWNER sees exactly their genuine firing, untouched by the unknown-namespace lines
    assert [(i["spec_kind"], i["idempotency_key"]) for i in mine] == [("agent", k)]
    assert [e["status"] for e in mine[0]["events"]] == ["pending", "succeeded"]
    assert "fx1" not in json.dumps(mine) and "fx2" not in json.dumps(autos)
    monkeypatch.setenv(agent_ownership.OPERATORS_ENV, json.dumps([OPERATOR]))
    with TestClient(app, headers=auth_headers(OPERATOR)) as c:
        ops = [i for i in _all(c, kinds="firing") if i["spec_id"] == "followup"]
    unknown = [i for i in ops if i["spec_kind"] is None]
    assert len(unknown) == 2 and len(ops) == 3                         # non-vacuous
    for row in unknown:
        assert row["id"].startswith("firing:unknown/followup/")
        assert row["run_id"] is None and row["run_kind"] is None and row["run_owner"] is None
