"""End to end against the synthetic stdio MCP server (a real subprocess, a real MCP session).

Expectations are DERIVED from the corpus the fixture writes (`conftest.write_corpus` /
`write_db`), never typed in here as numbers.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.plans import PlanDraft, DraftStep, PlanError, load_plan
from databridge_mcp_ingest.runner import LEDGERS, verify_run

from conftest import FAKE_KEY, FIXTURE_SERVER, ENTITIES, fs_connection


def _store(walnut_env):
    from databridge_graph.store import GraphStore

    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


def _run_dir(report) -> Path:
    return Path(report.run_dir)


# --- plans ---------------------------------------------------------------------------------------

def _owner():
    """R-022 f1 #1 (FXI3): plans are approved by an ISSUED principal, never a typed name."""
    from conftest import test_principal

    return test_principal("Test Owner")

def test_rules_plan_for_a_filesystem_and_sql_server(walnut_env, corpus, fixture_db, common_vocab):
    root, _ = corpus
    db, _ = fixture_db
    mi.register_connection({**fs_connection("both", root, db=db), "auto_approve": False})
    plan = mi.propose_plan("both")
    assert plan.origin == "rules" and plan.status == "proposed" and not plan.problems
    assert set(plan.profiles) == {"mcp-server-filesystem", "mcp-server-postgres"}
    assert "write_file" in plan.tools_seen
    assert all("write_file" not in s.tools() for s in plan.steps)
    with pytest.raises(mi.PlanNotApproved):
        mi.run_ingest("both", store=_store(walnut_env))
    approved = mi.approve_plan("both", principal=_owner())
    assert approved.status == "approved" and load_plan("both").approved_by == "Test Owner"


def _odd_connection(root: Path) -> dict:
    return {"id": "odd", "transport": {"type": "stdio", "command": sys.executable,
                                       "args": [str(FIXTURE_SERVER), "--odd", "--root",
                                                str(root)]},
            "scope": {"roots": ["catalogue"]}}


def _plan_harness(draft: PlanDraft):
    from databridge_harness import FakeBackend, Harness

    backend = FakeBackend(draft.model_dump_json())
    return Harness(backend, profile="cloud"), backend


def test_model_plan_when_no_profile_matches(walnut_env, corpus, common_vocab):
    root, _ = corpus
    mi.register_connection(_odd_connection(root))
    good = PlanDraft(steps=[DraftStep(
        strategy="documents", list_tool="catalogue_entries", list_parse_mode="json",
        items_path="entries", name_field="title", id_field="key",
        read_tool="fetch_entry", read_args={"key": "{id}"}, rationale="catalogue then fetch")])
    harness, backend = _plan_harness(good)
    plan = mi.propose_plan("odd", harness=harness)
    assert backend.call_count == 1
    assert plan.origin == "model" and plan.status == "proposed" and not plan.problems
    assert any("needs review" in n for n in plan.notes)
    # `catalogue_entries` has no annotation and no read verb: fail closed until the approving
    # person vouches for it BY NAME (Codex round 3); `fetch_entry` is a read by its verb.
    assert plan.tool_classes["catalogue_entries"].startswith("unclassified:")
    with pytest.raises(PlanError, match="unclassified"):
        mi.approve_plan("odd", principal=_owner())
    mi.approve_plan("odd", principal=_owner(), vouch_read_tools=["catalogue_entries"])


def test_a_model_plan_that_invents_or_writes_cannot_be_approved(walnut_env, corpus,
                                                                common_vocab):
    root, _ = corpus
    mi.register_connection(_odd_connection(root))
    bad = PlanDraft(steps=[DraftStep(
        strategy="documents", list_tool="list_everything", read_tool="write_entry",
        read_args={"key": "{id}"})])
    harness, _ = _plan_harness(bad)
    plan = mi.propose_plan("odd", harness=harness)
    assert plan.problems
    assert any("list_everything" in p for p in plan.problems)
    with pytest.raises(PlanError):
        mi.approve_plan("odd", principal=_owner())


