"""Internal Brain leak / differential / injection / utility eval (plan §2 P1, P2, P7, P8; §4.6; §8).

``python -m evals.internal_brain.run`` from ``databridge/``. Importing this package puts the IB
app, the mock platforms, the Walnut libraries it imports and the IB e2e harness on ``sys.path``
(the same list as ``apps/internal_brain/tests/conftest.py``), so no editable install is needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]            # databridge/
HERE = Path(__file__).resolve().parent
WORLD = ROOT / "fixtures" / "enterprise" / "world.json"
GENERATOR = ROOT / "fixtures" / "enterprise" / "generate.py"
IB_TESTS = ROOT / "apps" / "internal_brain" / "tests"

for _rel in ("apps/internal_brain", "apps/mock_platforms", "packages/core", "packages/harness",
             "packages/adapter", "packages/retrieval", "apps/internal_brain/tests"):
    _p = str(ROOT / _rel)
    if _p not in sys.path:
        sys.path.insert(0, _p)
