"""Make the MCP servers runnable as standalone processes.

`apps/gateway/serve.py` does this for the gateway. These servers needed it too and did not have
it: `python apps/mcp_server/brain_reader_server.py` died with
`ModuleNotFoundError: No module named 'databridge_intake'`, because nothing had put the sibling
packages on `sys.path`.

That is not a small inconvenience for this design. The reader, the writer and the mail sender are
**separate processes on purpose** — that separation is the capability boundary (R-34), and it is
the thing the architecture claims. A boundary you cannot start is a boundary that will get
collapsed into one process by whoever has to run the thing. So the entrypoints have to work on
their own, with no wrapper script and no environment variable to remember.

Kept in step with `serve.py` by `tests/test_mcp_server_paths.py`, which fails if the two lists
drift — the same guard `serve.py` already carries against the root pyproject, for the same reason:
the drift is invisible to pytest (which sets its own path) and fatal to the demo.
"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]          # databridge/

PACKAGES = ("packages/core", "packages/harness", "packages/ingest", "packages/semantic",
            "packages/convert", "packages/graph", "packages/intake", "packages/agent",
            "packages/outbox", "packages/evals",
            "apps/gateway", "apps/mcp_server", "apps/dispatcher")


def ensure_path() -> None:
    """Put every sibling package on `sys.path`, idempotently."""
    for name in PACKAGES:
        path = _ROOT / name
        assert path.exists(), f"_bootstrap: {name} is on the path list but does not exist"
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
