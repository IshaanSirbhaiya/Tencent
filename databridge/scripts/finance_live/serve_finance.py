"""Boot the Walnut gateway for the FINANCE live demo on its own port (default 8100).

    python scripts/finance_live/serve_finance.py [--port 8100] [--frontend-port 3100]

Started by start-walnut-finance.ps1, which sets every WALNUT_* location to var/finance/ first.

Why this wrapper exists instead of `apps/gateway/serve.py` (setup only, no product code changed):
  * serve.py binds port 8000 unconditionally — the workspace demo's port, which must not be touched;
  * the gateway's CORS allow-list is fixed to the :3000 origins (main.py, D-FE-6), so a second
    frontend on :3100 would be refused by the browser. This wrapper ADDS one CORS middleware for
    the finance frontend's two loopback origins only; the :3000 allow-list is left as it is.
It does the same path setup and `.env` loading as serve.py (the model keys are read from
databridge/.env and are never printed).
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]              # databridge/
_PACKAGES = ("packages/core", "packages/harness", "packages/ingest", "packages/semantic",
             "packages/convert", "packages/graph", "packages/intake", "packages/agent",
             "packages/adapter", "packages/agentdef", "packages/materialise",
             "packages/outbox", "packages/evals", "packages/orchestrator",
             "packages/workflow_spec", "packages/mcp_ingest", "packages/retrieval",
             "packages/agent_runtime", "packages/automation", "packages/adapter_agents",
             "packages/federated",
             "apps/gateway", "apps/mcp_server", "apps/dispatcher")
for _p in _PACKAGES:
    sys.path.insert(0, str(ROOT / _p))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--frontend-port", type=int, default=3100)
    a = ap.parse_args()
    if a.port in (8000, 8014, 8015) or a.frontend_port == 3000:
        sys.exit("refusing a port the workspace demo or the E2E harness uses")
    if os.environ.get("DATABRIDGE_PACKS") != "finance":
        sys.exit("DATABRIDGE_PACKS must be 'finance' for this process (start-walnut-finance.ps1 "
                 "sets it); refusing to start against another pack's state")
    os.chdir(ROOT)
    backend = ("openai" if os.getenv("OPENAI_API_KEY")
               else "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "fake")
    print(f"[finance] LLM backend: {backend}   graph: {os.environ.get('WALNUT_GRAPH_DB_PATH')}",
          flush=True)

    import uvicorn
    from fastapi.middleware.cors import CORSMiddleware

    from databridge_gateway.main import create_app

    app = create_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{a.frontend_port}",
                       f"http://127.0.0.1:{a.frontend_port}"],
        allow_methods=["*"], allow_headers=["*"],
    )
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="info")


if __name__ == "__main__":
    main()
