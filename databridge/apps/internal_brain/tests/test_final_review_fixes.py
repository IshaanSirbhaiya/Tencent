"""Regression tests for the finals correctness review (docs/finals/reviews/final_codex.md), each
starting from Codex's repro:

* #1 graph edges come from the authorised CURRENT version, never from the index's older links;
* #2 /ib/ask/stream holds gate1/gate2 until the retrieval decision is sealed (no real-time timing
  of the live permission checks);
* #3 the doc-view link cap and the graph work cap are applied after the live check (D1..D12 + B);
* #4 one deadline bounds checks AND read-through; late work is denied and reported undecided;
* plan item 4: /ib/map is version-bound (authz.authorize), not ``.allowed`` + index metadata;
* plan item 5: a trace reconstruction failure is ledgered (best effort) before the 503.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from databridge_ib import app as appmod
from databridge_ib import authz, graph
from databridge_ib.models import AccessCheck
from databridge_ib.pipeline import STAGES

import test_graph_doc as G
from test_ask_stream import PERMITTED, _app as stream_app, _as, parse_sse
from test_graph_doc import A, C, NOW, _c, _d, _events, _strip, _world
from test_pipeline_core import FakeConnector


# ------------------------------------------------------------------------------------------------
# #1 stale edges
# ------------------------------------------------------------------------------------------------
def test_graph_edge_removed_in_the_current_version_is_not_shown(tmp_path):
    """Codex repro: A v1 (indexed) mentions PAY-1; at the source A is now v2 without that link and
    the change was not ingested. Both A and PAY-1 pass live checks; the edge must not appear."""
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    before = jd.get("/ib/graph", params={"focus": "slack:t1"}).json()
    assert any(e["source"] == "slack:t1" and e["target"] == "jira:PAY-1" for e in before["edges"])
    svc.connectors["slack"].docs["slack:t1"] = A.model_copy(update={"version": "2", "links": ()})
    for params in ({"focus": "slack:t1"}, {"focus": "jira:PAY-1"}, {}):
        body = jd.get("/ib/graph", params=params).json()
        assert not any({e["source"], e["target"]} == {"slack:t1", "jira:PAY-1"}
                       and e["kind"] in ("mentions", "links") for e in body["edges"]), params
    node = {n["id"]: n for n in jd.get("/ib/graph", params={"focus": "slack:t1"}).json()["nodes"]}
    assert node["slack:t1"]["version"] == "2"


def test_graph_edge_added_in_the_current_version_is_checked_and_shown(tmp_path):
    """The reverse: v2 links to a document the indexed v1 never mentioned. The new target is
    checked live in this call and the edge comes from the authorised snapshot."""
    app, svc = _world(tmp_path)
    svc.connectors["slack"].docs["slack:t1"] = A.model_copy(
        update={"version": "2", "links": ("jira:PAY-1", "jira:PAY-3")})
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "slack:t1"}).json()
    rel = {(e["source"], e["target"]): e["kind"] for e in body["edges"]}
    assert rel[("slack:t1", "jira:PAY-3")] == "mentions"
    focus = {n["id"]: n for n in body["nodes"]}["slack:t1"]
    assert focus["degree_hidden_safe"] is not None             # every current target was decided


def test_graph_counts_are_null_when_a_current_target_was_not_decided(tmp_path, monkeypatch):
    app, svc = _world(tmp_path)
    svc.connectors["slack"].docs["slack:t1"] = A.model_copy(
        update={"version": "2", "links": ("jira:PAY-1", "jira:PAY-3")})
    monkeypatch.setattr(graph, "MAX_CHECKS_FOCUS", 1)          # only the focus fits the budget
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "slack:t1"}).json()
    focus = {n["id"]: n for n in body["nodes"]}["slack:t1"]
    assert body["complete"] is False
    assert focus["degree_hidden_safe"] is None and focus["more"] is None


# ------------------------------------------------------------------------------------------------
# #3 caps after live authorisation (D1..D12 + B)
# ------------------------------------------------------------------------------------------------
def _dlinks_world(tmp_path, monkeypatch, n_denied, *, with_denied=True):
    """Doc A links, in order, to D1..Dn and then B. D* are readable in the index but revoked at the
    source (stale); B is readable everywhere. ``with_denied=False`` is the paired world without D*."""
    ds = [_d(f"jira:D-{i}", f"denied {i}", f"Denied doc {i}.", container="DEN", kind="issue",
             days=5) for i in range(1, n_denied + 1)]
    b = _d("jira:B-1", "Allowed neighbour", "The allowed neighbour.", container="PAY",
           kind="issue", days=6)
    links = tuple(d.doc_id for d in ds) + (b.doc_id,) if with_denied else (b.doc_id,)
    a = _d("slack:a", "Hub thread", "Links to many issues.", container="hub", links=links)
    for d in ds + [a, b]:
        monkeypatch.setitem(G.READERS, d.doc_id, {"jdoe", "priya"})
    docs = [a, b] + (ds if with_denied else [])
    app, svc = _world(tmp_path, docs)
    for d in ds if with_denied else ():
        svc.connectors["jira"].readers[d.doc_id].discard("jdoe")   # revoked at source, index stale
    return app, svc


def test_doc_links_skip_live_denied_targets_d1_to_d12_plus_b(tmp_path, monkeypatch):
    a, sa = _dlinks_world(tmp_path / "a", monkeypatch, 12)
    b, sb = _dlinks_world(tmp_path / "b", monkeypatch, 12, with_denied=False)
    ra = _c(a, sa, "jdoe").get("/ib/doc/slack:a").json()
    rb = _c(b, sb, "jdoe").get("/ib/doc/slack:a").json()
    assert [x["doc_id"] for x in ra["links"]] == ["jira:B-1"] and ra["links_complete"] is True
    assert ra["links"] == rb["links"]
    assert "D-1" not in json.dumps(ra["links"])


def test_doc_links_are_omitted_when_the_budget_cannot_settle_them(tmp_path, monkeypatch):
    from databridge_ib import docview
    monkeypatch.setattr(docview, "MAX_LINK_CHECKS", 6)
    app, svc = _dlinks_world(tmp_path, monkeypatch, 12)
    body = _c(app, svc, "jdoe").get("/ib/doc/slack:a").json()
    assert body["links"] == [] and body["links_complete"] is False


def test_graph_focus_checks_past_denied_neighbours_within_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(graph, "CHECK_BATCH", 5)
    a, sa = _dlinks_world(tmp_path / "a", monkeypatch, 12)
    b, sb = _dlinks_world(tmp_path / "b", monkeypatch, 12, with_denied=False)
    ra = _c(a, sa, "jdoe").get("/ib/graph", params={"focus": "slack:a"}).json()
    rb = _c(b, sb, "jdoe").get("/ib/graph", params={"focus": "slack:a"}).json()
    assert "jira:B-1" in G._ids(ra) and ra["complete"] is True
    assert _strip(ra) == _strip(rb)


def test_graph_focus_past_the_work_cap_omits_counts(tmp_path, monkeypatch):
    """More stale-denied neighbours than the work cap ahead of the allowed one: the response says it
    is incomplete and gives no count that looks exact."""
    monkeypatch.setattr(graph, "CHECK_BATCH", 5)
    monkeypatch.setattr(graph, "MAX_CHECKS_FOCUS", 10)
    app, svc = _dlinks_world(tmp_path, monkeypatch, 12)
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "slack:a"}).json()
    assert body["complete"] is False
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["slack:a"]["degree_hidden_safe"] is None
    assert "D-" not in json.dumps(body)


# ------------------------------------------------------------------------------------------------
# #4 one deadline through checks and read-through
# ------------------------------------------------------------------------------------------------
class _SlowFetch(FakeConnector):
    def __init__(self, *a, fetch_s=0.0, check_s=0.0, **k):
        super().__init__(*a, **k)
        self.fetch_s, self.check_s = fetch_s, check_s

    def fetch(self, doc_id):
        time.sleep(self.fetch_s)
        return super().fetch(doc_id)

    def check_access(self, user, doc_id, *, known_version=None):
        time.sleep(self.check_s)
        return super().check_access(user, doc_id, known_version=known_version)


def _drift_index(tmp_path, n, **slow):
    from databridge_ib.index import Index
    docs = [_d(f"slack:x{i}", f"t{i}", f"text {i}", container="c") for i in range(n)]
    conn = _SlowFetch("slack", docs, {d.doc_id: {"jdoe"} for d in docs}, **slow)
    idx = Index(tmp_path / "i.sqlite")
    for d in docs:
        idx.upsert(d, conn.acl(d.doc_id))
    return docs, conn, idx


def test_read_through_is_bounded_by_the_same_deadline(tmp_path):
    """Codex repro, scaled: drifted candidates whose fetch is slow. The call returns within the one
    budget, returns no unchecked content, and reports the late ids as undecided."""
    docs, conn, idx = _drift_index(tmp_path, 8, fetch_s=1.0)
    for d in docs[:6]:                                          # 6 drifted, 2 current
        conn.docs[d.doc_id] = d.model_copy(update={"version": "2", "title": "NEW " + d.title})
    user = G._users().get("jdoe")
    und: set = set()
    t0 = time.monotonic()
    ok, n = authz.authorize(user, [d.doc_id for d in docs], index=idx, connectors={"slack": conn},
                            timeout_s=0.4, undecided=und)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.9, elapsed
    assert set(ok) == {d.doc_id for d in docs[6:]} and n == 8
    assert und == {d.doc_id for d in docs[:6]}
    assert not any(s.title.startswith("NEW") for s in ok.values())


def test_checks_past_the_deadline_are_denied_and_undecided(tmp_path):
    docs, conn, idx = _drift_index(tmp_path, 3, check_s=0.6)
    user = G._users().get("jdoe")
    und: set = set()
    t0 = time.monotonic()
    ok, _ = authz.authorize(user, [d.doc_id for d in docs], index=idx, connectors={"slack": conn},
                            timeout_s=0.2, undecided=und)
    assert time.monotonic() - t0 < 0.5 and ok == {} and und == {d.doc_id for d in docs}
    und2: set = set()
    ok2, n2 = authz.authorize(user, [docs[0].doc_id], index=idx, connectors={"slack": conn},
                              deadline=time.monotonic() - 1, undecided=und2)
    assert ok2 == {} and und2 == {docs[0].doc_id} and n2 == 1


# ------------------------------------------------------------------------------------------------
# plan item 4: the knowledge map is version-bound
# ------------------------------------------------------------------------------------------------
def test_map_lists_the_checked_version_and_honours_a_drifted_acl(tmp_path):
    app, svc = _world(tmp_path)
    svc.connectors["jira"].docs["jira:PAY-1"] = C.model_copy(
        update={"version": "2", "title": "Database migration epic v2"})
    body = _c(app, svc, "jdoe").get("/ib/map").json()
    items = {i["doc_id"]: i for p in body["platforms"] for c in p["containers"] for i in c["items"]}
    assert items["jira:PAY-1"]["version"] == "2"
    assert items["jira:PAY-1"]["title"] == "Database migration epic v2"
    # the new version is restricted at the source: drift + fresh ACL deny -> not listed or counted
    svc.connectors["jira"].docs["jira:PAY-1"] = C.model_copy(update={"version": "3"})
    svc.connectors["jira"].readers["jira:PAY-1"] = {"priya"}
    svc.connectors["jira"].check_access = lambda user, d, known_version=None: AccessCheck(
        doc_id=d, verdict="drift_material" if d == "jira:PAY-1" else "verified",
        current_version="3" if d == "jira:PAY-1" else "1", checked_at=NOW)
    body = _c(app, svc, "jdoe").get("/ib/map").json()
    assert "jira:PAY-1" not in json.dumps(body) and "migration epic" not in json.dumps(body)


# ------------------------------------------------------------------------------------------------
# plan item 5: a trace failure is ledgered before the 503
# ------------------------------------------------------------------------------------------------
def test_trace_reconstruction_failure_is_ledgered_then_503(tmp_path, monkeypatch):
    app, svc = _world(tmp_path, real_ledger=True)
    jd = _c(app, svc, "jdoe")
    rid = jd.post("/ib/ask", json={"question": "database migration"}).json()["request_id"]

    def boom(_rid):
        raise RuntimeError("secret detail slack:t2")
    monkeypatch.setattr(svc.projection, "reconstruct", boom)
    r = jd.get(f"/ib/trace/{rid}")
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    ev = _events(svc, "trace.viewed")[-1]
    assert ev.outcome == "error" and ev.sub == "jdoe" and ev.citations_returned == 0
    assert "secret" not in ev.model_dump_json() and "slack:t2" not in ev.model_dump_json()


def test_trace_authorisation_failure_is_ledgered_then_503(tmp_path, monkeypatch):
    app, svc = _world(tmp_path, real_ledger=True)
    jd = _c(app, svc, "jdoe")
    rid = jd.post("/ib/ask", json={"question": "database migration"}).json()["request_id"]

    def boom(*a, **k):
        raise RuntimeError("authz down")
    monkeypatch.setattr(authz, "authorize", boom)
    assert jd.get(f"/ib/trace/{rid}").status_code == 503
    assert _events(svc, "trace.viewed")[-1].outcome == "error"


# ------------------------------------------------------------------------------------------------
# #2 the stream does not time the live permission checks
# ------------------------------------------------------------------------------------------------
def test_gate1_and_gate2_are_held_until_the_checks_are_sealed(tmp_path, monkeypatch):
    """A delayed connector makes Gate 2 take ~0.4 s. While the checks run, only ``accepted`` has
    been handed to the stream; gate1, gate2 and sealed are handed over back to back afterwards."""
    log: list[tuple[float, str]] = []
    lock = threading.Lock()

    class RecQueue(appmod.queue.Queue):
        def put(self, item, *a, **k):
            if item is not None and item[0] == "stage":
                with lock:
                    log.append((time.monotonic(), item[1]["name"]))
            return super().put(item, *a, **k)
    monkeypatch.setattr(appmod, "queue", SimpleNamespace(Queue=RecQueue))
    app, svc = stream_app(tmp_path)
    conn = svc.connectors["slack"]
    real = conn.check_access
    during: list[list[str]] = []
    window: list[float] = []

    def slow(user, doc_id, *, known_version=None):
        with lock:
            during.append([n for _, n in log])
        time.sleep(0.4)
        window.append(time.monotonic())
        return real(user, doc_id, known_version=known_version)
    conn.check_access = slow
    r = _as(app, svc, "jdoe").post("/ib/ask/stream", json={"question": PERMITTED})
    evs = parse_sse(r.text)
    assert [d["name"] for e, d in evs if e == "stage"] == list(STAGES)   # order unchanged
    assert during and all(seen == ["accepted"] for seen in during)       # nothing flushed mid-check
    t = {n: ts for ts, n in log}
    assert t["gate1"] >= max(window) and t["gate2"] >= max(window)
    assert t["sealed"] - t["gate1"] < 0.15                               # one burst after the checks
    assert [n for _, n in log][:4] == ["accepted", "gate1", "gate2", "sealed"]


def test_held_stages_are_dropped_when_the_request_fails_before_sealing(tmp_path, monkeypatch):
    app, svc = stream_app(tmp_path)
    real = svc.ledger.append

    def fail_seal(ev):
        if ev.kind == "retrieval.decided":
            from databridge_ib.audit.ledger import LedgerUnavailable
            raise LedgerUnavailable("X", "down")
        return real(ev)
    monkeypatch.setattr(svc.ledger, "append", fail_seal)
    evs = parse_sse(_as(app, svc, "jdoe").post("/ib/ask/stream",
                                               json={"question": PERMITTED}).text)
    assert [d.get("name") for e, d in evs if e == "stage"] == ["accepted"]
    assert evs[-1][0] == "error"


# ------------------------------------------------------------------------------------------------
# Opus final review #8: graph search re-searches without live-denied hits
# ------------------------------------------------------------------------------------------------
def _search_world(tmp_path, monkeypatch, *, with_stale=True):
    """TOP_K stale documents (readable in the index, revoked at the source) rank above one
    permitted document for the query; the paired world has no stale documents at all."""
    from databridge_ib.config import TOP_K
    stale = [_d(f"jira:ZS-{i}", f"zircon ledger zircon {i}", "zircon ledger zircon ledger zircon.",
                container="ZS", kind="issue", days=2) for i in range(TOP_K)]
    good = _d("jira:ZG-1", "zircon note", "A short note about the zircon ledger.", container="ZG",
              kind="issue", days=3)
    for d in stale + [good]:
        monkeypatch.setitem(G.READERS, d.doc_id, {"jdoe", "priya"})
    app, svc = _world(tmp_path, ([good] + stale) if with_stale else [good])
    for d in stale if with_stale else ():
        svc.connectors["jira"].readers[d.doc_id].discard("jdoe")
    return app, svc


def test_graph_search_rechecks_past_live_denied_hits(tmp_path, monkeypatch):
    a, sa = _search_world(tmp_path / "a", monkeypatch)
    b, sb = _search_world(tmp_path / "b", monkeypatch, with_stale=False)
    ra = _c(a, sa, "jdoe").get("/ib/graph", params={"q": "zircon ledger"}).json()
    rb = _c(b, sb, "jdoe").get("/ib/graph", params={"q": "zircon ledger"}).json()
    assert "jira:ZG-1" in G._ids(rb)
    assert "jira:ZG-1" in G._ids(ra) and ra["complete"] is True
    assert _strip(ra) == _strip(rb)
    assert "ZS-" not in json.dumps(ra)


# ------------------------------------------------------------------------------------------------
# Opus final review #3: /ib/trace does not verify the whole ledger on every call
# ------------------------------------------------------------------------------------------------
def test_trace_projection_catch_up_is_debounced(tmp_path, monkeypatch):
    app, svc = _world(tmp_path, real_ledger=True)
    jd = _c(app, svc, "jdoe")
    rid = jd.post("/ib/ask", json={"question": "database migration"}).json()["request_id"]
    calls = []
    real = svc.projection.sync

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(svc.projection, "sync", counting)
    for _ in range(4):
        assert jd.get(f"/ib/trace/{rid}").status_code == 200
    assert len(calls) == 1
    # an ask made after the catch-up is still visible at once (the projection follows appends)
    rid2 = jd.post("/ib/ask", json={"question": "replication lag"}).json()["request_id"]
    assert jd.get(f"/ib/trace/{rid2}").status_code == 200 and len(calls) == 1
    monkeypatch.setattr(appmod, "TRACE_SYNC_TTL_S", 0.0)
    jd.get(f"/ib/trace/{rid}")
    assert len(calls) == 2


# ------------------------------------------------------------------------------------------------
# Opus final review #1: Host allowlist (DNS rebinding)
# ------------------------------------------------------------------------------------------------
def test_a_foreign_host_header_is_refused_before_any_route(tmp_path):
    from fastapi.testclient import TestClient
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    for host in ("evil.example", "evil.example:3100", "10.0.0.7:8100", "localhost.evil.example"):
        r = jd.get("/ib/graph", headers={"Host": host})
        assert r.status_code == 400 and "migration" not in r.text, host
        assert TestClient(app).get("/health", headers={"Host": host}).status_code == 400
    n = len(_events(svc, "graph.viewed"))
    for host in ("127.0.0.1:8100", "localhost:8100", "[::1]:8100"):
        assert jd.get("/ib/graph", headers={"Host": host}).status_code == 200, host
    assert len(_events(svc, "graph.viewed")) == n + 3            # refused calls never ran


def test_trusted_hosts_follow_deployment(tmp_path):
    from databridge_ib.config import IbConfig
    dev = IbConfig(var_root=tmp_path)
    assert dev.trusted_hosts() == ["127.0.0.1", "localhost", "[::1]", "testserver"]
    dep = IbConfig(var_root=tmp_path)
    for k, v in dict(deployed=True, public_origin="https://brain.example.com:443",
                     extra_hosts="").items():
        object.__setattr__(dep, k, v)
    assert dep.trusted_hosts() == ["127.0.0.1", "localhost", "[::1]", "brain.example.com"]
    object.__setattr__(dep, "public_origin", "")
    assert dep.trusted_hosts() == ["127.0.0.1", "localhost", "[::1]"]
