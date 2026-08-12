"""The grounding wall — every extracted value must be findable in the source, or it is dropped.

This is the load-bearing anti-hallucination check and the thing the Innovation and Governance
criteria actually rest on. An LLM reading a referral letter will occasionally produce a plausible
policy number that is not on the page; a plausible fabrication is the dominant failure mode of
document extraction. This module converts that failure from an invisible wrong answer into a
visible, countable, refused event.

**The rule: the model controls syntax, the source controls semantics.** The extractor decides what
*shape* the answer has; nothing it says is believed until the value has been located in the
document text. A value that cannot be located does not become a low-confidence value — it is
removed from the record and its field path is listed in ``unresolved``.

Matching rules, adapted from ``packages/convert/databridge_convert/extract/grounding.py``:

  * ids / numbers / dates → exact after canonicalisation, compared against whole *tokens*
  * names                 → EXACT, contiguous tokens. No fuzzy tolerance at all.
  * free text             → per-token fuzzy, mean >= 0.90 with no token below 0.85

Three traps, each of which looks like a reasonable shortcut and is not:

1. **Never ground by substring containment.** ``"man" in "management"`` is true, so a fabricated
   value that is a fragment of real text would pass. Everything here matches whole tokens on
   word boundaries.
2. **Never score with a whole-string token-set ratio.** It returns a perfect score when one side's
   tokens are a subset of the other's, so any fragment of the document validates. (The same
   mistake, caught in review, is documented in ``rules.py``.)
3. **Never allow fuzzy matching on a person's name.** Even a careful per-token rule accepts
   "Lim Arjuna" against a voucher reading "Lim Arjun" — the mean is 0.96 and no single token is
   weak. But those are two different people, and this registry holds 300 patients with heavily
   overlapping name particles (Min/Ming, Wei/Wai, Tan/Tang). Names are matched exactly; the
   extractor is told to copy verbatim, so that costs nothing real.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional

from dateutil import parser as _dateparser
from rapidfuzz import fuzz

from databridge_core import GroundedValue, SourcePointer

# Kind of value being grounded — drives which comparison rule applies.
GroundKind = Literal["id", "number", "date", "name", "text"]

# Names / free text. Same 0.90 bar as the convert grounding wall (there on a 0-1 scale).
FUZZY_THRESHOLD = 0.90
# No SINGLE token of a value may be weaker than this, however good the average. See `_passes`.
TOKEN_FLOOR = 0.85
# Tokens this short must match exactly — see `_token_score`.
SHORT_TOKEN_LEN = 4

_WORD = re.compile(r"[A-Za-z0-9]+")
_NUM_TOKEN = re.compile(r"-?[\d,]*\.?\d+")
# Spaces are allowed AROUND the separators because the sample voucher literally prints its
# appointment date as "30 / 07 / 2026" in a form box.
_DATE_TOKEN = re.compile(
    r"\d{1,4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}")


# --- canonicalisation -------------------------------------------------------------
def canonicalise_id(s: str) -> str:
    """Collapse whitespace and case so 'S 8010946 C' and 's8010946c' compare equal.

    Public because the grounded value records the document's own spacing verbatim, so any
    downstream check on an identifier's *shape* has to canonicalise first or it will reject the
    very spaced-out forms this function exists to accommodate.
    """
    return re.sub(r"\s+", "", str(s)).upper()


_canon_id = canonicalise_id      # internal alias


def _to_decimal(s: str) -> Optional[Decimal]:
    t = re.sub(r"[^\d.\-]", "", str(s).replace(",", ""))
    if t in ("", "-", ".", "-."):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _word_tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def _value_tokens(text: str) -> list[str]:
    """Split into value-like tokens, keeping internal hyphens/dots/hashes so an identifier such as
    ``MRD707314`` or ``BP#HIV`` survives as one token."""
    return [t for t in re.split(r"[\s:;,|()]+", text.strip()) if t]


