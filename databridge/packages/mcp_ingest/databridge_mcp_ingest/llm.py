"""The harness this package uses when a caller does not pass one (the CLI).

Same decision as the gateway's `llm.detect_llm` / `build_extraction_harness`, restated here so the
CLI does not import the whole gateway app: a real `Harness` when the models `config/models.yaml`
routes this package's task classes to are reachable (and `DATABRIDGE_FORCE_GOLD` is not set), else
`None` — and `None` is reported, never papered over with a fake model.

HC1 (2026-10-01, SWEEP hardcode audit #9). This used to return one of two model ids written here,
outside ``config/models.yaml``, and overwrite the ``sonnet`` alias with it, so the CLI ran a model
no config named. Every model is
now READ from the routing table, per task class:

1. the model the task class's alias names, when its provider is reachable;
2. else a provider-suffixed alternate of that alias (``sonnet_anthropic``) that is reachable;
3. else the reachable ``primary_llm`` model (or one of its provider-suffixed alternates) — the
   gateway's fallback, read from the same file;
4. else nothing: ``detect()`` says why, per task class, and ``default_harness()`` returns ``None``.

The provider→key-env table below is a provider POLICY (which env var holds which provider's key),
not a model choice; it is the same table the gateway holds.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

#: The task classes this package routes through the harness. Their aliases come from models.yaml.
TASK_CLASSES = ("entity_extraction", "rule_proposal", "schema_annotation")

#: Provider → the env var holding its key. A provider policy, never a model id.
PROVIDER_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
#: Local providers that need no key (LiteLLM's Ollama prefixes).
KEYLESS_PROVIDERS = frozenset({"ollama", "ollama_chat"})
#: The alias whose model is the last-resort fallback, as in the gateway. Read, never a model here.
FALLBACK_ALIAS = "primary_llm"


def _provider(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else model


def _reachable(model: Optional[str]) -> tuple[bool, str]:
    if not model:
        return False, "no model configured"
    provider = _provider(model)
    if provider in PROVIDER_KEY_ENV:
        env = PROVIDER_KEY_ENV[provider]
        return (True, f"{env} is set") if os.getenv(env) else (False, f"{env} is not set")
    if provider in KEYLESS_PROVIDERS:
        return True, f"{provider} is a local provider and needs no key"
    return False, f"provider {provider!r} is not in the provider policy"


def _alias_model(cfg: dict, alias: Optional[str]) -> Optional[str]:
    entry = (cfg.get("aliases") or {}).get(alias or "")
    return entry.get("model") if isinstance(entry, dict) else None


def _first_reachable(cfg: dict, alias: str) -> tuple[Optional[str], Optional[str], str]:
    """``(model, alias_used, reason)`` for ``alias`` or one of its provider-suffixed alternates."""
    configured = _alias_model(cfg, alias)
    ok, why = _reachable(configured)
    if ok:
        return configured, alias, why
    providers = set(PROVIDER_KEY_ENV) | set(KEYLESS_PROVIDERS)
    for alt in sorted(a for a in (cfg.get("aliases") or {})
                      if a.startswith(alias + "_") and a[len(alias) + 1:] in providers):
        m = _alias_model(cfg, alt)
        alt_ok, alt_why = _reachable(m)
        if alt_ok:
            return m, alt, f"{alias} unreachable ({why}); {alt} used ({alt_why})"
    return None, None, f"{alias} unreachable ({why}) and no reachable alternate"


def detect(cfg: Optional[dict] = None) -> dict:
    """Which model each task class will run on, all read from ``config/models.yaml``.

    ``{"backend", "model", "routing": {task_class: {alias, configured, effective, fallback,
    reason}}}``. ``model`` is ``None`` (``backend: "none"``) unless EVERY task class has a reachable
    model — a CLI run that could extract but not propose plans would be half a run.
    """
    if os.getenv("DATABRIDGE_FORCE_GOLD") == "1":
        return {"backend": "none", "model": None, "routing": {},
                "reason": "DATABRIDGE_FORCE_GOLD=1"}
    if cfg is None:
        from databridge_core.config import load_model_config

        cfg = load_model_config()
    routing_table = cfg.get("routing") or {}
    fallback, fallback_alias, fallback_why = _first_reachable(cfg, FALLBACK_ALIAS)
    routing: dict[str, dict] = {}
    for task_class in TASK_CLASSES:
        alias = routing_table.get(task_class)
        configured = _alias_model(cfg, alias)
        model, used, why = _first_reachable(cfg, alias) if alias else (None, None, "unrouted")
        if model is None and fallback is not None:
            model, used = fallback, fallback_alias
            why = f"{why}; {fallback_alias} used as the fallback ({fallback_why})"
        routing[task_class] = {"alias": alias, "configured": configured, "effective": model,
                               "fallback": model is not None and model != configured,
                               "reason": why}
    missing = [t for t, r in routing.items() if r["effective"] is None]
    if missing:
        return {"backend": "none", "model": None, "routing": routing,
                "reason": f"no reachable model for {missing}"}
    first = routing[TASK_CLASSES[0]]["effective"]
    return {"backend": _provider(first), "model": first, "routing": routing, "reason": ""}


def default_harness() -> Optional[object]:
    from databridge_core.config import load_model_config

    cfg = copy.deepcopy(load_model_config())
    detected = detect(cfg)
    if not detected["model"]:
        return None
    from databridge_harness import Harness, LiteLLMBackend

    # Point an alias at its reachable fallback on the COPY only — the shared config is never
    # mutated, and the model written is one the config itself named.
    for route in detected["routing"].values():
        if route["fallback"] and route["alias"]:
            aliases = cfg.setdefault("aliases", {})
            aliases.setdefault(route["alias"], {})["model"] = route["effective"]
    return Harness(LiteLLMBackend(), config=cfg, profile="cloud")


# --- model-output cache + metering (BUILD_PLAN §11a "P") ------------------------------------------
#: Bump when what is cached changes meaning (e.g. the stored record's shape). The PROMPT and the
#: closed vocabulary need no bump: both are hashed into every key (see `ModelOutputCache`).
EXTRACT_CACHE_VERSION = 1


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class ModelOutputCache:
    """Model output persisted on disk, keyed by exactly what determines it:

        content   sha256 of the text sent (the chunk)               a changed chunk misses
        prompt    sha256 of the instructions sent (the system message: the closed vocabulary and
                  the prompt wording)                               a vocabulary/prompt edit misses
        schema    sha256 of the response schema (the kind enums)
        model     the model the task class resolved to              a routing change misses
        task_class, seed, EXTRACT_CACHE_VERSION

    What is stored is the model's RAW output and its parsed form — never post-processed graph
    rows — so the grounding wall and the vocabulary checks run again on every hit, against the
    vocabulary in force. Entries are write-once per key (atomic replace), so two workers writing
    the same key race harmlessly. A garbled entry reads as a miss, never as an error.
    """

    def __init__(self, root) -> None:
        self.root = Path(root)

    @staticmethod
    def key_parts(task_class: str, model: str, messages: list, schema: dict, seed) -> dict:
        content = messages[-1].get("content") if messages else ""
        return {"v": EXTRACT_CACHE_VERSION, "task_class": task_class, "model": model,
                "content": _sha(content if isinstance(content, str) else _canon(content)),
                "prompt": _sha(_canon(list(messages[:-1]))), "schema": _sha(_canon(schema)),
                "seed": seed}

    @staticmethod
    def key(parts: dict) -> str:
        return _sha(_canon(parts))

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[dict]:
        p = self.path(key)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, record: dict) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, sort_keys=True, default=str), encoding="utf-8")
        os.replace(tmp, p)


class ModelMeter:
    """Thread-safe counters for one run's model use: calls that reached the harness, disk-cache
    hits, harness in-memory cache hits, tokens, and latency (sum / max / mean)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.disk_hits = 0
        self.harness_hits = 0
        self.tokens = 0
        self.latency_ms = 0.0
        self.max_ms = 0.0
        self.errors = 0

    def record(self, *, ms: float = 0.0, tokens: int = 0, disk_hit: bool = False,
               harness_hit: bool = False, error: bool = False) -> None:
        with self._lock:
            if disk_hit:
                self.disk_hits += 1
                return
            if error:
                self.errors += 1
            elif harness_hit:
                self.harness_hits += 1
            else:
                self.calls += 1
            self.tokens += int(tokens or 0)
            self.latency_ms += ms
            self.max_ms = max(self.max_ms, ms)

    def as_dict(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "disk_cache_hits": self.disk_hits,
                    "harness_cache_hits": self.harness_hits, "errors": self.errors,
                    "tokens": self.tokens, "latency_ms_sum": round(self.latency_ms, 1),
                    "latency_ms_max": round(self.max_ms, 1),
                    "latency_ms_mean": (round(self.latency_ms / self.calls, 1)
                                        if self.calls else 0.0)}


