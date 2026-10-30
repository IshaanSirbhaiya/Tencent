"""/audit/stats and /audit/journey hardening (Codex plan review #5, #6, #8; Opus #11, #12): the
half-open stats window, ledgered handler failures, the head-keyed stats cache, and the split
request/document flows."""
from __future__ import annotations

import datetime as dt

from databridge_ib.audit import routes as audit_routes
from databridge_ib.audit.stats import compute_stats

from test_dev_views import Q, _app, _as

NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)


class E:
    def __init__(self, seq, kind, data):
        self.seq, self.kind, self.data, self.hash, self.recorded_at = seq, kind, data, f"h{seq}", None


def _req(seq, when):
    return E(seq, "request.accepted", {"sub": "a", "event_time": when.isoformat(), "channel": "ui"})


def test_stats_window_is_half_open_since_inclusive_now_exclusive():
    since = NOW - dt.timedelta(days=30)
    evs = [_req(1, since - dt.timedelta(microseconds=1)),      # just before: out
           _req(2, since),                                      # exactly since: in
           _req(3, NOW - dt.timedelta(microseconds=1)),         # just before now: in
           _req(4, NOW),                                        # exactly now: out
           _req(5, NOW + dt.timedelta(days=2))]                 # future: out
    s = compute_stats(evs, now=NOW, days=30)
    assert s["totals"]["requests"] == 2
    assert s["window"] == {"since": since.isoformat(), "until": NOW.isoformat(), "days": 30}


def test_cited_never_exceeds_released_and_flows_are_separate():
    t = (NOW - dt.timedelta(days=1)).isoformat()
    evs = [E(1, "request.accepted", {"sub": "a", "req_id": "r1", "event_time": t}),
           E(2, "retrieval.decided", {"sub": "a", "req_id": "r1", "event_time": t,
                                      "considered": ["jira:X-1", "jira:X-2"],
                                      "released": ["jira:X-1"],
                                      "gate2": [{"doc_id": "jira:X-1", "verdict": "verified"},
                                                {"doc_id": "jira:X-2", "verdict": "verified"}]}),
           # two citations (two chunks) of the same released doc count once
           E(3, "answer.released", {"sub": "a", "req_id": "r1", "event_time": t, "refused": False,
                                    "citations": [{"doc_id": "jira:X-1"}, {"doc_id": "jira:X-1"}],
                                    "model": {}, "latency_ms": 5})]
    s = compute_stats(evs, now=NOW, days=30)
    assert s["requests_flow"] == {"asked": 1, "answered": 1, "refused": 0, "failed": 0}
    assert s["documents_flow"]["released"] == 1 and s["documents_flow"]["cited"] == 1
    assert s["totals"]["citations"] == 2


def test_stats_computation_failure_is_ledgered_500(tmp_path, monkeypatch):
    app, svc = _app(tmp_path)
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))

    def boom(*a, **k):
        raise ValueError("malformed field")
    monkeypatch.setattr(audit_routes, "compute_stats", boom)
    r = off.get("/audit/stats")
    assert r.status_code == 500 and "malformed" not in r.text
    ev = [e for e in svc.ledger.events() if e.kind == "audit.query"][-1]
    assert ev.data["filter"]["op"] == "stats" and ev.data["filter"]["outcome"] == "error"


def test_stats_on_a_ledger_that_does_not_verify_is_503_and_never_cached(tmp_path, monkeypatch):
    from databridge_ib.audit.ledger import LedgerUnavailable
    app, svc = _app(tmp_path)
    _as(app, svc, "jdoe", ("employee",)).post("/ib/ask", json={"question": Q})
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))
    assert off.get("/audit/stats").status_code == 200          # a result is now cached

    def unverifiable(*a, **k):
        raise LedgerUnavailable("LEDGER_CHAIN_BROKEN", "the chain does not verify")
    monkeypatch.setattr(svc.ledger, "events", unverifiable)
    r = off.get("/audit/stats")
    assert r.status_code == 503 and "totals" not in r.text


def test_journey_projection_failure_is_ledgered_500(tmp_path, monkeypatch):
    app, svc = _app(tmp_path)
    rid = _as(app, svc, "jdoe", ("employee",)).post("/ib/ask", json={"question": Q}).json()[
        "request_id"]
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))

    def broken_sync(*a, **k):
        raise RuntimeError("sqlite is locked")
    monkeypatch.setattr(svc.projection, "sync", broken_sync)
    r = off.get(f"/audit/journey/{rid}")
    assert r.status_code == 500 and "sqlite" not in r.text
    ev = [e for e in svc.ledger.events() if e.kind == "audit.query"][-1]
    assert ev.data["filter"]["op"] == "journey" and ev.data["filter"]["outcome"] == "error"


def test_stats_cache_is_keyed_on_the_verified_head(tmp_path, monkeypatch):
    app, svc = _app(tmp_path)
    _as(app, svc, "jdoe", ("employee",)).post("/ib/ask", json={"question": Q})
    off = _as(app, svc, "sara.sec", ("employee", "compliance_officer"))
    calls = []
    real = audit_routes.compute_stats

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(audit_routes, "compute_stats", counting)
    first = off.get("/audit/stats").json()
    second = off.get("/audit/stats").json()
    # the first call appended its own audit.query, so the head moved: recomputed, and it shows
    assert len(calls) == 2 and second["totals"]["audit_queries"] == first["totals"]["audit_queries"] + 1


def test_graph_views_exclude_poll_write_ahead_rows_and_the_upload_folder_picker():
    """Round 5 (Opus cross-check): the live poll's ``checking`` write-ahead rows and the upload
    folder picker's rows (D-34) are ``graph.viewed`` for the audit trail, but not graph views."""
    t = (NOW - dt.timedelta(days=1)).isoformat()

    def gv(seq, mode, outcome="ok"):
        return E(seq, "graph.viewed", {"sub": "a", "event_time": t, "mode": mode,
                                       "outcome": outcome})
    evs = [gv(1, "overview"), gv(2, "focus"), gv(3, "changes"),            # counted
           gv(4, "changes", "checking"), gv(5, "upload_folders", "checking"),
           gv(6, "upload_folders")]                                          # not counted
    assert compute_stats(evs, now=NOW, days=30)["totals"]["graph_views"] == 3
