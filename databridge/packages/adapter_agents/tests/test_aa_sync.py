"""sync: per-connection isolation, the plan gate (P5), idempotence, status gates, events (§5.3)."""
import json

from aa_testkit import RecordingFakeHarness

from databridge_adapter_agents import EVENT_KEYS, read_events, sync_runner
from databridge_adapter_agents import events as ev_mod
from databridge_adapter_agents import manager as manager_mod
from databridge_adapter_agents.events import EMITTED_BY

#: BUILD_PLAN §5.3, copied from the frozen contract — the one place a literal belongs.
CONTRACT_EVENT_KEYS = {"event_id", "ts", "type", "source_id", "item_id", "uri", "content_hash",
                       "run_id", "attrs"}


def _setup(env, spec_factory, conns=("files-a", "db-b"), **spec_kw):
    for i, c in enumerate(conns):
        env.service.configure(c, items={f"doc{j}": f"{c} text {j}" for j in range(i + 2)})
    return env.registry.create(spec_factory(connections=conns, **spec_kw))


def test_sync_runs_every_approved_connection_into_the_shared_store(env, spec_factory):
    spec = _setup(env, spec_factory)
    summary = env.manager.sync(spec.id)
    assert summary.status == "completed"
    assert env.service.calls == list(spec.connections)
    expected_nodes = sum(len(env.service.config[c]["items"]) for c in spec.connections)
    assert expected_nodes > 0 and len(env.store.nodes) == expected_nodes
    assert summary.totals()["built"] == expected_nodes
    # mcp_ingest's report carries no `nodes` count: the total is unknown, not a guess
    assert summary.totals()["nodes"] is None
    record = json.loads(open(summary.record_path, encoding="utf-8").read())
    assert record["coverage"]["searched"] == list(spec.connections)
    assert {"run_id", "spec_digest", "trigger", "started", "ended", "status", "steps",
            "actions", "answer", "coverage"} <= set(record)


def test_failing_connection_does_not_stop_the_others_and_shows_in_health(env, spec_factory):
    spec = _setup(env, spec_factory, conns=("files-a", "broken-c", "db-b"))
    env.service.configure("broken-c", raises=ConnectionRefusedError("server exited"))
    summary = env.manager.sync(spec.id)
    by_conn = {c.connection: c for c in summary.connections}
    assert summary.status == "partial"
    assert by_conn["broken-c"].status == "failed" and "server exited" in by_conn["broken-c"].reason
    assert by_conn["files-a"].status == by_conn["db-b"].status == "completed"
    assert env.service.calls == ["files-a", "broken-c", "db-b"], "later connections still ran"

    env.clock.advance(hours=1)
    env.manager.sync(spec.id)
    health = env.manager.health(spec.id)
    h = {c["connection"]: c for c in health["connections"]}
    assert h["broken-c"]["consecutive_failures"] == 2
    assert h["broken-c"]["last_run"]["status"] == "failed"
    assert h["files-a"]["consecutive_failures"] == 0
    assert health["overall"] == "failing"
    assert any("broken-c" in i and "2 time(s)" in i for i in health["issues"])

    # recovery resets the streak
    env.service.configure("broken-c", raises=None)
    env.clock.advance(hours=1)
    env.manager.sync(spec.id)
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert h["broken-c"]["consecutive_failures"] == 0


def test_report_status_failed_is_a_failure(env, spec_factory):
    spec = _setup(env, spec_factory)
    env.service.configure("db-b", status="failed")
    summary = env.manager.sync(spec.id)
    assert {c.connection: c.status for c in summary.connections} == {"files-a": "completed",
                                                                     "db-b": "failed"}


