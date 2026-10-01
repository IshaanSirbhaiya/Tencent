"""The main agent, end to end, against the real finance estate — no mocked adapter anywhere.

Two sources, one `PolicyEngine`, one asking principal at a time: `core_banking` grants the credit
analyst a read; `mailbox` is compliance-only. That single fixture pair is enough to exercise every
non-negotiable in the brief without inventing a third estate:

* the Shell proposes and is validated exactly once, never retried against the model;
* a whole-source refusal comes back `Withheld` with a reason code, never a missing row;
* what crosses the boundary is cells and pointers, never a sentence about them;
* source selection reads only `describe()` and never calls a model;
* every step — including the refusal — is recorded through the trace sink.
"""
from __future__ import annotations

import json

import pytest

from databridge_adapter import CapabilityRequest, Projection
from databridge_core import Withheld
from databridge_harness import FakeBackend, Harness

from databridge_orchestrator import AdapterHandle, GroundedCell, Orchestrator


def _scripted(model, messages, guided_json=None, **kwargs):
    """One FakeBackend response per adapter brief, dispatched on the source_id the brief names.

    This is the offline stand-in for "the model plans a read"; it never sees the OTHER source's
    brief (each Shell only ever sends its own), and it never proposes something a source has not
    advertised unless a test asks it to on purpose.
    """
    brief = messages[0]["content"]
    if '"source_id": "core_banking"' in brief:
        return json.dumps({
            "capability": "row_read", "entity": "facility",
            "fields": ["facility_id", "limit_amount", "drawn_amount", "currency", "status"],
            "order_by": ["facility_id"], "limit": 5})
    if '"source_id": "mailbox"' in brief:
        return json.dumps({
            "capability": "message_read", "entity": "message",
            "fields": ["message_id", "subject", "claimed_from_address"],
            "order_by": ["message_id"], "limit": 5})
    return json.dumps({"escalate": True, "escalation_reason": f"unrecognised brief: {brief[:60]}"})


def _orchestrator(handles, backend=None) -> tuple[Orchestrator, FakeBackend]:
    backend = backend or FakeBackend(_scripted)
    return Orchestrator(handles, harness=Harness(backend)), backend


