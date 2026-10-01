"""pytest wiring for the agent runtime tests. The fixtures and helpers live in
`agent_runtime_helpers.py` under a distinctive module name: several packages in this repo have a
`conftest.py`, and `from conftest import …` would resolve to whichever one was imported first when
the whole suite runs together."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from agent_runtime_helpers import (  # noqa: E402,F401 - fixtures are registered by import
    compile_inputs,
    gateway,
    gov_root,
    store,
    workspace_vocab,
)
