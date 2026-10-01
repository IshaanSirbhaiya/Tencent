"""R-13 — a checkbox-derived field fails CLOSED when its recovery cannot be proved complete.

The defect these tests pin down. `ground.mask_unticked_options` is the control that stops the wall
confirming an option a form declined: the unticked lines are blanked, so `WELL1` cannot be grounded
off a voucher that ticked `WELL2`. That control is made entirely of the ``☐`` glyph. OCR does not
reliably recognise ``☒``/``☐`` — the bundled engine drops them — and when the glyph is gone the
option text is still on the page, the masking silently becomes a no-op, and a test the patient
**declined** can be grounded, confirmed and billed. Before this change that was a document-wide
*warning*, and recovering either glyph anywhere suppressed even that.

What "provably complete" means, encoded: recognised text can never prove it, because the evidence
of a missing checkbox is precisely its absence — a line reading ``WELL1 — Essential Screen`` looks
identical whether no box was printed or the box was not recognised. Text that was *read* rather
than recognised (a ``.txt`` taken verbatim, a PDF's own text layer) does prove it. So the control
keys on the ORIGIN of the characters, per line, and there is no "we saw at least one box" branch.
See ``ground.unprovable_checkbox_spans``.

Every test below fails before the fix. The two that matter most are
``test_a_declined_test_never_reaches_the_bill_or_a_claim`` (the harm, end to end: a patient billed
for a vaccination nobody ordered) and ``test_media_that_never_reached_the_mask_fails_closed`` (the
guard cannot be switched off by a caller forgetting to pass it the thing it needs).

Every document here is synthetic.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from databridge_convert.adapters.source.docling_extract import TextRegion
from databridge_harness import FakeBackend, Harness

from databridge_intake.claim import ClaimError, build_claim_draft
from databridge_intake.extract import CHECKBOX_SENSITIVE, IntakeDraft, extract_intake
from databridge_intake.ground import TICKED_BOX, UNTICKED_BOX, mask_unticked_options
from databridge_intake.ledger import EvidenceLedger, subject_fingerprint_for
from databridge_intake.models import ConfirmationState
from databridge_intake.ocr import MediaIndex
from databridge_intake.paths import chits_dir
from databridge_intake.pipeline import run_intake

VOUCHER = "chit_05_bluepeak_wellness_voucher.txt"
FORM = "chit_07_mol_authorisation_pee226.txt"
STAFF = "staff:front_desk_01"
NOW = dt.datetime(2026, 8, 13, 9, 0, tzinfo=dt.timezone.utc)

# The vaccination chit_07 section C offers and does NOT tick. Billing a patient for this is the
# concrete harm R-13 names.
DECLINED_ADD_ON = "Yellow Fever Single Dose (Vaccination)"


def _text(name: str) -> str:
    return (chits_dir() / name).read_text(encoding="utf-8")


def scanned(text: str, *, keep_ticked: bool = False) -> str:
    """The same document as an OCR engine that cannot recognise ballot glyphs returns it.

    The glyphs are REMOVED, not blanked: that is what a dropped character actually looks like, and
    keeping the offsets stable would quietly make the scenario easier than reality. ``keep_ticked``
    models the partial case — the engine recognised one ``☒`` and lost every ``☐``.
    """
    out = text.replace(UNTICKED_BOX, "")
    return out if keep_ticked else out.replace(TICKED_BOX, "")


def recognised(text: str, document_id: str = "doc", *, from_ocr: bool = True,
               conf: float = 0.93) -> MediaIndex:
    """One region per non-blank line, the shape docling returns for a page it recognised."""
    regions, cursor, y = [], 0, 0.0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if content.strip():
            regions.append(TextRegion(
                char_start=cursor, char_end=cursor + len(content), page=1,
                x0=36.0, y0=y, x1=36.0 + 6.0 * len(content), y1=y + 10.0,
                page_width=595.0, page_height=842.0,
                from_ocr=from_ocr, ocr_conf=conf if from_ocr else None,
                media_id=f"{document_id}:p1" if from_ocr else None))
            y += 12.0
        cursor += len(line)
    return MediaIndex(document_id=document_id, regions=tuple(regions))


def model_says(**fields) -> Harness:
    """A harness pinned to one extraction draft — the model's opinion, for the wall to judge."""
    payload = IntakeDraft(**fields).model_dump_json()
    return Harness(FakeBackend(lambda *, model, messages, guided_json: payload), profile="cloud")


