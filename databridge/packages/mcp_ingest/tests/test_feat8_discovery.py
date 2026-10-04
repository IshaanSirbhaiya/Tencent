"""FEAT8 / Codex R-027 AAX f1 #1: a CAPTURE-ONLY discovery, enforced by ingest.

A source set up through its adapter agent is first DISCOVERED: collected, extracted, screened and
its table mapping proposed - and nothing is built, for tables and documents alike, until a person
approves the mapping and hands the source off. The plain run (the Sources page's) is unchanged.

Expectations are derived from the run's own ledgers and the mapper's own output, never typed in.
Estates: the conftest corpus + database (documents AND tables), and the workspace and finance
estates (tables).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import build, runner
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection
from conftest import test_principal as _principal

SID = "feat8-disc"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))


def _approve(sid: str, client=None) -> None:
    plan = mi.propose_plan(sid, **({"client": client} if client is not None else {}))
    unknown = [t for t, c in plan.tool_classes.items() if c.startswith("unclassified")]
    mi.approve_plan(sid, principal=_principal("Dana"), vouch_read_tools=unknown,
                    **({"client": client} if client is not None else {}))


def _events(walnut_env) -> list[dict]:
    p = Path(walnut_env["WALNUT_EVENTS_PATH"])
    return read_jsonl(p) if p.is_file() else []


def _node_count(store) -> int:
    return int(store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])


def _check_discovery(rep, sid: str, *, docs_expected: bool) -> dict:
    """What every discovery must show, read from its own artefacts."""
    from databridge_mcp_ingest.mapping import load_mapping, mapping_digest
    from databridge_mcp_ingest.plans import load_plan

    run_dir = Path(rep.run_dir)
    assert rep.status == runner.DISCOVERED, (rep.status, rep.message)
    assert rep.verify["ok"], rep.verify["problems"]
    captured = read_jsonl(run_dir / "captured.jsonl")
    tables = [r for r in captured if r.get("kind") == "table"]
    documents = [r for r in captured if r.get("kind") != "table"]
    assert tables and (documents if docs_expected else True)             # non-vacuous
    # nothing built: no build line, every allowed item recorded as discovery-only
    assert read_jsonl(run_dir / "build.jsonl") == []
    allowed = {r["item_id"] for r in read_jsonl(run_dir / "governance.jsonl")
               if r.get("decision") == "allow"}
    skipped = {r["item_id"] for r in read_jsonl(run_dir / "build_skipped.jsonl")
               if r.get("reason") == runner.DISCOVERY_ONLY}
    assert allowed and skipped == allowed
    assert not (run_dir / "index.json").exists() and not (run_dir / "identity_consolidation.json").exists()
    # the source's incremental state was not advanced
    assert runner.load_state(sid) == {}
    # the manifest: every captured table with ITS capture digest, and the mapping proposed from it
    man = json.loads((run_dir / runner.DISCOVERY_FILE).read_text(encoding="utf-8"))
    by_sha = {r["sha256"]: r["item_id"] for r in tables}
    assert {t["sha256"] for t in man["tables"].values()} == set(by_sha)
    mapping = load_mapping(sid)
    proposed = [ref for ref, t in man["tables"].items() if "mapping_digest" in t]
    assert proposed                                                       # non-vacuous
    for ref in proposed:
        assert man["tables"][ref]["mapping_digest"] == mapping_digest(mapping.tables[ref])
        assert mapping.tables[ref].status in ("proposed", "unmapped")    # never approved here
    assert man["documents"]["captured"] == len(documents)
    latest = runner.latest_discovery(sid)
    assert latest["run_id"] == rep.run_id and latest["status"] == runner.DISCOVERED
    assert latest["plan_digest"] == runner.plan_digest(load_plan(sid))
    return man


@pytest.mark.real_approval
def test_discovery_captures_screens_and_proposes_but_builds_no_table_and_no_document(
        walnut_env, corpus, fixture_db, common_vocab, fake_harness):
    from databridge_graph.store import GraphStore

    root, _ = corpus
    db, _ = fixture_db
    mi.register_connection({**fs_connection(SID, root, db=db), "auto_approve": False})
    _approve(SID)
    gpath = Path(walnut_env["WALNUT_GRAPH_DB_PATH"])
    gpath.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(str(gpath))
    before_events = len(_events(walnut_env))
    rep = mi.run_ingest(SID, store=store, harness=fake_harness, capture_only=True)
    _check_discovery(rep, SID, docs_expected=True)
    assert _node_count(store) == 0                         # the store it was HANDED is untouched
    sent = [m["content"] for call in fake_harness.calls for m in call]
    assert not any(str(c).startswith("DOCUMENT TEXT:") for c in sent)   # no document extraction
    assert len(_events(walnut_env)) == before_events       # no item or run event
    assert rep.plan.get("mode") == runner.DISCOVERY_MODE
    # health and freshness never read a discovery as a sync
    assert [r["status"] for r in mi.list_runs(SID)] == [runner.DISCOVERED]

    # the plain run is unchanged: it builds the documents (tables wait for a person's approval)
    rep2 = mi.run_ingest(SID, store=store, harness=fake_harness)
    assert rep2.status in ("ok", "unverified"), (rep2.status, rep2.message)
    built = read_jsonl(Path(rep2.run_dir) / "build.jsonl")
    assert any(r.get("kind") == "document" for r in built) and _node_count(store) > 0
    assert any(e.get("graph") is not False for e in runner.load_state(SID).values())


@pytest.mark.real_approval
@pytest.mark.parametrize("estate_name", ["workspace", "finance"])
def test_discovery_on_both_estates_proposes_the_mapping_and_builds_nothing(
        walnut_env, tmp_path, monkeypatch, estate_name):
    import estates
    from databridge_graph.packs import clear_caches, deployment
    from databridge_graph.store import GraphStore
    from test_id7_kind_split_and_identity import _estate_profile

    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_estate_profile(tmp_path, estate_name)))

    db = estates.build_estate(estate_name, tmp_path / "e.sqlite")
    scope = [f"{s}.{t}" for s, t in estates.tables_of(db, estate_name)][:4]
    clear_caches()
    try:
        with deployment(estates.ESTATES[estate_name]["pack"]) as vocab:
            client = estates.engine_client(db)
            mi.register_connection({"id": SID, "transport": {"type": "stdio", "command": "uvx"},
                                    "scope": {"tables_include": scope}})
            _approve(SID, client)
            store = GraphStore(str(tmp_path / "g.db"))
            rep = mi.run_ingest(SID, store=store, client=client, vocab=vocab, capture_only=True)
            man = _check_discovery(rep, SID, docs_expected=False)
            assert set(man["tables"]) <= set(scope)
            assert _node_count(store) == 0
    finally:
        clear_caches()
        db.close()


@pytest.mark.real_approval
def test_a_discovery_is_never_resumed_as_a_build_nor_a_build_as_a_discovery(
        walnut_env, corpus, common_vocab):
    from databridge_graph.store import GraphStore

    root, _ = corpus
    mi.register_connection({**fs_connection(SID, root), "auto_approve": False})
    _approve(SID)
    gpath = Path(walnut_env["WALNUT_GRAPH_DB_PATH"])
    gpath.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(str(gpath))
    disc = mi.run_ingest(SID, store=store, capture_only=True)
    with pytest.raises(runner.ResumeRefused, match="mode"):
        mi.run_ingest(SID, store=store, run_id=disc.run_id)
    assert _node_count(store) == 0
    built = mi.run_ingest(SID, store=store)
    assert built.status in ("ok", "unverified")
    with pytest.raises(runner.ResumeRefused, match="mode"):
        mi.run_ingest(SID, store=store, run_id=built.run_id, capture_only=True)


def test_inside_a_discovery_every_graph_write_is_refused_on_that_thread_only():
    """`build.capture_only` is the ingest-level guard: each graph-writing entry point refuses,
    whatever store it is handed; another thread (another source's sync) is unaffected."""
    calls = {
        "store_batch": lambda: build.store_batch(object()).__enter__(),
        "build_tables": lambda: build.build_tables(None, None, source_id="x", tables=[],
                                                   retrieved_at=None, mask_cfg=None),
        "consolidate_identity_after_run": lambda: build.consolidate_identity_after_run(None,
                                                                                      None),
        "ProseBuilder.apply": lambda: build.ProseBuilder.apply(None, None, []),
        "the build stage": lambda: runner._build(*([None] * 15)),
        "item events and removals": lambda: runner._emit_item_events(
            "x", "r", [], {}, Path("."), True, None),
        "passage indexing": lambda: runner._index_passages("x", Path("."), False, None, None,
                                                           None),
    }
    other: dict = {}
    with build.capture_only():
        for name, call in calls.items():
            with pytest.raises(build.CaptureOnlyViolation, match=re.escape(name)):
                call()

        def elsewhere():
            other["inside"] = build.in_capture_only()
        t = threading.Thread(target=elsewhere)
        t.start()
        t.join()
    assert other == {"inside": False}
    assert not build.in_capture_only()


@pytest.mark.real_approval
def test_f2_3_a_table_held_for_review_reaches_the_mapper_as_schema_only_and_no_model(
        walnut_env, tmp_path, monkeypatch):
    """Codex R-027 AAX f2 #3: the finance estate plants text for an automated reader in a table
    (the screen holds it for review). Discovery still proposes its mapping, but from its schema
    only: no row of it reaches the mapper, and no model is offered for it."""
    import estates
    from databridge_graph.packs import clear_caches, deployment
    from databridge_mcp_ingest import mapping as mp
    from test_id7_kind_split_and_identity import _estate_profile

    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_estate_profile(tmp_path, "finance")))
    db = estates.build_estate("finance", tmp_path / "e.sqlite")
    scope = [f"{s}.{t}" for s, t in estates.tables_of(db, "finance")]
    seen: list[tuple[dict, object]] = []
    real = mp.ensure_mapping

    def spy(source_id, snapshots, vocab, **kw):
        seen.append((snapshots, kw.get("harness")))
        return real(source_id, snapshots, vocab, **kw)
    monkeypatch.setattr(mp, "ensure_mapping", spy)
    clear_caches()
    try:
        with deployment(estates.ESTATES["finance"]["pack"]) as vocab:
            client = estates.engine_client(db)
            mi.register_connection({"id": SID, "transport": {"type": "stdio", "command": "uvx"},
                                    "scope": {"tables_include": scope}})
            _approve(SID, client)
            sentinel = object()
            rep = mi.run_ingest(SID, client=client, vocab=vocab, harness=sentinel,
                                capture_only=True)
            man = json.loads((Path(rep.run_dir) / runner.DISCOVERY_FILE).read_text("utf-8"))
            held = [t for t, e in man["tables"].items() if e["decision"] == "review"]
            allowed = [t for t, e in man["tables"].items() if e["decision"] == "allow"]
            assert allowed                                                # non-vacuous
            for snaps, harness in seen:
                for t, snap in snaps.items():
                    if t in held:
                        assert snap["rows"] == [] and harness is None, t
                        assert set(snap) <= {*runner._SCHEMA_KEYS, "rows"}
            assert all(any(t in snaps for snaps, _ in seen) for t in held)   # still proposed
            assert any(h is sentinel for snaps, h in seen if set(snaps) & set(allowed))
            for t in held:
                assert "mapping_digest" in man["tables"][t]
            # R-029 (E2E run 5 F-01): the planted text holds its ROWS, not its table; no held
            # row reaches the mapper, and the discovery says how many it kept out
            from databridge_mcp_ingest.screen import row_digest
            gov = runner.read_jsonl(Path(rep.run_dir) / "governance.jsonl")
            rows_held = [g for g in gov if g.get("kind") == "table_row"
                         and g.get("decision") != "allow"]
            assert rows_held                                              # non-vacuous
            digests = {g["sha256"] for g in rows_held}
            for snaps, _ in seen:
                for t, snap in snaps.items():
                    assert not [r for r in snap.get("rows") or [] if row_digest(r) in digests], t
            for t in {g["table"] for g in rows_held}:
                assert man["tables"][t]["rows_held"] == sum(g["table"] == t for g in rows_held)
    finally:
        clear_caches()
        db.close()
