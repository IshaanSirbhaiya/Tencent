"""Shared test doubles: a scripted harness, and the sealed read adapters every test reads through.

Named ``agent_stubs`` rather than ``conftest`` on purpose. pytest puts every test directory on
``sys.path``, three packages in this repo already ship a ``conftest.py``, and ``import conftest``
would resolve to whichever one landed first — a collision that only appears when the whole suite
runs, which is the worst time to discover it.

Three deliberate choices here.

**The model is always a ``FakeBackend`` and the cache is always off.** Off, because ``Harness``
keys its cache on the message list and a scripted sequence must be consumed in the order the test
wrote it — a cache hit would silently skip a step and make an assertion pass for the wrong reason.

**Tools are stubs.** ``packages/agent`` imports no graph, so every safety property in this suite is
proved without one. A stub can also do things a real tool would not — return a row full of an
injected instruction, for instance — which is exactly what the injection suite needs.

**This module is itself scanned.** ``SealedReadAdapter.__init_subclass__`` reads the source of the
file an adapter is defined in and refuses transports and write-shaped calls. That applies here too,
which is the point: a test double is code the agent will call, and a test double with a socket in
it is the first place this design would rot.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from databridge_agent import (
    AGENT_TASK_CLASS,
    CandidateRead,
    ClinicStructuralRead,
    SealedReadAdapter,
    Tool,
    ToolResult,
    ToolRow,
)
from databridge_core import SourcePointer
from databridge_core.config import load_model_config
from databridge_harness import FakeBackend, Harness

# The agent's routing class. `config/models.yaml` is owned by the composition root, not by this
# package, and the line
#     agent_reasoning:  sonnet
# is pending there. Until it lands this shim adds it in-memory; the moment it lands the
# `setdefault` below becomes a no-op and these tests keep passing against the real table unchanged.
_PENDING_ROUTE_ALIAS = "sonnet"

NO_ARGUMENTS = {"type": "object", "properties": {}, "additionalProperties": False}


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


def report(*refs, reason: str = "test script") -> dict:
    """The answer step. ``refs`` are ``(tool, row, field)`` triples — the model's only vocabulary."""
    return {"report": [{"tool": t, "row": r, "field": f} for t, r, f in refs], "reason": reason}


# --- the stub adapters ------------------------------------------------------------

DOC_POINTER = SourcePointer(
    source_id="chit_01_meridian_referral.txt", kind="doc_span",
    document_id="chit_01_meridian_referral.txt", char_start=120, char_end=168)
# A graph traversal cites the pointers it walked over, the same shape `clinic_seed` emits.
BLOCKED_POINTER = SourcePointer(
    source_id="clinic_graph", kind="derived",
    derivation="traversal: intake -> blocked_on -> identity_check",
    derived_from=[DOC_POINTER])

BLOCKED_ROWS = (
    ToolRow(values={"intake_id": "intake:0001", "blocked_on": "identity_check",
                    "detail": "3 registry candidates; a human chooses at the counter"},
            value_sources={"intake_id": BLOCKED_POINTER, "blocked_on": BLOCKED_POINTER,
                           "detail": DOC_POINTER}),
    ToolRow(values={"intake_id": "intake:0002", "blocked_on": "unrecognised_issuer_code",
                    "detail": "issuer code on the chit has no row in the coverage table"},
            value_sources={"intake_id": BLOCKED_POINTER, "blocked_on": BLOCKED_POINTER,
                           "detail": DOC_POINTER}),
)


class RowsAdapter(SealedReadAdapter):
    """Returns rows a test already grounded. One class, several declared tools.

    Defined here rather than reused from ``databridge_agent.adapters`` so the suite exercises the
    class-definition scan on a module outside the package — which is the case that matters, since
    real adapters live in the composition root.
    """

    def __init__(self, capability, rows=(), note: str = "", *, name: str,
                 description: str = "Reads clinic data.", parameters: dict = None,
                 raises: Exception = None, returns=None) -> None:
        self.name = name
        self.description = description
        self.parameters = dict(parameters if parameters is not None else NO_ARGUMENTS)
        super().__init__(capability)
        self._rows = tuple(rows)
        self._note = note
        self._raises = raises
        self._returns = returns
        self.calls: list[dict] = []

    def read(self, **arguments) -> ToolResult:
        self.calls.append(dict(arguments))
        if self._raises is not None:
            raise self._raises
        if self._returns is not None:
            return self._returns
        return ToolResult(tool=self.name, rows=self._rows, note=self._note)


@dataclass
class Stub:
    """A tool plus its adapter, so a test can assert exactly what was executed.

    ``calls`` is the load-bearing half for the injection suite: proving the agent did not act means
    proving no callable ran that should not have, and the only callables in existence are these.
    """

    tool: Tool
    adapter: RowsAdapter
    calls: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.calls = self.adapter.calls


def make_stub(name: str, rows=(), *, capability=None, description: str = "Reads clinic data.",
              parameters: dict = None, note: str = "", raises: Exception = None,
              returns=None) -> Stub:
    adapter = RowsAdapter(capability or ClinicStructuralRead(), rows=rows, note=note, name=name,
                          description=description, parameters=parameters, raises=raises,
                          returns=returns)
    return Stub(tool=Tool(adapter=adapter), adapter=adapter)


def blocked_intakes_tool(rows=BLOCKED_ROWS) -> Stub:
    """A stub for the T-41 tool the agent's first playbook needs."""
    return make_stub("blocked_intakes", rows,
                     description="Intakes waiting on a human, and what each is waiting on.")


def candidate_tool(rows=(), name: str = "registry_candidates") -> Stub:
    """A candidate-returning read — the only identity-adjacent capability before an attestation.

    Its capability carries ``requires_human_selection: Literal[True]``, which the loop turns into
    the fixed, code-authored human-selection note on every answer built from it.
    """
    return make_stub(name, rows, capability=CandidateRead(),
                     description="Registry rows a document's printed name and date of birth are "
                                 "consistent with. Not a determination.")
