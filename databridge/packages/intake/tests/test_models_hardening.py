"""Regression tests from the Phase 0 review gate — schema invariants and provenance.

The reviewers' recurring point was that several guarantees were stated in docstrings but not
enforced by anything. These tests are the enforcement.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib

import pytest
from pydantic import ValidationError

from databridge_core import GroundedValue, SourcePointer


def _fp(patient_id: str) -> str:
    """A subject fingerprint for the state-machine tests.

    `ConfirmationState` refuses a selection that cannot name the reading it was made about
    (R-17), so every selection here needs one. The real digest —
    `databridge_intake.ledger.subject_fingerprint_for` — needs a document revision and a candidate
    list, and these tests deliberately have neither: they exercise the state machine, not the
    pipeline. Derived per patient id so two subjects never share a digest, which is the property
    that matters here; a constant would let one attestation stand in for another.
    """
    return hashlib.sha256(patient_id.encode("utf-8")).hexdigest()

from databridge_intake import (
    ConfirmationState,
    DocumentRef,
    DocumentType,
    EvidenceEntry,
    IntakeRecord,
    IssuerBlock,
    PrefilledField,
    PrefilledQuestionnaire,
    PrefillStatus,
    QuestionnaireKind,
    SubjectBlock,
    VisitType,
    patient_registry_csv,
    registry,
)

NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)


def _gv(value, doc: str = "doc1") -> GroundedValue:
    return GroundedValue(
        value=value,
        pointer=SourcePointer(source_id=doc, kind="doc_span", document_id=doc,
                              char_start=0, char_end=5))


def _record(**kw) -> IntakeRecord:
    base = dict(
        intake_id="intake-1",
        document=DocumentRef(document_id="doc1", sha256="abc", media_type="text/plain",
                             channel="upload", received_at=NOW),
        document_type=_gv(DocumentType.HEALTH_CHECKUP_VOUCHER.value),
        extracted_at=NOW,
    )
    base.update(kw)
    return IntakeRecord(**base)


# --- provenance actually resolves ----------------------------------------------------
def test_registry_pointers_resolve_back_to_the_exact_source_cell():
    """The reviewers' sharpest test-gap note: the pointer tests checked pointer *metadata* but
    never opened the CSV to confirm the cited cell really holds the emitted value. A pointer that
    does not resolve is decoration, and the whole anti-hallucination story rests on it."""
    with open(patient_registry_csv(), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    for record in registry()[:40]:
        for field in ("full_name", "id_number", "sex", "nationality", "address",
                      "postal_code", "email", "drug_allergy"):
            gv = getattr(record, field)
            ptr = gv.pointer
            row = rows[int(ptr.row_id) - 1]        # row_id is the 1-based DATA row
            assert row[ptr.column] == gv.value, (
                f"{field}: pointer says {ptr.column!r} row {ptr.row_id} but that cell holds "
                f"{row[ptr.column]!r}, not {gv.value!r}")


def test_derived_dob_pointer_chains_back_to_a_cell_that_holds_the_raw_date():
    with open(patient_registry_csv(), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for record in registry()[:20]:
        ptr = record.date_of_birth.pointer
        base = ptr.derived_from[0]
        raw = rows[int(base.row_id) - 1][base.column]
        # the derivation string names the transformation, and the raw cell is quoted in it
        assert raw in ptr.derivation
        assert record.date_of_birth.value.isoformat() in ptr.derivation


def test_id_type_confidence_reflects_inference_strength():
    """An NRIC/FIN pattern hit is a positive match; 'passport' is inferred by exclusion. The
    difference should be visible downstream rather than both claiming certainty."""
    from databridge_intake.registry import classify_id_type
    from databridge_intake import IdType
    assert classify_id_type("S8010946C") is IdType.NRIC_FIN
    assert all(r.id_type.confidence == 1.0 for r in registry()[:20])


# --- closed vocabularies are enforced, not merely documented -------------------------
def test_document_type_outside_the_enum_is_refused():
    # A classifier returning "referral_letter" instead of "insurer_referral_letter" would
    # otherwise flow through the pipeline and merely present as "no coverage found".
    with pytest.raises(ValidationError, match="not a DocumentType"):
        _record(document_type=_gv("referral_letter"))


def test_visit_type_outside_the_enum_is_refused():
    with pytest.raises(ValidationError, match="not a VisitType"):
        _record(visit_type=_gv("screening"))


def test_issuer_kind_outside_the_enum_is_refused():
    with pytest.raises(ValidationError, match="not a IssuerKind"):
        _record(issuer=IssuerBlock(issuer_kind=_gv("insuurer")))


def test_valid_enum_values_pass():
    rec = _record(document_type=_gv(DocumentType.TPA_AUTHORISATION_FORM.value),
                  visit_type=_gv(VisitType.OCCUPATIONAL_HEALTH_SCREENING.value))
    assert rec.visit_type.value == "occupational_health_screening"


# --- `unresolved` is a claim about the record, so it must be true of the record ------
def test_unresolved_may_not_name_a_field_that_has_a_value():
    with pytest.raises(ValidationError, match="listed as unresolved but has a value"):
        _record(issuer=IssuerBlock(issuer_code=_gv("EVWPA")),
                unresolved=("issuer.issuer_code",))


def test_unresolved_may_not_name_a_field_that_does_not_exist():
    with pytest.raises(ValidationError, match="not a field of this schema"):
        _record(unresolved=("subject.blood_type",))


def test_unresolved_may_not_repeat_a_path():
    with pytest.raises(ValidationError, match="duplicate unresolved path"):
        _record(unresolved=("subject.claimed_id_number", "subject.claimed_id_number"))


def test_unresolved_accepts_genuinely_absent_fields():
    rec = _record(unresolved=("subject.claimed_id_number", "authorisation.package_code"))
    assert rec.subject.claimed_id_number is None
    assert "subject.claimed_id_number" in rec.unresolved


# --- the evidence ledger entry -------------------------------------------------------
def test_evidence_entry_requires_a_real_source_pointer():
    """A ledger row whose provenance is an unvalidated string can hold "" and still look like
    evidence. Typing it means an entry that cannot say where it came from cannot be written."""
    with pytest.raises(ValidationError):
        EvidenceEntry(intake_id="i1", field_path="issuer.issuer_code", value="EVWPA",
                      pointer="not json",            # type: ignore[arg-type]
                      extracted_at=NOW, confirmed_by="staff:1", confirmed_at=NOW)


def test_evidence_entry_requires_a_named_confirmer():
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d",
                        char_start=0, char_end=5)
    with pytest.raises(ValidationError, match="confirmed_by"):
        EvidenceEntry(intake_id="i1", field_path="f", value="v", pointer=ptr,
                      extracted_at=NOW, confirmed_by="   ", confirmed_at=NOW)


def test_a_well_formed_evidence_entry_is_accepted():
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d",
                        char_start=0, char_end=5)
    e = EvidenceEntry(intake_id="i1", field_path="issuer.issuer_code", value="EVWPA",
                      pointer=ptr, extracted_at=NOW, confirmed_by="staff:nurse-01",
                      confirmed_at=NOW)
    assert e.pointer.document_id == "d"


# --- questionnaire prefill safety ----------------------------------------------------
def test_prefilled_status_without_a_value_is_refused():
    # would render an empty box the patient believes is already answered
    with pytest.raises(ValidationError, match="status=prefilled with no value"):
        PrefilledField(field_id="name", label="Name", status=PrefillStatus.PREFILLED)


def test_patient_must_answer_carrying_a_value_is_refused():
    # this is exactly the clinical-prefill leak the scope line forbids
    with pytest.raises(ValidationError, match="cannot arrive pre-answered"):
        PrefilledField(field_id="asthma", label="Asthma", value=_gv("Yes"),
                       status=PrefillStatus.PATIENT_MUST_ANSWER)


def test_prefill_status_typo_is_rejected_rather_than_silently_zeroing_the_metric():
    # prefilled_count feeds the demo's time-saved number; a typo must not read as "0 saved"
    with pytest.raises(ValidationError):
        PrefilledField(field_id="name", label="Name", value=_gv("X"),
                       status="prefill")            # type: ignore[arg-type]


def test_prefilled_counts():
    q = PrefilledQuestionnaire(kind=QuestionnaireKind.GENERAL_HEALTH, fields=(
        PrefilledField(field_id="name", label="Name", value=_gv("Lim Arjun"),
                       status=PrefillStatus.PREFILLED),
        PrefilledField(field_id="asthma", label="Asthma"),
        PrefilledField(field_id="pregnant", label="Pregnant",
                       status=PrefillStatus.NOT_APPLICABLE),
    ))
    assert q.prefilled_count == 1
    assert q.patient_must_answer_count == 1


# --- the copy back door --------------------------------------------------------------
def test_model_copy_cannot_forge_a_committed_state():
    """pydantic's default model_copy skips validation entirely, so an unvalidated copy
    constructor on a gate model is a bypass. This one re-validates."""
    s = ConfirmationState(intake_id="i1")
    with pytest.raises(ValidationError):
        s.model_copy(update={"selected_patient_id": "pat:anything", "committed": True})


def test_model_copy_cannot_retract_an_attestation_under_a_live_selection():
    s = (ConfirmationState(intake_id="i1")
         .attest_identity_checked("staff:nurse-01", NOW)
         .select_patient("pat:one", subject_fingerprint=_fp("pat:one")))
    with pytest.raises(ValidationError, match="verified identity in person"):
        s.model_copy(update={"identity_checked_in_person_by": None})


def test_attestation_cannot_be_silently_reassigned():
    s = ConfirmationState(intake_id="i1").attest_identity_checked("staff:nurse-01", NOW)
    with pytest.raises(ValueError, match="already attested"):
        s.attest_identity_checked("staff:someone-else", NOW)


def test_attestation_requires_a_timestamp():
    s = ConfirmationState(intake_id="i1")
    with pytest.raises(ValueError, match="requires the time"):
        s.attest_identity_checked("staff:nurse-01", None)   # type: ignore[arg-type]


def test_an_attester_without_a_time_cannot_be_constructed():
    with pytest.raises(ValidationError, match="must be set together"):
        ConfirmationState(intake_id="i1", identity_checked_in_person_by="staff:1")


def test_the_gate_stays_closed_once_committed():
    s = (ConfirmationState(intake_id="i1")
         .attest_identity_checked("staff:nurse-01", NOW)
         .select_patient("pat:one", subject_fingerprint=_fp("pat:one")).commit())
    for action in (lambda: s.select_patient("pat:other", subject_fingerprint=_fp("pat:other")),
                   lambda: s.commit(),
                   lambda: s.confirm_field("f", "staff:1"),
                   lambda: s.attest_identity_checked("staff:2", NOW)):
        with pytest.raises(ValueError, match="already committed"):
            action()
