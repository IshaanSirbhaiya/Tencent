import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]
_AGENTDEF = _PKG.parent / "agentdef"
for p in (_PKG, _AGENTDEF):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
