"""S3 — the Google Drive source (@piotr-agier/google-drive-mcp 2.12.0) against a RECORDED fake.

No network, no Google account, no live model. The fake (`fixtures/gdrive/fake_gdrive_server.py`)
is a real stdio MCP server whose `tools/list` is the real server's, verbatim, and whose `search` /
`resources/*` answers follow the real handlers; the Drive behind it is the Larkspur demo corpus
uploaded into one folder, plus Drive-only shapes (native Docs, a duplicate name in one folder, a
Form, a trashed file, paging). Every expectation is DERIVED from that corpus and from
`fixtures/workspace_demo/expected_outcomes.yaml`, never typed in as a number.

The Drive profile needs four crawl-profile fields the engine does not have yet (see the profile's
header and `fixtures/gdrive/core_extension.patch`). Tests that run a crawl detect them and skip,
saying so, until the extension lands; the rest run today.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture, paths
from databridge_mcp_ingest.client import ToolInfo
from databridge_mcp_ingest.crawl import item_identity
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.parse import parse_output, render
from databridge_mcp_ingest import plans as _plans
from databridge_mcp_ingest.plans import ListSpec, ReadSpec, load_profiles

FIXTURES = Path(__file__).with_name("fixtures") / "gdrive"
sys.path.insert(0, str(FIXTURES))
import drive_corpus  # noqa: E402

FAKE_SERVER = FIXTURES / "fake_gdrive_server.py"
RECORDED = json.loads((FIXTURES / "tools_list.2.12.0.json").read_text(encoding="utf-8"))
PROFILE_DIR = paths.bundled_profiles_dir() / "gdrive"
ACTIVE = PROFILE_DIR / "google_drive_mcp.profile.yaml"
PENDING = PROFILE_DIR / "google_drive_mcp.profile.yaml.pending"
DEMO = paths.DATABRIDGE_ROOT / "fixtures" / "workspace_demo"

CORE_EXTENSION = ({"page", "uri", "meta_fields"} <= set(ListSpec.model_fields)
                  and "resource_uri" in ReadSpec.model_fields
                  and getattr(_plans, "writes_by_name", lambda _n: False)("createFolder"))
needs_core = pytest.mark.skipif(
    not CORE_EXTENSION,
    reason="the S3 core extension (list.page / list.uri / list.meta_fields / read.resource_uri / "
           "camelCase-aware write names) is not applied yet — see tests/fixtures/gdrive/core_extension.patch")


def _profile_file() -> Path:
    return ACTIVE if ACTIVE.is_file() else PENDING


def _profile() -> dict:
    return yaml.safe_load(_profile_file().read_text(encoding="utf-8"))


def _recorded_tools() -> list[ToolInfo]:
    return [ToolInfo(name=t["name"], description=t.get("description", ""),
                     input_schema=t.get("inputSchema") or {},
                     read_only=(t.get("annotations") or {}).get("readOnlyHint"),
                     destructive=(t.get("annotations") or {}).get("destructiveHint"))
            for t in RECORDED["tools"]]


def _read_only_names() -> set[str]:
    return {t["name"] for t in RECORDED["tools"]
            if (t.get("annotations") or {}).get("readOnlyHint") is True}


def _search_line(name: str, mime: str, fid: str, path: str = "My Drive/x",
                 created: str = "2026-09-01T09:00:00.000Z",
                 modified: str = "2026-09-02T09:00:00.000Z") -> str:
    """One `search` result line in the real handler's format (dist/index.js 2.12.0)."""
    return f"{name} ({mime}) [id: {fid}, path: {path}] [created: {created}, modified: {modified}]"


# ================================================================================ runs today
def test_the_recorded_tool_list_is_the_real_servers():
    tools = RECORDED["tools"]
    names = [t["name"] for t in tools]
    assert RECORDED["server"]["version"] == "2.12.0"
    assert len(names) == len(set(names)) > 0
    read_only = _read_only_names()
    assert read_only and read_only < set(names)
    # the server labels its reads and leaves every other tool unlabelled (no readOnlyHint: false,
    # no destructiveHint) — so annotations alone never flag its writers
    assert all(not (t.get("annotations") or {}) for t in tools if t["name"] not in read_only)


def test_the_profile_denies_every_tool_the_server_does_not_label_read_only():
    denied = set(_profile()["match"]["deny_tools"])
    writers = {t["name"] for t in RECORDED["tools"]} - _read_only_names()
    assert writers and denied == writers
    assert {"createFolder", "uploadFile", "deleteItem", "downloadFile", "shareFile"} <= denied


