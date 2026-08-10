"""C13 — OCR routing + image adapter (09_FINAL_PRD §11.3, §17.1).

OCR runs on real engines (RapidOCR primary). Where no engine is available (CI without the
ONNX models / Tesseract binary) the OCR tests SKIP — mirroring the live-PG / live-Ollama
deselect pattern in this repo — but the engine-abstraction, preprocessing, and provenance
construction tests run unconditionally."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from databridge_convert.adapters import documents  # noqa: F401  (registers image/eml adapters)
from databridge_convert.adapters.source import _imageprep, image_ocr
from databridge_convert.adapters.source.ocr_engines import (
    OCR_ENGINES,
    OcrFutureScope,
    OcrLine,
    OcrResult,
    OcrUnavailable,
    select_engine,
)
from databridge_convert.router import REGISTRY


def _engine_available() -> bool:
    try:
        select_engine()
        return True
    except OcrUnavailable:
        return False


_NEEDS_OCR = pytest.mark.skipif(not _engine_available(), reason="no OCR engine (models/binary)")


# --- a stub engine so provenance/preprocessing logic is tested WITHOUT a real model ---
class _StubEngine:
    name = "stub"
    wants_binarise = False

    def available(self) -> bool:
        return True

    def recognize(self, image: np.ndarray) -> OcrResult:
        h, w = image.shape[:2]
        return OcrResult(
            lines=[OcrLine(text="Invoice: INV-1", x0=10, y0=10, x1=100, y1=30, conf=0.97,
                           word_confs=(0.99, 0.95)),
                   OcrLine(text="Total: 42.00", x0=10, y0=40, x1=90, y1=60, conf=0.88)],
            width=w, height=h, engine_name=self.name)


def _png(text_lines: list[str], size=(400, 160)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    y = 20
    for ln in text_lines:
        d.text((20, y), ln, fill="black")
        y += 50
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --- registry ---
def test_image_and_eml_registered():
    assert REGISTRY.get("image").can_read and not REGISTRY.get("image").can_write
    assert REGISTRY.get("eml").can_read


# --- engine abstraction ---
def test_engine_chain_and_future_scope():
    assert OCR_ENGINES["docling"].available() is False
    assert OCR_ENGINES["opendataloader"].available() is False
    with pytest.raises(OcrFutureScope):
        OCR_ENGINES["docling"].recognize(np.zeros((10, 10, 3), np.uint8))


def test_select_unknown_engine_raises():
    with pytest.raises(OcrUnavailable):
        select_engine(prefer="no-such-engine")


# --- preprocessing (no engine needed) ---
def test_deskew_small_angle_recovered():
    img = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(img).text((20, 50), "HELLO WORLD TEXT LINE", fill="black")
    rot = img.rotate(5.0, expand=True, fillcolor="white")
    buf = io.BytesIO(); rot.save(buf, "PNG")
    pre = _imageprep.preprocess(buf.getvalue())
    assert abs(pre.skew_deg) <= 15.0           # bounded; never a 90° flip (the old bug)


def test_deskew_never_flips_clean_horizontal():
    pre = _imageprep.preprocess(_png(["CLEAN HORIZONTAL TEXT"]))
    assert pre.skew_deg == 0.0                 # clean text → no spurious large rotation


def test_load_image_rejects_nonimage():
    from databridge_convert.adapters._util import AdapterError
    with pytest.raises(AdapterError):
        _imageprep.load_image(b"not an image")


# --- provenance construction via the stub (deterministic, no model) ---
def test_ocr_blocks_carry_resolvable_bbox():
    doc, media, raster = image_ocr.read_with_artifacts(
        _png(["Invoice: INV-1", "Total: 42.00"]), engine=_StubEngine(), source_id="scan")
    assert len(doc.blocks) == 2
    for i in range(2):
        ptr = doc.block_provenance[i]
        assert ptr.kind == "ocr_bbox"
        assert ptr.media_id == media.media_id and ptr.page == 0
        assert all(getattr(ptr, f) is not None for f in ("x0", "y0", "x1", "y1", "ocr_conf"))
        crop = image_ocr.resolve_bbox(ptr, raster)        # RESOLVES to real pixels
        assert crop.size > 0
    assert media.kind == "page_raster" and media.page_no == 0


def test_word_confidence_retained():
    doc, _m, _r = image_ocr.read_with_artifacts(
        _png(["Invoice: INV-1"]), engine=_StubEngine(), source_id="scan")
    assert doc.source_meta["ocr_word_confs"][0] == [0.99, 0.95]


def test_low_confidence_flagged_for_review():
    class _LowConf(_StubEngine):
        def recognize(self, image):
            r = super().recognize(image)
            return OcrResult(lines=[OcrLine(text="blur", x0=1, y0=1, x1=9, y1=9, conf=0.55)],
                             width=r.width, height=r.height, engine_name="stub")
    doc, _m, _r = image_ocr.read_with_artifacts(
        _png(["x"]), engine=_LowConf(), source_id="scan")
    assert any("low-confidence" in s for s in doc.source_meta["loss_report"])


def test_resolve_bbox_rejects_wrong_kind():
    from databridge_core import SourcePointer
    from databridge_convert.adapters._util import AdapterError
    ptr = SourcePointer(source_id="x", kind="doc_span", document_id="d", char_start=0, char_end=1)
    with pytest.raises(AdapterError):
        image_ocr.resolve_bbox(ptr, np.zeros((10, 10, 3), np.uint8))


def test_empty_input_refused():
    from databridge_convert.adapters._util import AdapterError
    with pytest.raises(AdapterError):
        image_ocr.read_with_artifacts(b"", engine=_StubEngine())


# --- real-engine smoke (skips without models) ---
@_NEEDS_OCR
def test_real_ocr_reads_text():
    doc = image_ocr.read(_png(["INVOICE 12345"], size=(500, 120)))
    text = " ".join(b.text for b in doc.blocks)
    assert "12345" in text.replace(" ", "")
    assert doc.source_meta["ocr_engine"] in ("rapidocr", "tesseract")


# --- PDF OCR routing: a single PDF with MIXED text + scanned pages ---
def _mixed_pdf() -> bytes:
    """Page 0 = real text; page 1 = an image only (no extractable text → OCR route)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    scan_png = _png(["SCANNED PAGE TEXT"], size=(600, 200))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, "This is a real text page with plenty of extractable characters.")
    c.showPage()
    c.drawImage(ImageReader(io.BytesIO(scan_png)), 72, 500, width=400, height=133)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_pdf_ocr_routes_only_scanned_pages():
    from databridge_convert.adapters.source import pdf_ocr
    doc, medias, rasters = pdf_ocr.read_pdf_routed(_mixed_pdf(), engine=_StubEngine())
    # page 1 was OCR-routed; page 0 kept its extracted text.
    assert doc.source_meta["ocr_routed_pages"] == [1]
    assert len(medias) == 1 and 1 in rasters
    text = " ".join(b.text for b in doc.blocks)
    assert "real text page" in text                 # text page preserved
    assert "Invoice: INV-1" in text                 # OCR page (stub) merged in
    # page_map spans BOTH pages; the OCR page's blocks carry ocr_bbox provenance.
    assert doc.page_map is not None and len(doc.page_map) == 2
    ocr_ptrs = [p for p in doc.block_provenance.values() if p.kind == "ocr_bbox"]
    assert ocr_ptrs and all(p.page == 1 for p in ocr_ptrs)


def test_pdf_ocr_rejects_nonpdf():
    from databridge_convert.adapters._util import AdapterError
    from databridge_convert.adapters.source import pdf_ocr
    with pytest.raises(AdapterError):
        pdf_ocr.read_pdf_routed(b"not a pdf", engine=_StubEngine())


def test_pdf_ocr_empty_scanned_page_kept_in_page_map():
    """A scanned page that OCRs to ZERO lines must NOT vanish from page_map (correctness #1)."""
    from databridge_convert.adapters.source import pdf_ocr

    class _EmptyEngine(_StubEngine):
        def recognize(self, image):
            return OcrResult(lines=[], width=image.shape[1], height=image.shape[0],
                             engine_name="empty")
    doc, _m, _r = pdf_ocr.read_pdf_routed(_mixed_pdf(), engine=_EmptyEngine())
    # 2 pages in, 2 page_map entries out — the empty OCR page is still represented.
    assert doc.page_map is not None and len(doc.page_map) == 2
    assert [ps.page for ps in doc.page_map] == [0, 1]
