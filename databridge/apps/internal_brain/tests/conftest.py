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
