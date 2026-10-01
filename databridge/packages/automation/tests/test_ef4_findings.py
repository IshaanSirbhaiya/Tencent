"""EF4 — E2E-2026-10-01 findings F-09, F-22, F-26, F-32 and the batch-2 Codex review of
`packages/automation` (wiki/40-reviews/R-020-EF4-compiler-runtime.md). Every test names the
finding it proves. Expectations are derived from the catalogue, the rule under test or a second
compile — never a recalled answer. No network, no live model."""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from automation_testing import DB, FILES, catalogue, purposes, repo_with_golden
from databridge_automation import (
    ConnectionInfo,
    ToolRef,
    TriggerEngine,
    classify_tool,
    compile_english,
    save_draft,
)
from databridge_automation.catalogue import load_connection_info
from databridge_automation.compiler import ModelProposal
from databridge_harness import FakeBackend, Harness
from databridge_workflow_spec import load_spec, parse_tool

SGT = ZoneInfo("Asia/Singapore")


def _proposal(**kw) -> str:
    return json.dumps(ModelProposal.model_validate(kw).model_dump())


def _grants_on(spec, conn: str) -> set[str]:
    out = set()
    for t in spec.tools:
        p = parse_tool(t)
        if p["family"] in ("source", "facts") and p["connection"] == conn:
            out.add(t)
    return out


# ================================================================================================
# F-09 — model open questions are answerable, reach the model, and are not duplicated
# ================================================================================================
ADAPTER = "Keep the Larkspur files folder in the graph; check hourly"
MODEL_Q = "Should the whole Larkspur files connection be synced, or only a specific folder?"
ANSWER = "the whole connection"


def _asking_harness(*, ignore_answer: bool = False):
    """A model that asks MODEL_Q until the answer is in what it is shown (or always, when it
    ignores answers). The harness keeps its default cache ON, as the gateway's does."""
    def respond(model, messages, guided_json):
        shown = json.dumps(messages)
        ask = ignore_answer or ANSWER not in shown
        return _proposal(kind="adapter_agent", title="Larkspur files in the graph",
                         sources=["larkspur-files"], open_questions=[MODEL_Q] if ask else [])
    fb = FakeBackend(respond)
    return Harness(fb), fb


def test_f09_answering_a_model_question_removes_it_and_reaches_the_model():
    h, fb = _asking_harness()
    first = compile_english(ADAPTER, catalogue(FILES, DB), harness=h)
    model_qs = [q for q in first.open_questions if q.text == MODEL_Q]
    assert len(model_qs) == 1 and first.spec is None
    qid = model_qs[0].id
    second = compile_english(ADAPTER, catalogue(FILES, DB), harness=h, answers={qid: ANSWER})
    assert fb.call_count == 2                     # a new model call, not a cache replay
    assert ANSWER in json.dumps(fb.calls[1]["messages"])
    assert MODEL_Q in json.dumps(fb.calls[1]["messages"])   # the question it answers, too
    assert not any(q.id == qid or q.text == MODEL_Q for q in second.open_questions)
    assert second.spec is not None and second.activatable
    assert any(d["field"] == f"answers[{qid}]" and d["by"] == "answer"
               for d in second.derivations)


def test_f09_a_model_that_ignores_the_answer_cannot_keep_the_question_open():
    h, _ = _asking_harness(ignore_answer=True)
    qid = next(q.id for q in compile_english(ADAPTER, catalogue(FILES, DB), harness=h)
               .open_questions if q.text == MODEL_Q)
    res = compile_english(ADAPTER, catalogue(FILES, DB), harness=h, answers={qid: ANSWER})
    assert not any(q.text == MODEL_Q for q in res.open_questions)
    assert res.spec is not None


def test_f09_the_model_echoing_a_rule_question_is_not_asked_twice():
    text = "Every weekday at 9am, pull the pipeline from Salesforce and summarise it."
    rules = compile_english(text, catalogue(FILES, DB))
    rule_qs = [q.text for q in rules.open_questions]
    assert rule_qs                                                      # non-vacuous
    fb = FakeBackend(_proposal(kind="agent", title="Pipeline", instructions=["x"],
                               open_questions=rule_qs + [q.upper() for q in rule_qs]))
    res = compile_english(text, catalogue(FILES, DB), harness=Harness(fb, cache=None))
    texts = [q.text.lower() for q in res.open_questions]
    for q in rule_qs:
        assert texts.count(q.lower()) == 1, q


