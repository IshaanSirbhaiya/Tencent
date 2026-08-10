"""Harness — the ONE call site for every LLM call in DataBridge (07_PRD §1–§5).

Call sites pass a ``task_class`` (never a model name): routing is config, so a routing
change is a config diff, not a code hunt, and reproducibility is preserved. The same
calling code yields valid structured output from any backend (P2); the structured-output
ladder resolves per call from the capability table.

Enforced here, the single choke point:
  * the L1/L2/L3 structured-output ladder (02_TRD §A.5, 07_PRD §3),
  * content-hash caching (07_PRD §5) — key (task_class, model, inputs_hash,
    response_model_hash, seed),
  * per-run / per-source token budgets — ``BudgetExceeded`` rather than draining,
  * the airgapped refusal (07_PRD §10 mode 1, P1) — a hosted model under the airgapped
    profile raises LOUDLY before any backend call; no silent fallback.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter
from typing import Any, MutableMapping, Optional

from pydantic import BaseModel, ValidationError

from databridge_core import PipelineEvent, active_profile, content_hash, resolve_model

from .backends import Backend, caps_for, is_hosted
from .tracking import build_event

Message = dict  # {"role": "system"|"user"|"assistant", "content": str}

# Sentinel so `cache=None` (disable) is distinguishable from "use a fresh default cache".
_DEFAULT_CACHE = object()


# --- exceptions -----------------------------------------------------------------
class HarnessError(Exception):
    """Base for harness failures."""


class AirgappedRefusal(HarnessError):
    """A hosted model was resolved under the airgapped profile — refuse, never fall back."""


class BudgetExceeded(HarnessError):
    """A per-run or per-source token budget was reached (07_PRD §5)."""


class StructuredOutputError(HarnessError):
    """Output still failed schema validation after the L2 retry budget was exhausted."""


# --- results --------------------------------------------------------------------
@dataclass(frozen=True)
class StructuredResult:
    parsed: Any                      # an instance of the requested response_model
    raw: str
    event: PipelineEvent
    ladder_used: str                 # "L1" | "L2" | "L3"
    retries: int


@dataclass(frozen=True)
class TextResult:
    text: str
    event: PipelineEvent


class Harness:
    def __init__(
        self,
        backend: Optional[Backend] = None,
        *,
        config: Optional[dict] = None,
        profile: Optional[str] = None,
        run_budget: Optional[int] = None,
        source_budgets: Optional[dict[str, int]] = None,
        cache: Any = _DEFAULT_CACHE,           # None disables; default → fresh dict
    ) -> None:
        if backend is None:
            from .backends import LiteLLMBackend
            backend = LiteLLMBackend()
        self._backend = backend
        self._config = config
        self._profile = profile
        self._run_budget = run_budget
        self._source_budgets = source_budgets or {}
        self._cache: Optional[MutableMapping] = {} if cache is _DEFAULT_CACHE else cache
        self._run_tokens = 0
        self._source_tokens: dict[str, int] = defaultdict(int)

    # -- internals ---------------------------------------------------------------
    def _profile_name(self) -> str:
        return self._profile or active_profile()

    def _resolve(self, task_class: str) -> str:
        return resolve_model(task_class, profile=self._profile, config=self._config)

    def _guard_airgapped(self, model: str) -> None:
        if self._profile_name() == "airgapped" and is_hosted(model):
            raise AirgappedRefusal(
                f"airgapped profile resolved to hosted model {model!r}; refusing — "
                "no silent fallback (P1 / 07_PRD §10)"
            )

    def _check_budget(self, source_id: Optional[str]) -> None:
        if self._run_budget is not None and self._run_tokens >= self._run_budget:
            raise BudgetExceeded(
                f"run token budget {self._run_budget} reached ({self._run_tokens} used)"
            )
        if source_id is not None:
            cap = self._source_budgets.get(source_id)
            if cap is not None and self._source_tokens[source_id] >= cap:
                raise BudgetExceeded(
                    f"source {source_id!r} budget {cap} reached "
                    f"({self._source_tokens[source_id]} used)"
                )

    def _add_usage(self, tokens: int, source_id: Optional[str]) -> None:
        self._run_tokens += tokens
        if source_id is not None:
            self._source_tokens[source_id] += tokens

    # -- public API --------------------------------------------------------------
    def complete_structured(
        self,
        task_class: str,
        messages: list[Message],
        response_model: type[BaseModel],
        *,
        strict: bool = True,
        seed: Optional[int] = None,
        max_retries: int = 2,
        source_id: Optional[str] = None,
    ) -> StructuredResult:
        model = self._resolve(task_class)
        self._guard_airgapped(model)                       # before ANY backend call
        caps = caps_for(model)
        schema = response_model.model_json_schema()
        cache_key = (task_class, model, content_hash(messages), content_hash(schema), seed)

        if self._cache is not None and cache_key in self._cache:
            raw_text, parsed_dump, ladder, retries = self._cache[cache_key]
            parsed = response_model.model_validate(parsed_dump)
            event = build_event("complete_structured", messages, raw_text,
                                task_class=task_class, model=model, tokens=0, cost_usd=0.0,
                                duration_ms=0.0, ladder_used=ladder, retries=retries, cached=True)
            return StructuredResult(parsed=parsed, raw=raw_text, event=event,
                                    ladder_used=ladder, retries=retries)

        self._check_budget(source_id)

        use_l3 = strict and caps.has_guided_decoding
        guided = schema if use_l3 else None
        convo = list(messages)
        attempt = 0
        total_tokens = 0
        total_cost = 0.0
        t0 = perf_counter()
        while True:
            raw = self._backend.generate(model=model, messages=convo,
                                         json_mode=caps.json_mode, guided_json=guided, seed=seed)
            total_tokens += raw.tokens
            total_cost += raw.cost_usd
            try:
                parsed = response_model.model_validate_json(raw.text)
                break
            except ValidationError as err:
                if attempt >= max_retries:
                    self._add_usage(total_tokens, source_id)
                    raise StructuredOutputError(
                        f"{task_class}: output failed validation after {attempt} "
                        f"retr{'y' if attempt == 1 else 'ies'}: {err}"
                    ) from err
                attempt += 1
                # L2: feed the validation error back and ask for a correction.
                convo = convo + [{
                    "role": "user",
                    "content": (f"Your previous output failed validation: {err}. "
                                "Return ONLY corrected JSON matching the schema."),
                }]

        duration_ms = (perf_counter() - t0) * 1000
        # Report the rung that actually produced the accepted output. A clean first parse
        # is L3 (if guided was used) else L1; ANY retry means the L2 backstop kicked in —
        # surfacing that is the whole point of ladder_used as a regression signal (§2).
        ladder = ("L3" if use_l3 else "L1") if attempt == 0 else "L2"
        self._add_usage(total_tokens, source_id)
        if self._cache is not None:
            self._cache[cache_key] = (raw.text, parsed.model_dump(mode="json"), ladder, attempt)
        event = build_event("complete_structured", messages, parsed, task_class=task_class,
                            model=model, tokens=total_tokens, cost_usd=total_cost,
                            duration_ms=duration_ms, ladder_used=ladder, retries=attempt,
                            cached=False)
        return StructuredResult(parsed=parsed, raw=raw.text, event=event,
                                ladder_used=ladder, retries=attempt)

    def complete_text(
        self,
        task_class: str,
        messages: list[Message],
        *,
        seed: Optional[int] = None,
        source_id: Optional[str] = None,
    ) -> TextResult:
        """Free-form text — synthesis/narration only; anything machine-consumed uses structured."""
        model = self._resolve(task_class)
        self._guard_airgapped(model)
        cache_key = (task_class, model, content_hash(messages), "text", seed)

        if self._cache is not None and cache_key in self._cache:
            text = self._cache[cache_key]
            event = build_event("complete_text", messages, text, task_class=task_class,
                                model=model, tokens=0, cost_usd=0.0, duration_ms=0.0, cached=True)
            return TextResult(text=text, event=event)

        self._check_budget(source_id)
        t0 = perf_counter()
        raw = self._backend.generate(model=model, messages=list(messages), seed=seed)
        duration_ms = (perf_counter() - t0) * 1000
        self._add_usage(raw.tokens, source_id)
        if self._cache is not None:
            self._cache[cache_key] = raw.text
        event = build_event("complete_text", messages, raw.text, task_class=task_class,
                            model=model, tokens=raw.tokens, cost_usd=raw.cost_usd,
                            duration_ms=duration_ms, cached=False)
        return TextResult(text=raw.text, event=event)
