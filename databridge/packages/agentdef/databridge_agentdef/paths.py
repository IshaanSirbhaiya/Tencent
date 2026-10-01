"""Where the agent plane reads from. Four roots, named once.

``packs/<pack>/agents/*.yaml``    the declarations (D11)
``packs/<pack>/purposes.yaml``    the closed purpose vocabulary (D5) — read via `databridge_adapter`
``packs/<pack>/evals/*.jsonl``    the golden sets an eval binding points at
``fixtures/packs/<pack>/``        the ESTATE: what each adapter actually advertises

The last one is the awkward but honest join. A capability grant is only meaningful if something
independently states what the adapter offers, and in this checkout the only such statement is the
estate's ``sources.yaml`` plus its ``adapters/*.yaml``. The compiler reads those rather than a
list retyped into the agent declaration, because a capability screen against a list the declaring
author also wrote is not a screen.
"""
from __future__ import annotations

import os
from pathlib import Path

# packages/agentdef/databridge_agentdef -> agentdef -> packages -> databridge/
_DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_PACKS_ROOT = _DATABRIDGE_ROOT / "packs"
DEFAULT_ESTATES_ROOT = _DATABRIDGE_ROOT / "fixtures" / "packs"

AGENTS_DIR = "agents"


def databridge_root() -> Path:
    """The repo directory every recorded path is made relative to."""
    return _DATABRIDGE_ROOT


def packs_root() -> Path:
    """The active packs directory. Shares `DATABRIDGE_PACKS_ROOT` with `databridge_graph.packs`
    so that "where do packs live" has one answer in the codebase, not two."""
    override = os.environ.get("DATABRIDGE_PACKS_ROOT")
    return Path(override) if override else DEFAULT_PACKS_ROOT


def estates_root() -> Path:
    """The directory holding one estate per pack (``fixtures/packs/<pack>/``)."""
    override = os.environ.get("DATABRIDGE_ESTATES_ROOT")
    return Path(override) if override else DEFAULT_ESTATES_ROOT


def relative_to_root(path: Path) -> str:
    """A repo-relative, forward-slashed path — what goes into a grant record.

    Absolute paths are machine facts; a grant is read on another machine, in a review, a year
    later. Falls back to the absolute form rather than raising, because a declaration loaded from
    outside the tree (a test fixture, a scratch directory) should still compile to a readable
    record.
    """
    p = Path(path).resolve()
    try:
        return p.relative_to(_DATABRIDGE_ROOT).as_posix()
    except ValueError:
        return p.as_posix()
