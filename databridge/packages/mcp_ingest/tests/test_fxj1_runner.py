"""FXJ1 — the runner track's fixes for Codex's SECOND (final) review of mcp_ingest (f2-mi-runner).

Each test names the finding it proves. Expectations come from the run's own artefacts and the
corpus the fixture writes, never typed in.
"""
from __future__ import annotations

import dataclasses
import json
import threading
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture, ledger, plans, runner
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection

SECRET = "Zq9-f2-s3cr3t-Vx7"


@pytest.fixture
def store(walnut_env):
    from databridge_graph.store import GraphStore

    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")


def _source(corpus, sid: str = "docs", db=None) -> str:
    root, _ = corpus
    mi.register_connection(fs_connection(sid, root, db=db))
    mi.propose_plan(sid)
    return sid


def _crash(run_dir: Path, *unfinished: str) -> None:
    p = run_dir / "stages.json"
    stages = json.loads(p.read_text(encoding="utf-8"))
    for s in (*unfinished, "verify", "report"):
        stages.pop(s, None)
    p.write_text(json.dumps(stages), encoding="utf-8")
    (run_dir / "report.json").unlink(missing_ok=True)


def _events(walnut_env, run_id=None) -> list[dict]:
    ev = read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
    return [e for e in ev if run_id is None or e.get("run_id") == run_id]


# --- #1 a missing verifier fails closed ----------------------------------------------------------
@pytest.mark.parametrize("verifier", ["absent", "not_callable"])
def test_f2_01_no_verifier_means_nothing_runs(walnut_env, corpus, common_vocab, store,
                                              monkeypatch, verifier):
    sid = _source(corpus)
    if verifier == "absent":
        monkeypatch.delattr(plans, "verify_plan_approval", raising=False)
    else:
        monkeypatch.setattr(plans, "verify_plan_approval", None, raising=False)
    with pytest.raises(runner.PlanNotApproved, match="cannot be verified"):
        mi.run_ingest(sid, store=store)
    assert not runner.list_runs(sid)


# --- #2 / #3 / #15 tables: no auto_approve, mapping bound to the run, meta_digest -------------------
def test_f2_02_the_runner_never_passes_auto_approve(walnut_env, corpus, fixture_db,
                                                    common_vocab, store, monkeypatch):
    from databridge_mcp_ingest import mapping

    db, _ = fixture_db
    sid = _source(corpus, "tbl", db=db)
    seen: list = []
    wrapped = mapping.ensure_mapping

    def spy(source_id, snaps, vocab, **kw):
        import sys

        if sys._getframe(1).f_globals.get("__name__", "").endswith(".runner"):
            seen.append(kw.get("auto_approve"))
        return wrapped(source_id, snaps, vocab, **kw)

    monkeypatch.setattr(mapping, "ensure_mapping", spy)
    assert runner.load_connection(sid).auto_approve is True        # the flag IS set
    report = mi.run_ingest(sid, store=store)
    assert seen and all(v is False for v in seen), seen


def test_f2_03_a_resume_under_a_different_approved_mapping_is_refused(
        walnut_env, corpus, fixture_db, common_vocab, store):
    db, _ = fixture_db
    sid = _source(corpus, "tbl", db=db)
    first = mi.run_ingest(sid, store=store)
    rd = Path(first.run_dir)
    assert (rd / runner.MAPPING_BINDING).is_file()                  # tables were built
    snap = (rd / "mapping.snapshot.yaml").read_bytes()
    # an attempt of the same run that matches the binding resumes, and the snapshot is kept
    _crash(rd, "build", "index")
    again = mi.run_ingest(sid, store=store, run_id=first.run_id)
    assert again.verify["ok"], again.verify["problems"]
    assert (rd / "mapping.snapshot.yaml").read_bytes() == snap
    # the approved mapping "changes" (binding no longer matches): refused before any ledger moves
    (rd / runner.MAPPING_BINDING).write_text(json.dumps({"mapping": "an-older-mapping"}),
                                             encoding="utf-8")
    _crash(rd, "build", "index")
    before = {p.name: p.read_bytes() for p in rd.glob("*.jsonl")}
    with pytest.raises(runner.ResumeRefused, match="mapping"):
        mi.run_ingest(sid, store=store, run_id=first.run_id)
    assert {p.name: p.read_bytes() for p in rd.glob("*.jsonl")} == before