def _as_date(value: Any) -> Optional[dt.date]:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return _dateparser.parse(str(value), dayfirst=True, fuzzy=False).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _dates_in(text: str) -> list[dt.date]:
    out: list[dt.date] = []
    for m in _DATE_TOKEN.findall(text):
        try:
            out.append(_dateparser.parse(m, dayfirst=True, fuzzy=False).date())
        except (ValueError, OverflowError):
            continue
    return out


def _numeric_token_spans(text: str) -> list[tuple[int, int, Decimal]]:
    """Every numeric token with its offsets and parsed value.

    Date tokens are blanked (not removed) first, so offsets stay aligned with the original text
    while a fabricated quantity still cannot collide with a year or a day number.
    """
    masked = _DATE_TOKEN.sub(lambda m: " " * len(m.group()), text)
    out: list[tuple[int, int, Decimal]] = []
    for m in _NUM_TOKEN.finditer(masked):
        d = _to_decimal(m.group())
        if d is not None:
            out.append((m.start(), m.end(), d))
    return out


def _find_span_ignoring_whitespace(text: str, canon_needle: str) -> Optional[tuple[int, int]]:
    """Find ``canon_needle`` (already whitespace-stripped, upper-cased) in a whitespace-collapsed
    view of ``text``, and map the hit back to real offsets.

    Forms print identifiers with internal spacing — "NRIC : S 8010946 C" is the same NRIC as
    "S8010946C". Without this the wall refuses a value that is plainly on the page, which is a
    false refusal: the front desk is told "not on document" about something clearly printed.
    """
    if not canon_needle:
        return None
    idx_map: list[int] = []
    collapsed_chars: list[str] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            collapsed_chars.append(ch.upper())
            idx_map.append(i)
    collapsed = "".join(collapsed_chars)
    pos = collapsed.find(canon_needle)
    if pos < 0:
        return None
    # Reject a hit that is a fragment of a longer alphanumeric run (the containment trap again).
    before_ok = pos == 0 or not collapsed[pos - 1].isalnum()
    after = pos + len(canon_needle)
    after_ok = after >= len(collapsed) or not collapsed[after].isalnum()
    if not (before_ok and after_ok):
        return None
    return idx_map[pos], idx_map[after - 1] + 1


# Ballot boxes as printed on the authorisation forms.
TICKED_BOX = "☒"       # ☒
UNTICKED_BOX = "☐"     # ☐

# Media types whose text can only have come out of a RECOGNITION step, or might have. An image
# always did. A PDF may carry its own text layer instead — but from the media type alone the two
# are indistinguishable, and the distinction is exactly what this control turns on, so a PDF with
# no region index is treated as unproven rather than assumed to be a text layer.
_RECOGNISED_MEDIA_PREFIXES = ("image/",)
_RECOGNISED_MEDIA_TYPES = frozenset({"application/pdf"})


def _may_be_recognised(media_type: str) -> bool:
    mt = (media_type or "").split(";")[0].strip().lower()
    return bool(mt) and (mt in _RECOGNISED_MEDIA_TYPES
                         or any(mt.startswith(p) for p in _RECOGNISED_MEDIA_PREFIXES))


def _line_bounds(text: str) -> list[tuple[int, int]]:
    """(start, end) of each line's CONTENT — the terminator is excluded, offsets are absolute."""
    bounds, cursor = [], 0
    for line in text.splitlines(keepends=True):
        content = len(line.rstrip("\r\n"))
        bounds.append((cursor, cursor + content))
        cursor += len(line)
    return bounds


