"""``POST /intake/upload-file`` (T-36) — the file path, end to end.

Two things are being proved here, and only the second one is about extraction.

**1. A refused upload extracts NOTHING.** `/intake` promises the system never reads identity
documents or insurance e-cards. The refusal is not the status code — it is the absence of a stored
document, a record, a model call and a queue entry afterwards. So these tests assert on what did
*not* happen: `extract_upload` never reached, `run_intake` never called, the queue and the ledger
unmoved. A guard that returns 422 and stores the text anyway would pass a status-code test and
break the promise.

**2. Extraction happens once, and the pointers still resolve.** Every `SourcePointer` is
`(char_start, char_end)` into the document text, so the extracted text has to be what is stored and
what the evidence route slices. If those ever diverge the failure is silent: the evidence panel
frames the wrong characters. The PDF here is built from a real chit fixture, and the scan is that
PDF rendered to a raster — the same document arriving down the two different roads.

Every document in this file is synthetic.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH_HEADERS

from databridge_gateway import create_app, intake_routes

# The chit→PDF/PNG builder lives with the convert tests (it is a fixture *builder*, not product
# code, and that package already owns the extraction fixtures). Added explicitly rather than relying
# on pytest having collected that directory first, so this module runs when selected on its own.
_CONVERT_TESTS = pathlib.Path(__file__).resolve().parents[3] / "packages" / "convert" / "tests"
if str(_CONVERT_TESTS) not in sys.path:
    sys.path.insert(0, str(_CONVERT_TESTS))

from fixtures.chits.build import (BALLOT_TICKED, BALLOT_UNTICKED,  # noqa: E402
                                  ballot_font_path, pdf_page_to_png, text_to_pdf)

CHIT = "chit_07_mol_authorisation_pee226.txt"
STAFF = "staff:front_desk_01"


@pytest.fixture(scope="module", autouse=True)
def force_gold():
    """Pin the deterministic extraction backend. CLAUDE.md: no test may depend on a live model."""
    previous = os.environ.get("DATABRIDGE_FORCE_GOLD")
    os.environ["DATABRIDGE_FORCE_GOLD"] = "1"
    yield
    if previous is None:
        os.environ.pop("DATABRIDGE_FORCE_GOLD", None)
    else:
        os.environ["DATABRIDGE_FORCE_GOLD"] = previous


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        yield c


@pytest.fixture(scope="module")
def chit_text() -> str:
    from databridge_intake.paths import chits_dir

    return (chits_dir() / CHIT).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chit_pdf(chit_text) -> bytes:
    font = ballot_font_path()
    if font is None:
        pytest.skip("no font here carries U+2610/U+2612, so the fixture would lose the checkbox "
                    "marks it exists to test")
    return text_to_pdf(chit_text, font_path=font)


@pytest.fixture(scope="module")
def chit_scan(chit_pdf) -> bytes:
    return pdf_page_to_png(chit_pdf, page=0)


def _upload(client, name, blob, content_type, document_id=None):
    return client.post("/intake/upload-file",
                       files={"file": (name, blob, content_type)},
                       data={"document_id": document_id or name})


@pytest.fixture(scope="module")
def pdf_card(client, chit_pdf):
    response = _upload(client, "chit_07.pdf", chit_pdf, "application/pdf")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def scan_card(client, chit_scan):
    response = _upload(client, "chit_07_scan.png", chit_scan, "image/png")
    assert response.status_code == 200, response.text
    return response.json()


def _held(client, card):
    return client.app.state.intakes[card["intake_id"]]


# --- 1. the identity-document refusal ---------------------------------------------
def test_a_file_named_as_an_identity_document_is_refused_before_it_is_opened(
        client, chit_pdf, monkeypatch):
    # Not just "refused": the extractor is never reached at all. This is the layer that costs
    # nothing and runs on every upload.
    monkeypatch.setattr(intake_routes, "extract_upload",
                        lambda *a, **k: pytest.fail("extraction must not run on a refused upload"))
    before = client.get("/intake").json()["intakes"]
    response = _upload(client, "nric_front.jpg", chit_pdf, "image/jpeg")
    assert response.status_code == 422
    assert response.json()["refusal"]["code"] == "IDENTITY_DOCUMENT_REFUSED"
    assert client.get("/intake").json()["intakes"] == before


def test_an_identity_document_is_refused_from_its_content_and_nothing_is_kept(
        client, monkeypatch):
    # The filename says nothing — this is the photographed-NRIC case, and the only layer that can
    # catch it is one that reads the extracted text. What makes that acceptable is that the text
    # does not survive: no record, no model call, no stored document.
    monkeypatch.setattr(intake_routes, "run_intake",
                        lambda *a, **k: pytest.fail("no record may be built from a refused upload"))
    nric = ("REPUBLIC OF SINGAPORE\nIDENTITY CARD\nNo.: S8010946C\nName: LOH WEI MING\n"
            "Race: CHINESE\nDate of birth: 15-08-1980\nSex: M\nCountry of birth: SINGAPORE\n")
    before_queue = client.get("/intake").json()["intakes"]
    before_ids = set(client.app.state.intakes)

    response = _upload(client, "scan_0042.txt", nric.encode("utf-8"), "text/plain")

    assert response.status_code == 422
    body = response.json()
    assert body["refusal"]["code"] == "IDENTITY_DOCUMENT_REFUSED"
    assert set(client.app.state.intakes) == before_ids, "nothing may be stored"
    assert client.get("/intake").json()["intakes"] == before_queue
    # The refusal text must not quote the thing it refused to read.
    assert "S8010946C" not in response.text and "LOH WEI MING" not in response.text


def test_a_legitimate_chit_is_not_caught_by_the_guard(pdf_card):
    # The over-broad failure mode is the one that gets a guard switched off: three chits print
    # "NRIC/Passport No." and one tells the patient to bring their "identification card".
    assert pdf_card["document"]["document_type"] == "tpa_authorisation_form"


# --- 2. extraction happens once, and the pointers resolve -------------------------
def test_a_pdf_upload_produces_a_card(pdf_card):
    assert pdf_card["identity"]["requires_human_selection"] is True
    assert pdf_card["identity"]["verified"] is False
    assert pdf_card["extraction"]["engine"] == "docling"
    assert pdf_card["extraction"]["media_type"] == "application/pdf"
    assert pdf_card["extraction"]["page_count"] >= 1


def test_the_stored_text_is_the_extracted_text_and_evidence_slices_it(client, pdf_card):
    held = _held(client, pdf_card)
    fields = held["draft"].record.grounded_fields()
    spans = {p: v for p, v in fields.items() if v.pointer.kind == "doc_span"}
    assert spans, "the fixture should ground something out of the PDF"
    for path, value in spans.items():
        # The library-level invariant...
        assert held["text"][value.pointer.char_start:value.pointer.char_end] == value.value, path
        # ...and the same thing through the route the front desk actually clicks.
        evidence = client.get(f"/intake/{pdf_card['intake_id']}/evidence/{path}")
        assert evidence.status_code == 200, path
        assert evidence.json()["source_text"] == value.value


def test_the_checkbox_marks_survive_the_pdf_round_trip(client, pdf_card, chit_text):
    # On this form the ticked box IS the package signal — PEE224, PEE225 and PEE226 are all
    # printed, and only the ballot mark says which one was authorised.
    stored = _held(client, pdf_card)["text"]
    assert stored.count(BALLOT_TICKED) == chit_text.count(BALLOT_TICKED)
    assert stored.count(BALLOT_UNTICKED) == chit_text.count(BALLOT_UNTICKED)
    assert pdf_card["coverage"]["package_code"] == "PEE226"


def test_a_text_layer_pdf_claims_no_ocr(pdf_card, client):
    # No recognition ran, so there is no OCR confidence to report and no `ocr_bbox` may appear.
    assert pdf_card["extraction"]["ocr_pages"] == []
    for value in _held(client, pdf_card)["draft"].record.grounded_fields().values():
        assert value.pointer.child is None


# --- 3. the scan: doc_span → ocr_bbox --------------------------------------------
def test_a_scan_is_ocred_and_says_so(scan_card):
    assert scan_card["extraction"]["ocr_pages"] == [1]
    assert scan_card["extraction"]["regions"] > 0


def test_the_scan_warns_that_it_could_not_recover_the_checkbox_marks(scan_card):
    # The bundled OCR engine does not recognise ☒/☐, so `mask_unticked_options` silently becomes a
    # no-op and the wall can confirm an option the form declined. We cannot tell "no checkboxes"
    # from "checkboxes lost", so the card says so rather than guessing.
    assert any("checkbox" in w for w in scan_card["extraction"]["warnings"]), \
        scan_card["extraction"]["warnings"]


def test_every_pointer_from_a_scan_carries_the_page_region_it_was_read_from(client, scan_card):
    held = _held(client, scan_card)
    spans = {p: v for p, v in held["draft"].record.grounded_fields().items()
             if v.pointer.kind == "doc_span"}
    assert spans, "OCR should still ground values"
    media_ids = {f"{scan_card['document']['document_id']}:p1"}
    for path, value in spans.items():
        child = value.pointer.child
        assert child is not None, f"{path} cites the text but not the page"
        assert child.kind == "ocr_bbox"
        assert child.media_id in media_ids
        assert 0.0 < child.ocr_conf <= 1.0
        assert 0 <= child.x0 < child.x1 and 0 <= child.y0 < child.y1
        # And the span the child hangs off still resolves to the value it belongs to.
        assert held["text"][value.pointer.char_start:value.pointer.char_end] == value.value


def test_the_ocr_confidences_come_from_the_engine_rather_than_a_default(client, scan_card):
    confs = {v.pointer.child.ocr_conf
             for v in _held(client, scan_card)["draft"].record.grounded_fields().values()
             if v.pointer.kind == "doc_span" and v.pointer.child is not None}
    assert len(confs) > 1, f"a single repeated confidence looks like a default: {confs}"


def test_the_evidence_route_exposes_the_whole_chain(client, scan_card):
    held = _held(client, scan_card)
    path = next(p for p, v in held["draft"].record.grounded_fields().items()
                if v.pointer.kind == "doc_span" and v.pointer.child is not None)
    payload = client.get(f"/intake/{scan_card['intake_id']}/evidence/{path}").json()
    assert payload["source_text"] == payload["value"]
    child = payload["pointer"]["child"]
    assert child["kind"] == "ocr_bbox"
    assert {"media_id", "page", "x0", "y0", "x1", "y1", "ocr_conf"} <= set(child)


# --- 4. the page geometry survives a re-run -------------------------------------
def test_selecting_a_patient_does_not_strip_the_scan_regions(client, scan_card):
    # `select-patient` re-runs the whole pipeline. Re-running without the stored index would
    # rebuild every pointer with no `ocr_bbox` child, so an intake would lose its scan provenance
    # at exactly the moment a human took responsibility for it.
    candidates = scan_card["identity"]["candidates"]
    if not candidates:
        pytest.skip("this OCR pass produced no registry candidate to select")
    intake_id = scan_card["intake_id"]
    response = client.post(f"/intake/{intake_id}/select-patient", json={
        "patient_id": candidates[0]["patient_id"], "staff_id": STAFF,
        "attested_in_person": True})
    assert response.status_code == 200, response.text
    held = client.app.state.intakes[intake_id]
    assert held["media"], "the stored index must survive the re-run"
    children = [v.pointer.child for v in held["draft"].record.grounded_fields().values()
                if v.pointer.kind == "doc_span"]
    assert children and all(c is not None and c.kind == "ocr_bbox" for c in children)


# --- 5. the ordinary refusals ----------------------------------------------------
def test_a_file_type_we_do_not_read_is_refused(client):
    response = _upload(client, "notes.zip", b"PK\x03\x04nope", "application/zip")
    assert response.status_code == 422
    assert response.json()["refusal"]["code"] == "UNSUPPORTED_DOCUMENT"


def test_an_empty_file_is_refused(client):
    response = _upload(client, "empty.txt", b"", "text/plain")
    assert response.status_code == 422
    assert response.json()["refusal"]["code"] == "UNSUPPORTED_DOCUMENT"


def test_the_filename_becomes_the_document_id_when_none_is_supplied(client, chit_text):
    # Falling back to the filename is a defensible default; inventing an id is not, because the
    # document id is what the ledger row is stamped with.
    response = client.post("/intake/upload-file",
                           files={"file": ("alias__chit_07.txt", chit_text.encode("utf-8"),
                                           "text/plain")})
    assert response.status_code == 200, response.text
    assert response.json()["document"]["document_id"] == "alias__chit_07.txt"


def test_an_unbounded_document_id_is_refused(client, chit_text):
    response = _upload(client, "chit_07.txt", chit_text.encode("utf-8"), "text/plain",
                       document_id="x" * 300)
    assert response.status_code == 422
    assert response.json()["refusal"]["code"] == "INVALID_DOCUMENT_ID"


def test_a_text_file_upload_matches_a_paste_of_the_same_document(client, chit_text):
    """The two roads must arrive at the same pointers, or evidence means different things."""
    pasted = client.post("/intake/upload", json={"document_id": "paste__chit_07",
                                                 "text": chit_text})
    uploaded = _upload(client, "chit_07.txt", chit_text.encode("utf-8"), "text/plain",
                       document_id="file__chit_07")
    assert pasted.status_code == 200 and uploaded.status_code == 200
    a = client.app.state.intakes[pasted.json()["intake_id"]]
    b = client.app.state.intakes[uploaded.json()["intake_id"]]
    assert a["text"] == b["text"]
    for path, value in a["draft"].record.grounded_fields().items():
        other = b["draft"].record.grounded_fields()[path]
        assert (value.pointer.char_start, value.pointer.char_end) == \
               (other.pointer.char_start, other.pointer.char_end), path
