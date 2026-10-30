"""The structured model call for deliverables, on the answer's own ``ModelChain`` (no tools, ever).

Bounded (Codex review #9, Opus review #9):

* a process-wide cap on in-flight deliverable model calls (``MAX_INFLIGHT``), acquired without
  waiting: when it is full the call is not made and the caller falls back (``busy``);
* a dedicated small pool, and ONE deadline (``BUDGET_S``) covering every model in the chain and
  every retry: no model is started after the deadline, and a late result is discarded (it is
  never published and never ledgered);
* ``IB_JSON_MODE=0`` uses plain text with the schema in the prompt and accepts ONE whole JSON
  object, optionally inside a single ```json fence (never the first brace block of prose).

LiteLLM's own transport timeout is not set from here (the harness exposes none); a hung call
keeps its slot until it returns, which is what bounds the damage.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from ..models import ModelInfo
from ..narrate import user_message
from .prompts import TASK

log = logging.getLogger("databridge_ib.deliverables")

BUDGET_S = 30.0
MAX_INFLIGHT = 2
POOL = ThreadPoolExecutor(max_workers=MAX_INFLIGHT, thread_name_prefix="ib-deliver-model")
_SLOTS = threading.BoundedSemaphore(MAX_INFLIGHT)
_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.S)


def json_mode() -> bool:
    return os.environ.get("IB_JSON_MODE", "1") != "0"


def parse_json_object(text: str, model: type[BaseModel]) -> BaseModel:
    """One whole JSON object, optionally inside one ```json fence. Anything else raises."""
    s = (text or "").strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    if not (s.startswith("{") and s.endswith("}")):
        raise ValueError("not one JSON object")
    json.loads(s)                                               # strict JSON first
    return model.model_validate_json(s)


def _schema_note(model: type[BaseModel]) -> str:
    return ("Return ONLY a JSON object matching this JSON schema:\n"
            + json.dumps(model.model_json_schema(), separators=(",", ":")))


def _run(chain: Any, system: str, context: str, instruction: str, model: type[BaseModel],
         deadline: float) -> tuple[Optional[BaseModel], Optional[ModelInfo], str]:
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_message(context, instruction)}]
    reason = "model_error"
    try:
        for i, name in enumerate(chain.models):
            if time.monotonic() >= deadline:
                return None, None, "timeout"
            try:
                h = chain.harness_factory(name)
                if json_mode():
                    res = h.complete_structured(TASK, messages, model, seed=7, max_retries=1)
                    parsed = res.parsed
                else:
                    msgs = [{"role": "system", "content": system + "\n\n" + _schema_note(model)},
                            messages[1]]
                    parsed = parse_json_object(getattr(h.complete_text(TASK, msgs, seed=7),
                                                       "text", ""), model)
                if parsed is not None:
                    return parsed, ModelInfo(provider=chain.provider, model=name,
                                             fallback_used=i > 0), "none"
            except (ValidationError, ValueError) as exc:
                reason = "invalid_output"
                log.warning("deliverable model %s: %s", name, type(exc).__name__)
            except Exception as exc:                                # noqa: BLE001
                name_ = type(exc).__name__
                reason = "invalid_output" if "Structured" in name_ else "model_error"
                log.warning("deliverable model %s failed: %s", name, name_)
        return None, None, reason
    finally:
        _SLOTS.release()


def structured(chain: Any, system: str, context: str, instruction: str,
               model: type[BaseModel], *, budget_s: float = BUDGET_S
               ) -> tuple[Optional[BaseModel], Optional[ModelInfo], str]:
    """``(parsed | None, model_info | None, fallback_code)``; ``fallback_code`` is ``none`` on
    success, else ``busy``, ``timeout``, ``invalid_output`` or ``model_error``."""
    if chain is None:
        return None, None, "no_model"
    if not _SLOTS.acquire(blocking=False):
        return None, None, "busy"
    deadline = time.monotonic() + budget_s
    try:
        fut = POOL.submit(_run, chain, system, context, instruction, model, deadline)
    except Exception:                                               # noqa: BLE001
        _SLOTS.release()
        return None, None, "model_error"
    try:
        return fut.result(timeout=max(0.0, deadline - time.monotonic()))
    except FutTimeout:
        return None, None, "timeout"                 # the worker keeps its slot until it returns
    except Exception:                                               # noqa: BLE001
        return None, None, "model_error"


__all__ = ["structured", "parse_json_object", "json_mode", "BUDGET_S", "MAX_INFLIGHT"]
