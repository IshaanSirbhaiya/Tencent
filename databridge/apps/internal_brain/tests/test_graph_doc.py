"""B5 /ib/graph, /ib/graph/openable, B6 /ib/doc, the asker's /ib/trace receipt, /ib/status and the
replay seeding switch.

The world: two platforms, a restricted Slack thread and a restricted Jira issue that shares a
project with permitted issues, a mention (Jira key) from a permitted thread to a permitted issue,
and a permitted issue that links to the restricted thread. Every "restricted" assertion is checked
the strong way: the response of a world WITH the restricted documents equals the response of the
same world WITHOUT them (paired worlds), not merely that a key is absent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import Services, create_app
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.config import IbConfig
from databridge_ib.index import Index
from databridge_ib.ingest import Ingestor
from databridge_ib.models import AccessCheck, SourceDoc
from databridge_ib.pipeline import Pipeline

from test_pipeline_core import NOW, FakeConnector, MemAnswers, MemLedger, _capturing_chain, _users

CANARY = "CNRY-graph-91"


def _d(doc_id, title, text, *, container, author="", links=(), version="1", kind="thread",
       days=3):
    platform = doc_id.split(":", 1)[0]
    return SourceDoc(doc_id=doc_id, platform=platform, kind=kind, container=container,
                     container_name=container.upper() if platform == "jira" else container,
                     title=title, text=text, version=version,
                     created_at=NOW - timedelta(days=days), updated_at=NOW - timedelta(days=days),
                     deep_link=f"https://x/{doc_id}", author=author or None, links=tuple(links))


A = _d("slack:t1", "migration status", "The database migration cutover is on Friday. "
       "Tracking in PAY-1. Blocker: replication lag on the ledger shard.",
       container="db-migration", author="priya", links=("jira:PAY-1",))
B = _d("slack:t2", "leads only", f"Leads only: vendor contract at risk {CANARY}. See PAY-2.",
       container="db-migration-leads", author="priya", links=("jira:PAY-2",))
C = _d("jira:PAY-1", "Database migration epic", "Epic for the database migration cutover.",
       container="PAY", author="jdoe", kind="issue", days=2)
D = _d("jira:PAY-2", "Vendor contract escalation", f"Confidential escalation {CANARY}.",
       container="PAY", author="priya", kind="issue", days=1)
E = _d("jira:PAY-3", "Replication lag follow-up", "Follow-up on replication lag. Thread: slack:t2",
       container="PAY", author="jdoe", kind="issue", links=("slack:t2",), days=4)
ALL = [A, B, C, D, E]
PUBLIC_ONLY = [A, C, E]
READERS = {"slack:t1": {"jdoe", "priya"}, "slack:t2": {"priya"}, "jira:PAY-1": {"jdoe", "priya"},
           "jira:PAY-2": {"priya"}, "jira:PAY-3": {"jdoe", "priya"}}
SECRETS = (CANARY, "leads only", "Vendor contract", "PAY-2", "slack:t2", "db-migration-leads")


def _world(tmp_path, docs=ALL, *, real_ledger=False, ledger=None, chain=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    readers = {d.doc_id: READERS[d.doc_id] for d in docs}
    conns = {p: FakeConnector(p, [d for d in docs if d.platform == p], readers)
             for p in ("slack", "jira")}
    idx = Index(tmp_path / "index.sqlite")
    for d in docs:
        idx.upsert(d, conns[d.platform].acl(d.doc_id))
    directory = _users()
    for uid in ("jdoe", "priya", "ken.ext"):
        directory._users[uid] = directory.get(uid).model_copy(update={"roles": ("employee",)})
    cfg = IbConfig(var_root=tmp_path / "var")
    led = AccessLedger(cfg) if real_ledger else (ledger or MemLedger())
    proj = AuditProjection(led) if real_ledger else None
    pipe = Pipeline(index=idx, directory=directory, connectors=conns, ledger=led,
                    answers=MemAnswers(), chain=chain)
    svc = Services(cfg=cfg, directory=directory, index=idx, connectors=conns, ledger=led,
                   answers=MemAnswers(), pipeline=pipe,
                   ingestor=Ingestor(idx, conns, tmp_path / "c.json", directory=directory),
                   projection=proj)
    return create_app(svc), svc


def _c(app, svc, sub, roles=("employee",), **kw):
    tok = authmod.mint(svc.cfg.token_key, sub, list(roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"}, **kw)


def _events(svc, kind):
    led = svc.ledger
    evs = led.events if isinstance(led, MemLedger) else [e.event for e in led.events()]
    return [e for e in evs if getattr(e, "kind", "") == kind]


def _strip(body, *keys):
    body = json.loads(json.dumps(body))
    for k in ("as_of",) + keys:
        body.pop(k, None)
    return json.dumps(body, sort_keys=True)


def _ids(body):
    return {n["id"] for n in body["nodes"]}


# ------------------------------------------------------------------------------------------------
# /ib/graph
# ------------------------------------------------------------------------------------------------
def test_graph_overview_lists_only_confirmed_containers_and_counts(tmp_path):
    app, svc = _world(tmp_path)
    r = _c(app, svc, "jdoe").get("/ib/graph")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "overview" and body["sub"] == "jdoe"
    text = json.dumps(body)
    for s in SECRETS:
        assert s not in text
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes["container:jira:PAY"]["count"] == 2              # PAY-2 is not counted
    assert nodes["container:slack:db-migration"]["count"] == 1
    assert "container:slack:db-migration-leads" not in nodes
    assert nodes["person:jdoe"]["count"] == 2 and nodes["person:priya"]["count"] == 1
    assert all(e["source"] in nodes and e["target"] in nodes for e in body["edges"])
    # priya sees everything she can open
    full = {n["id"]: n for n in _c(app, svc, "priya").get("/ib/graph").json()["nodes"]}
    assert full["container:jira:PAY"]["count"] == 3
    assert "container:slack:db-migration-leads" in full


def test_graph_is_identical_with_and_without_restricted_docs(tmp_path):
    a, sa = _world(tmp_path / "a", ALL)
    b, sb = _world(tmp_path / "b", PUBLIC_ONLY)
    calls = [{}, {"focus": "container:jira:PAY"}, {"focus": "jira:PAY-3"}, {"focus": "slack:t1"},
             {"focus": "person:priya"}, {"focus": "platform:jira"}, {"q": "replication lag"},
             {"q": "vendor contract"}, {"focus": "slack:t2"}, {"focus": "container:jira:PAY"}]
    for params in calls:
        ra = _c(a, sa, "jdoe").get("/ib/graph", params=params)
        rb = _c(b, sb, "jdoe").get("/ib/graph", params=params)
        assert ra.status_code == rb.status_code, params
        if ra.status_code == 200:
            assert _strip(ra.json()) == _strip(rb.json()), params
            for s in SECRETS:
                assert s not in ra.text, (params, s)
        else:
            assert ra.content == rb.content


def test_graph_focus_restricted_is_the_same_404_as_unknown(tmp_path):
    app, svc = _world(tmp_path)
    c = _c(app, svc, "jdoe")
    bodies = []
    for focus in ("slack:t2", "slack:nope", "jira:PAY-2", "container:slack:db-migration-leads",
                  "container:slack:nope", "person:nobody", "platform:drive", "container:x"):
        r = c.get("/ib/graph", params={"focus": focus})
        assert r.status_code == 404, focus
        bodies.append(r.content)
    assert len(set(bodies)) == 1
    assert all(e.outcome == "not_found" for e in _events(svc, "graph.viewed"))


def test_graph_focus_doc_neighbourhood_mentions_and_links(tmp_path):
    app, svc = _world(tmp_path)
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "slack:t1"}).json()
    ids = _ids(body)
    assert {"slack:t1", "container:slack:db-migration", "person:priya", "jira:PAY-1"} <= ids
    kinds = {(e["source"], e["target"]): e["kind"] for e in body["edges"]}
    assert kinds[("slack:t1", "jira:PAY-1")] == "mentions"
    assert kinds[("person:priya", "slack:t1")] == "authored"
    assert kinds[("container:slack:db-migration", "slack:t1")] == "contains"
    # PAY-3 links to the restricted thread: for priya it is an edge, for jdoe nothing
    pr = _c(app, svc, "priya").get("/ib/graph", params={"focus": "jira:PAY-3"}).json()
    assert any(e["source"] == "jira:PAY-3" and e["target"] == "slack:t2" and e["kind"] == "links"
               for e in pr["edges"])
    jd = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "jira:PAY-3"}).json()
    assert "slack:t2" not in json.dumps(jd)


def test_graph_hidden_counts_are_exact_or_absent(tmp_path):
    app, svc = _world(tmp_path)
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "jira:PAY-3"}).json()
    nodes = {n["id"]: n for n in body["nodes"]}
    # the PAY container holds PAY-1 and PAY-3 for jdoe; PAY-3 is shown, PAY-1 is not a neighbour
    pay = nodes["container:jira:PAY"]
    assert pay["count"] == 2 and pay["degree_hidden_safe"] == 1 and pay["more"] is True
    # a capped overview: containers not shown are never counted on the platform beyond the truth
    small = _c(app, svc, "jdoe").get("/ib/graph", params={"limit": 1}).json()
    assert small["truncated"] is True and len(small["nodes"]) == 1
    for n in small["nodes"]:
        assert n["degree_hidden_safe"] is None or n["degree_hidden_safe"] >= 0


def test_graph_gate2_failure_and_revocation_drop_docs(tmp_path):
    app, svc = _world(tmp_path)
    svc.connectors["jira"].readers["jira:PAY-1"].discard("jdoe")    # revoked at source, index stale

    def boom(*a, **k):
        raise RuntimeError("source down")
    svc.connectors["slack"].check_access = boom                    # every slack check errors
    body = _c(app, svc, "jdoe").get("/ib/graph").json()
    ids = _ids(body)
    assert "jira:PAY-1" not in json.dumps(body) and "Database migration epic" not in json.dumps(body)
    assert not any(i.startswith("container:slack") for i in ids)
    assert {n["id"]: n for n in body["nodes"]}["container:jira:PAY"]["count"] == 1
    r = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "slack:t1"})
    assert r.status_code == 404


def test_graph_shows_the_checked_version_after_drift(tmp_path):
    app, svc = _world(tmp_path)
    fresh = C.model_copy(update={"version": "2", "title": "Database migration epic v2"})
    svc.connectors["jira"].docs["jira:PAY-1"] = fresh               # source moved on
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "jira:PAY-1"}).json()
    node = {n["id"]: n for n in body["nodes"]}["jira:PAY-1"]
    assert node["version"] == "2" and node["label"] == "Database migration epic v2"
    # the new version is restricted: drift + fresh ACL deny -> the same 404
    svc.connectors["jira"].docs["jira:PAY-1"] = C.model_copy(update={"version": "3"})
    svc.connectors["jira"].readers["jira:PAY-1"] = {"priya"}
    svc.connectors["jira"].check_access = lambda user, d, known_version=None: AccessCheck(
        doc_id=d, verdict="drift_material", current_version="3", checked_at=NOW)
    assert _c(app, svc, "jdoe").get("/ib/graph", params={"focus": "jira:PAY-1"}).status_code == 404


def test_graph_search_returns_permitted_hits_and_containers(tmp_path):
    app, svc = _world(tmp_path)
    body = _c(app, svc, "jdoe").get("/ib/graph", params={"q": "replication lag"}).json()
    assert body["mode"] == "search" and body["query"] == "replication lag"
    ranked = sorted((n for n in body["nodes"] if "rank" in n), key=lambda n: n["rank"])
    assert ranked and {n["id"] for n in ranked} <= {"slack:t1", "jira:PAY-3"}
    assert all(n["container"] in _ids(body) for n in ranked)
    empty = _c(app, svc, "jdoe").get("/ib/graph", params={"q": "zebra quantum orchid"}).json()
    assert empty["nodes"] == [] and empty["edges"] == []


def test_graph_view_is_ledgered_with_counts_only(tmp_path):
    app, svc = _world(tmp_path)
    _c(app, svc, "jdoe").get("/ib/graph", params={"q": "replication lag"})
    ev = _events(svc, "graph.viewed")[-1]
    assert ev.sub == "jdoe" and ev.mode == "search" and ev.outcome == "ok"
    assert ev.nodes_returned >= 1 and ev.docs_checked >= ev.docs_returned
    dumped = ev.model_dump_json()
    assert "replication" not in dumped and "PAY-3" not in dumped


def test_graph_ledger_down_is_503(tmp_path):
    class Down(MemLedger):
        def append(self, ev):
            raise RuntimeError("ledger down")
    app, svc = _world(tmp_path, ledger=Down())
    for params in ({}, {"focus": "slack:t1"}, {"focus": "slack:t2"}, {"focus": "nope"}):
        r = _c(app, svc, "jdoe").get("/ib/graph", params=params)
        assert r.status_code == 503 and "migration" not in r.text
    r = _c(app, svc, "jdoe").post("/ib/graph/openable", json={"doc_ids": ["slack:t1"]})
    assert r.status_code == 503


def test_graph_params_are_bounded(tmp_path):
    app, svc = _world(tmp_path)
    c = _c(app, svc, "jdoe")
    assert c.get("/ib/graph", params={"limit": 0}).status_code == 422
    assert c.get("/ib/graph", params={"limit": 301}).status_code == 422
    assert c.get("/ib/graph", params={"q": "x" * 501}).status_code == 422
    assert TestClient(app).get("/ib/graph").status_code == 401


# ------------------------------------------------------------------------------------------------
# /ib/graph/openable
# ------------------------------------------------------------------------------------------------
def test_openable_returns_only_what_the_caller_can_open(tmp_path):
    app, svc = _world(tmp_path)
    ask = ["slack:t2", "slack:t1", "jira:PAY-2", "jira:PAY-1", "nope:1", "slack:t1"]
    r = _c(app, svc, "jdoe").post("/ib/graph/openable", json={"doc_ids": ask})
    assert r.status_code == 200
    assert r.json()["openable"] == ["slack:t1", "jira:PAY-1"] and r.json()["sub"] == "jdoe"
    assert _c(app, svc, "priya").post("/ib/graph/openable", json={"doc_ids": ask}).json()[
        "openable"] == ["slack:t2", "slack:t1", "jira:PAY-2", "jira:PAY-1"]
    ev = _events(svc, "graph.viewed")[-1]
    assert ev.mode == "openable" and ev.docs_returned == 4
    c = _c(app, svc, "jdoe")
    assert c.post("/ib/graph/openable", json={"doc_ids": ["x"] * 301}).status_code == 422
    assert c.post("/ib/graph/openable", json={"doc_ids": [], "user": "priya"}).status_code == 422
    # a restricted id and an unknown id are left out the same way, in both worlds
    b, sb = _world(tmp_path / "b", PUBLIC_ONLY)
    rb = _c(b, sb, "jdoe").post("/ib/graph/openable", json={"doc_ids": ask})
    assert _strip(r.json()) == _strip(rb.json())


# ------------------------------------------------------------------------------------------------
# /ib/doc
# ------------------------------------------------------------------------------------------------
def test_doc_restricted_and_unknown_are_the_same_404(tmp_path):
    app, svc = _world(tmp_path)
    c = _c(app, svc, "jdoe")
    rs = [c.get(f"/ib/doc/{d}") for d in ("slack:t2", "jira:PAY-2", "slack:nope", "x")]
    assert {r.status_code for r in rs} == {404}
    assert len({r.content for r in rs}) == 1 and len({tuple(r.headers.items()) for r in rs}) == 1
    # each attempt was ledgered before the decision
    assert [e.doc_id for e in _events(svc, "doc.viewed")] == ["slack:t2", "jira:PAY-2",
                                                              "slack:nope", "x"]


def test_doc_returns_whitelisted_fields_and_permitted_links(tmp_path):
    app, svc = _world(tmp_path)
    r = _c(app, svc, "jdoe").get("/ib/doc/slack:t1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "migration status" and body["platform"] == "slack"
    assert body["author"] == {"id": "priya", "name": "P"}
    assert body["excerpt"].startswith("The database migration cutover")
    assert body["links"] == [{"doc_id": "jira:PAY-1", "platform": "jira",
                              "title": "Database migration epic", "container_name": "PAY",
                              "kind": "issue", "relation": "mentions"}]
    lin = body["lineage"]
    assert lin["connector"] == "slack" and lin["authz_checked_at"] and lin["live_read"] is False
    for raw in ("acl", "text", "groups", "indexed_at"):
        assert raw not in body
    # a link to a restricted doc is not listed, and not hinted
    e = _c(app, svc, "jdoe").get("/ib/doc/jira:PAY-3").json()
    assert e["links"] == [] and "slack:t2" not in json.dumps({k: v for k, v in e.items()
                                                                if k != "excerpt"})


def test_doc_best_chunk_and_drift(tmp_path):
    app, svc = _world(tmp_path)
    r = _c(app, svc, "jdoe").get("/ib/doc/slack:t1", params={"q": "replication lag"}).json()
    assert r["excerpt_kind"] == "best_chunk" and "replication lag" in r["excerpt"]
    fresh = A.model_copy(update={"version": "2", "text": "Cutover moved to Monday."})
    svc.connectors["slack"].docs["slack:t1"] = fresh
    r = _c(app, svc, "jdoe").get("/ib/doc/slack:t1").json()
    assert r["version"] == "2" and r["excerpt"] == "Cutover moved to Monday."
    assert r["lineage"]["live_read"] is True and "Friday" not in json.dumps(r)


def test_doc_is_identical_with_and_without_restricted_docs(tmp_path):
    a, sa = _world(tmp_path / "a", ALL)
    b, sb = _world(tmp_path / "b", PUBLIC_ONLY)
    for d in ("slack:t1", "jira:PAY-3", "jira:PAY-1"):
        ra = _c(a, sa, "jdoe").get(f"/ib/doc/{d}").json()
        rb = _c(b, sb, "jdoe").get(f"/ib/doc/{d}").json()
        for body in (ra, rb):
            body["lineage"].pop("content_checked_at")
            body["lineage"].pop("last_sync_at")
        assert _strip(ra) == _strip(rb)
    for d in ("slack:t2", "jira:PAY-2"):
        assert _c(a, sa, "jdoe").get(f"/ib/doc/{d}").content == \
            _c(b, sb, "jdoe").get(f"/ib/doc/{d}").content


def test_doc_ledger_down_is_the_same_503_for_every_id(tmp_path):
    class Down(MemLedger):
        def append(self, ev):
            raise RuntimeError("ledger down")
    app, svc = _world(tmp_path, ledger=Down())
    calls = 0
    real = svc.connectors["slack"].check_access

    def counting(*a, **k):
        nonlocal calls
        calls += 1
        return real(*a, **k)
    svc.connectors["slack"].check_access = counting
    rs = [_c(app, svc, "jdoe").get(f"/ib/doc/{d}") for d in ("slack:t1", "slack:t2", "nope")]
    assert {r.status_code for r in rs} == {503} and len({r.content for r in rs}) == 1
    assert calls == 0                     # the ledger refused before any access decision


# ------------------------------------------------------------------------------------------------
# /ib/trace (the asker's receipt)
# ------------------------------------------------------------------------------------------------
def _trace(app, svc, sub, rid):
    return _c(app, svc, sub).get(f"/ib/trace/{rid}")


def test_trace_lists_only_cited_docs_still_openable(tmp_path):
    """priya's answer is built from two released threads but cites only [C1]: the uncited one
    never appears. After a revocation at the source the cited one disappears too."""
    app, svc = _world(tmp_path, real_ledger=True, chain=_capturing_chain([]))
    pr = _c(app, svc, "priya")
    resp = pr.post("/ib/ask", json={"question": "database migration vendor contract"}).json()
    rel = [e.event for e in svc.ledger.events() if e.kind == "retrieval.decided"][-1]
    cited = {c["doc_id"] for c in resp["citations"]}
    assert len(rel.released) >= 2 and len(cited) == 1
    t = _trace(app, svc, "priya", resp["request_id"]).json()
    assert {c["doc_id"] for c in t["citations"]} == cited
    for d in set(rel.released) - cited:
        assert d not in json.dumps(t)
    only = next(iter(cited))
    svc.connectors[only.split(":")[0]].readers[only].discard("priya")
    t2 = _trace(app, svc, "priya", resp["request_id"]).json()
    assert t2["citations"] == [] and only not in json.dumps(t2)
    views = _events(svc, "trace.viewed")
    assert [v.citations_returned for v in views] == [1, 0]


def test_trace_of_a_refusal_and_of_a_failure_show_no_documents(tmp_path):
    app, svc = _world(tmp_path, real_ledger=True)
    jd = _c(app, svc, "jdoe")
    rid = jd.post("/ib/ask", json={"question": "zebra quantum orchid budget"}).json()["request_id"]
    t = _trace(app, svc, "jdoe", rid).json()
    assert t["status"] == "refused" and t["citations"] == []

    class Broken(MemAnswers):
        def put(self, req_id, answer):
            raise RuntimeError("store down")
    svc.pipeline.answers = Broken()
    assert jd.post("/ib/ask", json={"question": "database migration"}).status_code == 503
    failed = _events(svc, "request.failed")[-1]
    t = _trace(app, svc, "jdoe", failed.req_id).json()
    assert t["status"] == "failed" and t["citations"] == []
    status = {s["name"]: s["status"] for s in t["stages"]}
    assert status["seal"] == "ok" and status["compose"] == "skipped"
    text = json.dumps(t)
    for d in ("slack:t1", "jira:PAY-1", "migration status", "Database migration epic"):
        assert d not in text


def test_trace_restricted_question_equals_nonexistent_question(tmp_path):
    """Paired worlds: jdoe asks about the restricted vendor escalation. The whole receipt equals
    the receipt in a world where that content never existed (ids, hashes and times aside)."""
    out = []
    for name, docs in (("a", ALL), ("b", PUBLIC_ONLY)):
        app, svc = _world(tmp_path / name, docs, real_ledger=True)
        rid = _c(app, svc, "jdoe").post(
            "/ib/ask", json={"question": "vendor contract escalation"}).json()["request_id"]
        t = _trace(app, svc, "jdoe", rid).json()
        for k in ("req_id", "event_time", "answer_sha256", "latency_ms_rounded"):
            t.pop(k)
        for r in t["records"]:
            r.pop("hash")
        out.append(json.dumps(t, sort_keys=True))
    assert out[0] == out[1]
    for s in SECRETS:
        assert s not in out[0]


def test_trace_ledger_down_is_503(tmp_path):
    app, svc = _world(tmp_path, real_ledger=True)
    rid = _c(app, svc, "jdoe").post("/ib/ask", json={"question": "database migration"}).json()[
        "request_id"]

    def down(ev):
        raise RuntimeError("ledger down")
    svc.ledger.append = down
    r = _trace(app, svc, "jdoe", rid)
    assert r.status_code == 503 and "migration" not in r.text


def test_history_labels_channel_and_replay(tmp_path):
    app, svc = _world(tmp_path, real_ledger=True)
    _c(app, svc, "jdoe").post("/ib/ask", json={"question": "database migration"})
    svc.pipeline.ask(svc.directory.get("jdoe"), "replication lag", channel="replay", replay=True,
                     event_time=datetime.now(timezone.utc) - timedelta(days=2))
    svc.projection.sync()
    items = _c(app, svc, "jdoe").get("/ib/history").json()["items"]
    labels = {(i["channel"], i["synthetic_replay"]) for i in items}
    assert labels == {("ui", False), ("replay", True)}


# ------------------------------------------------------------------------------------------------
# replay labelling (B7 switch) and RequestFailed carrying the replay label
# ------------------------------------------------------------------------------------------------
KEY = "replay-key-for-tests-0123456789"


def _replay_world(tmp_path, key=KEY):
    app, svc = _world(tmp_path, real_ledger=True)
    object.__setattr__(svc.cfg, "replay_key", key)
    return app, svc


def test_replay_switch_labels_the_request(tmp_path):
    app, svc = _replay_world(tmp_path)
    at = (datetime.now(timezone.utc) - timedelta(days=5)).replace(microsecond=0)
    c = _c(app, svc, "jdoe", client=("127.0.0.1", 5000))
    r = c.post("/ib/ask", json={"question": "database migration"},
               headers={"X-IB-Replay-Key": KEY, "X-IB-Replay-At": at.isoformat()})
    assert r.status_code == 200, r.text
    acc = _events(svc, "request.accepted")[-1]
    assert acc.channel == "replay" and acc.synthetic_replay is True and acc.event_time == at
    same = [x.data for x in svc.ledger.events() if x.data.get("req_id") == acc.req_id]
    assert len(same) == 3 and all(d["synthetic_replay"] for d in same)


def test_replay_switch_refuses_every_unsafe_call(tmp_path):
    app, svc = _replay_world(tmp_path)
    at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    loop = _c(app, svc, "jdoe", client=("127.0.0.1", 5000))
    q = {"question": "database migration"}
    assert loop.post("/ib/ask", json=q, headers={"X-IB-Replay-Key": "wrong-" + KEY,
                                                 "X-IB-Replay-At": at}).status_code == 403
    remote = _c(app, svc, "jdoe", client=("10.0.0.5", 5000))
    assert remote.post("/ib/ask", json=q, headers={"X-IB-Replay-Key": KEY,
                                                   "X-IB-Replay-At": at}).status_code == 403
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert loop.post("/ib/ask", json=q, headers={"X-IB-Replay-Key": KEY,
                                                 "X-IB-Replay-At": future}).status_code == 422
    tok = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    cookie = TestClient(app, client=("127.0.0.1", 5000), cookies={"ib_session": tok})
    assert cookie.post("/ib/ask", json=q, headers={"X-IB-Replay-Key": KEY,
                                                   "X-IB-Replay-At": at}).status_code == 403
    off, soff = _replay_world(tmp_path / "off", key="")
    loop_off = _c(off, soff, "jdoe", client=("127.0.0.1", 5000))
    assert loop_off.post("/ib/ask", json=q, headers={"X-IB-Replay-Key": KEY,
                                                     "X-IB-Replay-At": at}).status_code == 403
    assert not [e for e in _events(svc, "request.accepted") if e.channel == "replay"]


def test_deployed_config_refuses_a_replay_key(tmp_path):
    cfg = IbConfig(var_root=tmp_path)
    for k, v in dict(deployed=True, token_key="t" * 40, ledger_key="l" * 40, svc_token="s1",
                     admin_token="a1", webhook_secret="w1", replay_key=KEY).items():
        object.__setattr__(cfg, k, v)
    with pytest.raises(RuntimeError, match="IB_REPLAY_KEY"):
        cfg.assert_deployable()


def test_request_failed_keeps_the_replay_label_and_time(tmp_path):
    app, svc = _world(tmp_path)

    class Broken(MemAnswers):
        def put(self, req_id, answer):
            raise RuntimeError("store down")
    svc.pipeline.answers = Broken()
    at = datetime.now(timezone.utc) - timedelta(days=9)
    with pytest.raises(RuntimeError):
        svc.pipeline.ask(svc.directory.get("jdoe"), "database migration", channel="replay",
                         replay=True, event_time=at)
    failed = _events(svc, "request.failed")[-1]
    assert failed.synthetic_replay is True and failed.event_time == at


# ------------------------------------------------------------------------------------------------
# /ib/status and /health
# ------------------------------------------------------------------------------------------------
def test_status_is_authenticated_and_never_returns_a_secret(tmp_path, monkeypatch):
    secret = "sk-secret-value-never-returned-123"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("OPENAI_API_BASE", "http://internal-llm.example/v1")
    monkeypatch.setenv("IB_MODELS", "hy3, deepseek-v4.1-flash")
    monkeypatch.setenv("IB_FORCE_OFFLINE", "1")
    monkeypatch.delenv("TOKENHUB_API_KEY", raising=False)
    app, svc = _world(tmp_path, real_ledger=True)
    assert TestClient(app).get("/ib/status").status_code == 401
    assert TestClient(app).get("/health").json() == {"ok": True}
    r = _c(app, svc, "jdoe").get("/ib/status")
    assert r.status_code == 200
    body = r.json()
    assert body["models"]["chain"] == ["hy3", "deepseek-v4.1-flash"]
    assert body["key_configured"] is True and body["force_offline"] is True
    assert body["models"]["active"] is False and body["ledger"] == {"ok": True}
    assert secret not in r.text and "internal-llm" not in r.text and "seq" not in r.text
