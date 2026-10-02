"""`delegate.ask:<adapter agent>` inside a governed workflow-agent run (agent_runtime + FED).

The REAL compiler, the REAL harness over a scripted FakeBackend, the real facts store. Proves the
delegate tool is admitted like any other tool (declared + granted), that the delegate reads only
the calling agent's declared sources (a refusal is reduced coverage, not a silent gap), and that an
agent whose delegate cannot be served does not run at all.
"""
from __future__ import annotations

import json

import pytest

from fed_testkit import CRM, HARNESS_CONFIG

PACK, PURPOSE = "workspace", "workspace.agent_run"


@pytest.fixture
def compile_inputs(tmp_path):
    from databridge_agent_runtime import CompileInputs

    root = tmp_path / "repo"
    (root / "packs" / PACK / "evals").mkdir(parents=True)
    (root / "packs" / PACK / "purposes.yaml").write_text(
        f"pack: {PACK}\npurposes:\n  - id: {PURPOSE}\n    description: run a user agent\n"
        f"    subject_scoped: false\n    lawful_basis: legitimate interests\n", encoding="utf-8")
    (root / "packs" / PACK / "evals" / "agent_contract.jsonl").write_text(
        json.dumps({"question": "q", "expect": "cited"}) + "\n", encoding="utf-8")
    return CompileInputs(packs_dir=root / "packs", repo_root=root)


def _pair(**over):
    from databridge_agent_runtime import Governance, declaration_from_workflow, \
        parse_declaration_text
    from databridge_workflow_spec import WorkflowSpec

    d = {"agent": "overdue_counter", "title": "Overdue counter",
         "created_from": "How many overdue invoices do we have?", "mode": "read_only",
         "sources": [CRM], "delegates": ["crm_sources"], "tools": ["delegate.ask:crm_sources"],
         "instructions": "1. Ask the CRM delegate. 2. Answer with the count, cited.",
         "limits": {"max_steps": 4, "max_tool_calls": 4, "tokens_per_run": 20000}}
    d.update(over)
    spec = WorkflowSpec.model_validate(d)
    text = declaration_from_workflow(spec, PACK, Governance(
        purpose=PURPOSE, golden_set=f"packs/{PACK}/evals/agent_contract.jsonl",
        escalations_per_day=10, cost_ceiling_usd_per_day=5.0))
    assert "delegate.crm_sources" in text or "delegate." in text
    return spec, parse_declaration_text(text, declared_in=f"var/agents/{spec.agent}.agent.yaml")


def _planner(tool: str, subtask: dict):
    from databridge_harness import FakeBackend, Harness
    state = {"i": 0, "obs": None}

    def respond(model, messages, guided_json):
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return json.dumps({"tool_calls": [{"tool": tool, "args": {"subtask": subtask}}]})
        obs = json.loads(messages[-1]["content"][len("Observations:\n"):])
        state["obs"] = obs
        rows = (obs[0].get("result") or {}).get("rows") or []
        if not rows:
            return json.dumps({"final": {"claims": []}})
        cell = rows[0]["cells"]["count"]
        return json.dumps({"final": {"claims": [
            {"text": f"There are {cell['value']} overdue invoices.",
             "citations": [cell["ref"]]}]}})

    return Harness(FakeBackend(respond), config=HARNESS_CONFIG, profile="cloud",
                   cache=None), state


COUNT_OVERDUE = {"entity_kinds": ["invoices"], "aggregate": {"op": "count"},
                 "filters": [{"field": "status", "op": "eq", "value": "overdue"}]}


def test_agent_delegates_a_counted_subquery_and_cites_it(world, compile_inputs):
    from databridge_agent_runtime import run_agent

    spec, decl = _pair()
    harness, state = _planner("delegate.ask:crm_sources", COUNT_OVERDUE)
    rec = run_agent(spec, decl, store=world.store, harness=harness,
                    federation=world.service(), facts=world.facts, compile_inputs=compile_inputs,
                    runs_dir=world.runs_dir, vocabulary=world.vocab)
    assert rec.status == "completed", rec.answer.markdown
    expected = sum(1 for r in world.invoices if r["status"] == "overdue")
    assert rec.answer.unsupported_claims == []
    assert str(expected) in rec.answer.markdown
    step = rec.steps[0]
    assert step.tool == "delegate.ask:crm_sources"
    assert step.policy_decision["decision"] == "permit"
    assert step.policy_decision["capability"] == "delegate.crm_sources"
    # pushed down: ONE statement reads the data. FIN-12 adds one read-only spelling probe per text
    # `eq` filter (which stored spellings share the value), never a row read — derived from the
    # sub-task's own filters
    probes = [c for c in world.facts.calls if c[0].startswith("SELECT DISTINCT CAST(")]
    text_eq = [f for f in COUNT_OVERDUE["filters"] if f["op"] == "eq"]
    assert text_eq and len(probes) == len(text_eq)
    assert len(world.facts.calls) - len(probes) == 1
    assert CRM in rec.coverage.searched


def test_delegate_cannot_read_a_source_the_agent_is_not_given(world, compile_inputs):
    from databridge_agent_runtime import run_agent

    spec, decl = _pair(sources=["some-other-db"])               # crm-db NOT declared
    harness, state = _planner("delegate.ask:crm_sources", COUNT_OVERDUE)
    rec = run_agent(spec, decl, store=world.store, harness=harness,
                    federation=world.service(), facts=world.facts, compile_inputs=compile_inputs,
                    runs_dir=world.runs_dir, vocabulary=world.vocab)
    assert world.facts.calls == []                              # nothing was read
    assert any(s["reason_code"] == "delegate_skipped" for s in rec.coverage.skipped)
    assert rec.answer.markdown.startswith("> **Reduced coverage.**")
    assert CRM in rec.answer.markdown


def test_agent_with_an_unservable_delegate_does_not_run(world, compile_inputs):
    from databridge_agent_runtime import run_agent

    spec, decl = _pair()
    harness, state = _planner("delegate.ask:crm_sources", COUNT_OVERDUE)
    rec = run_agent(spec, decl, store=world.store, harness=harness, federation=None,
                    facts=world.facts, compile_inputs=compile_inputs, runs_dir=world.runs_dir,
                    vocabulary=world.vocab)
    assert rec.status == "refused"
    assert state["i"] == 0                                      # the model was never called
    assert "delegate.ask:crm_sources" in rec.answer.markdown


def test_the_model_cannot_redirect_a_delegate_through_its_arguments(world, compile_inputs):
    """The adapter agent is the one the TOOL NAME names; an `adapter_agent` in the args is
    overwritten, so a model cannot reach notes_sources through crm_sources' grant."""
    from databridge_agent_runtime import run_agent

    spec, decl = _pair()
    sneaky = {**COUNT_OVERDUE, "adapter_agent": "notes_sources"}
    harness, state = _planner("delegate.ask:crm_sources", sneaky)
    rec = run_agent(spec, decl, store=world.store, harness=harness,
                    federation=world.service(), facts=world.facts, compile_inputs=compile_inputs,
                    runs_dir=world.runs_dir, vocabulary=world.vocab)
    assert rec.status == "completed"
    assert world.retriever.calls == []
    assert state["obs"][0]["result"]["adapter_agent"] == "crm_sources"
