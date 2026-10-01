"""`databridge_orchestrator` — the main agent (PHASE_PLAN / the ask-the-fleet workstream).

Closes the loop nothing in this repo ran before: a question comes in, the `Orchestrator` decides
which adapters can answer it from their own declared capability matrix, hands each one's
`AdapterShell` a brief, takes the `CapabilityRequest` or the escalation the Shell produced, and lets
the adapter's own `PolicyEngine`-backed `resolve()` decide what actually comes back. See
`orchestrator.py` for the full account of what stays true and why.

This package holds no credential, no driver, and no `PolicyEngine` of its own — every one of those
already exists in `databridge_adapter` and is reused, not rebuilt.
"""
from __future__ import annotations

from .orchestrator import (
    AdapterHandle,
    AskResult,
    GroundedCell,
    Orchestrator,
    SourceOutcome,
    TraceSink,
)
from .selection import overlap_score, select_sources, source_vocabulary, tokenize

__all__ = [
    "Orchestrator",
    "AdapterHandle",
    "AskResult",
    "SourceOutcome",
    "GroundedCell",
    "TraceSink",
    "select_sources",
    "source_vocabulary",
    "overlap_score",
    "tokenize",
]
