"""T-36 — the pointer chain, and the one invariant an upload path can silently break.

    GroundedValue
      └─ pointer: doc_span(document_id, char_start, char_end)      ← the extracted text
           └─ child: ocr_bbox(media_id, page, x0,y0,x1,y1, ocr_conf)  ← the region on the scan

The end-to-end proof over a real PDF and a real scan lives in
``apps/gateway/tests/test_intake_upload_file.py``, where the whole route runs. These tests hold the
*logic* to account without a model or an OCR engine in the loop, so a failure names the rule that
broke rather than "the extraction changed".

The invariant under all of it: ``text[char_start:char_end]`` must still be the value. Every one of
these assertions exists because an upload path is where that quietly stops being true — the stored
text and the text pointers resolve against drift apart, and the symptom is not an exception, it is
an evidence panel that frames the wrong words.
"""
from __future__ import annotations

import pytest
from databridge_core import GroundedValue, SourcePointer
from databridge_convert.adapters.source.docling_extract import TextRegion

from databridge_intake.extract import CHECKBOX_SENSITIVE, extract_intake
from databridge_intake.gold import gold_harness
from databridge_intake.ocr import (MediaIndex, UnsupportedUpload, attach_media_provenance,
                                   extract_upload)
from databridge_intake.paths import chits_dir

CHIT = "chit_07_mol_authorisation_pee226.txt"


def line_regions(text: str, document_id: str = "doc", *, from_ocr: bool = True,
                 conf: float = 0.95) -> MediaIndex:
    """One region per line of ``text``, laid out down a page — the shape docling returns.

    Synthetic on purpose: the geometry is not what these tests are about, and running a real OCR
    engine to obtain it would make them slow and machine-dependent for no extra assurance.
    """
    regions, cursor, y = [], 0, 0.0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.strip():
            regions.append(TextRegion(
                char_start=cursor, char_end=cursor + len(stripped), page=1,
                x0=36.0, y0=y, x1=36.0 + 6.0 * len(stripped), y1=y + 10.0,
                page_width=595.0, page_height=842.0,
                from_ocr=from_ocr, ocr_conf=conf if from_ocr else None,
                media_id=f"{document_id}:p1" if from_ocr else None))
            y += 12.0
        cursor += len(line)
    return MediaIndex(document_id=document_id, regions=tuple(regions))


@pytest.fixture(scope="module")
def chit_text() -> str:
    return (chits_dir() / CHIT).read_text(encoding="utf-8")


# --- MediaIndex: which span gets which box ---------------------------------------
def test_a_span_inside_one_line_gets_that_line_box():
    text = "alpha beta\ngamma delta\n"
    index = line_regions(text)
    start = text.index("delta")
    child = index.child_for(start, start + len("delta"))
    assert child is not None and child.kind == "ocr_bbox"
    assert child.page == 1 and child.ocr_conf == 0.95
    assert (child.y0, child.y1) == (12.0, 22.0)          # the second line, not the first


def test_a_span_crossing_two_lines_gets_their_union_and_the_weaker_confidence():
    text = "Kumar Sheng\nYang\n"
    regions = list(line_regions(text).regions)
    regions[1] = TextRegion(**{**regions[1].__dict__, "ocr_conf": 0.61})
    index = MediaIndex(document_id="doc", regions=tuple(regions))
    child = index.child_for(0, len(text))
    # A value is only as trustworthy as its worst character, so the union carries the WEAKER score.
    assert child.ocr_conf == 0.61
    assert child.y0 == 0.0 and child.y1 == 22.0


def test_a_span_that_touches_a_second_page_stays_on_the_first():
    # A box spanning two pages is not a region on any page, so the far-page lines are dropped
    # rather than merged into a box that resolves to nothing.
    a = TextRegion(char_start=0, char_end=10, page=1, x0=0, y0=0, x1=50, y1=10,
                   page_width=595, page_height=842, from_ocr=True, ocr_conf=0.9,
                   media_id="doc:p1")
    b = TextRegion(char_start=12, char_end=20, page=2, x0=0, y0=0, x1=50, y1=10,
                   page_width=595, page_height=842, from_ocr=True, ocr_conf=0.9,
                   media_id="doc:p2")
    child = MediaIndex(document_id="doc", regions=(a, b)).child_for(0, 20)
    assert child.page == 1 and child.media_id == "doc:p1"


def test_a_text_layer_region_never_becomes_a_child():
    # No recognition step ran, so there is no OCR confidence, and the `ocr_bbox` kind requires one.
    index = line_regions("alpha\nbeta\n", from_ocr=False)
    assert index.child_for(0, 5) is None


def test_an_empty_index_is_falsy_and_attaches_nothing():
    value = GroundedValue(value="x", pointer=SourcePointer(
        source_id="doc", kind="doc_span", document_id="doc", char_start=0, char_end=1))
    assert attach_media_provenance(value, MediaIndex(document_id="doc")) is value
    assert attach_media_provenance(value, None) is value


# --- attach_media_provenance ------------------------------------------------------
def test_the_child_is_hung_off_a_derived_pointer_s_evidence_span():
    # A classified field cites its evidence one level down, through `derived_from`. Attaching only
    # to the top pointer left the quoted fields traceable to the scan and the derived ones not.
    text = "alpha beta\ngamma delta\n"
    index = line_regions(text)
    value = GroundedValue(value="tpa_authorisation_form", pointer=SourcePointer(
        source_id="doc", kind="derived", derivation="classified from layout markers",
        derived_from=[SourcePointer(source_id="doc", kind="doc_span", document_id="doc",
                                    char_start=0, char_end=10)]))
    attached = attach_media_provenance(value, index)
    assert attached.pointer.derived_from[0].child is not None
    assert attached.pointer.derived_from[0].child.kind == "ocr_bbox"