def test_f2_03_bind_mapping_refuses_a_changed_approval(tmp_path):
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping

    def m(kind):
        return SourceMapping.model_validate({"source_id": "s", "tables": {"t": TableMapping.model_validate({
            "status": "approved", "node": {"kind": kind},
            "approval": {"principal_id": "p", "at": "x"}}).model_dump()}})

    runner._bind_mapping(tmp_path, m("A"))
    runner._bind_mapping(tmp_path, m("A"))
    with pytest.raises(runner.ResumeRefused):
        runner._bind_mapping(tmp_path, m("B"))


def test_f2_15_table_extraction_and_build_carry_the_meta_digest(walnut_env, corpus, fixture_db,
                                                                common_vocab, store):
    db, _ = fixture_db
    sid = _source(corpus, "tbl", db=db)
    rd = Path(mi.run_ingest(sid, store=store).run_dir)
    cap = {r["item_id"]: r["meta_digest"] for r in read_jsonl(rd / "captured.jsonl")
           if r.get("kind") == "table"}
    ext = [r for r in read_jsonl(rd / "extracted.jsonl") if r.get("kind") == "table"]
    built = [r for r in read_jsonl(rd / "build.jsonl") if r.get("kind") == "table"]
    assert ext and built                                            # non-vacuous
    assert all(r.get("meta_digest") == cap[r["item_id"]] for r in [*ext, *built])


# --- #4 credentials in locators ------------------------------------------------------------------
def test_f2_04_a_credential_bearing_locator_is_blocked_and_never_persisted(
        walnut_env, corpus, common_vocab, store, monkeypatch):
    sid = _source(corpus)
    real = runner.enumerate_documents
    poisoned: list[str] = []

    def leaky(*a, **k):
        for item in real(*a, **k):
            if not poisoned:
                item = dataclasses.replace(
                    item, uri=f"https://reader:{SECRET}@files.example/{item.rel}?token={SECRET}",
                    attrs={"link": f"https://x/?api_key={SECRET}"})
                poisoned.append(item.item_id)
            yield item

    monkeypatch.setattr(runner, "enumerate_documents", leaky)
    report = mi.run_ingest(sid, store=store)
    rd = Path(report.run_dir)
    blocked = {r["item_id"]: r for r in read_jsonl(rd / "blocked.jsonl")}
    assert poisoned and blocked[poisoned[0]]["rule"] == runner.CREDENTIAL_IN_LOCATOR
    assert poisoned[0] not in {r["item_id"] for r in read_jsonl(rd / "captured.jsonl")}
    for p in [*rd.rglob("*.json*"), Path(walnut_env["WALNUT_EVENTS_PATH"])]:
        assert SECRET not in p.read_text(encoding="utf-8"), p.name


def test_f2_04_ledgers_events_and_capture_metadata_scrub_locators(walnut_env, tmp_path):
    url = f"https://u:{SECRET}@h/x.pdf?token={SECRET}"
    with ledger.Ledger(tmp_path / "x.jsonl") as led:
        led.write({"item_id": "a" * 64, "uri": url, "name": url, "attrs": {"l": url},
                   "sha256": "b" * 64})
    line = (tmp_path / "x.jsonl").read_text(encoding="utf-8")
    assert SECRET not in line and "a" * 64 in line and "b" * 64 in line
    ev = tmp_path / "ev.jsonl"
    ledger.emit_event("item_added", source_id="s", run_id="r", uri=url, path=ev)
    assert SECRET not in ev.read_text(encoding="utf-8")
    sha = capture.put_bytes(b"bytes for f2 #4")
    capture.put_meta(sha, {"source": "s", "uri": url, "title": url, "attrs": {"l": url}})
    assert SECRET not in capture.meta_path(sha).read_text(encoding="utf-8")
    assert capture.get_meta(sha, expect_digest=capture.stored_meta_digest(sha))


# --- #5 extracted text is write-once and digest-enforced ---------------------------------------------
def test_f2_05_extracted_text_is_write_once(walnut_env):
    sha = capture.put_bytes(b"some captured bytes")
    first = capture.put_text(sha, "first text", text_sha256=capture.sha256_bytes(b"first text"),
                             extractor="a")
    capture.put_text(sha, "first text")                              # same text: a no-op
    with pytest.raises(capture.TextConflict) as exc:
        capture.put_text(sha, "second text")
    assert exc.value.existing_digest == capture.sha256_bytes(b"first text")
    assert first.read_text(encoding="utf-8") == "first text"