# --- the full run --------------------------------------------------------------------------------
def test_full_run_end_to_end(walnut_env, corpus, fixture_db, common_vocab, fake_harness):
    root, outcomes = corpus
    db, row_counts = fixture_db
    mi.register_connection(fs_connection("both", root, db=db))
    plan = mi.propose_plan("both")
    assert plan.status == "approved"                                    # auto_approve
    store = _store(walnut_env)
    events: list[dict] = []
    report = mi.run_ingest("both", store=store, harness=fake_harness, progress_cb=events.append)
    run_dir = _run_dir(report)
    assert report.status == "ok", report.message
    assert report.verify["ok"], report.verify["problems"]
    assert events and events[-1]["stage"] == "report"

    # every ledger exists, including the ones this run had nothing to write to
    for name in LEDGERS:
        assert (run_dir / f"{name}.jsonl").is_file(), name
    for name in ("report.json", "verify.json", "plan.snapshot.yaml", "checkpoint.json"):
        assert (run_dir / name).is_file(), name

    # reconciliation, derived from the corpus the fixture wrote
    files = [r for r, o in outcomes.items()]
    blocked_dirs = {r.split("/")[0] for r, o in outcomes.items() if o == "blocked"}
    visible_files = [r for r in files if r.split("/")[0] not in blocked_dirs]
    tables = len(row_counts)
    c = report.counts
    assert c["inventory"] == len(visible_files) + len(blocked_dirs) + tables
    assert c["blocked"] == len(blocked_dirs)
    assert c["excluded"] == sum(1 for o in outcomes.values() if o == "excluded")
    assert c["captured"] == sum(1 for o in outcomes.values() if o == "collect") + tables

    # the planted key was blocked BEFORE any model call
    gov = {r["uri"].replace("\\", "/"): r for r in read_jsonl(run_dir / "governance.jsonl")}
    key_doc = next(v for k, v in gov.items() if k.endswith("engineering/deploy.txt"))
    assert key_doc["decision"] == "block" and key_doc["fired"][0]["kind"] == "decisive"
    sent = "\n".join(m["content"] for call in fake_harness.calls for m in call
                     if isinstance(m.get("content"), str))
    assert "PRIVATE KEY" not in sent and FAKE_KEY.splitlines()[1] not in sent
    extraction_calls = [call for call in fake_harness.calls
                        if call[-1]["content"].startswith("DOCUMENT TEXT:")]
    allowed_chunks = [r for r in read_jsonl(run_dir / "governance_chunks.jsonl")
                      if r.get("item_id") and r["decision"] == "allow"]
    assert len(extraction_calls) == len(allowed_chunks) > 0

    # contact identifiers never reached the model
    assert "planning@aldermoor.example" not in sent

    # every pointer resolves against the captured text, and names what it grounds
    pointers = read_jsonl(run_dir / "pointers.jsonl")
    spans = [p for p in pointers if p["kind"] == "doc_span" and p.get("label")]
    assert spans
    for p in spans:
        text = capture.get_text(p["sha"])
        assert text[p["start"]:p["end"]] == p["label"]
    assert {p["label"] for p in spans} == {n for n in ENTITIES
                                           if any(n in (root / r).read_text(encoding="utf-8")
                                                  for r, o in outcomes.items()
                                                  if o == "collect" and "deploy" not in r)}

    # the graph: an anchor per built document, and table rows with table_cell pointers
    built_docs = [r for r in read_jsonl(run_dir / "build.jsonl") if r["kind"] == "document"]
    assert built_docs and all(r["anchor"] for r in built_docs)
    for r in built_docs:
        assert store.open_node_row(r["anchor"]) is not None
    # a document anchor's label cites its LISTING record (R-022 #5, table `mcp_listing`): those
    # are anchor pointers, not table rows
    from databridge_mcp_ingest.build import LISTING_TABLE
    listing = [p for p in pointers if p["kind"] == "table_cell" and p["table"] == LISTING_TABLE]
    assert len(listing) == len(built_docs)
    cells = [p for p in pointers if p["kind"] == "table_cell" and p["table"] != LISTING_TABLE]
    # audit_log matches no kind; since EF3 (E2E F-23) it is PROPOSED under the policy's fallback
    # meta-kind instead of staying unmapped, and here the conftest's named approver approved it.
    from databridge_mcp_ingest.mapping import load_mapping
    mapping = load_mapping("both")
    assert any("fallback" in r for r in mapping.tables["public.audit_log"].rationale)
    approved = [t.split(".", 1)[-1] for t, tm in mapping.tables.items()
                if tm.status == "approved"]
    assert "audit_log" in approved
    mapped_rows = sum(row_counts[t] for t in approved)
    assert len({p["row_id"] + p["table"] for p in cells}) == mapped_rows
    import sqlite3
    with sqlite3.connect(db) as raw:
        linked = raw.execute("SELECT COUNT(*) FROM accounts WHERE organisation_id IS NOT NULL"
                             ).fetchone()[0]
    fk_edges = [e for e in store.open_edges() if e["src_id"].startswith("account:")
                and e["dst_id"].startswith("organisation:")]
    assert len(fk_edges) == linked > 0

    # every pointer in the store (entity, attribute, and a derived pointer's basis) carries the
    # connection id as its source_id — the agent runtime scopes visibility on exactly that
    def walk(p):
        yield p
        for parent in (p.derived_from or []):
            yield from walk(parent)

    checked = 0
    for row in [*store.open_nodes(), *store.open_edges()]:
        ptrs = [*store.sources_for(row["id"]), *store.attr_sources_for(row["id"]).values()]
        for ptr in ptrs:
            for p in walk(ptr):
                assert p.source_id == "both", (row["id"], p.source_id)
                assert p.trust is not None and p.trust.value == "untrusted"
                checked += 1
    assert checked > 0

    # capture layout (frozen contract)
    for rec in read_jsonl(run_dir / "captured.jsonl"):
        sha = rec["sha256"]
        base = Path(walnut_env["WALNUT_CAPTURE_DIR"]) / sha[:2]
        assert (base / sha).is_file() and (base / f"{sha}.json").is_file()
        meta = json.loads((base / f"{sha}.json").read_text(encoding="utf-8"))
        assert set(meta) >= {"source", "uri", "mime", "title", "retrieved_at"}

    # events (BUILD_PLAN §5.3)
    ev = read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
    added = [e for e in ev if e["type"] == "item_added"]
    assert len(added) == c["built"]
    assert all(e["content_hash"].startswith("sha256:") for e in added)
    assert ev[-1]["type"] == "run_completed"


