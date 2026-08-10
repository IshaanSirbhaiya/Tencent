"""Shared helpers for the document adapters (C12, 09_FINAL_PRD §5.2/§11.1/§13).

Every document adapter parses bytes into a list of ``Block``s and hands them here to build
a ``DocumentRecord`` with provenance + a ``CharIndex`` that maps each block to its character
span in the canonical Markdown serialisation. The Markdown serialiser is the document IR's
**native** form (§5.2) and is shared by the md target adapter and the container-mode path,
so block→char spans are computed exactly once and stay consistent.
"""
from __future__ import annotations

import hashlib
import io
import warnings
import zipfile
from typing import Optional

from ..ir import Block, CharIndex, DocumentRecord, PageSpan
from ._util import AdapterError, snapshot_for

_ENCODINGS = ("utf-8", "utf-8-sig", "latin-1")

# §11.3 needs_ocr threshold: a page with fewer than this many extractable chars (+ raster) is
# an OCR candidate. SHARED by pdf_text (flags candidates) and pdf_ocr (routes them) so the two
# can never disagree about what "scanned" means.
OCR_HINT_CHARS = 8

# Decompression-bomb limits (§21.5) — DOCX/PPTX/XLSX ARE zips. Mirrors the excelsub guard.
_ZIP_MAGIC = b"PK\x03\x04"
_MAX_UNCOMPRESSED = 2 * 1024 ** 3        # 2 GiB expanded
_MAX_RATIO = 200
_MAX_ENTRIES = 50_000


def guard_zip_bomb(data: bytes, *, label: str = "document") -> None:
    """Refuse decompression bombs in OOXML (docx/pptx) zip containers (§21.5). Fast-rejects
    on declared sizes, then ENFORCES a real ceiling by streaming each member (a member that
    lies about its size cannot slip past). Raises AdapterError."""
    if data[:4] != _ZIP_MAGIC:
        return
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > _MAX_ENTRIES:
                raise AdapterError(f"{label}: {len(infos)} zip entries (>{_MAX_ENTRIES}) — "
                                   "decompression bomb")
            declared = sum(i.file_size for i in infos)
            comp = sum(i.compress_size for i in infos) or 1
            if declared > _MAX_UNCOMPRESSED or declared / comp > _MAX_RATIO:
                raise AdapterError(f"{label}: declared expansion {declared}B / ratio "
                                   f"{declared / comp:.0f}x exceeds limits — decompression bomb")
            actual = 0
            for info in infos:
                with z.open(info) as fh:
                    while True:
                        chunk = fh.read(1 << 20)
                        if not chunk:
                            break
                        actual += len(chunk)
                        if actual > _MAX_UNCOMPRESSED:
                            raise AdapterError(
                                f"{label}: actual decompressed bytes exceed "
                                f"{_MAX_UNCOMPRESSED} (member {info.filename} lied) — bomb")
    except zipfile.BadZipFile:
        return


def decode_text(data: bytes, *, label: str = "doc") -> str:
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
            if enc != "utf-8":
                warnings.warn(f"{label}: decoded with fallback encoding {enc}", stacklevel=2)
            return text
        except UnicodeDecodeError:
            continue
    warnings.warn(f"{label}: undecodable bytes — latin-1 with replacement", stacklevel=2)
    return data.decode("latin-1", errors="replace")


def _block_to_md(b: Block) -> str:
    """Serialise ONE block to its canonical Markdown text (no trailing newline)."""
    if b.kind == "Heading":
        return f"{'#' * max(1, min(b.level or 1, 6))} {b.text}"
    if b.kind == "Paragraph":
        return b.text
    if b.kind == "Code":
        lang = b.lang or ""
        return f"```{lang}\n{b.text}\n```"
    if b.kind == "ListBlock":
        # list items are stored newline-separated in text; ensure each is a bullet
        lines = b.text.split("\n") if b.text else []
        return "\n".join(ln if ln.lstrip().startswith(("-", "*", "+")) else f"- {ln}"
                         for ln in lines)
    if b.kind == "Table" and b.rows:
        out = []
        for i, row in enumerate(b.rows):
            out.append("| " + " | ".join(str(c) for c in row) + " |")
            if i == 0:
                out.append("| " + " | ".join("---" for _ in row) + " |")
        return "\n".join(out)
    if b.kind == "Image":
        return f"![]({b.media_id or ''})"
    if b.kind == "PageBreak":
        return "---"
    return b.text


def markdown_serialize(blocks: list[Block]) -> tuple[str, CharIndex]:
    """Blocks → (canonical Markdown text, CharIndex mapping block idx → char span)."""
    parts: list[str] = []
    spans: dict[int, tuple[int, int]] = {}
    cursor = 0
    for i, b in enumerate(blocks):
        md = _block_to_md(b)
        start = cursor
        parts.append(md)
        cursor += len(md)
        spans[i] = (start, cursor)
        if i != len(blocks) - 1:
            parts.append("\n\n")
            cursor += 2
    return "".join(parts), CharIndex(spans=spans)


def build_page_map(page_records: list[tuple[int, Optional[int], Optional[int]]],
                   char_index: CharIndex) -> list[PageSpan]:
    """Build a ``page_map`` covering EVERY page in order — including pages that produced no
    blocks (blank/figure-only scans). ``page_records`` is ``(page_no, first_block, last_block)``
    with ``first/last = None`` for an empty page. An empty page gets a zero-length span at the
    running cursor so page IDENTITY and ORDER survive (a page never silently vanishes from the
    map — the C13 correctness fix). Shared by pdf_text and pdf_ocr (no drift)."""
    page_map: list[PageSpan] = []
    cursor = 0
    for page_no, first, last in page_records:
        if first is None or last is None:
            page_map.append(PageSpan(page=page_no, char_start=cursor, char_end=cursor))
        else:
            start, end = char_index.spans[first][0], char_index.spans[last][1]
            page_map.append(PageSpan(page=page_no, char_start=start, char_end=end))
            cursor = end
    return page_map


def make_document(blocks: list[Block], data: bytes, *, source_id: str,
                  page_map: Optional[list[PageSpan]] = None,
                  source_meta: Optional[dict] = None,
                  loss: Optional[list[str]] = None) -> DocumentRecord:
    """Build a DocumentRecord from parsed blocks. ``loss`` (itemised lossy features) is
    carried in ``source_meta['loss_report']`` so the container path can surface it (§13.1)."""
    _text, char_index = markdown_serialize(blocks)
    meta = dict(source_meta or {})
    if loss:
        meta["loss_report"] = loss
    sha = hashlib.sha256(data).hexdigest()
    return DocumentRecord(
        document_id=source_id,
        sha256=sha,
        snapshot=snapshot_for(source_id, data),
        blocks=blocks,
        char_index=char_index,
        page_map=page_map,
        source_meta=meta,
    )
