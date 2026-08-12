"""The confirmation gate, the append-only ledger, the billing draft and the guardrails.

These are the Governance & Safety story. The two that matter most:
`test_the_ledger_refuses_without_an_in_person_attestation` and
`test_the_guardrail_runs_before_the_model_is_ever_called`.
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_intake.guardrails import REFUSAL_CODE, REFUSAL_TEXT, check_question
from databridge_intake.ledger import EvidenceLedger, IdentityNotVerified, LedgerError
from databridge_intake.models import ConfirmationState, CoverageStatus
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import evidence_for, run_intake, summary_card

STAFF = "staff:front_desk_01"
NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)


def _text(name: str) -> str:
    return (fixtures_dir() / "chits" / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def drafts():
    return {p.name: run_intake(p.read_text(encoding="utf-8"), document_id=p.name)
            for p in sorted((fixtures_dir() / "chits").glob("*"))}


def _attested(draft) -> ConfirmationState:
    return (ConfirmationState(intake_id=draft.intake_id)
            .attest_identity_checked(STAFF, NOW)
            .select_patient(draft.resolution.candidates[0].patient_id))


# --- THE GATE ---------------------------------------------------------------------------


def test_the_ledger_refuses_without_an_in_person_attestation(drafts):
    """Nothing reaches the evidence ledger until a named human has checked identity in person."""
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    with pytest.raises(IdentityNotVerified):
        ledger.commit(draft.record, ConfirmationState(intake_id=draft.intake_id))
    assert ledger.count() == 0, "a refused commit must write nothing at all"


def test_an_attestation_without_a_selection_is_still_refused(drafts):
    """Both halves are required: checking an ID proves who is standing there, selecting a
    candidate says which registry row that is. Neither alone is a resolution."""
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    partial = ConfirmationState(intake_id=draft.intake_id).attest_identity_checked(STAFF, NOW)
    with pytest.raises(IdentityNotVerified, match="no registry candidate"):
        ledger.commit(draft.record, partial)
    assert ledger.count() == 0


def test_a_selection_cannot_even_be_constructed_without_an_attestation(drafts):
    """The first lock, in the schema itself: `ConfirmationState` refuses to hold a selected
    patient with nobody's name against the identity check."""
    draft = drafts["chit_02_everwell_voucher.txt"]
    with pytest.raises(ValueError, match="identity_checked_in_person_by"):
        ConfirmationState(intake_id=draft.intake_id, selected_patient_id="pat:whoever")


def test_commit_with_an_attestation_writes_every_grounded_field(drafts):
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    entries = ledger.commit(draft.record, _attested(draft))
    assert entries
    assert ledger.count(draft.intake_id) == len(entries)
    for entry in entries:
        assert entry.confirmed_by == STAFF
        assert entry.pointer is not None


def test_a_confirmation_for_another_intake_is_refused(drafts):
    draft = drafts["chit_02_everwell_voucher.txt"]
    other = drafts["chit_01_meridian_referral.txt"]
    ledger = EvidenceLedger()
    with pytest.raises(LedgerError, match="confirmation is for intake"):
        ledger.commit(draft.record, _attested(other))
    assert ledger.count() == 0


def test_every_ledger_row_has_a_resolvable_pointer(drafts):
    """A committed fact that cannot say where it came from is not evidence."""
    draft = drafts["chit_01_meridian_referral.txt"]
    ledger = EvidenceLedger()
    ledger.commit(draft.record, _attested(draft))
    text = _text("chit_01_meridian_referral.txt")
    for row in ledger.entries():
        pointer = ledger.pointer_of(row)
        assert pointer.source_id
        if pointer.kind == "doc_span":
            assert text[pointer.char_start:pointer.char_end]


# --- APPEND-ONLY ------------------------------------------------------------------------


def test_the_ledger_rejects_an_update_at_the_database_level(drafts):
    """"Append-only" enforced by a trigger, not by everyone remembering. A convention survives
    exactly as long as nobody is in a hurry."""
    import sqlite3

    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    ledger.commit(draft.record, _attested(draft))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.conn.execute("UPDATE evidence SET value_json = '\"tampered\"'")


def test_the_ledger_rejects_a_delete_at_the_database_level(drafts):
    import sqlite3

    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    ledger.commit(draft.record, _attested(draft))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.conn.execute("DELETE FROM evidence")