def test_an_unapproved_plan_refuses_the_whole_sync_with_its_reasons(env, spec_factory):
    """R-019 (Codex round 3, builder UI): an agent syncs only when EVERY crawl plan it references
    is approved — the spec's record and the plan on disk. One unapproved plan refuses the sync,
    manual or scheduled, with each reason; nothing is ingested."""
    conns = ("files-a", "db-b", "drive-c", "new-d")
    for c in conns:
        env.service.configure(c, items={"x": c})
    env.service.configure("db-b", plan="proposed")        # on disk: proposed
    env.service.configure("new-d", plan=None)             # no plan at all
    spec = env.registry.create(spec_factory(
        connections=conns, plans={"files-a": "approved", "db-b": "approved",
                                  "drive-c": "proposed", "new-d": "approved"}))
    for trigger in ({"type": "manual"}, {"type": "schedule"}):
        summary = env.manager.sync(spec.id, trigger=trigger)
        assert summary.status == "refused" and summary.connections == []
        for c in ("db-b", "drive-c", "new-d"):
            assert repr(c) in summary.reason and "P5" in summary.reason
        assert "'proposed'" in summary.reason and "no crawl plan" in summary.reason
        assert "'files-a'" not in summary.reason
    assert env.service.calls == []
    # every plan approved: it syncs
    env.service.configure("db-b", plan="approved")
    env.service.configure("new-d", plan="approved")
    env.registry.update(spec.id, {"plans": {c: "approved" for c in conns}})
    assert env.manager.sync(spec.id).status == "completed"


def test_a_connection_held_by_drift_is_skipped_with_a_reason(env, spec_factory):
    conns = ("files-a", "db-b")
    for c in conns:
        env.service.configure(c, items={"x": c})
    spec = env.registry.create(spec_factory(connections=conns))
    env.manager.adaptation.store.set_hold(spec.id, "db-b", "ad_test", {"why": "a test hold"})
    summary = env.manager.sync(spec.id)
    by = {c.connection: c for c in summary.connections}
    assert env.service.calls == ["files-a"]
    assert by["db-b"].status == "skipped" and "ad_test" in by["db-b"].reason
    assert summary.status == "partial" and summary.skipped == ["db-b"]


def test_events_follow_the_ownership_rule_and_the_contract_keys(env, spec_factory):
    """§12: mcp_ingest emits run_completed/run_failed for every run it starts; the manager emits a
    run_failed ONLY for a run that failed before ingestion could emit. One event per attempt."""
    spec = _setup(env, spec_factory, conns=("files-a", "broken-c", "held-d"),
                  plans={"files-a": "approved", "broken-c": "approved", "held-d": "approved"})
    env.manager.adaptation.store.set_hold(spec.id, "held-d", "ad_test", {"why": "held"})
    env.service.configure("broken-c", raises=RuntimeError("boom"))
    summary = env.manager.sync(spec.id)
    events = read_events(env.events_path)
    assert set(EVENT_KEYS) == CONTRACT_EVENT_KEYS
    ours = [e for e in events if e["attrs"].get("emitted_by") == EMITTED_BY]
    assert ours and all(set(e) == CONTRACT_EVENT_KEYS for e in ours)
    by = {c.connection: c for c in summary.connections}
    # the pre-start failure is the only event the manager owns
    assert [(e["type"], e["source_id"]) for e in ours] == [("run_failed", "broken-c")]
    assert by["broken-c"].event_owner == "adapter_agent" and by["broken-c"].event_id
    assert by["files-a"].event_owner == "mcp_ingest" and by["files-a"].event_id is None
    # exactly one run event per (run_id, type) across both writers — nothing to de-duplicate
    run_events = [(e["run_id"], e["type"]) for e in events
                  if e["type"] in ("run_completed", "run_failed")]
    assert len(run_events) == len(set(run_events)) == len(summary.ran)
    assert "held-d" not in {e["source_id"] for e in events}, "no event for a skipped connection"
    for e in ours:
        assert e["item_id"] is None and e["uri"] is None and e["content_hash"] is None
        assert e["ts"].endswith("Z") and e["attrs"]["adapter_agent"] == spec.id
    raw = env.events_path.read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == len(events)


