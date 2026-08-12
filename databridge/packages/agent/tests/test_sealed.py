"""Sealed read adapters and the closed capability set — R-07 and the structural half of R-08.

Before this, ``effect: Literal["read"]`` was an annotation on an arbitrary callable. The review's
sentence was exact: *a tool named ``clinic_lookup`` could POST or write to the ledger.* Nothing in
the package would have known, and the registry could grow after it had been reviewed.

Two rules govern this file, and both are learned from the repo's own history:

* **Every scan is paired with a planted violation.** A scan that is only ever run against clean
  code stays green when its patterns are wrong, and a green suite asserting nothing is worse than
  no suite.
* **The forbidden tokens below are written in fragments.** This module is itself scanned when the
  adapters in it are defined, so a contiguous transport import or ledger write here would refuse
  the test file rather than the fixture it is planting.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from agent_stubs import BLOCKED_POINTER, NO_ARGUMENTS, RowsAdapter
from pydantic import ValidationError

from databridge_agent import (
    AdapterRefused,
    AdapterSealMutated,
    CandidateRead,
    IdentityArgumentRefused,
    ClinicStructuralRead,
    GrantRefused,
    PatientReadGrant,
    PatientScopedRead,
    SealBroken,
    SubjectMismatch,
    Tool,
    ToolRegistry,
    ToolResult,
    ToolRow,
    TransportInAdapter,
    WriterInAdapter,
    scan_source,
)

# Fragments, for the reason in the module docstring.
_TRANSPORT = "im" + "port so" + "cket"
_LEDGER_WRITE = "led" + "ger." + "com" + "mit()"
_SQL_WRITE = "INS" + "ERT IN" + "TO ledger VALUES (1)"

CLEAN_MODULE = (
    "from databridge_agent import ClinicStructuralRead, SealedReadAdapter, ToolResult\n"
    "class Clean(SealedReadAdapter):\n"
    "    name = 'clinic_lookup'\n"
    "    description = 'Reads clinic structure.'\n"
    "    def read(self, **a):\n"
    "        return ToolResult(tool='clinic_lookup')\n")


def _load(tmp_path: pathlib.Path, name: str, source: str):
    """Import ``source`` as a real module so ``__init_subclass__`` runs against a real file."""
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def test_a_clean_adapter_module_is_accepted(tmp_path):
    """The over-refusal guard. Without it every ban below could pass while the package was
    unusable, which is the failure mode the intake guardrail was written twice to avoid."""
    module = _load(tmp_path, "clean_adapter", CLEAN_MODULE)
    adapter = module.Clean(ClinicStructuralRead())
    assert Tool(adapter=adapter).name == "clinic_lookup"


def test_an_adapter_module_that_can_reach_the_network_is_refused_at_definition(tmp_path):
    """R-07's headline. The refusal fires at IMPORT, before any agent, model or request exists."""
    with pytest.raises(TransportInAdapter):
        _load(tmp_path, "transport_adapter", _TRANSPORT + "\n" + CLEAN_MODULE)


def test_an_adapter_module_that_can_write_to_the_ledger_is_refused(tmp_path):
    """"A tool named `clinic_lookup` could ... write to the ledger" — the review's own example."""
    source = CLEAN_MODULE.replace("        return ToolResult",
                                  f"        {_LEDGER_WRITE}\n        return ToolResult")
    with pytest.raises(WriterInAdapter):
        _load(tmp_path, "ledger_adapter", source)


@pytest.mark.parametrize("planted,expected", [
    (_TRANSPORT, TransportInAdapter),
    (_LEDGER_WRITE, WriterInAdapter),
    (_SQL_WRITE, WriterInAdapter),
    ("store.upsert_node(node)", WriterInAdapter),
    ("path.write_text('x')", WriterInAdapter),
    ("open('f.txt', 'w')", WriterInAdapter),
])
def test_the_scan_catches_each_planted_violation(planted, expected):
    """Every pattern, exercised directly. A pattern nobody has seen fire is a pattern that may not."""
    with pytest.raises(expected):
        scan_source(planted, "planted")


def test_the_scan_does_not_fire_on_ordinary_read_code():
    scan_source("rows = store.open_node_row(node_id)\ncursor.execute('SELECT 1')\n", "reader")


# The one-line off switch. `_abstract_base` skips the scan, and until this test it was honoured
# wherever it was set — including on a concrete adapter in somebody else's module, which is the
# code the scan exists to examine. A guard whose own bypass is reachable by the code it guards is
# not a guard, and this is the second time this repo has found that shape (REMEDIATION.md Shape C).
_OPT_OUT_MODULE = (
    "from databridge_agent import ClinicStructuralRead, SealedReadAdapter, ToolResult\n"
    "class OptOut(SealedReadAdapter):\n"
    "    _abstract_base = True\n"
    "    name = 'clinic_lookup'\n"
    "    def read(self, **a):\n"
    "        return ToolResult(tool='clinic_lookup')\n")