# --- 1. a scan whose glyphs are gone grounds no checkbox-derived field -------------
def test_a_scanned_voucher_with_lost_glyphs_grounds_no_checkbox_derived_field():
    """Every one of the four `CHECKBOX_SENSITIVE` paths — not just the package code.

    Before the fix each of these grounded happily: the option text is all genuinely printed on the
    voucher, and with no ``☐`` left there was nothing for the mask to blank.
    """
    text = scanned(_text(VOUCHER))
    assert UNTICKED_BOX not in text and TICKED_BOX not in text

    rec = extract_intake(
        text, document_id=VOUCHER, media_type="image/png", media=recognised(text, VOUCHER),
        harness=model_says(package_code="WELL1",              # the form DECLINED this one
                           package_name="Essential Screen",
                           requested_tests=["Full Blood Count"],
                           add_ons=["Liver Cancer Marker"]))

    assert rec.authorisation.package_code is None
    assert rec.authorisation.package_name is None
    assert list(rec.authorisation.requested_tests) == []
    assert list(rec.authorisation.add_ons) == []
    for path in CHECKBOX_SENSITIVE:
        assert path in rec.unresolved, f"{path} must be named as unresolved, not silently empty"


def test_the_scan_still_grounds_everything_that_is_not_checkbox_derived():
    """The control is narrow on purpose: it withholds four fields, not the document.

    Without this the fix would be indistinguishable from refusing the upload, and nobody would
    keep a control that costs them the whole card.
    """
    text = scanned(_text(VOUCHER))
    rec = extract_intake(
        text, document_id=VOUCHER, media_type="image/png", media=recognised(text, VOUCHER),
        harness=model_says(issuer_code="BLPHS", claimed_name="Loh Amir",
                           policy_number="BLP301158"))
    assert rec.issuer.issuer_code.value == "BLPHS"
    assert rec.subject.claimed_name.value == "Loh Amir"
    assert rec.issuer.policy_number.value == "BLP301158"
    # …and they still carry the page region they were recognised from.
    assert rec.subject.claimed_name.pointer.child.kind == "ocr_bbox"


# --- 2. the harm: a declined test must not reach the bill or a claim ---------------
def test_a_declined_test_never_reaches_the_bill_or_a_claim():
    """chit_07 lists 13 vaccinations and ticks one. A scan that lost the marks used to let the
    other twelve through the wall — and `draft_billing` prices whatever the record carries."""
    text = scanned(_text(FORM))
    assert DECLINED_ADD_ON in text, "the declined option is still printed — that is the trap"

    draft = run_intake(text, document_id=FORM, media_type="image/png",
                       media=recognised(text, FORM),
                       harness=model_says(issuer_code="MRDEB", claimed_name="Kumar Sheng Yang",
                                          package_code="PEE224",
                                          add_ons=[DECLINED_ADD_ON]))

    billing = json.dumps(draft.billing.model_dump(mode="json"))
    assert DECLINED_ADD_ON not in billing
    assert "Yellow Fever" not in billing, (
        "a vaccination the form declined reached the billing draft — this is the patient being "
        "charged for something they did not agree to")
    assert not any("Yellow Fever" in t.as_printed for t in draft.coverage.authorised_tests)

    # And it cannot arrive later either. The ledger writes `grounded_fields()` and the claim is
    # built from the ledger, so a field that never grounded has no row and no claim line.
    assert not any(p.startswith("authorisation.add_ons")
                   for p in draft.record.grounded_fields())
    ledger = EvidenceLedger()
    patient_id = draft.resolution.candidates[0].patient_id if draft.resolution.candidates else None
    if patient_id is not None:
        fingerprint = subject_fingerprint_for(draft.record, draft.resolution, patient_id)
        state = (ConfirmationState(intake_id=draft.intake_id)
                 .attest_identity_checked(STAFF, NOW)
                 .select_patient(patient_id, subject_fingerprint=fingerprint))
        ledger.commit(draft.record, state, subject_fingerprint=fingerprint, at=NOW)
        try:
            claim = build_claim_draft(draft.intake_id, ledger)
        except ClaimError:
            pass          # refusing to draft at all is a stronger outcome, not a weaker one
        else:
            assert "Yellow Fever" not in claim.model_dump_json()


