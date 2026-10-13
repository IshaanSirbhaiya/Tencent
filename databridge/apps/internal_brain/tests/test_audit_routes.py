"""A5 /audit/* routes on a small test app with a fake principal dependency: role gate, every call
ledgered, reveal reasoned + ledgered before the text, fail-closed on a broken ledger."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from databridge_ib.audit.anchor import FileSink, publish_head
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.audit.routes import make_audit_router

from test_audit_support import NOW, SPECS, clone, seed

ROLES = {"sara.sec": ("employee", "compliance_officer"), "jdoe": ("employee",),
         "tom.it": ("admin_it",)}


@dataclass
class Principal:
    sub: str
    roles: tuple[str, ...]


def require_role(role: str):
    def dep(x_test_sub: str | None = Header(None)) -> Principal:
        if not x_test_sub or x_test_sub not in ROLES:
            raise HTTPException(status_code=401, detail="not authenticated")
        p = Principal(x_test_sub, ROLES[x_test_sub])
        if role not in p.roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return p
    return Depends(dep)


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit-routes-src")
    seed(root)
    return root


@pytest.fixture
def env(template, tmp_path):
    cfg, _, store = clone(template, tmp_path)
    sink = FileSink(tmp_path / "ext" / "heads.jsonl")
    ledger = AccessLedger(cfg, external=sink)
    proj = AuditProjection(ledger)
    app = FastAPI()
    app.include_router(make_audit_router(ledger, store, proj, require_role, sink=sink,
                                         clock=lambda: NOW))
    yield TestClient(app), ledger, store, cfg, sink
    store.close()


CO = {"X-Test-Sub": "sara.sec"}


def _kinds(ledger):
    return [e for e in ledger.events() if e.kind in ("audit.query", "audit.reveal")]


@pytest.mark.parametrize("who", ["jdoe", "tom.it"])
def test_non_compliance_roles_forbidden(env, who):
    client, ledger, *_ = env
    before = len(list(ledger.events()))
    h = {"X-Test-Sub": who}
    assert client.get("/audit/events", headers=h).status_code == 403
    assert client.get("/audit/doc/jira:PAY-201/accessors", headers=h).status_code == 403
    assert client.post("/audit/ask", json={"question": "x"}, headers=h).status_code == 403
    assert client.post("/audit/reveal", json={"req_id": SPECS[0].req_id, "reason": "r"},
                       headers=h).status_code == 403
    assert client.get("/audit/verify", headers=h).status_code == 403
    assert client.get("/audit/events").status_code == 401
    assert len(list(ledger.events())) == before                 # nothing ran, nothing ledgered


def test_events_query_is_ledgered(env):
    client, ledger, *_ = env
    r = client.get("/audit/events", params={"user": "jdoe", "container": "payment-gateway",
                                            "since": "2026-09-13T09:00:00+08:00"}, headers=CO)
    assert r.status_code == 200
    rows = r.json()["events"]
    assert rows and all(x["sub"] == "jdoe" for x in rows)
    q = _kinds(ledger)[-1]
    assert q.kind == "audit.query" and q.data["sub"] == "sara.sec"
    assert q.data["filter"]["user"] == "jdoe" and q.data["result_count"] == len(rows)


def test_events_rejects_open_filters(env):
    client, *_ = env
    assert client.get("/audit/events", params={"decision": "maybe"}, headers=CO).status_code == 422
    assert client.get("/audit/events", params={"limit": 501}, headers=CO).status_code == 422
    assert client.get("/audit/events", params={"since": "2024-01-01T00:00:00+00:00"},
                      headers=CO).status_code == 422


def test_ask_and_accessors(env):
    client, ledger, *_ = env
    r = client.post("/audit/ask", headers=CO, json={
        "question": "Show me everything user `jdoe` accessed related to the `payment-gateway` "
                    "Confluence space in the last 30 days"})
    body = r.json()
    assert r.status_code == 200 and body["parser"] == "deterministic"
    assert body["filter"]["user"] == "jdoe" and body["filter"]["container"] == "payment-gateway"
    assert body["events"]
    assert _kinds(ledger)[-1].data["filter"]["op"] == "ask"
    r = client.get("/audit/doc/jira:PAY-201/accessors", headers=CO)
    assert r.status_code == 200 and r.json()["accessors"]
    assert _kinds(ledger)[-1].data["filter"] == {"op": "accessors", "doc": "jira:PAY-201"}


def test_reveal_requires_reason_and_is_ledgered(env):
    client, ledger, *_ = env
    sp = next(s for s in SPECS if not s.failed)
    for reason in ("", "   "):
        r = client.post("/audit/reveal", json={"req_id": sp.req_id, "reason": reason}, headers=CO)
        assert r.status_code == 422
    assert not [e for e in ledger.events() if e.kind == "audit.reveal"]
    r = client.post("/audit/reveal", headers=CO,
                    json={"req_id": sp.req_id, "reason": "complaint #42 investigation"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == sp.answer and body["content_matches_ledger"] is True
    rev = [e for e in ledger.events() if e.kind == "audit.reveal"]
    assert len(rev) == 1 and rev[0].data["target_req_id"] == sp.req_id
    assert rev[0].data["sub"] == "sara.sec" and rev[0].data["reason"] == "complaint #42 investigation"
    assert sp.answer not in ledger.path.read_text(encoding="utf-8")
    # the reveal shows up in the reconstruction
    proj = AuditProjection(ledger, attach=False)
    proj.rebuild()
    assert proj.reconstruct(sp.req_id)["reveals"][0]["by"] == "sara.sec"


def test_reveal_unknown_or_failed_request(env):
    client, ledger, *_ = env
    failed = next(s for s in SPECS if s.failed)
    for rid in ("no-such-request", failed.req_id):
        r = client.post("/audit/reveal", json={"req_id": rid, "reason": "check"}, headers=CO)
        assert r.status_code == 404


def test_verify_route(env):
    client, ledger, store, cfg, sink = env
    publish_head(ledger, sink)
    r = client.get("/audit/verify", headers=CO)
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["external_head_match"] is True
    assert body["ledgered"] is True
    assert set(body) >= {"ok", "records", "head", "external_head_match", "problems"}


def test_broken_ledger_fails_closed(env):
    client, ledger, store, cfg, sink = env
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    cfg.ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert client.get("/audit/events", headers=CO).status_code == 503
    assert client.post("/audit/ask", json={"question": "who accessed PAY-201"},
                       headers=CO).status_code == 503
    sp = next(s for s in SPECS if not s.failed)
    r = client.post("/audit/reveal", json={"req_id": sp.req_id, "reason": "x"}, headers=CO)
    assert r.status_code == 503 and sp.answer not in r.text
    v = client.get("/audit/verify", headers=CO).json()
    assert v["ok"] is False and v["ledgered"] is False and v["problems"]
