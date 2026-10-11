"""R-029 SETX F-09 at the runtime (E2E run 5, finance): a task that declared no source ran 130 s -
three model calls, four tool calls, every graph pointer withheld as out of scope - and then said
"No answer was produced".

* A spec that declares no source and no delegate can read nothing (its scope IS its sources): the
  runtime refuses it before any model call, saying so (defence behind the compiler's question).
* Every run is bounded in wall-clock time: near the budget the planner is told to wrap up, and at
  the budget the run stops with an honest reason - never an open-ended wait.
Expectations come from the spec and the budget the test sets, never written down.
"""
from __future__ import annotations

import pytest

from agent_runtime_helpers import briefing_spec, make_pair, scripted_harness


def _run(spec, decl, script, *, store, gateway, compile_inputs, tmp_path, **kw):
    from databridge_agent_runtime import run_agent

    harness, backend = scripted_harness(script)
    rec = run_agent(spec, decl, store=store, harness=harness, gateway=gateway,
                    trigger={"type": "one_time", "by": "dana", "question": None},
                    compile_inputs=compile_inputs, notifier=lambda _e: None,
                    runs_dir=tmp_path / "runs", capture_dir=tmp_path / "capture", **kw)
    return rec, backend


def test_f09_a_task_that_declares_no_source_is_refused_before_any_model_call(
        store, gateway, compile_inputs, tmp_path):
    spec, decl = make_pair(briefing_spec(sources=[], tools=["graph.search", "graph.node"]))
    assert not spec.sources and not getattr(spec, "delegates", None)     # the run-5 shape
    calls = []

    def script(i, obs):
        calls.append(i)
        return {"final": {"claims": []}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gateway,
                  compile_inputs=compile_inputs, tmp_path=tmp_path)
    assert calls == [] and rec.status == "refused"
    assert rec.outcome.reason_code == "agent_not_runnable"
    assert "no source" in rec.answer.markdown and "No answer was produced" not in \
        rec.answer.markdown


def test_f09_a_run_is_bounded_in_time_and_says_why_it_stopped(store, gateway, compile_inputs,
                                                              tmp_path, monkeypatch):
    """The planner keeps asking for a tool; the wall clock (a fake, advanced per model call) runs
    past the budget the test sets; the run stops with that reason."""
    import databridge_agent_runtime.runtime as rt

    budget = 30.0
    monkeypatch.setenv(rt.RUN_SECONDS_ENV, str(budget))
    now = [0.0]
    monkeypatch.setattr(rt, "_clock", lambda: now[0])
    spec, decl = make_pair(briefing_spec(limits={"max_steps": 50, "max_tool_calls": 200,
                                                 "tokens_per_run": 200000}))
    turns = []

    def script(i, obs):
        turns.append(i)
        now[0] += budget / 4 + 1                    # each turn costs a quarter of the budget+
        return {"tool_calls": [{"tool": "graph.search", "args": {"query": f"q{i}"}}]}

    rec, _ = _run(spec, decl, script, store=store, gateway=gateway,
                  compile_inputs=compile_inputs, tmp_path=tmp_path)
    assert len(turns) < 50                                   # it did not run to the step limit
    assert now[0] <= budget + 2 * (budget / 4 + 1)          # at most one turn past wrap-up
    md = rec.answer.markdown
    assert "time budget" in md and f"{int(budget)}" in md, md


def test_f1_2_a_delegate_only_task_is_refused_before_any_model_call(store, gateway,
                                                                   compile_inputs, tmp_path):
    """R-029 Codex f1 #2: a delegate reads only within the caller's sources, so a task with a
    delegate and no source is as blind as one with neither."""
    from databridge_agent_runtime.runtime import readable_sources

    spec, decl = make_pair(briefing_spec(sources=[], tools=["graph.search"],
                                         delegates=["keeper-x"]))
    assert spec.delegates and not readable_sources(spec)
    calls = []
    rec, _ = _run(spec, decl, lambda i, obs: calls.append(i) or {"final": {"claims": []}},
                  store=store, gateway=gateway, compile_inputs=compile_inputs, tmp_path=tmp_path)
    assert calls == [] and rec.status == "refused"
    assert "no source it can read" in rec.answer.markdown