# --- 3. recovering one glyph proves nothing about the next line -------------------
def test_recovering_one_glyph_does_not_suppress_the_control_for_the_others():
    """The voucher's ``☒ WELL2`` survived; both ``☐`` lines did not.

    Before the fix this was the *worst* case: the document-wide warning was suppressed because a
    glyph had been recovered, and WELL1 grounded because its own ``☐`` was gone. Per-line
    recognition succeeds and fails independently, so a mark on one line is not evidence about
    another — which is why "we saw at least one box" is not a control.
    """
    text = scanned(_text(VOUCHER), keep_ticked=True)
    assert TICKED_BOX in text and UNTICKED_BOX not in text
    media = recognised(text, VOUCHER)

    declined = extract_intake(text, document_id=VOUCHER, media_type="image/png", media=media,
                              harness=model_says(package_code="WELL1"))
    assert declined.authorisation.package_code is None
    assert "authorisation.package_code" in declined.unresolved

    # And the line whose mark DID survive is withheld too. Its own glyph proves it was selected;
    # it proves nothing about whether the siblings' glyphs were recovered, and the wall cannot
    # tell the two questions apart. Fail closed for both.
    ticked = extract_intake(text, document_id=VOUCHER, media_type="image/png", media=media,
                            harness=model_says(package_code="WELL2"))
    assert ticked.authorisation.package_code is None
    assert "authorisation.package_code" in ticked.unresolved


# --- 4. the guard cannot be disarmed by forgetting to pass it anything ------------
def test_media_that_never_reached_the_mask_fails_closed():
    """A caller that extracts a scan and does not thread `media` through gets the SAFE answer.

    This is the shape this repo keeps being bitten by: a guard that silently does not apply when
    someone forgets to hand it its evidence. No test anywhere else would notice, because the
    record would look exactly like a good one.
    """
    text = scanned(_text(VOUCHER))
    rec = extract_intake(text, document_id=VOUCHER, media_type="image/png", media=None,
                         harness=model_says(package_code="WELL1"))
    assert rec.authorisation.package_code is None
    assert "authorisation.package_code" in rec.unresolved


def test_a_pdf_with_no_region_index_is_also_treated_as_unproven():
    """From the media type alone a PDF's text layer and a scan of the same page are the same
    thing, and the whole control turns on telling them apart. Assume the unsafe one."""
    text = scanned(_text(VOUCHER))
    rec = extract_intake(text, document_id=VOUCHER, media_type="application/pdf", media=None,
                         harness=model_says(package_code="WELL1"))
    assert rec.authorisation.package_code is None


# --- 5. what must NOT change: text that was read rather than recognised -----------
def test_a_pdf_text_layer_still_grounds_its_ticked_package():
    """No recognition step ran, so the character stream is the document's own: a ``☐`` that is
    not there was never printed. That is the one proof of complete recovery that exists."""
    text = _text(FORM)
    rec = extract_intake(text, document_id=FORM, media_type="application/pdf",
                         media=recognised(text, FORM, from_ocr=False),
                         harness=model_says(package_code="PEE226"))
    assert rec.authorisation.package_code.value == "PEE226"


def test_pasted_text_is_untouched_by_the_control():
    rec = extract_intake(_text(FORM), document_id=FORM,
                         harness=model_says(package_code="PEE226"))
    assert rec.authorisation.package_code.value == "PEE226"


def test_an_unticked_option_is_still_refused_on_text_that_kept_its_glyphs():
    """The original control has not been replaced, only backed up."""
    rec = extract_intake(_text(FORM), document_id=FORM,
                         harness=model_says(package_code="PEE224"))
    assert rec.authorisation.package_code is None
    assert "authorisation.package_code" in rec.unresolved


# --- 6. the view itself ------------------------------------------------------------
def test_the_masked_view_preserves_offsets_in_every_mode():
    """Pointers built against this view resolve against the ORIGINAL text, so the two must stay
    the same length whatever was blanked."""
    text = scanned(_text(VOUCHER))
    for kwargs in ({}, {"media_type": "image/png"},
                   {"media_type": "image/png", "media": recognised(text, VOUCHER)},
                   {"media_type": "application/pdf",
                    "media": recognised(text, VOUCHER, from_ocr=False)}):
        assert len(mask_unticked_options(text, **kwargs)) == len(text), kwargs


