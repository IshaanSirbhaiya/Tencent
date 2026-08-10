"""Image / scanned-page OCR SOURCE adapter (C13, 09_FINAL_PRD §11.3, §17.1).

bytes (png/jpg/tiff) → ``DocumentRecord`` whose OCR blocks each carry an ``ocr_bbox``
SourcePointer (media_id, page, x0,y0,x1,y1, ocr_conf). The page raster we OCR is persisted
as a ``MediaRecord`` page, so every bbox **resolves** to the exact region the engine read
(``resolve_bbox`` crops it — this is the D7 "bbox provenance resolvable" guarantee).

Pipeline: preprocess (deskew + binarise, deterministic) → engine.recognize (RapidOCR primary,
Tesseract fallback) → one Block per recognised region + a parallel ``block_provenance`` entry.
Region/word confidence is retained for §11.5 confidence blending.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from databridge_core import SourcePointer

from .._docutil import markdown_serialize
from .._util import AdapterError, snapshot_for
from ...ir import Block, DocumentRecord, MediaRecord
from . import _imageprep
from .ocr_engines import OcrEngine, OcrResult, select_engine


def _media_id(source_id: str, page_no: int) -> str:
    return f"{source_id}:p{page_no}"


def build_document_from_ocr(
    result: OcrResult,
    raster: np.ndarray,
    *,
    source_id: str,
    page_no: int,
    media_sha: str,
    snapshot,
    skew_deg: float = 0.0,
    extra_meta: Optional[dict] = None,
) -> tuple[DocumentRecord, MediaRecord]:
    """Assemble a DocumentRecord (+ its page MediaRecord) from a recognised ``OcrResult``.

    Factored out so the PDF-OCR route and tests (with a stub engine) reuse the exact same
    block→ocr_bbox provenance construction the image path uses."""
    media_id = _media_id(source_id, page_no)
    h, w = raster.shape[:2]
    media = MediaRecord(media_id=media_id, sha256=media_sha, snapshot=snapshot,
                        kind="page_raster", width=int(w), height=int(h), page_no=page_no)

    blocks: list[Block] = []
    provenance: dict[int, SourcePointer] = {}
    confidences: list[float] = []
    for line in result.lines:
        idx = len(blocks)
        blocks.append(Block(kind="Paragraph", text=line.text))
        provenance[idx] = SourcePointer(
            source_id=source_id, kind="ocr_bbox", media_id=media_id, page=page_no,
            x0=line.x0, y0=line.y0, x1=line.x1, y1=line.y1, ocr_conf=line.conf)
        confidences.append(line.conf)

    _text, char_index = markdown_serialize(blocks)
    meta = {
        "ocr_engine": result.engine_name,
        "ocr_skew_deg": skew_deg,
        "ocr_word_confs": [list(l.word_confs) for l in result.lines],
        "media": {media_id: {"page": page_no, "width": int(w), "height": int(h)}},
        "loss_report": [
            f"scanned page → OCR ({result.engine_name}); text reconstructed from raster, "
            "layout/figures dropped (§11.3)",
        ],
    }
    if confidences and min(confidences) < 0.80:
        meta["loss_report"].append(
            f"low-confidence OCR regions present (min conf {min(confidences):.2f}) → §11.5 review")
    if extra_meta:
        meta.update(extra_meta)

    doc = DocumentRecord(
        document_id=source_id, sha256=media_sha, snapshot=snapshot,
        blocks=blocks, char_index=char_index, block_provenance=provenance, source_meta=meta)
    return doc, media


def read_with_artifacts(
    data: bytes, *, engine: Optional[OcrEngine] = None, prefer: Optional[str] = None,
    source_id: str = "image", page_no: int = 0,
    do_deskew: bool = True, do_binarise: Optional[bool] = None,
) -> tuple[DocumentRecord, MediaRecord, np.ndarray]:
    """Full image-OCR read returning the DocumentRecord, its page MediaRecord, and the page
    raster (so callers/demos can ``resolve_bbox`` against it).

    Engine is selected FIRST so preprocessing matches it: ``do_binarise`` defaults to the
    engine's ``wants_binarise`` (deskew always; binarise only where it helps — §11.3)."""
    if not data:
        raise AdapterError("image_ocr: empty input")
    eng = engine or select_engine(prefer)
    binarise = eng.wants_binarise if do_binarise is None else do_binarise
    pre = _imageprep.preprocess(data, do_deskew=do_deskew, do_binarise=binarise)
    result = eng.recognize(pre.raster)
    snapshot = snapshot_for(source_id, data)
    media_sha = snapshot.content_hash
    doc, media = build_document_from_ocr(
        result, pre.raster, source_id=source_id, page_no=page_no, media_sha=media_sha,
        snapshot=snapshot, skew_deg=pre.skew_deg)
    return doc, media, pre.raster


def read(data: bytes) -> DocumentRecord:
    """Registry entry point: image bytes → DocumentRecord (default RapidOCR→Tesseract chain)."""
    doc, _media, _raster = read_with_artifacts(data, source_id="image")
    return doc


# --- bbox resolution (D7: provenance is RESOLVABLE) -----------------------------
def resolve_bbox(pointer: SourcePointer, raster: np.ndarray, *, pad: int = 2) -> np.ndarray:
    """Crop the region a ``ocr_bbox`` pointer references out of its page raster — proof the
    pointer resolves to real pixels. Clamps to image bounds; raises on a non-ocr pointer."""
    if pointer.kind != "ocr_bbox":
        raise AdapterError(f"resolve_bbox: pointer kind {pointer.kind!r} is not ocr_bbox")
    h, w = raster.shape[:2]
    x0 = max(0, int(pointer.x0) - pad)
    y0 = max(0, int(pointer.y0) - pad)
    x1 = min(w, int(pointer.x1) + pad)
    y1 = min(h, int(pointer.y1) + pad)
    if x1 <= x0 or y1 <= y0:
        raise AdapterError(f"resolve_bbox: degenerate box ({x0},{y0},{x1},{y1})")
    return raster[y0:y1, x0:x1]
