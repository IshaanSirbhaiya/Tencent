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

SERVER_NAME = "databridge-writer"


def writer_harness():
    """The extraction harness this process ingests with: the REAL model (routed through
    ``packages/harness`` by ``build_extraction_harness``) when the model ROUTED for extraction is
    reachable, and the deterministic gold backend only when it is not or ``DATABRIDGE_FORCE_GOLD=1``.
    The same decision the gateway's ``/brain/ingest`` makes, taken from the same function, so the
    two write paths cannot disagree about which model reads a document."""
    return writer_extraction()[0]


def writer_extraction() -> tuple:
    """``(harness, route)`` — the harness, and the record of which model extraction runs on and
    why. Routing is decided per EXTRACTION alias, independently of the brain-query model (Codex f2
    #7), and the route travels with every ingestion result (``extraction_route``) instead of being
    discarded here."""
    from databridge_evals.e_intake import gold_harness
    from databridge_gateway.llm import build_extraction_harness, detect_llm

    detected = detect_llm()
    harness = build_extraction_harness(detected)
    route = {"backend": "real" if harness is not None else "gold",
             "routing": detected.get("routing", {})}
    return (harness or gold_harness()), route


def default_writer_tools() -> BrainTools:
    """A ``BrainTools`` over the SHARED graph store (``WALNUT_GRAPH_DB_PATH``), so what this process
    ingests is what the gateway and the reader serve. The ENABLED packs' declared demo seeds and
    demo sources (``demo_seed.yaml``) are loaded only when ``WALNUT_LOAD_CLINIC_SEED=1``; this
    process names no pack's seed or fixture itself (Codex f2 #6).

    ``sources`` is an ALLOW-LIST of ingestable documents, not a directory: ``brain_ingest`` never
    opens a caller-supplied path.
    """
    from brain_server import demo_seed_enabled, demo_seed_sources, open_shared_store

    seeded = demo_seed_enabled()
    sources: dict = demo_seed_sources() if seeded else {}
    harness, route = writer_extraction()
    tools = BrainTools(open_shared_store(), harness=harness, sources=sources,
                       extraction_route=route)
    if seeded:
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
    # Standalone launch: put the sibling packages on sys.path BEFORE the imports below
    # resolve. These servers are separate processes by design, so they have to be runnable
    # on their own — see _bootstrap.
    import pathlib as _pl
    import sys as _sys
    _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _bootstrap import ensure_path
    ensure_path()

    main()
