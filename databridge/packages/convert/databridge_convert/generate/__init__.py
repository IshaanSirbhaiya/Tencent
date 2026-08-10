"""generate/ — S→U grounded generation core (C17, 09_FINAL_PRD §12).

Every fact is supplied, never invented. Pipeline:
  fact_extractor (rows/aggregates → [F1..Fn] GroundedValue, rendered by formatters)  §12.1
    → templates + synthesiser (template-first; cite facts_used per turn; absent-fact fallback) §12.2
    → claim_check (deterministic: every number/date/name token must match a cited fact)       §12.3a
    → verifier (independent model, DIFFERENT alias; claim-level supported/unsupported)         §12.3b
    → conversation_set serialisers (openai / sharegpt / alpaca)                                §12.4

`formatters.py` is THE unsung dependency: the canonical render forms the validators string-match.
Built first, tested exhaustively — hallucination defence, not cosmetics (§12.1).
"""
from __future__ import annotations

from .claim_check import ClaimCheckResult, ClaimViolation, check_claims
from .fact_extractor import Fact, aggregate_fact, extract_facts, facts_by_id, render_fact, sum_fact
from .synthesiser import GenerationResult, select_template, synthesise
from .templates import Template, default_template, get_template, library, register
from .verifier import VerificationResult, verify
from .formatters import (
    render_date,
    render_decimal,
    render_int,
    render_money,
    render_name,
    render_percent,
)

__all__ = [
    "render_money", "render_date", "render_name", "render_percent",
    "render_int", "render_decimal",
    "Fact", "extract_facts", "aggregate_fact", "sum_fact", "render_fact", "facts_by_id",
    "check_claims", "ClaimCheckResult", "ClaimViolation",
    "synthesise", "select_template", "GenerationResult",
    "Template", "get_template", "library", "register", "default_template",
    "verify", "VerificationResult",
]