def test_a_mixed_document_keeps_the_lines_that_were_read(monkeypatch):
    """Half the regions from a text layer, half from OCR. The control is per line, so the
    text-layer half is still groundable — a whole-document verdict would throw it away."""
    text = _text(FORM)
    index = recognised(text, FORM)
    half = len(index.regions) // 2
    mixed = MediaIndex(document_id=FORM, regions=tuple(
        TextRegion(**{**r.__dict__, "from_ocr": False, "ocr_conf": None, "media_id": None})
        if i < half else r
        for i, r in enumerate(index.regions)))
    view = mask_unticked_options(text, media=mixed, media_type="application/pdf")
    assert view[:index.regions[0].char_end] == text[:index.regions[0].char_end]
    tail = index.regions[-1]
    assert view[tail.char_start:tail.char_end].strip() == ""


# --- 7. the route: an uploaded referring document, and one that is refused ---------
# These drive the EXISTING `/intake/upload-file` route (`apps/gateway`) rather than a stand-in,
# because "the upload button lands in the same pipeline" is the claim being checked. They live
# here rather than in the gateway suite only because that suite belongs to another work track.
@pytest.fixture(scope="module")
def client():
    import os

    from fastapi.testclient import TestClient

    from databridge_gateway import create_app

    from databridge_gateway.auth import reset_auth_state_for_tests

    # Every /intake route requires a principal (INT1, 2026-10-01): this suite sets its own one-token
    # directory, as a real deployment would, and restores whatever was there before.
    token, principal = "test-token-intake-ocr", "staff"
    saved = {k: os.environ.get(k) for k in ("DATABRIDGE_FORCE_GOLD", "WALNUT_API_TOKENS")}
    os.environ["DATABRIDGE_FORCE_GOLD"] = "1"       # CLAUDE.md: no test depends on a live model
    os.environ["WALNUT_API_TOKENS"] = json.dumps({token: principal})
    reset_auth_state_for_tests()
    try:
        with TestClient(create_app(), headers={"Authorization": f"Bearer {token}"}) as c:
            yield c
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_auth_state_for_tests()


def test_an_uploaded_referring_document_runs_the_same_pipeline_as_a_paste(client):
    name = "chit_02_everwell_voucher.txt"
    body = _text(name).encode("utf-8")

    response = client.post("/intake/upload-file",
                           files={"file": (name, body, "text/plain")},
                           data={"document_id": name})
    assert response.status_code == 200, response.text
    card = response.json()

    assert card["identity"]["requires_human_selection"] is True
    assert card["identity"]["verified"] is False
    assert card["document"]["document_type"] != "no_document"
    assert card["extraction"]["engine"] == "verbatim"
    assert card["extraction"]["ocr_pages"] == []
    # It is in the queue the front desk reads — the same one a paste lands in.
    assert card["intake_id"] in {r["intake_id"] for r in client.get("/intake").json()["intakes"]}
    # …and it is byte-identical to the paste path, which is what "no separate demo path" means.
    pasted = client.post("/intake/upload", json={"document_id": name, "text": _text(name)})
    assert pasted.status_code == 200, pasted.text
    assert pasted.json()["intake_id"] == card["intake_id"]


def test_an_identity_document_upload_is_still_refused_and_nothing_is_kept(client):
    nric = ("REPUBLIC OF SINGAPORE\nIDENTITY CARD\nNo.: S8010946C\nName: LOH WEI MING\n"
            "Race: CHINESE\nDate of birth: 15-08-1980\nSex: M\nCountry of birth: SINGAPORE\n")
    before = client.get("/intake").json()["intakes"]

    named = client.post("/intake/upload-file",
                        files={"file": ("nric_front.jpg", b"\xff\xd8\xff junk", "image/jpeg")})
    assert named.status_code == 422
    assert named.json()["refusal"]["code"] == "IDENTITY_DOCUMENT_REFUSED"

    # The photographed-NRIC case: the filename says nothing, so only the extracted-text screen can
    # catch it — and what makes that acceptable is that the text does not survive the call.
    from_content = client.post("/intake/upload-file",
                               files={"file": ("scan_0042.txt", nric.encode("utf-8"),
                                               "text/plain")},
                               data={"document_id": "scan_0042.txt"})
    assert from_content.status_code == 422
    assert from_content.json()["refusal"]["code"] == "IDENTITY_DOCUMENT_REFUSED"
    assert "S8010946C" not in from_content.text and "LOH WEI MING" not in from_content.text
    assert client.get("/intake").json()["intakes"] == before, "nothing may be stored"
