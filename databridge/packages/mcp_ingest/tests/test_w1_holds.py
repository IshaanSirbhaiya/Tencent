"""W1 — the runner honours adapter-agent drift holds per TABLE, PATH and STEP (AA review item 1).

A real `run_ingest` over the stdio fixture server (files, and SQLite dressed as Postgres). The
holds file is written through `records.save_holds`, exactly as an adapter agent's service writes
it. Every expectation is derived from the run's own artefacts (which tables the unheld run built,
which items the corpus holds), never typed in.

* a held table is captured (inventoried, so nothing is inferred removed) but NOT built, recorded
  with rule DRIFT_PENDING_APPROVAL and its reversal; every OTHER table still builds; its state is
  not advanced, so once released the next sync builds it;
* a held path glob does the same for documents;
* a held step is not enumerated, the listing is marked incomplete, and nothing is inferred removed;
* a plan re-approved through `approve_plan` that changes the held step resolves the hold — the
  same plan re-approved does not.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import records, runner
from databridge_mcp_ingest.records import DRIFT_PENDING_APPROVAL, DriftHolds

from conftest import FIXTURE_SERVER, fs_connection

AID = "ad_w1test0000001"


def _jl(run_dir: Path, name: str) -> list[dict]:
    p = Path(run_dir) / f"{name}.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if p.is_file() else []


def _allowed(run_dir) -> set[str]:
    """Items the pre-model screen allowed this run (the ones a build is owed)."""
    return {r["item_id"] for r in _jl(run_dir, "governance") if r["decision"] == "allow"}


def _hold(source_id: str, **buckets) -> None:
    records.save_holds(DriftHolds(source_id=source_id, **buckets))


@pytest.fixture
def store(walnut_env):
    from databridge_graph.store import GraphStore

    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


@pytest.fixture(autouse=True)
def _no_pool(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")        # every run sees the source as it is NOW
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))


def test_the_flag_is_set_and_the_adapter_service_sees_it():
    assert runner.HONOURS_DRIFT_HOLDS is True


# --- tables ------------------------------------------------------------------------------------
def test_a_held_table_is_captured_not_built_and_the_other_tables_still_build(
        walnut_env, fixture_db, common_vocab, store):
    db, _ = fixture_db
    mi.register_connection({"id": "db", "title": "db", "auto_approve": True,
                            "transport": {"type": "stdio", "command": sys.executable,
                                          "args": [str(FIXTURE_SERVER), "--db", str(db)]}})
    assert mi.propose_plan("db").status == "approved"
    first = mi.run_ingest("db", store=store, tabular=False)
    built = sorted(r["uri"] for r in _jl(first.run_dir, "build") if r.get("kind") == "table")
    assert len(built) >= 2, f"the fixture must build at least two tables to prove this: {built}"
    held_ref, others = built[0], built[1:]

    # every table changes, and one of them is held
    with sqlite3.connect(db) as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
            c.execute(f'INSERT INTO "{t}" ("{cols[0]}") VALUES (900)')
    _hold("db", tables={held_ref: AID})
    second = mi.run_ingest("db", store=store, tabular=False)
    rd = Path(second.run_dir)
    assert second.verify["ok"], second.verify["problems"]

    inv = {r["uri"]: r for r in _jl(rd, "inventory")}
    captured = {r["uri"] for r in _jl(rd, "captured")}
    assert held_ref in inv and held_ref in captured               # captured: still observable
    [h] = _jl(rd, runner.HELD_LEDGER)
    assert (h["uri"], h["rule"], h["adaptation_id"]) == (held_ref, DRIFT_PENDING_APPROVAL, AID)
    assert AID in h["reverse"] and "approve" in h["reverse"]
    rebuilt = {r["uri"] for r in _jl(rd, "build") if r.get("kind") == "table"}
    assert held_ref not in rebuilt
    assert set(others) <= rebuilt, "an unheld table must still build"
    skipped = {r["uri"]: r for r in _jl(rd, "build_skipped")}
    assert skipped[held_ref]["reason"] == DRIFT_PENDING_APPROVAL
    assert AID in skipped[held_ref]["reverse"]
    # the typed readers see the hold, counted by rule (P3)
    arts = records.read_run(rd, strict=True)
    assert arts.exclusions_by_rule()["excluded"].get(DRIFT_PENDING_APPROVAL) == 1
    assert second.counts["held"] == 1 and any(DRIFT_PENDING_APPROVAL in n for n in second.notes)
    # nothing was inferred removed
    assert _jl(rd, "removed") == []
    held_iid = inv[held_ref]["item_id"]
    assert runner.load_state("db")[held_iid]["run_id"] == Path(first.run_dir).name

    # released: the next sync builds it (the state was not advanced, so it is not "unchanged")
    _hold("db")
    third = mi.run_ingest("db", store=store, tabular=False)
    assert held_ref in {r["uri"] for r in _jl(third.run_dir, "build") if r.get("kind") == "table"}
    assert _jl(third.run_dir, runner.HELD_LEDGER) == []


def test_a_held_table_the_source_dropped_is_not_inferred_removed(
        walnut_env, fixture_db, common_vocab, store):
    db, _ = fixture_db
    mi.register_connection({"id": "db3", "title": "db3", "auto_approve": True,
                            "transport": {"type": "stdio", "command": sys.executable,
                                          "args": [str(FIXTURE_SERVER), "--db", str(db)]}})
    mi.propose_plan("db3")
    first = mi.run_ingest("db3", store=store, tabular=False)
    built = sorted(r["uri"] for r in _jl(first.run_dir, "build") if r.get("kind") == "table")
    assert len(built) >= 2
    held_ref, dropped_ref = built[0], built[1]
    with sqlite3.connect(db) as c:
        for ref in (held_ref, dropped_ref):
            c.execute(f'DROP TABLE "{ref.split(".")[-1]}"')
    _hold("db3", tables={held_ref: AID})
    report = mi.run_ingest("db3", store=store, tabular=False)
    removed = {r["uri"] for r in _jl(report.run_dir, "removed")}
    assert removed == {dropped_ref}, "only the UNHELD dropped table is closed"
    assert any("not inferred removed" in n for n in report.notes)
    assert held_ref in {v["uri"] for v in runner.load_state("db3").values()}


def test_event_attrs_and_holds_survive_the_nested_tabular_recipe(
        walnut_env, fixture_db, common_vocab, store):
    """The database recipe runs the core run nested inside itself: `event_attrs` must reach the
    run_completed event through that path too, and a hold is honoured there as well."""
    db, _ = fixture_db
    mi.register_connection({"id": "db2", "title": "db2", "auto_approve": True,
                            "transport": {"type": "stdio", "command": sys.executable,
                                          "args": [str(FIXTURE_SERVER), "--db", str(db)]}})
    mi.propose_plan("db2")
    first = mi.run_ingest("db2", store=store)
    tables = sorted(r["uri"] for r in _jl(first.run_dir, "build") if r.get("kind") == "table")
    assert tables
    schema = tables[0].rsplit(".", 1)[0] if "." in tables[0] else ""
    new_ref = f"{schema}.w1_contracts" if schema else "w1_contracts"
    _hold("db2", tables={tables[0]: AID, new_ref: AID})
    with sqlite3.connect(db) as c:
        t = tables[0].split(".")[-1]
        col = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')][0]
        c.execute(f'INSERT INTO "{t}" ("{col}") VALUES (901)')
        # a NEW table that arrives held: the recipe must not profile, map or approve it
        c.executescript("CREATE TABLE w1_contracts (id INTEGER PRIMARY KEY, title TEXT);"
                        "INSERT INTO w1_contracts VALUES (1, 'Freight MSA');")
    report = mi.run_ingest("db2", store=store, event_attrs={"shape_changed": {"drifts": 1}})
    assert any(n.startswith("tabular:") for n in report.notes)
    events = [json.loads(x) for x in Path(walnut_env["WALNUT_EVENTS_PATH"])
              .read_text(encoding="utf-8").splitlines()]
    [done] = [e for e in events if e["run_id"] == report.run_id and e["type"] == "run_completed"]
    assert done["attrs"]["shape_changed"] == {"drifts": 1}
    assert sorted(r["uri"] for r in _jl(report.run_dir, runner.HELD_LEDGER)) == \
        sorted([tables[0], new_ref])
    assert not {tables[0], new_ref} & {r["uri"] for r in _jl(report.run_dir, "build")}
    assert new_ref not in mi.load_mapping("db2").tables, "a held new table is never mapped"


def test_a_holds_file_cannot_release_a_hold_by_itself(walnut_env, corpus, common_vocab, store):
    """Only the adapter agent, with an authenticated principal, lifts a hold (R-019): the holds
    file has no "resolved" entry, and one typed into it fails the run LOUDLY (never "no holds")."""
    import yaml

    root, _ = corpus
    mi.register_connection(fs_connection("docs", root))
    mi.propose_plan("docs")
    path = records.holds_path("docs")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"source_id": "docs", "paths": {"**": AID},
                                    "resolved": {AID: {"by": "someone"}}}), encoding="utf-8")
    with pytest.raises(Exception, match="resolved"):
        mi.run_ingest("docs", store=store)
    [failed] = [r for r in runner.list_runs("docs")]
    assert failed["status"] == "failed"


# --- paths -------------------------------------------------------------------------------------
def test_a_held_path_is_captured_not_built(walnut_env, corpus, common_vocab, store):
    root, outcomes = corpus
    mi.register_connection(fs_connection("docs", root))
    mi.propose_plan("docs")
    collect = sorted(rel for rel, o in outcomes.items() if o == "collect")
    held = sorted(rel for rel in collect if rel.startswith("projects/"))
    assert held and set(collect) - set(held)
    _hold("docs", paths={"projects/**": AID})
    report = mi.run_ingest("docs", store=store)
    rd = Path(report.run_dir)
    assert report.verify["ok"], report.verify["problems"]
    rel_of = {r["item_id"]: r["rel"] for r in _jl(rd, "inventory")}
    assert sorted(rel_of[r["item_id"]] for r in _jl(rd, "captured")) == collect
    assert sorted(r["rel"] for r in _jl(rd, runner.HELD_LEDGER)) == held
    # built: exactly the items the screen allowed, minus the held ones (non-vacuous: some are)
    allowed = sorted(rel_of[i] for i in _allowed(rd))
    expect_built = sorted(set(allowed) - set(held))
    assert expect_built and set(held) <= set(allowed)
    assert sorted(rel_of[r["item_id"]] for r in _jl(rd, "build")) == expect_built
    assert sorted(rel_of[r["item_id"]] for r in _jl(rd, "build_skipped")
                  if r["reason"] == DRIFT_PENDING_APPROVAL) == held


# --- steps -------------------------------------------------------------------------------------
def test_a_held_step_is_not_enumerated_and_nothing_is_inferred_removed(
        walnut_env, corpus, common_vocab, store):
    root, _ = corpus
    mi.register_connection(fs_connection("docs", root))
    plan = mi.propose_plan("docs")
    first = mi.run_ingest("docs", store=store)
    state_before = runner.load_state("docs")
    assert state_before
    _hold("docs", steps={s.id: AID for s in plan.steps})
    second = mi.run_ingest("docs", store=store)
    rd = Path(second.run_dir)
    failed = _jl(rd, "failed")
    assert sorted(f["step"] for f in failed) == sorted(s.id for s in plan.steps)
    assert all(f["rule"] == DRIFT_PENDING_APPROVAL and AID in f["reverse"] for f in failed)
    assert _jl(rd, "inventory") == [] and _jl(rd, "removed") == []
    assert runner.load_state("docs") == state_before              # nothing closed or forgotten
    assert "HELD" in second.message and second.status == "empty"
    assert any("removals were not inferred" in n for n in second.notes)
    assert Path(first.run_dir) != rd


def test_approve_plan_hands_a_replanned_hold_to_the_adapter_agents_with_the_principal(
        walnut_env, corpus, common_vocab, store):
    from databridge_mcp_ingest import plans
    from databridge_mcp_ingest.plans import approve_plan, load_plan, save_plan

    root, _ = corpus
    mi.register_connection(fs_connection("docs", root))
    plan = mi.propose_plan("docs")
    mi.run_ingest("docs", store=store)                  # the plan in force when drift is seen
    step = plan.steps[0]
    _hold("docs", steps={step.id: AID})
    calls: list = []
    remove = plans.add_plan_approval_listener(lambda sid, matching, who: calls.append(
        (sid, matching, who)) or {"decided": sorted(matching)})
    from conftest import test_principal
    from databridge_mcp_ingest.plans import ApprovalRefused

    principal = test_principal("dana")                  # R-022 f1 #1: an ISSUED principal
    try:
        # the SAME plan re-approved: nothing about the held step was reconsidered
        save_plan(load_plan("docs").model_copy(update={"status": "proposed"}))
        approve_plan("docs", principal=principal)
        assert calls == []
        # a person re-plans the step (re-declared under a new id) ...
        changed = load_plan("docs")
        changed = changed.model_copy(update={
            "status": "proposed",
            "steps": [s.model_copy(update={"id": s.id + "-v2"}) if s.id == step.id else s
                      for s in changed.steps]})
        save_plan(changed)
        assert plans.matching_step_holds(changed.model_copy(update={"status": "approved"}))             == {AID: [step.id]}
        # ... "approved" by a typed NAME only: refused outright (FXI3), so it lifts nothing
        with pytest.raises(ApprovalRefused):
            approve_plan("docs", approved_by="dana")
        assert calls == []
        # ... approved with the authenticated principal: the agents are asked to decide
        save_plan(changed)
        approve_plan("docs", approved_by="dana", principal=principal)   # the name matches
        assert calls == [("docs", [step.id], principal)]
    finally:
        remove()
    # nothing here edited the holds file: only the agent's decision releases it
    assert records.load_holds("docs").step_hold(step.id) == AID
