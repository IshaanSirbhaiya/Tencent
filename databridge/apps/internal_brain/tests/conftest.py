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
def _no_standin_model_from_the_environment(monkeypatch, tmp_path_factory):
    """No test builds an app that reaches a model from the developer's shell: the stand-in's key,
    switches and caps, and the TokenHub / OpenAI-compatible settings are cleared for every test (a
    test that wants one sets them). The stand-in's spend ledger goes to a NEW per-test directory,
    so no test writes into the repo's var/ and no two tests share a ledger."""
    for k in ("ANTHROPIC_API_KEY", "IB_CLAUDE", "IB_CLAUDE_BUDGET_USD", "IB_CLAUDE_FAST",
              "IB_CLAUDE_EFFORT", "IB_CLAUDE_ANSWER_EFFORT", "IB_CLAUDE_USER_LIMIT",
              "IB_MODEL_CAP_USD", "IB_MODEL_DAILY_CAP_USD", "IB_MODEL_RPM", "IB_MODEL_USER_RPM",
              "TOKENHUB_API_KEY", "TOKENHUB_BASE", "OPENAI_API_KEY", "OPENAI_API_BASE", "IB_MODELS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("IB_MODEL_GUARD_DIR", str(tmp_path_factory.mktemp("model_guard") / "guard"))
