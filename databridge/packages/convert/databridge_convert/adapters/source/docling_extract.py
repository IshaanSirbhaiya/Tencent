"""docling-backed extraction: PDF / scanned-image bytes → ONE text plus per-region geometry.

Sits behind the existing document IR rather than beside it (T-36): docling replaces the *parsing*
step, and the result is the same ``DocumentRecord`` + ``markdown_serialize`` char index every other
source adapter produces. Nothing downstream learns a new shape.

**Why a separate module from ``pdf_text``/``pdf_ocr``.** Those two split a PDF by page and
decide per page whether to rasterise. docling does layout + OCR in one pass and — crucially —
reports, for every recognised text line, its page, its box, and whether that line came from the
text layer or from OCR. That last flag is what makes an honest ``ocr_bbox`` pointer possible, and
it is not available through the pypdfium2 route.

**The pointer chain this module exists to make true** (T-36 §3):

    GroundedValue
      └─ pointer: doc_span(document_id, char_start, char_end)      ← this module's `text`
           └─ child: ocr_bbox(media_id, page, x0,y0,x1,y1, ocr_conf)  ← the page region

``char_start``/``char_end`` index the text returned here, so the caller MUST store *this* text as
the document body. If the stored text and the text pointers resolve against ever differ, the
evidence click-through resolves to the wrong characters and the grounding wall starts refusing
values that are genuinely on the page.

**Three deliberate restrictions, each of which a looser design would get wrong:**

1. **We read ``parsed_page.textline_cells``, not the assembled ``DoclingDocument``.** The assembled
   document is grouped and REORDERED by the layout model — on the sample authorisation form it
   hoisted a checkbox line above the letterhead and merged a whole page into one text item whose
   bounding box was the entire page. A page-sized box is a citation that cannot be checked, which
   is worse than none. The textline cells stay in page reading order and each keeps its own box.
2. **An ``ocr_bbox`` pointer is emitted ONLY for a line docling marks ``from_ocr``.** A PDF text
   layer has geometry but no OCR ever ran on it, so there is no OCR confidence to report;
   inventing ``ocr_conf=1.0`` there would be exactly the "value without a source" error in
   miniature. Text-layer lines get a ``doc_span`` and no child, and ``TextRegion.from_ocr`` says so.
3. **A ``MediaRecord`` is emitted only for a page docling actually rasterised.** ``media_id`` names
   a raster; a page that was never rendered has none, and a pointer must not name one.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Optional

from databridge_core import SourcePointer

from .._docutil import build_page_map, markdown_serialize
from .._util import AdapterError, snapshot_for
from ...ir import Block, DocumentRecord, MediaRecord

# Ceilings mirror the raster-bomb defences in `pdf_ocr` (§21.5). docling loads the whole document
# into memory and runs a layout model per page, so an unbounded upload is a trivial DoS.
MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_PAGES = 40

# The formats this module claims. Anything else must be routed elsewhere (plain text needs no
# extraction at all) rather than guessed at — see `is_supported`.
_PDF_TYPES = frozenset({"application/pdf"})
_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/tiff", "image/bmp",
                          "image/webp"})
SUPPORTED_MEDIA_TYPES = _PDF_TYPES | _IMAGE_TYPES

_PDF_MAGIC = b"%PDF-"
# (magic bytes, media type) — sniffed so a mislabelled upload is refused rather than mis-parsed.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


class DoclingUnavailable(RuntimeError):
    """docling is not installed here (``pip install "databridge-convert[docling]"``)."""


@dataclass(frozen=True)
class TextRegion:
    """One recognised line: its span in the extracted text, and where it sits on the page.

    ``ocr_conf`` is the engine's own confidence for the line and is ``None`` when the line came
    from a PDF text layer — there was no recognition step, so there is no confidence to report.
    """

    char_start: int
    char_end: int
    page: int                 # 1-based, as docling numbers pages
    x0: float
    y0: float
    x1: float
    y1: float                 # top-left origin, in the page's own coordinate space
    page_width: float
    page_height: float
    from_ocr: bool
    ocr_conf: Optional[float]
    media_id: Optional[str]   # set iff the page was rasterised (i.e. iff `from_ocr`)

    def to_pointer(self, source_id: str) -> Optional[SourcePointer]:
        """The ``ocr_bbox`` pointer for this region, or ``None`` if it was not OCR'd."""
        if not self.from_ocr or self.ocr_conf is None or self.media_id is None:
            return None
        return SourcePointer(
            source_id=source_id, kind="ocr_bbox", media_id=self.media_id, page=self.page,
            x0=self.x0, y0=self.y0, x1=self.x1, y1=self.y1, ocr_conf=self.ocr_conf)


@dataclass(frozen=True)
class ExtractedDocument:
    """The one extraction. ``text`` is what must be stored; ``regions`` index into it."""

    text: str
    document: DocumentRecord
    regions: tuple[TextRegion, ...]
    media: tuple[MediaRecord, ...]
    page_count: int
    ocr_pages: tuple[int, ...]
    engine: str
    warnings: tuple[str, ...]


