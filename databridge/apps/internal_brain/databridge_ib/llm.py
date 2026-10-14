"""Model access for the Internal Brain — Tencent TokenHub (Hunyuan hy3) first (plan §4.10).

Routing goes through Walnut's harness (the only place a vendor SDK is imported). TokenHub is
OpenAI-compatible, so LiteLLM reaches it with ``openai/<model>`` plus ``OPENAI_API_BASE`` =
``https://tokenhub-intl.tencentcloudmaas.com/v1`` and ``OPENAI_API_KEY`` = the TokenHub key.

The chain is explicit and runtime-level (independent of the harness's key-based fallback):
hy3 → deepseek-v4.1-flash → deterministic extractive answer. Any error, timeout, 429 or grounding
violation moves down the chain; the answer always says which model produced it.
"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from .models import ModelInfo
from .narrate import IB_SYSTEM_PROMPT, TASK, IbGroundedNarrator, user_message

log = logging.getLogger("databridge_ib.llm")

TOKENHUB_BASE = "https://tokenhub-intl.tencentcloudmaas.com/v1"
DEFAULT_CHAIN = ("hy3", "deepseek-v4.1-flash")


def tokenhub_configured() -> bool:
    return bool(os.environ.get("TOKENHUB_API_KEY") or
                (os.environ.get("OPENAI_API_KEY") and "tencentcloudmaas" in os.environ.get("OPENAI_API_BASE", "")))


def _config_for(model: str) -> dict:
    return {"routing": {TASK: "ib_model", "embed": "ib_embed"},
            "aliases": {"ib_model": {"model": f"openai/{model}"},
                        "ib_embed": {"model": "openai/kinfra-text-embedding-0.6b"}},
            "profiles": {"cloud": {}, "airgapped": {}}}


def _prepare_env() -> None:
    key = os.environ.get("TOKENHUB_API_KEY")
    if key:
        os.environ.setdefault("OPENAI_API_KEY", key)
        os.environ.setdefault("OPENAI_API_BASE", os.environ.get("TOKENHUB_BASE", TOKENHUB_BASE))


@dataclass
class ModelChain:
    """Ordered narrators; ``harness_factory(model)`` returns a Walnut Harness (or a test double)."""
    models: Sequence[str]
    harness_factory: Callable[[str], Any]
    provider: str = "tencent-tokenhub"

    @classmethod
    def tokenhub(cls, models: Sequence[str] = DEFAULT_CHAIN) -> Optional["ModelChain"]:
        if not tokenhub_configured() or os.environ.get("IB_FORCE_OFFLINE") == "1":
            return None
        _prepare_env()
        from databridge_harness import Harness, LiteLLMBackend

        def factory(model: str):
            return Harness(LiteLLMBackend(), config=copy.deepcopy(_config_for(model)), profile="cloud")
        return cls(models=list(models), harness_factory=factory)

    def answer(self, context: str, question: str) -> tuple[Optional[str], Optional[ModelInfo], list[str]]:
        """``(grounded_text | None, model_info | None, notes)``. None ⇒ caller serves extractive."""
        notes: list[str] = []
        messages = [{"role": "system", "content": IB_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message(context, question)}]
        for i, model in enumerate(self.models):
            try:
                narrator = IbGroundedNarrator(self.harness_factory(model))
                res = narrator.complete_text(TASK, messages, seed=7)
                text = getattr(res, "text", "") or ""
                if text.strip():
                    return text, ModelInfo(provider=self.provider, model=model, fallback_used=i > 0), notes
                notes.append(f"{model}: empty")
            except Exception as exc:                                # noqa: BLE001
                notes.append(f"{model}: {type(exc).__name__}")
                log.warning("model %s failed: %s", model, type(exc).__name__)
        return None, None, notes