def test_a_concrete_adapter_cannot_switch_the_scan_off(tmp_path):
    """`_abstract_base = True` in another module buys nothing: the transport is still found."""
    with pytest.raises(TransportInAdapter):
        _load(tmp_path, "opt_out_adapter", _TRANSPORT + "\n" + _OPT_OUT_MODULE)


def test_the_opt_out_module_is_otherwise_importable(tmp_path):
    """Guards the test above from passing for the wrong reason — without the planted transport the
    same module loads, so the refusal is the scan firing and not a broken fixture."""
    module = _load(tmp_path, "opt_out_clean", _OPT_OUT_MODULE)
    assert module.OptOut(ClinicStructuralRead()).name == "clinic_lookup"


def test_a_subclass_does_not_inherit_its_parents_scan_record(tmp_path):
    """`scanned_source` is inherited like any class attribute, and an inherited scan record
    describes a DIFFERENT file. The child's own module is what will run."""
    parent = _load(tmp_path, "clean_parent", CLEAN_MODULE)
    child_source = ("from clean_parent import Clean\n"
                    + _TRANSPORT + "\n"
                    "class Child(Clean):\n"
                    "    name = 'clinic_lookup_2'\n")
    sys.modules["clean_parent"] = parent
    try:
        with pytest.raises(TransportInAdapter):
            _load(tmp_path, "dirty_child", child_source)
    finally:
        sys.modules.pop("clean_parent", None)


_PRESET_MODULE = (
    "from databridge_agent import ClinicStructuralRead, SealedReadAdapter, ToolResult\n"
    "class Preset(SealedReadAdapter):\n"
    "    scanned_source = 'already scanned, honest'\n"
    "    name = 'clinic_lookup'\n"
    "    def read(self, **a):\n"
    "        return ToolResult(tool='clinic_lookup')\n")


def test_claiming_to_have_been_scanned_is_not_being_scanned(tmp_path):
    """The other one-line off switch. `scanned_source` is an ordinary class attribute, so a class
    body can preset it; whether a class was scanned is tracked where a class body cannot write."""
    with pytest.raises(TransportInAdapter):
        _load(tmp_path, "preset_adapter", _TRANSPORT + "\n" + _PRESET_MODULE)


def test_an_adapter_with_no_readable_source_is_refused():
    """A guard that cannot see the code fails closed — including for a class built by `exec`."""
    namespace: dict = {}
    source = ("from databridge_agent import SealedReadAdapter, ToolResult\n"
              "class Hidden(SealedReadAdapter):\n"
              "    name = 'hidden'\n"
              "    def read(self, **a):\n"
              "        return ToolResult(tool='hidden')\n")
    with pytest.raises(AdapterRefused):
        exec(compile(source, "<not-a-file>", "exec"), namespace)   # noqa: S102 - the point


# --- the closed capability set ----------------------------------------------------


def test_the_capability_set_contains_no_writer():
    """Three read capabilities, no fourth. Adding one is a visible schema change."""
    assert {ClinicStructuralRead().scope, CandidateRead().scope} == {"clinic_structural",
                                                                     "candidates"}
    with pytest.raises(ValidationError):
        ClinicStructuralRead(scope="write")


def test_a_candidate_capability_always_requires_a_human():
    """`Literal[True]`, the device from `ResolutionResult.requires_human_selection`."""
    assert CandidateRead().requires_human_selection is True
    with pytest.raises(ValidationError):
        CandidateRead(requires_human_selection=False)


def test_an_adapter_refuses_a_capability_from_outside_the_set():
    with pytest.raises(AdapterRefused):
        RowsAdapter("clinic_wide_please", name="clinic_lookup")


# --- the grant (R-08) -------------------------------------------------------------

GRANT = dict(patient_id="patient:0042", subject_fingerprint="sha256:abc",
             attested_by="staff:front_desk_01", attested_at="2026-08-12T09:00:00")


def test_a_grant_records_a_named_human_and_the_subject_it_was_made_about():
    grant = PatientReadGrant(**GRANT)
    assert grant.selection_made_in_person is True
    assert PatientScopedRead(grant=grant).patient_id == "patient:0042"


def test_a_grant_cannot_claim_the_machine_selected():
    with pytest.raises(ValidationError):
        PatientReadGrant(**{**GRANT, "selection_made_in_person": False})


@pytest.mark.parametrize("who", ["system", "agent", "  ", "copilot", "auto"])
def test_a_grant_attested_by_something_that_is_not_a_person_is_refused(who):
    """CLAUDE.md §Hard constraints 1: a named human, at the counter, in person."""
    with pytest.raises(GrantRefused):
        PatientReadGrant(**{**GRANT, "attested_by": who})


