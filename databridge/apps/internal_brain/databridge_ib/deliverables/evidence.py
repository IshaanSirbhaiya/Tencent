"""The evidence a deliverable may rest on: SOURCE text only, with its row and sentence boundaries.

Reviews applied (docs/finals/deliverables/review_codex.md #1, #2, #13; review_opus.md #2, #6, #16):

* Only the cited document's CURRENT, live-checked, masked snapshot text (``authz.AuthorizedDoc``)
  is evidence. The answer's own sentences are model-written; they go to the model in a separate
  block for orientation and are NEVER checked against.
* Boundaries are kept: a CSV-shaped block becomes rows (parsed with ``csv.reader``, rectangular
  only), everything else becomes sentences. ``narrate.Passage.line`` flattens a document onto one
  line, so it is not used here.
* Lines are filtered the way ``narrate.extractive_answer`` filters them before a model sees them:
  field-header metadata, URL-only lines and text addressed to an AI are dropped; inline URLs are
  removed; citation-shaped tokens are neutralised.
* Each document contributes at most ``EXCERPT_CHARS`` (1,200, as ``/ib/doc``) and the whole
  context at most ``MAX_CONTEXT_CHARS``; the cut happens at unit boundaries, before the evidence
  map is built, so the checker sees exactly what the model saw.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

from .. import narrate
from ..docview import EXCERPT_CHARS
from ..index import STOPWORDS, tokens

MAX_CONTEXT_CHARS = 12_000
MAX_CSV_COLS = 8
MAX_CSV_CELL = 200
_SENT = re.compile(r"(?<=[.!?])\s+")


def nfkc(text: str) -> str:
    """Unicode NFKC (full-width ``＝`` becomes ``=``, a no-break space a space), whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).split())


@dataclass(frozen=True)
class Unit:
    """One checkable piece of a source: a CSV row (``row`` >= 0), the CSV header (``row`` == -1)
    or a prose sentence (``row`` is None)."""
    text: str
    row: Optional[int] = None
    cells: tuple[str, ...] = ()


@dataclass(frozen=True)
class CsvBlock:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Evidence:
    tag: str
    doc_id: str
    version: str
    meta: str                       # "(platform · place · title · vX · date)" from our records
    units: tuple[Unit, ...]
    csv: Optional[CsvBlock] = None

    def text(self) -> str:
        return "\n".join(u.text for u in self.units)


def _clean_line(line: str) -> str:
    s = narrate._URL.sub("", line)                                  # inline links: removed
    return narrate.neutralise_tags(nfkc(s)).strip()


def _keep(line: str) -> bool:
    raw = line.strip()
    return bool(raw) and not (narrate._METADATA.match(raw) or narrate._URL_ONLY.match(raw)
                              or narrate._ADDRESSED_TO_AI.search(raw))


def _split_csv(line: str) -> Optional[list[str]]:
    delim = "\t" if "\t" in line else ","
    if delim not in line:
        return None
    try:
        rows = list(csv.reader(io.StringIO(line), delimiter=delim, skipinitialspace=True))
    except csv.Error:
        return None
    if len(rows) != 1:
        return None
    return [c.strip() for c in rows[0]]


def find_csv_block(lines: Sequence[str]) -> Optional[tuple[int, int, list[list[str]]]]:
    """The first run of >= 3 consecutive lines (a header + >= 2 rows) that ``csv.reader`` parses
    into the same number (2..``MAX_CSV_COLS``) of fields, every header field non-empty and every
    cell at most ``MAX_CSV_CELL`` characters. ``(start, end, rows)`` or None. Prose with commas
    rarely keeps an identical field count over three lines with a non-empty short header; a block
    that does not parse rectangularly is simply not a table (Codex review #13)."""
    parsed = [_split_csv(x) for x in lines]
    i = 0
    while i < len(lines):
        head = parsed[i]
        if not head or not 2 <= len(head) <= MAX_CSV_COLS or not all(head) \
                or any(len(c) > 40 for c in head):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and parsed[j] is not None and len(parsed[j]) == len(head) \
                and all(len(c) <= MAX_CSV_CELL for c in parsed[j]):
            j += 1
        if j - i >= 3:
            return i, j, [list(r) for r in parsed[i:j]]
        i += 1
    return None


def _units(text: str) -> tuple[list[Unit], Optional[CsvBlock]]:
    lines = [_clean_line(x) for x in str(text or "").splitlines() if _keep(x)]
    lines = [x for x in lines if x]
    block = find_csv_block(lines)
    units: list[Unit] = []
    csvb = None
    start = end = -1
    if block:
        start, end, rows = block
        csvb = CsvBlock(tuple(rows[0]), tuple(tuple(r) for r in rows[1:]))
    for i, line in enumerate(lines):
        if start <= i < end:
            r = i - start - 1
            units.append(Unit(line, row=r, cells=tuple(block[2][i - start])))
            continue
        for s in _SENT.split(line):
            s = s.strip()
            if s and not narrate._ADDRESSED_TO_AI.search(s):
                units.append(Unit(s))
    return units, csvb