def unprovable_checkbox_spans(text: str, media: Any = None,
                              media_type: str = "text/plain") -> tuple[tuple[int, int], ...]:
    """Character ranges of ``text`` whose checkbox state cannot be **proved** complete (R-13).

    **What "provably complete" means here, and why it is this and not something looser.**

    ``mask_unticked_options`` is only a control while the ``☐`` is still on the page. If the glyph
    is lost, the option text survives, the masking silently becomes a no-op, and a test the
    patient DECLINED can be grounded, confirmed and billed. So the question is not "did we see a
    checkbox?" but "can we prove we saw *every* checkbox?".

    For recognised text that proof does not exist, and cannot be manufactured. The evidence of a
    missing checkbox is precisely its absence: a line reading ``WELL1 — Essential Screen`` looks
    identical whether the form printed no box beside it or the engine failed to recognise the box
    it did print. Recovering a ``☒`` somewhere else on the page is not evidence about *this* line —
    per-line recognition succeeds and fails independently — which is why "we saw at least one box"
    is not a control, and why the document-wide warning this replaces was not one either.

    The proof that *does* exist is about ORIGIN, not about glyphs: where the characters were read
    rather than recognised — a text file taken verbatim, or a PDF's own text layer — the character
    stream is the document's own, so a ``☐`` that is not there was never printed. That is the only
    complete-recovery proof available, so it is the one encoded:

      * region index reports OCR-recognised lines → **those lines** are unprovable (per line, so a
        mixed PDF keeps its text-layer pages and a partially recognised page fails closed);
      * region index reports only text-layer lines → provable, nothing is withheld;
      * **no region index at all, but a media type that recognition could have produced** →
        unprovable, whole document. This is the fail-closed default for a caller that extracted a
        scan and did not thread ``media`` through: forgetting must not silently restore the
        permissive behaviour, because no test anywhere would notice;
      * no region index, and a media type recognition never touches (pasted text, ``.txt``,
        ``.eml``) → provable, nothing is withheld.

    There is deliberately no branch in which recognised text is treated as provably complete. If an
    engine ever reported per-glyph recall — "this line was searched for a ballot box and none was
    printed" — that is where it would go, and it would be a change to this function alone.
    """
    regions = tuple(getattr(media, "regions", ()) or ())
    recognised = [r for r in regions
                  if getattr(r, "from_ocr", False)
                  and getattr(r, "char_start", None) is not None
                  and getattr(r, "char_end", None) is not None]
    if recognised:
        # A whole LINE, not the region's own span: a value can sit in the gap between two
        # recognised cells (a form's label/value columns are separate cells), and a control that
        # only blanked the cells would leave exactly those values groundable.
        covered = bytearray(len(text))
        for r in recognised:
            start = max(0, min(int(r.char_start), len(text)))
            end = max(start + 1, min(int(r.char_end), len(text)))
            covered[start:end] = b"\x01" * (end - start)
        return tuple((s, e) for s, e in _line_bounds(text)
                     if s < e and any(covered[s:e]))
    if regions:
        return ()                       # every recognised line came from a text layer
    if _may_be_recognised(media_type):
        return ((0, len(text)),)        # the forgotten-media case — fail closed, loudly
    return ()


def _blank(chars: list[str], start: int, end: int) -> None:
    """Replace a range with spaces IN PLACE, leaving line terminators alone.

    Offsets are preserved (characters replaced, never deleted) so pointers built against the
    masked view still resolve correctly against the original text.
    """
    for i in range(max(0, start), min(end, len(chars))):
        if chars[i] not in ("\n", "\r"):
            chars[i] = " "


