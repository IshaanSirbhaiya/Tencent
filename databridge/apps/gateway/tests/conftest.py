"""Shared gateway-test setup — the auth token directory every gated-route test authenticates with.

R-015 put `auth.require_principal` in front of every disclosing route. Tests that build their own
bare `FastAPI()` + `TestClient(...)` (most of this package) now need a credential to reach one, the
same way a real caller would. This file configures a fixed, known token directory for the whole
test session.

`WALNUT_API_TOKENS` is set in `pytest_configure`, not a fixture — the same device the repo-root
`conftest.py` uses for `DATABRIDGE_FORCE_GOLD`, and for the same reason: some tests here use
MODULE-scoped fixtures (`test_intelligence_ask.py`'s `answered`, among others) that make their
first HTTP call during the first test's setup, before any FUNCTION-scoped autouse fixture (which
`monkeypatch`-based tokens would have to be) has run. Setting the env var at collection time, once,
before pytest resolves any fixture, avoids that ordering hazard entirely.
"""
from __future__ import annotations

from pathlib import Path

import json
import os

#: token -> principal_id. Fixed and known to every test in this package. The identities match the
#: two directories `orchestrator_fleet.PRINCIPALS` and `verify_fleet`'s own directory declare
#: (`analyst-7`, `fcc-3`, `audit-1`), plus the generic `"staff"` identity the graph/trace/
#: intelligence/evidence-trace surfaces use — none of those call `fleet.principal_for`, so they
#: need nothing more specific than "a caller who holds *a* credential".
TEST_TOKENS: dict[str, str] = {
    "test-token-analyst-7": "analyst-7",
    "test-token-fcc-3": "fcc-3",
    "test-token-audit-1": "audit-1",
    "test-token-staff": "staff",
}


def auth_headers(principal_id: str = "staff") -> dict[str, str]:
    """`{"Authorization": "Bearer <token>"}` for a known test principal.

    Raises `KeyError` (loudly, at test-collection or test-run time) for a principal_id no test
    token maps to, rather than silently authenticating as the wrong identity.
    """
    token = next(t for t, p in TEST_TOKENS.items() if p == principal_id)
    return {"Authorization": f"Bearer {token}"}


#: The default identity for tests that do not care WHO is asking, only THAT someone authenticated.
AUTH_HEADERS = auth_headers("staff")


def pytest_configure(config) -> None:                                # noqa: ARG001
    os.environ["WALNUT_API_TOKENS"] = json.dumps(TEST_TOKENS)
    # Explicitly OFF for the whole suite, same reasoning as `WALNUT_DEV_AUTH` everywhere else in
    # this repo: a test that wants to exercise dev mode or proxy mode turns it on itself, in its
    # own function, and must call `auth.reset_auth_state_for_tests()` after changing the
    # environment — `_AuthState` is read once per process and does not notice an env change on
    # its own.
    os.environ.pop("WALNUT_DEV_AUTH", None)
    os.environ.pop("WALNUT_AUTH_MODE", None)
    os.environ.pop("WALNUT_PROXY_IDENTITY_HEADER", None)
    os.environ.pop("WALNUT_PROXY_IDENTITY_MAP", None)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _operator_registration_mode(monkeypatch):
    """R-022 round 2 (Codex f2 #10): mcp_ingest registration is fail-closed unless the DEPLOYMENT
    declares operator mode, as the local launchers do. The gateway's own remote-caller policy is
    what these tests exercise, so the process is in operator mode here."""
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")


@pytest.fixture(autouse=True)
def _local_approval_key(monkeypatch):
    """INT5 (R-022 build cross-track 4): approvals are SEALED with the deployment approval key
    (`mcp_ingest.screen`). Tests use a LOCAL key file under `databridge/var/keys/` (gitignored,
    never committed), generated here if missing — never the developer's `~/.walnut` key. A test
    about key custody overrides `WALNUT_APPROVAL_KEY_FILE` itself."""
    import secrets as _secrets

    key = Path(__file__).resolve().parents[3] / "var" / "keys" / "approval.test.key"
    if not key.is_file():
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_name(f".{key.name}.{_secrets.token_hex(6)}.tmp")
        tmp.write_text(_secrets.token_hex(32), encoding="utf-8")
        try:
            tmp.replace(key) if not key.exists() else tmp.unlink()
        except OSError:
            tmp.unlink(missing_ok=True)
    monkeypatch.delenv("WALNUT_APPROVAL_KEY", raising=False)
    monkeypatch.setenv("WALNUT_APPROVAL_KEY_FILE", str(key))