def test_reads_never_observe_a_partially_inserted_commit_batch(drafts):
    """The bug this pins: `commit` documents its lock as preventing interleaved transactions, but
    `entries`/`count`/`current` used to run straight on the shared connection with no lock at all.
    `EvidenceLedger.conn` is ONE `sqlite3.Connection` shared across threads
    (`check_same_thread=False`), so a read from another thread mid-`commit` does not get its own
    snapshot — it sees exactly however many of that batch's INSERTs have executed so far on that
    same connection. `GET /intake/{id}/ledger` running concurrently with a `commit()` could
    therefore read a batch of, say, 3 rows out of a 9-row commit as if that were the whole answer.

    Forces the interleaving deterministically (no sleep-and-hope): a wrapped connection sleeps
    after every INSERT inside `commit`'s transaction, and a reader thread hammers `entries()`
    throughout. With the lock in place, every observed count must be 0 (before the transaction) or
    the full batch size (after it) — NEVER a number strictly in between.
    """
    import threading
    import time

    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    total = len(draft.record.grounded_fields())
    assert total >= 3, "need a multi-row commit for a partial read to even be observable"

    real_conn = ledger.conn
    first_insert_seen = threading.Event()

    class _SlowConn:
        """Delegates everything to the real connection; sleeps after each INSERT so a concurrent
        reader has a wide, reliable window to land mid-transaction if it is not locked out."""

        def execute(self, sql, args=()):
            result = real_conn.execute(sql, args)
            if sql.strip().upper().startswith("INSERT"):
                first_insert_seen.set()
                time.sleep(0.02)
            return result

        def __enter__(self):
            return real_conn.__enter__()

        def __exit__(self, *exc):
            return real_conn.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    ledger.conn = _SlowConn()

    observed: list[int] = []
    stop = threading.Event()

    def hammer():
        first_insert_seen.wait(timeout=2)
        while not stop.is_set():
            observed.append(ledger.count(draft.intake_id))

    reader = threading.Thread(target=hammer)
    reader.start()
    ledger.commit(draft.record, _attested(draft))
    stop.set()
    reader.join(timeout=2)

    assert observed, "the reader thread never got a sample in — test did not exercise the race"
    partial = [n for n in observed if 0 < n < total]
    assert partial == [], (
        f"observed a partially-inserted batch ({partial[:5]} of {total} rows) — reads are not "
        f"locked out during commit")


def test_a_second_commit_is_refused_and_writes_nothing(drafts):
    """The worst kind of bug: a refusal that had already written.

    `is_ready_to_commit()` stays True after a commit, so without an explicit `committed` check the
    rows landed and only THEN did the state transition raise — leaving a full duplicate batch
    superseding the originals behind a 422 that said nothing happened."""
    from databridge_intake.ledger import AlreadyCommitted

    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    state = _attested(draft)
    ledger.commit(draft.record, state)
    after_first = ledger.count()
    with pytest.raises(AlreadyCommitted):
        ledger.commit(draft.record, state.commit())
    assert ledger.count() == after_first, "a refused commit wrote rows"


def test_a_commit_records_which_candidate_the_human_selected(drafts):
    """Otherwise the durable record cannot show the pair the gate exists to prove — an attestation
    AND the patient it was made about. The selection would live only in process memory."""
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    state = _attested(draft)
    ledger.commit(draft.record, state)
    for row in ledger.entries():
        assert row["selected_patient_id"] == state.selected_patient_id


def test_committing_an_unknown_field_path_is_refused(drafts):
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    with pytest.raises(LedgerError, match="not values this document carries"):
        ledger.commit(draft.record, _attested(draft), field_paths=["issuer.not_a_field"])
    assert ledger.count() == 0


def test_a_selective_commit_writes_only_the_named_fields(drafts):
    """`field_paths` was accepted by the ledger and silently dropped by the route, so a client
    asking to commit one approved value got all nineteen and a 200 saying nothing was wrong."""
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    entries = ledger.commit(draft.record, _attested(draft), field_paths=["issuer.issuer_code"])
    assert [e.field_path for e in entries] == ["issuer.issuer_code"]
    assert ledger.count() == 1