def mask_unticked_options(text: str, *, media: Any = None,
                          media_type: str = "text/plain") -> str:
    """The view a checkbox-derived field is grounded against: declined options and unproven
    checkbox state both blanked out, offsets preserved.

    On a checkbox form the unselected options are printed in full: the TPA authorisation form
    lists PEE224, PEE225 *and* PEE226, and the wellness voucher lists WELL1, WELL2 *and* WELL3.
    All of them are genuinely on the page, so a wall that only asks "does this string appear in
    the source?" happily grounds an option the form explicitly declined — authorising the wrong
    examination and billing the wrong package.

    Masking makes the wall, not the prompt, responsible for checkbox correctness. It keys on the
    Unicode ballot box rather than on any issuer's layout, so it works for any form that uses the
    convention and is a no-op for documents that do not.

    **R-13.** That is only true while the glyphs are on the page, so the second half of this view
    is about the glyphs that are NOT. ``media``/``media_type`` say where the characters came from;
    any stretch whose checkbox state cannot be proved complete
    (``unprovable_checkbox_spans``) is blanked as well. The caller grounds only the
    checkbox-derived fields against this view, so the effect is that those fields — and only
    those — come back ``None`` and land in ``unresolved`` instead of arriving as a grounded value
    beside a warning nobody has to act on.

    Both arguments are keyword-only and both are consulted: passing neither, for a document that
    a recognition step produced, is the failure this control exists to catch.
    """
    withheld = unprovable_checkbox_spans(text, media, media_type)
    if UNTICKED_BOX not in text and not withheld:
        return text
    chars = list(text)
    if UNTICKED_BOX in text:
        for start, end in _line_bounds(text):
            line = text[start:end]
            if UNTICKED_BOX in line and TICKED_BOX not in line:
                _blank(chars, start, end)
    for start, end in withheld:
        _blank(chars, start, end)
    return "".join(chars)


# --- locating a value in the source ------------------------------------------------
def _boundary_find(text: str, needle: str) -> Optional[tuple[int, int]]:
    """First whole-token occurrence of ``needle``, case-insensitive, or None.

    Word-boundary anchored so ``"man"`` cannot ride inside ``"management"`` — grounding by
    containment is the single easiest way to make this wall useless.
    """
    if not needle.strip():
        return None
    m = re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", text, re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def _find_span_fuzzy(text: str, value: str) -> Optional[tuple[int, int]]:
    """Locate a multi-token value whose wording differs slightly from the source.

    Slides a window of the value's token length over the source tokens and keeps the best window
    scoring at or above threshold. Used only after the exact search fails, so the common case
    stays cheap.
    """
    vtoks = _word_tokens(value)
    if not vtoks:
        return None
    positions = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    stoks = [text[s:e].lower() for s, e in positions]
    if not stoks:
        return None
    width = len(vtoks)
    best: tuple[float, tuple[int, int]] | None = None
    for i in range(0, len(stoks) - width + 1):
        window = stoks[i:i + width]
        ok, score = _passes_in_order(vtoks, window)
        if ok and (best is None or score > best[0]):
            best = (score, (positions[i][0], positions[i + width - 1][1]))
    return best[1] if best else None


def _find_span_exact_tokens(text: str, vtoks: list[str]) -> Optional[tuple[int, int]]:
    """Span of the first place ``vtoks`` appear contiguously (ignoring separators).

    Requiring contiguity matters: "Loh" and "Ming" both appearing somewhere on a page does not
    mean the page names "Loh Ming".
    """
    positions = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    stoks = [text[s:e].lower() for s, e in positions]
    n = len(vtoks)
    for i in range(0, len(stoks) - n + 1):
        if stoks[i:i + n] == vtoks:
            return positions[i][0], positions[i + n - 1][1]
    return None


def _token_score(vt: str, source_tokens: list[str]) -> float:
    """One value token's best match against any source token.

    Tokens of ``SHORT_TOKEN_LEN`` characters or fewer must match EXACTLY. Edit-distance ratios are
    meaningless at that length — ``ratio("min", "ming")`` is 0.86, comfortably over any sane
    floor — and short tokens are exactly where Singaporean names differ: Min/Ming, Wei/Wai,
    Tan/Tang are different people, and this registry has 300 patients with heavily overlapping
    name particles. Longer tokens keep fuzzy tolerance for genuine OCR drift.
    """
    if len(vt) <= SHORT_TOKEN_LEN:
        return 1.0 if vt in source_tokens else 0.0
    return max(fuzz.ratio(vt, st) / 100.0 for st in source_tokens)


def _token_scores(value_tokens: list[str], source_tokens: list[str]) -> list[float]:
    if not value_tokens or not source_tokens:
        return []
    return [_token_score(vt, source_tokens) for vt in value_tokens]


