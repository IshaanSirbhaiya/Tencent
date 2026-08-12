"""Every sentence that could leave the building can say where it came from.

CLAUDE.md §Hard constraints 2 is usually read as a rule about extracted fields. It is a rule about
*emitted values*, and a sentence in a patient email is the most consequential emitted value in the
system — it is the one a person reads and acts on. So the same proof runs here as in
`test_extract.py::test_every_pointer_resolves_to_its_value`: not "there is a pointer" but "the
thing the pointer names really holds this text".
"""
from __future__ import annotations

import datetime as dt

import pytest
import yaml

from databridge_core import GroundedValue, SourcePointer
from databridge_outbox.drafting import build_outbound_draft
from databridge_outbox.models import DraftRefused, OutboundDraft
from databridge_outbox.monitors import Observation, monitors_path
from databridge_outbox.recipients import recipient_from_committed_ledger, recipient_typed_by_human

#: Defined here rather than imported from ``conftest``: two test trees each have a top-level
#: ``conftest`` module and importing one by name picks whichever was collected first.
CHIT = "chit_07_mol_authorisation_pee226.txt"


def doc_pointer(value: str, text: str) -> SourcePointer:
    """A ``doc_span`` that really does slice to ``value``, so a grounding proof means something."""
    start = text.index(value)
    return SourcePointer(source_id=CHIT, kind="doc_span", document_id=CHIT,
                         char_start=start, char_end=start + len(value))


@pytest.fixture()
def recipient(intake, ledger):
    return recipient_from_committed_ledger(intake.intake_id, ledger)


@pytest.fixture()
def draft(table, coverage_observation, recipient):
    return build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                recipient=recipient)


# --- the anti-fabrication proof ---------------------------------------------------------


def test_every_word_of_the_body_comes_from_the_template_or_from_a_cited_value(
        draft, table, coverage_observation):
    """Rebuild the body from the config row and the observation, and demand it match.

    If the drafting code ever composed a sentence of its own — a helpful extra line, a model's
    phrasing — this fails, because the reconstruction would not contain it.
    """
    monitor = table.by_id("coverage_conflict_on_file")
    rebuilt = monitor.body_template
    for name in monitor.placeholders("body_template"):
        rebuilt = rebuilt.replace("{" + name + "}", str(coverage_observation.value(name)))
    assert " ".join(draft.body.value.split()) == " ".join(rebuilt.split())


def test_every_doc_span_the_draft_cites_slices_to_the_value_it_stands_for(draft, chit_text):
    """EQUALITY, not containment — a whole-document pointer must not pass itself off as
    provenance (the rule `test_claim.py` states and this repo keeps)."""
    spans = [p for p in draft.because if p.kind == "doc_span"]
    assert spans, "the body interpolated committed values and cites none of them"
    for pointer in spans:
        sliced = chit_text[pointer.char_start:pointer.char_end]
        assert sliced in draft.body.value, (
            f"the draft cites {sliced!r} from {pointer.document_id} and does not say it")


def test_every_config_pointer_the_draft_cites_resolves_to_the_row_it_names(draft):
    rows = {r["id"]: r for r in
            yaml.safe_load(monitors_path().read_text(encoding="utf-8"))["monitors"]}
    cells = [p for p in draft.because
             if p.kind == "table_cell" and p.source_id == "clinic_monitors.yaml"]
    assert cells
    for pointer in cells:
        assert pointer.row_id in rows, pointer
        assert pointer.column in rows[pointer.row_id], pointer


def test_the_subject_and_body_carry_derived_pointers_naming_their_template(draft):
    for grounded in (draft.subject, draft.body):
        assert grounded.pointer.kind == "derived"
        assert "clinic_monitors.yaml" in (grounded.pointer.derivation or "")
        cited = grounded.pointer.derived_from or []
        assert any(p.table == "monitors" for p in cited), "the template row is not cited"


def test_because_lists_the_recipient_the_subject_and_the_body(draft):
    """A reviewer reads `because`; provenance it does not list is provenance nobody will see."""
    cited = {p.model_dump_json(exclude_none=True) for p in draft.because}
    for pointer in (draft.recipient.pointer, draft.subject.pointer, draft.body.pointer):
        assert pointer.model_dump_json(exclude_none=True) in cited


def test_because_names_the_condition_that_fired(draft):
    assert any(p.column == "condition" for p in draft.because), (
        "a draft must be able to say WHICH condition produced it, not only what it says")


# --- what cannot be drafted ---------------------------------------------------------------


def test_a_placeholder_with_no_grounded_fact_refuses_the_draft(table, intake, recipient):
    """The one that matters. Rendering `{package_code}` as a blank — or as itself — would put an
    unreviewed sentence in front of a patient."""
    partial = Observation(intake_id=intake.intake_id, facts={
        "unauthorised_test": GroundedValue(
            value="ECG", pointer=SourcePointer(source_id=CHIT, kind="doc_span", document_id=CHIT,
                                               char_start=0, char_end=3))})
    with pytest.raises(DraftRefused, match="package_code"):
        build_outbound_draft(table.by_id("coverage_conflict_on_file"), partial,
                             recipient=recipient)