def test_f2_05_text_changed_under_a_run_is_caught_by_build_and_verify(
        walnut_env, corpus, common_vocab, store, fake_harness):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store, harness=fake_harness)
    rd = Path(report.run_dir)
    assert report.verify["ok"], report.verify["problems"]
    doc = next(r for r in read_jsonl(rd / "build.jsonl") if r.get("kind") == "document")
    tp = capture.text_path(doc["sha256"])
    text = tp.read_text(encoding="utf-8")
    tp.write_text(text.upper() if text.upper() != text else text + " ", encoding="utf-8")
    v = runner.verify_run(sid, report.run_id, store=store)
    assert any("not the text this run extracted" in p for p in v["problems"])
    # a resumed build never sends the changed text anywhere
    build = rd / "build.jsonl"
    build.write_text("".join(json.dumps(r) + "\n" for r in read_jsonl(build)
                             if r["item_id"] != doc["item_id"]), encoding="utf-8")
    _crash(rd, "build", "index")
    again = mi.run_ingest(sid, store=store, harness=fake_harness, run_id=report.run_id)
    skipped = {r["item_id"]: r["reason"] for r in read_jsonl(rd / "build_skipped.jsonl")}
    assert skipped.get(doc["item_id"]) == "text_mismatch"
    assert not again.verify["ok"]


# --- #6 every graph row this run wrote is grounded ---------------------------------------------------
def test_f2_06_an_ungrounded_graph_row_and_an_unchecked_kind_fail_verification(
        walnut_env, corpus, common_vocab, store, fake_harness, monkeypatch):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store, harness=fake_harness)
    rd = Path(report.run_dir)
    sp = report.verify["counts"]["store_provenance"]
    assert report.verify["ok"] and sp["graph_ids"] > 0 and sp["ungrounded"] == 0
    # a row the build says it wrote, whose only evidence is in no capture of this run
    lines = read_jsonl(rd / "build.jsonl")
    victim = next(r for r in lines if r.get("graph_ids"))["graph_ids"][-1]
    real_sources = store.sources_for
    monkeypatch.setattr(store, "sources_for",
                        lambda eid: [] if eid == victim else real_sources(eid))
    v = runner.verify_run(sid, report.run_id, store=store)
    assert any("carry no resolving pointer" in p for p in v["problems"]), v["problems"]
    monkeypatch.setattr(store, "sources_for", real_sources)
    assert runner.verify_run(sid, report.run_id, store=store)["ok"]
    # a pointer kind verification cannot resolve is a problem, not a pass
    real_check = runner._verify_store_provenance
    monkeypatch.setattr(runner, "_verify_store_provenance",
                        lambda *a: {**real_check(*a), "unchecked_kinds": 1})
    v = runner.verify_run(sid, report.run_id, store=store)
    assert any("kind verification cannot resolve" in p for p in v["problems"])


# --- #7 re-applying an item after a crash is idempotent ------------------------------------------------
def test_f2_07_a_build_replayed_after_a_crash_writes_nothing_new(walnut_env, corpus, common_vocab,
                                                                  store, fake_harness):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store, harness=fake_harness)
    rd = Path(report.run_dir)

    def rows():
        return {t: store.fetchone(f"SELECT COUNT(*) AS n FROM {t}")["n"]
                for t in ("nodes", "edges", "sources")}

    before = rows()
    assert before["nodes"] > 0
    # the crash: every graph transaction committed, no build line written
    (rd / "build.jsonl").write_text("", encoding="utf-8")
    (rd / "build_skipped.jsonl").write_text("", encoding="utf-8")
    _crash(rd, "build", "index")
    again = mi.run_ingest(sid, store=store, harness=fake_harness, run_id=report.run_id)
    assert again.verify["ok"], again.verify["problems"]
    assert rows() == before


# --- #8 a stale resume never rolls back newer state ----------------------------------------------
def test_f2_08_a_resume_after_a_newer_run_advanced_state_is_refused(walnut_env, corpus,
                                                                    common_vocab, store):
    sid = _source(corpus)
    a = mi.run_ingest(sid, store=store)
    _crash(Path(a.run_dir), "build", "index")
    # resuming with nobody in between is fine
    assert mi.run_ingest(sid, store=store, run_id=a.run_id).verify["ok"]
    _crash(Path(a.run_dir), "build", "index")
    mi.run_ingest(sid, store=store)                                 # run B advances the state
    state_b = runner.state_path(sid).read_bytes()
    with pytest.raises(runner.ResumeRefused, match="advanced this source's state"):
        mi.run_ingest(sid, store=store, run_id=a.run_id)
    assert runner.state_path(sid).read_bytes() == state_b


