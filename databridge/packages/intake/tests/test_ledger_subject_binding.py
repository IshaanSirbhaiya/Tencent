"""R-17 — an attestation is bound to its SUBJECT, not to an intake id.

This project's signature bug ("state outliving its subject", REMEDIATION.md §1 Shape B) in its
sixth disguise. ``intake_id`` is derived from the document (``sha256(document_id + text sha)``) and
is therefore identical before and after a re-extraction, so a check a human made about one reading
of a document and one candidate list could authorise a record resolved against another one. The
guard that looked like it was watching that door — ``confirmation.intake_id != record.intake_id``
— compares two values that both survive the change, so it could never fire on it.

Every test here is written so that it FAILS if the binding is removed:

* the candidate set and the document revision each move the digest;
* a selection cannot be recorded without one;
* ``commit`` has no way to be asked politely to skip the check.
"""
from __future__ import annotations

import datetime as dt
import inspect

import pytest

from databridge_intake.ledger import (EvidenceLedger, IdentityNotVerified, SubjectChanged,
                                      attestation_subject_matches, subject_fingerprint_for)
from databridge_intake.models import (ConfirmationState, ResolutionCandidate, ResolutionResult,
                                      ResolutionStatus)
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake

STAFF = "staff:front_desk_01"
NOW = dt.datetime(2026, 8, 12, 9, 0, tzinfo=dt.timezone.utc)

# chit 3 is the deliberately ambiguous one: two registry candidates, so there is a real set to
# change. chit 2 resolves to one.
AMBIGUOUS = "chit_03_everwell_group_invitation.txt"
SINGLE = "chit_02_everwell_voucher.txt"


def _text(name: str) -> str:
    return (fixtures_dir() / "chits" / name).read_text(encoding="utf-8")


def _draft(name: str, **kw):
    return run_intake(_text(name), document_id=name, **kw)


@pytest.fixture(scope="module")
def ambiguous():
    return _draft(AMBIGUOUS)


@pytest.fixture(scope="module")
def single():
    return _draft(SINGLE)


def _attested(draft, resolution=None, patient_id=None) -> ConfirmationState:
    """What the counter records: an in-person check, then a pick out of the list on screen."""
    resolution = resolution if resolution is not None else draft.resolution
    patient_id = patient_id or resolution.candidates[0].patient_id
    return (ConfirmationState(intake_id=draft.intake_id)
            .attest_identity_checked(STAFF, NOW)
            .select_patient(patient_id,
                            subject_fingerprint=subject_fingerprint_for(
                                draft.record, resolution, patient_id)))


def _resolution(*candidates: ResolutionCandidate) -> ResolutionResult:
    ranked = tuple(sorted(candidates, key=lambda c: c.score, reverse=True))
    status = (ResolutionStatus.SINGLE_CANDIDATE if len(ranked) == 1
              else ResolutionStatus.MULTIPLE_CANDIDATES)
    return ResolutionResult(candidates=ranked, status=status)


# --- the bug itself ------------------------------------------------------------------


def test_the_intake_id_guard_cannot_see_a_re_extraction(ambiguous):
    """The premise of the whole file, asserted rather than assumed.

    If this ever stops holding — if `intake_id` starts moving when a document is re-read — then
    `commit`'s `intake_id` check would begin catching some of these cases and the tests below
    would be testing something weaker than they claim to.
    """
    again = _draft(AMBIGUOUS)
    assert again.intake_id == ambiguous.intake_id, (
        "intake_id must be stable across a re-extraction — that stability is exactly why binding "
        "an attestation to it does not bind it to anything")
    assert again.record.extracted_at != ambiguous.record.extracted_at, (
        "a re-extraction must be distinguishable from the reading it replaced")


def test_an_attestation_over_one_candidate_set_does_not_authorise_another(ambiguous):
    """THE test. A human checked an ID against candidates {A, B} and picked A. The document is
    re-read and now resolves to {A, C}. `intake_id` is unchanged, the selected patient is still a
    candidate, the state still says `ready_to_commit` — and the act the human performed was about
    a list that no longer exists. The gate must close."""
    a, b = ambiguous.resolution.candidates[0], ambiguous.resolution.candidates[1]
    state = _attested(ambiguous, _resolution(a, b), a.patient_id)

    # ... the same document, re-read, now offering {A, C}.
    c = _draft(SINGLE).resolution.candidates[0]
    assert c.patient_id not in {a.patient_id, b.patient_id}, "the fixture must actually differ"
    replaced = _resolution(a, c)

    assert state.intake_id == ambiguous.record.intake_id, (
        "the old guard compares equal — this is the case it cannot see")

    ledger = EvidenceLedger()
    with pytest.raises(SubjectChanged):
        ledger.commit(ambiguous.record, state,
                      subject_fingerprint=subject_fingerprint_for(
                          ambiguous.record, replaced, a.patient_id))
    assert ledger.count() == 0, "a refused commit must write nothing at all"


