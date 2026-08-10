"""Confidence blending + routing (06_PRD §4).

LLM self-reported confidence is miscalibrated and gameable, so the router never trusts
it alone. The BLEND combines self-report with (a) structural-hint agreement, (b) dtype
compatibility, and (c) a sparsity penalty — and ``unknown`` always collapses to 0.0
(routes to a human). Bands: ≥0.95 auto-approve · 0.80–0.95 spot-check · <0.80 review.

Calibration is itself evaluated (E1 reliability diagram): if the 0.9-bucket isn't ≥90%
accurate, fix the BLEND weights here, never the thresholds.
"""
from __future__ import annotations

from typing import Literal, Optional

from databridge_core import SemanticType

Band = Literal["auto", "spot_check", "review"]

AUTO_THRESHOLD = 0.95
REVIEW_THRESHOLD = 0.80

# Semantic types that should be DENSE — a sparse one is suspicious (06_PRD §4).
REQUIRES_DENSITY: frozenset[SemanticType] = frozenset({
    SemanticType.ENTITY_ID,
    SemanticType.FOREIGN_KEY,
    SemanticType.TIMESTAMP_CREATED,
    SemanticType.TIMESTAMP_EVENT,
    SemanticType.BOOLEAN_FLAG,
    SemanticType.AMOUNT_MONEY,
    SemanticType.PERSONAL_IDENTIFIER,
})

_INT = {"integer", "int", "bigint", "smallint", "serial", "bigserial", "uuid"}
_NUM = {"numeric", "decimal", "money", "float", "double", "real"}
_TS = {"timestamp", "timestamptz", "datetime", "date"}
_STR = {"varchar", "char", "text", "string"}

# Which dtypes are compatible with each semantic type (06_PRD §4: amount_money on
# numeric ✓, on varchar ✗).
DTYPE_COMPAT: dict[SemanticType, set[str]] = {
    SemanticType.ENTITY_ID: _INT,
    SemanticType.FOREIGN_KEY: _INT,
    SemanticType.TIMESTAMP_CREATED: _TS,
    SemanticType.TIMESTAMP_EVENT: _TS,
    SemanticType.AMOUNT_MONEY: _NUM,
    SemanticType.AMOUNT_COUNT: _INT | {"numeric"},
    SemanticType.SCORE_RATING: _NUM | {"integer"},
    SemanticType.DURATION_SECONDS: _INT | {"numeric"},
    SemanticType.BOOLEAN_FLAG: {"boolean", "bool"},
    SemanticType.STATUS_CATEGORICAL: _STR | {"integer", "smallint"},
    SemanticType.FREE_TEXT: _STR,
    SemanticType.GEOGRAPHIC_LOCATION: _STR,
    SemanticType.PERSONAL_IDENTIFIER: _STR,
    SemanticType.URL: _STR,
    SemanticType.JSON_BLOB: {"json", "jsonb", "text"},
}


def dtype_compatible(semantic_type: SemanticType, dtype: str) -> bool:
    """Is ``dtype`` a sane carrier for ``semantic_type``? (unknown is vacuously compatible)."""
    if semantic_type == SemanticType.UNKNOWN:
        return True
    return dtype.lower() in DTYPE_COMPAT.get(semantic_type, set())


def blended_confidence(
    self_confidence: float,
    semantic_type: SemanticType,
    *,
    structural_prelabel: Optional[SemanticType],
    dtype: str,
    null_fraction: float,
) -> float:
    """The 06_PRD §4 blend, implemented exactly.

    self-report → +0.07 structural agreement → +0.03 / −0.15 dtype (in)compatibility →
    −0.10 sparsity penalty on density-requiring types → unknown collapses to 0.0.
    """
    c = float(self_confidence)
    if structural_prelabel is not None and structural_prelabel == semantic_type:
        c = min(1.0, c + 0.07)                         # structural agreement bonus
    if dtype_compatible(semantic_type, dtype):
        c = min(1.0, c + 0.03)                         # dtype compatible
    else:
        c -= 0.15                                      # dtype INcompatible (strong signal)
    if null_fraction > 0.6 and semantic_type in REQUIRES_DENSITY:
        c -= 0.10                                      # sparse but should be dense
    if semantic_type == SemanticType.UNKNOWN:
        c = 0.0                                        # unknown ALWAYS routes to human
    return max(0.0, c)


def confidence_band(confidence: float) -> Band:
    """≥0.95 auto · 0.80–0.95 spot-check · <0.80 mandatory review."""
    if confidence >= AUTO_THRESHOLD:
        return "auto"
    if confidence >= REVIEW_THRESHOLD:
        return "spot_check"
    return "review"


def second_model_vote(
    confidence: float,
    primary_type: SemanticType,
    second_type: Optional[SemanticType],
) -> Band:
    """Optional cheap second-model vote for the <0.80 band (06_PRD §4).

    Agreement promotes to spot-check; disagreement stays mandatory-review (both shown).
    A no-vote (``second_type is None``) leaves the band unchanged.
    """
    band = confidence_band(confidence)
    if band != "review" or second_type is None:
        return band
    return "spot_check" if second_type == primary_type else "review"
