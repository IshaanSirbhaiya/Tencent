"""English -> draft. Each sample carries its STATED INTENT; the assertions check the compiled spec
against that intent (weekdays, local time, trigger type, mode, each action's approval), never
against a copied-out spec. Every test here runs with NO model (harness=None) unless it says so."""
from __future__ import annotations

import pytest
import yaml

from automation_testing import DB, DRIVE, FILES, PACK, catalogue, purposes, repo_with_golden
from databridge_automation import (
    ActivationRefused,
    CronSchedule,
    compile_english,
    save_draft,
)
from databridge_workflow_spec import AdapterAgentSpec, WorkflowSpec, load_spec, parse_tool

OVERDUE = ("Every weekday at 9am, find invoices more than 14 days overdue, look up the customer's "
           "payment terms and billing contact, draft a polite reminder into the Follow-ups folder, "
           "and log a follow-up task in the CRM. Ask me before logging anything; just tell me "
           "after drafts are saved.")
BRIEFING = ("Brief me on a customer before a call: open invoices, recent tickets or meetings, key "
            "contacts and what their contract says about payment terms.")
ADAPTER_FOUNDER = "keep my Drive Contracts folder and the CRM database in the graph, check hourly"
ADAPTER_PLAN = "Keep the Larkspur files folder and CRM database in the graph; check hourly"

MON_FRI = {1, 2, 3, 4, 5}

#: (text, catalogue connections, stated intent)
SAMPLES = [
    (OVERDUE, (FILES, DB), dict(
        kind="agent", trigger="schedule", weekdays=MON_FRI, at=(9, 0), mode="read_write",
        sources={"larkspur-files", "larkspur-db"},
        # "ask me before logging" -> the log action; "tell me after drafts are saved" -> the draft
        actions={"draft": ("human_notified", "larkspur-files"),
                 "log": ("human_required", "larkspur-db")})),
    (BRIEFING, (FILES, DB), dict(kind="agent", trigger="manual", mode="read_only", actions={},
                                 sources=set())),
    (ADAPTER_FOUNDER, (FILES, DB, DRIVE), dict(kind="adapter_agent", trigger="schedule",
                                               hourly=True, sources={"larkspur-drive",
                                                                     "larkspur-db"})),
    (ADAPTER_PLAN, (FILES, DB), dict(kind="adapter_agent", trigger="schedule", hourly=True,
                                     sources={"larkspur-files", "larkspur-db"})),
    ("Every Monday at 9am, summarise last week's new contracts for me.", (FILES, DB),
     dict(kind="agent", trigger="schedule", weekdays={1}, at=(9, 0), mode="read_only", actions={})),
    ("Every Friday at 5pm, read the Contracts folder and brief me. Read-only.", (FILES, DB),
     dict(kind="agent", trigger="schedule", weekdays={5}, at=(17, 0), mode="read_only",
          actions={}, sources={"larkspur-files"})),
    ("Every morning, check the CRM for invoices due today and tell me which ones are at risk.",
     (FILES, DB), dict(kind="agent", trigger="schedule", weekdays=set(range(7)), at=(9, 0),
                       mode="read_only", actions={}, sources={"larkspur-db"})),
    ("When a new contract lands in the Contracts folder, summarise its payment terms.",
     (FILES, DB), dict(kind="agent", trigger="event", event_on={"item_added"},
                       event_source="larkspur-files", glob_folder="Contracts",
                       mode="read_only", actions={})),
    ("Whenever an invoice is updated in the CRM database, save a note into the Follow-ups folder "
     "and tell me after.", (FILES, DB),
     dict(kind="agent", trigger="event", event_on={"item_changed"}, event_source="larkspur-db",
          mode="read_write", actions={"save": ("human_notified", "larkspur-files")})),
    ("Every 2 hours, move processed invoices into the Invoices folder automatically. To undo, "
     "move them back.", (FILES, DB),
     dict(kind="agent", trigger="schedule", every_hours=2, mode="read_write",
          actions={"move": ("autonomous", "larkspur-files")}, reversal="move them back")),
]


def _check_schedule(trigger, intent):
    cron = CronSchedule.parse(trigger.schedule.cron, trigger.schedule.timezone)
    assert trigger.schedule.timezone == "Asia/Singapore"          # the deployment default
    if "weekdays" in intent:
        assert set(cron.weekdays) == intent["weekdays"]
    if "at" in intent:
        assert (set(cron.hours), set(cron.minutes)) == ({intent["at"][0]}, {intent["at"][1]})
    if intent.get("hourly"):
        assert set(cron.hours) == set(range(24)) and len(cron.minutes) == 1
    if "every_hours" in intent:
        n = intent["every_hours"]
        assert set(cron.hours) == set(range(0, 24, n)) and len(cron.minutes) == 1


