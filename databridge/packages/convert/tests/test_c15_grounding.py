"""C15 grounding wall — the hardest-tested module (09_FINAL_PRD §11.4).

THE GROUNDING CANARY (EV-DX, §23): a fabricated field value — one that does NOT appear in
source — MUST be caught. This is non-negotiable and is written as the FIRST assertion of the
load-bearing wall: if `test_canary_*` ever passes a fabricated value, the wall is breached and
the whole U→S direction is unsafe."""
from __future__ import annotations

import io

import pytest
from reportlab.pdfgen import canvas

from databridge_convert.adapters.source import pdf_text
from databridge_convert.extract import FieldSpec, GroundingSource, ground_field
from databridge_convert.extract.grounding import CONFUSION_MAP, FUZZY_THRESHOLD, OCR_THRESHOLD


def _doc(lines: list[str]):
    buf = io.BytesIO(); c = canvas.Canvas(buf)
    y = 740
    for ln in lines:
        c.drawString(72, y, ln); y -= 20
    c.showPage(); c.save()
    return pdf_text.read(buf.getvalue())


@pytest.fixture
def invoice_src():
    return GroundingSource(_doc([
        "Invoice Number: INV-2024-0042", "Invoice Date: 2024-03-15",
        "Bill To: Acme Industries", "Total Due: 1350.00"]))


# === THE CANARY (must catch fabrication) ========================================
def test_canary_fabricated_id_is_caught(invoice_src):
    out = ground_field(FieldSpec("invoice_number", "id"), "INV-9999-0000", invoice_src)
    assert out.passed is False and out.pointer is None         # fabrication → no pointer

def test_canary_fabricated_number_is_caught(invoice_src):
    out = ground_field(FieldSpec("total_due", "number"), 9999.99, invoice_src)
    assert out.passed is False

def test_canary_fabricated_name_is_caught(invoice_src):
    out = ground_field(FieldSpec("bill_to", "name"), "Globex Corporation", invoice_src)
    assert out.passed is False and out.score < FUZZY_THRESHOLD

def test_canary_fabricated_date_is_caught(invoice_src):
    out = ground_field(FieldSpec("invoice_date", "date"), "2099-12-31", invoice_src)
    assert out.passed is False


# === real values pass, with a RESOLVABLE pointer ================================
def test_real_id_passes_with_pointer(invoice_src):
    out = ground_field(FieldSpec("invoice_number", "id"), "INV-2024-0042", invoice_src)
    assert out.passed and out.score == 1.0
    assert out.pointer.kind == "doc_span" and out.pointer.char_end > out.pointer.char_start

def test_real_number_exact(invoice_src):
    assert ground_field(FieldSpec("total_due", "number"), 1350.00, invoice_src).passed
    # currency-formatted source token still matches the numeric value
    assert ground_field(FieldSpec("total_due", "number"), 1350, invoice_src).passed

def test_real_date_any_format(invoice_src):
    assert ground_field(FieldSpec("invoice_date", "date"), "2024-03-15", invoice_src).passed
    assert ground_field(FieldSpec("invoice_date", "date"), "March 15, 2024", invoice_src).passed

def test_name_fuzzy_threshold(invoice_src):
    # exact name passes; a near-but-different name must NOT sneak past 0.90
    assert ground_field(FieldSpec("bill_to", "name"), "Acme Industries", invoice_src).passed
    assert not ground_field(FieldSpec("bill_to", "name"), "Acme Holdings", invoice_src).passed


# === OCR-sourced values: confusion-tolerant ≥0.85 + bbox existence ==============
def _ocr_source():
    """A DocumentRecord whose blocks carry ocr_bbox provenance (built via the stub OCR path)."""
    import numpy as np
    from databridge_convert.adapters.source import image_ocr
    from databridge_convert.adapters.source.ocr_engines import OcrLine, OcrResult

    class _Stub:
        name = "stub"; wants_binarise = False
        def available(self): return True
        def recognize(self, image):
            h, w = image.shape[:2]
            return OcrResult(lines=[
                OcrLine(text="Order ID: O0O0-1l1I", x0=10, y0=10, x1=120, y1=30, conf=0.9),
                OcrLine(text="Total: 1350.00", x0=10, y0=40, x1=100, y1=60, conf=0.92)],
                width=w, height=h, engine_name="stub")
    import io
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (300, 100), "white").save(b, "PNG")
    doc, _m, _r = image_ocr.read_with_artifacts(b.getvalue(), engine=_Stub(), source_id="scan")
    return GroundingSource(doc)


def test_ocr_confusion_tolerant_match():
    src = _ocr_source()
    assert src.is_ocr
    # gold "O0O0-1l1I" read as the same with 0/O,1/l confusions → tolerated ≥0.85, bbox present
    out = ground_field(FieldSpec("order_id", "id"), "0000-1111", src)
    assert out.passed and out.score >= OCR_THRESHOLD
    assert out.pointer.kind == "ocr_bbox"            # bbox existence satisfied

def test_ocr_fabrication_still_caught():
    src = _ocr_source()
    out = ground_field(FieldSpec("order_id", "id"), "ZZZZ-9999", src)
    assert out.passed is False


# === adversarial: fragments / collisions must NOT pass (post-review hardening) ===
def test_id_fragment_rejected(invoice_src):
    # a fragment of the real id must NOT ground (substring-containment hole, B2)
    assert not ground_field(FieldSpec("invoice_number", "id"), "2024-0042", invoice_src).passed
    assert not ground_field(FieldSpec("invoice_number", "id"), "INV", invoice_src).passed

def test_enum_fragment_rejected():
    src = GroundingSource(_doc(["Rating: CLASS-A-PRIME"]))
    assert not ground_field(FieldSpec("rating", "enum"), "A", src).passed

def test_name_entity_confusion_rejected():
    # "John Smith" must NOT ground against a DIFFERENT entity "John Smithson" (B1)
    src = GroundingSource(_doc(["Contact: John Smithson"]))
    assert not ground_field(FieldSpec("contact", "name"), "John Smith", src).passed

def test_name_pure_fabrication_rejected(invoice_src):
    assert not ground_field(FieldSpec("bill_to", "name"), "Globex Corporation", invoice_src).passed

def test_number_does_not_collide_with_date_year(invoice_src):
    # fabricated total "2024" must NOT match the year inside the date "2024-03-15" (B3)
    assert not ground_field(FieldSpec("total_due", "number"), 2024, invoice_src).passed

def test_name_embedded_in_prose_still_passes():
    # real value embedded in prose must STILL ground (don't over-correct into false negatives)
    src = GroundingSource(_doc(["Billed to Stark Industries for services rendered."]))
    assert ground_field(FieldSpec("bill_to", "name"), "Stark Industries", src).passed


def test_empty_value_fails():
    src = GroundingSource(_doc(["Invoice Number: INV-1"]))
    assert not ground_field(FieldSpec("x", "text"), "", src).passed
    assert not ground_field(FieldSpec("x", "text"), None, src).passed


def test_confusion_map_is_the_canonical_one():
    # the §11.4 set, no broader (i→1 etc. would risk false matches in grounding)
    assert CONFUSION_MAP == {"O": "0", "o": "0", "l": "1", "I": "1"}
