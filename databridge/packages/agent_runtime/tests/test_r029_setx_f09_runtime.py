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