def _answer_sources(text, cat, res, **kw):
    """R-029 F-09: an agent whose words name no source is ASKED which to read (a task that reads
    no source sees nothing). When that is the question, it is the only one; the person confirms
    the sources the catalogue says hold what the words name, and the draft is compiled again."""
    from databridge_automation.compiler import SOURCES_QID, infer_sources

    if not any(q.id == SOURCES_QID for q in res.open_questions):
        return res, None
    assert res.spec is None and [q.id for q in res.open_questions] == [SOURCES_QID]
    again = compile_english(text, cat, answers={SOURCES_QID: "yes", **kw.pop("answers", {})},
                            **kw)
    return again, {cid for cid, _ in infer_sources(text, cat)}


@pytest.mark.parametrize("text,conns,intent", SAMPLES, ids=[s[0][:40] for s in SAMPLES])
def test_english_sample_compiles_to_its_stated_intent(text, conns, intent):
    res = compile_english(text, catalogue(*conns), harness=None)      # offline: rules only
    res, inferred = _answer_sources(text, catalogue(*conns), res, harness=None)
    if inferred is not None:
        assert inferred and not intent.get("sources")      # only a request naming no source
        intent = {**intent, "sources": inferred}
    # the only question allowed is an adapter agent's unapproved crawl plan (R-017 #2), one per
    # connection whose plan the catalogue says is not approved — derived, not listed
    unapproved = {f"plan:{c.id}" for c in conns if c.plan_status != "approved"}
    assert all(q.id in unapproved for q in res.open_questions), [q.text for q in res.open_questions]
    assert res.model_used is False
    assert res.kind == intent["kind"]
    spec = res.spec
    assert spec is not None and res.created_from == text
    assert spec.created_from == text                                   # verbatim, never reworded
    assert load_spec(yaml.safe_load(res.spec_yaml)) == spec             # the YAML round-trips
    assert res.understood.strip()
    trig = spec.trigger if isinstance(spec, WorkflowSpec) else spec.sync
    assert trig.type == intent["trigger"]
    if trig.type == "schedule":
        _check_schedule(trig, intent)
    if trig.type == "event":
        assert set(trig.event.on) == intent["event_on"]
        assert trig.event.source == intent["event_source"]
        if "glob_folder" in intent:
            assert f"/{intent['glob_folder']}/" in trig.event.filter["uri_glob"]
    if isinstance(spec, AdapterAgentSpec):
        assert set(spec.connections) == intent["sources"]
        assert spec.extract.pack == PACK
        for cid in spec.connections:                   # plan status comes from the catalogue
            want = next(c for c in conns if c.id == cid).plan_status
            assert spec.plans.get(cid) == want
        asked = {q.id for q in res.open_questions}
        assert asked == {f"plan:{c}" for c in spec.connections if spec.plans.get(c) != "approved"}
        assert res.activatable is (not asked)
        return
    assert spec.mode == intent["mode"]
    if "sources" in intent:
        assert set(spec.sources) == intent["sources"]
    got = {}
    for a in spec.actions:
        verb = a.name.split("_")[0]
        got[verb] = (a.automation_mode, parse_tool(a.tool)["connection"])
        assert parse_tool(a.tool)["verb"] == "write"
        conn = next(c for c in conns if c.id == parse_tool(a.tool)["connection"])
        assert conn.tool(parse_tool(a.tool)["tool"]).write        # a real write tool
    assert got == intent["actions"]
    if intent.get("reversal"):
        assert all(a.reversal == intent["reversal"] for a in spec.actions)
    # read tools are real read tools of declared sources; no write tool among them
    for t in spec.tools:
        p = parse_tool(t)
        assert p["verb"] != "write"
        if p["family"] == "source":
            assert p["connection"] in spec.sources


def test_plan_examples_need_no_model():
    """The build plan's two example sentences and the adapter sentence compile fully offline."""
    for text in (OVERDUE, BRIEFING, ADAPTER_PLAN):
        res = compile_english(text, catalogue(FILES, DB), harness=None)
        res, _ = _answer_sources(text, catalogue(FILES, DB), res, harness=None)
        assert res.spec is not None and not res.open_questions and not res.model_used


