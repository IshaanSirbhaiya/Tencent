"""The identity-verification refusal for this pack's assistant — the engine mechanism, re-exported.

C-5 (finance dry run, 2026-10-02). The deterministic screen that refuses identity-verification
questions before any model is called is an ENGINE guarantee (CLAUDE.md §Hard constraints 1) and now
lives in ``databridge_core.identity_guardrail``. Its detection rule is unchanged and runs under
every binding; its refusal WORDING and the identity nouns it adds to its floor come from a pack's
``agent_wording.yaml`` (``identity`` section), never from engine code.

What this module adds: the intake assistant is the surface of the pack that carries the deployment's
rule table (``pack.active_pack``), so its refusal is worded in THAT pack's voice — even in a
deployment that binds several packs, where a shared engine surface speaks neutrally. When no pack
carries rules, it falls back to the engine's voice resolution.
"""
from __future__ import annotations

from typing import Optional

from databridge_core.identity_guardrail import (
    REFUSAL_CODE,
    GuardrailResult,
    detect,
)
from databridge_core.identity_guardrail import refusal_text as _refusal_text

from .pack import PackConfigError, active_pack


def _rules_pack() -> Optional[str]:
    try:
        return active_pack()
    except PackConfigError:
        return None


def refusal_text() -> str:
    """The refusal sentence in the rule-carrying pack's words (neutral when none declares one)."""
    return _refusal_text(_rules_pack())


def check_question(question: str) -> GuardrailResult:
    """Screen a question before it reaches any model (``databridge_core.identity_guardrail``).

    ``refused=True`` with the pack's refusal text for identity-verification questions;
    ``refused=False`` for everything else — including questions that merely *name* a subject.
    """
    rule = detect(question)
    if not rule:
        return GuardrailResult(refused=False)
    return GuardrailResult(refused=True, code=REFUSAL_CODE, reason=rule, answer=refusal_text())


def __getattr__(name: str):
    # ``REFUSAL_TEXT`` was a module constant; it is the bound pack's sentence now, read per access.
    if name == "REFUSAL_TEXT":
        return refusal_text()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["GuardrailResult", "REFUSAL_CODE", "REFUSAL_TEXT", "check_question", "refusal_text"]
