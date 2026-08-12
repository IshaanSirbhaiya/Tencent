"""T-36 — docling extraction, and the honesty of the geometry it reports.

The extraction itself is the easy half. What these tests hold the module to is the part that goes
wrong silently:

* the char spans must index the text the module returns, because that text is what gets stored and
  what every ``doc_span`` pointer resolves against;
* an ``ocr_bbox`` must only appear where OCR actually ran, and must carry the engine's own
  confidence rather than a convenient default;
* a box must land on the page region it claims, not its mirror image — a y-origin flip produces
  pointers that are perfectly well-formed and frame the wrong words.

docling and its layout model are heavy, so the conversions are module-scoped and there are exactly
two of them: one text PDF and one page raster. Where docling is not installed the geometry tests
skip, mirroring the OCR-engine skip pattern in ``test_c13_ocr.py``; the pure-logic tests below run
unconditionally.
"""
from __future__ import annotations

import pathlib

import pytest

from databridge_convert.adapters._util import AdapterError
from databridge_convert.adapters.source import docling_extract

from fixtures.chits.build import (BALLOT_TICKED, BALLOT_UNTICKED, ballot_font_path,
                                  pdf_page_to_png, text_to_pdf)

CHITS = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "hackathon" / "chits"
# The TPA authorisation form: the fixture whose package selection IS a set of ballot boxes, so it
# is the one that proves the checkbox signal survives the container change.
CHIT = "chit_07_mol_authorisation_pee226.txt"

_NEEDS_DOCLING = pytest.mark.skipif(not docling_extract.available(), reason="docling not installed")


@pytest.fixture(scope="module")
def chit_text() -> str:
    return (CHITS / CHIT).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chit_pdf(chit_text) -> bytes:
    font = ballot_font_path()
    if font is None:
        pytest.skip("no font on this machine carries U+2610/U+2612; the fixture would lose the "
                    "checkbox marks it exists to test")
    return text_to_pdf(chit_text, font_path=font)


@pytest.fixture(scope="module")
def extracted_pdf(chit_pdf):
    return docling_extract.extract(chit_pdf, source_id="chit_07.pdf",
                                   media_type="application/pdf")


@pytest.fixture(scope="module")
def extracted_scan(chit_pdf):
    """The same document as a page raster — i.e. what a scanner or a phone would hand us."""
    png = pdf_page_to_png(chit_pdf, page=0)
    return docling_extract.extract(png, source_id="chit_07_scan.png", media_type="image/png")


# --- the invariant everything downstream rests on ---------------------------------
@_NEEDS_DOCLING
def test_every_region_span_indexes_the_text_that_was_returned(extracted_pdf):
    text = extracted_pdf.text
    assert text.strip()
    for region in extracted_pdf.regions:
        assert 0 <= region.char_start < region.char_end <= len(text)
        assert text[region.char_start:region.char_end].strip(), \
            "a region that names an empty slice is a citation to nothing"


@_NEEDS_DOCLING
def test_regions_are_in_reading_order_and_do_not_overlap(extracted_pdf):
    spans = [(r.char_start, r.char_end) for r in extracted_pdf.regions]
    assert spans == sorted(spans)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start, "overlapping regions would make one span resolve to two boxes"


@_NEEDS_DOCLING
def test_the_document_record_char_index_agrees_with_the_returned_text(extracted_pdf):
    # `DocumentRecord.char_index` is what the rest of the IR uses. If it disagreed with `text`,
    # two consumers of the same extraction would resolve the same pointer differently.
    for idx, block in enumerate(extracted_pdf.document.blocks):
        start, end = extracted_pdf.document.char_index.spans[idx]
        assert extracted_pdf.text[start:end] == block.text


# --- the checkbox signal ----------------------------------------------------------
@_NEEDS_DOCLING
def test_checkbox_marks_survive_a_text_pdf(extracted_pdf, chit_text):
    # On this form the ticked box IS the package signal: PEE224, PEE225 and PEE226 are all printed,
    # and only the ballot mark says which examination was authorised. Lose the glyphs and
    # `mask_unticked_options` silently becomes a no-op, so the grounding wall will happily confirm
    # an option the form declined.
    assert extracted_pdf.text.count(BALLOT_TICKED) == chit_text.count(BALLOT_TICKED)
    assert extracted_pdf.text.count(BALLOT_UNTICKED) == chit_text.count(BALLOT_UNTICKED)


# --- what may and may not carry an ocr_bbox ---------------------------------------
@_NEEDS_DOCLING
def test_a_text_layer_page_gets_no_ocr_bbox_and_no_media(extracted_pdf):
    # There was no recognition step, so there is no OCR confidence to report. Emitting
    # `ocr_conf=1.0` here would be a value with no source wearing a source's clothes.
    assert extracted_pdf.ocr_pages == ()
    assert extracted_pdf.media == ()
    assert extracted_pdf.document.block_provenance == {}
    for region in extracted_pdf.regions:
        assert region.from_ocr is False
        assert region.ocr_conf is None
        assert region.media_id is None
        assert region.to_pointer("chit_07.pdf") is None