def test_a_grant_with_no_subject_fingerprint_is_refused():
    """Shape B: an attestation not bound to the document revision and candidate set it was made
    against can authorise a record it was never about."""
    with pytest.raises(GrantRefused):
        PatientReadGrant(**{**GRANT, "subject_fingerprint": "  "})


def test_a_patient_scoped_capability_cannot_exist_without_a_grant():
    with pytest.raises(ValidationError):
        PatientScopedRead()


def test_a_patient_scoped_tool_takes_its_subject_from_the_grant_not_an_argument():
    """The whole of R-08 in one assertion: the subject is on the capability, and the schema the
    model is shown has no property through which a different one could be named."""
    grant = PatientReadGrant(**GRANT)
    adapter = RowsAdapter(PatientScopedRead(grant=grant), name="patient_record")
    tool = Tool(adapter=adapter)

    assert tool.subject == "patient:0042"
    assert tool.parameters["properties"] == {}
    assert tool.parameters["additionalProperties"] is False
    assert "patient" not in tool.spec()["parameters"].get("properties", {})


def test_before_an_attestation_there_is_no_patient_scoped_tool_at_all():
    """The pre-attestation surface: candidates, and nothing patient-scoped to point anywhere."""
    from agent_stubs import candidate_tool
    registry = ToolRegistry([candidate_tool().tool])
    assert registry.patient_scoped() == ()
    assert registry.scopes() == ("candidates",)


# --- the seal on the way out ------------------------------------------------------


def test_an_adapter_that_returns_a_bare_dict_is_refused_by_the_seal():
    adapter = RowsAdapter(ClinicStructuralRead(), name="blocked_intakes",
                          returns={"rows": []})
    with pytest.raises(SealBroken):
        adapter()


def test_an_adapter_that_mislabels_its_result_is_refused_by_the_seal():
    adapter = RowsAdapter(ClinicStructuralRead(), name="blocked_intakes",
                          returns=ToolResult(tool="something_else"))
    with pytest.raises(SealBroken):
        adapter()


def test_the_seal_passes_a_well_formed_result():
    """Guards the two above: they must fail on the seal, not on the fixture."""
    rows = (ToolRow(values={"intake_id": "intake:0001"},
                    value_sources={"intake_id": BLOCKED_POINTER}),)
    adapter = RowsAdapter(ClinicStructuralRead(), rows=rows, name="blocked_intakes",
                          parameters=NO_ARGUMENTS)
    assert adapter().rows == rows


# --- the schema is sealed at construction (R-11, round two) -----------------------


def test_the_declared_schema_is_frozen_at_construction():
    """Screened once, then frozen: the thing that runs has to be the thing that was screened."""
    adapter = RowsAdapter(ClinicStructuralRead(), name="intake_card", parameters={
        "type": "object", "properties": {"intake_id": {"type": "string"}},
        "additionalProperties": False})

    with pytest.raises(AdapterSealMutated):
        adapter.parameters["properties"]["patient_id"] = {"type": "string"}
    with pytest.raises(AdapterSealMutated):
        adapter.parameters["properties"].update({"nric": {"type": "string"}})
    with pytest.raises(AdapterSealMutated):
        adapter.name = "something_else"
    assert sorted(adapter.parameters["properties"]) == ["intake_id"]


def test_the_adapter_screens_its_own_schema_at_construction():
    """The Tool is not the only door. An adapter is a callable in its own right."""
    with pytest.raises(IdentityArgumentRefused):
        RowsAdapter(ClinicStructuralRead(), name="intake_card",
                    parameters={"type": "object", "properties": {"patient_id": {"type": "string"}},
                                "additionalProperties": False})


def test_a_patient_scoped_read_returning_another_patients_row_is_refused():
    """A tripwire, not the boundary — see `_check_subject`. Subject scoping is enforced where the
    query is written; this catches the row that arrives labelled with somebody else."""
    grant = PatientReadGrant(**GRANT)
    rows = (ToolRow(values={"patient_id": "patient:0043", "package_code": "WELL2"},
                    value_sources={"patient_id": BLOCKED_POINTER,
                                   "package_code": BLOCKED_POINTER}),)
    adapter = RowsAdapter(PatientScopedRead(grant=grant), rows=rows, name="patient_record")
    with pytest.raises(SubjectMismatch):
        adapter()


def test_a_patient_scoped_read_of_its_own_subject_passes():
    """Guards the test above: it must fail on the mismatch, not on the check being unconditional."""
    grant = PatientReadGrant(**GRANT)
    rows = (ToolRow(values={"patient_id": "patient:0042", "package_code": "WELL2"},
                    value_sources={"patient_id": BLOCKED_POINTER,
                                   "package_code": BLOCKED_POINTER}),)
    adapter = RowsAdapter(PatientScopedRead(grant=grant), rows=rows, name="patient_record")
    assert adapter().rows == rows