def test_a_fact_whose_value_is_blank_is_not_a_fact(table, intake, recipient, chit_text):
    observation = Observation(intake_id=intake.intake_id, facts={
        "unauthorised_test": GroundedValue(value="   ",
                                           pointer=doc_pointer("PEE226", chit_text)),
        "package_code": GroundedValue(value="PEE226",
                                      pointer=doc_pointer("PEE226", chit_text))})
    with pytest.raises(DraftRefused, match="unauthorised_test"):
        build_outbound_draft(table.by_id("coverage_conflict_on_file"), observation,
                             recipient=recipient)


def test_a_fact_containing_a_placeholder_is_not_interpolated_again(table, intake, recipient,
                                                                   chit_text):
    """Template injection through patient data. A document says `{package_code}` and it must land
    as those literal characters, not as a second substitution."""
    observation = Observation(intake_id=intake.intake_id, facts={
        "unauthorised_test": GroundedValue(value="{package_code} and {anything}",
                                           pointer=doc_pointer("PEE226", chit_text)),
        "package_code": GroundedValue(value="PEE226",
                                      pointer=doc_pointer("PEE226", chit_text))})
    draft = build_outbound_draft(table.by_id("coverage_conflict_on_file"), observation,
                                 recipient=recipient)
    assert "{package_code} and {anything}" in draft.body.value


def test_a_recipient_from_the_wrong_door_is_refused(table, coverage_observation):
    """The config declares which door. A call site may not substitute a more convenient one."""
    typed = recipient_typed_by_human("patient@example.com", typed_by="Nurse Lim")
    with pytest.raises(DraftRefused, match="recipient_from"):
        build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                             recipient=typed)


def test_the_typed_door_satisfies_the_monitor_that_declares_it(table, intake, chit_text):
    monitor = table.by_id("intake_blocked_too_long")
    observation = Observation(intake_id=intake.intake_id, facts={
        "minutes_blocked": GroundedValue(
            value=34, pointer=SourcePointer(
                source_id=f"intake_queue:{intake.intake_id}", kind="table_cell",
                table="intake_queue", column="minutes_blocked", row_id=intake.intake_id)),
        "blocked_on": GroundedValue(
            value="an in-person identity check", pointer=SourcePointer(
                source_id=f"intake_queue:{intake.intake_id}", kind="table_cell",
                table="intake_queue", column="blocked_on", row_id=intake.intake_id))})
    draft = build_outbound_draft(
        monitor, observation,
        recipient=recipient_typed_by_human("patient@example.com", typed_by="Nurse Lim"))
    assert "34 minutes" in draft.body.value
    assert draft.recipient.origin == "typed_by_human"


# --- the draft type itself ------------------------------------------------------------------


def test_a_draft_cannot_be_built_without_a_reason(draft):
    """An action with no traceable justification is refused construction, exactly as an ungrounded
    field is (GRAPH_AGENT_AND_MCP.md §4.3)."""
    with pytest.raises(DraftRefused, match="because"):
        OutboundDraft(draft_id=draft.draft_id, recipient=draft.recipient, subject=draft.subject,
                      body=draft.body, playbook_id=draft.playbook_id, because=(),
                      created_at=dt.datetime.now(dt.timezone.utc))


def test_a_draft_whose_citations_omit_its_own_provenance_is_refused(draft):
    """`because` that does not list the recipient's pointer is `because` a reviewer cannot use."""
    without_recipient = tuple(p for p in draft.because if p is not draft.recipient.pointer)
    with pytest.raises(DraftRefused, match="recipient"):
        OutboundDraft(draft_id=draft.draft_id, recipient=draft.recipient, subject=draft.subject,
                      body=draft.body, playbook_id=draft.playbook_id,
                      because=without_recipient, created_at=dt.datetime.now(dt.timezone.utc))


def test_a_draft_is_inert_and_says_so(draft):
    assert draft.requires_human_approval is True
    payload = draft.to_payload()
    assert payload["requires_human_approval"] is True
    assert "status" not in payload, "a draft has no status until the outbox files it"
    assert draft.created_at.tzinfo is not None


def test_a_subject_with_a_line_break_is_refused(draft):
    with pytest.raises(Exception):
        OutboundDraft(
            draft_id=draft.draft_id, recipient=draft.recipient,
            subject=GroundedValue(value="hello\r\nBcc: attacker@evil.test",
                                  pointer=draft.subject.pointer),
            body=draft.body, playbook_id=draft.playbook_id, because=draft.because,
            created_at=dt.datetime.now(dt.timezone.utc))
