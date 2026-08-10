"""Deterministic claim check (C17, 09_FINAL_PRD §12.3a) — the S→U anti-fabrication wall.

Every number/date token in generated output must EXACTLY match a cited rendered fact
(longest-match-first). A hedge word on a quantity ("approximately 1,350", "about a hundred") is a
violation in dataset outputs — hedging launders a fabrication as an estimate.

This is the S→U counterpart of C15's grounding wall: there, every extracted value had to appear
in the source; here, every emitted quantity must equal a canonical fact. The trap is the same —
a value that LOOKS right but is invented — so the check is strict and adversarial by design.

Scope (deterministic, honest): the HARD gate is fact-SHAPED tokens — currency, percentages,
decimals, thousands-grouped numbers, 4+-digit integers, ISO dates, and spelled-out magnitudes
(hundred/thousand/…). Bare 1–3 digit integers in prose ("top 5") are structural, not flagged,
UNLESS hedged. Free-text NAME fabrication is the independent verifier's job (§12.3b); claim_check
owns the unambiguous numeric/date surface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# --- fact-shaped token patterns (what a fabricated quantity looks like) -----------
_MONEY = r"[$€£¥₹]\s?-?\d[\d,]*(?:\.\d+)?"
_PERCENT = r"-?\d[\d,]*(?:\.\d+)?\s?%"
_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_GROUPED = r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?"        # 1,234 / 1,234,567.89
_DECIMAL = r"-?\d+\.\d+"                            # 19.99
_LONG_INT = r"-?\d{4,}"                            # 2024, 12345 (4+ digits — fact-shaped)
_FACT_SHAPED = re.compile("|".join(
    f"(?:{p})" for p in (_MONEY, _PERCENT, _ISO_DATE, _GROUPED, _DECIMAL, _LONG_INT)))

# a quantity carrying an explicit UNIT word ("250 dollars", "8 percent") is fact-shaped even
# without a symbol — a symbol-less money/percent amount must still equal a cited fact (review HIGH).
_UNIT_NUMBER = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:dollars?|usd|euros?|eur|pounds?|gbp|yen|jpy|rupees?|inr|cents?|"
    r"percent|per\s?cent|pct)\b", re.IGNORECASE)

# spelled-out magnitudes (a "number" not in canonical digit form → suspect in a dataset output)
_NUMBER_WORDS = {"hundred", "thousand", "million", "billion", "trillion", "dozen", "dozens",
                 "hundreds", "thousands", "millions", "billions"}
_CARDINALS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
              "eleven", "twelve", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
              "eighty", "ninety"}

# hedge markers — "approximately"+number = violation (§12.3a). Covers single words AND the
# multi-word phrases that launder a fabricated count as an estimate (review HIGH).
_HEDGES = {"approximately", "approx", "about", "roughly", "around", "nearly", "almost",
           "circa", "~", "around", "ish", "or-so"}
_HEDGE_PHRASE = re.compile(
    r"(?:~|\b(?:approximately|approx|about|roughly|around|nearly|almost|circa|"
    r"upwards of|up to|as many as|as few as|give or take|north of|south of|in excess of|"
    r"a little over|a little under|just over|just under|close to|in the region of|"
    r"on the order of|somewhere around|or so)\b)\s*"
    r"(?:a\s+|an\s+)?"
    r"(?:[$€£¥₹]\s?)?\d[\d,]*(?:\.\d+)?%?|"
    r"(?:~|\b(?:approximately|approx|about|roughly|around|nearly|almost|circa|upwards of|"
    r"as many as|give or take|north of|in excess of|a little over|close to|"
    r"in the region of|or so)\b)\s+(?:a\s+|an\s+)?(?:hundred|thousand|million|billion|dozen)",
    re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]+|\d[\d,]*(?:\.\d+)?%?|[$€£¥₹]")


@dataclass
class ClaimViolation:
    kind: str                  # "fabricated_number" | "fabricated_date" | "hedged_quantity" | "spelled_number"
    token: str
    detail: str = ""


@dataclass
class ClaimCheckResult:
    ok: bool
    violations: list[ClaimViolation] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)     # fact forms found + accepted

    def __bool__(self) -> bool:
        return self.ok


def _mask_fact_forms(text: str, fact_forms: list[str]) -> tuple[str, list[str]]:
    """Replace each cited fact form's occurrences with spaces (longest-first so '$1,234,567.89'
    is consumed whole, not flagged piecewise). Returns the masked text + the forms actually seen.

    DIGIT-BOUNDARY aware: a fact form is masked only when it is NOT flanked by a digit — so the
    real fact '8.0%' does NOT mask the fabricated '18.0%' (which would leave a harmless '1' and let
    the fabrication escape). This is the S→U analogue of the C15 substring hole: never consume a
    fabrication as a substring of a real fact form."""
    seen: list[str] = []
    masked = list(text)
    for form in sorted(set(fact_forms), key=len, reverse=True):
        if not form:
            continue
        start = 0
        hit = False
        while True:
            # search the ORIGINAL text (stable indices); mask via the parallel char list
            i = text.find(form, start)
            if i < 0:
                break
            j = i + len(form)
            before_ok = i == 0 or not text[i - 1].isdigit()
            after_ok = j >= len(text) or not text[j].isdigit()
            if before_ok and after_ok:
                for k in range(i, j):
                    masked[k] = " "
                hit = True
            start = i + 1
        if hit:
            seen.append(form)
    return "".join(masked), seen


def _spelled_numbers(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z]+", text.lower()):
        if w in _NUMBER_WORDS:
            out.append(w)
    return out


def _hedged_quantities(text: str) -> list[str]:
    """A hedge marker (single word OR multi-word phrase) on a following quantity — digit, currency
    amount, percent, OR spelled magnitude (even a small count the residual scan ignores) →
    violation. Catches 'approximately $1,350', 'about a hundred', 'upwards of 50 cases'."""
    return [m.group(0).strip() for m in _HEDGE_PHRASE.finditer(text)]


def check_claims(text: str, facts: Iterable, *, allow: Iterable[str] = ()) -> ClaimCheckResult:
    """Check ``text`` against the cited ``facts`` (objects with a ``.rendered`` str, or raw strs).

    ``allow`` is an optional set of extra canonical forms permitted (e.g. structural constants the
    template legitimately emits). Returns ok=False with itemised violations on any ungrounded
    quantity, ungrounded date, hedged quantity, or spelled-out magnitude."""
    fact_forms = [getattr(f, "rendered", f) for f in facts]
    fact_forms = [f for f in fact_forms if isinstance(f, str)] + list(allow)

    masked, seen = _mask_fact_forms(text, fact_forms)
    violations: list[ClaimViolation] = []

    # 1) residual fact-shaped tokens in the MASKED text = not covered by any cited fact
    for m in _FACT_SHAPED.finditer(masked):
        tok = m.group(0).strip()
        if not tok:
            continue
        kind = "fabricated_date" if re.fullmatch(_ISO_DATE, tok) else "fabricated_number"
        violations.append(ClaimViolation(kind=kind, token=tok,
                                         detail="quantity/date not equal to any cited rendered fact"))

    # 2) symbol-less money / number+unit-word ("250 dollars", "8 percent") in the MASKED text —
    #    a fabricated amount that dodged the symbol patterns by spelling its unit (review HIGH)
    for m in _UNIT_NUMBER.finditer(masked):
        violations.append(ClaimViolation(kind="fabricated_number", token=m.group(0).strip(),
                                         detail="symbol-less quantity not equal to a cited fact"))

    # 3) spelled-out magnitudes anywhere (canonical facts are digits, so a magnitude word is suspect)
    for w in _spelled_numbers(masked):
        violations.append(ClaimViolation(kind="spelled_number", token=w,
                                         detail="spelled-out magnitude is not a canonical fact form"))

    # 4) hedged quantities (run on ORIGINAL text — a hedge on a REAL fact is still a violation)
    for h in _hedged_quantities(text):
        violations.append(ClaimViolation(kind="hedged_quantity", token=h,
                                         detail="hedging a quantity launders fabrication (§12.3a)"))

    return ClaimCheckResult(ok=not violations, violations=violations, covered=seen)
