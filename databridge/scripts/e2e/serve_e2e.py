"""Boot the Walnut gateway for the E2E proof on a private port with private runtime dirs.

    python scripts/e2e/serve_e2e.py [--port 8012]

Same env as start-walnut-kg.ps1 (workspace pack, engine on, dev auth token) but every runtime
path points into the e2e scratch dir. Adds ONE e2e-only route, POST /__e2e/tick {"now": iso},
which calls the trigger engine's `tick(now)` — the "fast-forward the engine clock" hook for the
schedule scenario. Nothing in product code is changed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import ROOT, setup_env  # noqa: E402

E = setup_env()
os.chdir(ROOT)
os.environ.setdefault("WALNUT_AUTOMATION_ENGINE", "1")
# Run 2+: authenticate like a real deployment — WALNUT_API_TOKENS (a principal directory) and the
# operator's federated grants, from <run>/auth/*.json written by the orchestrator. Never the dev
# token. Run 1 (no auth dir) keeps the dev token it was run with.
_auth = E / "auth"
if (_auth / "api_tokens.json").is_file():
    import json as _json
    os.environ["WALNUT_API_TOKENS"] = (_auth / "api_tokens.json").read_text(encoding="utf-8")
    if (_auth / "federated_grants.json").is_file():
        os.environ["WALNUT_FEDERATED_GRANTS"] = (_auth / "federated_grants.json").read_text(
            encoding="utf-8")
    os.environ.pop("WALNUT_DEV_AUTH", None)
    _json.loads(os.environ["WALNUT_API_TOKENS"])
    # Run 3+: operators, operator-mode registration (the operator's manifest), the run's own
    # approval key and the synthetic DB passwords, exactly as start-walnut-kg.ps1 sets them.
    if (_auth / "operators.json").is_file():
        os.environ["WALNUT_OPERATORS"] = (_auth / "operators.json").read_text(encoding="utf-8")
        os.environ["WALNUT_SOURCE_REGISTRATION"] = "operator"
        os.environ["WALNUT_APPROVAL_KEY_FILE"] = str(E / "keys" / "approval.key")
        os.environ.pop("WALNUT_APPROVAL_KEY", None)
        os.environ.setdefault("WALNUT_DEMO_PG_READER_PASSWORD", "walnut_reader_synthetic")
        os.environ.setdefault("WALNUT_DEMO_PG_WRITER_PASSWORD", "walnut_agent_writer_synthetic")
else:
    os.environ["WALNUT_DEV_AUTH"] = "1"

import uvicorn  # noqa: E402
from datetime import datetime  # noqa: E402
from fastapi import Body  # noqa: E402

from databridge_gateway.main import create_app  # noqa: E402


def build():
    app = create_app()

    @app.post("/__e2e/tick")
    async def _e2e_tick(body: dict = Body(...)):
        rt = app.state.platform
        now = datetime.fromisoformat(body["now"].replace("Z", "+00:00"))
        rt.reload_engine()
        recs = await rt.engine.tick(now)
        if body.get("drain", True):
            await rt.engine.drain()
        return {"records": recs}

    @app.post("/__e2e/poll_events")
    async def _e2e_poll():
        rt = app.state.platform
        recs = await rt.engine.poll_events()
        await rt.engine.drain()
        return {"records": recs}

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8012)
    a = ap.parse_args()
    assert a.port not in (8000, 3000)
    print(f"[e2e] scratch={E} port={a.port} graph={os.environ['WALNUT_GRAPH_DB_PATH']}", flush=True)
    uvicorn.run(build(), host="127.0.0.1", port=a.port, log_level="info")
