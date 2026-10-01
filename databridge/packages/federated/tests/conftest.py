"""pytest wiring for the federated tests. Helpers live in `fed_testkit.py` (a distinctive module
name: several packages have a `conftest.py`, and `from conftest import …` would resolve to
whichever was imported first when the whole suite runs together)."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PACKAGES = _HERE.parents[1]
for _p in [str(_HERE)] + [str(_PACKAGES / n) for n in (
        "federated", "agent_runtime", "workflow_spec", "agentdef", "adapter", "outbox", "graph",
        "core", "harness", "convert", "mcp_ingest", "adapter_agents", "retrieval")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fed_testkit import env, world  # noqa: E402,F401 - fixtures are registered by import
