"""Value-shaped tokens in prose, read by what they ARE — an identifier, a number, or a bare token.

FIN-08 (finance dry run, 2026-10-02). The answer checks compared the values a narration states
against the evidence by SPELLING, through a tokeniser that split on commas and read a hyphen as a
minus sign:

* ``33,600,000.00`` became ``33`` / ``600`` / ``000.00`` and failed against evidence holding
  ``33600000.00`` — a correct total was refused;
* ``CM-2026-0029`` became ``-2026`` / ``-0029`` (two negative numbers), and the second one was not
  "covered" because it touched a digit — a correct identifier was refused.

This module reads both sides of a comparison the same way:

* an **identifier** — letters AND digits joined by separators (``CM-2026-0029``, ``CB-F-000302``,
  ``v1.2``) — is one whole token, compared case-insensitively with typographic dashes folded to
  ``-``. Its digits are never read as numbers, and its hyphens never as signs;
* a **number** is compared by ``Decimal`` VALUE: thousands separators and trailing zeros do not
  matter (``33,600,000.00`` == ``33600000``), a currency/unit code written flush against it is
  split off when the figure is grouped or decimal (``HKD33,600,000.00``;
  ``WELL2`` stays one token), and a sign counts only when the ``-`` does not follow a word
  character (``10-20`` is two positive numbers; ``-500`` is negative). A different value is a
  different key, so it is still refused;
* an ISO date is one **token** (``2025-12-31``), never three numbers;
* anything else that carries a digit is a **token**, compared as the callers always did.

Pure and domain-free; no caller-specific vocabulary.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

#: Typographic dashes a writer (or a model) uses where the source has an ASCII hyphen. Every one is
#: a single code point, so folding preserves string offsets.
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—−﹣－"})

#: An ISO date (optionally with a time) is ONE value, compared as written — never three numbers.
_DATE = re.compile(r"(?<![A-Za-z0-9])\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?(?![A-Za-z0-9])")
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:[-_/.:][A-Za-z0-9]+)+")
#: A code flush against an amount (``HKD33600000.00``) is a NUMBER with a code, not an identifier.
_CODED_AMOUNT = re.compile(r"[A-Z]{2,4}\d[\d,]*(?:\.\d+)?")
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9.,])"
    r"(?:(?P<code>[A-Z]{2,4})|(?P<sign>-))?"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?![\d,.]?\d)")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9./:_-]*")
_TOKEN_STRIP = ".,;:!?)(\"'-/"


class ValueSpan(NamedTuple):
    start: int
    end: int
    raw: str          # as written in the ORIGINAL text
    kind: str         # "id" | "num" | "tok"
    key: str          # what two spans are compared by


def fold_dashes(text: str) -> str:
    return str(text or "").translate(_DASHES)


def number_key(num: str, *, negative: bool = False) -> str:
    """``"33,600,000.00"`` → ``"33600000"``; ``"09"`` → ``"9"``; ``-0`` → ``"0"``."""
    try:
        d = Decimal(num.replace(",", "")).normalize()
    except (InvalidOperation, ValueError):
        return num.replace(",", "")
    if negative:
        d = -d
    if d == 0:
        d = Decimal(0)
    return format(d, "f")


def _is_identifier(raw: str) -> bool:
    return (any(c.isalpha() for c in raw) and any(c.isdigit() for c in raw)
            and not _CODED_AMOUNT.fullmatch(raw))


def value_spans(text: str) -> list[ValueSpan]:
    """Every digit-bearing value in ``text``, in order, each classified and keyed."""
    original = str(text or "")
    folded = fold_dashes(original)
    spans: list[ValueSpan] = []
    masked = list(folded)

    for m in _DATE.finditer(folded):
        spans.append(ValueSpan(m.start(), m.end(), original[m.start():m.end()], "tok",
                               m.group(0).lower()))
        masked[m.start():m.end()] = " " * (m.end() - m.start())
    folded_ids = "".join(masked)

    for m in _IDENTIFIER.finditer(folded_ids):
        if _is_identifier(m.group(0)):
            spans.append(ValueSpan(m.start(), m.end(), original[m.start():m.end()], "id",
                                   m.group(0).lower()))
            masked[m.start():m.end()] = " " * (m.end() - m.start())
    after_ids = "".join(masked)

    for m in _NUMBER.finditer(after_ids):
        if m.group("code") and not re.search(r"[,.]", m.group("num")):
            # ``WELL2`` / ``PEE226`` are codes, not an amount with a currency: only a grouped or
            # decimal figure flush against capitals is read as a coded amount. The rest stays one
            # whole token (the scan below).
            continue
        num_start = m.start("num")
        spans.append(ValueSpan(num_start if not m.group("sign") else m.start(), m.end(),
                               original[(m.start("sign") if m.group("sign") else num_start):m.end()],
                               "num", number_key(m.group("num"), negative=bool(m.group("sign")))))
        masked[m.start():m.end()] = " " * (m.end() - m.start())
    rest = "".join(masked)

    for m in _TOKEN.finditer(rest):
        tok = m.group(0)
        stripped = tok.strip(_TOKEN_STRIP)
        if not any(c.isdigit() for c in stripped):
            continue
        lead = len(tok) - len(tok.lstrip(_TOKEN_STRIP))
        start = m.start() + lead
        spans.append(ValueSpan(start, start + len(stripped), original[start:start + len(stripped)],
                               "tok", stripped.lower()))
    return sorted(spans)


def number_keys(text: str) -> set[str]:
    return {s.key for s in value_spans(text) if s.kind == "num"}


def identifier_keys(text: str) -> set[str]:
    return {s.key for s in value_spans(text) if s.kind == "id"}


__all__ = ["ValueSpan", "fold_dashes", "identifier_keys", "number_key", "number_keys",
           "value_spans"]
