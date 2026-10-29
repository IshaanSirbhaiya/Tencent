"""B11 POST /audit/tamper-demo: the tamper scenarios on a temporary COPY of the live ledger.

Compliance only, ledgered write-ahead, the live log/anchors/external head byte-identical (apart from
the endpoint's own appended audit records), the edit detected at the record it edited, bounded
runtime, the temporary directory removed."""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_ib.audit import tamper as tampermod
from databridge_ib.audit.anchor import FileSink, publish_head
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.audit.routes import make_audit_router
from databridge_ib.models import AuditQueryEvent

from test_audit_routes import CO, require_role
from test_audit_support import NOW, clone, seed


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("tamper-route-src")
    seed(root)
    return root


@pytest.fixture
def env(template, tmp_path):
    cfg, _, store = clone(template, tmp_path)
    sink = FileSink(tmp_path / "ext" / "heads.jsonl")
    ledger = AccessLedger(cfg, external=sink)
    publish_head(ledger, sink)
    ledger.append(AuditQueryEvent(sub="sara.sec", filter={"op": "after-publication"},
                                  result_count=0, event_time=NOW))
    ledger.append(AuditQueryEvent(sub="sara.sec", filter={"op": "after-publication-2"},
                                  result_count=0, event_time=NOW))
    app = FastAPI()
    app.include_router(make_audit_router(ledger, store, AuditProjection(ledger), require_role,
                                         sink=sink, clock=lambda: NOW))
    yield TestClient(app), ledger, sink
    store.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _tamper_queries(ledger) -> list[dict]:
    return [e.data["filter"] for e in ledger.events() if e.kind == "audit.query"
            and e.data["filter"].get("op") == "tamper-demo"]


def test_non_compliance_roles_forbidden_and_nothing_copied(env):
    c, ledger, _ = env
    before = ledger.head()
    for sub in ("jdoe", "tom.it"):
        assert c.post("/audit/tamper-demo", headers={"X-Test-Sub": sub}).status_code == 403
    assert c.post("/audit/tamper-demo").status_code == 401
    assert ledger.head() == before                       # nothing ran, nothing was recorded


def test_runs_on_a_copy_and_the_real_ledger_is_unchanged(env):
    c, ledger, sink = env
    log, anchors, ext = Path(ledger.path), Path(ledger.anchors_path), Path(sink.path)
    log_bytes = log.read_bytes()
    hashes = {p: _sha(p) for p in (anchors, ext)}
    seq_before, head_before = ledger.head()
    r = c.post("/audit/tamper-demo", headers=CO)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["real_log_modified"] is False
    # the live log is the old bytes plus exactly the endpoint's own two audit records
    after = log.read_bytes()
    assert after[:len(log_bytes)] == log_bytes
    assert ledger.head()[0] == seq_before + 2
    # anchors grow by one anchor per append; the external head file is untouched
    assert _sha(ext) == hashes[ext]
    assert ledger.verify().ok
    # head_before names the copied head (the write-ahead record is part of the copy)
    recs = [json.loads(x) for x in log_bytes.decode().splitlines() if x.strip()]
    copied_head = [json.loads(x) for x in after.decode().splitlines() if x.strip()][seq_before]["hash"]
    assert body["head_before"] == copied_head[:12] and head_before == recs[-1]["hash"]
    assert body["records_copied"] == seq_before + 1 and body["external_head_copied"] is True


def test_scenarios_report_and_edit_detected_at_the_right_seq(env):
    c, _, _ = env
    body = c.post("/audit/tamper-demo", headers=CO).json()
    by = {s["id"]: s for s in body["scenarios"]}
    assert set(by) == {sid for sid, _, _ in tampermod.SCENARIOS}
    for s in body["scenarios"]:
        assert s["ok"] is True, s                         # every outcome matched its expectation
        assert {"id", "title", "what_we_did", "expected", "detected", "problems"} <= set(s)
    assert by["baseline"]["detected"] is False
    edit = by["edit-record"]
    k = int(edit["what_we_did"].split("record ")[1].split(" ")[0])
    assert edit["detected"] is True and edit["first_bad_seq"] == k
    # the edit flips a DECISION (Fable final review): a sealed Gate-2 denial in record k
    assert edit["what_we_did"] == (f"Changed one Gate-2 decision in record {k} from 'forbidden' "
                                   f"to 'verified' without re-sealing it.")
    rec = json.loads(Path(env[1].path).read_text(encoding="utf-8").splitlines()[k - 1])
    assert rec["ib_event"]["kind"] == "retrieval.decided"
    assert "forbidden" in [g["verdict"] for g in rec["ib_event"]["gate2"]]
    for only_external in ("directory-rollback", "key-holder-rewrite"):
        assert by[only_external]["internal_ok"] and by[only_external]["external_head_match"] is False
    assert by["rollback-after-publication"]["detected"] is False         # the stated window
    for s in body["scenarios"]:                           # no temp path leaks into the report
        assert not any(tempfile.gettempdir() in p for p in s["problems"])


def test_is_ledgered_write_ahead_then_ok(env):
    c, ledger, _ = env
    c.post("/audit/tamper-demo", headers=CO)
    q = _tamper_queries(ledger)
    assert [x["outcome"] for x in q] == ["started", "ok"]
    assert q[1]["real_log_modified"] is False and q[1]["as_expected"] == len(tampermod.SCENARIOS)


