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
from typing import TYPE_CHECKING, Any, MutableMapping, Optional

from pydantic import BaseModel, ValidationError

from databridge_core import PipelineEvent, active_profile, content_hash, resolve_model

from .backends import Backend, caps_for, is_hosted
from .tracking import build_event

if TYPE_CHECKING:  # pragma: no cover
    from .embedding import EmbeddingResult

Message = dict  # {"role": "system"|"user"|"assistant", "content": str}


def schema_instruction(schema: dict) -> Message:
    """The L1 rung: the schema, IN THE PROMPT, for a backend that cannot be grammar-constrained.

    **This was missing, and its absence was silent.** `caps_for("openai/*").guided_decoding` is
    `False`, so L3 is skipped and `backends.generate` sent only
    ``response_format={"type": "json_object"}`` — which guarantees the reply is *syntactically*
    JSON and says nothing whatever about its field names. The model therefore guessed them:
    gpt-4o returned ``patient_name`` / ``patient_id`` / ``patient_dob`` where `IntakeDraft` wants
    ``claimed_name`` / ``claimed_id_number`` / ``claimed_dob``.

    Pydantic ignores unknown keys, so the model validated **cleanly with every field ``None``** —
    no ``ValidationError``, therefore no L2 retry, therefore no error anywhere. `/health` reported
    a live model while every chit extracted to a blank card. A guard whose failure mode is silence
    gets reported as coverage; this one was reported as "the model is wired up".

    Naming the schema is also what makes the ladder's own labels true: "L1" has always meant
    prompt-level schema instruction in `07_PRD` §3, and until now L1 instructed nothing.

    The wording tells the model to OMIT what the source does not state rather than to invent a
    plausible filler, which is the same rule the grounding wall enforces afterwards
    (CLAUDE.md §Hard constraints 2). The wall is still the guarantee; this only stops the model
    fabricating work for it.
    """
    import json

    return {"role": "user", "content": (
        "Return ONLY a JSON object conforming to this JSON Schema. Use EXACTLY these field "
        "names — do not rename, abbreviate or invent fields. Any value the source does not "
        "state must be null; never guess one." + "\n\n"
        + json.dumps(schema, indent=2, sort_keys=True))}

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
        # L3 constrains the tokens themselves, so the schema is already binding and repeating it
        # in the prompt would only cost context. Every other backend gets it in the prompt or
        # gets nothing — see `schema_instruction`.
        # Inserted BEFORE the final message, not appended after it. Two reasons, and the second
        # is the one that bit: instructions-then-data is the ordering that reads correctly to a
        # model, and the fixture-keyed gold backend dispatches on `messages[-1]` — appending left
        # every gold lookup matching the schema instead of the document, so the whole offline
        # suite extracted empty drafts. Keeping the document last preserves that contract.
        convo = (list(messages) if use_l3
                 else [*messages[:-1], schema_instruction(schema), *messages[-1:]])
        attempt = 0
        total_tokens = 0
        total_cost = 0.0
        t0 = perf_counter()
        while True:
            # `Caps.seed` is declared per provider and was then ignored. Anthropic does not
            # accept `seed`, so with only ANTHROPIC_API_KEY set every structured call raised
            # `UnsupportedParamsError` — which the gateway caught and quietly degraded to the
            # gold backend, leaving `/health` still advertising a live model. Honour the
            # capability instead of discovering it as an exception.
            raw = self._backend.generate(model=model, messages=convo,
                                         json_mode=caps.json_mode, guided_json=guided,
                                         seed=seed if caps.seed else None)
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
        # Same capability check as the structured path: `/brain/query` runs through here, so an
        # Anthropic key alone made the narrated answer raise rather than the extraction only.
        raw = self._backend.generate(model=model, messages=list(messages),
                                     seed=seed if caps_for(model).seed else None)
        duration_ms = (perf_counter() - t0) * 1000
        self._add_usage(raw.tokens, source_id)
        if self._cache is not None:
            self._cache[cache_key] = raw.text
        event = build_event("complete_text", messages, raw.text, task_class=task_class,
                            model=model, tokens=raw.tokens, cost_usd=raw.cost_usd,
                            duration_ms=duration_ms, cached=False)
        return TextResult(text=raw.text, event=event)

    # -- embeddings (additive: task_class ``embed``, BUILD_PLAN §5.5) ---------------------------
    def embed(
        self,
        texts: list[str],
        *,
        task_class: str = "embed",
        batch_size: int = 64,
        workers: int = 4,
        source_id: Optional[str] = None,
    ) -> "EmbeddingResult":
        """Embed ``texts`` (order preserved) through the routed embedding model.

        Routing is config like every other call. The model is resolved and the airgapped guard
        runs FIRST, so a hosted embedding model under the airgapped profile refuses loudly rather
        than quietly dropping to the offline fallback.

        The **offline deterministic fallback** (``embedding.hashing_embed``) is used — and the
        result says so in ``offline``/``label`` — when ``WALNUT_EMBED_OFFLINE`` is set, when the
        backend has no ``embed`` method (e.g. an offline gold backend), or when the real LiteLLM
        backend's provider has no credential in the environment. It is never used to paper over
        a failing *real* call: that raises, because silence is not success.

        Batches run on ``workers`` threads (I/O-bound HTTP calls). Throughput is MEASURED over
        this call and returned; nothing here estimates it.
        """
        from concurrent.futures import ThreadPoolExecutor

        from .embedding import (
            OFFLINE_EMBED_DIM,
            OFFLINE_EMBED_MODEL,
            OFFLINE_LABEL,
            EmbeddingResult,
            RawEmbedding,
            embedding_key_present,
            force_offline,
            hashing_embed,
        )

        texts = list(texts)
        model = self._resolve(task_class)
        self._guard_airgapped(model)
        backend_embed = getattr(self._backend, "embed", None)
        offline = (force_offline() or backend_embed is None
                   or (getattr(self._backend, "name", "") == "litellm"
                       and not embedding_key_present(model)))
        n_chars = sum(len(t) for t in texts)
        batch_size = max(1, int(batch_size))
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        t0 = perf_counter()

        if offline:
            vectors = hashing_embed(texts, OFFLINE_EMBED_DIM)
            duration_ms = (perf_counter() - t0) * 1000
            secs = duration_ms / 1000
            event = build_event("embed", texts, f"{len(vectors)} vectors", task_class=task_class,
                                model=OFFLINE_EMBED_MODEL, tokens=0, cost_usd=0.0,
                                duration_ms=duration_ms, cached=False)
            return EmbeddingResult(
                vectors=vectors, model=OFFLINE_EMBED_MODEL, dim=OFFLINE_EMBED_DIM, offline=True,
                label=OFFLINE_LABEL, tokens=0, cost_usd=0.0, duration_ms=duration_ms,
                batches=len(batches), workers=1,
                texts_per_second=(len(texts) / secs) if texts and secs > 0 else None,
                chars_per_second=(n_chars / secs) if texts and secs > 0 else None,
                events=[event])

        # Per-text cache: an unchanged chunk re-embeds for free (incremental indexing).
        results: list[Optional[list[float]]] = [None] * len(texts)
        todo: list[int] = []
        for i, t in enumerate(texts):
            key = (task_class, model, content_hash(t), "embed")
            if self._cache is not None and key in self._cache:
                results[i] = list(self._cache[key])
            else:
                todo.append(i)
        todo_batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
        if todo_batches:
            self._check_budget(source_id)

        def _run(idx: list[int]) -> tuple[list[int], RawEmbedding, float]:
            b0 = perf_counter()
            raw = backend_embed(model=model, inputs=[texts[i] for i in idx])
            if len(raw.vectors) != len(idx):
                raise HarnessError(f"{task_class}: backend returned {len(raw.vectors)} vectors "
                                   f"for {len(idx)} inputs")
            return idx, raw, (perf_counter() - b0) * 1000

        n_workers = max(1, min(int(workers), len(todo_batches) or 1))
        if n_workers == 1:
            outs = [_run(b) for b in todo_batches]
        else:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                outs = list(pool.map(_run, todo_batches))

        tokens = 0
        cost = 0.0
        events = []
        for idx, raw, ms in outs:
            tokens += raw.tokens
            cost += raw.cost_usd
            for i, vec in zip(idx, raw.vectors):
                results[i] = list(map(float, vec))
                if self._cache is not None:
                    self._cache[(task_class, model, content_hash(texts[i]), "embed")] = \
                        tuple(results[i])
            events.append(build_event("embed", [texts[i] for i in idx],
                                      f"{len(raw.vectors)} vectors", task_class=task_class,
                                      model=model, tokens=raw.tokens, cost_usd=raw.cost_usd,
                                      duration_ms=ms, cached=False))
        self._add_usage(tokens, source_id)
        dims = {len(v) for v in results if v is not None}
        if len(dims) > 1:
            raise HarnessError(f"{task_class}: inconsistent embedding dimensions {sorted(dims)}")
        duration_ms = (perf_counter() - t0) * 1000
        secs = duration_ms / 1000
        return EmbeddingResult(
            vectors=[v for v in results if v is not None] if texts else [],
            model=model, dim=dims.pop() if dims else 0, offline=False, label=None,
            tokens=tokens, cost_usd=cost, duration_ms=duration_ms, batches=len(todo_batches),
            workers=n_workers,
            texts_per_second=(len(texts) / secs) if texts and secs > 0 else None,
            chars_per_second=(n_chars / secs) if texts and secs > 0 else None,
            events=events)