def test_the_drive_profile_names_only_real_read_only_tools_with_their_required_args():
    prof = _profile()
    names = {t["name"]: t for t in RECORDED["tools"]}
    assert set(prof["match"]["all_tools"]) <= set(names)
    step = prof["step"]
    called = [step["list"]["tool"]] + [step.get("read", {}).get(k) for k in
                                       ("text_tool", "binary_tool")]
    called = [t for t in called if t]
    assert called, "the profile calls no tool"
    for tool in called:
        assert tool in _read_only_names(), f"{tool} is not annotated read-only by the server"
        required = set(names[tool]["inputSchema"].get("required") or [])
        assert required <= set(step["list"]["args"]), (tool, required)
    # nothing is listed without a declared root — no whole-Drive default (P5)
    assert "root_arg_default" not in step["list"]
    assert "roots" not in step


def test_the_listing_parser_reads_the_real_search_format():
    """Header and paging lines are dropped; titles with brackets, '#', and dashes parse; native
    types are scoped as the type they export to; folders are folders."""
    doc = drive_corpus.DOC
    cases = [  # (name, drive mime, expected type, expected scoped mime)
        ("contracts", drive_corpus.FOLDER, "folder", None),
        ("Q3 #roadmap", drive_corpus.FOLDER, "folder", None),
        ("Kick-off notes (Pellucid)", doc, "file", "text/markdown"),
        ("Budget (draft) v2", "application/vnd.google-apps.spreadsheet", "file", "text/csv"),
        ("Quarterly review — deck", "application/vnd.google-apps.presentation", "file",
         "text/plain"),
        ("MSA (signed).pdf", "application/pdf", "file", "application/pdf"),
        ("notes.md", "text/markdown", "file", "text/markdown"),
        ("Team survey", drive_corpus.FORM, "file", drive_corpus.FORM),
    ]
    ids = [drive_corpus.drive_id(f"parse:{i}") for i in range(len(cases))]
    text = (f"Found {len(cases)} files (ordered by name):\n"
            + "\n".join(_search_line(n, m, i, path="My Drive/Walnut demo/a (b)")
                        for (n, m, _, _), i in zip(cases, ids))
            + "\n\nMore results available. Use pageToken: ~!!~AI9FV7abc000100"
            + "\nPass orderBy: name again to keep this ordering.")
    entries = parse_output(text, _profile()["step"]["list"]["parse"])
    assert [e["name"] for e in entries] == [c[0] for c in cases]
    assert [e["id"] for e in entries] == ids
    for entry, (_, drive_mime, kind, scoped) in zip(entries, cases):
        assert entry["type"] == kind
        assert entry.get("mime") == scoped
        assert entry["drive_path"] == "My Drive/Walnut demo/a (b)"
        assert entry["modified"] == "2026-09-02T09:00:00.000Z"
        if entry.get("drive_mime"):
            assert entry["drive_mime"] == drive_mime


def test_the_page_token_pattern_matches_the_paging_line_and_nothing_else():
    import re

    page = _profile()["step"]["list"]["page"]
    rx = re.compile(page["regex"], re.M)
    token = "~!!~AI9FV7Zx_-9000050"
    text = ("Found 1 files (ordered by name):\n" + _search_line("pageToken notes.md", "text/plain",
                                                                 "1abc")
            + f"\n\nMore results available. Use pageToken: {token}\nPass orderBy: name again")
    assert [m.group("token") for m in rx.finditer(text)] == [token]
    assert rx.search(text.split("\n\nMore")[0]) is None
    assert page["arg"] in {p for t in RECORDED["tools"] if t["name"] == "search"
                           for p in t["inputSchema"]["properties"]}


def test_the_pending_profile_does_not_break_the_bundled_loader():
    profiles = load_profiles()
    ids = [p.id for p in profiles]
    if ACTIVE.is_file():
        assert "google-drive-mcp" in ids
    else:
        assert "google-drive-mcp" not in ids and PENDING.is_file()


