"""A5 audit projection: rebuild == incremental, and the S5 reconstruction (jdoe x payment-gateway x
30 days) derived from the seeded specs."""
from __future__ import annotations

import datetime as dt

import pytest
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditFilter, AuditProjection
from databridge_ib.audit.content import AnswerStore
from pydantic import ValidationError

from test_audit_support import DOCS, NOW, SPECS, clone, make_cfg, seed, write_request


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit-query")
    cfg, ledger, store = seed(root)
    proj = AuditProjection(ledger, attach=False)
    proj.rebuild()
    return root, ledger, store, proj


def _pg_docs():
    return {d for d, meta in DOCS.items() if meta[1] == "payment-gateway"}


def test_rebuild_equals_incremental(tmp_path):
    cfg = make_cfg(tmp_path)
    ledger = AccessLedger(cfg)
    store = AnswerStore(cfg)
    inc = AuditProjection(ledger)                     # subscribed: applied on every append
    for sp in SPECS[:6]:
        write_request(ledger, store, sp)
    rebuilt = AuditProjection(ledger, attach=False)
    assert rebuilt.rebuild() == inc.last_seq
    assert rebuilt.snapshot() == inc.snapshot()
    assert inc.snapshot()["events"]                   # non-vacuous
    # a projection that missed events catches up with sync()
    late = AuditProjection(ledger, attach=False, catch_up=False)
    assert late.sync() == inc.last_seq
    assert late.snapshot() == inc.snapshot()


def test_sync_rebuilds_after_rollback(tmp_path):
    cfg = make_cfg(tmp_path)
    ledger = AccessLedger(cfg)
    store = AnswerStore(cfg)
    write_request(ledger, store, SPECS[0])
    import shutil
    backup = tmp_path / "backup"
    shutil.copytree(cfg.var_root, backup)
    proj = AuditProjection(ledger)
    write_request(ledger, store, SPECS[1])
    assert proj.reconstruct(SPECS[1].req_id) is not None
    store.close()
    shutil.rmtree(cfg.var_root)
    shutil.copytree(backup, cfg.var_root)             # whole-directory rollback
    proj.sync()
    assert proj.reconstruct(SPECS[1].req_id) is None  # the projection follows the ledger


def test_s5_jdoe_payment_gateway_last_30_days(seeded):
    _, _, _, proj = seeded
    since = NOW - dt.timedelta(days=30)
    f = AuditFilter(user="jdoe", container="payment-gateway", since=since, until=NOW)
    rows = proj.search(f)
    expected = {sp.req_id for sp in SPECS
                if sp.sub == "jdoe" and set(sp.docs) & _pg_docs() and sp.event_time >= since}
    assert expected and len(expected) >= 3
    assert {r["req_id"] for r in rows} == expected
    # three events (accepted, decided, released) per request, newest first
    assert len(rows) == 3 * len(expected)
    times = [dt.datetime.fromisoformat(r["event_time"]) for r in rows]
    assert times == sorted(times, reverse=True)
    for r in rows:
        assert r["sub"] == "jdoe" and r["synthetic_replay"] is True
        assert r["recorded_at"] and r["event_time"]
        assert "SECRET-ANSWER-TEXT" not in str(r)
    # the request outside the window is excluded
    old = [sp.req_id for sp in SPECS if sp.sub == "jdoe" and sp.event_time < since
           and set(sp.docs) & _pg_docs()]
    assert old and not set(old) & {r["req_id"] for r in rows}


def test_s5_reconstruction(seeded):
    _, _, store, proj = seeded
    sp = next(s for s in SPECS if s.sub == "jdoe" and s.denied)
    rec = proj.reconstruct(sp.req_id)
    assert rec["sub"] == "jdoe" and rec["question"] == sp.question and rec["channel"] == "replay"
    assert rec["event_time"] == sp.event_time.isoformat() and rec["recorded_at"]
    assert rec["synthetic_replay"] is True
    assert [c["doc_id"] for c in rec["considered"]] == list(sp.docs)
    for c in rec["considered"]:
        platform, container, cname, title = DOCS[c["doc_id"]]
        assert (c["title"], c["container"], c["platform"]) == (title, container, platform)
        assert c["verdict"] == ("forbidden" if c["doc_id"] in sp.denied else "verified")
        assert c["decision"] == ("deny" if c["doc_id"] in sp.denied else "allow")
    assert {g["doc_id"]: g["reason_code"] for g in rec["gate2"]} == \
        {d: ("acl.denied" if d in sp.denied else "acl.ok") for d in sp.docs}
    assert [r["doc_id"] for r in rec["released"]] == list(sp.released)
    assert [c["doc_id"] for c in rec["citations"]] == list(sp.cited)
    assert rec["model"]["model"] == "extractive-v1"
    assert rec["answer_ref"] and "answer" not in rec
    assert store.get(rec["answer_ref"]).answer == sp.answer     # only via the content store
    assert rec["status"] == "released"
    assert [e["kind"] for e in rec["events"]] == ["request.accepted", "retrieval.decided",
                                                 "answer.released"]


