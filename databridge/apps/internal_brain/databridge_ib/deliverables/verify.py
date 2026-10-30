"""The deterministic checks every deliverable passes before anyone sees it. Pure: no I/O.

What a kept NUMBER (a chart point, a numeric table cell) has to satisfy (review_opus.md #1, #3,
#5; review_codex.md #3, #4):

1. its ``tag`` is one of the evidence tags (a cited document the asker can open now);
2. ``value`` is a finite number (no bool, NaN or infinity; schema-level) and ``raw`` holds exactly
   ONE number span (``databridge_core.value_forms``) whose key equals ``value``'s key, sign
   included. No unit conversion, ever: ``0.35%`` given as ``0.0035`` is dropped;
3. ``raw`` occurs in ONE source unit of that tag (a CSV row or a sentence) such that its number
   lies EXACTLY on a number span of the source, with word boundaries on both ends. So ``35%``
   inside ``0.35%``, ``0.5%`` inside ``-0.5%`` and ``20,000`` inside ``120,000`` never pass;
4. scale and locale tricks never pass: a number followed by a letter or a scale word (``20k``,
   ``1.8 million``, ``5 bn``, ``35 bps``, ``2万``), an ambiguous grouping (``IDR 20.000``,
   ``0,35``, ``20 000``) or an accounting bracket ``(500)`` is rejected, in the proposal and in
   the source;
5. the LABEL is bound to that number: in a CSV row every label token is a token of that same row;
   in prose every label token is written before the number in the same sentence with no other
   number in between. A label needs at least one real word token;
6. the UNIT is worked out by our code from ``raw`` (else the CSV column header): percent, a
   currency (one ISO code or symbol), days, hours, or a plain number. One unit and one currency
   per series, and per chart. The model's own ``unit`` field is not trusted;
7. (round 4 B6) the METRIC is bound to that number too: a series name, y-axis label or table column
   label taken from the source must be that column's header or words of the number's own cell (CSV),
   or words of the number's own clause (prose). A value moved to another column or metric is
   dropped; a label the source does not hold is replaced by neutral text ("Values", "Column n").
   (round 5 #3) A chart series name is decided first, over all its points' evidence; when it is
   from the sources, EVERY point must be bound to it in its own row or sentence, or it is dropped.
   A chart title may only use words of the chart's own checked labels.

A SENTENCE (analysis) is checked against the units of its OWN tags only (never the answer):
every number by key AND unit marker, every identifier as a whole token, every capitalised term
(sentence-initial included, unless a common word) as a whole word, no identity assertion, no text
addressed to an AI, no URL, e-mail address or phone number, a shared content word, no filler
opener, at most 30 words. Round 4 B5: each NUMBER must be found in ONE source unit together with
the names and content words of its own clause in the sentence (round 5 #2: every alphabetic word
of 2+ letters except a small stop-word list, so "gas", "fee", "net" bind too): in a CSV row, within
that row plus the header of the number's column; in prose, within the number's own clause of that
sentence ("Singapore revenue is 20" fails when 20 is Hong Kong's, or Singapore's cost).

What this does NOT prove (rule 8): that a comparison is meaningful (two figures from different
dates), or the relation a sentence draws between two evidenced terms. Identifiers and dates in a
sentence are checked for presence in the sentence's sources, not bound to a clause; a clause with
no name or content word (e.g. "and 20") binds to any unit holding that number. Lowercase or non-Latin
invented names are not caught by the capitalised-term rule (non-Latin runs must appear verbatim).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

from databridge_core.value_forms import ValueSpan, fold_dashes, number_key, value_spans

from .. import narrate
from ..grounding import identity_assertion_violations
from .evidence import Evidence, Unit, nfkc

# ------------------------------------------------------------------------------------------------
# small text helpers
# ------------------------------------------------------------------------------------------------
_WORD = re.compile(r"[^\W_]+", re.U)
#: function words that never count as a label token (a label of only these binds to nothing)
STOP = frozenset(
    "a an and are as at be been but by for from has have in into is it its of on or per the "
    "than that this to was were with vs via".split())
_SCALE_AFTER = re.compile(
    r"^(?:\s?(?:k|m|mm|mn|b|bn|bil|t|tn|thousand|thousands|million|millions|billion|billions|"
    r"trillion|trillions|lakh|lakhs|crore|crores|ribu|juta|miliar|triliun|bps|bp|basis\s+points?)"
    r"\b|\s?[千万萬亿億兆])", re.I)
_AMBIGUOUS = (re.compile(r"^[1-9]\d{0,2}\.\d{3}$"),          # 20.000 (ID / EU thousands)
              re.compile(r"^\d{1,3},\d{1,2}$"))               # 0,35 (EU decimal)
_URL = re.compile(r"https?://|www\.|\b[\w.-]+\.(?:com|net|org|io|sg|hk|id|example)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_CJK = re.compile(r"[㐀-鿿豈-﫿]{2,}")
_FILLER = re.compile(r"^(?:overall|in conclusion|in summary|to summari[sz]e|notably|"
                     r"it is (?:important|worth) (?:to note|noting)|it should be noted)\b", re.I)
_CONTENT = re.compile(r"[a-z][a-z'-]{3,}")
_FUNCTION = frozenset(
    "about above after again also although among another because been before being below "
    "between both could does doing down during each either else ever every from further have "
    "having here however into itself just least less many more most much must neither never "
    "none only other otherwise over same should since some such than that their them then there "
    "these they this those though through under until very were what when where whether which "
    "while whom whose will with within without would your yours".split())
#: capitalised words a sentence may open with that are not names (checked case-insensitively)
COMMON = frozenset(
    "a an the this that these those it its there here our we you your their they he she his her "
    "all both each every some any most many no none one two three four five several few only "
    "also and but or so yet if when while since because although though as at by for from in "
    "into of on onto to with without per under over after before during until between among "
    "fees fee rates rate prices price customers customer accounts account plans plan markets "
    "market draft drafts approved current proposed new not however currently today yes".split())
CURRENCIES = ("SGD", "HKD", "IDR", "USD", "EUR", "GBP", "MYR", "CNY", "RMB", "JPY", "AUD", "THB",
              "PHP", "VND", "INR", "KRW", "TWD", "CHF", "CAD", "NZD")
_CODE = re.compile(r"(?<![A-Za-z])(" + "|".join(CURRENCIES) + r")(?![A-Za-z])")
_SYMBOL = (("HK$", "HKD"), ("S$", "SGD"), ("US$", "USD"), ("Rp", "IDR"), ("€", "EUR"),
           ("£", "GBP"), ("¥", "JPY"), ("$", "$"))


def norm(text: str) -> str:
    """NFKC, typographic dashes folded (offset-preserving), whitespace collapsed. Case is KEPT:
    the number parser reads ``SGD20,000`` only in capitals (Codex review #3)."""
    return fold_dashes(nfkc(text))


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(nfkc(text))]


