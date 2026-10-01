"""FXI1 — the runner track's fixes for Codex's FIRST review of mcp_ingest (f1-mi-runner, 12 findings).

Each test names the finding it proves. Expectations are derived from the corpus the fixture writes
(`conftest.write_corpus`) and from the run's own artefacts, never typed in.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture, ledger, paths, plans, records, runner
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.records import DriftHolds

from conftest import fs_connection

AID = "ad_fxi1test000001"


@pytest.fixture
def store(walnut_env):
    from databridge_graph.store import GraphStore

    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


@pytest.fixture(autouse=True)
def _no_pool(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))


def _source(corpus, sid: str = "docs") -> str:
    root, _ = corpus
    mi.register_connection(fs_connection(sid, root))
    mi.propose_plan(sid)
    return sid


def _crash(run_dir: Path, *unfinished: str) -> None:
    """As if the process died before these stages were marked done."""
    p = run_dir / "stages.json"
    stages = json.loads(p.read_text(encoding="utf-8"))
    for s in (*unfinished, "verify", "report"):
        stages.pop(s, None)
    p.write_text(json.dumps(stages), encoding="utf-8")
    (run_dir / "report.json").unlink(missing_ok=True)


def _events(walnut_env) -> list[dict]:
    return read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))


# --- #1 approval --------------------------------------------------------------------------------
def test_f1_01_a_plan_whose_approval_does_not_verify_never_runs(walnut_env, corpus, common_vocab,
                                                                 store, monkeypatch):
    sid = _source(corpus)
    seen = []

    def refuse(plan, root=None):
        seen.append(plan.source_id)
        raise plans.PlanError("no authenticated approval bound to this plan's digest")

    monkeypatch.setattr(plans, "verify_plan_approval", refuse, raising=False)
    with pytest.raises(runner.PlanNotApproved, match="authenticated principal"):
        mi.run_ingest(sid, store=store)
    assert seen == [sid]
    assert not runner.list_runs(sid)                 # refused before a run directory exists


def test_f1_01_without_the_verifier_nothing_runs(walnut_env, corpus, common_vocab, store,
                                                 monkeypatch):
    # Codex f2 #1 superseded the f1 behaviour ("run, and note the gap"): a missing verifier now
    # FAILS CLOSED - see test_fxj1_runner.py::test_f2_01_*
    sid = _source(corpus)
    monkeypatch.delattr(plans, "verify_plan_approval", raising=False)
    with pytest.raises(runner.PlanNotApproved, match="cannot be verified"):
        mi.run_ingest(sid, store=store)
    assert not runner.list_runs(sid)
    monkeypatch.setattr(plans, "verify_plan_approval", lambda plan, root=None: {"ok": 1},
                        raising=False)
    assert mi.run_ingest(sid, store=store).status == "ok"


# --- #2 resume binding --------------------------------------------------------------------------
def test_f1_02_a_resume_under_a_new_drift_hold_is_refused_and_the_run_left_as_it_was(
        walnut_env, corpus, common_vocab, store):
    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)
    rd = Path(first.run_dir)
    _crash(rd, "screen", "build", "index")
    snapshot_before = (rd / "plan.snapshot.yaml").read_bytes()
    ledgers_before = {p.name: p.read_bytes() for p in rd.glob("*.jsonl")}
    records.save_holds(DriftHolds(source_id=sid, paths={"projects/**": AID}))
    with pytest.raises(runner.ResumeRefused, match="holds"):
        mi.run_ingest(sid, store=store, run_id=first.run_id)
    assert (rd / "plan.snapshot.yaml").read_bytes() == snapshot_before
    assert {p.name: p.read_bytes() for p in rd.glob("*.jsonl")} == ledgers_before
    assert not (rd / "attempts").exists()


def test_f1_02_a_resume_under_new_screen_overrides_is_refused(walnut_env, corpus, common_vocab,
                                                              store, monkeypatch):
    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)
    _crash(Path(first.run_dir), "build", "index")
    some = next(r["item_id"] for r in read_jsonl(Path(first.run_dir) / "captured.jsonl"))
    monkeypatch.setattr(runner, "load_overrides", lambda *a, **k: {some})
    with pytest.raises(runner.ResumeRefused, match="overrides"):
        mi.run_ingest(sid, store=store, run_id=first.run_id)


# --- #3 never truncate --------------------------------------------------------------------------
def test_f1_03_a_resumed_stage_moves_its_old_ledgers_aside_intact(walnut_env, corpus,
                                                                 common_vocab, store):
    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)
    rd = Path(first.run_dir)
    before = {n: (rd / f"{n}.jsonl").read_bytes()
              for n in ("extracted", "extract_skipped", "unchanged", "governance")}
    assert before["extracted"] and before["governance"]           # non-vacuous
    _crash(rd, "extract", "screen", "build", "index")
    again = mi.run_ingest(sid, store=store, run_id=first.run_id)
    assert again.verify["ok"], again.verify["problems"]
    for name, data in before.items():
        moved = rd / "attempts" / "1" / f"{name}.jsonl"
        # an EMPTY ledger holds nothing to keep; every non-empty one is kept byte for byte
        assert (moved.read_bytes() == data) if data else not moved.exists()


def test_f1_03_carried_decisions_survive_a_screen_only_resume(walnut_env, corpus, common_vocab,
                                                              store):
    sid = _source(corpus)
    mi.run_ingest(sid, store=store)
    second = mi.run_ingest(sid, store=store)          # blocked/review items are carried forward
    rd = Path(second.run_dir)
    carried = [r for r in read_jsonl(rd / "governance.jsonl") if r.get("carried_forward")]
    assert carried, "the corpus has a screened-out item, so something is carried"
    _crash(rd, "screen", "build", "index")
    again = mi.run_ingest(sid, store=store, run_id=second.run_id)
    assert again.verify["ok"], again.verify["problems"]
    assert [r["item_id"] for r in read_jsonl(rd / "governance.jsonl")
            if r.get("carried_forward")] == [r["item_id"] for r in carried]


# --- #4 path traversal ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["..", "../x", "..\\..\\target", "a/b", "C:evil", "", ".hidden.",
                                 "x..y"])
def test_f1_04_ids_that_would_leave_their_directory_are_refused(walnut_env, bad):
    with pytest.raises(paths.UnsafePathSegment):
        runner.run_dir_for("docs", bad)
    with pytest.raises(paths.UnsafePathSegment):
        runner.source_dir(bad)
    with pytest.raises(paths.UnsafePathSegment):
        runner.verify_run("docs", bad)
    with pytest.raises(paths.UnsafePathSegment):
        records.list_run_dirs(bad)


def test_f1_04_a_traversing_run_id_writes_nothing_outside(walnut_env, corpus, common_vocab,
                                                          store, tmp_path):
    sid = _source(corpus)
    with pytest.raises(paths.UnsafePathSegment):
        mi.run_ingest(sid, store=store, run_id="..\\..\\escaped")
    assert not list(tmp_path.rglob("escaped*"))
    assert runner.run_dir_for(sid, "20261001T000000Z-abc123").parent == runner.source_dir(sid)


# --- #5 secrets ---------------------------------------------------------------------------------
SECRET = "hunter2-Zq9"


def test_f1_05_scrub_masks_credentials_and_keeps_ordinary_words():
    for text in (f"postgresql://reader:{SECRET}@db:5432/x", f"FATAL: password={SECRET} host=db",
                 f"Authorization: Bearer {SECRET}abcdef", f'{{"api_key": "{SECRET}"}}',
                 f"PGPASSWORD={SECRET}", f"token: {SECRET}; retry"):
        assert SECRET not in ledger.scrub(text), text
    assert ledger.scrub("passwords are required") == "passwords are required"


def test_f1_05_fetch_errors_run_errors_and_event_attrs_never_persist_a_secret(
        walnut_env, corpus, common_vocab, store, monkeypatch):
    sid = _source(corpus)

    def leaky_fetch(*a, **k):
        raise RuntimeError(f"server said: auth failed for postgresql://u:{SECRET}@h/db "
                           f"(password={SECRET})")

    monkeypatch.setattr(runner, "fetch_document", leaky_fetch)
    report = mi.run_ingest(sid, store=store, event_attrs={"note": f"token={SECRET}"})
    rd = Path(report.run_dir)
    failed = read_jsonl(rd / "failed.jsonl")
    assert failed                                       # non-vacuous: fetches failed
    for p in [*rd.glob("*.json*"), Path(walnut_env["WALNUT_EVENTS_PATH"])]:
        assert SECRET not in p.read_text(encoding="utf-8"), p.name

    def boom(*a, **k):
        raise RuntimeError(f"connect: api_key={SECRET}")

    monkeypatch.setattr(runner, "_collect", boom)
    with pytest.raises(RuntimeError):
        mi.run_ingest(sid, store=store)
    for p in [*walnut_env["WALNUT_INGEST_DIR"].rglob("report.json"),
              Path(walnut_env["WALNUT_EVENTS_PATH"])]:
        assert SECRET not in p.read_text(encoding="utf-8"), p


# --- #6 crash-safe build, events, lock ------------------------------------------------------------
def test_f1_06_a_crash_after_the_build_before_save_state_loses_neither_state_nor_events(
        walnut_env, corpus, common_vocab, store):
    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)
    rd = Path(first.run_dir)
    built = {r["item_id"]: r["sha256"] for r in read_jsonl(rd / "build.jsonl")}
    assert built
    # the crash: graph + build.jsonl written; state.json never saved; no events emitted
    runner.state_path(sid).write_text("{}", encoding="utf-8")
    ev = Path(walnut_env["WALNUT_EVENTS_PATH"])
    ev.write_text("".join(json.dumps(e) + "\n" for e in _events(walnut_env)
                          if e["run_id"] != first.run_id), encoding="utf-8")
    _crash(rd, "build", "index")
    again = mi.run_ingest(sid, store=store, run_id=first.run_id)
    assert again.verify["ok"], again.verify["problems"]
    state = runner.load_state(sid)
    assert {i: state[i]["content_hash"] for i in built} == built
    added = [e for e in _events(walnut_env) if e["run_id"] == first.run_id
             and e["type"] == "item_added"]
    assert sorted(e["item_id"] for e in added) == sorted(built)


def test_f1_06_a_resume_after_the_events_were_emitted_does_not_double_them(
        walnut_env, corpus, common_vocab, store):
    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)
    before = [e for e in _events(walnut_env) if e["run_id"] == first.run_id
              and e["type"].startswith("item_")]
    assert before
    _crash(Path(first.run_dir), "build", "index")
    mi.run_ingest(sid, store=store, run_id=first.run_id)
    after = [e for e in _events(walnut_env) if e["run_id"] == first.run_id
             and e["type"].startswith("item_")]
    assert sorted(e["event_id"] for e in after) == sorted(e["event_id"] for e in before)


def test_f1_06_two_runs_of_one_source_never_overlap(walnut_env, corpus, common_vocab, store):
    sid = _source(corpus)
    with runner._source_lock(sid):
        with pytest.raises(runner.RunInProgress):
            mi.run_ingest(sid, store=store)
        errs = []
        t = threading.Thread(target=lambda: errs.append(
            pytest.raises(runner.RunInProgress, runner._source_lock(sid).__enter__)))
        t.start()
        t.join()
        assert errs
    assert mi.run_ingest(sid, store=store).status == "ok"    # released


def test_f1_06_temp_files_are_unique_per_write(tmp_path):
    names = set()
    orig = capture.os.replace

    def spy(src, dst):
        names.add(Path(src).name)
        return orig(src, dst)

    capture.os.replace = spy
    try:
        for i in range(3):
            capture._atomic_write(tmp_path / "f.txt", str(i).encode())
    finally:
        capture.os.replace = orig
    assert len(names) == 3


# --- #7 meta_digest end to end -------------------------------------------------------------------
def test_f1_07_metadata_tampering_is_caught_by_verify_and_the_anchor_reaches_build(
        walnut_env, corpus, common_vocab, store):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store)
    rd = Path(report.run_dir)
    assert report.verify["ok"], report.verify["problems"]
    cap = {r["item_id"]: r for r in read_jsonl(rd / "captured.jsonl")}
    docs = [r for r in read_jsonl(rd / "build.jsonl") if r.get("kind") == "document"]
    assert docs and all(r["meta_digest"] == cap[r["item_id"]]["meta_digest"] for r in docs)
    victim = docs[0]["sha256"]
    mp = capture.meta_path(victim)
    meta = json.loads(mp.read_text(encoding="utf-8"))
    meta["title"] = "swapped"
    meta.pop("meta_digest")                              # stripped, so the file alone can't tell
    mp.write_text(json.dumps(meta), encoding="utf-8")
    v = runner.verify_run(sid, report.run_id, store=store)
    assert not v["ok"] and any("metadata altered" in p for p in v["problems"])


def test_f1_07_a_captured_line_without_its_anchor_is_a_problem(walnut_env, corpus, common_vocab,
                                                               store):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store)
    rd = Path(report.run_dir)
    lines = read_jsonl(rd / "captured.jsonl")
    lines[0].pop("meta_digest")
    (rd / "captured.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines),
                                       encoding="utf-8")
    v = runner.verify_run(sid, report.run_id)
    assert any("no meta_digest anchor" in p for p in v["problems"])


def test_f1_07_a_legacy_metadata_file_gains_its_digest(walnut_env):
    sha = capture.put_bytes(b"legacy bytes")
    mp = capture.meta_path(sha)
    legacy = {"source": "s", "uri": "u", "mime": None, "title": "t", "retrieved_at": "x"}
    mp.write_text(json.dumps(legacy), encoding="utf-8")
    capture.put_meta(sha, legacy)
    assert capture.stored_meta_digest(sha) == capture.meta_digest(sha, legacy)


# --- #8 masked text keyed by rule set ----------------------------------------------------------------
def test_f1_08_masked_views_are_per_rule_set_write_once_and_digest_checked(walnut_env):
    sha = capture.put_bytes(b"x")
    a, b = capture.redaction_variant("fp1", False), capture.redaction_variant("fp1", True)
    assert a != b and a != capture.redaction_variant("fp2", False)
    _, da = capture.put_redacted_variant(sha, a, "masked [EMAIL]")
    _, db = capture.put_redacted_variant(sha, b, "masked me@x.example")
    assert capture.get_redacted_variant(sha, a, expect_digest=da) == "masked [EMAIL]"
    with pytest.raises(capture.CaptureCorrupt):
        capture.put_redacted_variant(sha, a, "something else")
    with pytest.raises(capture.CaptureCorrupt):
        capture.get_redacted_variant(sha, a, expect_digest=db)


def test_f1_08_the_build_uses_its_own_decision_s_masked_text_not_the_shared_copy(
        walnut_env, corpus, common_vocab, store, fake_harness):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store, harness=fake_harness)
    rd = Path(report.run_dir)
    from databridge_mcp_ingest.screen import load_screen
    cfg = load_screen()
    decided = [r for r in read_jsonl(rd / "governance.jsonl")
               if r.get("decision") == "allow" and r.get("masked_sha256")]
    assert decided
    for r in decided:
        raw = capture.get_text(r["sha256"])
        masked = runner._masked_for_build({"sha256": r["sha256"]}, raw, r, cfg, False)
        assert capture.sha256_bytes(masked.encode()) == r["masked_sha256"]
        # another rule set overwrote the SHARED copy with unmasked text: not read
        capture.put_redacted(r["sha256"], raw)
        assert runner._masked_for_build({"sha256": r["sha256"]}, raw, r, cfg, False) == masked
    # a decision whose masked digest nothing reproduces is never built
    bad = {**decided[0], "masked_sha256": "0" * 64}
    raw = capture.get_text(bad["sha256"])
    assert runner._masked_for_build({"sha256": bad["sha256"]}, raw, bad, cfg, False) is None


# --- #9 / #10 pointer verification ---------------------------------------------------------------
def test_f1_09_derived_pointers_resolve_through_their_basis(walnut_env):
    sha = capture.put_bytes(json.dumps({"table": "t", "primary_key": ["id"],
                                        "rows": [{"id": 1, "name": "A"}]}).encode())
    res = runner._PointerResolver()
    cell = {"kind": "table_cell", "sha": sha, "row_id": "1", "column": "name"}
    bad = {**cell, "column": "nope"}
    assert res.resolves(cell) and not res.resolves(bad)
    assert res.resolves({"kind": "derived", "basis": [cell]})
    assert not res.resolves({"kind": "derived", "basis": []})
    assert not res.resolves({"kind": "derived", "basis": [cell, bad]})
    assert not res.resolves({"kind": "mystery", "sha": sha})


def test_f1_10_graph_rows_are_checked_against_the_store_not_only_the_ledger(
        walnut_env, corpus, common_vocab, store, fake_harness):
    sid = _source(corpus)
    report = mi.run_ingest(sid, store=store, harness=fake_harness)
    rd = Path(report.run_dir)
    sp = report.verify["counts"]["store_provenance"]
    assert sp["pointers"] > 0 and sp["unresolved"] == 0           # non-vacuous
    # the ledger loses every pointer: 0 of 0 "resolving" is not a pass
    (rd / "pointers.jsonl").write_text("", encoding="utf-8")
    v = runner.verify_run(sid, report.run_id, store=store)
    assert any("wrote graph rows but no pointers" in p for p in v["problems"])
    # the captured text under the graph's pointers changes: the STORE check catches it
    shas = sorted({r["sha256"] for r in read_jsonl(rd / "build.jsonl")
                   if r.get("kind") == "document"})
    for sha in shas:
        capture.text_path(sha).write_text("", encoding="utf-8")
    out = runner._verify_store_provenance(store, shas, runner._PointerResolver())
    assert out["unresolved"] > 0


# --- #11 absent path holds -------------------------------------------------------------------------
def test_f1_11_absent_hold_matches_the_relative_path_the_hold_was_written_against():
    h = DriftHolds(source_id="docs", paths={"projects/**": AID})
    uri = "file:///C:/work/corpus/projects/alpha/notes.txt"
    assert h.item_hold("document", uri, "projects/alpha/notes.txt") == AID
    assert h.absent_hold(uri, "projects/alpha/notes.txt") == AID
    assert h.absent_hold(uri) == AID                      # legacy state: suffix fallback
    assert h.absent_hold("file:///C:/work/corpus/docs/o.md", "docs/o.md") is None


def test_f1_11_a_held_path_that_disappears_is_not_inferred_removed(walnut_env, corpus,
                                                                   common_vocab, store):
    root, outcomes = corpus
    sid = _source(corpus)
    first = mi.run_ingest(sid, store=store)
    state = runner.load_state(sid)
    rel_of = {r["item_id"]: r["rel"] for r in read_jsonl(Path(first.run_dir) / "inventory.jsonl")}
    gone_rel = next(rel for rel, o in sorted(outcomes.items())
                    if o == "collect" and rel.startswith("projects/")
                    and any(rel_of.get(i) == rel for i in state))
    assert all(v.get("rel") == rel_of[i] for i, v in state.items())   # state records rel
    (root / gone_rel).unlink()
    records.save_holds(DriftHolds(source_id=sid, paths={"projects/**": AID}))
    second = mi.run_ingest(sid, store=store)
    assert not read_jsonl(Path(second.run_dir) / "removed.jsonl")
    assert any("not inferred removed" in n for n in second.notes)


# --- #12 a retried fetch ----------------------------------------------------------------------------
def test_f1_12_a_fetch_that_failed_then_succeeded_on_resume_verifies(walnut_env, corpus,
                                                                   common_vocab, store,
                                                                   monkeypatch):
    sid = _source(corpus)
    real = runner.fetch_document
    failed_once: list[str] = []

    def flaky(client, step, item):
        if not failed_once:
            failed_once.append(item.item_id)
            raise RuntimeError("transient")
        return real(client, step, item)

    monkeypatch.setattr(runner, "fetch_document", flaky)
    first = mi.run_ingest(sid, store=store)
    rd = Path(first.run_dir)
    assert [r["item_id"] for r in read_jsonl(rd / "failed.jsonl")] == failed_once
    monkeypatch.setattr(runner, "fetch_document", real)
    _crash(rd, "collect", "extract", "screen", "build", "index")
    again = mi.run_ingest(sid, store=store, run_id=first.run_id)
    assert again.verify["ok"], again.verify["problems"]
    assert again.verify["counts"]["failed_retried"] == 1
    assert failed_once[0] in {r["item_id"] for r in read_jsonl(rd / "captured.jsonl")}
    assert read_jsonl(rd / "failed.jsonl")                  # the failure stays as history