def test_an_attestation_does_not_survive_a_change_of_document_revision(single):
    """The other half: the candidate list is identical and the DOCUMENT was re-read.

    A re-upload lands on the same `intake_id` and deliberately keeps the existing attestation —
    the route will not let a repeated drop of a file rewrite "who checked this ID, and when". So
    the reading moves underneath a retained attestation, and the digest has to notice.
    """
    state = _attested(single)
    reread = _draft(SINGLE)
    assert reread.intake_id == single.intake_id

    ledger = EvidenceLedger()
    with pytest.raises(SubjectChanged):
        ledger.commit(reread.record, state,
                      subject_fingerprint=subject_fingerprint_for(
                          reread.record, reread.resolution, state.selected_patient_id))
    assert ledger.count() == 0


def test_the_matching_attestation_still_commits(single):
    """The guard has to let the real workflow through, or it is just an outage."""
    state = _attested(single)
    ledger = EvidenceLedger()
    entries = ledger.commit(single.record, state,
                            subject_fingerprint=subject_fingerprint_for(
                                single.record, single.resolution, state.selected_patient_id))
    assert entries and ledger.count(single.intake_id) == len(entries)


def test_re_checking_identity_against_the_new_reading_reopens_the_gate(single):
    """The gate closes on a re-read; it is not welded shut. A fresh in-person check against what
    the counter is looking at NOW produces a fresh digest and commits — which is the whole point:
    the human act is required again, not routed around."""
    reread = _draft(SINGLE)
    state = _attested(single)
    state = state.select_patient(state.selected_patient_id,
                                 subject_fingerprint=subject_fingerprint_for(
                                     reread.record, reread.resolution, state.selected_patient_id))
    ledger = EvidenceLedger()
    entries = ledger.commit(reread.record, state,
                            subject_fingerprint=subject_fingerprint_for(
                                reread.record, reread.resolution, state.selected_patient_id))
    assert entries


# --- the structural guards -----------------------------------------------------------


def test_a_selection_cannot_exist_without_a_subject_fingerprint(single):
    """Enforced at construction, beside the other identity invariants, so an attestation that
    cannot name its subject cannot be brought into existence at all."""
    with pytest.raises(ValueError, match="subject_fingerprint"):
        ConfirmationState(intake_id=single.intake_id,
                          identity_checked_in_person_by=STAFF, identity_checked_at=NOW,
                          selected_patient_id=single.resolution.candidates[0].patient_id)
    # …and not through the transition either.
    attested = ConfirmationState(intake_id=single.intake_id).attest_identity_checked(STAFF, NOW)
    with pytest.raises(TypeError):
        attested.select_patient("pat:whoever")            # type: ignore[call-arg]
    with pytest.raises(ValueError):
        attested.select_patient("pat:whoever", subject_fingerprint="")
    # …and not by copy, which is how a frozen model's guards usually get bypassed.
    with pytest.raises(ValueError):
        attested.model_copy(update={"selected_patient_id": "pat:whoever"})


def test_a_fingerprint_without_a_selection_is_refused(single):
    """The pair travels in both directions. A digest with nothing selected is a fingerprint of a
    choice nobody made — and it would read, to anything downstream, as an attestation."""
    with pytest.raises(ValueError, match="subject_fingerprint"):
        ConfirmationState(intake_id=single.intake_id,
                          identity_checked_in_person_by=STAFF, identity_checked_at=NOW,
                          subject_fingerprint="0" * 64)