# ================================================================================================
# Codex #5 — every advertised question id is consumed by `answers`
# ================================================================================================
def _ids(res) -> set[str]:
    return {q.id for q in res.open_questions}


def test_c5_trigger_answer_is_consumed():
    text = "Every weekday at 9am, and whenever a new file lands in larkspur-files, summarise it."
    first = compile_english(text, catalogue(FILES, DB))
    q = next(q for q in first.open_questions if q.id == "trigger")
    for choice in q.options:
        res = compile_english(text, catalogue(FILES, DB), answers={"trigger": choice})
        assert "trigger" not in _ids(res) and res.spec is not None
        assert res.spec.trigger.type == choice


def test_c5_schedule_time_answer_is_parsed_as_a_schedule():
    text = "Every weekday, summarise new contracts in larkspur-files."
    first = compile_english(text, catalogue(FILES, DB))
    assert "schedule_time" in _ids(first)
    res = compile_english(text, catalogue(FILES, DB), answers={"schedule_time": "at 9am"})
    whole = compile_english("Every weekday at 9am, summarise new contracts in larkspur-files.",
                            catalogue(FILES, DB))
    assert not _ids(res) and res.spec is not None
    assert res.spec.trigger.schedule.cron == whole.spec.trigger.schedule.cron
    d = next(d for d in res.derivations if d["field"] == "trigger.schedule.cron")
    assert d["by"] == "answer"


def test_c5_target_answer_routes_the_write():
    text = "Every day at 9am, draft a summary. Tell me after."
    first = compile_english(text, catalogue(FILES, DB))
    qid = next(q.id for q in first.open_questions if q.id.startswith("target:"))
    loc = FILES.locations[1]
    res = compile_english(text, catalogue(FILES, DB), answers={qid: f"{FILES.id}:{loc}"})
    assert qid not in _ids(res)
    assert res.spec is not None and res.spec.actions
    assert parse_tool(res.spec.actions[0].tool)["connection"] == FILES.id


def test_c5_mode_answer_is_consumed():
    text = ("Read only. Draft a summary into the Follow-ups folder of larkspur-files every day "
            "at 9am; tell me after.")
    first = compile_english(text, catalogue(FILES, DB))
    assert "mode" in _ids(first)
    ro = compile_english(text, catalogue(FILES, DB), answers={"mode": "read_only"})
    assert "mode" not in _ids(ro) and ro.spec is not None and ro.spec.mode == "read_only"
    rw = compile_english(text, catalogue(FILES, DB), answers={"mode": "read_write"})
    assert "mode" not in _ids(rw) and rw.spec is not None and rw.spec.mode == "read_write"


def test_c5_connections_answer_is_consumed():
    text = "Keep everything in the graph, check hourly."
    first = compile_english(text, catalogue(FILES, DB))
    assert "connections" in _ids(first)
    res = compile_english(text, catalogue(FILES, DB), answers={"connections": FILES.id})
    assert "connections" not in _ids(res)
    assert res.spec is not None and list(res.spec.connections) == [FILES.id]


def test_c5_unknown_kind_answered_with_an_existing_kind():
    fb = FakeBackend(_proposal(kind="agent", title="t", instructions=["x"],
                               kinds_referenced=["ticket"]))
    h = Harness(fb, cache=None)
    text = "Summarise new contracts in larkspur-files."
    first = compile_english(text, catalogue(FILES, DB), harness=h)
    q = next(q for q in first.open_questions if q.id == "kind:ticket")
    res = compile_english(text, catalogue(FILES, DB), harness=h,
                          answers={"kind:ticket": q.options[0]})
    assert "kind:ticket" not in _ids(res)


# ================================================================================================
# F-22 — a plan's step `strategy` (not `kind`) tells the catalogue it is a database with facts
# ================================================================================================
@pytest.mark.parametrize("strategy,kind,facts", [("tabular", "database", True),
                                                 ("documents", "files", False)])
def test_f22_plan_strategy_derives_kind_and_facts(tmp_path, strategy, kind, facts):
    (tmp_path / "src.yaml").write_text(yaml.safe_dump({"id": "src", "title": "S"}), "utf-8")
    (tmp_path / "src.plan.yaml").write_text(yaml.safe_dump({
        "source_id": "src", "status": "approved",
        "steps": [{"id": "p", "strategy": strategy}]}), "utf-8")
    info = load_connection_info(tmp_path / "src.yaml", ingest_root=tmp_path / "none")
    assert info.kind == kind and info.facts_capable is facts