def test_a_correction_is_a_new_row_that_supersedes_the_old_one(drafts):
    """Both rows stay readable, which is what makes "what did we believe, and when?" answerable."""
    draft = drafts["chit_02_everwell_voucher.txt"]
    ledger = EvidenceLedger()
    first = ledger.commit(draft.record, _attested(draft))
    before = ledger.count()
    # A correction arrives as a FRESH attestation (someone re-checking), not as a replay of a
    # committed state — that path is refused outright by the test above.
    second = ledger.commit(draft.record, _attested(draft), at=NOW + dt.timedelta(hours=1))
    assert ledger.count() == before + len(second) > before
    assert any(e.supersedes is not None for e in second)
    assert all(e.supersedes is None for e in first)
    # the live view collapses to one row per field, history intact underneath
    assert len(ledger.current(draft.intake_id)) == len(first)


# --- THE GUARDRAIL ----------------------------------------------------------------------


IDENTITY_QUESTIONS = [
    "Is this the right person?",
    "Is that really him?",
    "Can you confirm this patient's identity?",
    "Is his IC valid?",
    "Does this NRIC belong to the patient standing here?",
    "Should I accept this e-card as ID?",
    "Can we skip the identity check for this one?",
    "Verify the passport is genuine please",
    # Paraphrases a real staff member would type — every one of these was a bypass found by review.
    "Is the woman standing here the passport holder?",
    "Do the registry details line up with the visitor?",
    "Has this person passed the identity check?",
    "Can I let her proceed under this NRIC?",
    "This is actually him, right?",
    "Confirm who this patient is",
]


@pytest.mark.parametrize("question", IDENTITY_QUESTIONS)
def test_identity_shaped_questions_are_refused_deterministically(question):
    result = check_question(question)
    assert result.refused, f"not refused: {question!r}"
    assert result.code == REFUSAL_CODE
    assert result.answer == REFUSAL_TEXT


ORDINARY_QUESTIONS = [
    "What tests are authorised for Lim Arjun?",
    "What does the WELL2 package include?",
    "Which patients does Meridian Life Assurance cover?",
    "Who is the issuer on chit 2?",
    "What is Loh Wei Ming's registry date of birth?",
    "Which intakes are waiting on an identity check?",
    # Over-refusals found by review. An assistant that refuses these teaches staff to route around
    # it, which costs more than it protects.
    "Which package matches this patient?",
    "What tests match the patient's package?",
    "Is this the correct patient questionnaire?",
    "Can you confirm who this is billed to?",
    "Is the passport number correct as printed on the referral?",
    "Can you verify the identity field was copied from the document?",
]


@pytest.mark.parametrize("question", ORDINARY_QUESTIONS)
def test_ordinary_clinic_questions_are_not_refused(question):
    """Scoping matters as much as coverage. Refusing every question that names a patient would
    make the assistant useless and teach staff to route around it."""
    assert not check_question(question).refused, f"wrongly refused: {question!r}"


def test_the_guardrail_runs_before_the_model_is_ever_called():
    """The point of a deterministic pre-filter: the refusal does not depend on a model reading a
    prompt carefully. Spy on the backend and assert it is never touched."""
    from databridge_harness import FakeBackend, Harness

    calls: list[dict] = []

    def spy(*, model, messages, guided_json):
        calls.append({"model": model})
        return "{}"

    harness = Harness(FakeBackend(spy), profile="cloud")

    for question in IDENTITY_QUESTIONS:
        result = check_question(question)
        if result.refused:
            continue                       # the caller returns here, never reaching the harness
        harness.complete_structured(messages=[], guided_json={}, task_class="x")
    assert calls == [], "the model was called for an identity question"


def test_a_newline_does_not_defeat_the_guardrail():
    """Every pattern uses `.{0,N}` and `.` does not cross a line break, so before whitespace
    normalisation a single newline let the question straight through to the model while the same
    words on one line were refused. For "the guarantee", that is the difference between a control
    and a decoration."""
    one_line = "Can you confirm this patient's identity?"
    assert check_question(one_line).refused
    for wrapped in (one_line.replace("confirm ", "confirm\n"),
                    one_line.replace("this ", "this\r\n"),
                    one_line.replace(" ", "   ")):
        assert check_question(wrapped).refused, repr(wrapped)


