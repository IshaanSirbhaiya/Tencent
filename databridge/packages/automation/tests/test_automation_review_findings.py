"""R-017: one test (or more) per Codex finding on packages/automation (t03), each written to FAIL on
the pre-fix code. Expectations are derived from the rule under test, never copied out of output.

Founder intent these pin: a missing reference is ASKED, never guessed; a read-only agent can never
write; an automation fires exactly once per trigger and is never silently lost."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from automation_testing import DB, DRIVE, FILES, catalogue
from databridge_automation import (
    ActivationRefused,
    ConnectionInfo,
    CronSchedule,
    TriggerEngine,
    classify_tool,
    compile_english,
    save_draft,
)
from databridge_automation.catalogue import WRITE_VERB
from databridge_automation.compiler import ModelProposal, PROMPT_VERSION
from databridge_harness import FakeBackend, Harness
from databridge_workflow_spec import load_spec, parse_tool

SGT = ZoneInfo("Asia/Singapore")


# ---------------------------------------------------------------------------------------------
# helpers (engine)
# ---------------------------------------------------------------------------------------------
def wf(agent, trigger, status="active"):
    return load_spec({"spec": "workflow/v1", "agent": agent, "title": agent,
                      "created_from": "synthetic", "mode": "read_only",
                      "sources": ["larkspur-files"], "tools": ["graph.search"],
                      "trigger": trigger, "instructions": "1. do it", "status": status})


def aa(agent, plans, status="active"):
    return load_spec({"spec": "adapter_agent/v1", "id": agent, "title": agent,
                      "created_from": "synthetic", "connections": sorted(plans),
                      "plans": plans, "sync": {"type": "manual"},
                      "extract": {"pack": "workspace"}, "status": status})


class Clock:
    def __init__(self, t):
        self.t = t.astimezone(timezone.utc)

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = t.astimezone(timezone.utc)


class Recorder:
    def __init__(self, fail_times=0, delay=0.0):
        self.calls = []
        self.fail_times = fail_times
        self.delay = delay

    async def __call__(self, spec, info):
        self.calls.append(info)
        if self.delay:
            await asyncio.sleep(self.delay)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("transient: source unreachable")
        return {"run_id": f"run-{len(self.calls)}"}


@pytest.fixture
def dirs(tmp_path):
    return {"events_path": tmp_path / "events" / "events.jsonl",
            "offsets_dir": tmp_path / "events" / "offsets",
            "firings_path": tmp_path / "automation" / "firings.jsonl"}


def engine(dirs, runner, specs, clock=None, **kw):
    return TriggerEngine(runner, specs=specs, clock=clock or Clock(datetime.now(timezone.utc)),
                         **dirs, **kw)


def firings(dirs):
    p = dirs["firings_path"]
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def emit(path, **over):
    ev = {"event_id": str(uuid.uuid4()), "ts": "2026-10-01T09:00:00Z", "type": "item_added",
          "source_id": "larkspur-files", "uri": "file:///data/Contracts/a.pdf", "run_id": "r1",
          "attrs": {}}
    ev.update(over)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(json.dumps(ev).encode() + b"\n")
    return ev


def contracts(status="active"):
    return wf("contract_watch", {"type": "event", "event": {"source": "larkspur-files",
                                                            "on": ["item_added"]}}, status)


def run(coro):
    return asyncio.run(coro)


# =============================================================================================
# 1 (Critical) — a draft cannot be fired, manually or otherwise
# =============================================================================================
@pytest.mark.parametrize("status", ["draft", "paused"])
def test_f01_manual_fire_refuses_anything_not_active(dirs, status):
    rec = Recorder()
    e = engine(dirs, rec, [contracts(status)])
    out = run(e.fire("contract_watch", by="staff:ana"))
    assert rec.calls == [] and out["status"] == "skipped"
    assert status in out["reason"]


# =============================================================================================
# 2 (Critical) — adapter agents: no activation and no dispatch without approved crawl plans
# =============================================================================================
def test_f02_adapter_agent_with_unapproved_plan_is_not_activatable():
    res = compile_english("keep my Drive Contracts folder in the graph, check hourly",
                          catalogue(FILES, DB, DRIVE))              # DRIVE's plan is `proposed`
    assert res.spec is not None and res.kind == "adapter_agent"
    assert DRIVE.plan_status != "approved"                          # non-vacuous
    assert not res.activatable
    assert any(q.id == f"plan:{DRIVE.id}" for q in res.open_questions)
    with pytest.raises(ActivationRefused):
        save_draft(res, activate=True)


def test_f02_engine_refuses_to_dispatch_an_adapter_agent_whose_plan_is_not_approved(dirs):
    rec = Recorder()
    e = engine(dirs, rec, [aa("aa_one", {"larkspur-files": "approved",
                                         "larkspur-drive": "proposed"})])
    out = run(e.fire("aa_one"))
    assert rec.calls == [] and out["status"] == "skipped" and "larkspur-drive" in out["reason"]
    ok = engine(dirs, rec, [aa("aa_two", {"larkspur-files": "approved"})])
    assert run(ok.fire("aa_two"))["status"] == "succeeded"


# =============================================================================================
# 3 (Critical) — identity matching hidden in another step still forces human_required
# =============================================================================================
def test_f03_subject_reference_in_another_step_marks_every_write():
    text = ("Every day at 6pm, find the person matching each new invoice customer in the CRM. "
            "Add a note in the CRM automatically. To undo, delete the note.")
    res = compile_english(text, catalogue(FILES, DB))
    assert res.spec is not None
    (a,) = res.spec.actions
    assert "person" not in a.description.lower()          # the write clause itself names nobody
    assert not res.activatable
    q = next(q for q in res.open_questions if q.id == f"mode:{a.name}")
    assert q.options == ["human_required"]


def test_f03_model_proposed_actions_are_subject_checked_too():
    text = "For each person in the CRM with an overdue invoice, keep a list. Just do it."
    h = _harness({"kind": "agent", "title": "t", "instructions": ["x"],
                  "actions": [{"name": "save_list", "tool": "source.write:larkspur-files:write_file",
                               "automation_mode": "autonomous", "reversal": "delete it"}]})
    # the model's own reversal is never taken (it must be the user's words or answer)
    res = compile_english(text, catalogue(FILES, DB), harness=h,
                          answers={"model_action:save_list": "yes"})
    assert [q.id for q in res.open_questions] == ["reversal:save_list"]
    res = compile_english(text, catalogue(FILES, DB), harness=h,
                          answers={"model_action:save_list": "yes",
                                   "reversal:save_list": "delete the list"})
    assert any(q.id == "mode:save_list" and q.options == ["human_required"]
               for q in res.open_questions), [q.id for q in res.open_questions]


# =============================================================================================
# 4 (Critical) — catalogue-valid model guesses are questions until the user confirms them
# =============================================================================================
def _harness(proposal):
    fb = FakeBackend(json.dumps(ModelProposal.model_validate(proposal).model_dump()))
    return Harness(fb, cache=None)


def test_f04_model_source_not_in_the_users_words_is_asked():
    text = "Summarise overdue invoices for me."
    assert "larkspur" not in text.lower() and "crm" not in text.lower()   # the user named none
    res = compile_english(text, catalogue(FILES, DB), harness=_harness(
        {"kind": "agent", "title": "t", "instructions": ["x"], "sources": ["larkspur-db"],
         "tools": ["source.query:larkspur-db"]}))
    assert res.spec is None
    q = next(q for q in res.open_questions if q.id == "model_source:larkspur-db")
    assert "larkspur-db" in q.text
    ok = compile_english(text, catalogue(FILES, DB), harness=_harness(
        {"kind": "agent", "title": "t", "instructions": ["x"], "sources": ["larkspur-db"],
         "tools": ["source.query:larkspur-db"]}), answers={q.id: "yes"})
    assert ok.spec is not None and ok.spec.sources == ["larkspur-db"]
    d = next(d for d in ok.derivations if d["field"] == "sources[larkspur-db]")
    assert d["by"] == "model+answer"


def test_f04_model_tool_on_an_unconfirmed_connection_is_dropped():
    res = compile_english("Summarise contracts for me.", catalogue(FILES, DB), harness=_harness(
        {"kind": "agent", "title": "t", "instructions": ["x"],
         "tools": ["source.query:larkspur-db", "retrieve.search"]}))
    assert res.spec is not None
    assert not any(t.startswith("source.") for t in res.spec.tools)
    assert any("source.query:larkspur-db" in n for n in res.notes)


def test_f04_model_cannot_turn_an_agent_into_an_adapter_agent_unasked():
    res = compile_english("Summarise contracts in the Contracts folder for me.",
                          catalogue(FILES, DB),
                          harness=_harness({"kind": "adapter_agent", "title": "t"}))
    assert res.kind == "agent" or any(q.id == "model_kind" for q in res.open_questions)
    assert not (res.spec is not None and res.kind == "adapter_agent")


def test_f04_model_action_is_asked_even_with_a_valid_tool():
    text = "Keep a running list of overdue invoices for me. Just tell me after."
    h = _harness({"kind": "agent", "title": "t", "instructions": ["x"],
                  "actions": [{"name": "save_list", "tool": "source.write:larkspur-files:write_file",
                               "automation_mode": "human_notified"}]})
    res = compile_english(text, catalogue(FILES, DB), harness=h)
    assert res.spec is None and any(q.id == "model_action:save_list" for q in res.open_questions)


# =============================================================================================
# 5 (Critical) — deterministic paths ask instead of defaulting
# =============================================================================================
def test_f05_event_with_no_named_source_asks_even_with_one_connection():
    res = compile_english("When a new file appears, summarise it.", catalogue(FILES))
    assert len(catalogue(FILES).connections) == 1                   # non-vacuous
    assert res.spec is None and any(q.id == "event_source" for q in res.open_questions)


def test_f05_folder_name_matching_two_paths_in_one_connection_asks():
    two = ConnectionInfo(id="larkspur-files", title="Larkspur files", kind="files",
                         tools=FILES.tools, locations=("Legal/Contracts", "Sales/Contracts"),
                         plan_status="approved")
    text = "When a new contract lands in the Contracts folder, summarise its payment terms."
    res = compile_english(text, catalogue(two))
    q = next(q for q in res.open_questions if q.id == "folder:Contracts")
    assert set(q.options) == {f"larkspur-files:{p}" for p in two.locations}
    res2 = compile_english(text, catalogue(two), answers={q.id: "larkspur-files:Sales/Contracts"})
    assert "Sales/Contracts" in res2.spec.trigger.event.filter["uri_glob"]


def test_f05_no_timezone_anywhere_is_a_question(monkeypatch):
    monkeypatch.delenv("WALNUT_TIMEZONE", raising=False)
    text = "Every weekday at 9am, check the CRM for overdue invoices."
    res = compile_english(text, catalogue(FILES, DB))
    assert res.spec is None and any(q.id == "timezone" for q in res.open_questions)
    res2 = compile_english(text, catalogue(FILES, DB), answers={"timezone": "Europe/London"})
    assert res2.spec.trigger.schedule.timezone == "Europe/London"
    d = next(d for d in res2.derivations if d["field"] == "trigger.schedule.timezone")
    assert d["by"] == "answer"


def test_f05_configured_timezone_is_recorded_as_config(monkeypatch):
    monkeypatch.setenv("WALNUT_TIMEZONE", "Europe/Paris")
    res = compile_english("Every weekday at 9am, check the CRM for overdue invoices.",
                          catalogue(FILES, DB))
    assert res.spec.trigger.schedule.timezone == "Europe/Paris"
    d = next(d for d in res.derivations if d["field"] == "trigger.schedule.timezone")
    assert d["by"] == "config" and "WALNUT_TIMEZONE" in d["evidence"]


# =============================================================================================
# 6 (Critical) — unknown tools are never read tools (deny by default)
# =============================================================================================
@pytest.mark.parametrize("name", ["publish_report", "approve_invoice", "writeFile", "frobnicate",
                                  "add_observations"])
def test_f06_unannotated_unknown_or_mutating_tools_are_not_reads(name):
    t = classify_tool(name)
    assert t.write is True and t.basis in ("name", "unknown")


def test_f06_add_observations_is_a_write_by_the_shared_name_rule():
    """server-memory's `add_observations` has no annotations; the ingest rule (S4) calls it a
    write and so must the compiler, or a read-only agent could be granted it."""
    from databridge_automation.catalogue import writes_by_name
    assert writes_by_name("add_observations")
    t = classify_tool("add_observations")
    assert t.write and t.basis == "name"
    mem = ConnectionInfo(id="memory", title="Memory", tools=(classify_tool("read_graph"),
                                                             classify_tool("add_observations")))
    res = compile_english("Summarise what the Memory holds for me. Read-only.", catalogue(mem))
    assert res.spec is not None and res.spec.mode == "read_only"
    assert not any("add_observations" in t for t in res.spec.tools)
    assert any("read_graph" in t for t in res.spec.tools)


@pytest.mark.parametrize("name", ["read_text_file", "list_directory", "searchFiles", "query",
                                  "directory_tree", "get_file_info"])
def test_f06_named_reads_stay_reads(name):
    assert classify_tool(name).write is False


def test_f06_server_annotation_read_only_is_honoured_but_a_write_name_wins():
    assert classify_tool("frobnicate", read_only=True).write is False
    assert classify_tool("delete_all", read_only=True).write is True


def test_f06_read_only_agent_never_holds_an_unknown_tool():
    conn = ConnectionInfo(id="reports", title="Reports", kind="files",
                          tools=(classify_tool("read_report"), classify_tool("publish_report"),
                                 classify_tool("frobnicate")), locations=("Q3",))
    res = compile_english("Summarise the Reports for me. Read-only.", catalogue(conn))
    assert res.spec is not None and res.spec.mode == "read_only"
    held = {parse_tool(t).get("tool") for t in res.spec.tools if t.startswith("source.")}
    assert "read_report" in held and not held & {"publish_report", "frobnicate"}


def test_f06_new_english_write_verbs_make_actions():
    res = compile_english("Every day at 6pm, publish the summary into the Follow-ups folder.",
                          catalogue(FILES, DB))
    assert res.spec is None or res.spec.mode == "read_write"
    assert any(q.id.startswith(("mode:publish", "tool:publish")) for q in res.open_questions)


# =============================================================================================
# 7 (High) — idempotency is atomic and shared across engines/processes
# =============================================================================================
def test_f07_two_engines_racing_on_one_key_dispatch_once(dirs):
    rec = Recorder(delay=0.05)

    async def both():
        a = engine(dirs, rec, [contracts()])
        b = engine(dirs, rec, [contracts()])
        return await asyncio.gather(a.fire("contract_watch", idempotency_key="k1"),
                                    b.fire("contract_watch", idempotency_key="k1"))

    outs = run(both())
    assert len(rec.calls) == 1
    assert sorted(o["status"] for o in outs) == ["skipped", "succeeded"]


def test_f07_concurrent_event_polls_dispatch_once(dirs):
    rec = Recorder(delay=0.02)
    emit(dirs["events_path"])

    async def both():
        a = engine(dirs, rec, [contracts()], consumer="c_a")
        b = engine(dirs, rec, [contracts()], consumer="c_b")
        await asyncio.gather(a.poll_events(), b.poll_events())

    run(both())
    assert len(rec.calls) == 1


# =============================================================================================
# 8 (High) — a scheduled firing is recorded before the schedule advances, and recovered
# =============================================================================================
def test_f08_crash_between_advance_and_dispatch_is_recovered(dirs):
    clock = Clock(datetime(2026, 10, 2, 8, 59, tzinfo=SGT))
    spec = wf("daily", {"type": "schedule", "schedule": {"cron": "0 9 * * *",
                                                         "timezone": "Asia/Singapore"}})
    e1 = engine(dirs, Recorder(), [spec], clock=clock, claim_lease=timedelta(minutes=5))
    run(e1.tick())
    clock.set(datetime(2026, 10, 2, 9, 0, 10, tzinfo=SGT))

    async def crash():                      # tick, then die before the dispatch task runs
        await e1.tick()
        for t in list(e1._tasks):
            t.cancel()
    try:
        run(crash())
    except asyncio.CancelledError:
        pass
    pending = [f for f in firings(dirs) if f.get("status") == "pending"]
    assert len(pending) == 1                                  # the intent is on disk
    rec = Recorder()
    clock.set(datetime(2026, 10, 2, 9, 10, tzinfo=SGT))       # restart after the lease
    e2 = engine(dirs, rec, [spec], clock=clock, claim_lease=timedelta(minutes=5))

    async def go():
        await e2.run_once()
        await e2.drain()
    run(go())
    assert len(rec.calls) == 1
    assert rec.calls[0]["idempotency_key"] == pending[0]["idempotency_key"]
    assert rec.calls[0]["attempt"] == 2


# =============================================================================================
# 9 (High) — failures retry with the same key, then dead-letter; they never consume silently
# =============================================================================================
def test_f09_failed_attempt_is_retried_then_succeeds(dirs):
    clock = Clock(datetime(2026, 10, 2, 9, 0, tzinfo=SGT))
    rec = Recorder(fail_times=1)
    e = engine(dirs, rec, [contracts()], clock=clock, retry_backoff=timedelta(seconds=30))
    first = run(e.fire("contract_watch", idempotency_key="x"))
    assert first["status"] == "failed" and first["next_attempt_at"]
    clock.set(datetime(2026, 10, 2, 9, 1, tzinfo=SGT))

    async def go():
        await e.run_once()
        await e.drain()
    run(go())
    assert [c["attempt"] for c in rec.calls] == [1, 2]
    assert len({c["idempotency_key"] for c in rec.calls}) == 1
    assert firings(dirs)[-1]["status"] == "succeeded"


def test_f09_exhausted_retries_are_dead_lettered(dirs):
    clock = Clock(datetime(2026, 10, 2, 9, 0, tzinfo=SGT))
    rec = Recorder(fail_times=99)
    e = engine(dirs, rec, [contracts()], clock=clock, max_attempts=2,
               retry_backoff=timedelta(seconds=1))
    run(e.fire("contract_watch", idempotency_key="x"))
    for minute in (1, 2, 3):
        clock.set(datetime(2026, 10, 2, 9, minute, tzinfo=SGT))

        async def go():
            await e.run_once()
            await e.drain()
        run(go())
    assert len(rec.calls) == 2                                       # == max_attempts
    dead = [f for f in firings(dirs) if f.get("status") == "dead_lettered"]
    assert len(dead) == 1 and dead[0]["attempts"] == 2
    dl = dirs["firings_path"].parent / "dead_letter.jsonl"
    assert dl.is_file() and json.loads(dl.read_text().splitlines()[0])["idempotency_key"]


# =============================================================================================
# 10 (High) — an approval phrase that names no action is not applied to every action
# =============================================================================================
def test_f10_unlinked_approval_phrase_is_asked_not_applied_globally():
    text = ("Every day at 6pm, save a digest into the Follow-ups folder. Then add a row in the "
            "CRM. Just tell me about the latter.")
    res = compile_english(text, catalogue(FILES, DB))
    assert res.spec is None
    mode_qs = {q.id for q in res.open_questions if q.id.startswith("mode:")}
    assert len(mode_qs) == 2, [q.id for q in res.open_questions]


def test_f10_blanket_phrase_still_applies_to_all():
    text = ("Every day at 6pm, save a digest into the Follow-ups folder and add a row in the "
            "CRM. Tell me after.")
    res = compile_english(text, catalogue(FILES, DB))
    assert not res.open_questions, [q.text for q in res.open_questions]
    assert {a.automation_mode for a in res.spec.actions} == {"human_notified"}


# =============================================================================================
# 11 (High) — a replaced event log is re-read from the start, not from the old offset
# =============================================================================================
def test_f11_replaced_log_of_equal_or_larger_size_is_detected(dirs):
    rec = Recorder()
    p = dirs["events_path"]
    emit(p)
    e = engine(dirs, rec, [contracts()])
    run(e.poll_events())
    assert len(rec.calls) == 1
    # rotate: a NEW file replaces the old one, and is at least as long
    tmp = p.with_suffix(".new")
    first = emit(tmp, uri="file:///data/Contracts/b.pdf")
    emit(tmp, uri="file:///data/Contracts/c.pdf")
    os.replace(tmp, p)
    out = run(e.poll_events())
    got = [c["event"]["event_id"] for c in rec.calls[1:]]
    assert first["event_id"] in got and len(got) == 2
    assert any(o.get("status") == "offset_reset" for o in out)


def test_f11_changed_events_path_resets_the_offset(dirs, tmp_path):
    rec = Recorder()
    emit(dirs["events_path"])
    run(engine(dirs, rec, [contracts()]).poll_events())
    other = tmp_path / "other" / "events.jsonl"
    ev = emit(other)
    emit(other)
    run(engine({**dirs, "events_path": other}, rec, [contracts()]).poll_events())
    assert ev["event_id"] in [c["event"]["event_id"] for c in rec.calls]


# =============================================================================================
# 12, 13 (High) — weekday ranges and interval+time are never silently truncated
# =============================================================================================
@pytest.mark.parametrize("phrase,days", [
    ("Every Monday through Wednesday at 9am", {1, 2, 3}),
    ("Every Tuesday to Thursday at 9am", {2, 3, 4}),
    ("Every Friday to Monday at 9am", {5, 6, 0, 1}),
])
def test_f12_weekday_ranges(phrase, days):
    res = compile_english(phrase + ", check the CRM for overdue invoices.", catalogue(FILES, DB))
    assert not res.open_questions, [q.text for q in res.open_questions]
    s = res.spec.trigger.schedule
    assert set(CronSchedule.parse(s.cron, s.timezone).weekdays) == days


def test_f13_interval_with_an_explicit_time_is_asked():
    res = compile_english("Every 2 hours at 1pm, check the CRM for overdue invoices.",
                          catalogue(FILES, DB))
    assert res.spec is None and any(q.id.startswith("schedule") for q in res.open_questions)


# =============================================================================================
# 14 (High) — long outages are counted exactly, and the in-grace slot still fires
# =============================================================================================
def test_f14_long_outage_counts_every_missed_slot_and_fires_the_current_one(dirs):
    spec = wf("every_min", {"type": "schedule", "schedule": {"cron": "* * * * *",
                                                             "timezone": "UTC"}})
    t0 = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
    clock = Clock(t0)
    rec = Recorder()
    e = engine(dirs, rec, [spec], clock=clock, missed_record_limit=5)
    run(e.tick())
    t1 = t0 + timedelta(days=10)                                     # 14,400 minutes later
    clock.set(t1)

    async def go():
        await e.tick()
        await e.drain()
    run(go())
    cron = CronSchedule.parse("* * * * *", "UTC")
    total = cron.count_between(t0, t1)
    assert total == 14_400                                            # derived: 10 days of minutes
    skipped = [f for f in firings(dirs) if f.get("status") == "skipped"]
    counted = sum(f.get("missed_count", 1) for f in skipped)
    assert counted == total - 1                                       # all but the one that fired
    assert len(rec.calls) == 1                                        # the in-grace slot fired


# =============================================================================================
# 15, 16 (Medium) — cron horizon and DST fold
# =============================================================================================
def test_f15_leap_day_after_a_skipped_century_leap_year():
    c = CronSchedule.parse("0 0 29 2 *", "UTC")
    nxt = c.next_fire(datetime(2096, 3, 1, tzinfo=timezone.utc))
    assert nxt == datetime(2104, 2, 29, tzinfo=timezone.utc)


def test_f16_matches_only_the_first_occurrence_of_a_repeated_minute():
    c = CronSchedule.parse("30 1 * * *", "America/New_York")
    first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)       # 01:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)      # 01:30 EST (repeat)
    assert c.matches(first) and not c.matches(second)
    assert c.next_fire(first - timedelta(minutes=1)) == first


# =============================================================================================
# 17 (Medium) — malformed event lines are recorded before the offset passes them
# =============================================================================================
def test_f17_malformed_lines_are_recorded(dirs):
    p = dirs["events_path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "ab") as fh:
        fh.write(b"{not json\n")
        fh.write(b"\xff\xfe\n")
        fh.write(b'{"type":"item_added"}\n')                        # no event_id
    run(engine(dirs, Recorder(), [contracts()]).poll_events())
    rej = [f for f in firings(dirs) if f.get("status") == "rejected_event"]
    assert len(rej) == 3
    assert all(f["byte_range"][1] > f["byte_range"][0] for f in rej)


# =============================================================================================
# §5.8 — the draft records, per field, whether rules, the model or the user produced it
# =============================================================================================
def test_derivations_record_rules_and_model_with_id_and_prompt_version():
    text = "Every weekday at 9am, check the CRM for overdue invoices."
    res = compile_english(text, catalogue(FILES, DB), harness=_harness(
        {"kind": "agent", "title": "Overdue check", "instructions": ["Query overdue invoices."]}))
    by_field = {d["field"]: d for d in res.derivations}
    assert by_field["trigger.schedule.cron"]["by"] == "rule"
    lo, hi = by_field["trigger.schedule.cron"]["span"]
    assert "weekday" in text[lo:hi].lower()                           # the span is in the words
    assert by_field["title"]["by"] == "model"
    assert by_field["title"]["prompt_version"] == PROMPT_VERSION
    assert "model" in by_field["title"] and by_field["title"]["task_class"]
    assert by_field["sources[larkspur-db]"]["by"] == "rule"
    assert res.as_record()["derivations"] == res.derivations


def test_unknown_tools_are_advertised_as_writes_not_reads():
    from databridge_automation import to_adapter_catalogue
    conn = ConnectionInfo(id="reports", tools=(classify_tool("read_report"),
                                                classify_tool("frobnicate")))
    from databridge_agent_runtime.mapping import tool_capability
    advert = to_adapter_catalogue(catalogue(conn)).get("reports")
    frob = tool_capability("frobnicate")                     # the runtime's names (W2)
    assert frob in advert.writes and frob not in advert.reads
    assert not WRITE_VERB.search("publish_report")          # the base rule alone misses it ...
    assert classify_tool("publish_report").write             # ... the extra verbs catch it


# =============================================================================================
# S2 (coordinator, 2026-10-01): counting/listing -> facts.query; DB writes -> the write connection
# =============================================================================================
WRITER = ConnectionInfo(id="larkspur_db_writer", title="Larkspur CRM agent write",
                        kind="database", purpose="agent_write", writes_for=("larkspur-db",),
                        tools=(classify_tool("execute_sql"),))
READER = ConnectionInfo(id="larkspur-db", title="Larkspur CRM database", kind="database",
                        tools=(ToolRefQ := classify_tool("query", read_only=True),),
                        locations=("public.invoices",), aliases=("crm",), plan_status="approved")


def test_s2_counting_request_uses_facts_query_not_source_query():
    from databridge_automation.compiler import facts_supported
    if not facts_supported():
        pytest.skip("the installed workflow_spec grammar has no facts.query yet")
    res = compile_english("How many invoices in the CRM are more than 30 days overdue?",
                          catalogue(READER))
    assert res.spec is not None, [q.text for q in res.open_questions]
    assert "facts.query:larkspur-db" in res.spec.tools
    assert "source.query:larkspur-db" not in res.spec.tools
    assert "facts.query:larkspur-db" in res.spec.instructions
    d = next(d for d in res.derivations if d["field"] == "tools[facts.query:larkspur-db]")
    assert d["by"] == "rule" and "how many" in d["evidence"].lower()


def test_s2_non_counting_request_keeps_the_live_query():
    res = compile_english("Brief me on the CRM before my call.", catalogue(READER))
    assert "source.query:larkspur-db" in res.spec.tools
    assert not any(t.startswith("facts.") for t in res.spec.tools)


def test_s2_facts_query_compiles_against_the_live_catalogue(tmp_path):
    from automation_testing import purposes, repo_with_golden
    from databridge_automation.compiler import facts_supported
    if not facts_supported():
        pytest.skip("the installed workflow_spec grammar has no facts.query yet")
    root = repo_with_golden(tmp_path)
    cat = catalogue(READER, golden="packs/workspace/evals/automations.jsonl")
    res = compile_english("List all invoices in the CRM that are overdue.", cat,
                          purposes=purposes(), repo_root=root)
    assert res.compilation.compiled, res.compilation.refusals


def test_s2_db_write_goes_through_the_declared_write_connection():
    res = compile_english("Every weekday at 9am, find overdue invoices in the CRM and log a "
                          "follow-up task in the CRM. Ask me before logging.",
                          catalogue(READER, WRITER))
    assert res.spec is not None, [q.text for q in res.open_questions]
    (a,) = res.spec.actions
    assert a.tool == "source.write:larkspur_db_writer:execute_sql"
    assert a.automation_mode == "human_required"
    # the write connection is never read
    assert not any(parse_tool(t).get("connection") == "larkspur_db_writer"
                   for t in res.spec.tools)


def test_s2_undeclared_write_connection_is_asked_not_picked():
    undeclared = ConnectionInfo(id="larkspur_db_writer", purpose="agent_write",
                                tools=(classify_tool("execute_sql"),))
    text = ("Every weekday at 9am, find overdue invoices in the CRM and log a follow-up task in "
            "the CRM. Ask me before logging.")
    res = compile_english(text, catalogue(READER, undeclared))
    q = next(q for q in res.open_questions if q.id.startswith("writer:"))
    assert q.options == ["larkspur_db_writer"] and res.spec is None
    res2 = compile_english(text, catalogue(READER, undeclared),
                           answers={q.id: "larkspur_db_writer"})
    assert res2.spec.actions[0].tool == "source.write:larkspur_db_writer:execute_sql"


def test_s2_write_connection_is_never_offered_for_reads():
    assert WRITER.read_tools == () and not WRITER.query_capable and not WRITER.facts_capable
    assert all(t.write for t in WRITER.write_tools)
    res = compile_english("Summarise larkspur_db_writer for me.", catalogue(READER, WRITER))
    assert res.spec is None or not any("larkspur_db_writer" in t for t in res.spec.tools)
    from databridge_automation.compiler import _validate_tool
    assert _validate_tool("source.query:larkspur-db", catalogue(READER, WRITER), write=True)
    assert _validate_tool("source.write:larkspur-db:query", catalogue(READER, WRITER), write=True)


def test_s2_execute_sql_is_not_a_read_by_name():
    assert classify_tool("execute_sql").write is True
    assert classify_tool("execute_sql", read_only=True).write is False


def test_s2_write_connection_yaml_is_loaded_as_write_only(tmp_path):
    import yaml
    from databridge_automation.catalogue import load_connection_info
    src = tmp_path / "sources"
    src.mkdir()
    (src / "larkspur_db_writer.yaml").write_text(yaml.safe_dump({
        "id": "larkspur_db_writer", "title": "Larkspur CRM agent write", "source_class": "database",
        "purpose": "agent_write", "writes_for": ["larkspur-db"],
        "tools_hint": {"write": "execute_sql"}}), encoding="utf-8")
    info = load_connection_info(src / "larkspur_db_writer.yaml", ingest_root=tmp_path / "ingest")
    assert info.agent_write and info.writes_for == ("larkspur-db",)
    assert [t.name for t in info.write_tools] == ["execute_sql"] and info.read_tools == ()
