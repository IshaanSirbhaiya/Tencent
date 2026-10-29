"""Developer-mode endpoints: /audit/stats and /audit/journey (compliance only, ledgered) and
/ib/trace (the asker's OWN requests, without the considered set or Gate-2 reasons)."""
from __future__ import annotations

import datetime as dt
import json

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import create_app
from databridge_ib.audit.content import AnswerStore
from databridge_ib.audit.routes import make_audit_router
from databridge_ib.audit.stats import STAGES, compute_stats

from test_mcp_server import _svc

Q = "database migration status"


def _app(tmp_path):
    svc = _svc(tmp_path)
    store = AnswerStore(svc.cfg)
    svc.answers = store
    svc.pipeline.answers = store
    app = create_app(svc, audit_router_factory=lambda current, require_role: make_audit_router(
        svc.ledger, store, svc.projection, require_role,
        clock=lambda: dt.datetime.now(dt.timezone.utc)))
    return app, svc


def _as(app, svc, sub, roles):
    tok = authmod.mint(svc.cfg.token_key, sub, list(roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


def test_answer_release_records_stage_timings(tmp_path):
    app, svc = _app(tmp_path)
    c = _as(app, svc, "jdoe", ("employee",))
    assert c.post("/ib/ask", json={"question": Q}).status_code == 200
    rel = [e for e in svc.ledger.events() if e.kind == "answer.released"][-1]
    assert set(rel.data["stage_ms"]) == set(STAGES)
    assert all(isinstance(v, int) and v >= 0 for v in rel.data["stage_ms"].values())


def test_stats_needs_compliance_and_is_ledgered(tmp_path):
    app, svc = _app(tmp_path)
    emp = _as(app, svc, "jdoe", ("employee",))
    assert emp.post("/ib/ask", json={"question": Q}).status_code == 200
    assert emp.get("/audit/stats").status_code == 403
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))
    r = off.get("/audit/stats", params={"days": "7"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["totals"]["requests"] >= 1
    assert s["totals"]["answered"] + s["totals"]["refused"] >= 1
    assert s["requests_flow"]["asked"] == s["totals"]["requests"]
    df = s["documents_flow"]
    assert df["gate2_allowed"] + df["gate2_denied"] == df["gate2_checked"]
    assert df["cited"] <= df["released"] <= df["gate2_allowed"] <= df["gate2_checked"]
    assert "funnel" not in s                 # requests and documents are never one funnel
    assert s["stages_ms"]["gate2"]["count"] >= 1
    # counts only: no question or answer text anywhere in the payload
    assert Q not in json.dumps(s)
    assert any(e.kind == "audit.query" and e.data.get("filter", {}).get("op") == "stats"
               for e in svc.ledger.events())


def test_stats_rejects_bad_params_and_ledgers_the_rejection(tmp_path):
    app, svc = _app(tmp_path)
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))
    assert off.get("/audit/stats", params={"days": "0"}).status_code == 422
    assert off.get("/audit/stats", params={"replay": "maybe"}).status_code == 422
    rejected = [e for e in svc.ledger.events() if e.kind == "audit.query"
                and e.data.get("filter", {}).get("op") == "stats"]
    assert len(rejected) == 2 and all(e.data["filter"]["outcome"] == "rejected" for e in rejected)


def test_journey_is_compliance_only_and_has_no_answer_text(tmp_path):
    app, svc = _app(tmp_path)
    emp = _as(app, svc, "jdoe", ("employee",))
    rid = emp.post("/ib/ask", json={"question": Q}).json()["request_id"]
    assert emp.get(f"/audit/journey/{rid}").status_code == 403
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))
    j = off.get(f"/audit/journey/{rid}")
    assert j.status_code == 200, j.text
    body = j.json()
    assert body["sub"] == "jdoe" and body["status"] in ("released", "refused")
    assert set(body["stage_ms"]) == set(STAGES)
    assert [e["kind"] for e in body["events"]][:3] == ["request.accepted", "retrieval.decided",
                                                       "answer.released"]
    assert "answer" not in body
    assert off.get("/audit/journey/nope").status_code == 404


def test_trace_is_own_requests_only_and_hides_gate2_reasons(tmp_path):
    app, svc = _app(tmp_path)
    jd = _as(app, svc, "jdoe", ("employee",))
    rid = jd.post("/ib/ask", json={"question": Q}).json()["request_id"]
    t = jd.get(f"/ib/trace/{rid}")
    assert t.status_code == 200, t.text
    body = t.json()
    assert body["question"] == Q and [s["name"] for s in body["stages"]] == list(STAGES)
    assert all(s["status"] == "ok" for s in body["stages"])
    for hidden in ("considered", "gate2", "time_window", "answer_ref", "released", "stage_ms",
                   "latency_ms", "events"):
        assert hidden not in body
    assert "reason_code" not in json.dumps(body) and '"seq"' not in json.dumps(body)
    assert [r["ordinal"] for r in body["records"]] == [1, 2, 3]
    assert all(r["hash"] for r in body["records"])
    assert body["latency_ms_rounded"] % 100 == 0 and body["synthetic_replay"] is False
    # someone else's request id is indistinguishable from an unknown one
    other = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))
    a, b = other.get(f"/ib/trace/{rid}"), other.get("/ib/trace/does-not-exist")
    assert a.status_code == b.status_code == 404 and a.content == b.content


def test_compute_stats_window_and_replay_filter():
    now = dt.datetime(2026, 10, 30, tzinfo=dt.timezone.utc)

    class E:
        def __init__(self, seq, kind, data):
            self.seq, self.kind, self.data, self.hash, self.recorded_at = seq, kind, data, f"h{seq}", None

    old = (now - dt.timedelta(days=40)).isoformat()
    recent = (now - dt.timedelta(days=1)).isoformat()
    evs = [E(1, "request.accepted", {"sub": "a", "event_time": old, "channel": "ui"}),
           E(2, "request.accepted", {"sub": "a", "event_time": recent, "channel": "workbuddy-mcp"}),
           E(3, "retrieval.decided", {"sub": "a", "event_time": recent, "considered": ["jira:X-1", "slack:C:1"],
                                      "released": ["jira:X-1"],
                                      "gate2": [{"doc_id": "jira:X-1", "verdict": "verified", "reason_code": "ok"},
                                                {"doc_id": "slack:C:1", "verdict": "forbidden", "reason_code": "n"}]}),
           E(4, "answer.released", {"sub": "a", "event_time": recent, "refused": False, "latency_ms": 40,
                                    "model": {"provider": "deterministic", "model": "extractive-v1"},
                                    "citations": [{"doc_id": "jira:X-1"}], "stage_ms": {"gate1": 3}}),
           E(5, "request.accepted", {"sub": "b", "event_time": recent, "synthetic_replay": True})]
    s = compute_stats(evs, now=now, days=30)
    assert s["totals"]["requests"] == 2 and s["channels"] == {"workbuddy-mcp": 1, "ui": 1}
    assert s["documents_flow"]["gate2_allowed"] == 1 and s["documents_flow"]["gate2_denied"] == 1
    assert s["documents_flow"]["cited"] == 1 and s["documents_flow"]["released"] == 1
    assert s["platforms"]["jira"] == {"considered": 1, "released": 1, "gate2_checked": 1}
    assert s["latency_ms"]["p50"] == 40 and s["stages_ms"]["gate1"]["p50"] == 3
    assert s["ledger"]["last_seq"] == 5
    assert compute_stats(evs, now=now, days=30, include_replay=False)["totals"]["requests"] == 1
