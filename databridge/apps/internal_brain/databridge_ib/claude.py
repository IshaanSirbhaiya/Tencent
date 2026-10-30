"""Claude as the Brain's answer model (and drafts, analyses, the audit parser), when Hy3 is not.

A Walnut harness backend (``databridge_harness.Backend``) calling Claude through the official
``anthropic`` SDK, so everything that already applies to a model answer applies unchanged: the
model sees only passages that passed Gate 1 and the live Gate 2; the harness records the call;
``IbGroundedNarrator`` runs the grounding wall on the text; any failure falls back to the quoted
answer, which says so. The chain is used only when TokenHub is not configured (or forced
offline), and only when opted in: ``IB_CLAUDE=1`` and ``ANTHROPIC_API_KEY`` set, never under
``IB_FORCE_OFFLINE=1`` (``composer.claude_enabled``). Off by default. Outside this module the model
is the neutral ``standin/standin-1`` (answers, /ib/status, the ledger): never the provider name.

It shares the page composer's guard (``model_guard.ModelGuard.shared``): one persistent spend ledger
outside the demo state, a total and a daily cap, a service-wide and a per-person
(``llm.for_person``) rate. Any limit hit is no call, so the quoted answer is served. The key is
read from the environment and never logged.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from databridge_harness.backends import RawCompletion

from .llm import MODEL_SUB, ModelChain
from .model_guard import REFUSED, ModelGuard
from .views.composer import (MODEL, PRICE_IN, PRICE_OUT, PUBLIC_MODEL, PUBLIC_PROVIDER, claude_enabled,
                             cost_usd)

log = logging.getLogger("databridge_ib.claude")

ANSWER_MAX_TOKENS = 4_000
ANSWER_TIMEOUT_S = 45.0


class ClaudeBackend:
    """``generate(model=..., messages=...)`` for the harness: one bounded Claude call."""

    name = "anthropic"

    def __init__(self, guard: ModelGuard, *, client: Any = None, effort: str = "low"):
        #: the shared guard: per person (``llm.for_person``; a call made for nobody in particular
        #: counts under one shared key), service-wide, and the spend caps
        self.guard, self.effort = guard, effort
        self._client = client
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
        chars = len(system) + sum(len(m["content"]) for m in convo)
        worst = (chars + 8_000) * PRICE_IN / 1_000_000 + ANSWER_MAX_TOKENS * PRICE_OUT / 1_000_000
        ticket, why = self.guard.admit(MODEL_SUB.get() or "-", "answer", worst)
        if ticket is None:
            log.info("model call refused by the guard: %s", why)
            raise RuntimeError(REFUSED)
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
                                 cost_usd=actual, model_used=PUBLIC_MODEL)
        finally:
            try:
                self.guard.settle(ticket, actual)
            except Exception as exc:                                # noqa: BLE001
                log.error("spend not recorded: %s", type(exc).__name__)


def _config_for(model: str) -> dict:
    return {"routing": {"ib_answer": "ib_model", "ib_deliver": "ib_model"},
            "aliases": {"ib_model": {"model": f"anthropic/{model}"}},
            "profiles": {"cloud": {}, "airgapped": {}}}


def claude_chain(state_dir: Optional[Path] = None, *, client: Any = None,
                 guard: Optional[ModelGuard] = None) -> Optional[ModelChain]:
    """Claude as the answer chain, or None unless opted in (``claude_enabled``). ``client`` (a
    test double) skips the switch. ``state_dir`` is kept for the call sites; the spend lives in
    the shared guard (``guard`` overrides it), outside the state."""
    if client is None and not claude_enabled():
        return None
    from databridge_harness import Harness
    backend = ClaudeBackend(guard or ModelGuard.shared(),
                            client=client, effort=os.environ.get("IB_CLAUDE_ANSWER_EFFORT", "low"))

    def factory(_public: str):
        return Harness(backend, config=copy.deepcopy(_config_for(MODEL)), profile="cloud", cache=None)
    # the chain's names are what answers and /ib/status show: the neutral stand-in id
    return ModelChain(models=[PUBLIC_MODEL], harness_factory=factory, provider=PUBLIC_PROVIDER)


__all__ = ["ClaudeBackend", "claude_chain"]
