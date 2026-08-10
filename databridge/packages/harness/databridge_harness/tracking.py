"""Event emission — the harness emits events, not the call sites (07_PRD §10 mode 4).

Centralising construction here is what lets a coverage test assert ``event count ==
call count``: if every call goes through the harness and the harness always builds an
event, no call path can silently skip cost/telemetry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from databridge_core import PipelineEvent


def build_event(
    step: str,
    inputs: Any,
    outputs: Any,
    *,
    task_class: str,
    model: str,
    tokens: int,
    cost_usd: float,
    duration_ms: float,
    ladder_used: Optional[str] = None,
    retries: Optional[int] = None,
    cached: bool = False,
) -> PipelineEvent:
    """Build the PipelineEvent for one harness call (07_PRD §2/§5)."""
    return PipelineEvent.record(
        step,
        inputs,
        outputs,
        task_class=task_class,
        model_used=model,
        tokens=tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        created_at=datetime.now(),
        ladder_used=ladder_used,
        retries=retries,
        cached=cached,
    )