# ---------------------------------------------------------------------------------------------
# never guess: unknown references and missing decisions become questions, and nothing activates
# ---------------------------------------------------------------------------------------------
def _assert_blocked(res, qid_prefix):
    assert res.spec is None and res.spec_yaml is None
    assert not res.activatable
    assert any(q.id.startswith(qid_prefix) for q in res.open_questions), \
        [q.id for q in res.open_questions]
    with pytest.raises(ActivationRefused):
        save_draft(res, activate=True)


def test_unknown_folder_is_a_question():
    res = compile_english("When a new file appears in the Receipts folder, summarise it.",
                          catalogue(FILES, DB))
    _assert_blocked(res, "folder:Receipts")
    q = next(q for q in res.open_questions if q.id == "folder:Receipts")
    known = {f"{c.id}:{loc}" for c in (FILES, DB) for loc in c.locations}
    assert q.options and set(q.options) <= known          # offers only real folders


def test_unconnected_system_is_a_question():
    res = compile_english("Every weekday at 9am, pull new deals from Salesforce and summarise "
                          "them.", catalogue(FILES, DB))
    _assert_blocked(res, "source:salesforce")


def test_missing_source_type_is_a_question():
    res = compile_english(ADAPTER_FOUNDER, catalogue(FILES, DB))          # no Drive connected
    _assert_blocked(res, "source:drive")


def test_folder_in_two_sources_is_a_question_and_an_answer_settles_it():
    text = "When a new contract lands in the Contracts folder, summarise its payment terms."
    res = compile_english(text, catalogue(FILES, DB, DRIVE))
    _assert_blocked(res, "folder:Contracts")
    q = next(q for q in res.open_questions if q.id == "folder:Contracts")
    assert {o.split(":")[0] for o in q.options} == {"larkspur-files", "larkspur-drive"}
    pick = next(o for o in q.options if o.startswith("larkspur-drive:"))
    res2 = compile_english(text, catalogue(FILES, DB, DRIVE), answers={q.id: pick})
    assert not res2.open_questions
    assert res2.spec.trigger.event.source == "larkspur-drive"
    assert pick.split(":", 1)[1] in res2.spec.trigger.event.filter["uri_glob"]


def test_just_do_it_without_reversal_asks_how_to_undo():
    text = "Every day at 6pm, save a digest into the Follow-ups folder. Just do it."
    res = compile_english(text, catalogue(FILES, DB))
    _assert_blocked(res, "reversal:")
    qid = next(q.id for q in res.open_questions if q.id.startswith("reversal:"))
    res2 = compile_english(text, catalogue(FILES, DB), answers={qid: "delete the digest file"})
    assert not res2.open_questions
    (action,) = res2.spec.actions
    assert action.automation_mode == "autonomous" and action.reversal == "delete the digest file"


def test_write_without_approval_words_asks_for_the_mode():
    res = compile_english("Every day at 6pm, save a digest into the Follow-ups folder.",
                          catalogue(FILES, DB))
    _assert_blocked(res, "mode:")
    q = next(q for q in res.open_questions if q.id.startswith("mode:"))
    assert set(q.options) == {"human_required", "human_notified", "autonomous"}


def test_ambiguous_hour_is_a_question_and_pm_answer_gives_17():
    text = "Every weekday at 5, check the CRM for new overdue invoices."
    res = compile_english(text, catalogue(FILES, DB))
    _assert_blocked(res, "schedule_ampm")
    res2 = compile_english(text, catalogue(FILES, DB), answers={"schedule_ampm": "pm"})
    cron = CronSchedule.parse(res2.spec.trigger.schedule.cron, "UTC")
    assert set(cron.hours) == {17}


def test_read_only_and_a_write_is_a_question():
    res = compile_english("Every day at 6pm, save a digest into the Follow-ups folder and tell me "
                          "after. Stay read-only.", catalogue(FILES, DB))
    _assert_blocked(res, "mode")


def test_write_target_without_write_tool_is_a_question():
    res = compile_english("Every day at 6pm, save a digest into the Contracts folder in Drive "
                          "and tell me after.", catalogue(FILES, DB, DRIVE))
    _assert_blocked(res, "tool:")


def test_write_tool_that_does_not_fit_the_verb_is_a_question():
    """The CRM's only write tool inserts rows; 'update' is not an insert, so it asks."""
    res = compile_english("Every day at 6pm, update the overdue invoices in the CRM and tell me "
                          "after.", catalogue(FILES, DB))
    _assert_blocked(res, "tool:")


