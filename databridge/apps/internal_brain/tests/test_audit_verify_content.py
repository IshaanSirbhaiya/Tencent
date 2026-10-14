"""/audit/verify re-hashes the answer content store against the sealed digests, so an edit to an
answer nobody has revealed is still detected (CodeBuddy security review CB5 finding 3)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import create_app
from databridge_ib.audit.content import AnswerStore
from databridge_ib.audit.routes import make_audit_router

from test_mcp_server import _svc


def _officer(tmp_path):
    svc = _svc(tmp_path)
    store = AnswerStore(svc.cfg)                      # the real store, in this test's var dir
    svc.answers = store
    svc.pipeline.answers = store
    app = create_app(svc, audit_router_factory=lambda current, require_role: make_audit_router(
        svc.ledger, store, svc.projection, require_role, clock=lambda: dt.datetime.now(dt.timezone.utc)))
    tok = authmod.mint(svc.cfg.token_key, "sara.sec", ["employee", "compliance_officer"])
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"}), svc, store


def test_untouched_store_verifies(tmp_path):
    c, _, _ = _officer(tmp_path)
    assert c.post("/ib/ask", json={"question": "database migration status"}).status_code == 200
    v = c.get("/audit/verify").json()
    assert v["content"]["checked"] >= 1 and v["content"]["intact"] is True and v["ok"] is True


def test_edited_answer_is_detected_without_a_reveal(tmp_path):
    c, svc, store = _officer(tmp_path)
    assert c.post("/ib/ask", json={"question": "database migration status"}).status_code == 200
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE answers SET answer = answer || ' (edited)'")
    v = c.get("/audit/verify").json()
    assert v["content"]["intact"] is False and v["ok"] is False
    assert "does not match the sealed digest" in v["content"]["problems"][0]
    # counts only: no answer reference ever appears in the verify report
    with sqlite3.connect(store.path) as db:
        refs = [r[0] for r in db.execute("SELECT answer_ref FROM answers")]
    assert refs and not any(r in json.dumps(v) for r in refs)


def test_missing_answers_are_counted_not_listed(tmp_path):
    c, svc, store = _officer(tmp_path)
    for _ in range(3):
        assert c.post("/ib/ask", json={"question": "database migration status"}).status_code == 200
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM answers")
    v = c.get("/audit/verify").json()
    assert v["ok"] is False and v["content"]["problem_count"] == 3
    assert v["content"]["problems"] == ["3 answer(s) missing from the content store"]
