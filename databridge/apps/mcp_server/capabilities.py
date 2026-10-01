"""The capability manifest for the clinic MCP servers — one allow-list per process.

R-34 (`REMEDIATION.md` §3.2). ``brain_server.py`` used to build ONE MCP server carrying all five
tools, ``brain_ingest`` among them, while its header called itself read-only. The plan is to
register "our read-only clinic server" behind a self-hosted aggregator that an agent may reach, and
`docs/hackathon/GRAPH_AGENT_AND_MCP.md` §3 rule 3 requires read tools and mutating tools to live in
**different processes** — "so the capability boundary is a process boundary rather than a naming
convention". A single endpoint fronting both collapses exactly that.

So there are now three servers, and this module is the list each one is built from:

    READ_CAPABILITIES   → brain_reader_server.py   (the agent-reachable process)
    WRITE_CAPABILITIES  → brain_writer_server.py   (the model-free ingestion process)
    SEND_CAPABILITIES   → mail_send_server.py      (the DISPATCHER-only process)

``SEND_CAPABILITIES`` is the sharpest of the three and is documented at its own declaration below.
Read that before adding anything to it.

**What is actually enforced, precisely — no more:**

1. Each builder seals its `FastMCP` instance against the allow-list here (``seal_registry``) and
   **raises at construction** if the registered tool names are not exactly that list. A tool added
   to a server but not to the manifest, or vice versa, is a startup failure, not a review miss.
2. The reader process hands its tool bodies a :class:`ReadOnlyBrain`, whose attribute surface *is*
   the read allow-list: ``brain.brain_ingest`` raises ``AttributeError``. The registry is bound
   once and frozen — there is no post-construction mutation (the R-07 finding against
   ``packages/agent``, applied here before it can happen).
3. The reader process's ``BrainTools`` is built with **no harness and no registered sources**, so
   the ingest path it does not expose could not run even if it were reached.
4. `tests/test_capability_boundary.py` fails if any tool on the read list mutates the store —
   measured by running it against a real seeded graph and comparing a full SQLite dump, so it fails
   for a *future* tool nobody has named yet, not only for today's five.

**What is NOT claimed.** This is Python: a determined caller inside the reader process can still
reach the underlying object through a bound method's ``__self__``. The boundary that matters is the
process one — the aggregator fronts the reader's endpoint and not the writer's, so what an agent
can call is decided by which socket it connected to. Everything here makes the *wiring* mistake
impossible; the *process* split makes the capability real.

``BrainTools`` itself is NOT split. Both servers call the same methods on the same class, because
`brain_server.py`'s "one brain, two mouths" rule — the MCP tools and the FastAPI ``/brain/*`` routes
run identical code with no logic forks — is worth more than the illusion of safety a forked
read-only copy of the graph logic would buy.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Capability:
    """One MCP tool: the name an agent calls, and the ``BrainTools`` method behind it.

    Deliberately carries no ``effect: Literal["read"]`` field. That was R-07's finding against the
    agent's tool registry: a label on an arbitrary callable asserts nothing, because nothing checks
    it. Read-ness here is *membership of the list this capability is in*, which decides which
    process the tool exists in — and it is checked behaviourally by the boundary test.
    """

    name: str
    method: str
    summary: str


# --- the agent-reachable surface ---------------------------------------------------
#
# Adding an entry here puts a tool in front of an LLM. The boundary test then runs it against a
# seeded graph and fails if the store changed, so a mutating addition cannot land quietly.
READ_CAPABILITIES: tuple[Capability, ...] = (
    Capability("brain_query", "brain_query",
               "Answer a question from the knowledge graph, with a citation per value."),
    Capability("brain_neighbors", "brain_neighbors",
               "The subgraph around an entity, to a given depth."),
    Capability("brain_timeline", "brain_timeline",
               "The bi-temporal version history of an entity."),
    Capability("brain_path", "brain_path",
               "The provenance path between two entities, with the pointers along it."),
)

# --- the ingestion surface — a different process ------------------------------------
#
# Reached by the model-free ingestion path only. This list is never registered behind the
# aggregator endpoint the agent connects to; that is the whole point of R-34.
WRITE_CAPABILITIES: tuple[Capability, ...] = (
    Capability("brain_ingest", "brain_ingest",
               "Extract a grounded subgraph from inline text or a registered source, and merge it "
               "into the store. The single write path (04_PRD §9)."),
)

# --- the send surface — a third process, and the one that can be irreversible ---------
#
# ``mail_send_server.py`` only. This list is NEVER registered behind the aggregator, and it is not
# a ``BrainTools`` method: sending is not something the clinic brain does, it is something a
# model-free dispatcher does to a row a named human approved.
#
# `docs/hackathon/GRAPH_AGENT_AND_MCP.md` §4.2 states the gate in one line — ``send_email``
# reachable **only** by the dispatcher, never the agent — and §3 gives the reason: patient
# documents are attacker-controlled text, so an agent that could send is one prompt injection away
# from mailing patient data out. The boundary is not this comment. It is that the aggregator is
# registered against the reader's socket and against nothing else (MCP_RUNBOOK.md §3), and that
# ``packages/agent`` refuses a tool named like a transport at registration
# (``tools.screen_declaration``) — which would refuse ``send_email`` by name if anybody tried.
SEND_CAPABILITIES: tuple[Capability, ...] = (
    Capability("send_email", "send_email",
               "Hand one already-approved message to the mail server. Reachable by the dispatcher "
               "only; it decides nothing and reads no outbox."),
)


# --- everything else on BrainTools --------------------------------------------------
#
# Public methods that back the HTTP routes and are NOT exposed over MCP at all. Named explicitly so
# that a new method on ``BrainTools`` fails `test_every_public_method_is_classified` until somebody
# decides which side of the boundary it belongs on. An unclassified method is an undecided one.
#
#   load_seed            writes the whole clinic seed into the store — a write, and not a tool
#   graph                whole-graph export for the React Flow canvas (large; not agent-shaped)
#   provenance_by_source aggregate for the UI's provenance panel
#   insights             the /brain insights feed
#   node_detail          the node-detail drawer
#
# `GRAPH_AGENT_AND_MCP.md` §4.1 plans more read tools (`coverage_gap`, `claim_draft`,
# `blocked_intakes`). Each becomes a READ_CAPABILITIES entry — and each then has to survive the
# no-mutation check before it can ship.
UNEXPOSED_METHODS: frozenset[str] = frozenset({
    "load_seed", "graph", "provenance_by_source", "insights", "node_detail",
})


def capability_names(caps: Iterable[Capability]) -> tuple[str, ...]:
    return tuple(c.name for c in caps)


READ_TOOL_NAMES: frozenset[str] = frozenset(capability_names(READ_CAPABILITIES))
WRITE_TOOL_NAMES: frozenset[str] = frozenset(capability_names(WRITE_CAPABILITIES))
SEND_TOOL_NAMES: frozenset[str] = frozenset(capability_names(SEND_CAPABILITIES))


class CapabilityBoundaryError(RuntimeError):
    """A server was built whose tool registry does not match its allow-list."""


def registered_tool_names(server: Any) -> frozenset[str]:
    """The tool names an MCP server actually exposes.

    ``FastMCP.list_tools()`` is a coroutine, which is unusable from a synchronous builder that may
    already be inside an event loop, so the tool manager is read directly and the coroutine is only
    the fallback. Both are the same registry.
    """
    manager = getattr(server, "_tool_manager", None)
    if manager is not None:
        return frozenset(t.name for t in manager.list_tools())
    import asyncio                               # pragma: no cover — SDK layout changed
    return frozenset(t.name for t in asyncio.run(server.list_tools()))


def seal_registry(server: Any, allowed: Iterable[Capability], *, process: str) -> Any:
    """Fail construction unless ``server``'s registered tools are exactly ``allowed``.

    Called by both builders after their ``@mcp.tool()`` definitions. A tool registered but not
    declared (the R-34 shape: a write tool sitting behind the read endpoint) and a tool declared
    but not registered (a read the agent silently cannot call) both raise here, at import/boot,
    rather than being noticed in review — or not.

    Kept out of the test suite deliberately: a check that only runs under pytest is a check that a
    production entrypoint does not have.
    """
    registered = registered_tool_names(server)
    expected = frozenset(capability_names(allowed))
    if registered != expected:
        extra = sorted(registered - expected)
        missing = sorted(expected - registered)
        raise CapabilityBoundaryError(
            f"{process} server registry does not match its allow-list "
            f"(capabilities.py). Unexpected tools: {extra or 'none'}; "
            f"declared but not registered: {missing or 'none'}. Every tool in a process must be "
            f"declared there — that declaration is what keeps read and write in different "
            f"processes (R-34).")
    return server


class ReadOnlyBrain:
    """The reader process's view of ``BrainTools``: exactly ``READ_CAPABILITIES``, frozen.

    Built by binding the allow-listed methods once. The ``BrainTools`` instance is not kept as an
    attribute and no write method is bound, so ``brain.brain_ingest`` — or any future mutating
    method — raises ``AttributeError`` instead of resolving. Attribute assignment is refused, so
    the registry cannot grow after construction.

    This is a wiring guarantee, not a sandbox: bound methods still carry ``__self__``. The process
    split is what makes the capability boundary real; this makes the mistake hard to make.
    """

    __slots__ = ("_calls",)

    def __init__(self, tools: Any) -> None:
        missing = [c.method for c in READ_CAPABILITIES
                   if not callable(getattr(tools, c.method, None))]
        if missing:
            raise CapabilityBoundaryError(
                f"read capabilities name methods that do not exist on {type(tools).__name__}: "
                f"{missing}")
        object.__setattr__(self, "_calls", MappingProxyType(
            {c.name: getattr(tools, c.method) for c in READ_CAPABILITIES}))

    # -- the read surface, and nothing else ------------------------------------------
    @property
    def calls(self) -> Mapping[str, Any]:
        """The bound read tools, by MCP tool name. Immutable."""
        return self._calls

    def __getattr__(self, name: str) -> Any:
        # Only reached when normal lookup fails — i.e. for anything that is not a read capability.
        if name == "_calls":                      # pragma: no cover — recursion guard
            raise AttributeError(name)
        try:
            return self._calls[name]
        except KeyError:
            raise AttributeError(
                f"{name!r} is not a read capability. The reader process exposes "
                f"{sorted(self._calls)}; anything that writes lives in the writer process "
                f"(capabilities.py, R-34).") from None

    def __setattr__(self, name: str, value: Any) -> None:
        raise CapabilityBoundaryError(
            "the read registry is frozen at construction — a capability set that can be extended "
            "after the fact is a capability set nobody can reason about (R-07)")

    def __delattr__(self, name: str) -> None:
        raise CapabilityBoundaryError("the read registry is frozen at construction")

    def __dir__(self) -> list[str]:
        return sorted({*self._calls, "calls"})

    def __repr__(self) -> str:
        return f"ReadOnlyBrain({', '.join(sorted(self._calls))})"