def test_the_refusal_says_what_it_will_do_instead():
    """A refusal that only says no trains people to work around it."""
    assert "registry candidates" in REFUSAL_TEXT
    assert "You make the call." in REFUSAL_TEXT


def test_the_refusal_never_hedges_toward_confirming():
    lowered = REFUSAL_TEXT.lower()
    for forbidden in ("probably", "likely the same", "appears to be", "should be fine"):
        assert forbidden not in lowered


# --- BILLING --------------------------------------------------------------------------


def test_a_document_defined_rule_covers_the_tests_it_lists(drafts):
    """The bug this pins: `in_package` is only meaningful for a rule that HAS a package list. For
    a `tests_from_document` rule the document's list IS the covered scope, and splitting on
    `in_package` billed the patient SGD 195 for a referral the insurer pays for."""
    billing = drafts["chit_02_everwell_voucher.txt"].billing
    assert billing.billed_to == "insurer"
    assert len(billing.covered_items) == 4
    assert billing.patient_payable_items == []
    # Zero, not None — the patient owes nothing, which is a different statement from "we could not
    # price this". See `test_a_fully_covered_patient_owes_zero_not_unknown`.
    assert billing.estimated_patient_payable_sgd == 0


def test_an_add_on_beyond_a_coded_package_is_patient_payable(drafts):
    """Chit 7 orders an ECG and an MMR vaccination on top of PEE226."""
    billing = drafts["chit_07_mol_authorisation_pee226.txt"].billing
    assert billing.billed_to == "government_scheme"
    assert len(billing.patient_payable_items) == 2
    assert billing.estimated_patient_payable_sgd == 105


def test_undetermined_coverage_bills_nobody(drafts):
    """Chit 8. Defaulting to the patient would hand a bill to someone whose insurer simply did not
    state a package code."""
    billing = drafts["chit_08_mol_authorisation_vital.txt"].billing
    assert billing.billed_to == "unknown"
    assert billing.estimated_patient_payable_sgd is None
    assert any("payer is unknown" in c for c in billing.caveats)


def test_a_fully_covered_patient_owes_zero_not_unknown(drafts):
    """`None` means "we could not price this". Zero means "the patient owes nothing". Collapsing
    them loses the one distinction this module is otherwise careful to keep — and six of the nine
    fixtures were reporting a fully covered visit as an unknown amount."""
    billing = drafts["chit_01_meridian_referral.txt"].billing
    assert billing.patient_payable_items == []
    assert billing.estimated_patient_payable_sgd == 0


def test_a_test_outside_the_catalogue_is_not_asserted_as_patient_payable(drafts):
    """Its coverage is UNKNOWN. It must land in `unknown_coverage_items`, its own bucket — NOT in
    `patient_payable_items`, which would tell a UI the patient pays for something nobody
    established. Only a caveat, never the structured payable list, says coverage is unresolved."""
    from databridge_core import GroundedValue, SourcePointer

    from databridge_intake.billing import draft_billing
    from databridge_intake.eligibility import determine_coverage

    record = drafts["chit_01_meridian_referral.txt"].record
    ptr = SourcePointer(source_id="x", kind="doc_span", document_id="x", char_start=0, char_end=1)
    tweaked = record.model_copy(update={
        "authorisation": record.authorisation.model_copy(update={
            "requested_tests": [*record.authorisation.requested_tests,
                                GroundedValue(value="Cranial Aura Scan", pointer=ptr,
                                              confidence=1.0)]})})
    billing = draft_billing(tweaked, determine_coverage(tweaked))
    assert all(i["as_printed"] != "Cranial Aura Scan" for i in billing.patient_payable_items)
    entry = next(i for i in billing.unknown_coverage_items
                 if i["as_printed"] == "Cranial Aura Scan")
    assert entry["canonical"] is None
    assert any("established manually" in c for c in billing.caveats)