def _passes_in_order(value_tokens: list[str], window: list[str]) -> tuple[bool, float]:
    """Positional comparison of a value against an equal-length window of source tokens.

    Compares ``value_tokens[i]`` to ``window[i]`` rather than letting each value token pick its
    best match anywhere. Order-free matching grounds "Test HIV" against a document reading
    "HIV Test", and lets one source token satisfy two value tokens — both of which are phrases
    the document does not contain.
    """
    if len(value_tokens) != len(window) or not window:
        return False, 0.0
    per = [_token_score(vt, [st]) for vt, st in zip(value_tokens, window)]
    mean = sum(per) / len(per)
    return (mean >= FUZZY_THRESHOLD and min(per) >= TOKEN_FLOOR), mean


def _passes(value_tokens: list[str], source_tokens: list[str]) -> tuple[bool, float]:
    """Whether every token of the value is present in the source, and the mean score.

    Two conditions, because either alone leaks:

    * **mean >= 0.90** — catches values broadly unlike the source.
    * **min >= 0.85** (``TOKEN_FLOOR``) — catches what the mean cannot. "Loh Wei Mingus" against a
      document saying "Loh Wei Ming" scores (1.00 + 1.00 + 0.80) / 3 = 0.93 and sails through a
      mean-only check: two correct tokens dilute one fabricated surname. For a patient name that
      is exactly the error that must not pass, so no single token may be weak.
    """
    per = _token_scores(value_tokens, source_tokens)
    if not per:
        return False, 0.0
    mean = sum(per) / len(per)
    return (mean >= FUZZY_THRESHOLD and min(per) >= TOKEN_FLOOR), mean


# --- the wall ----------------------------------------------------------------------
@dataclass(frozen=True)
class GroundingResult:
    """Outcome of grounding one candidate value.

    Invariant: ``grounded`` implies ``span is not None``. A "grounded" verdict with nowhere to
    point is not evidence, and callers that trusted the flag without checking the span would
    emit a value whose provenance is empty.
    """

    grounded: bool
    span: Optional[tuple[int, int]] = None
    score: float = 0.0
    reason: str = ""
    matched_text: str = ""      # the exact source slice — what the value SHOULD be recorded as

    def __post_init__(self) -> None:
        if self.grounded and self.span is None:
            raise ValueError("GroundingResult: grounded=True requires a span")


