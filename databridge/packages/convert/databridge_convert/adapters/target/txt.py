"""Plain-text TARGET adapter — DocumentRecord → bytes (C12, §5.2). Strips Markdown
structure to readable text (headings/paragraphs as lines, tables as tab-joined rows)."""
from __future__ import annotations

from ...ir import DocumentRecord


def write(ir: DocumentRecord) -> bytes:
    out: list[str] = []
    for b in ir.blocks:
        if b.kind == "Table" and b.rows:
            out.extend("\t".join(str(c) for c in row) for row in b.rows)
        elif b.kind == "PageBreak":
            out.append("")
        else:
            out.append(b.text)
        out.append("")
    return ("\n".join(out)).encode("utf-8")
