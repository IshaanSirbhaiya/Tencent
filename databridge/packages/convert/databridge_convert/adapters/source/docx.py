"""DOCX SOURCE adapter — bytes → DocumentRecord (C12, §5.2).

python-docx, iterating the body in document order so paragraphs and tables keep their
relative position. Tracked changes are read in ACCEPTED state only (warned); images,
styles, headers/footers, and comments are dropped (recorded in the loss report, §13.1)."""
from __future__ import annotations

import io

import docx
from docx.document import Document as _Doc
from docx.oxml.ns import qn
from docx.table import Table as _Table
from docx.text.paragraph import Paragraph as _Para

from .._docutil import guard_zip_bomb, make_document
from ...ir import Block, DocumentRecord
from .._util import AdapterError


def _iter_body(document: _Doc):
    """Yield Paragraph / Table objects in document order (python-docx exposes them in
    separate lists, losing interleaving — walk the XML body to preserve order)."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield _Para(child, document)
        elif child.tag == qn("w:tbl"):
            yield _Table(child, document)


def read(data: bytes) -> DocumentRecord:
    guard_zip_bomb(data, label="docx")                 # §21.5 — docx is a zip
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise AdapterError(f"docx: cannot open document: {e}") from e

    blocks: list[Block] = []
    has_tracked = False
    for item in _iter_body(document):
        if isinstance(item, _Para):
            text = item.text.strip()
            if not text:
                continue
            style = (item.style.name or "") if item.style else ""
            if style.startswith("Heading"):
                level = "".join(c for c in style if c.isdigit())
                blocks.append(Block(kind="Heading", level=int(level) if level else 1,
                                    text=text))
            elif style == "Title":
                blocks.append(Block(kind="Heading", level=1, text=text))
            else:
                blocks.append(Block(kind="Paragraph", text=text))
        elif isinstance(item, _Table):
            rows = [[cell.text.strip() for cell in row.cells] for row in item.rows]
            if rows:
                blocks.append(Block(kind="Table", rows=rows))
    # tracked-changes detection (any <w:ins>/<w:del> in the body)
    body_xml = document.element.body.xml
    if "w:ins" in body_xml or "w:del" in body_xml:
        has_tracked = True

    loss = ["images / embedded objects dropped",
            "styles, headers/footers, comments dropped",
            "page geometry not preserved (semantic reading-order)"]
    if has_tracked:
        loss.insert(0, "tracked changes flattened to ACCEPTED state (§5.2, warned)")
    meta = {"author": (document.core_properties.author or None),
            "has_tracked_changes": has_tracked}
    return make_document(blocks, data, source_id="docx", source_meta=meta, loss=loss)
