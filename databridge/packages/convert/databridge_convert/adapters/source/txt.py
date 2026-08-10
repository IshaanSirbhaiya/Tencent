"""Plain-text SOURCE adapter — bytes → DocumentRecord (C12, §5.2). Encoding-sniffed like
CSV; blank-line-separated chunks become Paragraph blocks."""
from __future__ import annotations

from .._docutil import decode_text, make_document
from ...ir import Block, DocumentRecord


def read(data: bytes) -> DocumentRecord:
    text = decode_text(data, label="txt").replace("\r\n", "\n").replace("\r", "\n")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    blocks = [Block(kind="Paragraph", text=" ".join(p.split("\n"))) for p in paras]
    return make_document(blocks, data, source_id="txt")