def test_identity_by_full_path(walnut_env, corpus, common_vocab, fake_harness):
    root, outcomes = corpus
    mi.register_connection(fs_connection("files", root))
    mi.propose_plan("files")
    report = mi.run_ingest("files", store=_store(walnut_env), harness=fake_harness)
    same_name = [r for r in outcomes if r.endswith("/notes.txt")]
    assert len(same_name) == 2
    captured = [r for r in read_jsonl(_run_dir(report) / "captured.jsonl")
                if r["name"] == "notes.txt"]
    assert len({r["item_id"] for r in captured}) == 2
    anchors = {r["anchor"] for r in read_jsonl(_run_dir(report) / "build.jsonl")
               if r["uri"].replace("\\", "/").endswith("notes.txt")}
    assert len(anchors) == 2


def test_ledgers_are_written_even_when_empty(walnut_env, tmp_path, common_vocab, fake_harness):
    root = tmp_path / "clean"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("Aldermoor Logistics weekly note.\n", encoding="utf-8")
    mi.register_connection(fs_connection("clean", root))
    mi.propose_plan("clean")
    report = mi.run_ingest("clean", store=_store(walnut_env), harness=fake_harness)
    assert report.status == "ok"
    for name in ("excluded", "blocked", "failed", "extract_skipped", "coverage"):
        path = _run_dir(report) / f"{name}.jsonl"
        assert path.is_file() and path.read_text(encoding="utf-8") == "", name


def test_an_empty_run_is_not_reported_as_success(walnut_env, tmp_path, common_vocab):
    root = tmp_path / "empty"
    root.mkdir()
    mi.register_connection(fs_connection("empty", root))
    mi.propose_plan("empty")
    report = mi.run_ingest("empty", store=_store(walnut_env), harness=None)
    assert report.status == "empty" and "not a success" in report.message.lower()
    ev = read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
    assert ev[-1]["type"] == "run_completed" and ev[-1]["attrs"]["status"] == "empty"


def test_incremental_skips_unchanged_and_reingests_changed(walnut_env, corpus, common_vocab,
                                                           fake_harness):
    root, outcomes = corpus
    mi.register_connection(fs_connection("inc", root))
    mi.propose_plan("inc")
    store = _store(walnut_env)
    first = mi.run_ingest("inc", store=store, harness=fake_harness)
    calls_after_first = len(fake_harness.calls)
    second = mi.run_ingest("inc", store=store, harness=fake_harness)
    assert second.status == "ok"
    built_first = first.counts["built"]
    # EF3 (E2E F-31): an item the screen blocked / sent to review is ALSO unchanged next time
    decided_first = sum(first.counts["governance"].get(d, 0) for d in ("block", "review"))
    assert second.counts["unchanged"] == built_first + decided_first
    assert second.counts["built"] == 0
    assert len(fake_harness.calls) == calls_after_first                 # no model call
    target = root / "projects" / "alpha" / "notes.txt"
    target.write_text(target.read_text(encoding="utf-8") + "Quillon Freight joined.\n",
                      encoding="utf-8")
    third = mi.run_ingest("inc", store=store, harness=fake_harness)
    assert third.counts["built"] == 1 and \
        third.counts["unchanged"] == built_first - 1 + decided_first
    ev = read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
    changed = [e for e in ev if e["type"] == "item_changed"]
    assert len(changed) == 1 and changed[0]["uri"].replace("\\", "/").endswith(
        "alpha/notes.txt")
    target.unlink()
    fourth = mi.run_ingest("inc", store=store, harness=fake_harness)
    assert fourth.status == "ok"
    removed = [e for e in read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
               if e["type"] == "item_removed"]
    assert len(removed) == 1


