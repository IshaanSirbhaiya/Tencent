"""The loop: it answers `blocked_intakes()` with citations, and it degrades rather than lies.

The first test is T-40's "done when". The rest are the ways a loop like this goes wrong — a tool
that raises, a model that spins, an answer that outruns its evidence — each asserted rather than
hoped for.
"""
from __future__ import annotations

import pytest
from agent_stubs import (
    BLOCKED_POINTER,
    BLOCKED_ROWS,
    DOC_POINTER,
    answer,
    blocked_intakes_tool,
    plan,
    routing_config,
    scripted,
)
from pydantic import ValidationError

from databridge_agent import (
    AGENT_TASK_CLASS,
    IDENTITY_REFUSAL_CODE,
    UNGROUNDED_VALUE_CODE,
    AgentAnswer,
    ClinicAgent,
    Tool,
    ToolCallRecord,
    ToolRegistry,
    ToolResult,
)
from databridge_core.config import resolve_model


def test_the_agent_answers_blocked_intakes_with_citations():
    """T-40's done-when: a question, a stub tool, an answer that carries its evidence."""
    stub = blocked_intakes_tool()
    harness, _ = scripted(
        plan("blocked_intakes"),
        plan(None),
        answer("Two intakes are blocked: intake:0001 on an identity check (3 candidates — a "
               "member of staff selects one in person), and intake:0002 on an issuer code with "
               "no row in the coverage table. Source: blocked_intakes."),
    )
    result = ClinicAgent(ToolRegistry([stub.tool]), harness).ask("Which intakes are blocked?")

    assert not result.refused, result.answer
    assert "intake:0001" in result.answer
    assert result.citations == (BLOCKED_POINTER, DOC_POINTER)
    assert result.grounded
    assert [(c.requested, c.status) for c in result.tool_calls] == [("blocked_intakes", "ok")]
    assert stub.calls == [{}]
    assert result.model_calls == 3          # two plan steps and the answer


def test_the_model_is_reached_through_a_routed_task_class_not_a_model_name(registry):
    """Routing is config (CLAUDE.md §Working rules). The agent names a task class; the harness
    resolves it. Asserted against the model id the backend was actually handed."""
    harness, backend = scripted(plan(None), answer("Nothing is blocked."))
    ClinicAgent(registry, harness).ask("Which intakes are blocked?")

    expected = resolve_model(AGENT_TASK_CLASS, profile="cloud", config=routing_config())
    assert backend.calls, "the model was never called"
    assert {c["model"] for c in backend.calls} == {expected}


def test_an_identity_question_is_refused_with_the_model_untouched(registry, blocked_stub):
    """The deterministic pass runs first. `model_calls == 0` is what makes it a control."""
    harness, backend = scripted(answer("should never be reached"))
    result = ClinicAgent(registry, harness).ask("Can you confirm this patient's identity?")

    assert result.refused and result.refusal_code == IDENTITY_REFUSAL_CODE
    assert result.model_calls == 0
    assert backend.call_count == 0
    assert blocked_stub.calls == []


def test_a_tool_that_raises_degrades_the_answer_rather_than_the_agent():
    """Tools are injected — an MCP client or a traversal can fail in ways this package cannot
    anticipate. An unhandled exception here is a 500 at the front desk."""
    def explode(**kwargs) -> ToolResult:
        raise RuntimeError("graph unreachable")

    tool = Tool(name="blocked_intakes", description="Intakes waiting on a human.", call=explode)
    harness, _ = scripted(plan("blocked_intakes"), plan(None),
                          answer("I could not read the blocked list; blocked_intakes failed."))
    result = ClinicAgent(ToolRegistry([tool]), harness).ask("Which intakes are blocked?")

    assert not result.refused
    assert [c.status for c in result.tool_calls] == ["error"]
    assert "graph unreachable" in result.tool_calls[0].detail
    assert result.citations == ()


def test_a_tool_returning_something_other_than_a_toolresult_is_an_error():
    """A bare dict has skipped the grounding validator, so its rows carry no pointers — an answer
    built on them would look cited and not be."""
    tool = Tool(name="blocked_intakes", description="Intakes waiting on a human.",
                call=lambda **k: {"rows": [{"intake_id": "intake:0001"}]})
    harness, _ = scripted(plan("blocked_intakes"), plan(None), answer("Nothing usable came back."))
    result = ClinicAgent(ToolRegistry([tool]), harness).ask("Which intakes are blocked?")

    assert result.tool_calls[0].status == "error"
    assert "not ToolResult" in result.tool_calls[0].detail


