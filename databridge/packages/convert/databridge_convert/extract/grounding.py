"""Grounding validator — THE LOAD-BEARING WALL (C15, 09_FINAL_PRD §11.4).

**Every extracted value must appear in the source. No value bypasses grounding. Ever.**
An extracted value absent from the source text was invented — this single deterministic check
converts the dominant LLM-extraction failure mode (plausible fabrication) into a visible,
countable event (05_PRD §4.1). This module is the canonical example for the
``databridge-provenance-check`` skill.

Match rules by data type (§11.4):
  * ids / numbers / dates → EXACT after canonical formatting;
  * names / free text     → fuzzy token-set ratio ≥ 0.90;
  * OCR-sourced values    → confusion-tolerant compare (0/O, 1/l) ≥ 0.85 PLUS bbox existence.
Fail ⇒ optional field nulled+flagged · required field ⇒ record quarantined (handled by the
caller via ``GroundingOutcome.passed`` + ``FieldSpec.required``).

On success the value gets a RESOLVABLE ``SourcePointer`` (doc_span into the source IR, or the
block's ``ocr_bbox`` for OCR sources) — provenance is a wrapper type, not a side log (P4).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from dateutil import parser as _dateparser
from rapidfuzz import fuzz

from databridge_core import GroundedValue, SourcePointer

from ..ir import DocumentRecord
from .target_model import FieldSpec, GroundingKind

# §11.4 thresholds.
FUZZY_THRESHOLD = 0.90               # names / free text (token-set ratio)
OCR_THRESHOLD = 0.85                 # OCR-sourced values (confusion-tolerant)

# The CANONICAL confusion map (§11.4: 0/O and 1/l). Defined HERE (shipped code) and imported by
# EV-OCR so the eval's tolerant compare and this grounding wall are provably the SAME function.
CONFUSION_MAP = {"O": "0", "o": "0", "l": "1", "I": "1"}
_CONFUSE = str.maketrans(CONFUSION_MAP)


# --- the source view over a DocumentRecord -------------------------------------
@dataclass
class _Block:
    idx: int
    text: str
    char_start: int
    char_end: int
    ocr_pointer: Optional[SourcePointer]      # set iff this block came from OCR (ocr_bbox)
    block_pointer: Optional[SourcePointer] = None  # the block's INTRINSIC provenance (any kind)


class GroundingSource:
    """A searchable view of a DocumentRecord: per-block text + char span + intrinsic pointer.

    Char spans come from the SAME CharIndex the markdown serialiser produced, so a doc_span
    pointer built here resolves against the canonical text (§17.2). ``block_pointer`` is the
    block's own resolvable pointer when the adapter recorded one (``ocr_bbox`` for OCR,
    ``workbook_cell`` for an Excel-irregular KV sheet, §14.19/§17) — when present it is emitted
    on a match in preference to a synthesised doc_span, so a grounded value resolves to its most
    specific source location (P4). OCR confusion-tolerance stays keyed on ``ocr_pointer`` only."""

    def __init__(self, doc: DocumentRecord):
        self.doc = doc
        self.blocks: list[_Block] = []
        for i, b in enumerate(doc.blocks):
            span = doc.char_index.spans.get(i, (0, 0))
            ptr = doc.block_provenance.get(i)
            ocr = ptr if (ptr is not None and ptr.kind == "ocr_bbox") else None
            self.blocks.append(_Block(idx=i, text=b.text, char_start=span[0],
                                      char_end=span[1], ocr_pointer=ocr, block_pointer=ptr))

    @property
    def is_ocr(self) -> bool:
        return any(b.ocr_pointer is not None for b in self.blocks)


# --- canonicalisation ----------------------------------------------------------
def _canon_id(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).upper()


def _to_decimal(s: str) -> Optional[Decimal]:
    """Parse a money/number token: strip currency symbols, thousands separators."""
    t = re.sub(r"[^\d.\-]", "", str(s).replace(",", ""))
    if t in ("", "-", ".", "-."):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


_NUM_TOKEN = re.compile(r"-?[\d,]*\.?\d+")
_DATE_TOKEN = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}")
_WORD = re.compile(r"[A-Za-z0-9]+")


def _numbers_in(text: str) -> list[Decimal]:
    # strip date tokens FIRST so a fabricated number can't collide with a date's year/parts
    # (e.g. total "2024" matching the year in "2024-03-15") — B3 mitigation.
    text = _DATE_TOKEN.sub(" ", text)
    out = []
    for m in _NUM_TOKEN.findall(text):
        d = _to_decimal(m)
        if d is not None:
            out.append(d)
    return out


def _word_tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def _id_tokens(text: str) -> list[str]:
    """Split a block into VALUE-like tokens (whitespace + label punctuation), KEEPING internal
    hyphens/dots so an id like INV-2024-0042 stays one token. Used for exact token-equality
    matching (not substring containment)."""
    return [t for t in re.split(r"[\s:;,|]+", text.strip()) if t]


def _dates_in(text: str) -> list[_dt.date]:
    out = []
    for m in _DATE_TOKEN.findall(text):
        try:
            out.append(_dateparser.parse(m, dayfirst=False, fuzzy=False).date())
        except (ValueError, OverflowError):
            continue
    return out


def _as_date(value: Any) -> Optional[_dt.date]:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dateparser.parse(str(value), fuzzy=False).date()
    except (ValueError, OverflowError):
        return None


# --- per-kind matching: returns (score, matched_block) -------------------------
def _name_text_score(value: str, text: str) -> float:
    """Names/free text: the score is the MEAN over the value's tokens of each token's best match
    to any source token. This is high only when EVERY value token is present in the source — so:
      * a real value embedded in prose scores ~1.0 ('Oscorp' in '...prepared for Oscorp.'), and
      * a fabrication with a divergent token is dragged below threshold ('John Smith' vs source
        'John Smithson' → smith≉smithson → ~0.88; 'Globex' vs 'Acme' → ~0.3).
    Unlike a whole-block token-set/`partial_ratio` it is neither diluted by surrounding prose
    (no false negatives) nor fooled by a substring/fragment of real content (the B1 hole)."""
    vtoks = _word_tokens(value)
    stoks = _word_tokens(text)
    if not vtoks or not stoks:
        return 0.0
    per_token = [max(fuzz.ratio(vt, st) / 100.0 for st in stoks) for vt in vtoks]
    return sum(per_token) / len(per_token)


def _match_block(value: Any, kind: GroundingKind, block: _Block, *, ocr: bool) -> float:
    """Per-data-type match of ``value`` against one block (§11.4). For OCR sources both sides
    are confusion-collapsed (0/O, 1/l) FIRST, then the SAME per-type rule applies. Matching is
    IDENTITY against the block's tokens — never substring containment of the whole block (which
    would let a fragment of, or digit-collision with, real content pass; see B1/B2/B3)."""
    text = block.text
    vstr = str(value)
    if ocr:
        text = text.translate(_CONFUSE)
        vstr = vstr.translate(_CONFUSE)
    if kind in ("name", "text"):
        return _name_text_score(vstr, text)
    if kind == "number":
        d = _to_decimal(value)
        if d is None:
            return 0.0
        return 1.0 if any(n == d for n in _numbers_in(text)) else 0.0
    if kind == "date":
        dv = _as_date(value)
        if dv is None:
            return 0.0
        return 1.0 if dv in _dates_in(text) else 0.0
    if kind == "bool":
        # match the SPECIFIC literal word (not the whole truthy class, and NOT 0/1 digits which
        # collide with years/quantities) — M6.
        v = str(value).strip().lower()
        want = {"true", "yes"} if v in ("true", "yes", "y", "1") else {"false", "no"}
        toks = set(_word_tokens(text))
        return 1.0 if toks & want else 0.0
    # id / enum: EXACT token equality after canonicalisation (NOT substring of the block) —
    # a fragment like "2024-0042" of "INV-2024-0042", or enum "A" of "CLASS-A", must NOT pass.
    cv = _canon_id(vstr)
    return 1.0 if cv and any(cv == _canon_id(tok) for tok in _id_tokens(text)) else 0.0


def _threshold(kind: GroundingKind, ocr: bool) -> float:
    # fuzzy kinds: 0.85 OCR (confusion-tolerant) / 0.90 clean. exact kinds (id/number/date/
    # enum/bool) must match exactly — even on OCR they match exact-after-confusion-collapse.
    if kind in ("name", "text"):
        return OCR_THRESHOLD if ocr else FUZZY_THRESHOLD
    return 1.0


# --- public API ----------------------------------------------------------------
@dataclass
class GroundingOutcome:
    field: str
    value: Any
    passed: bool
    score: float
    pointer: Optional[SourcePointer] = None
    matched_block: Optional[int] = None
    reason: str = ""

    def grounded_value(self, confidence: float) -> Optional[GroundedValue]:
        if not self.passed or self.pointer is None:
            return None
        return GroundedValue(value=self.value, pointer=self.pointer, confidence=confidence)


def ground_field(spec: FieldSpec, value: Any, source: GroundingSource) -> GroundingOutcome:
    """Ground ONE scalar value against the source. The wall: no pass ⇒ no pointer ⇒ the caller
    nulls (optional) or quarantines (required)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return GroundingOutcome(field=spec.name, value=value, passed=False, score=0.0,
                                reason="empty/None value")
    thresh = _threshold(spec.grounding, source.is_ocr)
    best_score, best_block = 0.0, None
    for b in source.blocks:
        if not b.text.strip():
            continue
        s = _match_block(value, spec.grounding, b, ocr=b.ocr_pointer is not None)
        if s > best_score:
            best_score, best_block = s, b
    if best_block is None or best_score < thresh:
        return GroundingOutcome(field=spec.name, value=value, passed=False, score=best_score,
                                matched_block=best_block.idx if best_block else None,
                                reason=f"no source match (best {best_score:.2f} < {thresh:.2f}) "
                                       f"— possible fabrication")
    # build the resolvable pointer: the block's INTRINSIC pointer when the adapter recorded one
    # (ocr_bbox / workbook_cell — bbox/cell existence satisfied), else a synthesised doc_span.
    if best_block.block_pointer is not None:
        ptr = best_block.block_pointer
    else:
        ptr = SourcePointer(source_id=source.doc.document_id, kind="doc_span",
                            document_id=source.doc.document_id,
                            char_start=best_block.char_start, char_end=best_block.char_end)
    return GroundingOutcome(field=spec.name, value=value, passed=True, score=best_score,
                            pointer=ptr, matched_block=best_block.idx)