def test_failed_and_refused_reconstruction(seeded):
    _, _, _, proj = seeded
    failed = next(s for s in SPECS if s.failed)
    rec = proj.reconstruct(failed.req_id)
    assert rec["status"] == "failed" and rec["failure"]["error_code"] == "MODEL_TIMEOUT"
    refused = next(s for s in SPECS if s.refused)
    assert proj.reconstruct(refused.req_id)["status"] == "refused"
    assert proj.reconstruct("nope") is None


def test_doc_filter_and_accessors(seeded):
    _, _, _, proj = seeded
    doc = "jira:PAY-201"
    rows = proj.search(AuditFilter(doc="PAY-201", kind="request.accepted"))
    assert {r["req_id"] for r in rows} == {sp.req_id for sp in SPECS if doc in sp.docs}
    acc = proj.accessors(doc)
    want = {}
    for sp in SPECS:
        if doc in sp.docs:
            a = want.setdefault(sp.sub, {"requests": 0, "released": 0})
            a["requests"] += 1
            a["released"] += int(doc in sp.released)
    got = {a["sub"]: {"requests": a["requests"], "released": a["released"]}
           for a in acc["accessors"]}
    assert got == want and len(got) >= 2
    assert all(r["event_time"] and r["recorded_at"] for r in acc["requests"])


def test_decision_filters(seeded):
    _, _, _, proj = seeded
    deny = {r["req_id"] for r in proj.search(AuditFilter(user="ken.ext", decision="deny"))}
    assert deny == {sp.req_id for sp in SPECS if sp.sub == "ken.ext"} and deny
    scoped = {r["req_id"] for r in proj.search(AuditFilter(container="payment-gateway",
                                                           decision="deny"))}
    assert scoped == {sp.req_id for sp in SPECS if set(sp.denied) & _pg_docs()}
    allow = {r["req_id"] for r in proj.search(AuditFilter(decision="allow", limit=500))}
    assert allow == {sp.req_id for sp in SPECS if not sp.failed and not sp.refused}


def test_kind_and_limit(seeded):
    _, _, _, proj = seeded
    rows = proj.search(AuditFilter(kind="acl.changed"))
    assert len(rows) == 1 and rows[0]["event"]["platform"] == "confluence"
    assert len(proj.search(AuditFilter(limit=2))) == 2


def test_filter_is_closed_and_capped():
    with pytest.raises(ValidationError):
        AuditFilter(user="jdoe", sql="1=1")                           # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AuditFilter(limit=501)
    with pytest.raises(ValidationError):
        AuditFilter(decision="maybe")                                 # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AuditFilter(since=NOW - dt.timedelta(days=400), until=NOW)
    with pytest.raises(ValidationError):
        AuditFilter(user="jdoe' OR 1=1 --")
    with pytest.raises(ValueError):
        AuditFilter(since=NOW - dt.timedelta(days=400)).bounded(NOW)


def test_projection_from_cloned_directory(seeded, tmp_path):
    root, ledger, _, proj = seeded
    _, ledger2, _ = clone(root, tmp_path)
    p2 = AuditProjection(ledger2, attach=False)
    p2.rebuild()
    assert p2.snapshot() == proj.snapshot()


def test_bootstrap_form_and_history_shape(seeded):
    root, ledger, _, proj = seeded
    p = AuditProjection(ledger.cfg, ledger)              # bootstrap.py's call form; catches up
    assert p.snapshot() == proj.snapshot()
    rows = p.search(p.filter_type(user="jdoe", kind="request.accepted", limit=50))
    want = {sp.req_id: sp.question for sp in SPECS if sp.sub == "jdoe"}
    assert {r["req_id"]: r["question"] for r in rows} == want and want
