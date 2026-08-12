"""The clinic MCP **writer** — ingestion only, and not reachable by the agent.

One tool, ``capabilities.WRITE_CAPABILITIES``:

    brain_ingest(source_path)  → IngestResult   (the single write path, 04_PRD §9)

It used to sit in the same server as the read tools while that server's header called itself
"read-only by design" (R-34, `REMEDIATION.md` §3.2). Registering that server behind the aggregator
would have put a write tool behind the endpoint the agent may reach — the exact collapse
self-hosting was chosen to prevent — so ingestion now runs in its own process, with its own
registry, and **is not registered behind the agent's aggregator endpoint**.

What that does and does not buy:

* it *does* mean an agent connected to the reader endpoint has no ``brain_ingest`` to call, by
  construction rather than by convention, and that this process's registry is sealed against the
  write allow-list at boot (``seal_registry``);
* it does *not* make ingestion safe on its own. Who may connect here is a deployment question —
  this process is for the model-free ingestion path, and nothing in Python enforces that.

``brain_ingest`` keeps its own limits regardless of process: it refuses without a harness, and it
ingests inline text or a **registered** source only, never an arbitrary filesystem path.

The graph writes here are still the ones described in `CLAUDE.md`: extraction through the grounding
wall, which drops any value not present in the source. Nothing in this process can commit to the
evidence ledger — that stays behind ``POST /intake/{id}/confirm`` and its human attestation
(constraint 4).
"""
from __future__ import annotations

from typing import Optional

from brain_server import BrainTools
from capabilities import WRITE_CAPABILITIES, seal_registry
from serving import (
    DEFAULT_WRITER_PORT,
    WRITER_PORT_ENV,
    fastmcp_kwargs,
    port_from_env,
    serve_streamable_http,
)

SERVER_NAME = "databridge-clinic-writer"


def default_writer_tools() -> BrainTools:
    """A seeded ``BrainTools`` with the gold extraction harness and the demo source registered.

    ``sources`` is an ALLOW-LIST of ingestable documents, not a directory: ``brain_ingest`` never
    opens a caller-supplied path.
    """
    from databridge_evals.e_intake import FIXTURE_TEXT, gold_harness

    tools = BrainTools(harness=gold_harness(), sources={"demo_sample.txt": FIXTURE_TEXT})
    tools.load_seed()
    return tools


def writer_port() -> int:
    return port_from_env(WRITER_PORT_ENV, DEFAULT_WRITER_PORT)


def build_writer_server(tools: Optional[BrainTools] = None, *, port: Optional[int] = None):
    """Construct the ingestion-only MCP server. SDK imported lazily, as in the reader.

    It listens on its OWN port, distinct from the reader's. **Never register this URL with the
    aggregator**: the aggregator fronts the endpoint an agent connects to, and the entire point of
    the split is that a write tool is not behind it (R-34).
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore

    tools = tools if tools is not None else default_writer_tools()
    mcp = FastMCP(SERVER_NAME, **fastmcp_kwargs(port if port is not None else writer_port()))

    @mcp.tool()
    def brain_ingest(source_path: str) -> dict:
        return tools.brain_ingest(source_path)

    return seal_registry(mcp, WRITE_CAPABILITIES, process="writer")


def main() -> None:  # pragma: no cover (entrypoint)
    """Serve ingestion over streamable HTTP on the writer's own port. Whoever may reach this port
    may write to the graph, so it is a deployment decision — see the module docstring."""
    serve_streamable_http(build_writer_server())


if __name__ == "__main__":  # pragma: no cover
    main()
