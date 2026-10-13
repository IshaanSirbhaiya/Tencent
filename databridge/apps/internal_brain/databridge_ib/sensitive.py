"""Sensitive-value masking at ingest: card numbers, card security codes, Singapore NRIC/FIN, IBANs.

``mask(text) -> (masked_text, findings)`` is a pure function: no I/O after the rules are loaded, no
network, no model. The ingestor applies it to every document BEFORE the index (and so before
embeddings, the narrator prompt, answers and citations) ever holds the text:

* PAN (13-19 digits, single space/dash separators, valid issuer prefix, **Luhn-valid**) becomes
  first 6 + ``••••••`` + last 4: the PCI DSS v4.0.1 req. 3.4.1 display limit (BIN + last four).
* ``CVV: 123`` becomes ``CVV: •••``. Sensitive authentication data is never shown.
* Singapore NRIC/FIN ``[STFGM]\\d{7}[A-Z]`` becomes ``•••••`` + last 3 digits + checksum letter, the
  partial form in the PDPC NRIC advisory guidelines.
* IBAN (**mod-97-valid**) becomes country + check digits + ``••••`` + last 4.

The rules are DATA: ``packs/enterprise/sensitive.yaml`` (schema in that file). When the file is
absent, the built-in ``DEFAULT_RULES`` (the same rules) apply; a present but malformed file raises.

Findings never carry the raw value (not even a hash: a card's middle six digits are brute-forceable
from BIN + last 4 + Luhn). They carry the rule, the kind, the span in the ORIGINAL text and the
masked form, so a data owner can be told where to redact at source.

The masks are idempotent: no masked form matches any rule, so ``mask(mask(t)[0])[0] == mask(t)[0]``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

#: databridge/packs/enterprise/sensitive.yaml (this file is databridge/apps/internal_brain/databridge_ib/)
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "packs" / "enterprise" / "sensitive.yaml"

DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {"id": "iban", "kind": "iban", "pattern": r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b",
     "separators": " ", "min_len": 15, "max_len": 34, "prefix": r"^[A-Z]{2}\d{2}",
     "validate": "mod97",
     "mask": {"keep_head": 4, "fill": "••••", "keep_tail": 4}},
    {"id": "pan", "kind": "pan", "pattern": r"(?<![\w-])\d(?:[ -]?\d)+(?![\w-])",
     "separators": " -", "min_len": 13, "max_len": 19,
     "prefix": r"^(?:4|5[1-5]|2[2-7]|3[0456789]|6)", "validate": "luhn",
     "mask": {"keep_head": 6, "fill": "••••••", "keep_tail": 4}},
    {"id": "card_security_code", "kind": "card_security_code",
     "pattern": r"(?i)\b(?:CVV2?|CVC2?|CID|CSC)\b\s*[:#=]?\s*(\d{3,4})\b", "group": 1,
     "validate": "none", "mask": {"keep_head": 0, "fill": "•••", "keep_tail": 0}},
    {"id": "sg_nric", "kind": "sg_nric", "pattern": r"\b[STFGM]\d{7}[A-Z]\b", "validate": "none",
     "mask": {"keep_head": 0, "fill": "•••••", "keep_tail": 4}},
)

_RULE_KEYS = {"id", "kind", "pattern", "group", "separators", "min_len", "max_len", "prefix",
              "validate", "mask"}
_MASK_KEYS = {"keep_head", "fill", "keep_tail"}


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
    if len(s) < 5 or not s.isalnum():
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
        if r["id"] in seen:
            raise ValueError(f"duplicate sensitive rule id {r['id']!r}")
        seen.add(r["id"])
        m = r["mask"]
        if not isinstance(m, dict) or set(m) - _MASK_KEYS or "fill" not in m:
            raise ValueError(f"sensitive rule {r['id']!r}: bad mask {m!r}")
        validate = str(r.get("validate") or "none")
        if validate not in VALIDATORS:
            raise ValueError(f"sensitive rule {r['id']!r}: unknown validate {validate!r}")
        fill = str(m["fill"])
        if any(ch.isalnum() for ch in fill):
            raise ValueError(f"sensitive rule {r['id']!r}: fill must not contain letters/digits")
        out.append(Rule(
            id=str(r["id"]), kind=str(r["kind"]), pattern=re.compile(str(r["pattern"])),
            group=int(r.get("group", 0)), separators=str(r.get("separators") or ""),
            min_len=int(r.get("min_len", 1)), max_len=int(r.get("max_len", 10_000)),
            prefix=re.compile(str(r["prefix"])) if r.get("prefix") else None,
            validate=VALIDATORS[validate], keep_head=int(m.get("keep_head", 0)), fill=fill,
            keep_tail=int(m.get("keep_tail", 0))))
    return tuple(out)


def load_rules(path: Optional[Path] = None) -> tuple[Rule, ...]:
    """Rules from ``path`` (default ``packs/enterprise/sensitive.yaml``); built-ins if it is absent."""
    p = Path(path) if path is not None else DEFAULT_PATH
    if not p.is_file():
        return compile_rules(DEFAULT_RULES)
    import yaml                                     # local: only needed when the file exists
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or set(data) - {"version", "pack", "rules"}:
        raise ValueError(f"{p}: expected keys version/pack/rules")
    if data.get("version") != 1:
        raise ValueError(f"{p}: unsupported version {data.get('version')!r}")
    return compile_rules(data.get("rules") or [])


@lru_cache(maxsize=1)
def default_rules() -> tuple[Rule, ...]:
    return load_rules()


# ---------------------------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------------------------
def _best_run(rule: Rule, text: str, start: int, end: int) -> Optional[tuple[int, int, str]]:
    """Within text[start:end], the earliest-starting, longest run of whole separator-delimited
    groups that the rule accepts → (abs_start, abs_end, compact)."""
    seg = text[start:end]
    if not rule.separators:
        return (start, end, seg) if rule.accepts(seg) else None
    groups = [(m.start(), m.end()) for m in
              re.finditer(f"[^{re.escape(rule.separators)}]+", seg)]
    for i in range(len(groups)):
        for j in range(len(groups) - 1, i - 1, -1):
            s, e = groups[i][0], groups[j][1]
            compact = "".join(seg[a:b] for a, b in groups[i:j + 1])
            if len(compact) < rule.min_len:
                break                               # shorter runs from this start only shrink
            if rule.accepts(compact):
                return start + s, start + e, compact
    return None


def mask(text: str, rules: Optional[Sequence[Rule]] = None) -> tuple[str, list[dict[str, Any]]]:
    """Mask every sensitive value in ``text``. Returns the masked text and one finding per masked
    span: ``{"rule", "kind", "start", "end", "masked"}`` (offsets into the ORIGINAL text; no raw
    value). Rules run in order over the original text; the first rule to claim a span wins."""
    if not text:
        return text or "", []
    rules = default_rules() if rules is None else rules
    claimed: list[tuple[int, int, Rule, str]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e, _r, _m in claimed)

    for rule in rules:
        for m in rule.pattern.finditer(text):
            gs, ge = m.span(rule.group)
            if gs < 0:
                continue
            hit = _best_run(rule, text, gs, ge)
            if hit is None:
                continue
            s, e, compact = hit
            if free(s, e):
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


def mask_doc(doc: Any, rules: Optional[Sequence[Rule]] = None) -> Any:
    """A connector ``SourceDoc`` with its title and text masked (same object when nothing matched).
    The one call the ingest path makes before ``Index.upsert``."""
    title, f1 = mask(doc.title, rules)
    text, f2 = mask(doc.text, rules)
    if not f1 and not f2:
        return doc
    return doc.model_copy(update={"title": title, "text": text})


__all__ = ["mask", "mask_doc", "load_rules", "compile_rules", "default_rules", "Rule",
           "luhn_valid", "mod97_valid", "nric_valid", "DEFAULT_RULES", "DEFAULT_PATH"]