class _RecordingTrace:
    """Everything TraceHandle exposes, recorded rather than sent anywhere — proves the
    orchestrator calls the sink the shape the real recorder expects, without standing one up."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name):
        def method(*args, **kw):
            self.calls.append((name, kw))
            return self
        return method

    def __getattr__(self, name):
        return self._record(name)

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]


# =======================================================================================
# the happy path — cells and pointers, never prose
# =======================================================================================
def test_core_banking_answers_with_real_pointers(handles, analyst):
    orch, _ = _orchestrator(handles)
    result = orch.ask("what is the facility limit and drawn amount", principal=analyst)

    core = next(o for o in result.sources if o.source_id == "core_banking")
    assert core.status == "answered"
    assert core.cells, "the real finance estate seeds facility rows; an empty answer here is a defect"
    for cell in core.cells:
        assert isinstance(cell, GroundedCell)
        if cell.state == "grounded":
            assert cell.pointer is not None and cell.pointer.get("source_id") == "core_banking"
            assert cell.trust is not None


def test_grounded_values_are_not_transformed_on_the_way_out(handles, analyst, core_banking):
    """The strongest form of "never paraphrase": the values this module hands back are
    BYTE-IDENTICAL, in the same order, to what a direct `resolve()` against the same projection
    returns — nothing here rewrites, rounds, or re-renders a cell. Compared ordinally rather than
    by re-matching a key, because both reads use the identical deterministic order_by and limit
    against an estate nothing mutates between them."""
    from decimal import Decimal

    from databridge_core import GroundedValue

    orch, _ = _orchestrator(handles)
    result = orch.ask("facility limit and drawn amount", principal=analyst)
    core = next(o for o in result.sources if o.source_id == "core_banking")
    assert core.cells

    direct_req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=Projection(entity="facility",
                              fields=("facility_id", "limit_amount", "drawn_amount", "currency",
                                      "status"),
                              key=("facility_id",)),
        order_by=("facility_id",), limit=5)
    direct = core_banking.resolve(direct_req)
    direct_cells = [cell for row in direct.rows.rows for cell in row]
    orch_grounded = [c for c in core.cells if c.state == "grounded"]

    assert len(orch_grounded) == sum(1 for c in direct_cells if isinstance(c, GroundedValue))
    direct_iter = iter(c for c in direct_cells if isinstance(c, GroundedValue))
    for cell in orch_grounded:
        expected = next(direct_iter).value
        got = cell.value
        if isinstance(expected, Decimal):
            assert got == str(expected)
        else:
            assert got == expected


# =======================================================================================
# refusal — Withheld with a reason code, never a missing row
# =======================================================================================
def test_mailbox_is_refused_for_the_analyst_not_silently_dropped(handles, analyst):
    orch, _ = _orchestrator(handles)
    result = orch.ask("what does the email subject and from address say", principal=analyst)

    mailbox = next((o for o in result.sources if o.source_id == "mailbox"), None)
    assert mailbox is not None, "mailbox must still be REPORTED even though it will be refused"
    assert mailbox.status == "refused"
    assert mailbox.cells == (), "a refused source returns no rows, never a partial or guessed one"
    assert isinstance(mailbox.denial, Withheld)
    assert mailbox.denial.record.reason_code
    assert mailbox.denial.record.policy_id
    assert mailbox.denial.record.principal == analyst.actor


def test_compliance_officer_can_read_the_same_mailbox(handles, compliance):
    orch, _ = _orchestrator(handles)
    result = orch.ask("what does the email subject and from address say", principal=compliance)

    mailbox = next(o for o in result.sources if o.source_id == "mailbox")
    assert mailbox.status == "answered"
    assert mailbox.cells
    # untrusted_fields: ["*"] in the finance estate's mailbox contract — every grounded cell here
    # must carry that label, because untrusted content is quotable and the label is how a caller
    # downstream knows not to act on it.
    grounded = [c for c in mailbox.cells if c.state == "grounded"]
    assert grounded
    assert all(c.trust == "untrusted" for c in grounded)


# =======================================================================================
# the Shell's rejection is never retried
# =======================================================================================
def test_an_unadvertised_capability_is_escalated_once_not_retried(handles):
    backend = FakeBackend(json.dumps({"capability": "bulk_export", "entity": "party"}))
    core_only = [h for h in handles if h.source_id == "core_banking"]
    orch = Orchestrator(core_only, harness=Harness(backend))

    from databridge_adapter import Principal

    principal = Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                          purpose="lending.underwriting", grant_id="grant-analyst-7")
    result = orch.ask("export everything", principal=principal)

    outcome = result.sources[0]
    assert outcome.status == "escalated"
    assert backend.call_count == 1, (
        "the orchestrator called the model again after a rejection — that is the retry loop "
        "shell.py §10 exists to prevent")


# =======================================================================================
# selection reads only describe(), never a model, never invents a source
# =======================================================================================
def test_selection_never_calls_the_model(handles):
    backend = FakeBackend(_scripted)
    orch, _ = _orchestrator(handles, backend)
    result = orch.ask("facility limit", principal=_analyst_principal())
    # exactly one propose() per SELECTED source, none for scoring/selection itself
    assert backend.call_count == len(result.sources)


def test_selection_narrows_to_the_matching_source(handles, analyst):
    orch, _ = _orchestrator(handles)
    result = orch.ask("drawn amount and credit limit currency status", principal=analyst)
    assert result.sources_considered == ("core_banking",), (
        "a question with no mailbox vocabulary in it must not hand mailbox a brief at all")


def test_selection_falls_back_to_every_source_on_no_overlap(handles, analyst):
    orch, _ = _orchestrator(handles)
    result = orch.ask("zzqxpv wibblewobble frobnicate", principal=analyst)
    assert set(result.sources_considered) == {"core_banking", "mailbox"}
    assert "no declared entity" in result.selection_note


def test_selection_never_returns_a_source_outside_its_input(handles):
    from databridge_orchestrator import select_sources

    chosen, _ = select_sources("facility", handles, max_sources=10)
    assert all(h in handles for h in chosen)


# =======================================================================================
# every step recorded, including the refusal
# =======================================================================================
def test_every_step_is_recorded_including_the_refusal(handles, analyst):
    orch, _ = _orchestrator(handles)
    trace = _RecordingTrace()
    orch.ask("facility limit and the email subject", principal=analyst, trace=trace)

    kinds = trace.kinds()
    assert "principal" in kinds
    assert "capability" in kinds, "both sources' CapabilityRequests must be recorded"
    assert "policy" in kinds, "the mailbox refusal must show up as a policy step"
    assert "cell" in kinds, "core_banking's grounded cells must show up as cell steps"
    assert "ledger_append" in kinds
    assert "released" in kinds


def test_ask_works_with_no_trace_sink_at_all(handles, analyst):
    orch, _ = _orchestrator(handles)
    result = orch.ask("facility limit", principal=analyst)
    assert result.answered


def _analyst_principal():
    from databridge_adapter import Principal

    return Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                     purpose="lending.underwriting", grant_id="grant-analyst-7")


def test_int5_every_release_names_a_pointer_recorded_for_its_source_so_the_trace_is_complete(
        handles, analyst):
    """INT5 (ACT3 made `released()` require a `pointer_id` recorded earlier for the SAME source):
    the orchestrator's trace, replayed into the REAL gateway recorder, is complete."""
    from databridge_gateway.trace_recorder import TraceRecorder

    orch, _ = _orchestrator(handles)
    tr = TraceRecorder().begin(surface="orchestrator")
    orch.ask("what is the facility limit and drawn amount", principal=analyst, trace=tr)
    trace = tr.close("answered")
    released = [s.detail for s in trace.steps if s.kind == "released"]
    assert released, "nothing was released: the check would be vacuous"
    assert all(r["pointer_id"] and r["pointer_recorded"] for r in released), released
    assert "RELEASE_POINTER_NOT_RECORDED" not in trace.integrity()["defect_codes"]
    assert trace.integrity()["complete"] is True, trace.integrity()
