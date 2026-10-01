"""S1 — files/folders source: Walnut's original-bytes filesystem server, its crawl profile, the
write-capable follow-ups connection, and write-tool denial for ingestion.

Unit tests: no subprocess, no network, no model. The server's REAL tool handlers are driven
in-process (FastMCP.call_tool) behind the same sync client surface the runner uses, so what is
tested is the server code itself, not a re-implementation of it. The stdio route and the Larkspur
corpus (PDF/DOCX through docling) are covered by test_s1_integration_files.py.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
import yaml

import databridge_mcp_ingest as mi
from databridge_mcp_ingest.client import (ContentPart, McpClientError, ReadOnlyClient, ToolInfo,
                                          ToolResult, parts_from_dicts)
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.paths import bundled_profiles_dir
from databridge_mcp_ingest.plans import (WRITE_VERB, StepSpec, load_profiles, match_profiles,
                                         plan_from_tools, validate_steps)

from conftest import write_corpus

PKG = Path(__file__).resolve().parents[1]
SERVER_PY = PKG / "servers" / "files_blob" / "server.py"
FILES_PROFILES = bundled_profiles_dir() / "files"


def _server_module():
    spec = importlib.util.spec_from_file_location("walnut_files_blob_server", SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SRV = _server_module()


class InProcessClient:
    """The runner's sync client surface over a FastMCP instance's real handlers."""

    supports_resources = True

    def __init__(self, mcp) -> None:
        self._mcp = mcp
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self) -> list[ToolInfo]:
        out = []
        for t in asyncio.run(self._mcp.list_tools()):
            ann = t.annotations
            out.append(ToolInfo(name=t.name, description=t.description or "",
                                input_schema=dict(t.inputSchema or {}),
                                read_only=ann.readOnlyHint if ann else None,
                                destructive=ann.destructiveHint if ann else None))
        return out

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        self.calls.append((name, dict(arguments)))
        try:
            out = asyncio.run(self._mcp.call_tool(name, arguments))
        except Exception as exc:                                     # noqa: BLE001
            return ToolResult(parts=(ContentPart(kind="text",
                                                 text=f"Error executing tool {name}: {exc}"),),
                              is_error=True)
        if isinstance(out, tuple):
            out = out[0]
        return ToolResult(parts=parts_from_dicts([c.model_dump(by_alias=True) for c in out]))

    def list_resources(self):
        return []

    def read_resource(self, uri):
        raise McpClientError("no resources")

    def close(self) -> None:
        return None


def reader(*roots: Path, max_bytes: int = SRV.DEFAULT_MAX_BYTES):
    mcp = SRV.FastMCP("t", log_level="WARNING")
    SRV.build_reader(mcp, SRV.Roots([str(r) for r in roots]), max_bytes)
    return mcp


def writer(root: Path, exts=(".md", ".txt"), max_bytes: int = SRV.DEFAULT_WRITE_MAX_BYTES):
    mcp = SRV.FastMCP("t", log_level="WARNING")
    SRV.build_writer(mcp, SRV.Roots([str(root)]), tuple(exts), max_bytes)
    return mcp


def _load(name: str) -> dict:
    return yaml.safe_load((FILES_PROFILES / name).read_text(encoding="utf-8"))


# --- the profile ---------------------------------------------------------------------------------
def test_reader_tools_select_the_walnut_profile_with_no_problems(tmp_path):
    client = InProcessClient(reader(tmp_path))
    tools = client.list_tools()
    matched = [p.id for p in match_profiles(tools, has_resources=False)]
    assert matched == ["walnut-files-blob"]
    conn = mi.SourceConnection.model_validate(
        {"id": "x", "transport": {"type": "stdio", "command": "python"}})
    plan = plan_from_tools(conn, tools, has_resources=False)
    assert plan.problems == [] and plan.origin == "rules"
    step = plan.steps[0]
    # every read goes through read_bytes: the captured bytes are the file's own bytes
    assert step.read.text_tool is None and step.read.binary_tool == "read_bytes"


def test_reader_exposes_no_tool_that_can_change_the_source(tmp_path):
    tools = InProcessClient(reader(tmp_path)).list_tools()
    assert tools, "vacuous: the reader listed no tools"
    assert all(t.read_only is True and t.destructive is False for t in tools)
    assert not [t.name for t in tools if WRITE_VERB.search(t.name)]


#: tools/list of @modelcontextprotocol/server-filesystem 2026.8.31 — (name, readOnlyHint), read
#: from its published dist/index.js registerTool(...) annotations on 2026-10-01.
OFFICIAL_2026_8_31 = [
    ("read_file", True), ("read_text_file", True), ("read_media_file", True),
    ("read_multiple_files", True), ("write_file", False), ("edit_file", False),
    ("create_directory", False), ("list_directory", True), ("list_directory_with_sizes", True),
    ("directory_tree", True), ("move_file", False), ("search_files", True),
    ("get_file_info", True), ("list_allowed_directories", True)]


def test_official_profile_denies_every_write_tool_and_the_engine_refuses_each():
    deny = _load("filesystem.profile.yaml")["match"]["deny_tools"]
    writes = {n for n, ro in OFFICIAL_2026_8_31 if ro is False}
    assert writes, "vacuous: the recorded tool list has no write tool"
    assert writes <= set(deny), f"deny_tools misses {sorted(writes - set(deny))}"

    class Inner:
        called: list = []

        def call_tool(self, name, args):
            self.called.append(name)
            return ToolResult(parts=())

    inner = Inner()
    guarded = ReadOnlyClient(inner)
    tools = [ToolInfo(name=n, read_only=ro, input_schema={"required": []})
             for n, ro in OFFICIAL_2026_8_31]
    for name in deny:
        assert WRITE_VERB.search(name), name
        with pytest.raises(McpClientError, match="refused"):
            guarded.call_tool(name, {"path": "x"})
        step = StepSpec.model_validate({"id": "s", "strategy": "documents",
                                        "list": {"tool": "list_directory"},
                                        "read": {"text_tool": name}})
        problems = validate_steps([step], tools, has_resources=False)
        assert any(name in p and "can change the source" in p for p in problems), problems
    assert inner.called == []
    # and the official profile still plans for this tool list without touching a write tool
    matched = match_profiles(tools, has_resources=False)
    assert [p.id for p in matched] == ["mcp-server-filesystem"]
    plan_tools = set(StepSpec.model_validate({"id": "s", **matched[0].step}).tools())
    assert not plan_tools & set(deny)


def test_every_files_profile_loads_and_is_owned_by_the_files_directory():
    ids = {p.id for p in load_profiles()}
    on_disk = {yaml.safe_load(f.read_text(encoding="utf-8"))["id"]
               for f in FILES_PROFILES.glob("*.profile.yaml")}
    assert on_disk and on_disk <= ids


# --- path safety ---------------------------------------------------------------------------------
@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "files"
    (root / "Q3 #roadmap").mkdir(parents=True)
    (root / "Q3 #roadmap" / "roadmap.md").write_bytes(b"# Roadmap\n")
    (root / "a").mkdir()
    (root / "a" / "brief.md").write_bytes(b"alpha brief\r\n")
    (root / "b").mkdir()
    (root / "b" / "brief.md").write_bytes(b"beta brief\n")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4\n\x00\x01\x02binary\xff")
    sibling = tmp_path / "files2"
    sibling.mkdir()
    (sibling / "secret.md").write_bytes(b"outside")
    (tmp_path / "outside.md").write_bytes(b"outside")
    return root


@pytest.mark.parametrize("path", ["..\\outside.md", "../outside.md", "a/../../outside.md",
                                  "", "a\x00b"])
def test_relative_escapes_are_refused(tree, path):
    roots = SRV.Roots([str(tree)])
    with pytest.raises(SRV.AccessDenied):
        roots.resolve(path)


def test_absolute_outside_and_sibling_prefix_are_refused(tree):
    roots = SRV.Roots([str(tree)])
    for p in (tree.parent / "outside.md", tree.parent / "files2" / "secret.md",
              Path(str(tree) + "2") / "secret.md"):
        with pytest.raises(SRV.AccessDenied):
            roots.resolve(str(p))
    assert roots.resolve(str(tree / "a" / "brief.md")) == os.path.realpath(tree / "a" / "brief.md")


def test_relative_path_needs_a_single_root(tree, tmp_path):
    other = tmp_path / "files2"
    with pytest.raises(SRV.AccessDenied, match="one root"):
        SRV.Roots([str(tree), str(other)]).resolve("a/brief.md")


def test_symlink_out_of_the_root_is_listed_but_never_read(tree):
    link = tree / "escape.md"
    try:
        os.symlink(tree.parent / "outside.md", link)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symlinks")
    c = InProcessClient(reader(tree))
    names = [e["name"] for e in json.loads(c.call_tool("list_dir", {"path": str(tree)}).text)
             ["entries"]]
    assert "escape.md" in names                         # recorded, so it cannot vanish silently
    res = c.call_tool("read_bytes", {"path": str(link)})
    assert res.is_error and "outside the declared roots" in res.text


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are a Windows feature")
def test_junction_out_of_the_root_is_refused(tree):
    """A junction needs no privilege on Windows, so this path is always exercised there."""
    import subprocess

    target = tree.parent / "files2"
    link = tree / "jn"
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip(f"mklink /J failed: {done.stderr or done.stdout}")
    c = InProcessClient(reader(tree))
    listed = json.loads(c.call_tool("list_dir", {"path": str(tree)}).text)["entries"]
    assert "jn" in [e["name"] for e in listed]
    for tool, path in (("list_dir", link), ("read_bytes", link / "secret.md"),
                       ("stat_file", link / "secret.md")):
        res = c.call_tool(tool, {"path": str(path)})
        assert res.is_error and "outside the declared roots" in res.text, (tool, res.text)


def test_read_bytes_returns_the_original_bytes_with_their_digest(tree):
    c = InProcessClient(reader(tree))
    for rel in ("doc.pdf", "a/brief.md", "Q3 #roadmap/roadmap.md"):
        disk = (tree / rel).read_bytes()
        out = asyncio.run(c._mcp.call_tool("read_bytes", {"path": str(tree / rel)}))
        block = (out[0] if not isinstance(out, tuple) else out[0][0]).model_dump(by_alias=True)
        assert block["type"] == "resource"
        assert base64.b64decode(block["resource"]["blob"]) == disk          # CRLF, \xff intact
        assert block["resource"]["_meta"] == {"sha256": hashlib.sha256(disk).hexdigest(),
                                              "size": len(disk)}
        assert str(block["resource"]["uri"]).startswith("file:")


def test_read_bytes_refuses_directories_and_oversize(tree):
    c = InProcessClient(reader(tree, max_bytes=5))
    assert c.call_tool("read_bytes", {"path": str(tree / "a")}).is_error
    res = c.call_tool("read_bytes", {"path": str(tree / "doc.pdf")})
    assert res.is_error and "cap" in res.text


def test_list_dir_keeps_full_paths_and_hash_folder_names(tree):
    c = InProcessClient(reader(tree))
    listing = json.loads(c.call_tool("list_dir", {"path": str(tree)}).text)
    by_name = {e["name"]: e for e in listing["entries"]}
    assert by_name["Q3 #roadmap"]["type"] == "dir"
    assert by_name["Q3 #roadmap"]["path"] == str(tree / "Q3 #roadmap")
    inner = json.loads(c.call_tool("list_dir", {"path": by_name["Q3 #roadmap"]["path"]}).text)
    assert [e["name"] for e in inner["entries"]] == ["roadmap.md"]
    stat = json.loads(c.call_tool("stat_file", {"path": str(tree / "doc.pdf")}).text)
    assert stat["sha256"] == hashlib.sha256((tree / "doc.pdf").read_bytes()).hexdigest()


# --- the writer ----------------------------------------------------------------------------------
def test_writer_creates_once_and_refuses_everything_else(tmp_path):
    root = tmp_path / "Follow-ups"
    root.mkdir()
    (root / "existing").mkdir()
    c = InProcessClient(writer(root))
    names = {t.name for t in c.list_tools()}
    assert "write_file" in names and "read_bytes" not in names and "stat_file" not in names
    ok = json.loads(c.call_tool("write_file", {"path": "draft-INV-0041.md",
                                               "content": "Dear Rowan,\n"}).text)
    written = root / "draft-INV-0041.md"
    assert Path(ok["path"]) == written and written.read_bytes() == b"Dear Rowan,\n"
    assert ok["sha256"] == hashlib.sha256(b"Dear Rowan,\n").hexdigest()
    assert c.call_tool("write_file", {"path": "existing/x.txt", "content": "y"}).is_error is False
    refusals = {
        "overwrite": {"path": "draft-INV-0041.md", "content": "changed"},
        "extension": {"path": "run.ps1", "content": "x"},
        "escape": {"path": "../escape.md", "content": "x"},
        "absolute_outside": {"path": str(tmp_path / "escape.md"), "content": "x"},
        "new_folder": {"path": "newdir/x.md", "content": "x"},
    }
    for why, args in refusals.items():
        assert c.call_tool("write_file", args).is_error, why
    assert written.read_bytes() == b"Dear Rowan,\n"                     # not overwritten
    assert not (tmp_path / "escape.md").exists() and not (root / "newdir").exists()


def test_writer_size_cap(tmp_path):
    c = InProcessClient(writer(tmp_path, max_bytes=4))
    assert c.call_tool("write_file", {"path": "a.md", "content": "12345"}).is_error


def test_reader_and_writer_modes_are_exclusive(tmp_path):
    with pytest.raises(SystemExit, match="exclusive"):
        SRV.main(["--root", str(tmp_path), "--write-root", str(tmp_path)])
    with pytest.raises(SystemExit):
        SRV.main([])


def test_write_connection_is_separate_and_has_no_crawl_plan(tmp_path, monkeypatch):
    read_decl = _load("connections/larkspur-files.connection.yaml")
    write_decl = _load("connections/larkspur-followups.connection.yaml")
    assert read_decl["id"] != write_decl["id"]
    assert "--write-root" in write_decl["transport"]["args"]
    assert "--root" not in write_decl["transport"]["args"]
    assert "--write-root" not in read_decl["transport"]["args"]
    for decl in (read_decl, write_decl):
        mi.SourceConnection.model_validate(decl)           # both are valid declarations
    tools = InProcessClient(writer(tmp_path)).list_tools()
    conn = mi.SourceConnection.model_validate(write_decl)
    plan = plan_from_tools(conn, tools, has_resources=False)
    assert plan.steps == [] and plan.profiles == []


# --- end to end through the runner (in-process server, text corpus) ------------------------------
def test_runner_over_the_in_process_server(walnut_env, tmp_path, common_vocab, fake_harness):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import capture

    root = tmp_path / "corpus"
    root.mkdir()
    outcomes = write_corpus(root)
    hash_rel = "planning/Q3 #roadmap/roadmap.md"
    (root / hash_rel).parent.mkdir(parents=True)
    (root / hash_rel).write_bytes(b"# Roadmap\n\nAldermoor Logistics milestones.\n")
    outcomes[hash_rel] = "collect"
    client = InProcessClient(reader(root))
    mi.register_connection({"id": "s1unit", "auto_approve": True,
                            "transport": {"type": "stdio", "command": "unused"},
                            "scope": {"blocked": ["**/pricing/**"]}})
    plan = mi.propose_plan("s1unit", client=client)
    assert plan.status == "approved" and plan.profiles == ["walnut-files-blob"]
    report = mi.run_ingest("s1unit", store=GraphStore(str(tmp_path / "g.db")),
                           harness=fake_harness, client=client)
    assert report.status == "ok", (report.message, report.verify)
    run_dir = Path(report.run_dir)
    captured = read_jsonl(run_dir / "captured.jsonl")

    def rel(uri: str) -> str:
        return Path(uri).relative_to(root).as_posix()

    expected_collect = {r for r, o in outcomes.items() if o == "collect"}
    assert {rel(r["uri"]) for r in captured} == expected_collect
    for r in captured:                    # original bytes: the digest is the file's own sha256
        disk = (root / rel(r["uri"])).read_bytes()
        assert r["sha256"] == hashlib.sha256(disk).hexdigest()
        assert capture.get_bytes(r["sha256"]) == disk
    notes = [r for r in captured if r["name"] == "notes.txt"]
    assert len(notes) == 2 and len({r["item_id"] for r in notes}) == 2
    assert report.counts["pointers"] == report.counts["pointers_resolved"] > 0
    assert not [n for n, _ in client.calls if WRITE_VERB.search(n)]
