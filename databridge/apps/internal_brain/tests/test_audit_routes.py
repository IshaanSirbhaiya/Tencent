"""A5 /audit/* routes on a small test app with a fake principal dependency: role gate, every call
ledgered, reveal reasoned + ledgered before the text, fail-closed on a broken ledger."""
from __future__ import annotations

import datetime as dt
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
    assert _kinds(ledger)[-1].data["filter"] == {"op": "accessors", "doc": "jira:PAY-201",
                                                 "truncated": False, "outcome": "ok"}
    assert r.json()["truncated"] is False


def test_reveal_requires_reason_and_is_ledgered(env):
    client, ledger, *_ = env
    sp = next(s for s in SPECS if not s.failed)
    for reason in ("", "   "):
        r = client.post("/audit/reveal", json={"req_id": sp.req_id, "reason": reason}, headers=CO)
        assert r.status_code == 422
    assert not [e for e in ledger.events() if e.kind == "audit.reveal"]
    rejected = [e for e in _kinds(ledger) if e.data["filter"].get("op") == "reveal"]
    assert [e.data["filter"]["outcome"] for e in rejected] == ["rejected", "rejected"]
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


# --- Codex security review r1, track 2 ---------------------------------------------------------
def _last(ledger):
    return _kinds(ledger)[-1].data["filter"]


def _tamper_answer(cfg, req_id, text):
    import sqlite3
    db = sqlite3.connect(str(cfg.content_db))
    db.execute("UPDATE answers SET answer = ? WHERE req_id = ?", (text, req_id))
    db.commit()
    db.close()


def test_reveal_refuses_content_that_does_not_match_the_ledger(env):
    # #7: no text is released on a digest mismatch; 409; the attempt is ledgered as "mismatch"
    client, ledger, store, cfg, sink = env
    sp = next(s for s in SPECS if not s.failed)
    forged = "FORGED-TEXT planted in the content store"
    _tamper_answer(cfg, sp.req_id, forged)
    r = client.post("/audit/reveal", headers=CO, json={"req_id": sp.req_id, "reason": "case 7"})
    assert r.status_code == 409
    assert r.json() == {"error": "content does not match the sealed ledger digest"}
    assert forged not in r.text and sp.answer not in r.text
    kinds = _kinds(ledger)
    assert kinds[-2].kind == "audit.reveal"                          # write-ahead, as before
    assert kinds[-1].data["filter"]["outcome"] == "mismatch"
    assert kinds[-1].data["filter"]["target_req_id"] == sp.req_id


def test_failed_audit_calls_are_ledgered(env):
    # #9: invalid filters (incl. ones the framework used to reject), unknown targets, bad doc
    # ids and parser failures each leave an audit.query with outcome "rejected"
    client, ledger, *_ = env
    cases = [
        lambda: client.get("/audit/events", params={"decision": "maybe"}, headers=CO),
        lambda: client.get("/audit/events", params={"since": "not-a-date"}, headers=CO),
        lambda: client.get("/audit/events", params={"limit": "lots"}, headers=CO),
        lambda: client.get("/audit/doc/" + "x" * 301 + "/accessors", headers=CO),
        lambda: client.post("/audit/ask", json={"question": "  "}, headers=CO),
        lambda: client.post("/audit/ask", json={"question": "what did jdoe ask on 2026-02-30"},
                            headers=CO),
        lambda: client.post("/audit/reveal", json={"req_id": "no-such-request", "reason": "x"},
                            headers=CO),
    ]
    for call in cases:
        before = len(_kinds(ledger))
        r = call()
        assert r.status_code in (404, 422), r.text
        assert len(_kinds(ledger)) == before + 1
        assert _last(ledger)["outcome"] == "rejected" and _last(ledger)["error"]
        assert _kinds(ledger)[-1].data["sub"] == "sara.sec"


def test_rejection_on_a_broken_ledger_is_503(env):
    client, ledger, store, cfg, sink = env
    lines = cfg.ledger_path.read_text(encoding="utf-8").splitlines()
    cfg.ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert client.get("/audit/events", params={"decision": "maybe"}, headers=CO).status_code == 503
    assert client.post("/audit/reveal", json={"req_id": "x", "reason": " "},
                       headers=CO).status_code == 503


def test_events_window_is_capped_and_stated(env):
    # #13: no dates -> last 365 days; until-only -> 365 days before it; the window is returned
    client, ledger, *_ = env
    r = client.get("/audit/events", params={"user": "jdoe"}, headers=CO)
    body = r.json()
    assert r.status_code == 200 and body["window"]["until_exclusive"] is True
    since = dt.datetime.fromisoformat(body["window"]["since"])
    until = dt.datetime.fromisoformat(body["window"]["until"])
    assert until == NOW and until - since == dt.timedelta(days=365)
    assert _last(ledger)["since"] == body["window"]["since"]
    r = client.get("/audit/events", params={"until": "2024-01-01T00:00:00+00:00"}, headers=CO)
    w = r.json()["window"]
    assert r.status_code == 200
    assert dt.datetime.fromisoformat(w["until"]) - dt.datetime.fromisoformat(w["since"]) \
        == dt.timedelta(days=365)
    r = client.get("/audit/events", params={"since": "2027-01-01T00:00:00+00:00"}, headers=CO)
    assert r.status_code == 422                                      # since after now