def test_the_fake_serves_the_recorded_tools_and_the_real_read_shapes(tmp_path):
    """Fidelity of the fake itself: recorded tools/list; search paging; the error envelope; a
    Doc read as text/markdown, a PDF as a blob."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    drive = drive_corpus.build(tmp_path / "drive", DEMO / "files")
    log = tmp_path / "calls.jsonl"

    async def go():
        params = StdioServerParameters(command=sys.executable, args=[
            str(FAKE_SERVER), "--drive", str(drive.manifest), "--log", str(log),
            "--page-cap", "3"])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                first = await s.call_tool("search", {"query": f"'{drive_corpus.ROOT_ID}' in "
                                                              f"parents", "rawQuery": True,
                                                     "orderBy": "name", "pageSize": 100})
                bad = await s.call_tool("search", {"query": "contracts"})
                doc = next(f for f in drive.files if f.mime == drive_corpus.DOC)
                pdf = next(f for f in drive.files if f.mime == "application/pdf")
                doc_read = await s.read_resource(f"gdrive:///{doc.id}")
                pdf_read = await s.read_resource(f"gdrive:///{pdf.id}")
                return tools, first, bad, doc_read, pdf_read

    tools, first, bad, doc_read, pdf_read = asyncio.run(go())
    assert [t.name for t in tools] == [t["name"] for t in RECORDED["tools"]]
    text = first.content[0].text
    assert text.startswith("Found 3 files (ordered by name):") and "Use pageToken: " in text
    assert bad.isError and bad.content[0].text.startswith("Error: ")
    assert doc_read.contents[0].mimeType == "text/markdown" and doc_read.contents[0].text
    assert pdf_read.contents[0].mimeType == "application/pdf" and pdf_read.contents[0].blob
    assert [json.loads(line)["name"] for line in log.read_text().splitlines()
            if '"tools/call"' in line] == ["search", "search"]


# ======================================================================= needs the extension
def _activate(monkeypatch, tmp_path) -> None:
    """Make the Drive profile loadable: when it is still `.pending`, serve a copy through the
    extra-profiles directory (the bundled lookup is untouched)."""
    if ACTIVE.is_file():
        return
    extra = tmp_path / "profiles"
    extra.mkdir()
    shutil.copyfile(PENDING, extra / "google_drive_mcp.profile.yaml")
    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(extra))


def _connection(conn_id: str, drive, log: Path, *, page_cap: int = 4, **scope) -> dict:
    return {"id": conn_id, "title": "Larkspur Drive (fake)", "auto_approve": True,
            "transport": {"type": "stdio", "command": sys.executable,
                          "args": [str(FAKE_SERVER), "--drive", str(drive.manifest), "--log",
                                   str(log), "--page-cap", str(page_cap)]},
            "scope": {"roots": [drive_corpus.ROOT_ID], **scope}}


def _store(walnut_env):
    from databridge_graph.store import GraphStore

    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


@needs_core
def test_the_drive_server_gets_the_drive_profile_and_a_read_only_plan(walnut_env, tmp_path,
                                                                     monkeypatch, common_vocab):
    _activate(monkeypatch, tmp_path)
    drive = drive_corpus.build(tmp_path / "drive", DEMO / "files")
    mi.register_connection({**_connection("gdrive", drive, tmp_path / "calls.jsonl"),
                            "auto_approve": False})
    plan = mi.propose_plan("gdrive")
    # the older resources-only draft in the same group (server_gdrive) also matches this
    # server's tool list; the scoped profile must win the group
    assert plan.profiles == ["google-drive-mcp"], plan.profiles
    assert plan.origin == "rules" and not plan.problems and plan.status == "proposed"
    used = {t for s in plan.steps for t in s.tools()}
    assert used == {"search"} and used <= _read_only_names()
    assert set(plan.tools_seen) == {t["name"] for t in RECORDED["tools"]}
    # the listing's page token is FOLLOWED, so the plan is not flagged as first-page-only
    assert not getattr(plan, "pagination", [])


@needs_core
def test_a_route_that_cannot_read_resources_cannot_approve_the_plan(tmp_path, monkeypatch):
    """The plan reads bytes as resources; a client that proxies tools only (the minimal
    MCPJungle client) gets a plan with a problem, in words — not a crawl that reads nothing."""
    from databridge_mcp_ingest.connections import SourceConnection
    from databridge_mcp_ingest.plans import load_profiles as _load, plan_from_tools

    _activate(monkeypatch, tmp_path)
    conn = SourceConnection.model_validate({"id": "gdrive-jungle", "via": "mcpjungle",
                                            "transport": {"type": "http",
                                                          "url": "http://127.0.0.1:8080/mcp"}})
    plan = plan_from_tools(conn, _recorded_tools(), has_resources=False,
                           supports_resources=False, profiles=_load())
    assert plan.profiles == ["google-drive-mcp"]
    assert any("resources" in p and "directly" in p for p in plan.problems), plan.problems


@needs_core
def test_no_write_tool_of_the_drive_server_can_be_called_by_a_crawl(tmp_path, monkeypatch):
    """Every tool the server does not label read-only is refused — by plan validation and by the
    read-only client — through the profile's declared deny list; and a camelCase writer is
    caught by name even with no deny list (`createFolder` has no separator around its verb)."""
    from databridge_mcp_ingest.client import McpClientError, ReadOnlyClient, ToolResult
    from databridge_mcp_ingest.connections import SourceConnection
    from databridge_mcp_ingest.plans import (StepSpec, load_profiles as _load, writes_by_name,
                                             plan_from_tools, validate_steps)

    _activate(monkeypatch, tmp_path)
    tools = _recorded_tools()
    read_only = _read_only_names()
    writers = sorted(t.name for t in tools if t.name not in read_only)
    conn = SourceConnection.model_validate({"id": "gdrive", "transport": {
        "type": "stdio", "command": sys.executable}})
    plan = plan_from_tools(conn, tools, has_resources=True, profiles=_load())
    assert set(plan.deny_tools) == set(writers)

    class Stub:
        supports_resources = True
        called: list[str] = []

        def list_tools(self):
            return tools

        def call_tool(self, name, args):
            self.called.append(name)
            return ToolResult(parts=())

    client = ReadOnlyClient(Stub(), plan=plan)
    client.list_tools()
    for name in writers:
        with pytest.raises(McpClientError):
            client.call_tool(name, {})
    # FXI3 (Codex f1 #2): with a plan, a crawl calls ONLY the tools the plan classifies as
    # reads — a read-only tool the plan does not use is refused too (derived from the plan)
    plan_reads = sorted(t for t, c in plan.tool_classes.items() if c.startswith("read:"))
    assert plan_reads                                                   # non-vacuous
    for name in sorted(read_only):
        if name in plan_reads:
            client.call_tool(name, {})
        else:
            with pytest.raises(McpClientError):
                client.call_tool(name, {})
    assert Stub.called == [n for n in sorted(read_only) if n in plan_reads]
    for name in ("uploadFile", "downloadFile", "createTextFile"):
        step = StepSpec.model_validate({"id": "s", "strategy": "documents",
                                        "list": {"tool": "search", "args": {"query": "x"}},
                                        "read": {"text_tool": name}})
        problems = validate_steps([step], tools, has_resources=True, deny_tools=plan.deny_tools)
        assert any(name in p and "change the source" in p for p in problems), problems
    # by name alone: every camelCase verb-first writer, and no read tool
    assert {"createFolder", "deleteItem", "uploadFile", "moveItem", "renameItem",
            "updateGoogleDoc", "insertText"} <= {w for w in writers if writes_by_name(w)}
    assert not [r for r in read_only if writes_by_name(r)]


@needs_core
def test_full_crawl_of_the_drive_folder(walnut_env, tmp_path, monkeypatch, fake_harness):
    from databridge_graph.packs import deployment

    from databridge_mcp_ingest.extract import docling_available

    if not (paths.packs_dir() / "workspace" / "screen.yaml").is_file():
        pytest.skip("packs/workspace not present")
    _activate(monkeypatch, tmp_path)
    expected = yaml.safe_load((DEMO / "expected_outcomes.yaml").read_text(encoding="utf-8"))["files"]
    drive = drive_corpus.build(tmp_path / "drive", DEMO / "files")
    by_id = drive.by_id()
    log = tmp_path / "calls.jsonl"
    store = _store(walnut_env)

    with deployment("workspace"):
        mi.register_connection(_connection("larkspur-drive", drive, log))
        plan = mi.propose_plan("larkspur-drive")
        assert plan.status == "approved" and plan.profiles == ["google-drive-mcp"]
        first = mi.run_ingest("larkspur-drive", store=store, harness=fake_harness)
    assert first.status == "ok", (first.message, first.verify)
    assert first.verify["ok"], first.verify["problems"]
    run = Path(first.run_dir)
    inventory = [r for r in read_jsonl(run / "inventory.jsonl") if r.get("kind") != "folder"]
    files = {r["uri"]: r for r in inventory}

    def file_id(uri: str) -> str:
        return uri.rsplit("?id=", 1)[1]

    # ---- enumeration: every non-trashed file below the root, through every page
    assert {file_id(u) for u in files} == set(by_id)
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    searches = [c for c in calls if c["method"] == "tools/call"]
    assert any("pageToken" in c["args"] for c in searches), "paging was never exercised"
    children_of_root = sum(1 for f in by_id.values() if "/" not in f.rel) + sum(
        1 for rel in drive.folders if rel and "/" not in rel)
    assert children_of_root > 4, "the root must need more than one page at --page-cap 4"
    # ---- read-only: the crawl called `search` and nothing else; bytes came as resources
    assert {c["name"] for c in searches} == {"search"}
    assert all(c["args"].get("rawQuery") is True for c in searches)
    reads = [c for c in calls if c["method"] == "resources/read"]
    captured = read_jsonl(run / "captured.jsonl")
    assert len(reads) == len(captured)

    # ---- identity: source-qualified folder path + file id (P4)
    for uri, rec in files.items():
        f = by_id[file_id(uri)]
        assert uri == f"gdrive:///{drive_corpus.ROOT_ID}/{f.rel}?id={f.id}"
        assert rec["item_id"] == item_identity("larkspur-drive", uri)
    dup = [r for r in captured if r["uri"].split("?id=")[0].endswith(
        "projects/demand-forecasting/notes.md")]
    assert len(dup) == 2 and len({r["item_id"] for r in dup}) == 2
    assert len({r["sha256"] for r in dup}) == 2
    assert any("#roadmap/" in r["uri"] for r in captured), "the '#' folder was not collected"

    # ---- native Docs: exported as Markdown, recorded with their native mime + modified time
    for f in (f for f in by_id.values() if f.mime == drive_corpus.DOC):
        rec = next(r for r in captured if file_id(r["uri"]) == f.id)
        assert rec["mime"] == "text/markdown"
        # per item (captured.jsonl); capture meta is per CONTENT and keeps its first sighting —
        # here the Doc's text equals an uploaded .txt, so they share one capture
        assert rec["attrs"]["drive_mime"] == drive_corpus.DOC
        assert rec["attrs"]["modified"] and rec["attrs"]["drive_path"].startswith("My Drive/")
        assert capture.get_meta(rec["sha256"])["mime"] in ("text/markdown", "text/plain")
    # ---- a type nothing can read is RECORDED, never silently dropped
    excluded = {file_id(r["uri"]): r for r in read_jsonl(run / "excluded.jsonl")}
    unreadable = [f for f in by_id.values() if f.equivalent is None]
    assert unreadable and all(f.id in excluded and excluded[f.id]["reverse"] for f in unreadable)
    assert set(excluded) == {f.id for f in unreadable}

    # ---- screen decisions match the corpus's own expectations for the equivalent file
    gov = {file_id(r["uri"]): r for r in read_jsonl(run / "governance.jsonl")}
    skipped = {file_id(r["uri"]): r["reason"] for r in read_jsonl(run / "extract_skipped.jsonl")}
    checked = 0
    for f in by_id.values():
        if f.equivalent is None:
            continue
        if f.id in skipped:
            assert not docling_available() and skipped[f.id] == "extractor_unavailable", \
                (f.rel, skipped[f.id])
            continue
        spec = expected[f.equivalent]
        assert gov[f.id]["decision"] == spec["decision"], (f.rel, gov[f.id].get("rationale"))
        assert set(spec.get("redacted") or []) <= {r["rule"] for r in gov[f.id]["redactions"]}
        checked += 1
    assert checked >= sum(1 for f in by_id.values() if f.equivalent
                          and f.equivalent.endswith((".md", ".txt")))
    assert {gov[f.id]["decision"] for f in by_id.values() if f.native and f.id in gov} >= \
        {"allow", "block"}

    # ---- events: one item_added per built item
    events = read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
    assert len([e for e in events if e["type"] == "item_added"]) == first.counts["built"] > 0

    # ---- second run: nothing changed on Drive -> nothing rebuilt (content hash decides)
    with deployment("workspace"):
        second = mi.run_ingest("larkspur-drive", store=store, harness=fake_harness)
    assert second.status == "ok" and second.counts["built"] == 0
    # EF3 (E2E F-31): blocked / review items are unchanged too — not re-extracted
    decided_first = sum(first.counts["governance"].get(d, 0) for d in ("block", "review"))
    assert second.counts["unchanged"] == first.counts["built"] + decided_first

    # ---- third run: one Doc edited, one file trashed on Drive
    kickoff = next(f for f in by_id.values() if f.native and f.mime == drive_corpus.DOC
                   and expected[f.equivalent]["decision"] == "allow")
    gone = next(f for f in by_id.values() if f.rel == "projects/route-optimisation/brief.md")
    old_text = capture.get_text(next(r["sha256"] for r in captured
                                     if file_id(r["uri"]) == kickoff.id))
    drive_corpus.edit(drive.manifest, kickoff.id,
                      text=old_text + "\nAction: Mira Oyelaran to circulate the revised plan.\n")
    drive_corpus.edit(drive.manifest, gone.id, trashed=True)
    before = len(read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"])))
    with deployment("workspace"):
        third = mi.run_ingest("larkspur-drive", store=store, harness=fake_harness)
    assert third.status == "ok", third.message
    new_events = read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))[before:]
    changed = [e for e in new_events if e["type"] == "item_changed"]
    removed = [e for e in new_events if e["type"] == "item_removed"]
    assert [file_id(e["uri"]) for e in changed] == [kickoff.id]
    assert [file_id(e["uri"]) for e in removed] == [gone.id]
    assert not [e for e in new_events if e["type"] == "item_added"]
    edited = next(r for r in read_jsonl(Path(third.run_dir) / "captured.jsonl")
                  if file_id(r["uri"]) == kickoff.id)
    assert edited["attrs"]["modified"] == "2026-09-30T12:00:00.000Z"
    # new content, new capture: its meta carries this sighting's Drive facts
    meta = capture.get_meta(edited["sha256"])
    assert meta["mime"] == "text/markdown" and meta["attrs"]["drive_mime"] == drive_corpus.DOC


@needs_core
def test_a_connection_without_a_declared_folder_lists_nothing(walnut_env, tmp_path, monkeypatch,
                                                             common_vocab):
    """No root, no crawl: the run says so in words and is not reported as a success."""
    _activate(monkeypatch, tmp_path)
    drive = drive_corpus.build(tmp_path / "drive", DEMO / "files")
    log = tmp_path / "calls.jsonl"
    conn = _connection("noroot", drive, log)
    conn["scope"] = {}
    mi.register_connection(conn)
    mi.propose_plan("noroot")
    report = mi.run_ingest("noroot", store=_store(walnut_env), harness=None)
    assert report.status == "empty"
    failed = read_jsonl(Path(report.run_dir) / "failed.jsonl")
    assert failed and "no roots" in failed[0]["error"]
    assert not [line for line in (log.read_text().splitlines() if log.is_file() else [])
                if '"tools/call"' in line]


@needs_core
def test_a_declared_root_may_name_an_environment_variable(walnut_env, tmp_path, monkeypatch,
                                                          common_vocab):
    """infra/mcp_servers.yaml declares the Drive root as "${WALNUT_GDRIVE_FOLDER_ID}": expanded at
    run time (identity carries the real id), never written expanded into the connection file."""
    _activate(monkeypatch, tmp_path)
    monkeypatch.setenv("WALNUT_TEST_GDRIVE_ROOT", drive_corpus.ROOT_ID)
    drive = drive_corpus.build(tmp_path / "drive", DEMO / "files")
    conn = _connection("envroot", drive, tmp_path / "calls.jsonl")
    conn["scope"] = {"roots": ["${WALNUT_TEST_GDRIVE_ROOT}"], "file_types": [".md", ".txt"]}
    mi.register_connection(conn)
    assert "${WALNUT_TEST_GDRIVE_ROOT}" in (tmp_path / "sources" / "envroot.yaml").read_text(
        encoding="utf-8")
    mi.propose_plan("envroot")
    report = mi.run_ingest("envroot", store=_store(walnut_env), harness=None)
    captured = read_jsonl(Path(report.run_dir) / "captured.jsonl")
    assert report.status == "ok" and captured, report.message
    assert all(r["uri"].startswith(f"gdrive:///{drive_corpus.ROOT_ID}/") for r in captured)
    # file_types [.md, .txt] admits the native Docs (exported as Markdown) by their export type
    natives = {f.id for f in drive.files if f.mime == drive_corpus.DOC}
    assert natives <= {r["uri"].rsplit("?id=", 1)[1] for r in captured}


def test_render_of_the_identity_template_is_stable():
    """The identity template, rendered the way the engine renders it, keeps the '#' and spaces of
    a Drive folder name verbatim — identity is the path the person sees, not a URL-encoding."""
    uri = render(_profile()["step"]["list"]["uri"],
                 {"root": "1ROOT", "rel": "planning/Q3 #roadmap/roadmap.md", "id": "1FILE"})
    assert uri == "gdrive:///1ROOT/planning/Q3 #roadmap/roadmap.md?id=1FILE"
