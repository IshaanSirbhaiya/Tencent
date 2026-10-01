"""The model pass, through the real `Harness` with a scripted `FakeBackend` (no network).

The model PROPOSES; rules and the catalogue DISPOSE. Every reference the model makes is checked,
and what rules already found is never overridden by it."""
from __future__ import annotations

import json

from automation_testing import DB, FILES, catalogue
from databridge_automation import compile_english
from databridge_automation.compiler import TASK_CLASS, ModelProposal
from databridge_harness import FakeBackend, Harness
from databridge_workflow_spec import parse_tool

BRIEFING = ("Brief me on a customer before a call: open invoices, recent tickets or meetings, key "
            "contacts and what their contract says about payment terms.")
OVERDUE = ("Every weekday at 9am, find invoices more than 14 days overdue, look up the customer's "
           "payment terms and billing contact, draft a polite reminder into the Follow-ups folder, "
           "and log a follow-up task in the CRM. Ask me before logging anything; just tell me "
           "after drafts are saved.")


def _harness(proposal: dict) -> tuple[Harness, FakeBackend]:
    fb = FakeBackend(json.dumps(ModelProposal.model_validate(proposal).model_dump()))
    return Harness(fb, cache=None), fb


def test_model_fills_title_steps_and_a_real_source():
    h, fb = _harness({
        "kind": "agent", "title": "Account briefing", "agent_id": "account_briefing",
        "instructions": ["Find the customer in the graph.", "Query open invoices.",
                         "Summarise contract payment terms with citations."],
        "sources": ["larkspur-db"], "tools": ["source.query:larkspur-db", "retrieve.search"],
        "kinds_referenced": ["invoice", "contract", "meeting"]})
    # BRIEFING names no source: the model's (real) source is a question until the user says yes
    first = compile_english(BRIEFING, catalogue(FILES, DB), harness=h)
    assert [q.id for q in first.open_questions] == ["model_source:larkspur-db"]
    assert first.spec is None
    res = compile_english(BRIEFING, catalogue(FILES, DB), harness=h,
                          answers={"model_source:larkspur-db": "yes"})
    assert fb.call_count == 2 and res.model_used
    assert not res.open_questions, [q.text for q in res.open_questions]
    spec = res.spec
    assert spec.title == "Account briefing" and spec.agent == "account_briefing"
    assert spec.sources == ["larkspur-db"] and "source.query:larkspur-db" in spec.tools
    assert spec.mode == "read_only" and spec.trigger.type == "manual"
    assert "Query open invoices." in spec.instructions
    assert spec.created_from == BRIEFING
    # the request reached the model verbatim, last, with the catalogue before it
    sent = fb.calls[0]["messages"]
    assert sent[-1]["content"].endswith(BRIEFING)
    assert any("larkspur-db" in m["content"] for m in sent[:-1])


def test_model_reference_to_unknown_things_becomes_questions():
    h, _ = _harness({"kind": "agent", "title": "x", "instructions": ["y"],
                     "sources": ["salesforce"], "kinds_referenced": ["ticket"]})
    res = compile_english(BRIEFING, catalogue(FILES, DB), harness=h)
    ids = {q.id for q in res.open_questions}
    assert "source:salesforce" in ids and "kind:ticket" in ids
    assert res.spec is None and not res.activatable


def test_model_cannot_smuggle_a_write_tool_into_reads():
    h, _ = _harness({"kind": "agent", "title": "x", "instructions": ["y"],
                     "sources": ["larkspur-files"],
                     "tools": ["source.write:larkspur-files:write_file",
                               "source.read:larkspur-files:write_file",
                               "source.read:larkspur-files:no_such_tool"]})
    res = compile_english(BRIEFING, catalogue(FILES, DB), harness=h,
                          answers={"model_source:larkspur-files": "yes"})
    assert res.spec is not None and res.spec.mode == "read_only"
    for t in res.spec.tools:
        p = parse_tool(t)
        if p["family"] == "source":
            assert not FILES.tool(p["tool"]).write if p["tool"] else True
    assert sum("dropped model tool" in n for n in res.notes) == 3


def test_rules_win_over_the_model():
    """The model proposes a different trigger, different modes and extra actions; the rules'
    findings (schedule, per-action approval from the user's words) stand."""
    h, _ = _harness({
        "kind": "agent", "title": "Overdue invoice follow-up", "agent_id": "overdue_followup",
        "instructions": ["Find overdue invoices.", "Draft reminders.", "Log tasks."],
        "trigger_type": "event", "event_source": "larkspur-files", "event_types": ["item_added"],
        "actions": [{"name": "delete_everything", "tool": "source.write:larkspur-files:move_file",
                     "automation_mode": "autonomous", "reversal": "none"}]})
    res = compile_english(OVERDUE, catalogue(FILES, DB), harness=h)
    assert not res.open_questions
    spec = res.spec
    assert spec.trigger.type == "schedule" and spec.trigger.schedule.cron.endswith("1-5")
    modes = {a.name.split("_")[0]: a.automation_mode for a in spec.actions}
    assert modes == {"draft": "human_notified", "log": "human_required"}
    assert spec.title == "Overdue invoice follow-up"


def test_model_action_mode_must_come_from_the_users_words():
    """With no approval words in the sentence, a model-proposed write is a question, not a mode."""
    text = "Keep a running list of overdue invoices for me."
    h, _ = _harness({"kind": "agent", "title": "Overdue list", "instructions": ["List them."],
                     "sources": ["larkspur-files"],
                     "actions": [{"name": "save_list", "connection": "larkspur-files",
                                  "tool": "source.write:larkspur-files:write_file",
                                  "automation_mode": "autonomous"}]})
    res = compile_english(text, catalogue(FILES, DB), harness=h,
                          answers={"model_source:larkspur-files": "yes",
                                   "model_action:save_list": "yes"})
    assert any(q.id == "mode:save_list" for q in res.open_questions)
    assert res.spec is None


def test_model_trigger_without_words_is_a_question():
    h, _ = _harness({"kind": "agent", "title": "t", "instructions": ["s"],
                     "trigger_type": "schedule", "cron": "0 9 * * *"})
    res = compile_english(BRIEFING, catalogue(FILES, DB), harness=h)
    assert any(q.id == "trigger" for q in res.open_questions)


def test_model_failure_degrades_to_rules_and_says_so():
    fb = FakeBackend("not json at all")
    res = compile_english(OVERDUE, catalogue(FILES, DB), harness=Harness(fb, cache=None))
    assert fb.call_count >= 1 and not res.model_used
    assert res.spec is not None and any("rules only" in n for n in res.notes)


def test_task_class_is_routed():
    from databridge_core.config import resolve_model
    assert resolve_model(TASK_CLASS)
