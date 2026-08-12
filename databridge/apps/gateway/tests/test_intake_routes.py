"""The `/intake/*` surface — and the confirmation gate, from the outside.

`PLAN.md` §5 Phase 4 names two of these explicitly under "Fixtures/tests": confirm without the
attestation must return `IDENTITY_NOT_VERIFIED` with the ledger unchanged, and confirm with it
must write N rows each with a resolvable pointer. The rest exist because an API is exactly where a
constraint enforced only in the library layer gets bypassed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from databridge_gateway import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:                  # `with` runs the lifespan
        yield c


@pytest.fixture(scope="module")
def chit_text():
    from databridge_intake.paths import fixtures_dir

    return (fixtures_dir() / "chits" / "chit_02_everwell_voucher.txt").read_text(encoding="utf-8")


@pytest.fixture
def uploaded(client, chit_text, request):
    """One fresh intake per test.

    `intake_id` is derived from the document, so a shared document_id would give every test the
    same intake — and the confirm test would leave a committed state behind for whatever ran next.
    Naming the document after the test keeps them independent and order-free.
    """
    document_id = f"{request.node.name}__chit_02.txt"
    r = client.post("/intake/upload", json={"document_id": document_id, "text": chit_text})
    assert r.status_code == 200, r.text
    return r.json()


STAFF = "staff:front_desk_01"


def _select(client, intake_id, patient_id, attested=True):
    return client.post(f"/intake/{intake_id}/select-patient", json={
        "patient_id": patient_id, "staff_id": STAFF, "attested_in_person": attested})


# --- upload + card ----------------------------------------------------------------------


def test_upload_returns_a_card_that_declares_identity_unverified(uploaded):
    assert uploaded["identity"]["requires_human_selection"] is True
    assert uploaded["identity"]["verified"] is False
    assert uploaded["identity"]["candidates"]


def test_upload_commits_nothing(client, uploaded):
    rows = client.get(f"/intake/{uploaded['intake_id']}/ledger").json()["rows"]
    assert rows == []


def test_an_oversized_document_is_refused(client):
    r = client.post("/intake/upload", json={"document_id": "big.txt", "text": "x" * 300_000})
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "DOCUMENT_TOO_LARGE"


def test_an_unknown_intake_is_404(client):
    assert client.get("/intake/nope").status_code == 404


# --- THE GATE ---------------------------------------------------------------------------


def test_confirm_without_the_attestation_is_refused_and_writes_nothing(client, uploaded):
    """PLAN.md §5 Phase 4, verbatim: 422 IDENTITY_NOT_VERIFIED, ledger row count unchanged."""
    intake_id = uploaded["intake_id"]
    before = len(client.get(f"/intake/{intake_id}/ledger").json()["rows"])
    r = client.post(f"/intake/{intake_id}/confirm",
                    json={"staff_id": STAFF, "attested_in_person": False})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"
    assert len(client.get(f"/intake/{intake_id}/ledger").json()["rows"]) == before


def test_confirm_with_the_attestation_but_no_selection_is_still_refused(client, uploaded):
    """Attesting that you checked an ID does not say WHICH registry row the person is."""
    intake_id = uploaded["intake_id"]
    r = client.post(f"/intake/{intake_id}/confirm",
                    json={"staff_id": STAFF, "attested_in_person": True})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"
    assert client.get(f"/intake/{intake_id}/ledger").json()["rows"] == []


def test_select_patient_without_the_attestation_is_refused(client, uploaded):
    intake_id = uploaded["intake_id"]
    patient_id = uploaded["identity"]["candidates"][0]["patient_id"]
    r = _select(client, intake_id, patient_id, attested=False)
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "IDENTITY_NOT_VERIFIED"


def test_a_patient_outside_the_candidate_list_cannot_be_selected(client, uploaded):
    """Otherwise a client could attach any registry row to any document."""
    r = _select(client, uploaded["intake_id"], "pat:000000000000")
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "NOT_A_CANDIDATE"


def test_the_full_confirm_path_writes_rows_each_with_a_resolvable_pointer(client, uploaded,
                                                                          chit_text):
    """The other half of PLAN.md's requirement: exactly N rows, each with a pointer that resolves."""
    intake_id = uploaded["intake_id"]
    patient_id = uploaded["identity"]["candidates"][0]["patient_id"]
    assert _select(client, intake_id, patient_id).status_code == 200

    r = client.post(f"/intake/{intake_id}/confirm",
                    json={"staff_id": STAFF, "attested_in_person": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["committed"] is True and body["entries_written"] > 0
    assert body["ledger_rows_after"] == body["ledger_rows_before"] + body["entries_written"]

    rows = client.get(f"/intake/{intake_id}/ledger").json()["rows"]
    assert len(rows) == body["entries_written"]
    for row in rows:
        pointer = row["pointer"]
        assert pointer["source_id"]
        assert row["confirmed_by"] == STAFF
        if pointer["kind"] == "doc_span":
            assert chit_text[pointer["char_start"]:pointer["char_end"]]


def test_a_second_confirm_is_refused_and_writes_no_duplicate_rows(client, uploaded):
    """A refusal that had already written. Reproduced by review: first confirm → 19 rows; second
    confirm → HTTP 422 while the ledger held 38, the originals superseded by the duplicates."""
    intake_id = uploaded["intake_id"]
    _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    first = client.post(f"/intake/{intake_id}/confirm",
                        json={"staff_id": STAFF, "attested_in_person": True})
    assert first.status_code == 200
    rows_after_first = len(client.get(f"/intake/{intake_id}/ledger").json()["rows"])

    second = client.post(f"/intake/{intake_id}/confirm",
                         json={"staff_id": STAFF, "attested_in_person": True})
    assert second.status_code == 422
    assert second.json()["refusal"]["code"] == "ALREADY_COMMITTED"
    assert len(client.get(f"/intake/{intake_id}/ledger").json()["rows"]) == rows_after_first


def test_a_second_staff_member_cannot_swap_the_selected_patient(client, chit_text):
    """Staff A attests and selects candidate A; staff B then selects candidate B and the record
    would still say A checked the ID — with every ledger row stamped `confirmed_by=A` for a
    candidate A never chose. The attester and the selector must be the same person."""
    r = client.post("/intake/upload", json={
        "document_id": "attester_mismatch__chit_03.txt",
        "text": (__import__("databridge_intake.paths", fromlist=["x"]).fixtures_dir()
                 / "chits" / "chit_03_everwell_group_invitation.txt").read_text(encoding="utf-8")})
    card = r.json()
    intake_id = card["intake_id"]
    candidates = card["identity"]["candidates"]
    assert len(candidates) == 2, "chit 3 must stay ambiguous for this test to mean anything"

    assert _select(client, intake_id, candidates[0]["patient_id"]).status_code == 200
    other = client.post(f"/intake/{intake_id}/select-patient", json={
        "patient_id": candidates[1]["patient_id"], "staff_id": "staff:someone_else",
        "attested_in_person": True})
    assert other.status_code == 422
    assert other.json()["refusal"]["code"] == "ATTESTER_MISMATCH"
    state = client.get(f"/intake/{intake_id}").json()["confirmation"]
    assert state["selected_patient_id"] == candidates[0]["patient_id"]
    assert state["identity_checked_in_person_by"] == STAFF


def test_selecting_a_patient_fills_the_questionnaire(client, uploaded):
    """The whole pre-fill capability was unreachable through HTTP: `run_intake` ran only at upload
    time with no selection, and no route re-ran it afterwards."""
    intake_id = uploaded["intake_id"]
    before = client.get(f"/intake/{intake_id}").json()["questionnaire"]["prefilled"]
    assert before == 0
    r = _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    assert r.status_code == 200
    assert r.json()["questionnaire_prefilled"] >= 10
    after = client.get(f"/intake/{intake_id}").json()
    assert after["questionnaire"]["prefilled"] >= 10
    assert after["intake_id"] == intake_id      # the re-run keeps the same intake


def test_a_selective_commit_writes_only_the_named_field(client, uploaded):
    intake_id = uploaded["intake_id"]
    _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    r = client.post(f"/intake/{intake_id}/confirm", json={
        "staff_id": STAFF, "attested_in_person": True,
        "field_paths": ["issuer.issuer_code"]})
    assert r.status_code == 200 and r.json()["entries_written"] == 1
    rows = client.get(f"/intake/{intake_id}/ledger").json()["rows"]
    assert [row["field_path"] for row in rows] == ["issuer.issuer_code"]


def test_ledger_rows_record_the_selected_patient(client, uploaded):
    intake_id = uploaded["intake_id"]
    patient_id = uploaded["identity"]["candidates"][0]["patient_id"]
    _select(client, intake_id, patient_id)
    client.post(f"/intake/{intake_id}/confirm",
                json={"staff_id": STAFF, "attested_in_person": True})
    rows = client.get(f"/intake/{intake_id}/ledger").json()["rows"]
    assert rows and all(row["selected_patient_id"] == patient_id for row in rows)


def test_the_ledger_route_404s_an_unknown_intake(client):
    """"No such intake" and "this intake committed nothing" are different answers."""
    assert client.get("/intake/nope/ledger").status_code == 404


def test_re_uploading_the_same_document_lands_on_the_same_intake(client, chit_text):
    """`intake_id` is derived from the document, not the clock. With the clock in the hash the
    queue accumulated a duplicate row per upload and any existing attestation was orphaned."""
    payload = {"document_id": "stable_id__chit_02.txt", "text": chit_text}
    first = client.post("/intake/upload", json=payload).json()["intake_id"]
    second = client.post("/intake/upload", json=payload).json()["intake_id"]
    assert first == second


def test_the_card_reports_why_the_gate_is_closed(client, uploaded):
    card = client.get(f"/intake/{uploaded['intake_id']}").json()
    assert card["confirmation"]["ready_to_commit"] is False
    assert "identity" in card["confirmation"]["blocked_because"]


# --- evidence click-through --------------------------------------------------------------


def test_evidence_resolves_to_the_exact_source_span(client, uploaded, chit_text):
    intake_id = uploaded["intake_id"]
    r = client.get(f"/intake/{intake_id}/evidence/issuer.issuer_code")
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == "EVWPA"
    assert body["source_text"] == "EVWPA"
    ptr = body["pointer"]
    assert chit_text[ptr["char_start"]:ptr["char_end"]] == "EVWPA"


def test_evidence_for_a_field_the_document_lacks_is_404_not_an_invention(client, uploaded):
    r = client.get(f"/intake/{uploaded['intake_id']}/evidence/subject.claimed_dob")
    assert r.status_code == 404 and r.json()["refusal"]["code"] == "NO_SUCH_FIELD"


# --- the claim draft ---------------------------------------------------------------------


def test_a_claim_draft_is_refused_before_confirmation(client, uploaded):
    r = client.get(f"/intake/{uploaded['intake_id']}/claim-draft")
    assert r.status_code == 422 and r.json()["refusal"]["code"] == "NOT_CONFIRMED"


def test_a_claim_draft_after_confirmation_is_explicitly_a_draft(client, uploaded):
    intake_id = uploaded["intake_id"]
    _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    client.post(f"/intake/{intake_id}/confirm",
                json={"staff_id": STAFF, "attested_in_person": True})
    body = client.get(f"/intake/{intake_id}/claim-draft").json()
    assert body["status"] == "draft"
    assert any("never files a claim" in c for c in body["caveats"])


def test_no_value_on_a_claim_draft_is_a_bare_string(client, uploaded):
    """CLAUDE.md §Hard constraints 2, at the API boundary.

    This route used to return `submit_to`, `issuer_code`, `package_code`, `patient_id` and
    `line_items` as naked values read from the in-memory draft. Every one of them is now either
    `null` — and named in `unresolved` — or an object carrying the pointer it came from.
    """
    intake_id = uploaded["intake_id"]
    _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    client.post(f"/intake/{intake_id}/confirm",
                json={"staff_id": STAFF, "attested_in_person": True})
    body = client.get(f"/intake/{intake_id}/claim-draft").json()

    for field in ("submit_to", "tpa_claim_required", "issuer_code", "package_code", "patient_id"):
        value = body[field]
        if value is None:
            assert field in body["unresolved"], f"{field} is blank without saying so"
            continue
        assert isinstance(value, dict), f"{field} is a bare value with no provenance"
        assert value["pointer"]["source_id"], f"{field} carries an empty pointer"
    assert body["line_items"], "chit 2 authorises tests; the claim must carry them"
    for line in body["line_items"]:
        assert line["pointer"]["source_id"], "a claim line with no provenance"


def test_a_partially_confirmed_intake_cannot_emit_what_was_never_confirmed(client, uploaded):
    """The specific defect: `field_paths` lets staff confirm a subset, and the old route returned
    the whole draft regardless — including fields no human had ever looked at."""
    intake_id = uploaded["intake_id"]
    _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    confirmed = client.post(f"/intake/{intake_id}/confirm", json={
        "staff_id": STAFF, "attested_in_person": True,
        # The issuer code and nothing else.
        "field_paths": ["issuer.issuer_code"]})
    assert confirmed.status_code == 200, confirmed.text

    body = client.get(f"/intake/{intake_id}/claim-draft").json()
    assert body["issuer_code"]["value"] == "EVWPA"
    assert body["line_items"] == [], (
        "no test was confirmed, so the claim must ask the payer for nothing")


def test_a_claim_draft_is_refused_when_no_issuer_was_confirmed(client, uploaded):
    """A walk-in self-pay has no third party, and neither does an intake whose issuer nobody
    confirmed. Naming one would put a bill in front of a company that never agreed to it."""
    intake_id = uploaded["intake_id"]
    _select(client, intake_id, uploaded["identity"]["candidates"][0]["patient_id"])
    client.post(f"/intake/{intake_id}/confirm", json={
        "staff_id": STAFF, "attested_in_person": True,
        "field_paths": ["subject.claimed_name"]})

    r = client.get(f"/intake/{intake_id}/claim-draft")
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "NO_PAYER_TO_CLAIM_FROM"


# --- the queue ----------------------------------------------------------------------------


def test_the_queue_lists_uploaded_intakes_with_their_gate_state(client, uploaded):
    body = client.get("/intake").json()
    row = next(i for i in body["intakes"] if i["intake_id"] == uploaded["intake_id"])
    assert row["identity_status"] == "single_candidate"
    assert row["identity_verified"] in (True, False)


# --- the guardrail, through the API -------------------------------------------------------


IDENTITY_QUESTIONS = [
    "Is this the right person?",
    "is his IC valid?",
    "Can you confirm this patient's identity?",
    "Does this NRIC belong to the patient?",
    "Should I accept this e-card as ID?",
    "Can we skip the identity check?",
]


@pytest.mark.parametrize("question", IDENTITY_QUESTIONS)
def test_the_ask_endpoint_refuses_identity_questions(client, question):
    r = client.post("/brain/query", json={"question": question})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "IDENTITY_VERIFICATION_OUT_OF_SCOPE"


def test_the_screen_endpoint_lets_the_ui_check_before_sending(client):
    assert client.post("/brain/screen",
                       json={"question": "Is that really him?"}).json()["refused"] is True
    assert client.post("/brain/screen",
                       json={"question": "What does WELL2 include?"}).json()["refused"] is False


def test_suggested_questions_come_from_config_not_the_bundle(client):
    body = client.get("/brain/suggested").json()
    assert body["questions"]
    assert "in person" in body["scope_note"]
    assert not any("acme" in q.lower() for q in body["questions"])
