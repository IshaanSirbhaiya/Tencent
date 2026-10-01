"""Shared constants for the agent-plane tests.

A separate module rather than `conftest.py` for a mundane but real reason: the repo runs one
pytest session over many packages, `conftest.py` is not importable by name across them (several
exist, and the first on `sys.path` wins), and `from conftest import ...` therefore resolves to
whichever package happened to be collected first. This name is unique in the tree.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

FINANCE_PACK = "finance"
GOVERNMENT_PACK = "government"
FINANCE_AGENT = "credit_memo_drafter"
GOVERNMENT_AGENT = "permit_assessment_drafter"

#: A fixed instant, so a decision record is byte-stable across runs. A decision that reads
#: differently on two machines is not evidence.
FROZEN_NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


def now() -> datetime:
    return FROZEN_NOW


def declaration_path(pack: str, agent: str) -> Path:
    from databridge_agentdef import packs_root

    return packs_root() / pack / "agents" / f"{agent}.yaml"