# ================================================================================================
# F-26 / Codex #4 — least privilege: only the read tools the instructions use
# ================================================================================================
PG = ConnectionInfo(
    id="larkspur_db", title="Larkspur CRM database", kind="database",
    tools=tuple(classify_tool(n, read_only=True) for n in (
        "execute_sql", "list_schemas", "list_objects", "get_object_details",
        "analyze_db_health", "get_top_queries")),
    plan_status="approved")
FS = ConnectionInfo(
    id="larkspur-files", title="Larkspur files", kind="files",
    tools=tuple(classify_tool(n, read_only=True) for n in (
        "list_roots", "list_dir", "stat_file", "read_bytes")),
    plan_status="approved")
BRIEF = ("Account briefing: when I ask about a customer, give me a one-page briefing from "
         "larkspur_db and larkspur-files. Read only; cite every fact.")
BRIEF_STEPS = ["Query larkspur_db with source.query. Use list_schemas, list_objects and "
               "get_object_details first to find the right tables.",
               "Use the larkspur-files read tools (list_dir, stat_file, read_bytes) to find the "
               "documents."]


def test_f26_model_read_tools_the_instructions_never_use_are_not_granted():
    every = [f"source.read:{c.id}:{t.name}" for c in (PG, FS) for t in c.read_tools
             if not t.query]
    fb = FakeBackend(_proposal(kind="agent", title="Account briefing", instructions=BRIEF_STEPS,
                               sources=["larkspur_db", "larkspur-files"], tools=every))
    res = compile_english(BRIEF, catalogue(PG, FS), harness=Harness(fb, cache=None))
    assert res.spec is not None, [q.text for q in res.open_questions]
    steps = res.spec.instructions
    for t in every:
        name = parse_tool(t)["tool"]
        assert (t in res.spec.tools) == (name in steps), t
    assert any(t not in res.spec.tools for t in every)                # non-vacuous
    assert "source.query:larkspur_db" in res.spec.tools
    assert any("least privilege" in n for n in res.notes)


def test_f26_rules_only_database_gets_its_query_not_every_read_tool():
    res = compile_english("Every weekday at 9am, list overdue invoices from larkspur_db.",
                          catalogue(PG, FS))
    assert res.spec is not None
    granted = _grants_on(res.spec, PG.id)
    assert granted and all(parse_tool(t)["verb"] in ("query",) or parse_tool(t)["family"] == "facts"
                           for t in granted)


def test_f26_a_connection_with_no_query_path_keeps_its_read_tools():
    res = compile_english("Every weekday at 9am, summarise new files in larkspur-files.",
                          catalogue(PG, FS))
    assert res.spec is not None
    assert _grants_on(res.spec, FS.id) == {f"source.read:{FS.id}:{t.name}" for t in FS.read_tools}


# ================================================================================================
# F-32 — titles and descriptions end on whole words / whole clauses
# ================================================================================================
def test_f32_a_parenthetical_stays_whole_in_the_action_description():
    text = ("Every weekday at 9am check larkspur-db for overdue invoices. Insert a follow-up "
            "task for each into larkspur-db (table tasks: customer_id, title); ask me before.")
    res = compile_english(text, catalogue(FILES, DB))
    assert res.spec is not None and res.spec.actions
    desc = res.spec.actions[0].description
    assert desc.count("(") == desc.count(")")
    assert "customer_id, title)" in desc


def test_f32_a_long_model_title_is_cut_on_a_whole_word_and_closes_its_clause():
    long_title = ("Keep larkspur-files and larkspur_db in the graph (hourly sync with search "
                  "passages for every document)")
    fb = FakeBackend(_proposal(kind="adapter_agent", title=long_title,
                               sources=["larkspur-files"]))
    res = compile_english(ADAPTER, catalogue(FILES, DB), harness=Harness(fb, cache=None))
    title = res.spec.title
    assert len(title) <= 80 and long_title.startswith(title)
    assert title.count("(") == title.count(")")
    nxt = long_title[len(title):len(title) + 1]
    assert nxt in ("", " ")                                        # never mid-word


# ================================================================================================
# Codex #3 — a hand-built ToolRef is classified by the shared rule, fail closed
# ================================================================================================
@pytest.mark.parametrize("name", ["execute_sql", "approve_invoice", "frobnicate"])
def test_c3_toolref_without_evidence_is_a_write(name):
    assert ToolRef(name).write is classify_tool(name).write is True


