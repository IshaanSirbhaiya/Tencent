"""TriggerEngine: schedules, §5.3 events, manual firing, idempotency, restart, missed runs.

Driven with an injected clock and explicit `tick()` / `poll_events()` calls — no sleeping — plus one
smoke test of the real asyncio loop."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from databridge_automation import TriggerEngine, event_matches, spec_digest
from databridge_automation.engine import glob_to_regex
from databridge_workflow_spec import load_spec

SGT = ZoneInfo("Asia/Singapore")


def wf(agent: str, trigger: dict, status: str = "active") -> object:
    return load_spec({"spec": "workflow/v1", "agent": agent, "title": agent,
                      "created_from": "synthetic", "mode": "read_only",
                      "sources": ["larkspur-files", "larkspur-db"], "tools": ["graph.search"],
                      "trigger": trigger, "instructions": "1. do it", "status": status})


def contracts_spec(status="active"):
    return wf("contract_watch", {"type": "event", "event": {
        "source": "larkspur-files", "on": ["item_added"],
        "filter": {"uri_glob": "**/Contracts/**"}}}, status)


def daily_spec(status="active", cron="0 9 * * *"):
    return wf("daily_digest", {"type": "schedule",
                               "schedule": {"cron": cron, "timezone": "Asia/Singapore"}}, status)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t.astimezone(timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def set(self, t: datetime) -> None:
        self.t = t.astimezone(timezone.utc)


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def __call__(self, spec, info):
        self.calls.append((getattr(spec, "agent", None) or spec.id, info))
        if self.fail:
            raise RuntimeError("source unreachable")
        return {"run_id": f"run-{len(self.calls)}"}


@pytest.fixture
def dirs(tmp_path: Path) -> dict:
    return {"events_path": tmp_path / "events" / "events.jsonl",
            "offsets_dir": tmp_path / "events" / "offsets",
            "firings_path": tmp_path / "automation" / "firings.jsonl"}


def engine(dirs, runner, specs, clock=None, **kw) -> TriggerEngine:
    return TriggerEngine(runner, specs=specs, clock=clock or Clock(datetime.now(timezone.utc)),
                         **dirs, **kw)


def emit(path: Path, *, type_="item_added", source="larkspur-files", uri="file:///data/Contracts/msa.pdf",
         event_id=None, run_id="r1", partial=False) -> dict:
    ev = {"event_id": event_id or str(uuid.uuid4()), "ts": "2026-10-01T09:00:00Z", "type": type_,
          "source_id": source, "item_id": "x", "uri": uri, "content_hash": "sha256:00",
          "run_id": run_id, "attrs": {"mime": "application/pdf"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(json.dumps(ev).encode() + (b"" if partial else b"\n"))
    return ev


def run(coro):
    return asyncio.run(coro)


def tick(e: TriggerEngine) -> list:
    """One tick and its dispatched runs, in ONE event loop (a task must finish in its own loop)."""
    async def both():
        out = await e.tick()
        await e.drain()
        return out
    return asyncio.run(both())


def firings(dirs) -> list[dict]:
    p = dirs["firings_path"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ---------------------------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------------------------
def test_event_fires_once_per_matching_event_and_resumes_after_restart(dirs):
    rec = Recorder()
    ev_path = dirs["events_path"]
    match = emit(ev_path)
    emit(ev_path, source="larkspur-db")                                    # other source
    emit(ev_path, uri="file:///data/Invoices/i1.pdf")                     # other folder
    emit(ev_path, type_="item_changed")                                   # other type
    e1 = engine(dirs, rec, [contracts_spec()])
    run(e1.poll_events())
    assert [c[1]["event"]["event_id"] for c in rec.calls] == [match["event_id"]]
    run(e1.poll_events())                                                 # nothing new
    assert len(rec.calls) == 1

    # restart: a new engine reads the persisted offset and does not re-fire
    e2 = engine(dirs, rec, [contracts_spec()])
    run(e2.poll_events())
    assert len(rec.calls) == 1
    second = emit(ev_path, uri="file:///data/Clients/Contracts/nda.pdf")
    run(e2.poll_events())
    assert [c[1]["event"]["event_id"] for c in rec.calls] == [match["event_id"],
                                                              second["event_id"]]
    ok = [f for f in firings(dirs) if f.get("status") == "succeeded"]
    assert len(ok) == 2 and all(f["trigger"] == "event" for f in ok)


def test_lost_offset_rereads_but_idempotency_keys_prevent_refire(dirs):
    rec = Recorder()
    emit(dirs["events_path"])
    run(engine(dirs, rec, [contracts_spec()]).poll_events())
    for p in dirs["offsets_dir"].glob("*.json"):
        p.unlink()                                                        # crash before offset save
    run(engine(dirs, rec, [contracts_spec()]).poll_events())
    assert len(rec.calls) == 1


def test_duplicate_event_ids_are_ignored(dirs):
    rec = Recorder()
    ev = emit(dirs["events_path"])
    emit(dirs["events_path"], event_id=ev["event_id"])
    e = engine(dirs, rec, [contracts_spec()])
    run(e.poll_events())
    assert len(rec.calls) == 1


def test_run_events_dedupe_by_run_id_and_type(dirs):
    """mcp_ingest and adapter_agents may BOTH emit run_completed for one run (§12): once per run."""
    rec = Recorder()
    spec = wf("after_sync", {"type": "event", "event": {"source": "larkspur-db",
                                                        "on": ["run_completed", "run_failed"]}})
    emit(dirs["events_path"], type_="run_completed", source="larkspur-db", run_id="R1")
    emit(dirs["events_path"], type_="run_completed", source="larkspur-db", run_id="R1")
    emit(dirs["events_path"], type_="run_failed", source="larkspur-db", run_id="R1")
    emit(dirs["events_path"], type_="run_completed", source="larkspur-db", run_id="R2")
    e = engine(dirs, rec, [spec])
    run(e.poll_events())
    got = [(c[1]["event"]["run_id"], c[1]["event"]["type"]) for c in rec.calls]
    assert got == [("R1", "run_completed"), ("R1", "run_failed"), ("R2", "run_completed")]
    # and after a restart with a lost offset, still once per run
    for p in dirs["offsets_dir"].glob("*.json"):
        p.unlink()
    emit(dirs["events_path"], type_="run_completed", source="larkspur-db", run_id="R2")
    run(engine(dirs, rec, [spec]).poll_events())
    assert len(rec.calls) == 3


def test_partial_trailing_line_waits_for_its_newline(dirs):
    rec = Recorder()
    ev = emit(dirs["events_path"], partial=True)
    e = engine(dirs, rec, [contracts_spec()])
    run(e.poll_events())
    assert rec.calls == []
    with open(dirs["events_path"], "ab") as fh:
        fh.write(b"\n")
    run(e.poll_events())
    assert [c[1]["event"]["event_id"] for c in rec.calls] == [ev["event_id"]]


def test_paused_and_draft_specs_do_not_fire_on_events(dirs):
    rec = Recorder()
    emit(dirs["events_path"])
    e = engine(dirs, rec, [contracts_spec("paused")])
    run(e.poll_events())
    assert rec.calls == []
    assert [f["reason"] for f in firings(dirs)] == ["status is paused"]


def test_one_event_fires_every_matching_spec_once(dirs):
    rec = Recorder()
    other = wf("contract_audit", {"type": "event", "event": {"source": "larkspur-files",
                                                             "on": ["item_added"]}})
    emit(dirs["events_path"])
    run(engine(dirs, rec, [contracts_spec(), other]).poll_events())
    assert sorted(c[0] for c in rec.calls) == ["contract_audit", "contract_watch"]


@pytest.mark.parametrize("uri,hit", [
    ("file:///C:/data/Contracts/msa.pdf", True),
    ("Contracts/msa.pdf", True),
    ("file:///data/clients/contracts/x/y.pdf", True),        # case-insensitive, any depth
    ("file:///data/Contractsx/msa.pdf", False),
    ("file:///data/Invoices/msa.pdf", False),
])
def test_uri_glob(uri, hit):
    trig = contracts_spec().trigger.event
    ev = {"source_id": "larkspur-files", "type": "item_added", "uri": uri, "attrs": {}}
    assert event_matches(trig, ev) is hit


def test_attr_filters():
    spec = wf("pdf_watch", {"type": "event", "event": {"source": "larkspur-files",
                                                       "on": ["item_added"],
                                                       "filter": {"mime": "application/pdf"}}})
    base = {"source_id": "larkspur-files", "type": "item_added", "uri": "a.pdf"}
    assert event_matches(spec.trigger.event, {**base, "attrs": {"mime": "application/pdf"}})
    assert not event_matches(spec.trigger.event, {**base, "attrs": {"mime": "text/plain"}})
    assert glob_to_regex("*.pdf").match("msa.pdf") and not glob_to_regex("*.pdf").match("a/msa.pdf")


# ---------------------------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------------------------
def test_schedule_fires_once_per_slot(dirs):
    rec = Recorder()
    clock = Clock(datetime(2026, 10, 2, 8, 59, tzinfo=SGT))
    e = engine(dirs, rec, [daily_spec()], clock=clock)
    run(e.tick())
    assert rec.calls == []
    clock.set(datetime(2026, 10, 2, 9, 0, 30, tzinfo=SGT))
    tick(e)
    clock.set(datetime(2026, 10, 2, 9, 1, tzinfo=SGT))
    tick(e)
    assert len(rec.calls) == 1
    info = rec.calls[0][1]
    slot = datetime.fromisoformat(info["scheduled_for"].replace("Z", "+00:00")).astimezone(SGT)
    assert (slot.hour, slot.minute) == (9, 0)
    assert info["idempotency_key"].endswith(info["scheduled_for"])
    assert spec_digest(daily_spec()) in info["idempotency_key"]


def test_missed_runs_are_skipped_and_recorded_after_restart(dirs):
    rec = Recorder()
    clock = Clock(datetime(2026, 10, 2, 9, 5, tzinfo=SGT))                # Friday, after the slot
    run(engine(dirs, rec, [daily_spec()], clock=clock).tick())            # persists last_checked
    # the engine is down all weekend; it comes back on Monday just after 09:00
    clock.set(datetime(2026, 10, 5, 9, 0, 20, tzinfo=SGT))
    e2 = engine(dirs, rec, [daily_spec()], clock=clock)
    tick(e2)
    recs = firings(dirs)
    missed = [r for r in recs if r["status"] == "skipped"]
    days = sorted(datetime.fromisoformat(r["scheduled_for"].replace("Z", "+00:00"))
                  .astimezone(SGT).strftime("%a") for r in missed)
    assert days == ["Sat", "Sun"] and all("missed" in r["reason"] for r in missed)
    assert len(rec.calls) == 1                                            # Monday's slot, once
    # a third engine at the same instant does not fire Monday again
    e3 = engine(dirs, rec, [daily_spec()], clock=clock)
    tick(e3)
    assert len(rec.calls) == 1


def test_slot_older_than_grace_is_skipped_not_run(dirs):
    rec = Recorder()
    clock = Clock(datetime(2026, 10, 2, 8, 0, tzinfo=SGT))
    e = engine(dirs, rec, [daily_spec()], clock=clock, grace=timedelta(minutes=2))
    clock.set(datetime(2026, 10, 2, 9, 30, tzinfo=SGT))                    # loop stalled 90 min
    tick(e)
    assert rec.calls == []
    assert [r["status"] for r in firings(dirs)] == ["skipped"]


def test_weekday_schedule_skips_weekend(dirs):
    rec = Recorder()
    clock = Clock(datetime(2026, 10, 3, 8, 0, tzinfo=SGT))                 # Saturday
    e = engine(dirs, rec, [daily_spec(cron="0 9 * * 1-5")], clock=clock)
    for day in (3, 4, 5):
        clock.set(datetime(2026, 10, day, 9, 0, 10, tzinfo=SGT))
        tick(e)
    fired = [datetime.fromisoformat(c[1]["scheduled_for"].replace("Z", "+00:00")).astimezone(SGT)
             for c in rec.calls]
    assert [f.weekday() for f in fired] == [0]
    assert e.next_fire("daily_digest").astimezone(SGT).weekday() in range(5)


def test_paused_schedule_records_skip_and_draft_is_silent(dirs):
    rec = Recorder()
    clock = Clock(datetime(2026, 10, 2, 8, 59, tzinfo=SGT))
    paused = daily_spec("paused")
    draft = wf("draft_one", {"type": "schedule", "schedule": {"cron": "0 9 * * *",
                                                              "timezone": "Asia/Singapore"}},
               "draft")
    e = engine(dirs, rec, [paused, draft], clock=clock)
    clock.set(datetime(2026, 10, 2, 9, 0, 5, tzinfo=SGT))
    run(e.tick())
    assert rec.calls == []
    recs = firings(dirs)
    assert [(r["spec_id"], r["reason"]) for r in recs] == [("daily_digest", "status is paused")]


def test_pause_and_resume_does_not_change_the_idempotency_key():
    assert spec_digest(daily_spec("active")) == spec_digest(daily_spec("paused"))
    assert spec_digest(daily_spec(cron="0 10 * * *")) != spec_digest(daily_spec())


# ---------------------------------------------------------------------------------------------
# manual, failures, async runners, the loop
# ---------------------------------------------------------------------------------------------
def test_manual_fire_and_caller_idempotency(dirs):
    rec = Recorder()
    e = engine(dirs, rec, [contracts_spec()])
    first = run(e.fire("contract_watch", idempotency_key="click-1", by="staff:ana"))
    again = run(e.fire("contract_watch", idempotency_key="click-1", by="staff:ana"))
    other = run(e.fire("contract_watch"))
    assert first["status"] == "succeeded" and again["status"] == "skipped"
    assert other["status"] == "succeeded" and len(rec.calls) == 2
    assert rec.calls[0][1]["by"] == "staff:ana" and rec.calls[0][1]["type"] == "manual"
    with pytest.raises(KeyError):
        run(e.fire("nope"))


def test_runner_failure_is_recorded(dirs):
    e = engine(dirs, Recorder(fail=True), [contracts_spec()])
    rec = run(e.fire("contract_watch"))
    assert rec["status"] == "failed" and "source unreachable" in rec["error"]


def test_async_runner_and_no_runner(dirs):
    seen = []

    async def arun(spec, info):
        await asyncio.sleep(0)
        seen.append(info["type"])
        return "ok"

    assert run(engine(dirs, arun, [contracts_spec()]).fire("contract_watch"))["status"] == \
        "succeeded"
    assert seen == ["manual"]
    rec = run(engine(dirs, None, [contracts_spec()]).fire("contract_watch"))
    assert rec["status"] == "no_runner"


def test_serve_loop_picks_up_an_event(dirs):
    rec = Recorder()

    async def scenario():
        e = TriggerEngine(rec, specs=[contracts_spec()], poll_interval=0.01, **dirs)
        await e.start()
        emit(dirs["events_path"])
        for _ in range(300):
            if rec.calls:
                break
            await asyncio.sleep(0.01)
        await e.stop()

    run(scenario())
    assert len(rec.calls) == 1


def test_reload_from_loader_registers_and_unregisters(dirs):
    specs = [contracts_spec()]
    e = TriggerEngine(Recorder(), spec_loader=lambda: list(specs), **dirs)
    e.reload()
    assert set(e.specs) == {"contract_watch"}
    specs[:] = [daily_spec()]
    e.reload()
    assert set(e.specs) == {"daily_digest"}
    assert e.status()["specs"][0]["next_fire"] is not None