def test_uncatalogued_tests_do_not_present_a_confident_zero_total(drafts):
    """The bug this pins: when EVERY printed test is uncatalogued, `patient_payable_items` (the
    catalogue-matched bucket) is empty, so the old total collapsed to a confident SGD 0 sitting
    right beside a non-empty `unknown_coverage_items` list — "nothing owed" printed next to tests
    staff still had to price and assign a payer to by hand.

    The first remedy flagged the zero as a LOWER BOUND and left the number at 0. Review's answer
    to that (R-16) is the one pinned here: a caveat is prose a consumer may not render, while the
    figure is what gets printed on the card. An incomplete total is `None`, and whatever DID price
    is exposed separately, under a name that cannot be mistaken for the whole."""
    from databridge_core import GroundedValue, SourcePointer

    from databridge_intake.billing import draft_billing
    from databridge_intake.eligibility import determine_coverage

    record = drafts["chit_01_meridian_referral.txt"].record
    ptr = SourcePointer(source_id="x", kind="doc_span", document_id="x", char_start=0, char_end=1)
    all_uncatalogued = record.model_copy(update={
        "authorisation": record.authorisation.model_copy(update={
            "requested_tests": [GroundedValue(value="Cranial Aura Scan", pointer=ptr,
                                              confidence=1.0)],
            "add_ons": []})})
    billing = draft_billing(all_uncatalogued, determine_coverage(all_uncatalogued))
    assert billing.patient_payable_items == []
    assert len(billing.unknown_coverage_items) == 1
    assert billing.estimated_patient_payable_sgd is None, (
        "a total printed beside unpriced tests is a number nobody can stand behind")
    assert billing.known_subtotal_sgd is None, "nothing priced, so there is no subtotal either"
    assert "TOTAL_IS_LOWER_BOUND" in billing.caveats
    assert any("established manually" in c for c in billing.caveats)


def test_a_genuinely_zero_total_is_still_distinguishable_from_an_unknown_one(drafts):
    """The distinction this module exists to keep. Everything covered and nothing outstanding is
    a real SGD 0; it must not be collapsed into `None` by the fix above."""
    from databridge_intake.billing import draft_billing
    from databridge_intake.eligibility import determine_coverage

    record = drafts["chit_01_meridian_referral.txt"].record
    billing = draft_billing(record, determine_coverage(record))
    if not billing.patient_payable_items and not billing.unknown_coverage_items:
        assert billing.estimated_patient_payable_sgd == 0
        assert "TOTAL_IS_LOWER_BOUND" not in billing.caveats


def test_a_test_printed_twice_is_billed_once(drafts):
    """Two lines for one test double its price on the card."""
    from databridge_intake.billing import draft_billing
    from databridge_intake.eligibility import determine_coverage

    record = drafts["chit_07_mol_authorisation_pee226.txt"].record
    doubled = record.model_copy(update={
        "authorisation": record.authorisation.model_copy(update={
            "requested_tests": [*record.authorisation.requested_tests,
                                *record.authorisation.requested_tests]})})
    billing = draft_billing(doubled, determine_coverage(doubled))
    canonicals = [i["canonical"] for i in billing.patient_payable_items if i["canonical"]]
    assert len(canonicals) == len(set(canonicals))


def test_every_draft_declares_its_prices_are_synthetic(drafts):
    """A plausible dollar figure with no disclaimer is the easiest thing for a demo to get wrong."""
    for name, draft in drafts.items():
        assert any("synthetic" in c for c in draft.billing.caveats), name


def test_a_billing_draft_is_never_final(drafts):
    for draft in drafts.values():
        assert draft.billing.status == "draft"


# --- THE SUMMARY CARD -------------------------------------------------------------------


def test_the_card_always_says_identity_is_unverified(drafts):
    for name, draft in drafts.items():
        identity = summary_card(draft)["identity"]
        assert identity["requires_human_selection"] is True, name
        assert identity["verified"] is False, name


def test_the_card_surfaces_the_honesty_channels(drafts):
    """`unresolved` and `refused` are promoted to the card rather than buried."""
    card = summary_card(drafts["chit_03_everwell_group_invitation.txt"])
    assert "subject.claimed_id_number" in card["unresolved"]


def test_the_card_shows_every_candidate_for_an_ambiguous_document(drafts):
    card = summary_card(drafts["chit_05_bluepeak_wellness_voucher.txt"])
    assert len(card["identity"]["candidates"]) == 2
    assert card["identity"]["status"] == "multiple_candidates"


def test_the_card_carries_the_coverage_reasoning_trace(drafts):
    card = summary_card(drafts["chit_05_bluepeak_wellness_voucher.txt"])
    assert card["coverage"]["reasoning"]
    assert "age_outside_package_band" in card["coverage"]["eligibility_flags"]


