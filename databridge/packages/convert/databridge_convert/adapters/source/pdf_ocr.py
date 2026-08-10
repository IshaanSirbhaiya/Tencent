"""PDF OCR routing (C13, §11.3) — a single PDF with MIXED text + scanned pages.

`pdf_text` (C12) already flags ``ocr_candidate_pages`` (page with < N extractable chars).
This module is the ROUTE for those pages: it rasterises each flagged page with pypdfium2 and
hands it to ``image_ocr``, while text pages keep their extracted-text blocks. The result is
ONE DocumentRecord with a unified ``page_map`` (the page_map is exactly what makes mixed
text/scanned PDFs work — §11.2) where text pages carry text blocks and scanned pages carry
OCR blocks with ``ocr_bbox`` provenance.

Kept separate from `pdf_text` so the pure-text adapter stays free of the heavy OCR/opencv
deps; this route is opt-in (demos, eval, and the `ocr=True` convert option).
"""
from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np
import pypdfium2 as pdfium
from databridge_core import SourcePointer

from .._docutil import OCR_HINT_CHARS, build_page_map, markdown_serialize
from .._util import AdapterError, snapshot_for
from ...ir import Block, DocumentRecord, MediaRecord
from . import _imageprep, image_ocr
from .ocr_engines import OcrEngine, select_engine

_OCR_HINT_CHARS = OCR_HINT_CHARS   # SHARED with pdf_text (no drift)
_RENDER_SCALE = 3.0           # ~216 DPI — enough for small invoice type
_MAX_PDF_PAGES = 5_000        # page-count cap (F8: raster-bomb / DoS defence)
# a rendered page may not exceed this many pixels (scale·dims); refuse oversized MediaBoxes.
_MAX_RENDER_PIXELS = _imageprep.MAX_IMAGE_PIXELS


def read_pdf_routed(
    data: bytes, *, engine: Optional[OcrEngine] = None, prefer: Optional[str] = None,
    render_scale: float = _RENDER_SCALE,
) -> tuple[DocumentRecord, list[MediaRecord], dict[int, np.ndarray]]:
    """Read a PDF, OCR-routing any page with too little extractable text. Returns the merged
    DocumentRecord, the page rasters' MediaRecords, and {page_no → raster} for bbox resolution."""
    if data[:5] != b"%PDF-":
        raise AdapterError("pdf_ocr: not a PDF (missing %PDF- header)")
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:                              # pragma: no cover - corrupt file
        raise AdapterError(f"pdf_ocr: cannot open PDF: {e}") from e

    source_id = "pdf"
    snapshot = snapshot_for(source_id, data)
    eng = engine or select_engine(prefer)

    blocks: list[Block] = []
    block_provenance: dict[int, SourcePointer] = {}
    page_records: list[tuple[int, int | None, int | None]] = []
    medias: list[MediaRecord] = []
    rasters: dict[int, np.ndarray] = {}
    ocr_pages: list[int] = []
    n_pages = len(pdf)
    if n_pages > _MAX_PDF_PAGES:
        pdf.close()
        raise AdapterError(f"pdf_ocr: {n_pages} pages (>{_MAX_PDF_PAGES}) — refused (raster-bomb)")
    try:
        for pno in range(n_pages):
            page = pdf[pno]
            tp = page.get_textpage()
            text = tp.get_text_bounded()
            tp.close()
            first = len(blocks)
            if len(text.strip()) < _OCR_HINT_CHARS:
                # --- scanned page: rasterise → OCR ---
                ocr_pages.append(pno)
                w_pt, h_pt = page.get_size()
                if int(w_pt * render_scale) * int(h_pt * render_scale) > _MAX_RENDER_PIXELS:
                    page.close(); pdf.close()
                    raise AdapterError(
                        f"pdf_ocr: page {pno} renders to >{_MAX_RENDER_PIXELS}px at scale "
                        f"{render_scale} — oversized MediaBox refused (raster bomb, §21.5)")
                rgb = page.render(scale=render_scale).to_numpy()        # H×W×{3,4} RGB(A)
                bgr = rgb[:, :, 2::-1].copy() if rgb.shape[2] >= 3 else rgb
                pre = _imageprep.preprocess_array(bgr, do_binarise=eng.wants_binarise)
                result = eng.recognize(pre.raster)
                # media sha keyed per page so each page raster has its own identity
                media_sha = hashlib.sha256(pre.raster.tobytes()).hexdigest()
                sub_doc, media = image_ocr.build_document_from_ocr(
                    result, pre.raster, source_id=source_id, page_no=pno,
                    media_sha=media_sha, snapshot=snapshot, skew_deg=pre.skew_deg)
                medias.append(media)
                rasters[pno] = pre.raster
                for i, blk in enumerate(sub_doc.blocks):
                    block_provenance[len(blocks)] = sub_doc.block_provenance[i]
                    blocks.append(blk)
            else:
                # --- text page: reading-order paragraph blocks (as pdf_text) ---
                lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")
                         if ln.strip()]
                for ln in lines:
                    blocks.append(Block(kind="Paragraph", text=ln))
            page.close()
            page_records.append((pno, first, len(blocks) - 1) if len(blocks) > first
                                else (pno, None, None))   # empty page kept (no page lost)
    finally:
        pdf.close()

    _text, char_index = markdown_serialize(blocks)
    page_map = build_page_map(page_records, char_index)
    meta = {
        "page_count": n_pages,
        "ocr_routed_pages": ocr_pages,
        "ocr_engine": eng.name,
        "media": {m.media_id: {"page": m.page_no, "width": m.width, "height": m.height}
                  for m in medias},
        "loss_report": ["mixed text/scanned PDF; scanned pages reconstructed via OCR (§11.3)"],
    }
    doc = DocumentRecord(
        document_id=source_id, sha256=snapshot.content_hash, snapshot=snapshot,
        blocks=blocks, char_index=char_index, page_map=page_map,
        block_provenance=block_provenance, source_meta=meta)
    return doc, medias, rasters


def read(data: bytes) -> DocumentRecord:
    """Registry-compatible entry: OCR-routed PDF read (text pages + OCR'd scanned pages)."""
    doc, _medias, _rasters = read_pdf_routed(data)
    return doc
