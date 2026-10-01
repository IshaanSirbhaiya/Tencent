"""Chunker — ~1,800 chars, 15% overlap, 120-char minimum (principles §3 starting constants).

Offsets are EXACT: every chunk is ``text[char_start:char_end]`` of the captured text, so a
``doc_span`` pointer built from it slices the capture ``.txt`` back to the chunk (the
anti-hallucination proof). Boundaries prefer a paragraph break, then a line break, then a sentence
end, then whitespace, searched in the last quarter of the window; leading/trailing whitespace is
trimmed by moving the offsets, never by editing the text.

Tabular sources are chunked coarser (``TABULAR_FACTOR`` x) — a row set means little in 1,800
characters, and numbers are answered from the facts store anyway (P6), not from passages.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .locators import Region, locate

CHUNK_CHARS = 1800
OVERLAP_FRACTION = 0.15
MIN_CHUNK_CHARS = 120
TABULAR_FACTOR = 2
#: Bump when the chunking rule changes: stored documents chunked under another version re-index.
CHUNKER_VERSION = "c1-1800-15-120"

_TABULAR_MIMES = ("text/csv", "application/vnd.ms-excel",
                  "application/vnd.openxmlformats-officedocument.spreadsheetml",
                  "text/tab-separated-values")
_TABULAR_EXT = {".csv", ".tsv", ".xls", ".xlsx", ".xlsm"}
_HEADING = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t#]*$")
_BREAKS = ("\n\n", "\n", ". ", "? ", "! ", "; ", " ")


@dataclass(frozen=True)
class Chunk:
    index: int
    char_start: int
    char_end: int
    text: str                   # == source[char_start:char_end]
    section: Optional[str]
    locator: str
    approx_locator: bool
    chunk_kind: str             # "prose" | "table"


def is_tabular(mime: Optional[str], extension: Optional[str]) -> bool:
    m = (mime or "").lower()
    return any(m.startswith(t) for t in _TABULAR_MIMES) or (extension or "").lower() in _TABULAR_EXT


def _trim(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def chunk_spans(text: str, size: int = CHUNK_CHARS, overlap: float = OVERLAP_FRACTION,
                min_chars: int = MIN_CHUNK_CHARS) -> list[tuple[int, int]]:
    """Character spans covering ``text``; adjacent spans overlap by ~``overlap`` of ``size``."""
    n = len(text)
    if n == 0 or not text.strip():
        return []
    step_back = int(size * overlap)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            floor = start + int(size * 0.75)
            for sep in _BREAKS:
                pos = text.rfind(sep, floor, end)
                if pos != -1:
                    end = pos + len(sep)
                    break
        s, e = _trim(text, start, end)
        if e > s:
            spans.append((s, e))
        if end >= n:
            break
        nxt = max(end - step_back, start + 1)
        # start the overlap on a word boundary rather than mid-word
        ws = re.search(r"\s", text[nxt:min(nxt + 60, end)])
        if ws:
            nxt += ws.end()
        start = nxt
    # a trailing fragment shorter than the minimum folds into its predecessor
    if len(spans) >= 2 and spans[-1][1] - spans[-1][0] < min_chars:
        last = spans.pop()
        spans[-1] = (spans[-1][0], max(spans[-1][1], last[1]))
    return spans


def _section_at(headings: list[tuple[int, str]], pos: int) -> Optional[str]:
    current = None
    for off, title in headings:
        if off <= pos:
            current = title
        else:
            break
    return current


def chunk_document(text: str, *, regions: list[Region], page_count: Optional[int] = None,
                   tabular: bool = False) -> list[Chunk]:
    size = CHUNK_CHARS * (TABULAR_FACTOR if tabular else 1)
    headings = [(m.start(), m.group(1).strip()) for m in _HEADING.finditer(text)]
    out: list[Chunk] = []
    for i, (s, e) in enumerate(chunk_spans(text, size=size)):
        locator, approx = locate(s, e, regions, char_count=len(text), page_count=page_count)
        out.append(Chunk(index=i, char_start=s, char_end=e, text=text[s:e],
                         section=_section_at(headings, s), locator=locator,
                         approx_locator=approx, chunk_kind="table" if tabular else "prose"))
    return out
