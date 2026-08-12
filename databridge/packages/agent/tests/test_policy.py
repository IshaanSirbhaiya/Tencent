"""Argument policy — R-11: the declared schema is now ENFORCED, outside the model.

The finding was that the schema was rendered into the system prompt and never checked. A schema
shown to a model is a request. These tests assert the two halves of the fix: a call that violates
the schema never reaches the adapter, and a general search primitive cannot be aimed at text that
appears neither in the question nor in the data.

**Round two.** A second review found the argument control bypassable in three ways at once, and
each has a test below that fails without the fix:

* the screen read ``parameters["properties"]`` and stopped, so
  ``{"filters": {"properties": {"patient_id": …}}}`` was one harmless property called ``filters``;
* it matched a denylist of names, so ``id``, ``key``, ``record`` and ``query`` were never on it;
* ``patternProperties`` admits names nobody wrote down, so no name screen can see them.

The durable answer is not a longer list. It is that a **candidate- or patient-scoped tool declares
no arguments at all**, and that the tree is walked rather than peeked at.
"""
from __future__ import annotations

import pytest
from agent_stubs import BLOCKED_POINTER, make_stub, plan, report, scripted

from databridge_agent import (
    ArgumentSchemaViolation,
    CandidateRead,
    ClinicAgent,
    IdentityArgumentRefused,
    PatientReadGrant,
    PatientScopedRead,
    SchemaNotStrict,
    SelectorArgumentRefused,
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


# --- round two: the ways the name screen was got round ----------------------------


def _nested(inner: dict) -> dict:
    """A selector one level down, inside an object the screen used to call `filters`."""
    return {"type": "object", "additionalProperties": False,
            "properties": {"filters": {"type": "object", "additionalProperties": False,
                                       "properties": inner}}}


def test_a_selector_nested_one_level_down_is_refused():
    """The review's own payload. The old screen inspected one level of a recursive structure."""
    with pytest.raises(IdentityArgumentRefused):
        screen_parameters("clinic_lookup", _nested({"patient_id": {"type": "string"}}))


NESTED_PLACES = [
    # `entries`, not `rows`: `rows` is itself a generic selector, and the case would then be
    # refused by that rule rather than by the tree walk it exists to exercise.
    ("in an array item", {
        "type": "object", "additionalProperties": False,
        "properties": {"entries": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"nric": {"type": "string"}}}}}}),
    ("in $defs", {
        "type": "object", "additionalProperties": False, "properties": {},
        "$defs": {"sel": {"type": "object", "additionalProperties": False,
                          "properties": {"candidate_index": {"type": "integer"}}}}}),
    ("in anyOf", {
        "type": "object", "additionalProperties": False, "properties": {},
        "anyOf": [{"type": "object", "additionalProperties": False,
                   "properties": {"patientId": {"type": "string"}}}]}),
    ("in a dependent schema", {
        "type": "object", "additionalProperties": False,
        "properties": {"intake_id": {"type": "string"}},
        "dependentSchemas": {"intake_id": {"type": "object", "additionalProperties": False,
                                           "properties": {"subject": {"type": "string"}}}}}),
    ("two levels down", _nested({"deeper": {"type": "object", "additionalProperties": False,
                                            "properties": {"passport": {"type": "string"}}}})),
]


@pytest.mark.parametrize("where,schema", NESTED_PLACES, ids=[w for w, _ in NESTED_PLACES])
def test_a_selector_is_refused_wherever_in_the_tree_it_hides(where, schema):
    with pytest.raises(IdentityArgumentRefused):
        screen_parameters("clinic_lookup", schema)


def test_pattern_properties_are_refused_outright():
    """A pattern admits property names nobody has written down, so no screen over names can see
    what it lets through. There is nothing to inspect, so there is nothing to allow."""
    with pytest.raises(SchemaNotStrict):
        screen_parameters("clinic_lookup", {
            "type": "object", "additionalProperties": False,
            "patternProperties": {"^p.*": {"type": "string"}}})


GENERIC_SELECTORS = ["id", "key", "record", "row", "index", "uid", "target", "which", "select"]


@pytest.mark.parametrize("argument", GENERIC_SELECTORS)
def test_a_generic_selector_argument_is_refused(argument):
    """"Use record 2" needs no patient vocabulary to arrive, which is why a denylist of
    patient-shaped names was never the control."""
    with pytest.raises(IdentityArgumentRefused):
        screen_parameters("clinic_lookup", {
            "type": "object", "additionalProperties": False,
            "properties": {argument: {"type": "string"}}})


