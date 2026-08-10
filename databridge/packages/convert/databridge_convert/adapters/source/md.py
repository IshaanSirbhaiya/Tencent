"""Markdown SOURCE adapter — Markdown bytes → DocumentRecord (C12, §5.2).

A lightweight, deterministic block parser (no external markdown lib): fenced code, ATX
headings, pipe tables, bullet lists, horizontal rules, and paragraphs. Near-identity with
the IR — DocumentRecord IS Markdown + blocks."""
from __future__ import annotations

from .._docutil import decode_text, make_document
from ...ir import Block, DocumentRecord


def _parse(text: str) -> list[Block]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # fenced code
        if stripped.startswith("```"):
            lang = stripped[3:].strip() or None
            body, i = [], i + 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1                                       # consume closing fence
            blocks.append(Block(kind="Code", text="\n".join(body), lang=lang))
            continue
        # heading
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            blocks.append(Block(kind="Heading", level=min(level, 6),
                                text=stripped[level:].strip()))
            i += 1
            continue
        # horizontal rule → PageBreak
        if stripped in ("---", "***", "___"):
            blocks.append(Block(kind="PageBreak"))
            i += 1
            continue
        # pipe table
        if stripped.startswith("|"):
            rows, i = [], i
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):    # skip the --- separator
                    rows.append(cells)
                i += 1
            blocks.append(Block(kind="Table", rows=rows))
            continue
        # bullet list
        if stripped[:2] in ("- ", "* ", "+ "):
            items, i = [], i
            while i < n and lines[i].strip()[:2] in ("- ", "* ", "+ "):
                items.append(lines[i].strip())
                i += 1
            blocks.append(Block(kind="ListBlock", text="\n".join(items)))
            continue
        # paragraph: consume consecutive non-blank, non-special lines
        para, i = [], i
        while i < n and lines[i].strip() and not _is_special(lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        blocks.append(Block(kind="Paragraph", text=" ".join(para)))
    return blocks


def _is_special(stripped: str) -> bool:
    return (stripped.startswith(("#", "```", "|"))
            or stripped[:2] in ("- ", "* ", "+ ")
            or stripped in ("---", "***", "___"))


def read(data: bytes) -> DocumentRecord:
    return make_document(_parse(decode_text(data, label="markdown")), data,
                         source_id="markdown")
