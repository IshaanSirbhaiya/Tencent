"""§5.8 runtime traceability for adapter-agent syncs — which agent, and which model.

`RecordingHarness` wraps the harness the manager hands to ingestion. Every model call goes through
the harness, and the harness result already names what ran (`event.task_class`,
`event.model_used`, `event.tokens`, `event.cost_usd`, `event.cached`); the wrapper only READS
those fields off each result and delegates everything else untouched. It never picks, changes or
defaults a model — a call whose result does not name its model is recorded with `model: None`.

`produced_by(...)` shapes the §5.8 record:
`{agent, agent_version, task_class, model, prompt_version, run_id, at}` plus the per-call list.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

_RECORDED = ("complete_structured", "complete_text", "embed")


class RecordingHarness:
    """A transparent proxy over a harness that records what each model call reports."""

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_calls", [])
        object.__setattr__(self, "_lock", threading.Lock())

    @property
    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._calls)

    def _record(self, method: str, args: tuple, kwargs: dict, result: Any) -> None:
        event = getattr(result, "event", None)
        task_class = getattr(event, "task_class", None)
        if task_class is None:
            task_class = kwargs.get("task_class") or (args[0] if args and
                                                      isinstance(args[0], str) else None)
        rec = {"method": method, "task_class": task_class,
               "model": getattr(event, "model_used", None),
               "tokens": getattr(event, "tokens", None),
               "cost_usd": getattr(event, "cost_usd", None),
               "cached": getattr(event, "cached", None)}
        with self._lock:
            self._calls.append(rec)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in _RECORDED or not callable(attr):
            return attr

        def recorded(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            self._record(name, args, kwargs, result)
            return result

        return recorded

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)


def produced_by(*, agent: str, agent_version: Optional[str], run_id: Optional[str], at: str,
                calls: Optional[list[dict[str, Any]]] = None, model_assisted: bool,
                ingest: Any = None) -> dict[str, Any]:
    """The §5.8 record for one sync step. `task_class`/`model` are the distinct values the calls
    reported (a single value when there was one, a list when several, `None` when none)."""
    calls = list(calls or [])

    def distinct(key: str) -> Any:
        vals = sorted({str(c[key]) for c in calls if c.get(key) is not None})
        return None if not vals else (vals[0] if len(vals) == 1 else vals)

    tokens = [c["tokens"] for c in calls if isinstance(c.get("tokens"), int)]
    return {"agent": agent, "agent_version": agent_version,
            "task_class": distinct("task_class"), "model": distinct("model"),
            "prompt_version": None, "run_id": run_id, "at": at,
            "model_assisted": model_assisted, "model_calls": len(calls),
            # a sum over only SOME calls would understate; unknown stays unknown (P17)
            "tokens": sum(tokens) if calls and len(tokens) == len(calls) else None,
            "ingest_produced_by": ingest}


def models_used(steps: list[dict[str, Any]]) -> list[str]:
    out: set[str] = set()
    for s in steps:
        m = (s.get("produced_by") or {}).get("model")
        if isinstance(m, str):
            out.add(m)
        elif isinstance(m, list):
            out.update(str(x) for x in m)
    return sorted(out)


__all__ = ["RecordingHarness", "produced_by", "models_used"]
