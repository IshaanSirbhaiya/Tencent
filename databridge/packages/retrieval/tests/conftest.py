"""Fixtures for packages/retrieval.

**Import bootstrap.** `packages/retrieval` is not yet on the root `pythonpath` (the coordinator
registers it together with `apps/gateway/serve.py::_PACKAGES`, which a test asserts agree), so this
conftest puts the package and its siblings on `sys.path`, as `packages/materialise` does.
`packages/mcp_ingest` is deliberately NOT added: these tests exercise the retrieval package's own
fallback screen loader over `packs/workspace/screen.yaml`, hermetic from a sibling in flux.

Every test gets its own capture dir and passages dir (env overrides), and a harness backed by
`FakeBackend` — no test calls a live model or the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DATABRIDGE = _PACKAGE_ROOT.parents[1]
for _p in (_PACKAGE_ROOT, _DATABRIDGE / "packages" / "core", _DATABRIDGE / "packages" / "harness"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

WORKSPACE_SCREEN = _DATABRIDGE / "packs" / "workspace" / "screen.yaml"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    cap, pas = tmp_path / "capture", tmp_path / "passages"
    cap.mkdir()
    monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(cap))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(pas))
    monkeypatch.delenv("WALNUT_EMBED_OFFLINE", raising=False)
    return cap, pas


@pytest.fixture
def screen():
    from databridge_retrieval.screen import _local

    return _local(WORKSPACE_SCREEN)


@pytest.fixture
def fake_harness():
    from databridge_harness import FakeBackend, Harness

    def make(embedder=None, responses=None):
        backend = FakeBackend(responses, embedder=embedder)
        return Harness(backend, profile="cloud", cache=None), backend

    return make


@pytest.fixture(autouse=True)
def _reset_runtime():
    from databridge_retrieval import configure
    from databridge_retrieval.vectors import MATRICES

    yield
    configure(harness=None)
    MATRICES.clear()
