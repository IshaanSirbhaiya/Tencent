"""The ``workbuddy-mcp`` channel label is honoured only on bearer-token calls, never on the browser's
cookie session (CodeBuddy security review CB5 finding 5)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod

from test_mcp_server import _build_app

Q = {"question": "database migration status"}
MCP = {"x-ib-channel": "workbuddy-mcp"}


def _channels(svc):
    return [e.data.get("channel") for e in svc.ledger.events() if e.kind == "request.accepted"]


def test_cookie_session_cannot_claim_the_mcp_channel(tmp_path):
    app, svc = _build_app(tmp_path)
    c = TestClient(app)
    assert c.post("/auth/demo-login", json={"persona": "jdoe"}).status_code == 200
    assert c.post("/ib/ask", json=Q, headers=MCP).status_code == 200
    assert _channels(svc) == ["ui"]


def test_bearer_call_keeps_the_mcp_channel(tmp_path):
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    c = TestClient(app, headers={"Authorization": f"Bearer {tok}", **MCP})
    assert c.post("/ib/ask", json=Q).status_code == 200
    assert _channels(svc) == ["workbuddy-mcp"]


def test_sign_in_is_recorded_before_the_session_is_issued(tmp_path):
    app, svc = _build_app(tmp_path)
    c = TestClient(app)
    assert c.post("/auth/demo-login", json={"persona": "jdoe"}).status_code == 200
    logins = [e for e in svc.ledger.events() if e.kind == "auth.login"]
    assert len(logins) == 1 and logins[0].data.get("sub") == "jdoe"