def test_c3_toolref_read_verb_is_a_read_and_explicit_classification_is_kept():
    assert ToolRef("list_directory").write is classify_tool("list_directory").write is False
    assert ToolRef("frobnicate", write=False, basis="annotation").write is False


# ================================================================================================
# Codex #2 — derivations are persisted with the spec, and every grant has one
# ================================================================================================
def test_c2_saved_spec_carries_a_derivation_for_every_field_it_grants(tmp_path):
    res = compile_english("Every weekday at 9am, summarise new files in larkspur-files.",
                          catalogue(FILES, DB))
    assert res.spec is not None
    fields = {d.field for d in res.spec.derivations}
    for t in res.spec.tools:
        assert f"tools[{t}]" in fields, t
    for f in ("title", "id", "instructions", "mode", "trigger.type"):
        assert f in fields, f
    path = save_draft(res, agents_dir=tmp_path)[0]
    assert {d.field for d in load_spec(path).derivations} == fields


# ================================================================================================
# Codex #8 — one declaration generator; the runtime's pairing check gates activation
# ================================================================================================
def test_c8_no_injected_declaration_builder():
    assert "declaration_builder" not in inspect.signature(compile_english).parameters


def test_c8_the_runtime_pairing_check_gates_activatable(tmp_path, monkeypatch):
    from databridge_automation import compiler
    root = repo_with_golden(tmp_path, "packs/workspace/evals/overdue.jsonl")
    text = "Every weekday at 9am, summarise new files in larkspur-files."
    cat = catalogue(FILES, DB, golden="packs/workspace/evals/overdue.jsonl")
    ok = compile_english(text, cat, purposes=purposes(), repo_root=root)
    assert ok.activatable, (ok.compilation and ok.compilation.as_record())
    monkeypatch.setattr(compiler, "pair_problems", lambda s, d: ["synthetic mismatch"])
    bad = compile_english(text, cat, purposes=purposes(), repo_root=root)
    assert not bad.activatable and "synthetic mismatch" in bad.compilation.reason


# ================================================================================================
# Codex #11 — write language that built no governed action is asked about, not dropped
# ================================================================================================
@pytest.mark.parametrize("text", [
    "Every day at 9am, check larkspur-files for new contracts and it emails the customer a summary.",
    "I want it to send the report automatically every day at 9am from larkspur-files.",
])
def test_c11_unbuilt_write_language_becomes_a_question(text):
    res = compile_english(text, catalogue(FILES, DB))
    assert any(q.id.startswith("write_intent:") for q in res.open_questions)
    assert res.spec is None


def test_c11_a_plain_read_request_is_not_asked():
    res = compile_english("Every weekday at 9am, list invoices sent last month from larkspur-db.",
                          catalogue(FILES, DB))
    assert not any(q.id.startswith("write_intent:") for q in res.open_questions)


# ================================================================================================
# Codex #1 — a request naming a subject kind carries the candidate-only rule into its steps
# ================================================================================================
def test_c1_subject_request_instructions_say_candidates_only():
    res = compile_english("Every weekday at 9am, match each person in larkspur-db to their "
                          "contact and show the matches.", catalogue(FILES, DB))
    assert res.spec is not None
    assert "candidate" in res.spec.instructions.lower()
    none = compile_english("Every weekday at 9am, list overdue invoices from larkspur-db.",
                           catalogue(FILES, DB))
    assert "candidate" not in none.spec.instructions.lower()


# ================================================================================================
# engine: Codex #6 (orphan claim), #7 (no runner), #9 (overlap), #10 (duplicate ids)
# ================================================================================================
class Clock:
    def __init__(self, t):
        self.t = t.astimezone(timezone.utc)

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = t.astimezone(timezone.utc)


class Recorder:
    def __init__(self, delay=0.0):
        self.calls, self.delay = [], delay

    async def __call__(self, spec, info):
        self.calls.append(info)
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"run_id": f"run-{len(self.calls)}"}


@pytest.fixture
def edirs(tmp_path):
    return {"events_path": tmp_path / "events" / "events.jsonl",
            "offsets_dir": tmp_path / "events" / "offsets",
            "firings_path": tmp_path / "automation" / "firings.jsonl"}


def wf(agent, trigger):
    return load_spec({"spec": "workflow/v1", "agent": agent, "title": agent,
                      "created_from": "synthetic", "mode": "read_only",
                      "sources": ["larkspur-files"], "tools": ["graph.search"],
                      "trigger": trigger, "instructions": "1. do it", "status": "active"})