def test_explicit_timezone_wins_over_default():
    res = compile_english("Every weekday at 9am UTC, check the CRM for overdue invoices.",
                          catalogue(FILES, DB))
    assert res.spec.trigger.schedule.timezone == "UTC"


# ---------------------------------------------------------------------------------------------
# governance: the declaration is generated and really compiled
# ---------------------------------------------------------------------------------------------
def test_overdue_declaration_compiles_and_activates(tmp_path):
    root = repo_with_golden(tmp_path)
    cat = catalogue(FILES, DB, golden="packs/workspace/evals/automations.jsonl")
    res = compile_english(OVERDUE, cat, purposes=purposes(), repo_root=root)
    assert res.compilation.outcome == "compiled", res.compilation.refusals
    assert res.activatable
    decl = yaml.safe_load(res.declaration_yaml)
    assert decl["agent"] == res.spec.agent and decl["pack"] == PACK
    modes = {next(iter(a)): a[next(iter(a))]["automation"]["mode"] for a in decl["actions"]}
    assert modes == {a.name: a.automation_mode for a in res.spec.actions}      # 1:1 with the spec
    # no write tool granted as a capability; every capability maps to a spec tool
    grants = res.compilation.decision.compiled.grants.grants
    assert {g.source_id + "." + g.capability for g in grants} == set(decl["capabilities"])
    written = save_draft(res, activate=True, agents_dir=tmp_path / "agents")
    names = sorted(p.name for p in written)
    assert names == sorted([f"{res.spec.agent}.workflow.yaml", f"{res.spec.agent}.agent.yaml"])
    assert load_spec(tmp_path / "agents" / f"{res.spec.agent}.workflow.yaml").status == "active"


def test_read_only_declaration_has_no_actions(tmp_path):
    root = repo_with_golden(tmp_path)
    cat = catalogue(FILES, DB, golden="packs/workspace/evals/automations.jsonl")
    res = compile_english(BRIEFING, cat, purposes=purposes(), repo_root=root,
                          answers={"sources": "yes"})               # R-029 F-09: confirmed
    assert res.compilation.compiled
    # the runtime generator writes an explicit empty list (W2); the claim is: no write path at all
    assert not yaml.safe_load(res.declaration_yaml).get("actions")
    assert not res.compilation.decision.compiled.actions


def test_autonomous_with_reversal_compiles(tmp_path):
    root = repo_with_golden(tmp_path)
    cat = catalogue(FILES, DB, golden="packs/workspace/evals/automations.jsonl")
    res = compile_english("Every 2 hours, move processed invoices into the Invoices folder "
                          "automatically. To undo, move them back.", cat,
                          purposes=purposes(), repo_root=root)
    assert res.compilation.compiled, res.compilation.refusals
    (a,) = yaml.safe_load(res.declaration_yaml)["actions"]
    body = next(iter(a.values()))
    assert body["automation"]["mode"] == "autonomous" and body["reversal"]


def test_uncompiled_declaration_is_not_activatable(tmp_path):
    """No purpose vocabulary / golden set for the pack -> compile_agent refuses -> no activation,
    but the draft can still be saved for review."""
    res = compile_english(OVERDUE, catalogue(FILES, DB), repo_root=tmp_path,
                          packs_dir=tmp_path / "packs")
    assert res.spec is not None and not res.compilation.compiled
    assert not res.activatable
    with pytest.raises(ActivationRefused):
        save_draft(res, activate=True, agents_dir=tmp_path / "agents")
    saved = save_draft(res, agents_dir=tmp_path / "agents")
    assert all(p.exists() for p in saved)
    assert load_spec(saved[0]).status == "draft"


def test_subject_touching_write_must_be_human_required():
    res = compile_english("Every day at 6pm, add a note to each person in the CRM with their "
                          "latest invoice total and tell me after.", catalogue(FILES, DB))
    assert res.spec is not None and not res.activatable
    (a,) = res.spec.actions
    assert a.automation_mode == "human_notified"          # what the user said ...
    q = next(q for q in res.open_questions if q.id == f"mode:{a.name}")
    assert q.options == ["human_required"]                # ... is not allowed for a person write


def test_a_bare_number_after_at_is_not_a_schedule():
    res = compile_english("Look at 3 invoices in the CRM and tell me which are overdue.",
                          catalogue(FILES, DB))
    assert not res.open_questions and res.spec.trigger.type == "manual"


def test_a_time_without_a_frequency_is_a_question():
    res = compile_english("At 6pm, check the CRM for overdue invoices.", catalogue(FILES, DB))
    _assert_blocked(res, "schedule_frequency")