def test_evidence_click_through_resolves_to_the_source_text(drafts):
    """Every value on the card can show the characters it came from."""
    draft = drafts["chit_01_meridian_referral.txt"]
    text = _text("chit_01_meridian_referral.txt")
    evidence = evidence_for(draft, "issuer.issuer_code", text)
    assert evidence["value"] == "MRDEB"
    assert evidence["source_text"] == "MRDEB"


def test_evidence_for_an_absent_field_is_none_not_an_invention(drafts):
    draft = drafts["chit_03_everwell_group_invitation.txt"]
    text = _text("chit_03_everwell_group_invitation.txt")
    assert evidence_for(draft, "subject.claimed_id_number", text) is None


# --- THE PIPELINE ----------------------------------------------------------------------


def test_the_questionnaire_is_blank_until_a_patient_is_selected(drafts):
    """The card shows an empty questionnaire not because pre-fill failed, but because identity is
    still open."""
    for name, draft in drafts.items():
        assert draft.questionnaire.prefilled_count == 0, name


def test_selecting_a_patient_fills_the_questionnaire():
    text = _text("chit_02_everwell_voucher.txt")
    draft = run_intake(text, document_id="chit_02_everwell_voucher.txt")
    selected = draft.resolution.candidates[0].record
    confirmed = run_intake(text, document_id="chit_02_everwell_voucher.txt",
                           confirmation=_attested(draft), selected_patient=selected)
    assert confirmed.questionnaire.prefilled_count >= 10


def test_the_pipeline_cannot_write_to_the_ledger():
    """`run_intake` takes no ledger and returns a draft. It cannot commit, so it cannot be tricked
    into committing."""
    import inspect

    from databridge_intake import pipeline

    source = inspect.getsource(pipeline)
    assert "EvidenceLedger" not in source
    assert ".commit(" not in source


def test_a_walk_in_produces_a_valid_self_pay_draft():
    draft = run_intake("", document_id="walkin_01.json", channel="walk_in")
    assert draft.coverage.status is CoverageStatus.DETERMINED
    assert draft.billing.billed_to == "patient"
    assert draft.resolution.status.value == "insufficient_identifiers"
    assert summary_card(draft)["identity"]["verified"] is False


def test_every_billing_line_cites_each_of_its_four_claims(drafts):
    """R-18. A line asserts four things — what was printed, what it maps to, whether the package
    covers it, and what it costs — and it carried ONE `source_field` string for all of them. That
    string is a dotted path into the IntakeRecord: it grounds the printed text and nothing else,
    so on the one artefact where the number is the point, the price and the coverage decision rode
    along uncited. `claim.py` fixed this for `ClaimLine`; this is the draft the patient is shown."""
    from databridge_intake.billing import draft_billing
    from databridge_intake.eligibility import determine_coverage

    checked = 0
    for draft in drafts.values():
        billing = draft_billing(draft.record, determine_coverage(draft.record))
        lines = [*billing.covered_items, *billing.patient_payable_items,
                 *billing.unknown_coverage_items]
        for line in lines:
            for claim in ("as_printed", "canonical", "in_package", "list_price_sgd"):
                if line.get(claim) is None and claim != "in_package":
                    continue                       # absent values need no citation
                assert claim in line["pointers"] or claim in line["unresolved"], (
                    f"{line['as_printed']}: {claim} is neither cited nor named as unresolved")
                if claim in line["pointers"]:
                    assert line["pointers"][claim]["kind"], f"{claim} pointer has no kind"
            checked += 1
    assert checked > 0, "no billing lines were exercised"


def test_the_billing_draft_cites_who_pays(drafts):
    """`billed_to` is a determination read from a rule row, and nothing on the draft said so."""
    from databridge_intake.billing import draft_billing
    from databridge_intake.eligibility import determine_coverage

    for draft in drafts.values():
        billing = draft_billing(draft.record, determine_coverage(draft.record))
        if billing.billed_to == "unknown":
            continue
        assert "billed_to" in billing.field_sources
        pointer = billing.field_sources["billed_to"]
        assert pointer.kind == "table_cell" and pointer.source_id == "coverage_rules.yaml"
