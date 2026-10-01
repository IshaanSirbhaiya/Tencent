"""The intake pipeline — the single entrypoint the gateway and the CLI both call.

classify → extract → ground → resolve → eligibility → billing → prefill

One orchestrator so there is exactly one order of operations, and so the identity boundary is
visible in one place. Read the stage list and you can see what the machine decides (document type,
package, coverage, billing split) and what it refuses to (which patient this is).

**Nothing here commits.** ``run_intake`` produces a *draft* the front desk reads. Committing is a
separate, explicit act through ``ledger.commit``, gated on a staff attestation. The pipeline does
not take a ``ConfirmationState`` at all — it cannot write, so it cannot be tricked into writing.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict

from databridge_harness import Harness

from .billing import draft_billing, walk_in_draft
from .eligibility import determine_coverage
from .extract import extract_intake
from .gold import gold_harness
from .history import ClinicHistory, NoHistory
from .models import (BillingDraft, ConfirmationState, CoverageDetermination, DocumentType,
                     IntakeRecord, PatientRecord, PrefilledQuestionnaire, QuestionnaireKind,
                     ResolutionResult)
from .ocr import MediaIndex
from .prefill import prefill
from .clock import resolve_today
from .resolve import resolve
from .rules import CoverageRules, rules


class IntakeDraft(BaseModel):
    """Everything the front-desk summary card needs, and nothing committed.

    ``questionnaire`` is empty until a human selects a patient — see ``prefill``. That is not a
    missing feature on the card; it is the card showing that identity is still open.
    """

    model_config = ConfigDict(frozen=True)

    intake_id: str
    record: IntakeRecord
    resolution: ResolutionResult
    coverage: CoverageDetermination
    billing: BillingDraft
    questionnaire: PrefilledQuestionnaire

    @property
    def requires_human_selection(self) -> bool:
        """Always True. Mirrors ``ResolutionResult`` so a card renderer cannot read the draft as
        settled without going through the resolution."""
        return self.resolution.requires_human_selection


def run_intake(text: str,
               *,
               document_id: str,
               harness: Optional[Harness] = None,
               channel: str = "upload",
               media_type: str = "text/plain",
               received_at: Optional[dt.datetime] = None,
               intake_id: Optional[str] = None,
               confirmation: Optional[ConfirmationState] = None,
               selected_patient: Optional[PatientRecord] = None,
               today: Optional[dt.date] = None,
               table: Optional[CoverageRules] = None,
               history: Optional[ClinicHistory] = None,
               media: Optional[MediaIndex] = None) -> IntakeDraft:
    """Run the whole pipeline over one document's text.

    ``confirmation``/``selected_patient`` are optional and are only ever *forwarded*: with them the
    questionnaire pre-fills and the age band is checked against the selected patient; without them
    the draft is the pre-counter view — candidates ranked, nothing chosen, questionnaire blank.

    ``media`` is the page geometry from an uploaded scan, carried through so pointers can name the
    region of the original they were read from. It must have been built against **this** ``text``:
    it is an index into these exact offsets, not into the file the text came from. Every re-run for
    an intake therefore has to pass the stored text and the stored index together.
    """
    table = table or rules()
    today = resolve_today(today)
    harness = harness or gold_harness()
    # `NoHistory` by default, and that is the tested default: the pipeline must produce a valid card
    # with no graph attached at all. History is an ENRICHMENT — its absence removes flags, never an
    # intake's ability to be read or confirmed (PLAN.md §5 Phase 6).
    history = history if history is not None else NoHistory()

    record = extract_intake(text, document_id=document_id, harness=harness,
                            channel=channel, media_type=media_type, received_at=received_at,
                            intake_id=intake_id, media=media)

    if record.document_type.value == DocumentType.NO_DOCUMENT.value:
        # The walk-in path. No document means nothing to resolve against and nobody to bill but
        # the patient — a normal counter workflow, not a failure.
        resolution = resolve(record.subject, today=today)
        coverage = determine_coverage(record, today=today, table=table)
        return IntakeDraft(
            intake_id=record.intake_id, record=record, resolution=resolution, coverage=coverage,
            billing=walk_in_draft(table),
            questionnaire=PrefilledQuestionnaire(kind=QuestionnaireKind.NONE))

    resolution = resolve(record.subject, today=today)
    coverage = determine_coverage(
        record, selected_patient=selected_patient, candidates=resolution.candidates,
        today=today, table=table, history=history)
    billing = draft_billing(record, coverage, table=table)
    questionnaire = prefill(
        coverage.questionnaire, confirmation=confirmation, selected_patient=selected_patient,
        record=record, coverage=coverage, table=table)

    return IntakeDraft(intake_id=record.intake_id, record=record, resolution=resolution,
                       coverage=coverage, billing=billing, questionnaire=questionnaire)


def summary_card(draft: IntakeDraft) -> dict:
    """The front-desk card payload, computed server-side.

    Section 1 is identity and it is deliberately first and deliberately unresolved: candidates with
    their scores and the reason each matched, plus the explicit statement that a human must check
    in person. Everything else on the card is downstream of a decision nobody has made yet.
    """
    record = draft.record
    return {
        "intake_id": draft.intake_id,
        "document": {
            "document_id": record.document.document_id,
            "document_type": record.document_type.value,
            "confidence": record.document_type.confidence,
            "channel": record.document.channel,
        },
        "identity": {
            "requires_human_selection": True,
            "verified": False,
            "status": draft.resolution.status.value,
            "notes": list(draft.resolution.notes),
            "candidates": [{
                "patient_id": c.patient_id,
                "name": c.record.full_name.value,
                "id_number": c.record.id_number.value,
                "date_of_birth": str(c.record.date_of_birth.value),
                "score": c.score,
                "matched_on": c.matched_on,
                "conflicts": c.conflicts,
            } for c in draft.resolution.candidates],
        },
        "coverage": {
            "status": draft.coverage.status.value,
            "issuer_code": draft.coverage.issuer_code,
            "package_code": draft.coverage.package_code,
            "package_name": draft.coverage.package_name,
            "matched_rule_id": draft.coverage.matched_rule_id,
            "questionnaire": draft.coverage.questionnaire.value,
            "eligibility_flags": draft.coverage.eligibility_flags,
            "unmatched_tests": draft.coverage.unmatched_tests,
            "reasoning": draft.coverage.reasoning,
            # Findings from the clinic GRAPH rather than from this document, each with the pointers
            # of the traversal that produced it. Kept separate from `reasoning` so the card can say
            # where a claim came from — "another document on file" is not something this document
            # says, and presenting it as though it were would misattribute the evidence.
            "history_findings": [{
                "kind": f.kind, "message": f.message,
                "document_id": f.document_id, "patient_id": f.patient_id,
                "sources": [p.model_dump(mode="json", exclude_none=True) for p in f.sources],
            } for f in draft.coverage.history_findings],
            "authorised_tests": [{
                "as_printed": t.as_printed, "canonical": t.canonical,
                "in_package": t.in_package,
                "list_price_sgd": str(t.list_price_sgd) if t.list_price_sgd is not None else None,
                "source_field": t.source_field,
            } for t in draft.coverage.authorised_tests],
        },
        "billing": draft.billing.model_dump(mode="json"),
        "questionnaire": {
            "kind": draft.questionnaire.kind.value,
            "prefilled": draft.questionnaire.prefilled_count,
            "patient_must_answer": draft.questionnaire.patient_must_answer_count,
            "fields": [{
                "field_id": f.field_id, "label": f.label, "status": f.status.value,
                "value": f.value.value if f.value is not None else None,
                "requires_confirmation": f.requires_confirmation,
            } for f in draft.questionnaire.fields],
        },
        # The honesty channels, promoted to the card rather than buried: "not on document" and
        # "we refused this value" are things staff should see at a glance.
        "unresolved": list(record.unresolved),
        "refused": list(record.refused),
    }


def evidence_for(draft: IntakeDraft, field_path: str, text: str) -> Optional[dict]:
    """The pointer behind one field, plus the exact source slice it names.

    This is the click-through that makes the card auditable: every value on screen can show the
    characters it came from. Returns ``None`` for a path the record does not carry.
    """
    value = draft.record.grounded_fields().get(field_path)
    if value is None:
        return None
    pointer = value.pointer
    slice_text = None
    if pointer.kind == "doc_span" and pointer.char_start is not None:
        slice_text = text[pointer.char_start:pointer.char_end]
    return {
        "field_path": field_path,
        "value": value.value,
        "confidence": value.confidence,
        "pointer": pointer.model_dump(mode="json", exclude_none=True),
        "source_text": slice_text,
    }