def test_ledger_down_is_503_and_nothing_runs(env, monkeypatch):
    c, ledger, _ = env
    ran = []
    monkeypatch.setattr("databridge_ib.audit.routes.run_on_live_copy",
                        lambda *a, **k: ran.append(1))
    from databridge_ib.audit.ledger import LedgerUnavailable

    def refuse(_ev):
        raise LedgerUnavailable("X", "down")
    monkeypatch.setattr(ledger, "append", refuse)
    assert c.post("/audit/tamper-demo", headers=CO).status_code == 503
    assert ran == []                                      # write-ahead: no record, no run


def test_failure_is_a_ledgered_500_and_busy_is_409(env, monkeypatch):
    c, ledger, _ = env

    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr("databridge_ib.audit.routes.run_on_live_copy", boom)
    assert c.post("/audit/tamper-demo", headers=CO).status_code == 500

    def busy(*a, **k):
        raise tampermod.TamperDemoBusy("busy")
    monkeypatch.setattr("databridge_ib.audit.routes.run_on_live_copy", busy)
    assert c.post("/audit/tamper-demo", headers=CO).status_code == 409
    assert [x["outcome"] for x in _tamper_queries(ledger)] == ["started", "error", "started",
                                                               "rejected"]


def test_temp_directory_is_removed(env, monkeypatch, tmp_path):
    _, ledger, sink = env
    made = []
    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, dir=str(tmp_path), **{x: y for x, y in k.items() if x != "dir"})
        made.append(Path(d))
        return d
    monkeypatch.setattr(tampermod.tempfile, "mkdtemp", spy)
    out = tampermod.run_on_live_copy(ledger, sink)
    assert out["real_log_modified"] is False and made and not any(d.exists() for d in made)


def test_runtime_is_bounded_on_1500_records(tmp_path):
    """~1,500 records (written with the sealing key directly, so the fixture is fast), one external
    publication: the whole run stays under 5 s."""
    from test_audit_support import make_cfg
    cfg = make_cfg(tmp_path)
    ledger = AccessLedger(cfg)
    ledger.append(AuditQueryEvent(sub="sara.sec", filter={"op": "seed"}, result_count=0,
                                  event_time=NOW))
    var = cfg.var_root
    one = json.loads(Path(ledger.path).read_text(encoding="utf-8").splitlines()[0])
    tampermod.reseal(var, cfg.ledger_key.encode(), lambda recs: [dict(one) for _ in range(1500)])
    sink = FileSink(tmp_path / "ext" / "heads.jsonl")
    ledger = AccessLedger(cfg, external=sink)
    publish_head(ledger, sink)
    for i in range(3):
        ledger.append(AuditQueryEvent(sub="sara.sec", filter={"op": "t", "i": i}, result_count=0,
                                      event_time=NOW))
    t0 = time.perf_counter()
    out = tampermod.run_on_live_copy(ledger, sink)
    elapsed = time.perf_counter() - t0
    assert out["records_copied"] >= 1500
    assert all(s["ok"] for s in out["scenarios"]), out["scenarios"]
    assert elapsed < 5.0, elapsed


def test_budget_skips_instead_of_running_long(env):
    _, ledger, _ = env
    src = Path(tempfile.mkdtemp())
    try:
        out = tampermod.run_scenarios(Path(ledger.cfg.var_root), None, ledger.cfg.ledger_key,
                                      work=src, budget_s=0.0)
    finally:
        import shutil
        shutil.rmtree(src, ignore_errors=True)
    assert out and all(s.get("skipped") and s["ok"] is False for s in out)


def test_a_failed_temp_directory_does_not_lock_the_demo(env, monkeypatch, tmp_path):
    """Codex final review #7: mkdtemp raising once used to leave the run lock held, so every later
    call answered "already running" until the service restarted."""
    _, ledger, sink = env
    real = tempfile.mkdtemp
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("no space left on device")
        return real(*a, dir=str(tmp_path), **{x: y for x, y in k.items() if x != "dir"})
    monkeypatch.setattr(tampermod.tempfile, "mkdtemp", flaky)
    with pytest.raises(OSError):
        tampermod.run_on_live_copy(ledger, sink)
    out = tampermod.run_on_live_copy(ledger, sink)              # not TamperDemoBusy
    assert out["real_log_modified"] is False and len(calls) == 2


def test_edit_record_falls_back_to_a_refusal_then_to_the_sub():
    """No Gate-2 denial on record: a refused answer is flipped to released; neither: the ``sub``."""
    def line(ev):
        return json.dumps({"ib_event": ev, "seq": 1})
    acc = {"kind": "request.accepted", "sub": "jdoe"}
    ok = {"kind": "retrieval.decided", "gate2": [{"doc_id": "d", "verdict": "verified"}]}
    ref = {"kind": "answer.released", "refused": True}
    seq, what, fn = tampermod._decision_edit([line(acc), line(ok), line(ref), line(acc)], 1)
    assert seq == 3 and "from refused to released" in what and f"record {seq} " in what
    rec = {"ib_event": dict(ref)}
    fn(rec)
    assert rec["ib_event"]["refused"] is False
    seq, what, fn = tampermod._decision_edit([line(acc), line(ok), line(acc)], 2)
    assert seq == 2 and "ib_event.sub" in what
    deny = {"kind": "retrieval.decided", "gate2": [{"doc_id": "a", "verdict": "verified"},
                                                   {"doc_id": "b", "verdict": "forbidden"}]}
    seq, what, fn = tampermod._decision_edit([line(acc), line(deny), line(ref), line(acc)], 3)
    assert seq == 2 and "'forbidden' to 'verified'" in what
    rec = {"ib_event": json.loads(json.dumps(deny))}
    fn(rec)
    assert [g["verdict"] for g in rec["ib_event"]["gate2"]] == ["verified", "verified"]
