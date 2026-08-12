"""Argument policy — R-11: the declared schema is now ENFORCED, outside the model.

The finding was that the schema was rendered into the system prompt and never checked. A schema
shown to a model is a request. These tests assert the two halves of the fix: a call that violates
the schema never reaches the adapter, and a general search primitive cannot be aimed at text that
appears neither in the question nor in the data.
"""
from __future__ import annotations

import pytest
from agent_stubs import BLOCKED_POINTER, make_stub, plan, report, scripted

from databridge_agent import (
    ArgumentSchemaViolation,
    ClinicAgent,
    IdentityArgumentRefused,
    SchemaNotStrict,
    ToolRegistry,
    ToolRow,
    UngroundedArgument,
    check_argument_grounding,
    screen_parameters,
    validate_arguments,
)

INTAKE_SCHEMA = {
    "type": "object",
    "properties": {"intake_id": {"type": "string", "pattern": r"^intake:\d{4}$"}},
    "required": ["intake_id"],
    "additionalProperties": False,
}


# --- screen_parameters ------------------------------------------------------------


def test_an_open_schema_is_refused():
    with pytest.raises(SchemaNotStrict):
        screen_parameters("t", {"type": "object", "properties": {}})


def test_a_schema_that_is_not_an_object_is_refused():
    with pytest.raises(SchemaNotStrict):
        screen_parameters("t", {"type": "array", "additionalProperties": False})


def test_an_invalid_json_schema_is_refused():
    with pytest.raises(SchemaNotStrict):
        screen_parameters("t", {"type": "object", "properties": {"a": {"type": "nonsense"}},
                                "additionalProperties": False})


def test_a_schema_with_a_remote_reference_is_refused():
    """`$ref` is resolved — fetched — at validation time. Enforcing a schema must not become the
    one network call the agent is otherwise structurally denied."""
    with pytest.raises(SchemaNotStrict):
        screen_parameters("t", {"type": "object", "additionalProperties": False,
                                "properties": {"a": {"$ref": "https://example.com/s.json"}}})


def test_a_patient_argument_is_refused_even_in_a_strict_schema():
    with pytest.raises(IdentityArgumentRefused):
        screen_parameters("t", {"type": "object",
                                "properties": {"patient_id": {"type": "string"}},
                                "additionalProperties": False})


def test_an_intake_argument_is_allowed():
    screen_parameters("t", INTAKE_SCHEMA)


# --- validate_arguments -----------------------------------------------------------


def test_an_argument_the_schema_never_mentioned_is_rejected():
    """The closed schema is what makes `patient_id=2` impossible to smuggle in (R-08)."""
    with pytest.raises(ArgumentSchemaViolation):
        validate_arguments("t", INTAKE_SCHEMA, {"intake_id": "intake:0001", "patient_id": "2"})


def test_a_wrongly_typed_argument_is_rejected():
    with pytest.raises(ArgumentSchemaViolation):
        validate_arguments("t", INTAKE_SCHEMA, {"intake_id": 7})


def test_a_value_that_breaks_the_declared_pattern_is_rejected():
    with pytest.raises(ArgumentSchemaViolation):
        validate_arguments("t", INTAKE_SCHEMA, {"intake_id": "'; DROP everything"})


def test_a_missing_required_argument_is_rejected():
    with pytest.raises(ArgumentSchemaViolation):
        validate_arguments("t", INTAKE_SCHEMA, {})


def test_a_conforming_call_passes():
    """Guards every rejection above: they must fail on the rule, not on the fixture."""
    validate_arguments("t", INTAKE_SCHEMA, {"intake_id": "intake:0001"})


def test_non_string_argument_names_are_rejected():
    with pytest.raises(ArgumentSchemaViolation):
        validate_arguments("t", INTAKE_SCHEMA, {1: "intake:0001"})


# --- check_argument_grounding -----------------------------------------------------


