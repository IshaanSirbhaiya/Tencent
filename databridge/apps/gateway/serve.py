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
_PACKAGES = ("packages/core", "packages/harness", "packages/ingest", "packages/semantic",
             "packages/convert", "packages/graph", "packages/intake", "packages/agent",
             "packages/evals",
             "apps/gateway", "apps/mcp_server")
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

import uvicorn  # noqa: E402

from databridge_gateway.main import create_app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="info")
