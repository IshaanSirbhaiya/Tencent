"""Fixtures only. The test doubles live in ``agent_stubs`` — its docstring says why they are not
in this file (three packages in this repo ship a ``conftest.py``, and ``import conftest`` resolves
to whichever landed on ``sys.path`` first)."""
from __future__ import annotations

import pytest
from agent_stubs import Stub, blocked_intakes_tool

from databridge_agent import ToolRegistry


@pytest.fixture
def blocked_stub() -> Stub:
    return blocked_intakes_tool()


@pytest.fixture
def registry(blocked_stub: Stub) -> ToolRegistry:
    return ToolRegistry([blocked_stub.tool])
