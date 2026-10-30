"""Claude as the Brain's answer model (and drafts, analyses, the audit parser), when Hy3 is not.

A Walnut harness backend (``databridge_harness.Backend``) calling Claude through the official
``anthropic`` SDK, so everything that already applies to a model answer applies unchanged: the
model sees only passages that passed Gate 1 and the live Gate 2; the harness records the call;
``IbGroundedNarrator`` runs the grounding wall on the text; any failure falls back to the quoted
answer, which says so. The chain is used only when TokenHub is not configured (or forced
offline), ``ANTHROPIC_API_KEY`` is set and ``IB_CLAUDE`` is not ``0``.

It shares the page composer's spend cap (the same file, ``views/composer.Budget``) and has its own
service-wide rate limit. The key is read from the environment and never logged.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from databridge_harness.backends import RawCompletion

from .llm import ModelChain
from .views.composer import (GLOBAL_RATE, MODEL, PRICE_IN, PRICE_OUT, Budget, _Window, cost_usd)

log = logging.getLogger("databridge_ib.claude")

ANSWER_MAX_TOKENS = 4_000
ANSWER_TIMEOUT_S = 45.0


class ClaudeBackend:
    """``generate(model=..., messages=...)`` for the harness: one bounded Claude call."""

    name = "anthropic"

    def __init__(self, budget: Budget, *, client: Any = None, effort: str = "low"):
        self.budget, self.effort = budget, effort
        self._client = client
        self._rate = _Window(GLOBAL_RATE, 60.0)
        self._lock = threading.Lock()

    def _api(self):
        with self._lock:
            if self._client is None:
                import anthropic
                self._client = anthropic.Anthropic(max_retries=1)
            return self._client

    def generate(self, *, model: str, messages: list[dict], json_mode: bool = False,
                 guided_json: Optional[dict] = None, seed: Optional[int] = None,
                 **kwargs: Any) -> RawCompletion:
        name = model.split("/", 1)[-1]
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        convo = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages
                 if m.get("role") in ("user", "assistant")]
        if json_mode:
            system += "\n\nReturn ONLY one JSON object, no prose and no markdown fence."
        if not self._rate.allow("*"):
            raise RuntimeError("claude rate limit")
        chars = len(system) + sum(len(m["content"]) for m in convo)
        worst = (chars + 8_000) * PRICE_IN / 1_000_000 + ANSWER_MAX_TOKENS * PRICE_OUT / 1_000_000
        if not self.budget.reserve(worst):
            raise RuntimeError("claude spend cap reached")
        actual = 0.0
        try:
            resp = self._api().with_options(timeout=ANSWER_TIMEOUT_S).beta.messages.create(
                model=name, max_tokens=ANSWER_MAX_TOKENS,
                system=[{"type": "text", "text": system or "You are a helpful assistant.",
                         "cache_control": {"type": "ephemeral"}}],
                messages=convo, output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            actual = cost_usd(resp.usage)
            if resp.stop_reason == "refusal":
                raise RuntimeError("claude refusal")
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            u = resp.usage
            return RawCompletion(text=text, tokens_in=int(getattr(u, "input_tokens", 0) or 0),
                                 tokens_out=int(getattr(u, "output_tokens", 0) or 0),
                                 cost_usd=actual, model_used=str(getattr(resp, "model", name)))
        finally:
            self.budget.settle(worst, actual)


def _config_for(model: str) -> dict:
    return {"routing": {"ib_answer": "ib_model", "ib_deliver": "ib_model"},
            "aliases": {"ib_model": {"model": f"anthropic/{model}"}},
            "profiles": {"cloud": {}, "airgapped": {}}}


def claude_chain(state_dir: Path, *, client: Any = None) -> Optional[ModelChain]:
    """Claude as the answer chain, or None (no key, or ``IB_CLAUDE=0``)."""
    if client is None and (not os.environ.get("ANTHROPIC_API_KEY")
                           or os.environ.get("IB_CLAUDE", "1") == "0"):
        return None
    from databridge_harness import Harness
    cap = float(os.environ.get("IB_CLAUDE_BUDGET_USD", "10"))
    backend = ClaudeBackend(Budget.for_path(Path(state_dir) / "claude_spend.json", cap),
                            client=client, effort=os.environ.get("IB_CLAUDE_ANSWER_EFFORT", "low"))

    def factory(model: str):
        return Harness(backend, config=copy.deepcopy(_config_for(model)), profile="cloud", cache=None)
    return ModelChain(models=[MODEL], harness_factory=factory, provider="anthropic")


__all__ = ["ClaudeBackend", "claude_chain"]