# --- #9 / #13 the tabular recipe: locked, and the event follows the durable outcome -----------------
def test_f2_09_the_whole_recipe_holds_the_lock_and_a_late_failure_is_not_ok(
        walnut_env, corpus, common_vocab, store, monkeypatch):
    from databridge_mcp_ingest.tabular import pipeline

    sid = _source(corpus)
    monkeypatch.setattr(runner, "_has_tabular_step", lambda plan: True)
    seen: dict = {}

    def fake_recipe(source_id, store_, harness, **kw):
        errs: list = []

        def other():
            try:
                with runner._source_lock(source_id):
                    errs.append("entered")
            except runner.RunInProgress:
                errs.append("refused")

        t = threading.Thread(target=other)
        t.start()
        t.join()
        seen["other_thread"] = errs
        rep = runner.run_ingest(source_id, store_, harness)   # the core run, nested
        seen["events_after_core"] = [e["type"] for e in _events(walnut_env, rep.run_id)
                                     if e["type"].startswith("run_")]
        raise RuntimeError("cards failed")

    monkeypatch.setattr(pipeline, "ingest_database", fake_recipe)
    report = mi.run_ingest(sid, store=store)
    assert seen["other_thread"] == ["refused"]
    assert seen["events_after_core"] == []                   # held until the recipe finished
    assert report.status == "unverified" and "cards failed" in report.message
    done = [e for e in _events(walnut_env, report.run_id) if e["type"].startswith("run_")]
    assert [e["type"] for e in done] == ["run_failed"]
    on_disk = json.loads((Path(report.run_dir) / "report.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "unverified"


def test_f2_13_report_json_is_durable_before_the_completion_event(walnut_env, corpus,
                                                                  common_vocab, store,
                                                                  monkeypatch):
    sid = _source(corpus)
    real = runner.emit_event
    seen: list = []

    def spy(type_, **kw):
        if type_ in ("run_completed", "run_failed"):
            rd = runner.run_dir_for(kw["source_id"], kw["run_id"])
            rep = rd / "report.json"
            seen.append(json.loads(rep.read_text(encoding="utf-8"))["status"]
                        if rep.is_file() else None)
        return real(type_, **kw)

    monkeypatch.setattr(runner, "emit_event", spy)
    report = mi.run_ingest(sid, store=store)
    assert seen == [report.status]


# --- #10 state advances only after verification ----------------------------------------------------
def test_f2_10_an_unverified_run_does_not_advance_incremental_state(walnut_env, corpus,
                                                                    common_vocab, store,
                                                                    monkeypatch):
    sid = _source(corpus)
    real = runner.verify_run
    monkeypatch.setattr(runner, "verify_run",
                        lambda *a, **k: {**real(*a, **k), "ok": False,
                                         "problems": ["injected"]})
    bad = mi.run_ingest(sid, store=store)
    built = {r["item_id"] for r in read_jsonl(Path(bad.run_dir) / "build.jsonl")}
    assert built and bad.status == "unverified"
    state = runner.load_state(sid)
    assert all(state[i].get("reason") == runner.PENDING_VERIFICATION for i in built)
    monkeypatch.setattr(runner, "verify_run", real)
    good = mi.run_ingest(sid, store=store)
    rebuilt = {r["item_id"] for r in read_jsonl(Path(good.run_dir) / "build.jsonl")}
    assert built <= rebuilt                                     # never skipped as unchanged
    state = runner.load_state(sid)
    assert all(state[i].get("graph", True) is not False for i in built)
    third = mi.run_ingest(sid, store=store)
    assert not read_jsonl(Path(third.run_dir) / "build.jsonl")    # now genuinely unchanged


# --- #11 overrides read once ------------------------------------------------------------------------
def test_f2_11_owner_overrides_are_read_once_per_run(walnut_env, corpus, common_vocab, store,
                                                     monkeypatch):
    sid = _source(corpus)
    real = runner.load_overrides
    calls: list = []
    monkeypatch.setattr(runner, "load_overrides",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    report = mi.run_ingest(sid, store=store)
    assert report.status == "ok" and len(calls) == 1


# --- #12 the first sighting is created exclusively -------------------------------------------------
def test_f2_12_racing_first_sightings_never_replace_each_other(walnut_env):
    shas = [capture.put_bytes(f"race {i}".encode()) for i in range(30)]
    sources = [f"src{n}" for n in range(6)]
    gate = threading.Barrier(len(sources))

    def go(src):
        gate.wait()
        for sha in shas:
            capture.put_meta(sha, {"source": src, "uri": f"file:///{src}/x", "title": src})

    ts = [threading.Thread(target=go, args=(s,)) for s in sources]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for sha in shas:
        meta = capture.get_meta(sha)
        named = {meta["source"]} | {s["source"] for s in meta.get("sightings") or []}
        assert named == set(sources), (sha, meta)


# --- #14 crash-safe control files and ledgers --------------------------------------------------------
def test_f2_14_a_torn_final_line_is_quarantined_not_welded(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_bytes(b'{"item_id": "a"}\n{"item_id": "b", "tor')
    assert [r["item_id"] for r in read_jsonl(p)] == ["a"]
    with ledger.Ledger(p) as led:
        led.write({"item_id": "c"})
    assert [r["item_id"] for r in read_jsonl(p)] == ["a", "c"]
    assert (tmp_path / "l.jsonl.torn.1").read_bytes() == b'{"item_id": "b", "tor'
    p.write_bytes(b'{"item_id": "a"}\nnot json\n{"item_id": "c"}\n')
    with pytest.raises(ValueError):
        read_jsonl(p)                                        # damage mid-file still raises


def test_f2_14_control_files_are_atomic_and_ledgers_synced_before_a_stage_is_done(
        walnut_env, corpus, common_vocab, store, monkeypatch):
    sid = _source(corpus)
    order: list = []
    real_atomic, real_sync = runner._atomic_text, ledger.Ledger.sync
    monkeypatch.setattr(runner, "_atomic_text",
                        lambda path, text: order.append(("atomic", Path(path).name))
                        or real_atomic(path, text))
    monkeypatch.setattr(ledger.Ledger, "sync",
                        lambda self: order.append(("sync", self.path.name)) or real_sync(self))
    mi.run_ingest(sid, store=store)
    names = {n for k, n in order if k == "atomic"}
    assert {"stages.json", "report.json", "binding.json", "state.json"} <= names
    first_stage = next(i for i, e in enumerate(order) if e == ("atomic", "stages.json"))
    assert ("sync", "captured.jsonl") in order[:first_stage]


# --- E2E run-2 N-01: passages are back-filled for documents built before they were enabled -------
def _passage_digests(sid: str) -> dict:
    from databridge_retrieval import store as rst
    from databridge_retrieval.paths import passages_dir

    p = passages_dir() / rst.store_filename(sid, "default")
    if not p.is_file():
        return {}
    conn = rst.connect(p, readonly=True)
    try:
        return {r["doc_id"]: r["sha256"] for r in conn.execute(
            "SELECT doc_id, sha256 FROM documents")}
    finally:
        conn.close()


def test_n01_documents_built_before_passages_were_on_are_back_filled(
        walnut_env, corpus, common_vocab, store, monkeypatch):
    pytest.importorskip("databridge_retrieval")
    from databridge_retrieval import doc_id_for

    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)                         # passages OFF
    built = {r["item_id"]: r for r in read_jsonl(Path(first.run_dir) / "build.jsonl")
             if r.get("kind") == "document"}
    assert built and not _passage_digests(sid)
    want = {doc_id_for({"uri": r["uri"]}, source_id=sid): r["sha256"] for r in built.values()}
    # a bounded sync: at most one back-fill per run, the rest reported pending
    monkeypatch.setenv("WALNUT_INGEST_BACKFILL_MAX", "1")
    second = mi.run_ingest(sid, store=store, passages=True)         # nothing changed
    idx = json.loads((Path(second.run_dir) / "index.json").read_text(encoding="utf-8"))
    assert not read_jsonl(Path(second.run_dir) / "build.jsonl")    # truly incremental
    assert idx["backfill"]["indexed"] == 1
    assert idx["backfill"]["pending"] == len(built) - 1
    monkeypatch.delenv("WALNUT_INGEST_BACKFILL_MAX")
    third = mi.run_ingest(sid, store=store, passages=True)
    idx = json.loads((Path(third.run_dir) / "index.json").read_text(encoding="utf-8"))
    assert idx["backfill"]["indexed"] == len(built) - 1 and idx["backfill"]["pending"] == 0
    have = _passage_digests(sid)
    assert {d: have.get(d) for d in want} == want                   # each at its CURRENT digest
    rep = json.loads((Path(third.run_dir) / "report.json").read_text(encoding="utf-8"))
    assert rep["counts"]["passages_backfilled"] == len(built) - 1
    fourth = mi.run_ingest(sid, store=store, passages=True)         # nothing left to back-fill
    idx = json.loads((Path(fourth.run_dir) / "index.json").read_text(encoding="utf-8"))
    assert idx["backfill"]["indexed"] == 0 and idx["backfill"]["candidates"] == 0
