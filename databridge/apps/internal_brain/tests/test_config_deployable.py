"""A deployed build refuses dev keys, and refuses demo sign-in without a real passcode."""
from __future__ import annotations

import pytest

from databridge_ib.config import IbConfig

STRONG = "k" * 40


MOCKS = {"MOCK_SVC_TOKEN": "s" * 32, "MOCK_ADMIN_TOKEN": "a" * 32, "MOCK_WEBHOOK_SECRET": "w" * 32}


def _cfg(monkeypatch, mocks=True, **env):
    for k in ("IB_DEPLOYED", "IB_DEMO_LOGIN", "IB_DEMO_PASSCODE", "IB_TOKEN_KEY", "IB_LEDGER_KEY", *MOCKS):
        monkeypatch.delenv(k, raising=False)
    if mocks:
        env = {**MOCKS, **env}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return IbConfig()


def test_local_demo_login_without_passcode_is_allowed(monkeypatch):
    _cfg(monkeypatch, IB_DEMO_LOGIN="1").assert_deployable()


def test_deployed_demo_login_needs_a_passcode(monkeypatch):
    cfg = _cfg(monkeypatch, IB_DEPLOYED="1", IB_DEMO_LOGIN="1", IB_TOKEN_KEY=STRONG,
               IB_LEDGER_KEY=STRONG + "x")
    with pytest.raises(RuntimeError, match="IB_DEMO_PASSCODE"):
        cfg.assert_deployable()


def test_deployed_demo_login_with_passcode_starts(monkeypatch):
    _cfg(monkeypatch, IB_DEPLOYED="1", IB_DEMO_LOGIN="1", IB_DEMO_PASSCODE="a1b2c3d4e5f60718",
         IB_TOKEN_KEY=STRONG, IB_LEDGER_KEY=STRONG + "x").assert_deployable()


def test_deployed_dev_keys_refused(monkeypatch):
    with pytest.raises(RuntimeError, match="IB_TOKEN_KEY"):
        _cfg(monkeypatch, IB_DEPLOYED="1").assert_deployable()


def test_deployed_mock_dev_secrets_and_shared_key_refused(monkeypatch):
    with pytest.raises(RuntimeError, match="MOCK_WEBHOOK_SECRET"):
        _cfg(monkeypatch, mocks=False, IB_DEPLOYED="1", IB_TOKEN_KEY=STRONG,
             IB_LEDGER_KEY=STRONG + "x").assert_deployable()
    with pytest.raises(RuntimeError, match="must differ"):
        _cfg(monkeypatch, IB_DEPLOYED="1", IB_TOKEN_KEY=STRONG, IB_LEDGER_KEY=STRONG).assert_deployable()
