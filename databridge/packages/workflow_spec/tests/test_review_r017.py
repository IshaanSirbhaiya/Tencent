"""Codex review t01 (wiki/40-reviews/R-017): each REAL finding, red before the fix, green after."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from databridge_workflow_spec import ActionSpec, SpecError, load_spec

from test_workflow_spec import ADAPTER, READ_ONLY, READ_WRITE


def _mut(base, **changes):
    d = copy.deepcopy(base)
    d.update(changes)
    return d


# -- finding 1: "frozen" must mean deeply immutable --------------------------------------------
def test_validated_specs_cannot_be_mutated_in_place():
    ro = load_spec(READ_ONLY)
    rw = load_spec(READ_WRITE)
    ad = load_spec(ADAPTER)
    ev = load_spec(_mut(READ_ONLY, trigger={"type": "event", "event": {
        "source": "larkspur-files", "on": ["item_added"], "filter": {"uri_glob": "**/x/**"}}}))
    write = ActionSpec(name="w", tool="source.write:larkspur-files:write_file",
                       automation_mode="human_required")
    attempts = [
        lambda: ro.actions.append(write),              # the exact bypass codex describes
        lambda: ro.tools.append("source.query:larkspur_db"),
        lambda: ro.sources.extend(["elsewhere"]),
        lambda: rw.actions.pop(),
        lambda: ad.plans.__setitem__("larkspur-files", "approved"),
        lambda: ad.plans.update({"x": "approved"}),
        lambda: ad.connections.append("unmanaged"),
        lambda: ad.screen.__setitem__("pack", "none"),
        lambda: ev.trigger.event.filter.__setitem__("uri_glob", "**"),
        lambda: ev.trigger.event.on.append("item_removed"),
    ]
    for attempt in attempts:
        with pytest.raises(TypeError):
            attempt()
    # still ordinary sequences/mappings to read (consumers compare with lists / dicts)
    assert ro.sources == READ_ONLY["sources"] and ad.plans == ADAPTER["plans"]
    # defaults are frozen too, not a fresh mutable list
    with pytest.raises(TypeError):
        load_spec(_mut(READ_ONLY, sources=[], tools=["graph.search"])).actions.append(write)


def test_frozen_specs_still_copy_dump_and_reload():
    spec = load_spec(READ_WRITE)
    assert spec.model_copy(deep=True) == spec
    assert copy.deepcopy(spec) == spec
    assert load_spec(spec.model_dump(mode="json")) == spec


# -- finding 2: read-only workflows have no persistence path and query has no tool suffix -------
def test_read_only_workflow_cannot_save_output():
    with pytest.raises(SpecError, match="save_to"):
        load_spec(_mut(READ_ONLY, outputs={"format": "cited_markdown", "save_to": "/Out/x.md"}))
    assert load_spec(_mut(READ_WRITE, outputs={"format": "json", "save_to": None}))


def test_source_query_takes_no_tool_suffix():
    """`source.query:<conn>` sends ONE checked statement; a suffix naming an arbitrary server tool
    (`source.query:crm:delete_or_update`) is not part of the grammar."""
    with pytest.raises(SpecError, match="source.query"):
        load_spec(_mut(READ_ONLY, tools=["source.query:larkspur_db:delete_or_update"]))


# -- finding 7: cron and timezone are validated for meaning, not only shape ---------------------
@pytest.mark.parametrize("cron", ["99 99 99 99 99", "BOGUS * * * *", "0 24 * * *", "0 9 0 * *",
                                  "0 9 * 13 *", "0 9 * * 8", "*/0 * * * *", "5-1 * * * *",
                                  "0 9 * * mon-", "0 9 ,, * *"])
def test_meaningless_cron_is_refused(cron):
    with pytest.raises(SpecError, match="cron"):
        load_spec(_mut(READ_WRITE, trigger={"type": "schedule", "schedule": {"cron": cron}}))


@pytest.mark.parametrize("cron", ["0 9 * * 1-5", "*/15 * * * *", "0 0 1 jan *", "0 9 * * mon-fri",
                                  "0 0 * * 7", "0,30 8-18/2 1-15 * *", "5/10 * * * *"])
def test_meaningful_cron_is_accepted(cron):
    assert load_spec(_mut(READ_WRITE, trigger={"type": "schedule", "schedule": {"cron": cron}}))


def test_unknown_timezone_is_refused():
    with pytest.raises(SpecError, match="timezone"):
        load_spec(_mut(READ_WRITE, trigger={"type": "schedule", "schedule": {
            "cron": "0 9 * * 1-5", "timezone": "Mars/Olympus_Mons"}}))


def test_cron_agrees_with_the_trigger_engine_parser():
    """Whatever the spec accepts, the scheduler can run, and vice versa (derived, not listed)."""
    p = str(Path(__file__).resolve().parents[2] / "automation")
    if p not in sys.path:
        sys.path.append(p)
    cron = pytest.importorskip("databridge_automation.cron")
    corpus = ["0 9 * * 1-5", "*/15 * * * *", "0 0 1 jan *", "0 9 * * mon-fri", "0 0 * * 7",
              "0,30 8-18/2 1-15 * *", "5/10 * * * *", "99 99 99 99 99", "BOGUS * * * *",
              "0 24 * * *", "0 9 0 * *", "*/0 * * * *", "5-1 * * * *", "0 9 * * 8", "* * * *"]
    agreed = 0
    for expr in corpus:
        try:
            cron.CronSchedule.parse(expr, "Asia/Singapore")
            engine_ok = True
        except cron.CronError:
            engine_ok = False
        try:
            load_spec(_mut(READ_WRITE, trigger={"type": "schedule", "schedule": {
                "cron": expr, "timezone": "Asia/Singapore"}}))
            spec_ok = True
        except SpecError:
            spec_ok = False
        assert spec_ok == engine_ok, expr
        agreed += 1
    assert agreed == len(corpus) and agreed > 0


# -- finding 8: exactly one trigger payload -----------------------------------------------------
@pytest.mark.parametrize("trigger", [
    {"type": "schedule", "schedule": {"cron": "0 9 * * 1-5"},
     "event": {"source": "larkspur-files", "on": ["item_added"]}},
    {"type": "event", "event": {"source": "larkspur-files", "on": ["item_added"]},
     "schedule": {"cron": "0 9 * * 1-5"}},
])
def test_trigger_carries_only_its_own_payload(trigger):
    with pytest.raises(SpecError, match="trigger"):
        load_spec(_mut(READ_WRITE, trigger=trigger))


# -- finding 9: adapter provenance is required --------------------------------------------------
def test_adapter_agent_needs_the_users_words():
    with pytest.raises(SpecError, match="created_from"):
        load_spec(_mut(ADAPTER, created_from="   "))


# -- finding 10: action names are really lower_snake_case ---------------------------------------
@pytest.mark.parametrize("name", ["save-draft", "save__draft", "_save", "save_", "Save", "9save"])
def test_action_name_is_lower_snake_case(name):
    acts = [{"name": name, "tool": "source.write:larkspur-files:write_file",
             "automation_mode": "human_required"}]
    with pytest.raises(SpecError, match="lower_snake_case"):
        load_spec(_mut(READ_WRITE, actions=acts))


# -- contract change (S2 + finding 2): facts.query, and actions are source.write only ------------
def test_facts_query_is_a_read_tool_on_a_declared_source():
    from databridge_workflow_spec import parse_tool
    assert parse_tool("facts.query:larkspur_db") == {
        "family": "facts", "op": "query", "verb": None, "connection": "larkspur_db", "tool": None}
    assert load_spec(_mut(READ_ONLY, tools=["facts.query:larkspur_db"]))
    with pytest.raises(SpecError, match="not in `sources`"):
        load_spec(_mut(READ_ONLY, tools=["facts.query:elsewhere"]))
    with pytest.raises(SpecError, match="known tool shape"):
        load_spec(_mut(READ_ONLY, tools=["facts.query:larkspur_db:execute_sql"]))


@pytest.mark.parametrize("tool", ["source.query:larkspur_db", "source.read:larkspur-files:x",
                                  "facts.query:larkspur_db", "source.write:larkspur-files"])
def test_an_action_is_only_ever_a_named_source_write(tool):
    acts = [{"name": "log_followup_task", "tool": tool, "automation_mode": "human_required"}]
    with pytest.raises(SpecError, match="source.write:<connection>:<tool>"):
        load_spec(_mut(READ_WRITE, actions=acts))


# -- contract addition (founder, 2026-10-01): AdapterAgentSpec.adapt ------------------------------
def test_adapt_defaults_when_the_block_is_absent():
    from databridge_workflow_spec import ALWAYS_ASKED, DRIFT_KINDS
    spec = load_spec(ADAPTER)
    assert "adapt" not in ADAPTER
    assert spec.adapt.policy == "auto_additive"
    assert (spec.adapt.watch.on_sync, spec.adapt.watch.on_list_changed) == (True, True)
    assert list(spec.adapt.always_ask) == list(DRIFT_KINDS)
    assert ALWAYS_ASKED <= set(DRIFT_KINDS) and len(ALWAYS_ASKED) == 2
    with pytest.raises(TypeError):
        spec.adapt.always_ask.remove("subject_touching")          # frozen like the rest


@pytest.mark.parametrize("keep", [["removed", "renamed"], ["new_write_tool"],
                                  ["subject_touching"], []])
def test_the_two_always_asked_kinds_cannot_be_removed(keep):
    with pytest.raises(SpecError, match="always_ask must keep"):
        load_spec(_mut(ADAPTER, adapt={"policy": "off", "always_ask": keep}))


def test_adapt_accepts_a_narrowed_list_that_keeps_the_two_and_refuses_unknowns():
    ok = load_spec(_mut(ADAPTER, adapt={"policy": "ask_all", "watch": {"on_list_changed": False},
                                        "always_ask": ["subject_touching", "new_write_tool",
                                                       "renamed"]}))
    assert ok.adapt.policy == "ask_all" and ok.adapt.watch.on_sync is True
    with pytest.raises(SpecError):
        load_spec(_mut(ADAPTER, adapt={"always_ask": ["new_write_tool", "subject_touching",
                                                      "vibes_changed"]}))
    with pytest.raises(SpecError):
        load_spec(_mut(ADAPTER, adapt={"policy": "sometimes"}))
    with pytest.raises(SpecError):
        load_spec(_mut(ADAPTER, adapt={"surprise": 1}))


@pytest.mark.parametrize("adapt", [None, {"policy": "off"},
                                   {"policy": "ask_all", "watch": {"on_sync": False},
                                    "always_ask": ["new_write_tool", "subject_touching"]}])
def test_adapt_round_trips_through_dump_and_load(adapt):
    from databridge_workflow_spec import dump_spec
    raw = ADAPTER if adapt is None else _mut(ADAPTER, adapt=adapt)
    spec = load_spec(raw)
    again = load_spec(dump_spec(spec))
    assert again == spec and again.adapt == spec.adapt


# -- contract addition (F2 / §5.8): optional `derivations` on both spec kinds ---------------------
def test_derivations_default_empty_and_accept_the_compilers_shape():
    from databridge_workflow_spec import dump_spec
    assert load_spec(READ_ONLY).derivations == [] and load_spec(ADAPTER).derivations == []
    compiler_rows = [   # exactly what databridge_automation's DraftResult.derivations holds
        {"field": "trigger.schedule.cron", "value": "0 9 * * 1-5", "by": "rule", "span": [0, 18],
         "evidence": "Every weekday at 9", "model": None},
        {"field": "title", "value": "Overdue follow-up", "by": "model", "span": None,
         "evidence": "", "model": "fake/planner", "task_class": "compile",
         "prompt_version": "compiler/1"},
        {"field": "actions[log].automation_mode", "value": "human_required", "by": "answer",
         "span": None, "evidence": "q3 = ask me", "model": None},
        {"field": "sources[x]", "value": ["a"], "by": "model+answer", "span": None,
         "evidence": "q1 = yes", "model": "fake/planner", "task_class": "compile",
         "prompt_version": "compiler/1"},
        {"field": "freshness.max_age_hours", "value": 24, "by": "default", "span": None,
         "evidence": "", "model": None}]
    spec = load_spec(_mut(READ_WRITE, derivations=compiler_rows))
    got = [(d.field, d.produced_by, d.basis) for d in spec.derivations]
    want_kind = {"rule": "rules", "model": "model", "answer": "user", "model+answer": "model",
                 "default": "rules"}
    assert got == [(r["field"], want_kind[r["by"]], r["by"]) for r in compiler_rows]
    assert spec.derivations[1].model == "fake/planner"
    assert load_spec(dump_spec(spec)) == spec                           # round trip
    with pytest.raises(TypeError):
        spec.derivations[3].value.append("b")                           # deeply immutable
    assert load_spec(_mut(ADAPTER, derivations=compiler_rows[:1])).derivations[0].produced_by \
        == "rules"


@pytest.mark.parametrize("bad", [{"field": "", "produced_by": "rules"},
                                 {"field": "x", "produced_by": "oracle"},
                                 {"field": "x", "produced_by": "rules", "span": [5, 1]},
                                 {"field": "x", "produced_by": "rules", "surprise": 1}])
def test_a_malformed_derivation_is_refused(bad):
    with pytest.raises(SpecError):
        load_spec(_mut(READ_ONLY, derivations=[bad]))