def label_tokens(text: str) -> list[str]:
    """The tokens a label must be bound by: every word (letters or digits) except function words."""
    return [w for w in words(text) if w not in STOP and (len(w) >= 2 or w.isdigit())]


def has_real_word(toks: Iterable[str]) -> bool:
    return any(any(c.isalpha() for c in t) and len(t) >= 2 for t in toks)


def unsafe_text(text: str) -> Optional[str]:
    """A reason the text may never be shown as ours: a URL, an e-mail, a phone number, text
    addressed to an AI, or an identity assertion. None when it is clean."""
    if _URL.search(text):
        return "url"
    if _EMAIL.search(text):
        return "email"
    if _PHONE.search(text) and sum(c.isdigit() for c in text) >= 8:
        return "phone"
    if narrate._ADDRESSED_TO_AI.search(text):
        return "injection"
    if identity_assertion_violations(text):
        return "identity"
    return None


def value_key(value: Any) -> Optional[str]:
    """The ``value_forms`` key of a proposed number (finite, not bool, magnitude < 1e15)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if abs(value) >= 1e15:
        return None
    try:
        d = Decimal(repr(value)) if isinstance(value, float) else Decimal(int(value))
    except (InvalidOperation, ValueError):
        return None
    return number_key(format(d, "f"))


def _nums(text: str) -> list[ValueSpan]:
    return [s for s in value_spans(text) if s.kind == "num"]


def _digits_of(span: ValueSpan) -> str:
    return span.raw.lstrip("-").lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def ambiguous_at(text: str, span: ValueSpan) -> Optional[str]:
    """Why the number span at ``span`` in ``text`` cannot be read safely, or None."""
    digits = _digits_of(span)
    if any(p.match(digits) for p in _AMBIGUOUS):
        return "ambiguous_grouping"
    after, before = text[span.end:], text[:span.start]
    if _SCALE_AFTER.match(after) or (after[:1].isalpha() and not after[:1] in "%"):
        return "scale"
    if re.match(r"^[.,]\d", after) or re.search(r"\d[.,]$", before):
        return "ambiguous_grouping"
    if re.match(r"^\s\d{3}(?!\d)", after) or re.search(r"\d\s$", before):
        return "ambiguous_grouping"
    if before.endswith("(") and after.startswith(")"):
        return "accounting"
    return None


def unit_of(raw: str, header: str = "") -> tuple[str, Optional[str]]:
    """``(unit_class, currency)`` worked out by OUR code: from ``raw``, else the column header."""
    for text in (raw, header):
        if not text:
            continue
        if "%" in text or re.search(r"\bper\s?cent\b", text, re.I):
            return "percent", None
        m = _CODE.search(text)
        if m:
            code = m.group(1)
            return "currency", ("CNY" if code == "RMB" else code)
        for sym, code in _SYMBOL:
            if sym in text:
                return "currency", code
        if re.search(r"\bdays?\b", text, re.I):
            return "days", None
        if re.search(r"\b(?:hours?|hrs?)\b", text, re.I):
            return "hours", None
    return "number", None


# ------------------------------------------------------------------------------------------------
# numbers bound to labels
# ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Found:
    """Where a checked number was found: the unit, the source span, our unit and currency, and
    (round 4 B6) the words its metric may be named by."""
    unit: Unit
    span: ValueSpan
    unit_class: str
    currency: Optional[str]
    metric_words: frozenset = frozenset()


# -- round 4 B5/B6: binding a value to ITS entity and metric ------------------------------------
#: where the words a value "sits with" end after it: the next value, punctuation, a conjunction
_CLAUSE_END = re.compile(r"[;,:!?]|\.(?!\d)|\s(?:and|but|while|whereas|whilst)\s", re.I)


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def wordset(text: str) -> set[str]:
    """Lower-case words, a plural ``s`` folded (``fees`` = ``fee``)."""
    return {_stem(w) for w in words(text)}


def clause(text: str, nums: Sequence[ValueSpan], span: ValueSpan) -> str:
    """The words the number at ``span`` sits with in ``text``: from the previous number to it,
    then what follows it up to the clause's end. In "Singapore revenue is 10, cost is 20" the
    clause of 20 is ", cost is " and never holds "Singapore" or "revenue"."""
    start = max((o.end for o in nums if o.end <= span.start), default=0)
    nxt = min((o.start for o in nums if o.start >= span.end), default=len(text))
    after = text[span.end:nxt]
    m = _CLAUSE_END.search(after)
    return text[start:span.start] + " " + (after[:m.start()] if m else after)


def metric_tokens(label: str) -> set[str]:
    """The words a metric label (a series name, an axis or column label) must be bound by."""
    return {_stem(w) for w in label_tokens(label) if any(c.isalpha() for c in w)}


#: round 5 #2: the words a clause may hold without claiming anything about its number. A short
#: content word ("gas", "oil", "fee", "tax", "net", "sg") is NOT here: it is bound like any other.
_CLAIM_STOP = STOP | _FUNCTION | frozenset(
    "am be is are was were been has had have do did does done get got its it our we us you he she "
    "they his her my me him an the of off on in at to by up out so if or nor no not too also all "
    "any can may now new one two per vs via say says said set hit put let saw see "
    # short relation verbs: they link a name to its number, they are not the entity or metric
    "pay add use run own led won ran".split())


def claim_tokens(text: str) -> set[str]:
    """What a sentence's clause claims about its number: every name or acronym (capitalised, not
    a common word) and every alphabetic content word of 2+ letters that is not a stop-word."""
    out: set[str] = set()
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9'&-]*[A-Za-z0-9]\b", text):
        low = m.group(0).lower()
        if low not in COMMON and low not in _FUNCTION:
            out |= {w for w in words(m.group(0)) if any(c.isalpha() for c in w)}
    out |= {w for w in words(text) if len(w) >= 2 and w.isalpha() and w not in _CLAIM_STOP}
    return {_stem(w) for w in out}


def _header(ev: Optional[Evidence], col: Optional[int]) -> str:
    if ev is None or ev.csv is None or col is None or col >= len(ev.csv.header):
        return ""
    return ev.csv.header[col]


def _boundary_ok(line: str, p: int, needle: str) -> bool:
    if needle[:1].isalnum() and p > 0 and line[p - 1].isalnum():
        return False
    end = p + len(needle)
    if needle[-1:].isalnum() and end < len(line) and line[end].isalnum():
        return False
    return True


def _occurrences(line: str, needle: str) -> Iterable[int]:
    i = line.find(needle)
    while i >= 0:
        yield i
        i = line.find(needle, i + 1)


def _token_positions(line: str, tok: str) -> list[int]:
    return [m.start() for m in re.finditer(r"(?<![^\W_])" + re.escape(tok) + r"(?![^\W_])",
                                           line.lower())]


def _bound_in_sentence(line: str, span: ValueSpan, toks: Sequence[str]) -> bool:
    """Every label token is written BEFORE the number in the same sentence with no other number
    in between ("SG 0.35%, HK 0.5%": HK is bound to 0.5% only, never to 0.35%). A label written
    only after its number ("0.4% for ID") is not accepted: it fails closed."""
    own = {q for t in toks for q in _token_positions(line, t)}       # the label's own numbers
    others = [o.start for o in _nums(line) if o.start != span.start and o.start not in own]
    for t in toks:
        ok = False
        for p in _token_positions(line, t):
            if p < span.start and not any(p < o < span.start for o in others):
                ok = True
                break
        if not ok:
            return False
    return True


def find_number(x: str, value: Any, raw: str, ev: Optional[Evidence], *,
                metric: str = "") -> tuple[Optional[Found], str]:
    """``(Found, "ok")`` when the point ``{x, value, raw}`` passes rules 2-6 in ``ev``; else
    ``(None, reason)`` with one of: tag, value, raw, scale, ambiguous_grouping, accounting, label,
    unsafe, not_found.

    Round 4 B6: with a ``metric`` (the series name, or the table column label), the number must
    ALSO be bound to it: in a CSV row, every metric word is in that column's header or in the
    number's own cell; in prose, in the number's own clause. A value moved to another column or
    metric is not found."""
    need = metric_tokens(metric) if metric else set()
    if ev is None:
        return None, "tag"
    vkey = value_key(value)
    if vkey is None:
        return None, "value"
    r = norm(raw).strip()
    if not r or len(r) > 60:
        return None, "raw"
    rnums = _nums(r)
    if len(rnums) != 1 or any(s.kind == "id" for s in value_spans(r)):
        return None, "raw"
    rs = rnums[0]
    if rs.key != vkey:
        return None, "value"
    why = ambiguous_at(r, rs)
    if why:
        return None, why
    toks = label_tokens(x)
    if not has_real_word(toks):
        return None, "label"
    if unsafe_text(x) or unsafe_text(r):
        return None, "unsafe"
    for unit in ev.units:
        if unit.row is not None and unit.row < 0:
            continue                                            # the header holds no values
        # a CSV row is read cell by cell (a neighbouring cell is not part of the number)
        pieces = ([(i, norm(c)) for i, c in enumerate(unit.cells)] if unit.row is not None
                  else [(None, norm(unit.text))])
        for col, line in pieces:
            for p in _occurrences(line, r):
                if not _boundary_ok(line, p, r):
                    continue
                src = next((s for s in _nums(line) if s.start == p + rs.start
                            and s.end == p + rs.end and s.key == vkey), None)
                if src is None or ambiguous_at(line, src):
                    continue
                if unit.row is not None:                        # a CSV row: the same row
                    if not set(toks) <= set(words(" ".join(unit.cells))):
                        continue
                elif not _bound_in_sentence(line, src, toks):
                    continue
                header = _header(ev, col)
                mw = (wordset(header) | wordset(line) if unit.row is not None
                      else wordset(clause(line, _nums(line), src)))
                if not need <= mw:                              # B6: another column / metric
                    continue
                uc, cur = unit_of(r, header)
                return Found(unit, src, uc, cur, frozenset(mw)), "ok"
    return None, "not_found"


# ------------------------------------------------------------------------------------------------
# the check report
# ------------------------------------------------------------------------------------------------
@dataclass
class Check:
    """Counts about what the MODEL proposed (all zero for deterministic builds). A function of
    permitted input only (the evidence), so it is identical with or without restricted content."""
    values_proposed: int = 0
    values_kept: int = 0
    values_dropped: int = 0
    items_proposed: int = 0
    items_kept: int = 0
    items_dropped: int = 0
    names_dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str, *, value: bool = False, name: bool = False,
             values: Optional[int] = None) -> None:
        """One item dropped. ``values``: how many proposed values went with it (counted in
        ``values_proposed`` already); else ``value`` counts one. Story 1 fix #7: every proposed
        value ends up kept or dropped, so values_proposed == values_kept + values_dropped."""
        self.items_dropped += 1
        if values is not None:
            self.values_dropped += max(0, values)
        elif value:
            self.values_dropped += 1
        if name:
            self.names_dropped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def retract(self, reason: str, values: int = 0) -> None:
        """An item counted as kept is dropped by a later check (a timeline entry whose date fails):
        it and its values move from kept to dropped."""
        self.items_kept = max(0, self.items_kept - 1)
        self.values_kept = max(0, self.values_kept - values)
        self.drop(reason, values=values)

    def public(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in ("values_proposed", "values_kept", "values_dropped",
                                              "items_proposed", "items_kept", "items_dropped",
                                              "names_dropped")}


_VALUE_REASONS = {"value", "raw", "scale", "ambiguous_grouping", "accounting", "not_found", "unit"}


# ------------------------------------------------------------------------------------------------
# charts
# ------------------------------------------------------------------------------------------------
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def period_key(x: str) -> Optional[tuple[int, int, int]]:
    """A sortable key when ``x`` is a date or period (ISO date, ``D Month YYYY``, ``Month YYYY``,
    ``Qn YYYY``, ``YYYY``), else None."""
    s = nfkc(x).lower()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return int(m[1]), int(m[2]), int(m[3])
    m = re.fullmatch(r"(\d{1,2}) ([a-z]{3})[a-z]* (\d{4})", s)
    if m and m[2] in _MONTHS:
        return int(m[3]), _MONTHS.index(m[2]) + 1, int(m[1])
    m = re.fullmatch(r"([a-z]{3})[a-z]* (\d{4})", s)
    if m and m[1] in _MONTHS:
        return int(m[2]), _MONTHS.index(m[1]) + 1, 0
    m = re.fullmatch(r"q([1-4]) (\d{4})|(\d{4}) q([1-4])", s)
    if m:
        q, y = (m[1], m[2]) if m[1] else (m[4], m[3])
        return int(y), int(q) * 3, 0
    m = re.fullmatch(r"(?:fy)?(\d{4})", s)
    if m:
        return int(m[1]), 0, 0
    return None


def check_short_text(text: str, hay_units: Sequence[Unit]) -> bool:
    """A title, axis label, series name or column label: every word token appears in the cited
    units (whole words), and it is safe. Stronger than the capitalised-name rule (Opus #7)."""
    toks = label_tokens(text)
    if not toks or unsafe_text(text) or _CJK.search(text) and not all(
            c in " ".join(u.text for u in hay_units) for c in _CJK.findall(text)):
        return False
    hay = set(words(" ".join(u.text for u in hay_units)))
    return set(toks) <= hay


def check_chart(spec: Mapping[str, Any], evidence: Mapping[str, Evidence], check: Check
                ) -> Optional[dict[str, Any]]:
    """The checked chart content, or None when fewer than 2 points of one unit survive."""
    series_out = []
    for s in spec.get("series") or []:
        pts, seen = [], set()
        sname = nfkc(str(s.get("name", "")))
        # B6 (round 5 #3): the series name is decided FIRST, over the evidence of all its points.
        # A name taken from the sources names a source metric, so EVERY point must be bound to it
        # in its own row or sentence (a point whose evidence lacks the metric is dropped); a name
        # the sources do not hold binds nothing and is replaced by "Values" below.
        s_tags = dict.fromkeys(str(p.get("tag", "")) for p in s.get("points") or [])
        s_hay = [u for t in s_tags if t in evidence for u in evidence[t].units]
        metric = sname if sname and check_short_text(sname, s_hay) else ""
        for p in s.get("points") or []:
            check.items_proposed += 1
            check.values_proposed += 1
            x = nfkc(str(p.get("x", "")))
            pev = evidence.get(str(p.get("tag", "")))
            found, why = find_number(x, p.get("value"), str(p.get("raw", "")), pev,
                                     metric=metric)
            if found is None:                       # the point's value goes with it
                check.drop(why, value=True, name=why in ("label", "unsafe"))
                continue
            if x.lower() in seen:
                check.drop("duplicate", value=True)
                continue
            seen.add(x.lower())
            pts.append({"x": x, "value": p.get("value"), "raw": nfkc(str(p.get("raw"))),
                        "tag": str(p.get("tag")), "_unit": (found.unit_class, found.currency),
                        "_mw": found.metric_words})
        if not pts:
            continue
        units = {p["_unit"] for p in pts}
        if len(units) != 1:                          # mixed units in one series: the series goes
            for _ in pts:
                check.drop("unit", value=True)
            continue
        series_out.append({"name": nfkc(str(s.get("name", ""))), "points": pts,
                           "_unit": next(iter(units))})
    if not series_out:
        return None
    # one unit and one currency per chart: keep the series sharing the unit of the largest one
    main = max(series_out, key=lambda s: len(s["points"]))["_unit"]
    kept = []
    for s in series_out:
        if s["_unit"] != main:
            for _ in s["points"]:
                check.drop("unit", value=True)
            continue
        if len(s["points"]) < 2:
            for _ in s["points"]:
                check.drop("too_few", value=True)
            continue
        kept.append(s)
    if not kept:
        return None
    tags = {p["tag"] for s in kept for p in s["points"]}
    hay = [u for t in sorted(tags) for u in evidence[t].units]
    y_label = nfkc(str(spec.get("y_label") or ""))
    yneed = metric_tokens(y_label) if check_short_text(y_label, hay) else set()
    y_bound = all(yneed <= p["_mw"] for s in kept for p in s["points"])     # B6: the y metric
    for i, s in enumerate(kept, 1):
        if not check_short_text(s["name"], hay):
            if s["name"]:
                check.names_dropped += 1
            s["name"] = "Values" if len(kept) == 1 else f"Series {i}"
        for p in s["points"]:
            p.pop("_unit", None)
            p.pop("_mw", None)
        s.pop("_unit", None)
    n_kept = sum(len(s["points"]) for s in kept)
    check.values_kept += n_kept
    check.items_kept += n_kept
    kind = spec.get("type") if spec.get("type") in ("bar", "line") else "bar"
    as_bar = False
    if kind == "line":
        if all(period_key(p["x"]) for s in kept for p in s["points"]):
            for s in kept:
                s["points"].sort(key=lambda p: period_key(p["x"]))
        else:
            kind, as_bar = "bar", True
    x_label = nfkc(str(spec.get("x_label") or ""))
    return {"type": kind, "drawn_as_bar": as_bar,
            "x_label": x_label if x_label and check_short_text(x_label, hay) else "",
            "y_label": (y_label if y_label and y_bound and check_short_text(y_label, hay)
                        else ""),
            "unit": main[0], "currency": main[1], "series": kept}


# ------------------------------------------------------------------------------------------------
# sentences (analysis) and table cells
# ------------------------------------------------------------------------------------------------
def _marker(text: str, span: ValueSpan) -> str:
    """The unit marker written next to a number: ``%``, a currency code, or ""."""
    after = text[span.end:span.end + 9]
    before = text[max(0, span.start - 5):span.start]
    if re.match(r"^\s?(?:%|per\s?cent)", after, re.I):
        return "%"
    m = _CODE.search(span.raw) or _CODE.search(before)
    if m:
        return m.group(1)
    for sym, code in _SYMBOL:
        if before.rstrip().endswith(sym):
            return code
    return ""


def _value_supported(text: str, span: ValueSpan, hay: str) -> bool:
    if span.kind == "num":
        if ambiguous_at(text, span):
            return False
        want = _marker(text, span)
        return any(s.key == span.key and _marker(hay, s) == want and not ambiguous_at(hay, s)
                   for s in _nums(hay))
    needle = re.compile(r"(?<![a-z0-9])" + re.escape(span.key) + r"(?![a-z0-9])")
    return bool(needle.search(fold_dashes(hay).lower()))


def _num_bound(t: str, nums: Sequence[ValueSpan], s: ValueSpan,
               sources: Sequence[tuple[Unit, Evidence]], *, bind: bool = True) -> bool:
    """Round 4 B5: a number in a sentence must be found (key AND unit marker, read safely) in ONE
    source unit together with what its clause claims about it (``claim_tokens``: the entity and
    metric words): in a CSV row, within that row's cells plus the header of the number's own
    column; in prose, within the number's own clause of that sentence. A value that only appears
    somewhere else in the evidence ("Singapore revenue is 20" when 20 is Hong Kong's) fails."""
    if ambiguous_at(t, s):
        return False
    want, need = _marker(t, s), (claim_tokens(clause(t, nums, s)) if bind else set())
    for unit, ev in sources:
        if unit.row is not None:
            if unit.row < 0:
                continue
            row_words = wordset(" ".join(unit.cells))
            for col, cell in enumerate(unit.cells):
                line = norm(cell)
                if any(o.key == s.key and _marker(line, o) == want and not ambiguous_at(line, o)
                       for o in _nums(line)) and need <= row_words | wordset(_header(ev, col)):
                    return True
            continue
        line = norm(unit.text)
        lnums = _nums(line)
        for o in lnums:
            if (o.key == s.key and _marker(line, o) == want and not ambiguous_at(line, o)
                    and need <= wordset(clause(line, lnums, o))):
                return True
    return False


def _names_ok(text: str, hay: str) -> bool:
    hay_words = set(words(hay))
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9'&-]*[A-Za-z0-9]\b", text):
        term = m.group(0)
        low = term.lower()
        if low in COMMON or low in _FUNCTION:
            continue
        parts = [w for w in words(term) if w]
        if not all(w in hay_words for w in parts):
            return False
    for run in _CJK.findall(text):
        if run not in hay:
            return False
    return True


def check_sentence(text: str, tags: Sequence[str], evidence: Mapping[str, Evidence], check: Check,
                   *, max_words: int = 30) -> Optional[dict[str, Any]]:
    """The sentence, checked against the units of its own tags only; None (counted) when it fails."""
    check.items_proposed += 1
    t = norm(text).strip()
    tags = [str(x) for x in dict.fromkeys(tags)]
    if not t or not tags or len(tags) > 3 or any(x not in evidence for x in tags):
        check.drop("tag")
        return None
    hay_units = [u for x in tags for u in evidence[x].units]
    hay = norm("\n".join(u.text for u in hay_units))
    if unsafe_text(t):
        check.drop("unsafe")
        return None
    if _FILLER.match(t) or len(t.split()) > max_words:
        check.drop("filler")
        return None
    spans = [s for s in value_spans(t)]
    nums = [s for s in spans if s.kind == "num"]
    check.values_proposed += len(nums)
    sources = [(u, evidence[x]) for x in tags for u in evidence[x].units]
    if any(not _value_supported(t, s, hay) for s in spans if s.kind != "num") or any(
            not _num_bound(t, nums, s, sources, bind=False) for s in nums):
        if not nums:                                    # the unsupported value is not a number:
            check.values_proposed += 1                  # it is still one proposed value
        check.drop("value", values=max(1, len(nums)))   # a value no cited source holds
        return None
    if not _names_ok(t, hay):
        check.drop("name", name=True, values=len(nums))
        return None
    if any(not _num_bound(t, nums, s, sources) for s in nums):
        check.drop("value", values=len(nums))           # B5: held, but not with ITS entity/metric
        return None
    hw = set(_CONTENT.findall(hay.lower())) - _FUNCTION
    if not (set(_CONTENT.findall(t.lower())) - _FUNCTION) & hw:
        check.drop("unbound", values=len(nums))
        return None
    check.items_kept += 1
    check.values_kept += len(nums)
    return {"text": t, "tags": tags}


def cell_numeric(text: str) -> bool:
    return len(_nums(norm(text))) == 1


def check_cell(text: str, tags: Sequence[str], row_label: str, evidence: Mapping[str, Evidence],
               check: Check, *, is_label: bool, column: str = "") -> Optional[dict[str, Any]]:
    """A table cell, bound to its ROW: the row label's tokens and the cell are in ONE unit (the
    same CSV row or sentence) of the cell's own tag. A numeric cell is checked like a chart point
    with the row label as its label. Empty cells pass as empty.

    Round 4 B6: bound to its COLUMN too (``column``, the checked column label; "" for none): a
    numeric cell as a chart point with the column label as its metric; a text cell in a CSV row
    must sit in a cell whose header (or own text) holds the column label's words; in prose the
    sentence must hold them."""
    t = norm(text).strip()
    tags = [str(x) for x in dict.fromkeys(tags)]
    if not t:
        return {"text": "", "tags": [], "numeric": False}
    check.items_proposed += 1
    if len(tags) != 1 or tags[0] not in evidence:
        if cell_numeric(t):                 # a dropped value is a proposed one too (fix #7)
            check.values_proposed += 1
        check.drop("tag", value=cell_numeric(t))
        return None
    ev = evidence[tags[0]]
    if unsafe_text(t):
        check.drop("unsafe")
        return None
    nums = _nums(t)
    if len(nums) == 1 and not is_label:
        check.values_proposed += 1
        found, why = _find_cell_number(row_label, t, ev, column)
        if found is None:
            check.drop(why, value=True)
            return None
        check.values_kept += 1
        check.items_kept += 1
        return {"text": t, "tags": tags, "numeric": True}
    toks = label_tokens(t)
    rtoks = label_tokens(row_label) if not is_label else []
    cneed = metric_tokens(column) if column and not is_label else set()
    for unit in ev.units:
        if unit.row is not None and unit.row < 0 and not is_label:
            continue
        line = norm(unit.text)
        lw = set(words(line))
        if toks:
            ok = set(toks) <= lw
        else:
            ok = t in line
        if ok and cneed:
            if unit.row is not None and unit.row >= 0:      # the cell sits under that column
                ok = any((set(toks) <= set(words(c)) if toks else t in norm(c))
                         and cneed <= wordset(_header(ev, i)) | wordset(c)
                         for i, c in enumerate(unit.cells))
            else:
                ok = cneed <= wordset(line)
        if ok and set(rtoks) <= lw and all(_value_supported(t, s, line) for s in value_spans(t)):
            check.items_kept += 1
            return {"text": t, "tags": tags, "numeric": False}
    if nums:                                # a dropped value is a proposed one too (fix #7)
        check.values_proposed += 1
    check.drop("not_found", value=bool(nums))
    return None


def _find_cell_number(row_label: str, cell: str, ev: Evidence, column: str = ""
                      ) -> tuple[Optional[Found], str]:
    """A numeric cell: its own number is the value, the row label is the label, the column label
    is the metric (round 4 B6)."""
    n = _nums(cell)[0]
    try:
        value: Any = float(Decimal(n.key)) if "." in n.key else int(n.key)
    except (InvalidOperation, ValueError):
        return None, "value"
    if value_key(value) != n.key:
        return None, "value"
    return find_number(row_label, value, cell, ev, metric=column)


__all__ = ["Check", "Found", "find_number", "check_chart", "check_sentence", "check_cell",
           "check_short_text", "unit_of", "value_key", "ambiguous_at", "period_key", "norm",
           "clause", "claim_tokens", "metric_tokens", "wordset",
           "label_tokens", "unsafe_text", "cell_numeric", "words"]