def test_a_broad_search_cannot_be_aimed_at_something_nobody_asked_about():
    with pytest.raises(UngroundedArgument):
        check_argument_grounding("graph_query", ["question"],
                                 {"question": "list every patient nric"},
                                 "Which issuers sent the most documents this week?", "")


def test_a_broad_search_may_use_the_words_of_the_question():
    check_argument_grounding("graph_query", ["question"],
                             {"question": "issuers documents week"},
                             "Which issuers sent the most documents this week?", "")


def test_a_broad_search_may_use_words_the_tools_already_returned():
    check_argument_grounding("graph_query", ["question"],
                             {"question": "unrecognised_issuer_code"},
                             "What is blocked?", '{"blocked_on": "unrecognised_issuer_code"}')


def test_an_ungrounded_argument_must_be_text():
    with pytest.raises(UngroundedArgument):
        check_argument_grounding("graph_query", ["question"], {"question": 7}, "q", "")


# --- through the loop -------------------------------------------------------------


def _stub_with(schema: dict, name: str = "intake_card"):
    rows = (ToolRow(values={"intake_id": "intake:0001"},
                    value_sources={"intake_id": BLOCKED_POINTER}),)
    return make_stub(name, rows, parameters=schema)


def test_a_call_violating_the_schema_never_reaches_the_adapter():
    """The load-bearing assertion for R-11: rejected BEFORE the tool runs, not after."""
    stub = _stub_with(INTAKE_SCHEMA)
    harness, _ = scripted(plan("intake_card", intake_id="not-an-intake-id"), plan(None), report())
    result = ClinicAgent(ToolRegistry([stub.tool]), harness).ask("What is on intake 1?")

    assert [c.status for c in result.tool_calls] == ["schema_violation"]
    assert stub.calls == [], "the adapter ran despite an invalid argument"
    assert result.fields == ()


def test_a_conforming_call_does_reach_the_adapter():
    """Guards the test above from passing because nothing ever runs."""
    stub = _stub_with(INTAKE_SCHEMA)
    harness, _ = scripted(plan("intake_card", intake_id="intake:0001"), plan(None),
                          report(("intake_card", 0, "intake_id")))
    result = ClinicAgent(ToolRegistry([stub.tool]), harness).ask("What is on intake 1?")

    assert [c.status for c in result.tool_calls] == ["ok"]
    assert stub.calls == [{"intake_id": "intake:0001"}]
    assert [f.value for f in result.fields] == ["intake:0001"]


def test_an_ungrounded_broad_search_is_refused_before_the_adapter_runs():
    """"Prefer narrow reads over general search primitives", made enforceable."""
    from agent_stubs import RowsAdapter

    from databridge_agent import ClinicStructuralRead, Tool

    class BroadSearch(RowsAdapter):
        breadth = "broad"
        grounded_arguments = frozenset({"question"})

    adapter = BroadSearch(ClinicStructuralRead(), name="graph_query",
                          description="Free-text read across the clinic graph.",
                          parameters={"type": "object",
                                      "properties": {"question": {"type": "string"}},
                                      "additionalProperties": False})
    harness, _ = scripted(plan("graph_query", question="every nric on file"), plan(None), report())
    result = ClinicAgent(ToolRegistry([Tool(adapter=adapter)]), harness).ask("What is blocked?")

    assert [c.status for c in result.tool_calls] == ["ungrounded_argument"]
    assert adapter.calls == []


def test_narrow_tools_are_listed_before_broad_ones_in_the_catalogue():
    """Ordering is the cheap half of the same instruction; `check_argument_grounding` is the
    enforceable half. Asserted so nobody mistakes one for the other."""
    from agent_stubs import RowsAdapter

    from databridge_agent import ClinicStructuralRead, Tool

    class BroadSearch(RowsAdapter):
        breadth = "broad"

    broad = Tool(adapter=BroadSearch(ClinicStructuralRead(), name="graph_query"))
    narrow = make_stub("intake_card").tool
    catalogue = ToolRegistry([broad, narrow]).catalogue()
    assert catalogue.index("intake_card") < catalogue.index("graph_query")