def test_in_run_failure_is_one_attempt_owned_by_ingest(env, spec_factory):
    """run_ingest writes a failed report, emits run_failed, then raises: the manager correlates the
    attempt to that run, emits nothing, and health counts ONE failure, not two."""
    spec = _setup(env, spec_factory, conns=("files-a",))
    env.service.configure("files-a", fail_in_run=RuntimeError("build crashed"))
    summary = env.manager.sync(spec.id)
    res = summary.connections[0]
    assert res.status == "failed" and res.event_owner == "mcp_ingest" and res.event_id is None
    runs = env.service.list_runs("files-a")
    assert res.ingest_run_id == runs[-1].run_id
    assert [e for e in read_events(env.events_path)
            if e["attrs"].get("emitted_by") == EMITTED_BY] == []
    h = env.manager.health(spec.id)["connections"][0]
    attempts = len(env.service.calls)
    assert h["consecutive_failures"] == attempts == 1


def test_event_write_failure_does_not_break_isolation_or_the_record(env, spec_factory,
                                                                    monkeypatch):
    spec = _setup(env, spec_factory, conns=("broken-a", "files-b", "broken-c"))
    for c in ("broken-a", "broken-c"):
        env.service.configure(c, raises=ConnectionRefusedError(f"{c} down"))

    def disk_full(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ev_mod, "emit", disk_full)
    summary = env.manager.sync(spec.id)
    assert env.service.calls == list(spec.connections), "every connection still ran"
    by = {c.connection: c for c in summary.connections}
    assert by["files-b"].status == "completed"
    for c in ("broken-a", "broken-c"):
        assert by[c].status == "failed" and "disk full" in by[c].event_error
    assert summary.record_path and any("disk full" in w for w in summary.warnings)
    record = json.loads(open(summary.record_path, encoding="utf-8").read())
    assert {s["args"]["connection"] for s in record["steps"] if s["event_error"]} == \
        {"broken-a", "broken-c"}


def test_record_write_failure_still_returns_the_summary(env, spec_factory, monkeypatch):
    spec = _setup(env, spec_factory)

    def boom(*a, **k):
        raise OSError("read-only runs dir")

    monkeypatch.setattr(env.manager, "_write_record", boom)
    summary = env.manager.sync(spec.id)
    assert summary.status == "completed" and "read-only" in summary.record_error
    assert summary.as_dict()["record_error"] == summary.record_error


def test_unknown_or_non_success_status_is_never_completed(env, spec_factory):
    """Missing, empty, unverified, partial or unfamiliar ⇒ `unverified`; never success."""
    words = [env.service.OMIT, "empty", "unverified", "partial", "done", "running"]
    conns = tuple(f"src-{i}" for i in range(len(words)))
    spec = _setup(env, spec_factory, conns=conns)
    for c, w in zip(conns, words):
        env.service.configure(c, status=w)
    summary = env.manager.sync(spec.id)
    assert {c.status for c in summary.connections} == {"unverified"}
    assert summary.status == "unverified"
    assert all(v is None for v in summary.totals().values()), "nothing completed, nothing summed"
    h = env.manager.health(spec.id)
    assert all(c["last_success_at"] is None and c["stale"] for c in h["connections"])
    assert h["overall"] == "failing"


def test_success_without_an_authoritative_report_is_unverified(env, spec_factory):
    spec = _setup(env, spec_factory, conns=("files-a",))
    env.service.configure("files-a", no_report=True)
    summary = env.manager.sync(spec.id)
    res = summary.connections[0]
    assert res.status == "unverified" and "no report.json" in res.reason
    h = env.manager.health(spec.id)["connections"][0]
    assert h["last_success_at"] is None and h["stale"] is True
    assert h["consecutive_failures"] == 1


def test_proposal_scan_failure_is_serialised_and_marks_the_sync(env, spec_factory, monkeypatch):
    spec = _setup(env, spec_factory)

    def broken(*a, **k):
        raise RuntimeError("vocabulary exploded")

    monkeypatch.setattr(manager_mod, "file_proposals", broken)
    summary = env.manager.sync(spec.id)
    assert summary.status == "completed_with_warnings"
    d = summary.as_dict()
    assert "vocabulary exploded" in d["proposals"]["error"]
    record = json.loads(open(summary.record_path, encoding="utf-8").read())
    assert "vocabulary exploded" in record["proposals"]["error"]
    assert any("vocabulary exploded" in w for w in record["warnings"])


