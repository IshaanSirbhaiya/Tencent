"""Put ``apps/mock_platforms`` on sys.path and provide a mock app over a tiny hand-written world.

``mini_world.json`` follows CONTRACTS §1 and contains every permission edge case the mock must
compute: a nested group (grp-payments-eng inside grp-eng), a nested page restriction, a trashed and
an archived page, a private channel, a guest, a Slack Connect user, a security-level issue, a
limited-access Drive folder, an "anyone" file, a domain file and a deactivated user.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]                     # databridge/apps/mock_platforms
for _p in (_PKG, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi.testclient import TestClient  # noqa: E402

from mock_platforms.app import create_app  # noqa: E402

from mockplat_helpers import ADMIN, SECRET, SVC  # noqa: E402

MINI_WORLD = Path(__file__).with_name("mini_world.json")


class Sink:
    """In-process webhook receiver: records (platform, headers, body)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def __call__(self, platform: str, headers: dict[str, str], body: bytes) -> None:
        self.calls.append((platform, headers, body))

    def payloads(self) -> list[dict]:
        return [json.loads(b) for _, _, b in self.calls]

    def clear(self) -> None:
        self.calls.clear()


@pytest.fixture
def world_path(tmp_path) -> Path:
    p = tmp_path / "world.json"
    shutil.copyfile(MINI_WORLD, p)
    return p


@pytest.fixture
def sink() -> Sink:
    return Sink()


@pytest.fixture
def client(world_path, sink):
    app = create_app(world_path, webhook_sink=sink, svc_token=SVC, admin_token=ADMIN,
                     webhook_secret=SECRET, webhook_url="")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def svc() -> dict[str, str]:
    return {"Authorization": f"Bearer {SVC}"}


@pytest.fixture
def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


@pytest.fixture
def mini() -> dict:
    return json.loads(MINI_WORLD.read_text(encoding="utf-8"))