def locate(text: str, value: Any, kind: GroundKind = "text") -> GroundingResult:
    """Find ``value`` in ``text`` under the rule for ``kind``, or explain why it is not there."""
    vstr = str(value).strip()
    if not vstr:
        return GroundingResult(False, reason="empty value")
    # A one-character "value" is not a groundable fact; it will collide with something.
    if len(vstr) < 2 and kind != "number":
        return GroundingResult(False, reason=f"{vstr!r} is too short to ground")

    # A value with no word characters at all cannot be located. Without this guard the token
    # comparisons below operate on an EMPTY token list, every "all tokens present" test passes
    # vacuously, and the value grounds against a span covering the whole document — the worst
    # possible outcome: arbitrary content admitted with an authoritative-looking citation.
    if not _WORD.search(vstr):
        return GroundingResult(
            False, reason=f"{vstr!r} contains no matchable word characters")

    if kind == "id":
        canon = _canon_id(vstr)
        for tok in _value_tokens(text):
            if _canon_id(tok) == canon:
                span = _boundary_find(text, tok)
                if span:
                    return GroundingResult(True, span, 1.0, matched_text=text[span[0]:span[1]])
        # The id may be printed with internal spacing ("S 8010946 C" on a form). Search a
        # whitespace-collapsed view and map the hit back to real offsets.
        span = _find_span_ignoring_whitespace(text, canon)
        if span:
            return GroundingResult(True, span, 1.0, matched_text=text[span[0]:span[1]])
        return GroundingResult(False, reason=f"id {vstr!r} does not appear in the source")

    if kind == "number":
        want = _to_decimal(vstr)
        if want is None:
            return GroundingResult(False, reason=f"{vstr!r} is not a number")
        # Locate the numeric TOKEN whose value equals `want`, so a match always has a span.
        # ("1000" against a source printing "1,000.00" is the same number written differently.)
        stripped_spans = _numeric_token_spans(text)
        for start, end, dec in stripped_spans:
            if dec == want:
                return GroundingResult(True, (start, end), 1.0, matched_text=text[start:end])
        return GroundingResult(False, reason=f"number {vstr!r} does not appear in the source")

    if kind == "date":
        want = _as_date(vstr)
        if want is None:
            return GroundingResult(False, reason=f"{vstr!r} is not a date")
        if want in _dates_in(text):
            # Point at the printed form, whatever format it used.
            for m in _DATE_TOKEN.finditer(text):
                try:
                    if _dateparser.parse(m.group(), dayfirst=True, fuzzy=False).date() == want:
                        return GroundingResult(True, (m.start(), m.end()), 1.0)
                except (ValueError, OverflowError):
                    continue
        return GroundingResult(False, reason=f"date {vstr!r} does not appear in the source")

    exact = _boundary_find(text, vstr)
    if exact:
        return GroundingResult(True, exact, 1.0, matched_text=text[exact[0]:exact[1]])

    if kind == "name":
        # Names get NO fuzzy tolerance. "Lim Arjuna" against a voucher reading "Lim Arjun" scores
        # 0.96 under any reasonable token rule — but they are two different people, and this
        # registry holds 300 patients with heavily overlapping name particles. The extractor is
        # instructed to copy verbatim, so exact-after-normalisation costs nothing real and closes
        # the one class of error that would put the wrong person's record on the screen.
        vtoks, stoks = _word_tokens(vstr), _word_tokens(text)
        missing = [t for t in vtoks if t not in stoks]
        if missing:
            return GroundingResult(
                False, reason=f"name {vstr!r} does not appear in the source "
                              f"(token(s) not present: {missing})")
        span = _find_span_exact_tokens(text, vtoks)
        if span:
            return GroundingResult(True, span, 1.0, matched_text=text[span[0]:span[1]])
        return GroundingResult(
            False, reason=f"name {vstr!r}: all tokens occur but never contiguously")

    # free text: a cheap whole-text pre-check, then an ORDER-PRESERVING contiguous window
    ok, score = _passes(_word_tokens(vstr), _word_tokens(text))
    if ok:
        span = _find_span_fuzzy(text, vstr)
        if span:
            return GroundingResult(True, span, score, matched_text=text[span[0]:span[1]])
        return GroundingResult(
            False, score=score,
            reason=f"{vstr!r} scores {score:.2f} but no contiguous in-order span matches it")
    return GroundingResult(False, score=score,
                           reason=f"{vstr!r} not found in the source (best token score "
                                  f"{score:.2f} < {FUZZY_THRESHOLD})")


def ground(text: str, value: Any, *, document_id: str, kind: GroundKind = "text",
           field_path: str = "") -> tuple[Optional[GroundedValue], Optional[str]]:
    """Ground one value. Returns ``(GroundedValue, None)`` or ``(None, refusal reason)``.

    On success the recorded value is **the source slice, not the model's string**. Where fuzzy
    tolerance let a slightly-off rendering through ("Complete Physical Examinaton"), storing the
    model's spelling would leave a record whose pointer resolves to different text than the value
    it supposedly cites — a citation that does not say what the field says. Taking the document's
    wording keeps `text[char_start:char_end] == value` true for every grounded field, which is the
    invariant the front-desk "click to see the source" affordance depends on.
    """
    result = locate(text, value, kind)
    if not result.grounded or result.span is None:
        where = f"{field_path}: " if field_path else ""
        return None, f"{where}{result.reason}"
    start, end = result.span
    pointer = SourcePointer(source_id=document_id, kind="doc_span", document_id=document_id,
                            char_start=start, char_end=end)
    recorded = result.matched_text or text[start:end]
    return GroundedValue(value=recorded, pointer=pointer, confidence=result.score), None