def test_totals_are_unknown_when_any_part_is_unknown():
    from databridge_adapter_agents import ConnectionResult, SyncSummary

    parts = [ConnectionResult("a", "completed", counts={"built": 5}),
             ConnectionResult("b", "completed", counts={"built": None})]
    s = SyncSummary("x", "r", "completed", {}, "", "", parts)
    assert s.totals()["built"] is None
    parts[1].counts["built"] = 2
    assert s.totals()["built"] == 5 + 2


def test_run_record_carries_agent_and_model_provenance(env, spec_factory):
    """§5.8: every sync step says which agent ran it, which model the harness reported, and the
    run header lists every model used."""
    spec = _setup(env, spec_factory)
    fake = RecordingFakeHarness()
    env.manager.harness = fake
    summary = env.manager.sync(spec.id)
    record = json.loads(open(summary.record_path, encoding="utf-8").read())
    assert record["models_used"] == [fake.model]
    assert record["produced_by"]["agent"] == f"adapter_agent:{spec.id}"
    for step in record["steps"]:
        pb = step["produced_by"]
        assert pb["agent"] == f"adapter_agent:{spec.id}"
        assert pb["agent_version"] == record["spec_digest"]
        assert pb["model"] == fake.model and pb["task_class"] == "entity_extraction"
        assert pb["model_assisted"] is True and pb["run_id"] == summary.run_id
    # with no harness, nothing is attributed to a model
    env.manager.harness = None
    env.service.config["files-a"]["items"]["doc0"] = "changed"
    record = json.loads(open(env.manager.sync(spec.id).record_path, encoding="utf-8").read())
    assert record["models_used"] == []
    assert all(s["produced_by"]["model"] is None and not s["produced_by"]["model_assisted"]
               for s in record["steps"])


def test_sync_is_idempotent_when_content_is_unchanged(env, spec_factory):
    spec = _setup(env, spec_factory)
    first = env.manager.sync(spec.id)
    nodes_after_first = set(env.store.nodes)
    env.clock.advance(hours=1)
    second = env.manager.sync(spec.id)
    assert second.status == first.status == "completed"
    assert env.store.nodes == nodes_after_first
    assert second.totals()["built"] == 0 and first.totals()["built"] > 0
    assert second.totals()["inventory"] == first.totals()["inventory"]
    # a changed item is picked up, and only it
    env.service.config["files-a"]["items"]["doc0"] = "edited text"
    env.clock.advance(hours=1)
    third = env.manager.sync(spec.id)
    assert third.totals()["built"] == 1


def test_paused_and_draft_are_refused_whatever_the_trigger(env, spec_factory):
    """A draft never syncs, not even manually (R-019): it is activated first, with its checks."""
    spec = _setup(env, spec_factory)
    env.registry.pause(spec.id)
    s = env.manager.sync(spec.id)
    assert s.status == "refused" and "paused" in s.reason and env.service.calls == []
    env.registry.update(spec.id, {"status": "draft"})
    for trigger in ({"type": "schedule"}, {"type": "manual"}):
        s = env.manager.sync(spec.id, trigger=trigger)
        assert s.status == "refused" and "draft" in s.reason
    assert env.service.calls == []


def test_sync_runner_honours_a_pause_that_landed_after_the_engine_loaded_the_spec(env,
                                                                                    spec_factory):
    spec = _setup(env, spec_factory)
    out = sync_runner(spec, {"type": "schedule", "fired_at": "2026-10-01T10:00:00Z"},
                      manager=env.manager)
    assert out["status"] == "completed" and out["trigger"]["fired_at"] == "2026-10-01T10:00:00Z"
    env.registry.pause(spec.id)
    out = sync_runner(spec, {"type": "schedule"}, manager=env.manager)
    assert out["status"] == "refused"
    env.registry.delete(spec.id)
    assert sync_runner(spec.id, {}, manager=env.manager)["status"] == "refused"


def test_progress_is_forwarded_with_agent_and_connection(env, spec_factory):
    spec = _setup(env, spec_factory)
    seen = []
    env.manager.progress_cb = seen.append
    env.manager.sync(spec.id)
    assert {p["connection"] for p in seen} == set(spec.connections)
    assert all(p["adapter_agent"] == spec.id for p in seen)
