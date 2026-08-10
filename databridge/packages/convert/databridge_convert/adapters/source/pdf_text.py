"""PDF (text) SOURCE adapter — bytes → DocumentRecord (C12, §5.2).

pypdfium2 extracts text per page in reading order; each non-empty line becomes a Paragraph
block and ``page_map`` (a PageSpan list) records each page's char span in the canonical
Markdown — so a record's pointers can span pages (§11.2). TEXT PDFs only; a page with no
extractable text is flagged for OCR routing (C13), not silently emptied (§11.3)."""
from __future__ import annotations

import pypdfium2 as pdfium

from .._docutil import OCR_HINT_CHARS, build_page_map, make_document, markdown_serialize
from ...ir import Block, DocumentRecord
from .._util import AdapterError

# A page with fewer than this many extractable chars + a raster present ⇒ likely scanned.
_OCR_HINT_CHARS = OCR_HINT_CHARS


def read(data: bytes) -> DocumentRecord:
    if data[:5] != b"%PDF-":
        raise AdapterError("pdf_text: not a PDF (missing %PDF- header)")
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as e:                              # pragma: no cover - corrupt file
        raise AdapterError(f"pdf_text: cannot open PDF: {e}") from e

    n_pages = len(pdf)                                   # true page total (M1: count up front)
    blocks: list[Block] = []
    # one record PER PAGE in order (first/last None ⇒ page produced no blocks) so no page is
    # ever dropped from page_map (C13 correctness fix; shared builder).
    page_records: list[tuple[int, int | None, int | None]] = []
    ocr_pages: list[int] = []
    try:
        for pno, page in enumerate(pdf):
            tp = page.get_textpage()
            text = tp.get_text_bounded()
            tp.close()
            page.close()
            lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")
                     if ln.strip()]
            if len(text.strip()) < _OCR_HINT_CHARS:
                ocr_pages.append(pno)
            first = len(blocks)
            for ln in lines:
                blocks.append(Block(kind="Paragraph", text=ln))
            page_records.append((pno, first, len(blocks) - 1) if len(blocks) > first
                                else (pno, None, None))
    finally:
        pdf.close()

    # page_map: each page's char span in the canonical Markdown serialisation.
    _text, char_index = markdown_serialize(blocks)
    page_map = build_page_map(page_records, char_index)

    loss = ["PDF layout geometry simplified to reading-order text blocks (§13.1)",
            "images / form fields / annotations dropped (text-only extraction)"]
    if ocr_pages:
        loss.append(f"pages with no extractable text → OCR routing needed (C13): {ocr_pages}")
    meta = {"page_count": n_pages, "ocr_candidate_pages": ocr_pages}
    return make_document(blocks, data, source_id="pdf", page_map=page_map,
                         source_meta=meta, loss=loss)
