"""Typed pipeline events — the replayable record of every step (02_TRD Task 1).

Every pipeline step emits a ``PipelineEvent``. ``inputs_hash``/``outputs_hash`` are
CONTENT hashes: they enable "same context + same seed → same output" reproducibility
claims and are the cache keys now. Stage-3 audit logs and the living-wiki auto-update
hooks both consume this event stream; the lineage DAG (09_FINAL_PRD §17.3) links event
hashes source→IR→plan→execution→validation→output.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


def content_hash(obj: Any) -> str:
    """Deterministic sha256 over a canonical JSON serialisation of ``obj``.

    Pydantic models are dumped in JSON mode; everything else falls back to ``str`` for
    non-JSON types. Keys are sorted so the hash is order-independent.
    """
    if isinstance(obj, BaseModel):
        payload: Any = obj.model_dump(mode="json")
    else:
        payload = obj
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PipelineEvent(BaseModel):
    """A typed, replayable record of one pipeline step (02_TRD Task 1).

    Emitted by every step; the harness (Task 2) fills ``model_used``/``tokens``/
    ``cost_usd`` for LLM calls, while deterministic steps leave them ``None``.
    """

    model_config = ConfigDict(frozen=True)

    step: str                                   # e.g. "schema_annotation", "rule_execute"
    inputs_hash: str
    outputs_hash: str
    task_class: Optional[str] = None
    model_used: Optional[str] = None
    tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[float] = None
    created_at: Optional[datetime] = None
    # Harness telemetry (07_PRD §2/§5): which structured-output rung produced the result,
    # how many L2 retries it cost, and whether the content-hash cache served it.
    ladder_used: Optional[str] = None           # "L1" | "L2" | "L3"
    retries: Optional[int] = None
    cached: Optional[bool] = None

    @classmethod
    def record(
        cls,
        step: str,
        inputs: Any,
        outputs: Any,
        *,
        task_class: Optional[str] = None,
        model_used: Optional[str] = None,
        tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        duration_ms: Optional[float] = None,
        created_at: Optional[datetime] = None,
        ladder_used: Optional[str] = None,
        retries: Optional[int] = None,
        cached: Optional[bool] = None,
    ) -> "PipelineEvent":
        """Build an event, content-hashing ``inputs``/``outputs`` for you."""
        return cls(
            step=step,
            inputs_hash=content_hash(inputs),
            outputs_hash=content_hash(outputs),
            task_class=task_class,
            model_used=model_used,
            tokens=tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            created_at=created_at,
            ladder_used=ladder_used,
            retries=retries,
            cached=cached,
        )
