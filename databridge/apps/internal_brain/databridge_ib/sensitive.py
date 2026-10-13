"""Sensitive-value masking at ingest: card numbers, card security codes, Singapore NRIC/FIN, IBANs.

``mask(text) -> (masked_text, findings)`` is a pure function: no I/O after the rules are loaded, no
network, no model. The ingestor applies it to every document BEFORE the index (and so before
embeddings, the narrator prompt, answers and citations) ever holds the text:

* PAN (13-19 digits, single space/dash separators, valid issuer prefix incl. Maestro 50/56-59,
  **Luhn-valid**) becomes first 6 + ``••••••`` + last 4: the PCI DSS v4.0.1 req. 3.4.1 display limit
  (BIN + last four). EVERY card in a run of adjacent cards is masked (sec3b #7).
* ``CVV: 123`` becomes ``CVV: •••``. Sensitive authentication data is never shown.
* Singapore NRIC/FIN ``[STFGM]\\d{7}[A-Z]`` (any case) becomes ``•••••`` + last 3 digits + checksum
  letter, the partial form in the PDPC NRIC advisory guidelines. Shape only (no checksum): masking a
  look-alike is the conservative error (sec3b #21, documented).
* IBAN (any case, **mod-97-valid**) becomes country + check digits + ``••••`` + last 4.

Matching runs on a NORMALISED VIEW of the text that has the same length as the original (so every
finding's offsets point into the original): NBSP and the other Unicode space characters, tabs,
newlines and zero-width characters become a plain space, Unicode dashes become ``-`` and full-width
digits become ASCII digits (sec3b #8, #17). Card boundaries are "not next to another digit", so a
card glued to a label, an underscore, a dash, quotes or brackets is still found.

Work is bounded: a candidate is cut into separator-delimited groups and scanned left to right with
windows no longer than ``max_len`` (≤ ``MAX_RULE_LEN``) — O(n · max_len²) for a candidate of n
characters, never the cubic all-pairs search (sec3b #9). Configured regexes are length-capped and
may not nest a repetition (or an alternation) inside another repetition, the classic catastrophic-
backtracking shape (sec3b #18).

The rules are DATA: ``packs/enterprise/sensitive.yaml`` (schema in that file). When the file is
absent, or present with no rules, the built-in ``DEFAULT_RULES`` (the same rules) apply; a built-in
kind the file leaves out is appended from the defaults, so a config can never switch a protection
off; a present but malformed file raises, and so does a rule whose mask would keep the whole
secret (sec3b #10).

Findings never carry the raw value (not even a hash: a card's middle six digits are brute-forceable
from BIN + last 4 + Luhn). They carry the rule, the kind, the span in the ORIGINAL text and the
masked form, so a data owner can be told where to redact at source.

The masks are idempotent: no masked form matches any rule, so ``mask(mask(t)[0])[0] == mask(t)[0]``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

#: databridge/packs/enterprise/sensitive.yaml (this file is databridge/apps/internal_brain/databridge_ib/)
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "packs" / "enterprise" / "sensitive.yaml"

#: hard caps on configured rules (sec3b #9, #18)
MAX_PATTERN_LEN = 512
MAX_RULE_LEN = 256

DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {"id": "iban", "kind": "iban",
     "pattern": r"(?i)(?<![A-Z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Z0-9])",
     "separators": " ", "min_len": 15, "max_len": 34, "prefix": r"(?i)^[A-Z]{2}\d{2}",
     "validate": "mod97",
     "mask": {"keep_head": 4, "fill": "••••", "keep_tail": 4}},
    {"id": "pan", "kind": "pan", "pattern": r"(?<!\d)\d(?:[ -]?\d)+(?!\d)",
     "separators": " -", "min_len": 13, "max_len": 19,
     "prefix": r"^(?:4|5[0-9]|2[2-7]|3[0456789]|6)", "validate": "luhn",
     "mask": {"keep_head": 6, "fill": "••••••", "keep_tail": 4}},
    {"id": "card_security_code", "kind": "card_security_code",
     "pattern": r"(?i)\b(?:CVV2?|CVC2?|CID|CSC)\b\s*[:#=]?\s*(\d{3,4})\b", "group": 1,
     "validate": "none", "mask": {"keep_head": 0, "fill": "•••", "keep_tail": 0}},
    {"id": "sg_nric", "kind": "sg_nric", "pattern": r"(?i)(?<![A-Z0-9])[STFGM]\d{7}[A-Z](?![A-Z0-9])",
     "min_len": 9, "max_len": 9, "validate": "none",
     "mask": {"keep_head": 0, "fill": "•••••", "keep_tail": 4}},
)

_RULE_KEYS = {"id", "kind", "pattern", "group", "separators", "min_len", "max_len", "prefix",
              "validate", "mask"}
_MASK_KEYS = {"keep_head", "fill", "keep_tail"}

# ---------------------------------------------------------------------------------------------
# Normalised view (same length as the input: offsets are preserved)
# ---------------------------------------------------------------------------------------------
_SPACES = ("\t\n\r\v\f            "
           "     　​‌‍⁠﻿")
_DASHES = "‐‑‒–—―−﹣－"
_NORM = {ord(c): " " for c in _SPACES}
_NORM.update({ord(c): "-" for c in _DASHES})
_NORM.update({0xFF10 + i: str(i) for i in range(10)})          # full-width digits


def normalise(text: str) -> str:
    """The matching view of ``text``: one output character per input character."""
    return text.translate(_NORM)


# ---------------------------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------------------------
def luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mod97_valid(iban: str) -> bool:
    s = iban.upper()
    if len(s) < 5 or not s.isascii() or not s.isalnum():
        return False
    rearranged = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1
    except ValueError:
        return False


_NRIC_W = (2, 7, 6, 5, 4, 3, 2)
_NRIC_ST = "JZIHGFEDCBA"
_NRIC_FG = "XWUTRQPNMLK"
_NRIC_M = "XWUTRQPNJLK"


def nric_valid(value: str) -> bool:
    """Singapore NRIC/FIN checksum (S/T citizens and PRs, F/G/M foreigners)."""
    v = value.upper()
    if not re.fullmatch(r"[STFGM]\d{7}[A-Z]", v):
        return False
    total = sum(int(d) * w for d, w in zip(v[1:8], _NRIC_W))
    total += {"S": 0, "T": 4, "F": 0, "G": 4, "M": 3}[v[0]]
    table = _NRIC_ST if v[0] in "ST" else (_NRIC_M if v[0] == "M" else _NRIC_FG)
    return table[total % 11] == v[-1]


VALIDATORS: dict[str, Optional[Callable[[str], bool]]] = {
    "none": None, "luhn": luhn_valid, "mod97": mod97_valid, "nric": nric_valid}


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    pattern: re.Pattern
    group: int
    separators: str
    min_len: int
    max_len: int
    prefix: Optional[re.Pattern]
    validate: Optional[Callable[[str], bool]]
    keep_head: int
    fill: str
    keep_tail: int
    splitter: Optional[re.Pattern] = field(default=None, compare=False, repr=False)

    def masked(self, compact: str) -> str:
        head = compact[: self.keep_head] if self.keep_head else ""
        tail = compact[len(compact) - self.keep_tail:] if self.keep_tail else ""
        return f"{head}{self.fill}{tail}"

    def accepts(self, compact: str) -> bool:
        if not (self.min_len <= len(compact) <= self.max_len):
            return False
        if self.prefix is not None and not self.prefix.search(compact):
            return False
        return self.validate is None or self.validate(compact)


try:                                                # Python 3.11+
    from re import _parser as _sre_parse            # type: ignore[attr-defined]
except ImportError:                                 # pragma: no cover - older Pythons
    import sre_parse as _sre_parse                  # type: ignore[no-redef]

_REPEATS = {_sre_parse.MAX_REPEAT, _sre_parse.MIN_REPEAT}
if hasattr(_sre_parse, "POSSESSIVE_REPEAT"):
    _REPEATS.add(_sre_parse.POSSESSIVE_REPEAT)


def _children(op: Any, av: Any) -> list[Any]:
    """Sub-patterns of one parsed node (only the node kinds that can hold a sub-pattern)."""
    if op in _REPEATS:
        return [av[2]]
    if op == _sre_parse.SUBPATTERN:
        return [av[-1]]
    if op == _sre_parse.BRANCH:
        return list(av[1])
    if op in (_sre_parse.ASSERT, _sre_parse.ASSERT_NOT):
        return [av[1]]
    if op == getattr(_sre_parse, "ATOMIC_GROUP", object()):
        return [av]
    if op == _sre_parse.GROUPREF_EXISTS:
        return [x for x in av[1:] if x is not None]
    return []


def _has_multi(p: Any) -> bool:
    """True if ``p`` contains a repetition that can match more than once, or an alternation."""
    for op, av in p:
        if op in _REPEATS and av[1] > 1:
            return True
        if op == _sre_parse.BRANCH:
            return True
        if any(_has_multi(c) for c in _children(op, av)):
            return True
    return False


def _check_regex(src: str, what: str, rule_id: str) -> re.Pattern:
    if len(src) > MAX_PATTERN_LEN:
        raise ValueError(f"sensitive rule {rule_id!r}: {what} longer than {MAX_PATTERN_LEN} chars")
    try:
        parsed = _sre_parse.parse(src)
        compiled = re.compile(src)
    except re.error as exc:
        raise ValueError(f"sensitive rule {rule_id!r}: bad {what}: {exc}") from exc

    def walk(p: Any) -> None:
        for op, av in p:
            if op in _REPEATS and av[1] > 1 and _has_multi(av[2]):
                raise ValueError(f"sensitive rule {rule_id!r}: {what} nests a repetition or "
                                 "alternation inside a repetition (catastrophic backtracking)")
            for c in _children(op, av):
                walk(c)

    walk(parsed)
    return compiled


def _int(r: dict[str, Any], key: str, default: int, rid: str) -> int:
    v = r.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"sensitive rule {rid!r}: {key} must be an integer")
    return v


def compile_rules(raw: Sequence[dict[str, Any]]) -> tuple[Rule, ...]:
    out = []
    seen: set[str] = set()
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ValueError(f"sensitive rule #{i} is not a mapping")
        unknown = set(r) - _RULE_KEYS
        if unknown:
            raise ValueError(f"sensitive rule {r.get('id', i)!r}: unknown keys {sorted(unknown)}")
        for req in ("id", "kind", "pattern", "mask"):
            if req not in r:
                raise ValueError(f"sensitive rule #{i}: missing {req!r}")
        rid = str(r["id"])
        if rid in seen:
            raise ValueError(f"duplicate sensitive rule id {rid!r}")
        seen.add(rid)
        m = r["mask"]
        if not isinstance(m, dict) or set(m) - _MASK_KEYS or "fill" not in m:
            raise ValueError(f"sensitive rule {rid!r}: bad mask {m!r}")
        validate = str(r.get("validate") or "none")
        if validate not in VALIDATORS:
            raise ValueError(f"sensitive rule {rid!r}: unknown validate {validate!r}")
        fill = str(m["fill"])
        if not fill or any(ch.isalnum() for ch in fill):
            raise ValueError(f"sensitive rule {rid!r}: fill must be non-empty, no letters/digits")
        pattern = _check_regex(str(r["pattern"]), "pattern", rid)
        prefix = _check_regex(str(r["prefix"]), "prefix", rid) if r.get("prefix") else None
        group = _int(r, "group", 0, rid)
        if not 0 <= group <= pattern.groups:
            raise ValueError(f"sensitive rule {rid!r}: group {group} not in the pattern")
        keep_head = _int(m, "keep_head", 0, rid)
        keep_tail = _int(m, "keep_tail", 0, rid)
        min_len = _int(r, "min_len", 1, rid)
        max_len = _int(r, "max_len", MAX_RULE_LEN, rid)
        if keep_head < 0 or keep_tail < 0:
            raise ValueError(f"sensitive rule {rid!r}: keep_head/keep_tail must be >= 0")
        if not 1 <= min_len <= max_len <= MAX_RULE_LEN:
            raise ValueError(f"sensitive rule {rid!r}: need 1 <= min_len <= max_len <= {MAX_RULE_LEN}")
        if keep_head + keep_tail and ("min_len" not in r or keep_head + keep_tail >= min_len):
            # the mask must hide part of EVERY accepted value (sec3b #10)
            raise ValueError(f"sensitive rule {rid!r}: keep_head + keep_tail must be < an explicit "
                             "min_len (the mask would keep the whole value)")
        seps = str(r.get("separators") or "")
        out.append(Rule(
            id=rid, kind=str(r["kind"]), pattern=pattern, group=group, separators=seps,
            min_len=min_len, max_len=max_len, prefix=prefix, validate=VALIDATORS[validate],
            keep_head=keep_head, fill=fill, keep_tail=keep_tail,
            splitter=re.compile(f"[^{re.escape(seps)}]+") if seps else None))
    return tuple(out)


def load_rules(path: Optional[Path] = None) -> tuple[Rule, ...]:
    """Rules from ``path`` (default ``packs/enterprise/sensitive.yaml``); built-ins if it is absent
    or lists no rules. A built-in kind the file omits is appended from the defaults."""
    p = Path(path) if path is not None else DEFAULT_PATH
    if not p.is_file():
        return compile_rules(DEFAULT_RULES)
    import yaml                                     # local: only needed when the file exists
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or set(data) - {"version", "pack", "rules"}:
        raise ValueError(f"{p}: expected keys version/pack/rules")
    if data.get("version") != 1:
        raise ValueError(f"{p}: unsupported version {data.get('version')!r}")
    raw = data.get("rules")
    if raw is None or raw == []:
        return compile_rules(DEFAULT_RULES)         # never zero rules
    if not isinstance(raw, list):
        raise ValueError(f"{p}: rules must be a list")
    kinds = {str(r.get("kind")) for r in raw if isinstance(r, dict)}
    ids = {str(r.get("id")) for r in raw if isinstance(r, dict)}
    missing = [d for d in DEFAULT_RULES if d["kind"] not in kinds and d["id"] not in ids]
    return compile_rules(list(raw) + missing)


@lru_cache(maxsize=1)
def default_rules() -> tuple[Rule, ...]:
    return load_rules()


# ---------------------------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------------------------
def _runs(rule: Rule, view: str, start: int, end: int) -> list[tuple[int, int, str]]:
    """Every accepted, non-overlapping run of whole separator-delimited groups in view[start:end],
    left to right; at each start the LONGEST accepted run wins → [(abs_start, abs_end, compact)].
    Windows never exceed ``max_len`` characters, so the scan is linear in the candidate."""
    seg = view[start:end]
    if rule.splitter is None:
        return [(start, end, seg)] if rule.accepts(seg) else []
    groups = [(m.start(), m.end()) for m in rule.splitter.finditer(seg)]
    out: list[tuple[int, int, str]] = []
    i, n = 0, len(groups)
    while i < n:
        best: Optional[tuple[int, str]] = None
        parts: list[str] = []
        total = 0
        j = i
        while j < n:
            a, b = groups[j]
            total += b - a
            if total > rule.max_len:
                break
            parts.append(seg[a:b])
            if total >= rule.min_len:
                compact = "".join(parts)
                if rule.accepts(compact):
                    best = (j, compact)
            j += 1
        if best is None:
            i += 1
            continue
        j, compact = best
        out.append((start + groups[i][0], start + groups[j][1], compact))
        i = j + 1
    return out


def mask(text: str, rules: Optional[Sequence[Rule]] = None) -> tuple[str, list[dict[str, Any]]]:
    """Mask every sensitive value in ``text``. Returns the masked text and one finding per masked
    span: ``{"rule", "kind", "start", "end", "masked"}`` (offsets into the ORIGINAL text; no raw
    value). Rules run in order over the normalised view; the first rule to claim a span wins."""
    if not text:
        return text or "", []
    rules = default_rules() if rules is None else rules
    view = normalise(text)
    taken = bytearray(len(text))
    claimed: list[tuple[int, int, Rule, str]] = []
    for rule in rules:
        for m in rule.pattern.finditer(view):
            gs, ge = m.span(rule.group)
            if gs < 0:
                continue
            for s, e, compact in _runs(rule, view, gs, ge):
                if not any(taken[s:e]):
                    taken[s:e] = b"\x01" * (e - s)
                    claimed.append((s, e, rule, rule.masked(compact)))
    if not claimed:
        return text, []
    claimed.sort(key=lambda c: c[0])
    out, pos, findings = [], 0, []
    for s, e, rule, masked in claimed:
        out.append(text[pos:s])
        out.append(masked)
        pos = e
        findings.append({"rule": rule.id, "kind": rule.kind, "start": s, "end": e,
                         "masked": masked})
    out.append(text[pos:])
    return "".join(out), findings


#: SourceDoc string fields that are identifiers / pointers, never free text (not masked: masking
#: them would break lookups, and they are produced by the connectors, not typed by people)
MASK_EXEMPT_FIELDS = frozenset({"doc_id", "platform", "kind", "container", "version", "deep_link",
                                "author"})


def mask_doc(doc: Any, rules: Optional[Sequence[Rule]] = None) -> Any:
    """A connector ``SourceDoc`` with EVERY free-text string field masked — title, text,
    container_name and any string field added later — except the identifiers / links / version
    in ``MASK_EXEMPT_FIELDS`` (sec3b #19). Same object when nothing matched. The one call the
    ingest path makes before ``Index.upsert``."""
    update: dict[str, str] = {}
    for name in type(doc).model_fields:
        if name in MASK_EXEMPT_FIELDS:
            continue
        value = getattr(doc, name)
        if isinstance(value, str) and value:
            masked, found = mask(value, rules)
            if found:
                update[name] = masked
    if not update:
        return doc
    return doc.model_copy(update=update)


__all__ = ["mask", "mask_doc", "load_rules", "compile_rules", "default_rules", "Rule", "normalise",
           "luhn_valid", "mod97_valid", "nric_valid", "DEFAULT_RULES", "DEFAULT_PATH",
           "MASK_EXEMPT_FIELDS", "MAX_PATTERN_LEN", "MAX_RULE_LEN"]
