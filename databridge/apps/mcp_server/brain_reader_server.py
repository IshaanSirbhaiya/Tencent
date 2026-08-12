"""The clinic MCP **reader** — the only clinic endpoint an agent is allowed to reach.

This process exposes the four read tools in ``capabilities.READ_CAPABILITIES`` and nothing else:

    brain_query(question, as_of?)      → grounded answer + citations
    brain_neighbors(entity, depth?)    → subgraph JSON
    brain_timeline(entity)             → bi-temporal history
    brain_path(from_entity, to_entity) → provenance path

``brain_ingest`` is not here. It runs in ``brain_writer_server.py``, a separate process, because
`docs/hackathon/GRAPH_AGENT_AND_MCP.md` §3 requires the capability boundary to be a process
boundary rather than a naming convention, and `REMEDIATION.md` §3.2 chose a self-hosted aggregator
specifically so that only this endpoint is registered in front of the agent (R-34).

Three things this file does, beyond leaving the write tool out:

* the tool bodies close over a :class:`~capabilities.ReadOnlyBrain`, not over ``BrainTools`` — so
  there is no attribute named ``brain_ingest`` reachable from inside this module at all;
* ``seal_registry`` compares the registered tools against the allow-list and **raises at boot** if
  they differ, so registering a mutating tool here is a startup failure rather than a review miss;
* the default ``BrainTools`` is built with **no harness and no registered sources**, so the write
  path this process does not expose has nothing to run with either.

None of that is a sandbox — see the honest scope note in ``capabilities.py``. The process split is
what makes the boundary real; this file makes wiring it wrongly hard.

"One brain, two mouths" is unchanged: these tools call the same ``BrainTools`` methods the FastAPI
``/brain/*`` routes call, with no logic forks.
"""
from __future__ import annotations

from typing import Optional

from brain_server import BrainTools
from capabilities import READ_CAPABILITIES, ReadOnlyBrain, seal_registry
from serving import (
    DEFAULT_READER_PORT,
    READER_PORT_ENV,
    fastmcp_kwargs,
    port_from_env,
    serve_streamable_http,
)

SERVER_NAME = "databridge-clinic-reader"


def default_reader_tools() -> BrainTools:
    """A seeded ``BrainTools`` with no ingestion capability configured.

    ``harness=None`` and ``sources={}`` are not decoration: ``BrainTools.brain_ingest`` refuses
    without a harness, and refuses any source that is not registered. So even a caller who
    reflected its way past :class:`ReadOnlyBrain` would find the write path inert in this process.
    """
    tools = BrainTools(harness=None, sources={})
    tools.load_seed()
    return tools


def build_reader_brain(tools: Optional[BrainTools] = None) -> ReadOnlyBrain:
    """Wrap ``BrainTools`` (or the seeded default) in the frozen read-only view."""
    return ReadOnlyBrain(tools if tools is not None else default_reader_tools())


def reader_port() -> int:
    return port_from_env(READER_PORT_ENV, DEFAULT_READER_PORT)


def build_reader_server(brain: Optional[ReadOnlyBrain] = None, *, port: Optional[int] = None):
    """Construct the read-only MCP server. Imports the SDK lazily so this module stays importable
    (gateway + tests) without ``mcp`` installed.

    The port and the DNS-rebinding allow-list come from ``serving.py``; **this** is the port the
    aggregator is registered against (``register --url http://host.docker.internal:<port>/mcp``).
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore

    brain = brain if brain is not None else build_reader_brain()
    mcp = FastMCP(SERVER_NAME, **fastmcp_kwargs(port if port is not None else reader_port()))

    # Written out one by one rather than looped, so each tool keeps a real signature for the
    # schema the agent is shown. `seal_registry` below is what stops the list drifting from the
    # allow-list — the explicitness is for the schema, not for the boundary.
    #
    # The DESCRIPTION comes from the manifest rather than a docstring here. Registering these
    # without one produced four tools that an aggregator listed with blank descriptions — a model
    # choosing between `brain_neighbors` and `brain_path` would have had nothing but the names to
    # go on, which is a broken tool surface even though every test passed. Taking the text from
    # `READ_CAPABILITIES` also means the description cannot drift from the allow-list entry that
    # authorises the tool: they are the same object.
    def _summary(name: str) -> str:
        return next(c.summary for c in READ_CAPABILITIES if c.name == name)

    @mcp.tool(description=_summary("brain_query"))
    def brain_query(question: str, as_of: Optional[str] = None) -> dict:
        return brain.brain_query(question, as_of)

    @mcp.tool(description=_summary("brain_neighbors"))
    def brain_neighbors(entity: str, depth: int = 1) -> dict:
        return brain.brain_neighbors(entity, depth)

    @mcp.tool(description=_summary("brain_timeline"))
    def brain_timeline(entity: str) -> dict:
        return brain.brain_timeline(entity)

    @mcp.tool(description=_summary("brain_path"))
    def brain_path(from_entity: str, to_entity: str) -> dict:
        return brain.brain_path(from_entity, to_entity)

    return seal_registry(mcp, READ_CAPABILITIES, process="reader")


def main() -> None:  # pragma: no cover (entrypoint)
    """Serve the reader over streamable HTTP — the transport the aggregator speaks."""
    serve_streamable_http(build_reader_server())


if __name__ == "__main__":  # pragma: no cover
    # Standalone launch: put the sibling packages on sys.path BEFORE the imports below
    # resolve. These servers are separate processes by design, so they have to be runnable
    # on their own — see _bootstrap.
    import pathlib as _pl
    import sys as _sys
    _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _bootstrap import ensure_path
    ensure_path()

    main()