@_NEEDS_DOCLING
def test_a_scanned_page_produces_ocr_bbox_pointers_carrying_a_real_confidence(extracted_scan):
    assert extracted_scan.ocr_pages, "a page raster has no text layer, so it must be OCR'd"
    pointers = list(extracted_scan.document.block_provenance.values())
    assert pointers, "OCR'd lines must carry provenance"
    media = {m.media_id: m for m in extracted_scan.media}
    for pointer in pointers:
        assert pointer.kind == "ocr_bbox"
        assert pointer.media_id in media, "a pointer must not name a raster we did not record"
        assert 0.0 < pointer.ocr_conf <= 1.0


@_NEEDS_DOCLING
def test_every_ocr_bbox_resolves_to_a_region_of_its_own_page(extracted_scan):
    """The child pointer's whole job is to be checkable against the original."""
    media = {m.media_id: m for m in extracted_scan.media}
    for region in extracted_scan.regions:
        pointer = region.to_pointer("chit_07_scan.png")
        if pointer is None:
            continue
        page = media[pointer.media_id]
        assert pointer.page == page.page_no
        assert 0 <= pointer.x0 < pointer.x1 <= page.width + 1
        assert 0 <= pointer.y0 < pointer.y1 <= page.height + 1


@_NEEDS_DOCLING
def test_the_boxes_are_top_left_origin_and_follow_the_reading_order(extracted_scan):
    # The y-origin is the single thing most likely to be silently wrong: flipped boxes are
    # well-formed, in-bounds, and frame the mirror image of the line they cite. Lines are read
    # down the page, so a top-left origin means y grows as the text advances.
    first, last = None, None
    for region in extracted_scan.regions:
        if not region.from_ocr:
            continue
        first = region if first is None else first
        last = region
    assert first is not None and last is not None
    assert last.y0 > first.y0, "y should increase down the page under a top-left origin"


@_NEEDS_DOCLING
def test_the_scan_and_the_text_pdf_are_different_readings_of_the_same_page(extracted_scan):
    # Not an accuracy claim — just that OCR ran and produced the document's own identifiers, so the
    # downstream grounding tests are exercising a real recognition rather than a text layer that
    # leaked through.
    assert "MOL0199VME" in extracted_scan.text.replace(" ", "")


# --- refusals ---------------------------------------------------------------------
def test_empty_input_is_refused():
    with pytest.raises(AdapterError):
        docling_extract.extract(b"", source_id="x")


def test_an_unrecognised_container_is_refused_rather_than_guessed_at():
    with pytest.raises(AdapterError):
        docling_extract.extract(b"just some bytes", source_id="x")


def test_an_oversized_upload_is_refused_before_docling_is_touched(monkeypatch):
    monkeypatch.setattr(docling_extract, "MAX_INPUT_BYTES", 16)
    monkeypatch.setattr(docling_extract, "converter",
                        lambda: pytest.fail("docling must not be reached"))
    with pytest.raises(AdapterError):
        docling_extract.extract(b"%PDF-" + b"0" * 64, source_id="x")


def test_a_pdf_declared_as_an_image_is_refused():
    # The declared type is what decided the routing upstream. If it disagrees with the bytes, that
    # routing decision was made on a lie and the safe answer is to stop.
    with pytest.raises(AdapterError, match="declared"):
        docling_extract.extract(b"%PDF-1.4\n%%EOF\n", source_id="x", media_type="image/png")


def test_sniffing_beats_the_declared_type():
    assert docling_extract.sniff(b"%PDF-1.7 ...") == "application/pdf"
    assert docling_extract.sniff(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert docling_extract.sniff(b"hello world") is None


# --- the y-flip, without needing docling ------------------------------------------
class _Box:
    def __init__(self, l, t, r, b, origin):                      # noqa: E741
        self.l, self.t, self.r, self.b = l, t, r, b              # noqa: E741
        self.coord_origin = type("O", (), {"value": origin})()


class _Cell:
    def __init__(self, box):
        self.rect = type("R", (), {"to_bounding_box": lambda _self: box})()


def test_a_bottom_left_box_is_flipped_into_top_left_space():
    # docling's PDF backend reports BOTTOMLEFT and its image backend TOPLEFT. Taking the origin on
    # trust puts the box on the mirror-image line.
    top_left = docling_extract._bbox(_Cell(_Box(10, 20, 100, 40, "TOPLEFT")), page_height=800)
    assert top_left == (10.0, 20.0, 100.0, 40.0)

    bottom_left = docling_extract._bbox(_Cell(_Box(10, 780, 100, 760, "BOTTOMLEFT")),
                                        page_height=800)
    assert bottom_left == (10.0, 20.0, 100.0, 40.0)


def test_a_bottom_left_box_on_a_page_of_unknown_height_gets_no_box_at_all():
    # A guess would be a box in the wrong place, which is worse than no box.
    assert docling_extract._bbox(_Cell(_Box(10, 780, 100, 760, "BOTTOMLEFT")),
                                 page_height=0) is None