def is_supported(media_type: str) -> bool:
    return media_type.split(";")[0].strip().lower() in SUPPORTED_MEDIA_TYPES


def sniff(data: bytes) -> Optional[str]:
    """Media type from magic bytes, or ``None``. The declared type is never trusted on its own."""
    if data[:5] == _PDF_MAGIC:
        return "application/pdf"
    for magic, mtype in _IMAGE_MAGIC:
        if data[:len(magic)] == magic:
            return mtype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # BMP's magic is two ASCII letters, which any number of text documents begin with. The header
    # also declares the file size, so require that to agree — otherwise a referral letter starting
    # "BM" would be routed to the image pipeline and come back as noise.
    if (data[:2] == b"BM" and len(data) >= 6
            and int.from_bytes(data[2:6], "little") == len(data)):
        return "image/bmp"
    return None


# --- the converter, built once ---------------------------------------------------
# docling loads a layout model on first use (~10s). Rebuilding it per upload would put that on
# every request; the counter is a live surface. Guarded because FastAPI runs sync routes in a
# threadpool, so two uploads can race the first construction.
_converter = None
_converter_lock = threading.Lock()


def _build_converter():
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import (DocumentConverter, ImageFormatOption,
                                                PdfFormatOption)
    except ImportError as exc:                                   # pragma: no cover - env-dependent
        raise DoclingUnavailable(
            "docling is not installed; install the `docling` extra of databridge-convert"
        ) from exc

    # `generate_parsed_pages` is what retains `page.parsed_page.textline_cells` after assembly —
    # without it docling discards the per-line geometry and confidences and only the merged,
    # reordered document survives. Everything in restriction (1) above depends on this flag.
    options = PdfPipelineOptions(generate_parsed_pages=True)
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
    })


def converter():
    global _converter
    if _converter is None:
        with _converter_lock:
            if _converter is None:
                _converter = _build_converter()
    return _converter


def available() -> bool:
    """Whether this environment can run docling at all — cheap, no model load."""
    import importlib.util

    return importlib.util.find_spec("docling") is not None


def _media_id(source_id: str, page_no: int) -> str:
    return f"{source_id}:p{page_no}"


def _cells_of(page):
    parsed = getattr(page, "parsed_page", None)
    return list(getattr(parsed, "textline_cells", None) or []) if parsed is not None else []


def _bbox(cell, page_height: float) -> Optional[tuple[float, float, float, float]]:
    """Cell rect → (x0, y0, x1, y1) with a TOP-LEFT origin.

    docling reports either origin depending on the backend — the PDF backend has used BOTTOMLEFT
    and the image backend TOPLEFT — and the two differ by a y-flip about the page. Taking the
    origin on trust produces boxes that resolve to the *mirror image* of the cited line: a pointer
    that looks perfectly well-formed and frames the wrong words. So the flip is explicit, and a
    page whose height we do not know does not get a box at all.
    """
    rect = getattr(cell, "rect", None)
    if rect is None:
        return None
    try:
        box = rect.to_bounding_box()
    except Exception:                                            # pragma: no cover - shape drift
        return None
    origin = str(getattr(getattr(box, "coord_origin", None), "value", "") or "").upper()
    x0, x1 = float(min(box.l, box.r)), float(max(box.l, box.r))
    lo, hi = float(min(box.t, box.b)), float(max(box.t, box.b))
    if origin.startswith("BOTTOM"):
        if page_height <= 0:
            return None
        return x0, page_height - hi, x1, page_height - lo
    return x0, lo, x1, hi