def test_reconciliation_catches_a_planted_drop(walnut_env, corpus, common_vocab, fake_harness):
    root, _ = corpus
    mi.register_connection(fs_connection("drop", root))
    mi.propose_plan("drop")
    report = mi.run_ingest("drop", store=_store(walnut_env), harness=fake_harness)
    assert verify_run("drop", report.run_id)["ok"]
    path = _run_dir(report) / "extracted.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1
    path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")        # silently lose one
    result = verify_run("drop", report.run_id)
    assert not result["ok"]
    assert any("captured" in p for p in result["problems"])


def test_a_pointer_that_does_not_resolve_is_caught(walnut_env, corpus, common_vocab,
                                                   fake_harness):
    root, _ = corpus
    mi.register_connection(fs_connection("ptr", root))
    mi.propose_plan("ptr")
    report = mi.run_ingest("ptr", store=_store(walnut_env), harness=fake_harness)
    path = _run_dir(report) / "pointers.jsonl"
    rows = read_jsonl(path)
    target = next(r for r in rows if r.get("label"))
    target["start"] += 1                                                 # shift one character
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert not verify_run("ptr", report.run_id)["ok"]


def test_no_model_still_captures_screens_and_anchors(walnut_env, corpus, common_vocab):
    root, _ = corpus
    mi.register_connection(fs_connection("nomodel", root))
    mi.propose_plan("nomodel")
    report = mi.run_ingest("nomodel", store=_store(walnut_env), harness=None)
    assert report.status == "ok"
    assert any("no model configured" in n for n in report.notes)
    assert report.counts["entities"] == 0 and report.counts["built"] > 0


# --- the shared async client (BUILD_PLAN §5.4) -----------------------------------------------------
def test_open_session_direct(walnut_env, corpus):
    root, _ = corpus
    conn = mi.register_connection(fs_connection("sess", root))

    async def go():
        async with mi.open_session("sess") as s:
            tools = await s.list_tools()
            listing = await s.call_tool("list_directory", {"path": str(root)})
            return tools, listing

    tools, listing = asyncio.run(go())
    assert {"list_directory", "read_text_file"} <= {t["name"] for t in tools}
    assert listing["is_error"] is False and "[DIR] projects" in listing["content"][0]["text"]
    assert conn.id == "sess"


def test_mcpjungle_route_speaks_the_flat_invoke_body():
    import httpx

    from databridge_mcp_ingest.client import JungleClient, _MinimalJungle

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/v0/tools":
            return httpx.Response(200, json=[
                {"name": "files__list_directory", "input_schema": {}, "enabled": True},
                {"name": "other__query", "input_schema": {}}])
        body = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text",
                                                      "text": f"[FILE] {body['path']}"}],
                                         "isError": False})

    api = _MinimalJungle("http://jungle", transport=httpx.MockTransport(handler))
    conn = mi.SourceConnection.model_validate({"id": "files", "via": "mcpjungle",
                                               "transport": {"type": "stdio", "command": "x"}})
    client = JungleClient(conn, backend=api)
    assert [t.name for t in client.list_tools()] == ["list_directory"]
    result = client.call_tool("list_directory", {"path": "/data"})
    assert result.text == "[FILE] /data"
    body = json.loads(seen[-1][2])
    assert body == {"name": "files__list_directory", "path": "/data"}
    with pytest.raises(mi.McpClientError):
        client.list_resources()


def test_a_run_resumes_without_redoing_finished_work(walnut_env, corpus, common_vocab,
                                                     fake_harness):
    root, _ = corpus
    mi.register_connection(fs_connection("resume", root))
    mi.propose_plan("resume")
    store = _store(walnut_env)
    first = mi.run_ingest("resume", store=store, harness=fake_harness)
    run_dir = _run_dir(first)
    stages = json.loads((run_dir / "stages.json").read_text(encoding="utf-8"))
    for s in ("build", "verify"):
        stages.pop(s, None)                            # as if the process died during build
    (run_dir / "stages.json").write_text(json.dumps(stages), encoding="utf-8")
    calls = len(fake_harness.calls)
    again = mi.run_ingest("resume", store=store, harness=fake_harness, run_id=first.run_id)
    assert again.run_id == first.run_id and again.status == "ok", again.message
    built = [r["item_id"] for r in read_jsonl(run_dir / "build.jsonl")]
    assert len(built) == len(set(built)) == first.counts["built"]
    assert len(fake_harness.calls) == calls            # nothing re-sent to the model


def test_every_pointer_this_package_mints_names_its_trust():
    from databridge_core.trust_gate import format_report, scan

    pkg = Path(mi.__file__).resolve().parent
    violations = scan([pkg])
    assert not violations, format_report(violations)
