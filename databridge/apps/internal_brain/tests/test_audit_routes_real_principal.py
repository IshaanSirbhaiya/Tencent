"""The audit routes must accept the app's REAL principal (a ``DirectoryUser``, whose subject is
``id``), not only the dict/``sub`` shapes the audit unit tests use. Regression: every /audit/* call
through ``create_app`` answered 401 "not authenticated" for a valid compliance-officer token."""
from __future__ import annotations

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod

from test_mcp_server import _build_app


def _client(tmp_path, sub, roles):
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, sub, list(roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"}), svc


def test_compliance_officer_bearer_token_reaches_audit_routes(tmp_path):
    c, svc = _client(tmp_path, "sara.sec", ("employee", "compliance_officer"))
    assert c.post("/ib/ask", json={"question": "database migration status"}).status_code == 200
    v = c.get("/audit/verify")
    assert v.status_code == 200, v.text
    ev = c.get("/audit/events", params={"user": "sara.sec"})
    assert ev.status_code == 200, ev.text
    # the audit query itself is ledgered under the officer's own subject
    assert any(e.kind == "audit.query" and e.data.get("sub") == "sara.sec"
               for e in svc.ledger.events())


def test_employee_is_forbidden_not_unauthenticated(tmp_path):
    c, _ = _client(tmp_path, "jdoe", ("employee",))
    assert c.get("/audit/verify").status_code == 403
