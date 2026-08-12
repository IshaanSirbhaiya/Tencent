"""Phase 0 — the schema-level guards around the hard constraints.

These are not ordinary model tests. `ResolutionResult` and `ConfirmationState` are where the
"identity verification is never automated" constraint is enforced *structurally* — if these can be
worked around, every downstream protection is decorative.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from pydantic import ValidationError

from databridge_core import GroundedValue, SourcePointer
from databridge_intake import (
    ConfirmationState,
    DocumentRef,
    DocumentType,
    IntakeRecord,
    IssuerBlock,
    ResolutionCandidate,
    ResolutionResult,
    ResolutionStatus,
    SubjectBlock,
)
from databridge_intake.models import AuthorisationBlock


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


NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)


def _gv(value: str, doc: str = "doc1", start: int = 0, end: int = 5) -> GroundedValue:
    return GroundedValue(
        value=value,
        pointer=SourcePointer(source_id=doc, kind="doc_span", document_id=doc,
                              char_start=start, char_end=end))


# --- ResolutionResult: candidates, never a confirmation ---------------------------
def test_requires_human_selection_cannot_be_turned_off():
    """The whole identity constraint rests on this being unrepresentable."""
    with pytest.raises(ValidationError):
        ResolutionResult(status=ResolutionStatus.SINGLE_CANDIDATE,
                         requires_human_selection=False)   # type: ignore[arg-type]


def _candidate(score: float, pid: str = "pat:one") -> ResolutionCandidate:
    from databridge_intake import registry
    return ResolutionCandidate(patient_id=pid, score=score, matched_on=["id_number"],
                               record=registry()[0])


def test_requires_human_selection_defaults_true_even_for_a_single_exact_candidate():
    r = ResolutionResult(status=ResolutionStatus.SINGLE_CANDIDATE,
                         candidates=(_candidate(1.0),))
    assert r.requires_human_selection is True


def test_status_cannot_contradict_the_candidate_list():
    """A SINGLE_CANDIDATE result with nothing in it renders a confident card backed by nothing."""
    with pytest.raises(ValidationError, match="contradicts 0 candidate"):
        ResolutionResult(status=ResolutionStatus.SINGLE_CANDIDATE)
    with pytest.raises(ValidationError, match="contradicts 1 candidate"):
        ResolutionResult(status=ResolutionStatus.NO_CANDIDATE, candidates=(_candidate(1.0),))
    with pytest.raises(ValidationError, match="contradicts 1 candidate"):
        ResolutionResult(status=ResolutionStatus.MULTIPLE_CANDIDATES,
                         candidates=(_candidate(1.0),))
    with pytest.raises(ValidationError, match="contradicts 1 candidate"):
        ResolutionResult(status=ResolutionStatus.INSUFFICIENT_IDENTIFIERS,
                         candidates=(_candidate(1.0),))


def test_candidates_must_be_ranked_best_first():
    # `top` and the UI both assume descending order; an unsorted list would silently mislead.
    with pytest.raises(ValidationError, match="ranked best-first"):
        ResolutionResult(status=ResolutionStatus.MULTIPLE_CANDIDATES,
                         candidates=(_candidate(0.4, "pat:a"), _candidate(0.9, "pat:b")))


def test_resolution_result_is_frozen_and_candidates_are_immutable():
    r = ResolutionResult(status=ResolutionStatus.NO_CANDIDATE)
    with pytest.raises(ValidationError):
        r.status = ResolutionStatus.SINGLE_CANDIDATE     # type: ignore[misc]
    assert isinstance(r.candidates, tuple)               # cannot be appended to


def test_top_is_display_ordering_not_a_selection():
    # `top` exists so the UI can order candidates; it must never be read as "the answer".
    assert ResolutionResult(status=ResolutionStatus.NO_CANDIDATE).top is None


# --- ConfirmationState: the human gate --------------------------------------------
def test_selection_without_identity_check_is_refused_at_construction():
    with pytest.raises(ValidationError, match="verified identity in person"):
        ConfirmationState(intake_id="i1", selected_patient_id="pat:abc")


def test_state_is_frozen_so_there_is_no_assignment_back_door():
    """A mutable model with validate_assignment does NOT hold: pydantic writes the field first and
    validates after, so a rejected assignment raises but leaves the forbidden value in place. The
    state is frozen precisely so that path does not exist."""
    s = ConfirmationState(intake_id="i1")
    with pytest.raises(ValidationError):
        s.selected_patient_id = "pat:abc"       # type: ignore[misc]
    assert s.selected_patient_id is None


def test_select_patient_before_attestation_is_refused_and_leaves_state_untouched():
    s = ConfirmationState(intake_id="i1")
    with pytest.raises(ValidationError, match="verified identity in person"):
        s.select_patient("pat:abc", subject_fingerprint=_fp("pat:abc"))
    assert s.selected_patient_id is None        # the original is unchanged, not half-applied


def test_transitions_return_new_instances_leaving_the_prior_state_intact():
    s0 = ConfirmationState(intake_id="i1")
    s1 = s0.attest_identity_checked("staff:nurse-01", NOW)
    s2 = s1.select_patient("pat:abc", subject_fingerprint=_fp("pat:abc"))
    assert s0.identity_checked_in_person_by is None      # s0 untouched
    assert s1.selected_patient_id is None                # s1 untouched
    assert s2.selected_patient_id == "pat:abc"


def test_commit_is_refused_without_a_completed_gate():
    s = ConfirmationState(intake_id="i1")
    with pytest.raises(ValueError, match="identity has not been verified"):
        s.commit()

    s = s.attest_identity_checked("staff:nurse-01", NOW)
    with pytest.raises(ValueError, match="no registry candidate has been selected"):
        s.commit()


def test_committed_true_cannot_be_constructed_around_the_gate():
    """Bypassing the transition methods and building the state directly must fail too."""
    with pytest.raises(ValidationError, match="committed=True"):
        ConfirmationState(intake_id="i1", committed=True)


def test_the_legitimate_order_works_attest_then_select_then_commit():
    s = ConfirmationState(intake_id="i1")
    assert s.is_ready_to_commit() == (False, "identity has not been verified in person by staff")

    s = s.attest_identity_checked("staff:nurse-01", NOW)
    ok, why = s.is_ready_to_commit()
    assert not ok and "no registry candidate has been selected" in why

    s = s.select_patient("pat:abc", subject_fingerprint=_fp("pat:abc"))
    assert s.is_ready_to_commit() == (True, "")

    s = s.commit()
    assert s.committed is True
    assert s.identity_checked_in_person_by == "staff:nurse-01"
    assert s.identity_checked_at == NOW


def test_attestation_requires_a_named_staff_member():
    s = ConfirmationState(intake_id="i1")
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="staff identifier"):
            s.attest_identity_checked(blank, NOW)
    assert s.identity_checked_in_person_by is None


def test_confirm_field_accumulates_without_mutating_the_prior_state():
    s0 = ConfirmationState(intake_id="i1")
    s1 = s0.confirm_field("issuer.issuer_code", "staff:nurse-01")
    s2 = s1.confirm_field("subject.claimed_name", "staff:nurse-01")
    assert s0.confirmed_fields == {}
    assert set(s1.confirmed_fields) == {"issuer.issuer_code"}
    assert set(s2.confirmed_fields) == {"issuer.issuer_code", "subject.claimed_name"}


# --- IntakeRecord ------------------------------------------------------------------
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


def test_an_empty_record_is_valid_the_walk_in_path_is_first_class():
    rec = _record(document_type=_gv(DocumentType.NO_DOCUMENT.value))
    assert rec.document_type.value == "no_document"
    assert rec.grounded_fields() == {"document_type": rec.document_type}


def test_grounded_fields_flattens_blocks_to_dotted_paths():
    rec = _record(
        issuer=IssuerBlock(issuer_code=_gv("EVWPA"), policy_number=_gv("EVW563298")),
        subject=SubjectBlock(claimed_name=_gv("Lim Arjun")),
    )
    paths = rec.grounded_fields()
    assert paths["issuer.issuer_code"].value == "EVWPA"
    assert paths["subject.claimed_name"].value == "Lim Arjun"
    assert "subject.claimed_id_number" not in paths      # unset fields are absent, not None-valued


def test_grounded_fields_indexes_list_elements_so_each_test_is_citable():
    rec = _record(authorisation=AuthorisationBlock(
        requested_tests=[_gv("Chest X-Ray"), _gv("Resting ECG")]))
    paths = rec.grounded_fields()
    assert paths["authorisation.requested_tests[0]"].value == "Chest X-Ray"
    assert paths["authorisation.requested_tests[1]"].value == "Resting ECG"


def test_unresolved_records_absence_as_a_fact():
    rec = _record(unresolved=["subject.claimed_id_number"])
    assert rec.subject.claimed_id_number is None
    assert "subject.claimed_id_number" in rec.unresolved


def test_intake_record_is_frozen():
    rec = _record()
    with pytest.raises(ValidationError):
        rec.intake_id = "tampered"      # type: ignore[misc]


def test_subject_fields_are_all_named_claimed():
    """The naming is load-bearing: no downstream reader should mistake these for verified facts."""
    non_claimed = [f for f in SubjectBlock.model_fields if not f.startswith("claimed_")]
    assert non_claimed == []
