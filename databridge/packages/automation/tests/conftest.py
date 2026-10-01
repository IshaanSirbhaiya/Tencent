"""sys.path shim so `python -m pytest packages/automation` works without an install.

The package and the siblings it imports (workflow_spec, agentdef, adapter, harness, core, and
agent_runtime — the one declaration generator and capability namer, W2) are put
on `sys.path` if they are not already there. Test-only helpers live in `automation_testing.py`
(a unique module name: the repo runs one pytest session over many packages)."""
from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
_PACKAGES = _PKG.parent
for _p in (_PKG, _PKG / "tests", _PACKAGES / "workflow_spec", _PACKAGES / "agentdef",
           _PACKAGES / "adapter", _PACKAGES / "harness", _PACKAGES / "core",
           _PACKAGES / "agent_runtime", _PACKAGES / "outbox", _PACKAGES / "graph"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _deployment_timezone(monkeypatch):
    """The test deployment CONFIGURES its zone, as a real one must (R-017 #5): with no configured
    zone and none in the sentence, the compiler asks. Tests that exercise that unset it."""
    monkeypatch.setenv("WALNUT_TIMEZONE", "Asia/Singapore")