def test_f1_2_readable_sources_excludes_a_destination_with_no_read_tool():
    from agent_runtime_helpers import followup_spec
    from databridge_agent_runtime.runtime import readable_sources
    from databridge_workflow_spec import WorkflowSpec, parse_tool

    spec = WorkflowSpec.model_validate(followup_spec())
    written = {parse_tool(a.tool)["connection"] for a in spec.actions}
    read = {parse_tool(t)["connection"] for t in spec.tools if parse_tool(t)["verb"] != "write"}
    got = readable_sources(spec)
    assert got and set(got) == {s for s in spec.sources if s not in written or s in read}
    assert all(s not in got for s in written - read)                    # non-vacuous below
    assert written - read


# --- R-029 Codex f1 #3: the deadline holds around blocking calls and writes -------------------
def test_f1_3_a_model_call_that_outlasts_the_budget_stops_the_run(store, gateway, compile_inputs,
                                                                 tmp_path, monkeypatch):
    import time

    import databridge_agent_runtime.runtime as rt

    budget = 1.0
    monkeypatch.setenv(rt.RUN_SECONDS_ENV, str(budget))
    spec, decl = make_pair(briefing_spec())

    def slow(i, obs):
        time.sleep(budget * 4)                       # the model outlasts the whole budget
        return {"final": {"claims": []}}

    t0 = time.monotonic()
    rec, _ = _run(spec, decl, slow, store=store, gateway=gateway, compile_inputs=compile_inputs,
                  tmp_path=tmp_path)
    assert time.monotonic() - t0 < budget * 3                 # it did not wait for the model
    assert rec.status != "completed" and "time budget" in rec.answer.markdown


def test_f1_3_a_reply_after_the_deadline_starts_no_automatic_write(store, compile_inputs,
                                                                  tmp_path, monkeypatch):
    import databridge_agent_runtime.runtime as rt
    from agent_runtime_helpers import FakeGateway, followup_spec, refs_in

    budget = 30.0
    monkeypatch.setenv(rt.RUN_SECONDS_ENV, str(budget))
    now = [0.0]
    monkeypatch.setattr(rt, "_clock", lambda: now[0])
    gw = FakeGateway()
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    auto = [a.name for a in spec.actions if a.automation_mode != "human_required"]
    assert auto                                                          # non-vacuous

    def script(i, obs):
        if i == 0:
            return {"tool_calls": [{"tool": "source.query:larkspur-db",
                                    "args": {"sql": "SELECT invoice_no FROM invoices"}}]}
        now[0] = budget + 5                          # this reply lands after the deadline
        return {"actions": [{"name": n, "args": {"path": "/Follow-ups/x.txt", "content": "x"},
                             "derived_from": refs_in(obs)[:1]} for n in auto],
                "final": {"claims": []}}

    rec, _ = _run(spec, decl, script, store=store, gateway=gw, compile_inputs=compile_inputs,
                  tmp_path=tmp_path)
    assert gw.writes() == []
    refused = [s for s in rec.coverage.skipped if s.get("reason_code") == "time_budget"]
    assert {s["action"] for s in refused} == set(auto)


# --- R-029 Codex f1 #6: every trigger carries a validated actor --------------------------------
def test_f1_6_trigger_actors_are_validated_or_named_honestly():
    import datetime as dt

    from databridge_agent_runtime.runtime import _trigger

    at = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)
    alice = {"principal_id": "alice", "authenticated_by": "gateway",
             "principal_type": "AuthenticatedPrincipal", "dev_only": False}
    t = _trigger({"type": "manual", "by": "alice", "actor": alice}, at)
    assert t["actor"]["principal_id"] == "alice" and t["actor"]["authenticated_by"] == "gateway"
    with pytest.raises(Exception):
        _trigger({"type": "manual", "by": "bob", "actor": alice}, at)       # inconsistent
    with pytest.raises(Exception):
        _trigger({"type": "manual", "by": "alice", "actor": {"principal_id": "alice"}}, at)
    sched = _trigger({"type": "schedule", "by": None}, at)
    assert sched["actor"]["principal_type"] != "AuthenticatedPrincipal"
    assert sched["actor"]["authenticated_by"] == "agent_runtime"
    typed = _trigger({"type": "manual", "by": "dana"}, at)
    assert typed["actor"]["principal_id"] == "dana"
    assert typed["actor"]["authenticated_by"].startswith("unverified")   # never passed off