def test_an_existing_child_is_never_replaced():
    child = SourcePointer(source_id="doc", kind="ocr_bbox", media_id="other:p9", page=9,
                          x0=1, y0=2, x1=3, y1=4, ocr_conf=0.5)
    value = GroundedValue(value="x", pointer=SourcePointer(
        source_id="doc", kind="doc_span", document_id="doc", char_start=0, char_end=5,
        child=child))
    attached = attach_media_provenance(value, line_regions("alpha\nbeta\n"))
    assert attached.pointer.child.media_id == "other:p9"


# --- the whole record -------------------------------------------------------------
@pytest.fixture(scope="module")
def scanned_record(chit_text):
    """The chit read as though it had arrived as a scan: same text, plus page geometry."""
    return extract_intake(chit_text, document_id=CHIT, harness=gold_harness(),
                          media=line_regions(chit_text, CHIT))


def test_every_grounded_value_still_resolves_to_its_own_source_text(scanned_record, chit_text):
    fields = scanned_record.grounded_fields()
    assert fields, "the fixture should ground something"
    for path, value in fields.items():
        pointer = value.pointer
        if pointer.kind != "doc_span":
            continue
        assert chit_text[pointer.char_start:pointer.char_end] == value.value, path


def test_every_doc_span_in_the_record_carries_its_page_region(scanned_record):
    fields = scanned_record.grounded_fields()
    spans = [(p, v) for p, v in fields.items() if v.pointer.kind == "doc_span"]
    assert spans, "the fixture should produce quoted fields"
    for path, value in spans:
        child = value.pointer.child
        assert child is not None, f"{path} has no scan region"
        assert child.kind == "ocr_bbox"
        assert child.ocr_conf == 0.95
        assert child.media_id == f"{CHIT}:p1"


def test_the_classification_pointer_chains_through_to_the_scan(scanned_record):
    evidence = scanned_record.document_type.pointer.derived_from[0]
    assert evidence.kind == "doc_span"
    assert evidence.child is not None and evidence.child.kind == "ocr_bbox"


def test_the_same_document_without_geometry_is_unchanged_except_for_the_children(chit_text):
    # Pasted text has no page behind it. Apart from the missing children the two records must
    # agree — the media index is an ENRICHMENT, never a second extraction path.
    #
    # With ONE exception, and it is deliberate (R-13): `from_ocr` is also the evidence behind the
    # checkbox control, because a ☐ the engine failed to recognise is indistinguishable from one
    # that was never printed. So the four `CHECKBOX_SENSITIVE` paths are withheld from recognised
    # text and land in `unresolved` instead. Everything else must still match, which is what makes
    # the control narrow rather than "refuse the scan".
    plain = extract_intake(chit_text, document_id=CHIT, harness=gold_harness())
    scanned = extract_intake(chit_text, document_id=CHIT, harness=gold_harness(),
                             media=line_regions(chit_text, CHIT))
    assert set(plain.unresolved) <= set(scanned.unresolved)
    assert set(scanned.unresolved) - set(plain.unresolved) <= CHECKBOX_SENSITIVE
    scanned_fields = scanned.grounded_fields()
    for path, value in plain.grounded_fields().items():
        if path.rsplit("[", 1)[0] in CHECKBOX_SENSITIVE:
            assert path not in scanned_fields, (
                f"{path} grounded off text whose checkbox state cannot be proved complete")
            continue
        other = scanned_fields[path]
        assert value.value == other.value
        assert value.pointer.char_start == other.pointer.char_start
        assert value.pointer.char_end == other.pointer.char_end
        if value.pointer.kind == "doc_span":
            assert value.pointer.child is None and other.pointer.child is not None


# --- extract_upload, on the paths that need no engine -----------------------------
def test_a_text_file_is_taken_verbatim_so_a_paste_and_an_upload_share_pointers(chit_text):
    raw = chit_text.encode("utf-8")
    extraction = extract_upload(raw, document_id=CHIT, filename=CHIT, media_type="text/plain")
    assert extraction.text == chit_text
    assert not extraction.media, "a text file has no page geometry to report"
    assert extraction.engine == "verbatim"


def test_an_eml_is_read_as_text_like_the_fixture_route_serves_it():
    eml = (chits_dir() / "chit_09_coralharbour_hr_email.eml").read_bytes()
    extraction = extract_upload(eml, document_id="chit_09.eml", filename="chit_09.eml",
                                media_type="message/rfc822")
    assert extraction.text == eml.decode("utf-8")


@pytest.mark.parametrize("data,filename", [
    (b"", "empty.txt"),
    (b"MZ\x90\x00binary", "thing.exe"),
    (b"PK\x03\x04zip", "archive.zip"),
])
def test_a_file_we_do_not_read_is_refused_rather_than_decoded_hopefully(data, filename):
    # A PDF decoded as latin-1 is a page of binary noise that the grounding wall then refuses every
    # value against — which reads to the front desk as "this letter says nothing".
    with pytest.raises(UnsupportedUpload):
        extract_upload(data, document_id=filename, filename=filename)


def test_an_oversized_file_is_refused_before_anything_reads_it(monkeypatch):
    from databridge_intake import ocr

    monkeypatch.setattr(ocr, "MAX_UPLOAD_BYTES", 8)
    with pytest.raises(UnsupportedUpload, match="limit"):
        ocr.extract_upload(b"x" * 64, document_id="big.txt", filename="big.txt")
