"""The contract accepts the plan's example agents and refuses each structural violation."""
from __future__ import annotations

import copy

import pytest

from databridge_workflow_spec import (
    AUTOMATION_MODES, AdapterAgentSpec, SpecError, WorkflowSpec, dump_spec, load_spec, parse_tool,
)

READ_ONLY = {
    "spec": "workflow/v1",
    "agent": "account_briefing",
    "title": "Account briefing",
    "created_from": "Brief me on a customer before a call.",
    "mode": "read_only",
    "sources": ["larkspur_db", "larkspur-files"],
    "tools": ["graph.search", "graph.neighbors", "retrieve.search", "source.query:larkspur_db"],
    "instructions": "1. Find the customer. 2. Gather invoices, contacts, contract terms. 3. Cite.",
}

READ_WRITE = {
    "spec": "workflow/v1",
    "agent": "overdue_invoice_followup",
    "title": "Overdue invoice follow-up",
    "created_from": "Every weekday at 9am, find invoices more than 14 days overdue ...",
    "mode": "read_write",
    "sources": ["larkspur_db", "larkspur-files", "larkspur_db_writer"],
    "tools": ["graph.search", "retrieve.search", "source.query:larkspur_db",
              "facts.query:larkspur_db", "source.read:larkspur-files:read_text_file"],
    "actions": [
        {"name": "save_followup_draft", "tool": "source.write:larkspur-files:write_file",
         "automation_mode": "human_notified"},
        {"name": "log_followup_task", "tool": "source.write:larkspur_db_writer:execute_sql",
         "automation_mode": "human_required"},
    ],
    "trigger": {"type": "schedule", "schedule": {"cron": "0 9 * * 1-5", "timezone": "Asia/Singapore"}},
    "instructions": "1. Query overdue invoices. 2. Look up terms. 3. Draft. 4. Log task.",
}

ADAPTER = {
    "spec": "adapter_agent/v1",
    "id": "larkspur_workspace_sources",
    "title": "Larkspur workspace sources",
    "created_from": "Keep the Larkspur files folder and CRM database in the graph; check hourly",
    "connections": ["larkspur-files", "larkspur_db"],
    "plans": {"larkspur-files": "approved", "larkspur_db": "approved"},
    "sync": {"type": "schedule", "schedule": {"cron": "0 * * * *"}},
    "extract": {"pack": "workspace"},
}


@pytest.mark.parametrize("raw,model", [(READ_ONLY, WorkflowSpec), (READ_WRITE, WorkflowSpec),
                                       (ADAPTER, AdapterAgentSpec)])
def test_plan_examples_load_and_round_trip(raw, model):
    spec = load_spec(raw)
    assert isinstance(spec, model)
    again = load_spec(dump_spec(spec))
    assert again == spec


def _mut(base, **changes):
    d = copy.deepcopy(base)
    d.update(changes)
    return d


@pytest.mark.parametrize("raw,needle", [
    (_mut(READ_ONLY, actions=READ_WRITE["actions"]), "read_only"),
    (_mut(READ_WRITE, actions=[{"name": "x", "tool": "source.write:larkspur-files:write_file",
                                "automation_mode": "autonomous"}]), "reversal"),
    (_mut(READ_ONLY, tools=["source.query:crm-elsewhere"]), "not in `sources`"),
    (_mut(READ_ONLY, tools=["source.write:larkspur_db:x"]), "declared as `actions`"),
    (_mut(READ_ONLY, tools=["shell.exec"]), "known tool shape"),
    (_mut(READ_WRITE, trigger={"type": "schedule"}), "schedule"),
    (_mut(READ_WRITE, trigger={"type": "schedule", "schedule": {"cron": "every day"}}), "five fields"),
    (_mut(READ_ONLY, trigger={"type": "event", "event": {"source": "nowhere", "on": ["item_added"]}}),
     "not in `sources`"),
    (_mut(READ_ONLY, created_from="  "), "created_from"),
    (_mut(READ_ONLY, surprise=1), "surprise"),
    (_mut(ADAPTER, connections=[]), "at least one connection"),
    (_mut(ADAPTER, plans={"other": "approved"}), "does not manage"),
])
def test_each_violation_is_refused_with_a_readable_reason(raw, needle):
    with pytest.raises(SpecError) as err:
        load_spec(raw)
    assert needle in str(err.value)


def test_automation_modes_match_the_agentdef_compiler():
    agentdef = pytest.importorskip("databridge_agentdef.declaration")
    assert AUTOMATION_MODES == agentdef.AUTOMATION_MODES


def test_tool_grammar_parts():
    assert parse_tool("source.write:larkspur-files:write_file") == {
        "family": "source", "op": None, "verb": "write", "connection": "larkspur-files",
        "tool": "write_file"}
    assert parse_tool("graph.path")["op"] == "path"


# ------------------------------------------------------------------------------------------------
# FED — `delegate.ask:<adapter agent>` (additive contract change, 2026-10-01)
# ------------------------------------------------------------------------------------------------
def _delegating(**over):
    d = {"agent": "fed_reader", "title": "t", "created_from": "ask my sources",
         "mode": "read_only", "sources": [], "tools": ["delegate.ask:crm_sources"],
         "delegates": ["crm_sources"], "instructions": "1. ask"}
    d.update(over)
    return d


def test_delegate_tool_parses_as_a_read_only_family():
    p = parse_tool("delegate.ask:crm_sources")
    assert p == {"family": "delegate", "op": "ask", "verb": None, "connection": "crm_sources",
                 "tool": None}
    with pytest.raises(SpecError):
        parse_tool("delegate.ask:crm_sources:extra")
    with pytest.raises(SpecError):
        parse_tool("delegate.write:crm_sources")


def test_delegate_tool_must_name_a_declared_adapter_agent():
    spec = WorkflowSpec.model_validate(_delegating())
    assert list(spec.delegates) == ["crm_sources"]
    with pytest.raises(Exception, match="not in `delegates`"):
        WorkflowSpec.model_validate(_delegating(delegates=[]))
    with pytest.raises(Exception, match="unique"):
        WorkflowSpec.model_validate(_delegating(delegates=["crm_sources", "crm_sources"]))


def test_a_delegate_can_never_be_an_action():
    with pytest.raises(Exception, match="source.write"):
        WorkflowSpec.model_validate(_delegating(
            mode="read_write", sources=["x"],
            actions=[{"name": "go", "tool": "delegate.ask:crm_sources",
                      "automation_mode": "human_required"}]))


def test_delegates_round_trip_and_default_empty():
    spec = WorkflowSpec.model_validate(_delegating())
    assert load_spec(dump_spec(spec)) == spec
    plain = WorkflowSpec.model_validate(_delegating(tools=[], delegates=[]))
    assert list(plain.delegates) == []
    with pytest.raises(TypeError):
        spec.delegates.append("x")