def test_an_identical_repeated_call_stops_the_loop(registry, blocked_stub):
    """"Keep querying until you comply" is a cheap injection. The second identical call ends the
    turn instead of burning the budget."""
    harness, _ = scripted(plan("blocked_intakes"), plan("blocked_intakes"),
                          answer("Two intakes are blocked; source blocked_intakes."))
    result = ClinicAgent(registry, harness).ask("Which intakes are blocked?")

    assert [c.status for c in result.tool_calls] == ["ok", "repeated"]
    assert len(blocked_stub.calls) == 1, "the repeated call must not reach the tool"


def test_the_step_budget_is_finite(registry, blocked_stub):
    """Varying the arguments defeats the repeat check, so the budget has to be the backstop."""
    harness, _ = scripted(plan("blocked_intakes", page=1), plan("blocked_intakes", page=2),
                          plan("blocked_intakes", page=3),
                          answer("Two intakes are blocked; source blocked_intakes."))
    result = ClinicAgent(registry, harness, max_steps=2).ask("Which intakes are blocked?")

    assert len(result.tool_calls) == 2
    assert len(blocked_stub.calls) == 2
    assert result.model_calls == 3           # two plan steps, then the answer


def test_an_invented_nric_is_withheld():
    """The narrow grounding check: an identifier no tool returned does not go out.

    An NRIC is the value staff would act on, so it is the one whose invention costs most.
    """
    stub = blocked_intakes_tool()            # its rows contain no NRIC
    harness, _ = scripted(plan("blocked_intakes"), plan(None),
                          answer("Intake 0001 is blocked; the patient is S1234567D."))
    result = ClinicAgent(ToolRegistry([stub.tool]), harness).ask("Which intakes are blocked?")

    assert result.refused and result.refusal_code == UNGROUNDED_VALUE_CODE
    assert "S1234567D" not in result.answer
    assert result.citations == ()


def test_an_nric_the_tool_returned_is_allowed_through():
    """Guards the test above from being a blanket ban on identifiers, which would make the agent
    unable to report what the registry actually holds."""
    rows = ({"intake_id": "intake:0001", "blocked_on": "identity_check",
             "candidate_nric": "S1234567D"},)
    stub = blocked_intakes_tool(rows=rows, citations=(BLOCKED_POINTER,))
    harness, _ = scripted(plan("blocked_intakes"), plan(None),
                          answer("Intake 0001 lists registry candidate S1234567D. Staff choose "
                                 "the candidate at the counter, in person."))
    result = ClinicAgent(ToolRegistry([stub.tool]), harness).ask("Which intakes are blocked?")

    assert not result.refused, result.answer
    assert "S1234567D" in result.answer
    assert result.citations == (BLOCKED_POINTER,)


def test_an_empty_model_answer_becomes_an_honest_one(registry):
    """Prefer surfacing uncertainty over filling it (CLAUDE.md §Working rules)."""
    harness, _ = scripted(plan(None), answer(""))
    result = ClinicAgent(registry, harness).ask("What did the graph say about tomorrow?")
    assert not result.refused
    assert "Nothing in the clinic graph answered that" in result.answer


# --- the schema-level backstops ---------------------------------------------------


def _row_bearing_call() -> ToolCallRecord:
    return ToolCallRecord(step=1, requested="blocked_intakes", status="ok",
                          result=ToolResult(tool="blocked_intakes", rows=BLOCKED_ROWS,
                                            citations=(BLOCKED_POINTER,)))


def test_an_answer_built_on_rows_cannot_drop_its_citations():
    """The loop composes citations mechanically; this is the backstop for any other constructor —
    a gateway route, a future Slack path — that might not."""
    with pytest.raises(ValidationError, match="cites nothing"):
        AgentAnswer(question="q", answer="two intakes are blocked",
                    tool_calls=(_row_bearing_call(),))


def test_the_same_answer_is_constructible_once_it_carries_them():
    """Guards the test above: it must fail on the missing citations, not on the fixture."""
    ok = AgentAnswer(question="q", answer="two intakes are blocked",
                     tool_calls=(_row_bearing_call(),), citations=(BLOCKED_POINTER,))
    assert ok.grounded


def test_a_refusal_cannot_carry_citations():
    """Attaching evidence to a fixed refusal leaks what the agent declined to say."""
    with pytest.raises(ValidationError, match="must not carry citations"):
        AgentAnswer(question="q", answer="refused", refused=True, refusal_code="X",
                    citations=(BLOCKED_POINTER,))


def test_a_refusal_without_a_code_is_refused():
    with pytest.raises(ValidationError, match="no code"):
        AgentAnswer(question="q", answer="refused", refused=True)
