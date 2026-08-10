"""Per-field confidence blend + cross-field validation (C15, 09_FINAL_PRD §11.5).

``field_conf = f(extraction self-report, grounding match score, OCR word conf,
pre-extract lock=1.0)`` — blended, then routed by the SAME bands as the semantic layer (T07):
≥0.95 auto · 0.80–0.95 spot-check · <0.80 review. We REUSE ``databridge_semantic.confidence
.confidence_band`` so the two subsystems can never drift on the thresholds.

Cross-field validation applies the schema-declared rules (§11.5): date ordering, line-items
sum ≈ total within tolerance, enum membership, non-negativity. A violation flags the record
(and pulls the involved fields toward review).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from databridge_semantic.confidence import confidence_band  # SAME bands as T07 (§11.5)

from .grounding import _as_date, _to_decimal
from .target_model import CompiledTarget, CrossFieldRule

Band = str  # "auto" | "spot_check" | "review" (from confidence_band)


def blend_field_confidence(
    grounding_score: float,
    *,
    locked: bool = False,
    self_report: Optional[float] = None,
    ocr_word_conf: Optional[float] = None,
) -> float:
    """Blend the available confidence signals (§11.5).

    A pre-extract LOCKED field is 1.0 (deterministic, zero-token). Otherwise grounding is the
    DOMINANT signal (it is deterministic and is the anti-fabrication wall), nudged by the
    LLM self-report and any OCR word confidence — but a field can never be MORE confident than
    its grounding match (a barely-grounded value must not be auto-approved on a rosy
    self-report)."""
    if locked:
        # a LOCKED field is a deterministic exact capture (§11.1 lock=1.0) — but only auto-approve
        # at 1.0 when it ALSO grounded exactly; a lock that merely grounded fuzzily is capped at
        # its grounding score (don't let the lock overstate a weak match — review finding L7).
        return 1.0 if grounding_score >= 0.999 else grounding_score
    weights = [(grounding_score, 0.6)]
    if self_report is not None:
        weights.append((float(self_report), 0.25))
    if ocr_word_conf is not None:
        weights.append((float(ocr_word_conf), 0.15))
    wsum = sum(w for _v, w in weights)
    blended = sum(v * w for v, w in weights) / wsum if wsum else grounding_score
    return max(0.0, min(blended, grounding_score))     # cap at grounding (conservative)


def band_of(confidence: float) -> Band:
    return confidence_band(confidence)


# --- cross-field validation ----------------------------------------------------
@dataclass
class CrossFieldViolation:
    rule: str
    fields: tuple[str, ...]
    detail: str


def _num(v: Any) -> Optional[Decimal]:
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    return _to_decimal(v) if v is not None else None


def validate_cross_fields(record: dict, compiled: CompiledTarget) -> list[CrossFieldViolation]:
    """Apply the schema's cross-field rules + automatic enum membership. Returns violations."""
    out: list[CrossFieldViolation] = []
    # enum membership (automatic, from every enum FieldSpec)
    for fs in compiled.scalar_fields:
        if fs.enum and record.get(fs.name) is not None:
            if record[fs.name] not in fs.enum:
                out.append(CrossFieldViolation(
                    "enum", (fs.name,), f"{record[fs.name]!r} not in {list(fs.enum)}"))
    for rule in compiled.cross_field_rules:
        out.extend(_apply_rule(rule, record))
    return out


def _apply_rule(rule: CrossFieldRule, record: dict) -> list[CrossFieldViolation]:
    if rule.kind == "date_order" and len(rule.fields) == 2:
        a, b = (_as_date(record.get(rule.fields[0])), _as_date(record.get(rule.fields[1])))
        if a is not None and b is not None and a > b:
            return [CrossFieldViolation("date_order", rule.fields,
                                        f"{rule.fields[0]} ({a}) > {rule.fields[1]} ({b})")]
    elif rule.kind == "nonneg":
        bad = []
        for f in rule.fields:
            n = _num(record.get(f))
            if n is not None and n < 0:
                bad.append(f)
        if bad:
            return [CrossFieldViolation("nonneg", tuple(bad), "negative value(s)")]
    elif rule.kind == "sum_to_total":
        items = record.get(rule.items) or []
        total = _num(record.get(rule.total))
        if total is not None and isinstance(items, list) and items:
            s = sum((_num(it.get(rule.item_amount)) or Decimal(0))
                    for it in items if isinstance(it, dict))
            if abs(s - total) > Decimal(str(rule.tolerance)):
                return [CrossFieldViolation(
                    "sum_to_total", (rule.items, rule.total),
                    f"Σ {rule.item_amount} = {s} ≠ {rule.total} = {total} (tol {rule.tolerance})")]
    return []