def test_commit_cannot_be_asked_to_skip_the_subject_check():
    """The fix is a signature, not a convention (same discipline as
    `test_build_claim_draft_cannot_be_handed_the_working_draft`).

    A defaulted `subject_fingerprint=None` would protect exactly the call sites that already knew
    about it and let every other one opt out by saying nothing — a guard that fails open, which is
    Shape C. So the parameter list itself is the assertion.
    """
    params = inspect.signature(EvidenceLedger.commit).parameters
    assert "subject_fingerprint" in params, (
        "commit takes no subject fingerprint, so it cannot tell which resolution the attestation "
        "it is honouring was made about")
    param = params["subject_fingerprint"]
    assert param.default is inspect.Parameter.empty, (
        "subject_fingerprint has a default — a caller can now skip the check by omission")
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        "subject_fingerprint must be keyword-only so it can never be filled positionally by "
        "accident")


def test_omitting_the_fingerprint_is_a_typeerror_not_a_commit(single):
    """The signature assertion above, exercised rather than described."""
    ledger = EvidenceLedger()
    with pytest.raises(TypeError):
        ledger.commit(single.record, _attested(single))    # type: ignore[call-arg]
    assert ledger.count() == 0


def test_passing_no_fingerprint_explicitly_still_fails_closed(single):
    """`None` is accepted by the type and refused by the logic: it cannot equal a stored digest,
    and a state carrying no digest carries no selection either."""
    ledger = EvidenceLedger()
    with pytest.raises(IdentityNotVerified):
        ledger.commit(single.record, _attested(single), subject_fingerprint=None)
    assert ledger.count() == 0
    with pytest.raises(IdentityNotVerified):
        ledger.commit(single.record, ConfirmationState(intake_id=single.intake_id),
                      subject_fingerprint=None)
    assert ledger.count() == 0


# --- the digest itself ---------------------------------------------------------------


def test_the_digest_moves_with_each_part_of_the_subject(ambiguous):
    a, b = ambiguous.resolution.candidates[0], ambiguous.resolution.candidates[1]
    base = subject_fingerprint_for(ambiguous.record, ambiguous.resolution, a.patient_id)

    other_row = subject_fingerprint_for(ambiguous.record, ambiguous.resolution, b.patient_id)
    other_set = subject_fingerprint_for(ambiguous.record, _resolution(a), a.patient_id)
    later = subject_fingerprint_for(
        ambiguous.record.model_copy(update={"extracted_at": NOW + dt.timedelta(seconds=1)}),
        ambiguous.resolution, a.patient_id)
    other_doc = subject_fingerprint_for(
        ambiguous.record.model_copy(
            update={"document": ambiguous.record.document.model_copy(
                update={"document_id": "somewhere_else.txt"})}),
        ambiguous.resolution, a.patient_id)

    assert len({base, other_row, other_set, later, other_doc}) == 5, (
        "two different subjects share a digest — an attestation about one would authorise the "
        "other")


def test_the_digest_ignores_the_order_the_candidates_were_printed_in(ambiguous):
    """Membership is what the human was offered; a re-ranking is not a different set. Binding to
    the order would close the gate on a change that is not a change of subject."""
    a, b = ambiguous.resolution.candidates[0], ambiguous.resolution.candidates[1]
    flipped = ResolutionResult(
        candidates=(b.model_copy(update={"score": a.score}),
                    a.model_copy(update={"score": b.score})),
        status=ResolutionStatus.MULTIPLE_CANDIDATES)
    assert (subject_fingerprint_for(ambiguous.record, _resolution(a, b), a.patient_id)
            == subject_fingerprint_for(ambiguous.record, flipped, a.patient_id))


def test_a_fingerprint_of_no_selection_is_refused_rather_than_invented(single):
    """A digest of "nobody was selected" would be a stable value that two unattested intakes both
    match — a fingerprint for the absence of an attestation (Shape D)."""
    with pytest.raises(ValueError):
        subject_fingerprint_for(single.record, single.resolution, "")


# --- the shared read every surface uses ----------------------------------------------


def test_attestation_subject_matches_is_the_one_definition(single):
    """The queues and the card ask this, so "verified" cannot come to mean "verified about
    something else" on a dashboard while the gate knows better."""
    state = _attested(single)
    ok, why = attestation_subject_matches(single.record, single.resolution, state)
    assert ok and why == ""

    reread = _draft(SINGLE)
    stale, why = attestation_subject_matches(reread.record, reread.resolution, state)
    assert stale is False and why

    bare = ConfirmationState(intake_id=single.intake_id)
    none, why = attestation_subject_matches(single.record, single.resolution, bare)
    assert none is False and "identity" in why
