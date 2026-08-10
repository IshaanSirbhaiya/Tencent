"""Deterministic pre-extraction — the always-first pass (C15, 09_FINAL_PRD §11.1, P3).

Resolves fields at ZERO tokens BEFORE the LLM runs: Markdown/HTML tables → RowBatch (reusing
C04's adapters), key-value patterns (``Label: value``, definition lists), and per-field regex
``pattern`` hints from the target schema. Typically 30–60% of fields come back free; these are
**LOCKED** — passed to the LLM stage as KNOWN with an instruction not to contradict them, and
carried at confidence 1.0 (lock) through grounding (§11.5).

Every resolved field still carries a ``doc_span`` SourcePointer (P4 — even deterministic
values are grounded), built from the matching block's CharIndex span.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from databridge_core import SourcePointer

from ..ir import Block, DocumentRecord, RowBatch
from .target_model import CompiledTarget, FieldSpec

# "Label: value" / "Label - value" key-value lines and definition lists.
_KV = re.compile(r"^\s*([A-Za-z][\w /&().-]{1,48}?)\s*[:\-–]\s*(.+?)\s*$")


@dataclass
class PreField:
    name: str
    value: Any
    pointer: SourcePointer
    block_idx: int
    via: str                 # "table" | "key_value" | "pattern"


@dataclass
class PreExtractResult:
    locked: dict[str, PreField]              # field name → resolved value (LOCKED)
    table_rows: list[dict]                   # table records found (for repeating records)
    coverage: float                          # fraction of scalar schema fields resolved

    def known_values(self) -> dict[str, Any]:
        return {k: v.value for k, v in self.locked.items()}


def _doc_span(doc: DocumentRecord, block_idx: int) -> SourcePointer:
    start, end = doc.char_index.spans.get(block_idx, (0, 0))
    return SourcePointer(source_id=doc.document_id, kind="doc_span",
                         document_id=doc.document_id, char_start=start, char_end=end)


def _norm_label(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


def _table_blocks_to_rows(doc: DocumentRecord) -> list[dict]:
    """Table blocks in the DocumentRecord → row dicts (first row = header). Reuses the same
    pipe-table semantics as C04's md_tables adapter."""
    rows: list[dict] = []
    for b in doc.blocks:
        if b.kind == "Table" and b.rows and len(b.rows) >= 2:
            header = [str(c).strip() for c in b.rows[0]]
            for r in b.rows[1:]:
                if len(r) == len(header):
                    rows.append(dict(zip(header, (str(c).strip() for c in r))))
    return rows


def rows_from_markdown(data: bytes) -> RowBatch:
    """Markdown table bytes → RowBatch (reuse C04)."""
    from ..adapters import md_tables
    return md_tables.read(data)


def rows_from_html(data: bytes) -> RowBatch:
    from ..adapters import html_tables
    return html_tables.read(data)


def pre_extract(doc: DocumentRecord, compiled: CompiledTarget) -> PreExtractResult:
    """Run the deterministic first pass over a DocumentRecord for the given target schema."""
    locked: dict[str, PreField] = {}
    scalar = compiled.scalar_fields
    # label → field, by exact field name first. Description aliases are added ONLY when they
    # don't collide (a colliding alias is ambiguous → we refuse to lock on it, H1). Exact
    # field-name labels always win over description aliases.
    by_label: dict[str, FieldSpec] = {_norm_label(f.name): f for f in scalar}
    name_labels = set(by_label)
    alias_counts: dict[str, int] = {}
    for f in scalar:
        a = _norm_label(f.description)
        if a and a not in name_labels:
            alias_counts[a] = alias_counts.get(a, 0) + 1
    for f in scalar:
        a = _norm_label(f.description)
        if a and a not in name_labels and alias_counts[a] == 1:    # unambiguous alias only
            by_label.setdefault(a, f)

    # 1) key-value lines / definition lists
    for i, b in enumerate(doc.blocks):
        if b.kind not in ("Paragraph", "Heading", "ListBlock"):
            continue
        for line in b.text.split("\n"):
            m = _KV.match(line)
            if not m:
                continue
            label, value = _norm_label(m.group(1)), m.group(2).strip()
            fs = by_label.get(label)
            if fs is not None and fs.name not in locked and value:
                locked[fs.name] = PreField(name=fs.name, value=value,
                                           pointer=_doc_span(doc, i), block_idx=i, via="key_value")

    # 2) per-field regex pattern hints — match WITHIN a block so the pointer is the block the
    #    value actually came from (H2: never first-match-anywhere with a block-0 fallback).
    for fs in scalar:
        if fs.name in locked or not fs.pattern:
            continue
        for i, b in enumerate(doc.blocks):
            m = re.search(fs.pattern, b.text)
            if m:
                locked[fs.name] = PreField(name=fs.name, value=m.group(0),
                                           pointer=_doc_span(doc, i), block_idx=i, via="pattern")
                break

    table_rows = _table_blocks_to_rows(doc)
    coverage = (len(locked) / len(scalar)) if scalar else 0.0
    return PreExtractResult(locked=locked, table_rows=table_rows, coverage=coverage)
