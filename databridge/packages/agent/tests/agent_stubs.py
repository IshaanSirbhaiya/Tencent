"""Shared test doubles: a scripted harness, and the stub tools every test reads through.

Named ``agent_stubs`` rather than ``conftest`` on purpose. pytest puts every test directory on
``sys.path``, three packages in this repo already ship a ``conftest.py``, and ``import conftest``
would resolve to whichever one landed first — a collision that only appears when the whole suite
runs, which is the worst time to discover it.

Two deliberate choices here.

**The model is always a ``FakeBackend`` and the cache is always off.** Off, because ``Harness``
keys its cache on the message list and a scripted sequence must be consumed in the order the test
wrote it — a cache hit would silently skip a step and make an assertion pass for the wrong reason.

**Tools are stubs.** ``packages/agent`` imports no graph, so every safety property in this suite is
proved without one. A stub can also do things a real tool would not — return a row full of an
injected instruction, for instance — which is exactly what the injection suite needs.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from databridge_agent import AGENT_TASK_CLASS, Tool, ToolResult
from databridge_core import SourcePointer
from databridge_core.config import load_model_config
from databridge_harness import FakeBackend, Harness

# The agent's routing class. `config/models.yaml` is owned by the composition root, not by this
# package, and the line
#     agent_reasoning:  sonnet
# is pending there. Until it lands this shim adds it in-memory; the moment it lands the
# `setdefault` below becomes a no-op and these tests keep passing against the real table unchanged.
_PENDING_ROUTE_ALIAS = "sonnet"


def routing_config() -> dict:
    cfg = copy.deepcopy(load_model_config())
    cfg["routing"].setdefault(AGENT_TASK_CLASS, _PENDING_ROUTE_ALIAS)
    return cfg


def scripted(*responses) -> tuple[Harness, FakeBackend]:
    """A harness that returns ``responses`` in order (the last repeats once exhausted)."""
    texts = [r if isinstance(r, str) else json.dumps(r) for r in responses]
    backend = FakeBackend(texts)
    return Harness(backend, profile="cloud", config=routing_config(), cache=None), backend


def plan(tool: str | None, **arguments) -> dict:
    return {"tool": tool, "arguments": arguments, "reason": "test script"}


def answer(text: str) -> dict:
    return {"answer": text}


# --- the stub tools ---------------------------------------------------------------

DOC_POINTER = SourcePointer(
    source_id="chit_01_meridian_referral.txt", kind="doc_span",
    document_id="chit_01_meridian_referral.txt", char_start=120, char_end=168)
# A graph traversal cites the pointers it walked over, the same shape `clinic_seed` emits.
BLOCKED_POINTER = SourcePointer(
    source_id="clinic_graph", kind="derived",
    derivation="traversal: intake -> blocked_on -> identity_check",
    derived_from=[DOC_POINTER])

BLOCKED_ROWS = (
    {"intake_id": "intake:0001", "blocked_on": "identity_check",
     "detail": "3 registry candidates; a human selects one in person"},
    {"intake_id": "intake:0002", "blocked_on": "unrecognised_issuer_code",
     "detail": "issuer code on the chit has no row in the coverage table"},
)


@dataclass
class Stub:
    """A tool plus its call log, so a test can assert exactly what was executed.

    The log is the load-bearing half for the injection suite: proving the agent did not act means
    proving no callable ran that should not have, and the only callables in existence are these.
    """

    tool: Tool
    calls: list[dict]


def blocked_intakes_tool(rows=BLOCKED_ROWS, citations=(BLOCKED_POINTER, DOC_POINTER)) -> Stub:
    """A stub for the T-41 tool the agent's first playbook needs."""
    calls: list[dict] = []

    def call(**kwargs) -> ToolResult:
        calls.append(kwargs)
        return ToolResult(tool="blocked_intakes", rows=tuple(rows), citations=tuple(citations))

    return Stub(
        tool=Tool(name="blocked_intakes",
                  description="Intakes waiting on a human, and what each is waiting on.",
                  parameters={"type": "object", "properties": {}},
                  call=call),
        calls=calls)
