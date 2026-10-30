"""Model access for the Internal Brain — Tencent TokenHub (Hunyuan hy3) first (plan §4.10).

Routing goes through Walnut's harness (the only place a vendor SDK is imported). TokenHub is
OpenAI-compatible, so LiteLLM reaches it with ``openai/<model>`` plus ``OPENAI_API_BASE`` =
``https://tokenhub-intl.tencentcloudmaas.com/v1`` and ``OPENAI_API_KEY`` = the TokenHub key.

The chain is explicit and runtime-level (independent of the harness's key-based fallback):
hy3 → deepseek-v4.1-flash → deterministic extractive answer. Any error, timeout, 429 or grounding
violation moves down the chain; the answer always says which model produced it.

Bring your own model: any other OpenAI-compatible endpoint (OpenAI, a local Ollama or vLLM server)
works the same way. Set ``OPENAI_API_BASE`` and ``OPENAI_API_KEY`` and name the models, in order, in
``IB_MODELS`` (comma-separated). The model still sees only passages that passed both checks, and
the grounding wall still applies; only the provider changes.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from .models import ModelInfo
from .narrate import IB_SYSTEM_PROMPT, TASK, IbGroundedNarrator, user_message

log = logging.getLogger("databridge_ib.llm")

TOKENHUB_BASE = "https://tokenhub-intl.tencentcloudmaas.com/v1"

#: the person a model call is made for (a backend with a per-person limit reads it; never sent to
#: a model, never logged). Unset: "" (the backend then counts the call under one shared key).
MODEL_SUB: contextvars.ContextVar[str] = contextvars.ContextVar("ib_model_sub", default="")


@contextlib.contextmanager
def for_person(sub: str):
    """Model calls inside this block are made for ``sub`` (per-person rate limits)."""
    tok = MODEL_SUB.set(str(sub or ""))
    try:
        yield
    finally:
        MODEL_SUB.reset(tok)
DEFAULT_CHAIN = ("hy3", "deepseek-v4.1-flash")


def tokenhub_configured() -> bool:
    return bool(os.environ.get("TOKENHUB_API_KEY") or
                (os.environ.get("OPENAI_API_KEY") and "tencentcloudmaas" in os.environ.get("OPENAI_API_BASE", "")))


def model_configured() -> bool:
    """TokenHub, or any OpenAI-compatible endpoint given by ``OPENAI_API_BASE`` + ``OPENAI_API_KEY``."""
    return tokenhub_configured() or bool(os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_API_BASE"))


def configured_models(default: Sequence[str] = DEFAULT_CHAIN) -> list[str]:
    """The chain from ``IB_MODELS`` (comma-separated, tried in order), else the TokenHub default."""
    names = [m.strip() for m in os.environ.get("IB_MODELS", "").split(",") if m.strip()]
    return names or list(default)


def provider_label() -> str:
    return "tencent-tokenhub" if tokenhub_configured() else "openai-compatible"


def _config_for(model: str) -> dict:
    return {"routing": {TASK: "ib_model", "ib_deliver": "ib_model", "embed": "ib_embed"},
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
    def tokenhub(cls, models: Optional[Sequence[str]] = None) -> Optional["ModelChain"]:
        """The configured chain (TokenHub by default, or ``IB_MODELS`` on any OpenAI-compatible
        endpoint), or None for the deterministic quoting path."""
        if not model_configured() or os.environ.get("IB_FORCE_OFFLINE") == "1":
            return None
        _prepare_env()
        from databridge_harness import Harness, LiteLLMBackend

        def factory(model: str):
            return Harness(LiteLLMBackend(), config=copy.deepcopy(_config_for(model)), profile="cloud")
        return cls(models=list(models) if models else configured_models(), harness_factory=factory,
                   provider=provider_label())

    def answer(self, context: str, question: str) -> tuple[Optional[str], Optional[ModelInfo], list[str]]:
        """``(grounded_text | None, model_info | None, notes)``. None ⇒ caller serves extractive."""
        return self._grounded(IB_SYSTEM_PROMPT, context, question)

    def draft(self, system_prompt: str, context: str, instruction: str
              ) -> tuple[Optional[str], Optional[ModelInfo], list[str]]:
        """A draft (``actions.py``) under the SAME grounding wall as an answer: the context is the
        asker's own released, cited passages; no tools are passed; a sentence that binds to no
        passage is dropped and an unsupported value or name refuses the draft. None ⇒ the caller
        builds the deterministic draft."""
        return self._grounded(system_prompt, context, instruction)

    def _grounded(self, system_prompt: str, context: str, question: str
                  ) -> tuple[Optional[str], Optional[ModelInfo], list[str]]:
        notes: list[str] = []
        messages = [{"role": "system", "content": system_prompt},
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

    def complete(self, messages: list[dict], *, seed: int = 7) -> tuple[Optional[str], Optional[str], list[str]]:
        """``(text | None, model | None, notes)`` from the first model in the chain that returns
        non-empty text, in the chain's order, with NO grounding wall. Only for output that is
        validated against a closed schema before use (the audit filter parser, audit/nl_llm.py);
        answers to employees always go through `answer`."""
        notes: list[str] = []
        for model in self.models:
            try:
                res = self.harness_factory(model).complete_text(TASK, messages, seed=seed)
                text = getattr(res, "text", "") or ""
                if text.strip():
                    return text, model, notes
                notes.append(f"{model}: empty")
            except Exception as exc:                                # noqa: BLE001
                notes.append(f"{model}: {type(exc).__name__}")
                log.warning("model %s failed: %s", model, type(exc).__name__)
        return None, None, notes