class CachedHarness:
    """What `extract_graph` is handed instead of the bare `Harness`: `complete_structured`
    consults the on-disk `ModelOutputCache` first, bounds concurrent model calls with a semaphore,
    optionally paces them to a rate, and meters every call. Everything else is delegated, so the
    harness's own routing, budgets, airgapped guard and in-memory cache all still apply — a disk
    hit simply never reaches it."""

    def __init__(self, inner, *, cache: Optional[ModelOutputCache] = None,
                 max_concurrent: int = 4, max_per_second: Optional[float] = None,
                 meter: Optional[ModelMeter] = None) -> None:
        self._inner = inner
        self._cache = cache
        self._sem = threading.BoundedSemaphore(max(1, int(max_concurrent)))
        self._interval = (1.0 / max_per_second) if max_per_second else 0.0
        self._pace_lock = threading.Lock()
        self._next_at = 0.0
        self.meter = meter or ModelMeter()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _model_for(self, task_class: str) -> str:
        resolve = getattr(self._inner, "_resolve", None)
        try:
            return str(resolve(task_class)) if callable(resolve) else type(self._inner).__name__
        except Exception:                                            # noqa: BLE001
            return "unresolved"

    def _pace(self) -> None:
        if not self._interval:
            return
        with self._pace_lock:
            now = time.monotonic()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self._interval
        if wait > 0:
            time.sleep(wait)

    def complete_structured(self, task_class, messages, response_model, *, strict=True,
                            seed=None, max_retries=2, source_id=None):
        key = parts = None
        if self._cache is not None:
            parts = ModelOutputCache.key_parts(task_class, self._model_for(task_class),
                                               list(messages),
                                               response_model.model_json_schema(), seed)
            key = ModelOutputCache.key(parts)
            hit = self._cache.get(key)
            if hit is not None and "parsed" in hit:
                try:
                    parsed = response_model.model_validate(hit["parsed"])
                except Exception:                                    # noqa: BLE001
                    parsed = None                    # the schema moved under this key: re-ask
                if parsed is not None:
                    from databridge_harness import StructuredResult, build_event

                    self.meter.record(disk_hit=True)
                    event = build_event("complete_structured", messages, hit.get("raw"),
                                        task_class=task_class, model=parts["model"], tokens=0,
                                        cost_usd=0.0, duration_ms=0.0,
                                        ladder_used=hit.get("ladder"),
                                        retries=hit.get("retries"), cached=True)
                    return StructuredResult(parsed=parsed, raw=hit.get("raw") or "",
                                            event=event, ladder_used=hit.get("ladder") or "L1",
                                            retries=int(hit.get("retries") or 0))
        with self._sem:
            self._pace()
            t0 = time.perf_counter()
            try:
                result = self._inner.complete_structured(
                    task_class, messages, response_model, strict=strict, seed=seed,
                    max_retries=max_retries, source_id=source_id)
            except Exception:
                self.meter.record(ms=(time.perf_counter() - t0) * 1000, error=True)
                raise
            ms = (time.perf_counter() - t0) * 1000
        event = getattr(result, "event", None)
        self.meter.record(ms=ms, tokens=getattr(event, "tokens", 0) or 0,
                          harness_hit=bool(getattr(event, "cached", False)))
        if key is not None:
            self._cache.put(key, {"key_parts": parts, "raw": result.raw,
                                  "parsed": result.parsed.model_dump(mode="json"),
                                  "ladder": result.ladder_used, "retries": result.retries})
        return result


__all__ = ["TASK_CLASSES", "detect", "default_harness", "ModelOutputCache", "ModelMeter", "CachedHarness",
           "EXTRACT_CACHE_VERSION"]