def _score(unit: Unit, terms: set[str]) -> int:
    toks = set(tokens(unit.text))
    has_number = any(ch.isdigit() for ch in unit.text)
    return 3 * len(terms & toks) + (2 if has_number else 0)


def excerpt(units: Sequence[Unit], terms: set[str], budget: int = EXCERPT_CHARS) -> list[Unit]:
    """At most ``budget`` characters of ``units``, cut at unit boundaries. Everything when it fits;
    else the CSV block first (header and rows in order), then the best-scoring sentences, all
    returned in document order. Deterministic."""
    if sum(len(u.text) + 1 for u in units) <= budget:
        return list(units)
    keep: set[int] = set()
    used = 0
    for i, u in enumerate(units):                       # the table first, whole rows only
        if u.row is not None:
            if used + len(u.text) + 1 > budget:
                break
            keep.add(i)
            used += len(u.text) + 1
    ranked = sorted((i for i, u in enumerate(units) if u.row is None),
                    key=lambda i: (-_score(units[i], terms), i))
    for i in ranked:
        n = len(units[i].text) + 1
        if used + n <= budget:
            keep.add(i)
            used += n
    return [units[i] for i in sorted(keep)]


def build_evidence(cites: Mapping[str, object], *, question: str = "",
                   facts: Iterable[object] = (), max_chars: int = MAX_CONTEXT_CHARS
                   ) -> dict[str, Evidence]:
    """``{tag: Evidence}`` for every openable cited document (``ActionService.context().cites``),
    in tag order. Each document's units are cut to ``EXCERPT_CHARS``; documents after the global
    ``MAX_CONTEXT_CHARS`` (or ``max_chars``) are left out (whole documents, never half a unit)."""
    facts = list(facts)
    out: dict[str, Evidence] = {}
    total = 0
    for tag in sorted(cites, key=lambda t: int(t[1:]) if t[1:].isdigit() else 999):
        snap = cites[tag]
        said = " ".join(getattr(f, "text", "") for f in facts if tag in getattr(f, "tags", ()))
        terms = (set(tokens(question)) | set(tokens(said))) - STOPWORDS
        units, csvb = _units(getattr(snap, "text", ""))
        cut = excerpt(units, terms)
        if not cut:
            continue
        size = sum(len(u.text) + 1 for u in cut)
        if total + size > max_chars:
            break
        total += size
        rows_kept = {u.row for u in cut if u.row is not None}
        if csvb is not None and not {r for r in range(len(csvb.rows))} <= rows_kept:
            # a table cut by the budget keeps only its whole rows that fit
            csvb = CsvBlock(csvb.header, tuple(r for k, r in enumerate(csvb.rows) if k in rows_kept))
        if csvb is not None and -1 not in rows_kept:
            csvb = None
        clean = lambda s: narrate.neutralise_tags(nfkc(s))          # noqa: E731
        meta = (f"({clean(getattr(snap, 'platform', ''))} · {clean(getattr(snap, 'container_name', ''))}"
                f" · {clean(getattr(snap, 'title', ''))} · v{clean(getattr(snap, 'version', ''))}"
                f" · {clean(str(getattr(snap, 'updated_at', ''))[:10])})")
        out[tag] = Evidence(tag=tag, doc_id=str(getattr(snap, "doc_id", "")),
                            version=str(getattr(snap, "version", "")), meta=meta,
                            units=tuple(cut), csv=csvb)
    return out


def context_text(evidence: Mapping[str, Evidence], answer_sentences: Sequence[str]) -> str:
    """What the model is shown: the answer's sentences as an UNTAGGED orientation block (never
    evidence), then each source with its boundaries explicit: one unit per line, prefixed ``| ``."""
    parts = ["What the answer said (orientation only: not a source, never cite it):"]
    parts += [f"- {narrate.neutralise_tags(nfkc(s))}" for s in answer_sentences] or ["- (nothing)"]
    parts.append("")
    parts.append("Sources (each line below is one row or one sentence of that source):")
    for tag, ev in evidence.items():
        parts.append(f"[{tag}] {ev.meta}")
        parts += [f"| {u.text}" for u in ev.units]
    return "\n".join(parts)


__all__ = ["Unit", "CsvBlock", "Evidence", "build_evidence", "context_text", "excerpt",
           "find_csv_block", "nfkc", "EXCERPT_CHARS", "MAX_CONTEXT_CHARS"]
