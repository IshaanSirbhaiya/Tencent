"""Put the IB package and the Walnut libraries it imports on sys.path (no editable install needed).

Kept local to this test tree so the root ``pyproject.toml`` pythonpath (and the gateway's
``serve.py`` mirror of it) are not touched (plan C0; Opus review H7).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]          # databridge/
for rel in ("apps/internal_brain", "apps/mock_platforms", "packages/core", "packages/harness",
            "packages/adapter", "packages/retrieval"):
    p = str(_ROOT / rel)
    if p not in sys.path:
        sys.path.insert(0, p)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_standin_model_from_the_environment(monkeypatch):
    """No test builds an app that reaches the opt-in stand-in model from the developer's shell:
    the key and the switches are cleared for every test (a test that wants one sets them)."""
    for k in ("ANTHROPIC_API_KEY", "IB_CLAUDE", "IB_CLAUDE_BUDGET_USD", "IB_CLAUDE_FAST",
              "IB_CLAUDE_EFFORT", "IB_CLAUDE_ANSWER_EFFORT", "IB_CLAUDE_USER_LIMIT"):
        monkeypatch.delenv(k, raising=False)