def _run(coro):
    return asyncio.run(coro)


def test_c6_a_claim_orphaned_before_its_intent_is_recovered(edirs):
    clock = Clock(datetime(2026, 10, 2, 8, 59, tzinfo=SGT))
    spec = wf("daily", {"type": "schedule", "schedule": {"cron": "0 9 * * *",
                                                         "timezone": "Asia/Singapore"}})
    e1 = TriggerEngine(Recorder(), specs=[spec], clock=clock, claim_lease=timedelta(minutes=5),
                       **edirs)
    _run(e1.tick())
    clock.set(datetime(2026, 10, 2, 9, 0, 10, tzinfo=SGT))
    # die between the exclusive claim and the pending journal line
    e1._record = lambda *a, **k: (_ for _ in ()).throw(SystemExit("process died"))
    with pytest.raises(SystemExit):
        _run(e1.tick())
    lines = edirs["firings_path"].read_text("utf-8").splitlines() \
        if edirs["firings_path"].exists() else []
    assert not any(json.loads(l).get("status") == "pending" for l in lines)
    rec = Recorder()
    clock.set(datetime(2026, 10, 2, 9, 10, tzinfo=SGT))
    e2 = TriggerEngine(rec, specs=[spec], clock=clock, claim_lease=timedelta(minutes=5), **edirs)

    async def go():
        await e2.run_once()
        await e2.drain()
    _run(go())
    assert len(rec.calls) == 1 and rec.calls[0]["attempt"] == 2
    assert rec.calls[0]["scheduled_for"].startswith("2026-10-02T01:00")


def test_c7_cli_refuses_to_fire_or_serve_without_a_runner(tmp_path, monkeypatch):
    from databridge_automation.__main__ import main
    monkeypatch.setenv("WALNUT_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("WALNUT_ADAPTER_AGENTS_DIR", str(tmp_path / "adapter_agents"))
    monkeypatch.setenv("WALNUT_AUTOMATION_DIR", str(tmp_path / "automation"))
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(tmp_path / "events" / "events.jsonl"))
    (tmp_path / "agents").mkdir()
    from databridge_workflow_spec import dump_spec
    (tmp_path / "agents" / "x.workflow.yaml").write_text(
        dump_spec(wf("x", {"type": "manual"})), "utf-8")
    assert main(["fire", "x"]) == 2
    assert main(["serve", "--once"]) == 2
    assert main(["fire", "x", "--record-only"]) == 1      # recorded; nothing ran


def test_c9_a_slot_due_while_the_previous_run_is_busy_is_queued_not_dropped(edirs):
    clock = Clock(datetime(2026, 10, 2, 9, 0, 30, tzinfo=SGT))
    spec = wf("minutely", {"type": "schedule", "schedule": {"cron": "* * * * *",
                                                            "timezone": "Asia/Singapore"}})
    rec = Recorder(delay=0.2)

    async def go():
        e = TriggerEngine(rec, specs=[spec], clock=clock, **edirs)
        e._slots["minutely"].last_checked = clock() - timedelta(seconds=40)
        await e.tick()                               # 09:00 dispatched
        clock.set(datetime(2026, 10, 2, 9, 1, 5, tzinfo=SGT))
        second = await e.tick()                      # 09:01 while 09:00 is still running
        clock.set(datetime(2026, 10, 2, 9, 2, 5, tzinfo=SGT))
        third = await e.tick()                       # 09:02: one queued run already covers it
        await e.drain()
        await asyncio.sleep(0.5)
        await e.drain()
        return second, third
    second, third = _run(go())
    assert [r["status"] for r in second] == ["queued"]
    assert [r["status"] for r in third] == ["coalesced"]
    slots = [c["scheduled_for"] for c in rec.calls]
    assert len(slots) == 2 and slots[0] < slots[1]


def test_c10_duplicate_ids_are_refused_not_silently_replaced(edirs):
    a = wf("same", {"type": "manual"})
    b = load_spec({"spec": "adapter_agent/v1", "id": "same", "title": "x", "created_from": "x",
                   "connections": ["larkspur-files"], "plans": {"larkspur-files": "approved"},
                   "sync": {"type": "manual"}, "extract": {"pack": "workspace"},
                   "status": "active"})
    e = TriggerEngine(Recorder(), spec_loader=lambda: [a, b], **edirs)
    e.reload()
    assert "same" not in e.specs
    assert any("same" in p and "duplicate" in p for p in e.problems)