def test_a_nested_object_that_is_not_closed_is_refused():
    """Closing only the top level leaves the inner object open, and an open object accepts the
    argument it never mentioned — which is how the identity gets supplied."""
    with pytest.raises(SchemaNotStrict):
        screen_parameters("clinic_lookup", {
            "type": "object", "additionalProperties": False,
            "properties": {"filters": {"type": "object",
                                       "properties": {"since": {"type": "string"}}}}})


# --- round two: a subject-bound tool takes NOTHING --------------------------------

GRANT = dict(patient_id="patient:0042", subject_fingerprint="sha256:abc",
             attested_by="staff:front_desk_01", attested_at="2026-08-12T09:00:00")

INNOCENT_ARGUMENT = {"type": "object", "additionalProperties": False,
                     "properties": {"since": {"type": "string"}}}


@pytest.mark.parametrize("scope", ["candidates", "patient"])
def test_a_subject_bound_tool_may_declare_no_argument_at_all(scope):
    """Not "no patient-shaped argument" — none.

    ``since`` is innocent, and that is the point: any argument on a subject-bound tool is a channel
    the model can narrow or redirect a human's attestation through, and the next innocent-looking
    name is one nobody put on a list.
    """
    with pytest.raises(SelectorArgumentRefused):
        screen_parameters("patient_record", INNOCENT_ARGUMENT, scope=scope)


@pytest.mark.parametrize("scope", ["candidates", "patient"])
def test_a_subject_bound_tool_with_no_properties_is_fine(scope):
    """The over-refusal guard: the rule is zero arguments, not zero tools."""
    screen_parameters("patient_record", {"type": "object", "properties": {},
                                         "additionalProperties": False}, scope=scope)


def test_a_structural_tool_may_still_take_arguments():
    """The other over-refusal guard. `clinic_structural` reads name no patient by capability."""
    screen_parameters("coverage_gap", INTAKE_SCHEMA, scope="clinic_structural")


def test_the_rule_holds_through_the_adapter_and_the_tool(caplog):
    """Both doors: the adapter screens its own schema at construction, so a Tool built round it
    later cannot be the first place anybody looks."""
    from agent_stubs import RowsAdapter

    with pytest.raises(SelectorArgumentRefused):
        RowsAdapter(CandidateRead(), name="registry_candidates", parameters=INNOCENT_ARGUMENT)
    with pytest.raises(SelectorArgumentRefused):
        RowsAdapter(PatientScopedRead(grant=PatientReadGrant(**GRANT)), name="patient_record",
                    parameters=INNOCENT_ARGUMENT)


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


# --- round two: validation is on EVERY path, not only the loop's ------------------


def test_a_direct_tool_call_is_validated_too():
    """``Tool.call`` is public. Validation used to live in ``ClinicAgent._run_tool``, so this path
    — the one a route or a test would reach for — walked straight past the schema."""
    stub = _stub_with(INTAKE_SCHEMA)
    with pytest.raises(ArgumentSchemaViolation):
        stub.tool.call(intake_id="intake:0001", patient_id="patient:0043")
    assert stub.calls == [], "the adapter ran despite an argument the schema forbids"


def test_a_direct_adapter_call_is_validated_too():
    """And the adapter itself, which is what ``Tool.call`` delegates to."""
    stub = _stub_with(INTAKE_SCHEMA)
    with pytest.raises(ArgumentSchemaViolation):
        stub.adapter(intake_id="not-an-intake-id")
    assert stub.calls == []


def test_a_direct_call_that_conforms_still_works():
    """Guards the two above: they must fail on the schema, not on the plumbing."""
    stub = _stub_with(INTAKE_SCHEMA)
    assert stub.tool.call(intake_id="intake:0001").rows
    assert stub.calls == [{"intake_id": "intake:0001"}]


def test_mutating_the_schema_after_registration_changes_nothing():
    """The snapshot is taken at construction, screened once, and frozen.

    Before this, ``adapter.parameters`` was an ordinary dict: a tool could be registered with a
    closed, screened schema and then have a ``patient_id`` property added to it, and every
    subsequent call would be validated against the widened version that no screen had ever seen.
    """
    from databridge_agent import AdapterSealMutated

    stub = _stub_with(INTAKE_SCHEMA)
    registry = ToolRegistry([stub.tool])

    with pytest.raises(AdapterSealMutated):
        stub.adapter.parameters["properties"]["patient_id"] = {"type": "string"}
    with pytest.raises(AdapterSealMutated):
        stub.adapter.parameters["additionalProperties"] = True
    with pytest.raises(AdapterSealMutated):
        stub.adapter.parameters = {"type": "object", "properties": {"patient_id": {}},
                                   "additionalProperties": True}

    assert "patient_id" not in registry.get("intake_card").parameters["properties"]
    with pytest.raises(ArgumentSchemaViolation):
        stub.tool.call(intake_id="intake:0001", patient_id="patient:0043")