def extract(data: bytes, *, source_id: str, media_type: Optional[str] = None) -> ExtractedDocument:
    """Extract one PDF or image. Raises ``AdapterError`` / ``DoclingUnavailable`` — never guesses.

    The returned ``text`` is the canonical body: store it, and resolve every ``doc_span`` against
    it. ``regions`` carries the geometry needed to hang an ``ocr_bbox`` child off those spans.
    """
    if not data:
        raise AdapterError("docling_extract: empty input")
    if len(data) > MAX_INPUT_BYTES:
        raise AdapterError(
            f"docling_extract: {len(data)} bytes exceeds the {MAX_INPUT_BYTES}-byte ceiling")
    sniffed = sniff(data)
    if sniffed is None:
        raise AdapterError("docling_extract: not a PDF or a supported image (magic bytes)")
    if media_type is not None and is_supported(media_type):
        declared = media_type.split(";")[0].strip().lower()
        # PNG-vs-JPEG disagreement is harmless (docling sniffs too); PDF-vs-image is not, because
        # it means the caller's routing decision was made on a lie.
        if (declared in _PDF_TYPES) != (sniffed in _PDF_TYPES):
            raise AdapterError(
                f"docling_extract: declared {declared!r} but the bytes are {sniffed!r}")

    from docling.datamodel.base_models import DocumentStream

    suffix = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
              "image/tiff": ".tif", "image/bmp": ".bmp", "image/webp": ".webp"}[sniffed]
    stream = DocumentStream(name=f"upload{suffix}", stream=io.BytesIO(data))
    # Building the converter and running it fail for different reasons and deserve different
    # answers: a model that will not load is an unavailable SERVER (the caller should retry or
    # paste the text), while a document the loaded engine cannot parse is a bad FILE. Collapsing
    # the two told a judge with no model cache that their perfectly good PDF was unreadable.
    try:
        engine = converter()
    except DoclingUnavailable:
        raise
    except Exception as exc:                                     # noqa: BLE001
        raise DoclingUnavailable(
            f"the extraction engine could not be started here: {exc}") from exc
    try:
        result = engine.convert(stream, max_num_pages=MAX_PAGES)
    except Exception as exc:                                     # noqa: BLE001
        raise AdapterError(f"docling_extract: docling could not read this file: {exc}") from exc

    status = str(getattr(result.status, "name", result.status)).upper()
    if status not in {"SUCCESS", "PARTIAL_SUCCESS"}:
        raise AdapterError(f"docling_extract: conversion status {status}")

    snapshot = snapshot_for(source_id, data)
    blocks: list[Block] = []
    pending: list[tuple[int, dict]] = []          # (block index, region kwargs) before char spans
    page_records: list[tuple[int, Optional[int], Optional[int]]] = []
    medias: list[MediaRecord] = []
    ocr_pages: list[int] = []

    pages = sorted(result.pages, key=lambda p: p.page_no)
    for page in pages:
        cells = _cells_of(page)
        size = getattr(page, "size", None)
        pw = float(getattr(size, "width", 0.0) or 0.0)
        ph = float(getattr(size, "height", 0.0) or 0.0)
        page_ocr = any(bool(getattr(c, "from_ocr", False)) for c in cells)
        media_id = None
        if page_ocr and pw > 0 and ph > 0:
            # Only a rasterised page has a raster to name. `page.size` for an OCR'd page is the
            # pixel size of the render docling OCR'd, which is the space the boxes live in — so a
            # page whose size we do not know gets no media record and, because `media_id` stays
            # None, no pointer either. A box is only checkable against the frame it sits in.
            ocr_pages.append(page.page_no)
            media_id = _media_id(source_id, page.page_no)
            medias.append(MediaRecord(
                media_id=media_id, sha256=snapshot.content_hash, snapshot=snapshot,
                kind="page_raster", width=int(round(pw)), height=int(round(ph)),
                page_no=page.page_no))
        first = len(blocks)
        for cell in cells:
            text = str(getattr(cell, "text", "") or "")
            if not text.strip():
                continue
            idx = len(blocks)
            blocks.append(Block(kind="Paragraph", text=text))
            box = _bbox(cell, ph)
            if box is None:
                # The TEXT still counts. Dropping the line because its geometry was unreadable
                # would silently delete a sentence from the document to protect a bounding box —
                # so the line stays and simply has no region, which means no `ocr_bbox` child for
                # anything grounded in it.
                continue
            from_ocr = bool(getattr(cell, "from_ocr", False))
            conf = getattr(cell, "confidence", None)
            pending.append((idx, {
                "page": int(page.page_no),
                "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3],
                "page_width": pw, "page_height": ph,
                "from_ocr": from_ocr,
                # Carried, never defaulted: a text-layer line has no OCR confidence, and a cell
                # that reports none must not be given one.
                "ocr_conf": float(conf) if (from_ocr and conf is not None) else None,
                "media_id": media_id,
            }))
        page_records.append((page.page_no, first, len(blocks) - 1) if len(blocks) > first
                            else (page.page_no, None, None))

    text, char_index = markdown_serialize(blocks)
    regions: list[TextRegion] = []
    provenance: dict[int, SourcePointer] = {}
    for idx, kwargs in pending:
        start, end = char_index.spans[idx]
        region = TextRegion(char_start=start, char_end=end, **kwargs)
        regions.append(region)
        pointer = region.to_pointer(source_id)
        if pointer is not None:
            provenance[idx] = pointer

    warnings: list[str] = []
    if not blocks:
        warnings.append("no text was recognised in this file")
    weak = [r.ocr_conf for r in regions if r.ocr_conf is not None and r.ocr_conf < 0.80]
    if weak:
        warnings.append(f"{len(weak)} OCR line(s) below 0.80 confidence "
                        f"(lowest {min(weak):.2f})")

    meta = {
        "page_count": len(pages),
        "ocr_pages": ocr_pages,
        "extraction_engine": "docling",
        "media": {m.media_id: {"page": m.page_no, "width": m.width, "height": m.height}
                  for m in medias},
        "loss_report": [
            "layout geometry reduced to reading-order text lines; figures and rules dropped",
        ],
    }
    if ocr_pages:
        meta["loss_report"].append(
            f"pages {ocr_pages} were reconstructed by OCR; their text is a recognition, "
            "not the document's own characters")

    document = DocumentRecord(
        document_id=source_id, sha256=snapshot.content_hash, snapshot=snapshot,
        blocks=blocks, char_index=char_index,
        page_map=build_page_map(page_records, char_index),
        block_provenance=provenance, source_meta=meta)

    return ExtractedDocument(
        text=text, document=document, regions=tuple(regions), media=tuple(medias),
        page_count=len(pages), ocr_pages=tuple(ocr_pages), engine="docling",
        warnings=tuple(warnings))
