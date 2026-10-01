"""Run the gateway with uvicorn (dev/demo launcher).

The repo uses pytest `pythonpath` instead of editable installs; this launcher mirrors that so
`python serve.py` works without `uv`/installs. Binds 127.0.0.1 only (D-FE-6).

    python serve.py            # serves http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]          # databridge/
# Must stay in step with the root pyproject's `pythonpath`. It drifted once: `packages/intake` was
# added there (so the whole suite passed) but not here, and the server then failed to boot with
# `No module named 'databridge_intake'` — invisible to pytest, fatal to the demo. The assertion
# below makes that drift impossible to ship.
# `packages/outbox` and `apps/dispatcher` are here because the pin below requires this list to
# cover the pytest one, not because the gateway sends anything: the send path is a SEPARATE
# PROCESS (apps/dispatcher) with its own store handle, and importability is not reachability.
_PACKAGES = ("packages/core", "packages/harness", "packages/ingest", "packages/semantic",
             "packages/convert", "packages/graph", "packages/intake", "packages/agent",
             "packages/adapter", "packages/agentdef", "packages/materialise",
             "packages/outbox", "packages/evals", "packages/orchestrator",
             "packages/workflow_spec", "packages/mcp_ingest", "packages/retrieval",
             "packages/agent_runtime", "packages/automation", "packages/adapter_agents",
             "packages/federated",
             "apps/gateway", "apps/mcp_server", "apps/dispatcher")
for _p in _PACKAGES:
    _path = _ROOT / _p
    assert _path.exists(), f"serve.py: {_p} is on the path list but does not exist"
    sys.path.insert(0, str(_path))

# Load a local .env if present (optional dep — env vars work without python-dotenv).
try:
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

# Announce the LLM backend on startup (NEVER the key itself). Detection mirrors gateway/llm.py.
_backend = ("openai" if os.getenv("OPENAI_API_KEY")
            else "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "fake")
print(f"LLM backend: {_backend} (profile: cloud)")

# ---------------------------------------------------------------------------------------------
# Which graph the gateway serves.
#
# ONE shared store (KNOWLEDGE_SYSTEM_PRINCIPLES §6 Batch 1, contract C1): `WALNUT_GRAPH_DB_PATH`,
# default `var/graph/workspace.db` — resolved in `brain_server.graph_db_path`, the same function
# the MCP servers and the ingest CLI use, so every process lands on the same file.
#
# This launcher USED to pick the largest `var/graph/*.db` when the env var was unset. With one
# shared, writable store that would be wrong in a way nobody would see: the user's ingested
# sources would be written into whichever demo estate happened to be biggest. Those graphs are
# kept (nothing here deletes them) and listed, so the operator can point at one on purpose.
def _announce_graph_db() -> None:
    chosen = os.environ.get("WALNUT_GRAPH_DB_PATH") or str(_ROOT / "var" / "graph" / "workspace.db")
    print(f"[serve] graph store -> {chosen}  (set WALNUT_GRAPH_DB_PATH to override; clinic demo "
          f"seed {'ON' if os.environ.get('WALNUT_LOAD_CLINIC_SEED') == '1' else 'off'} — "
          f"WALNUT_LOAD_CLINIC_SEED=1 to load it)")
    others = sorted(q.name for q in (_ROOT / "var" / "graph").glob("*.db")
                    if q.is_file() and str(q.resolve()) != str(pathlib.Path(chosen).resolve()))
    if others:
        print(f"[serve] other built graphs on disk (not served): {', '.join(others)}")


_announce_graph_db()

import uvicorn  # noqa: E402

from databridge_gateway.main import create_app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="info")
