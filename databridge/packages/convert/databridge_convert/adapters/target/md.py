"""Markdown TARGET adapter — DocumentRecord → Markdown bytes (C12, §5.2).

Markdown is the document IR's NATIVE serialisation, so this is the reference renderer used
by container-mode conversions (PDF/DOCX/PPTX/HTML → Markdown go through here)."""
from __future__ import annotations

from .._docutil import markdown_serialize
from ...ir import DocumentRecord


def write(ir: DocumentRecord) -> bytes:
    text, _char_index = markdown_serialize(ir.blocks)
    return (text + "\n").encode("utf-8")
