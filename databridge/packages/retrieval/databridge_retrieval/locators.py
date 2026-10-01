"""Locators — where in the document a chunk sits, in words a person can follow ("p. 4", "slide 7").

Exact when the extraction carried structure; otherwise **approximate and flagged** (principles §3:
``locator`` + ``approx_locator``). Structure is read from, in order:

1. ``meta["locators"]`` — docling page/slide/sheet metadata written beside the capture, a list of
   ``{"kind": "page"|"slide"|"sheet", "label": "4", "char_start": int, "char_end": int}`` over the
   capture ``.txt`` offsets;
2. markers in the extracted text itself: a form feed ``\\f`` or ``<!-- page break -->`` between
   pages (pdftotext / docling ``page_break_placeholder``), ``<!-- page N -->``,
   ``<!-- slide N -->``, ``<!-- sheet: NAME -->``.

With neither, the locator is derived from character offsets — ``approx. p. N`` when the page count
is known, ``approx. chars S–E`` otherwise — and ``approx_locator`` is True. An approximate
locator is never presented as exact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

_KIND_WORD = {"page": ("p.", "pp."), "slide": ("slide", "slides"), "sheet": ("sheet", "sheets")}
_PAGE_BREAK = re.compile(r"\f|<!--\s*page[ _-]?break\s*-->", re.I)
_LABELLED = re.compile(r"<!--\s*(page|slide|sheet)\s*[:#]?\s*([^>]+?)\s*-->", re.I)


@dataclass(frozen=True)
class Region:
    kind: str           # page | slide | sheet
    label: str
    char_start: int
    char_end: int


def regions_from_meta(meta: Optional[dict[str, Any]]) -> list[Region]:
    out: list[Region] = []
    for item in (meta or {}).get("locators") or []:
        try:
            kind = str(item["kind"]).lower()
            if kind not in _KIND_WORD:
                continue
            out.append(Region(kind, str(item["label"]), int(item["char_start"]),
                              int(item["char_end"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda r: r.char_start)


def regions_from_text(text: str) -> list[Region]:
    labelled = [m for m in _LABELLED.finditer(text)
                if not re.fullmatch(r"(?i)break", m.group(2).strip())]
    if labelled:
        out = []
        for i, m in enumerate(labelled):
            end = labelled[i + 1].start() if i + 1 < len(labelled) else len(text)
            out.append(Region(m.group(1).lower(), m.group(2).strip(), m.start(), end))
        return out
    breaks = list(_PAGE_BREAK.finditer(text))
    if not breaks:
        return []
    out, start = [], 0
    for i, m in enumerate(breaks):
        out.append(Region("page", str(i + 1), start, m.start()))
        start = m.end()
    out.append(Region("page", str(len(breaks) + 1), start, len(text)))
    return out


def valid_regions(regions: list[Region], char_count: int) -> bool:
    """Regions are usable as EXACT structure only when each lies inside the text, is non-empty,
    and none overlaps the next (sorted by start). Malformed metadata is not structure."""
    prev_end = 0
    for r in sorted(regions, key=lambda r: r.char_start):
        if not (0 <= r.char_start < r.char_end <= char_count) or r.char_start < prev_end:
            return False
        prev_end = r.char_end
    return True


def _contiguous(regions: list[Region]) -> list[Region]:
    """Text-marker regions leave the marker itself (``\\f``, ``<!-- page break -->``) between two
    pages; it belongs to the page before it, so the cover has no gap a chunk could straddle."""
    out = list(regions)
    for i in range(len(out) - 1):
        if out[i].char_end < out[i + 1].char_start:
            out[i] = Region(out[i].kind, out[i].label, out[i].char_start, out[i + 1].char_start)
    return out


def regions_for(text: str, meta: Optional[dict[str, Any]]) -> list[Region]:
    """Validated structure: metadata regions when they are well-formed, else text markers. A
    malformed set (out of bounds, overlapping, empty) is dropped, never trusted (R-019 r3 #12)."""
    from_meta = regions_from_meta(meta)
    if from_meta and valid_regions(from_meta, len(text)):
        return from_meta
    return _contiguous(regions_from_text(text))


def _covered(start: int, end: int, regions: list[Region]) -> bool:
    """Whether ``[start, end)`` is covered entirely by ``regions`` with no gap."""
    pos = start
    for r in sorted(regions, key=lambda r: r.char_start):
        if r.char_end <= pos:
            continue
        if r.char_start > pos:
            return False
        pos = r.char_end
        if pos >= end:
            return True
    return pos >= end


def locate(start: int, end: int, regions: list[Region], *, char_count: int,
           page_count: Optional[int] = None) -> tuple[str, bool]:
    """``(locator, approx_locator)`` for the span ``[start, end)``. EXACT only when the whole span
    is covered by regions without a gap; a partial cover is never presented as exact."""
    hit = [r for r in regions if r.char_start < max(end, start + 1) and r.char_end > start]
    if hit and _covered(start, max(end, start + 1), hit):
        kind = hit[0].kind
        same = [r for r in hit if r.kind == kind]
        one, many = _KIND_WORD[kind]
        first, last = same[0].label, same[-1].label
        if first == last:
            return f"{one} {first}", False
        return f"{many} {first}–{last}", False
    if page_count and page_count > 0 and char_count > 0:
        p1 = min(page_count, start * page_count // char_count + 1)
        p2 = min(page_count, max(start, end - 1) * page_count // char_count + 1)
        return (f"approx. p. {p1}" if p1 == p2 else f"approx. pp. {p1}–{p2}"), True
    return f"approx. chars {start}–{end}", True