def test_ask_invalid_constraint_is_422_with_the_constraint(env):
    # #16 / #17 through the route: no broadened search, no 500
    client, ledger, *_ = env
    r = client.post("/audit/ask", json={"question": "who accessed the R&D plan"}, headers=CO)
    assert r.status_code == 422
    assert r.json()["detail"]["unparsed"] == {"doc": "R&D plan"}
    assert "events" not in r.json()
    r = client.post("/audit/ask", json={"question": "failed requests in the last 9999 years"},
                    headers=CO)
    assert r.status_code == 200 and r.json()["window"]["until_exclusive"] is True


def test_verify_route_fails_closed_on_sink_error(template, tmp_path):
    cfg, _, store = clone(template, tmp_path)

    class Raising:
        name = "raising"

        def latest(self, log_id):
            raise OSError("down")

        def publish(self, head):
            raise OSError("down")
    ledger = AccessLedger(cfg)
    app = FastAPI()
    app.include_router(make_audit_router(ledger, store, AuditProjection(ledger), require_role,
                                         sink=Raising(), clock=lambda: NOW))
    body = TestClient(app).get("/audit/verify", headers=CO).json()
    assert body["ok"] is False and body["external_head_match"] is False and body["ledgered"]
    store.close()


# --- QA_LIVE §4.2 / §4.5: titles resolve offline; the echoed filter carries no paging cap ----------
def test_ask_resolves_a_document_title_known_to_the_projection(env, monkeypatch):
    import test_audit_support as sup
    from test_audit_support import Spec, write_request
    from e2e_world import load_world
    w = load_world()
    breach = {f["doc"] for f in w["facts"] if f["id"].startswith("S3-breach-")}
    titles = {**{f"confluence:{p['id']}": p["title"] for p in w["confluence"]["pages"]},
              **{f"drive:{f['id']}": f["name"] for f in w["drive"]["files"]}}
    client, ledger, store, *_ = env
    for d in sorted(breach):
        platform, rest = d.split(":", 1)
        monkeypatch.setitem(sup.DOCS, d, (platform, "sec", "Security", titles[d]))
    sp = Spec("r-breach-1", "priya.sre", 2, tuple(sorted(breach)))
    write_request(ledger, store, sp)

    r = client.post("/audit/ask", json={"question": "Who retrieved the Q3 breach report?"},
                    headers=CO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parser"] == "deterministic" and body["filter"]["doc"] in breach
    assert {e["req_id"] for e in body["events"]} == {sp.req_id}
    assert any("matched" in n for n in body["notes"])
    assert "limit" not in body["filter"] and body["limit"] == 100       # no raw "limit" chip

    r = client.post("/audit/ask", json={"question": "Who retrieved the zebra quantum memo?"},
                    headers=CO)
    assert r.status_code == 200 and r.json()["events"] == []            # nothing broader
    assert any("no document title" in n for n in r.json()["notes"])


def test_events_window_note_is_plain_english(env):
    client, *_ = env
    applied = client.get("/audit/events", headers=CO).json()["window"]["applied"]
    assert applied == "no dates given, so the last 365 days"


def test_projection_failure_is_a_ledgered_500_on_every_query_route(template, tmp_path):
    """Opus final review #9: /audit/events, /audit/doc/{id}/accessors and /audit/ask catch up the
    projection through ``_sync_or_refuse``: a projection (not ledger) failure is a ledgered 500
    with the fixed envelope, never an unledgered framework 500."""
    cfg, _, store = clone(template, tmp_path)
    ledger = AccessLedger(cfg)
    proj = AuditProjection(ledger)

    def broken():
        raise RuntimeError("sqlite: database disk image is malformed")
    proj.sync = broken
    app = FastAPI()
    app.include_router(make_audit_router(ledger, store, proj, require_role, clock=lambda: NOW))
    client = TestClient(app, raise_server_exceptions=False)
    calls = [("events", lambda: client.get("/audit/events", params={"user": "jdoe"}, headers=CO)),
             ("accessors", lambda: client.get("/audit/doc/jira:PAY-201/accessors", headers=CO)),
             ("ask", lambda: client.post("/audit/ask", json={"question": "what did jdoe ask"},
                                         headers=CO))]
    for op, call in calls:
        r = call()
        assert r.status_code == 500, (op, r.text)
        assert "sqlite" not in r.text and "malformed" not in r.text
        last = _last(ledger)
        assert last["op"] == op and last["outcome"] == "error", last
    store.close()
