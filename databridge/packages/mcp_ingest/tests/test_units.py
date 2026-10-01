"""Unit tests that need no server: templates, parsers, profiles, screen, connections, chunking."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from databridge_mcp_ingest import paths
from databridge_mcp_ingest.build import chunk_spans
from databridge_mcp_ingest.client import ToolInfo
from databridge_mcp_ingest.connections import (ConnectionError_, SourceConnection, expand,
                                               import_declarations)
from databridge_mcp_ingest.crawl import Item, decide, item_identity
from databridge_mcp_ingest.connections import Scope
from databridge_mcp_ingest.parse import (TemplateError, join_path, loads_structured,
                                         parse_output, render)
from databridge_mcp_ingest.plans import (StepSpec, load_profiles, match_profiles,
                                         validate_steps)
from databridge_mcp_ingest.screen import (apply_masks, load_screen, luhn_ok, mod97_ok,
                                          parse_screen, ScreenError, screen_text)

PKG = Path(__file__).resolve().parents[1] / "databridge_mcp_ingest"


# --- templates and parsers -----------------------------------------------------------------------
def test_render_keeps_types_and_quotes_sql_safely():
    assert render("{limit|int}", {"limit": "5"}) == 5
    assert render({"a": ["{x}"]}, {"x": 3}) == {"a": [3]}
    sql = render("SELECT * FROM {s|ident}.{t|ident} WHERE n = {v|literal}",
                 {"s": "pub", "t": 'we"ird', "v": "O'Brien"})
    assert sql == "SELECT * FROM \"pub\".\"we\"\"ird\" WHERE n = 'O''Brien'"
    with pytest.raises(TemplateError):
        render("{missing}", {})


def test_python_repr_rows_are_parsed_without_retyping():
    text = ("[{'id': 1, 'amount': Decimal('12.50'), 'due': datetime.date(2026, 9, 1)}, "
            "{'id': 2, 'amount': Decimal('3'), 'due': None}]")
    rows = parse_output(text, {"mode": "json_rows"})
    assert rows[0]["amount"] == "12.50" and rows[0]["due"] == "2026-09-01"
    assert rows[1]["due"] is None
    assert loads_structured('[{"a": 1}]') == [{"a": 1}]


def test_lines_and_tree_parsers():
    lines = "[DIR] Q3 #roadmap\n[FILE] notes.txt\n\n"
    spec = {"mode": "lines", "patterns": [
        {"regex": r"^\[DIR\]\s+(?P<name>.+)$", "set": {"type": "dir"}},
        {"regex": r"^\[FILE\]\s+(?P<name>.+)$", "set": {"type": "file"}}]}
    got = parse_output(lines, spec)
    assert got == [{"name": "Q3 #roadmap", "type": "dir"}, {"name": "notes.txt", "type": "file"}]
    tree = '[{"name": "a", "type": "directory", "children": [{"name": "b.md", "type": "file"}]}]'
    assert {r["rel"] for r in parse_output(tree, {"mode": "tree"})} == {"a", "a/b.md"}
    assert join_path("C:\\data", "x.md") == "C:\\data\\x.md"
    assert join_path("/srv/data/", "x.md") == "/srv/data/x.md"


# --- profiles ------------------------------------------------------------------------------------
def _tools(*names: str) -> list[ToolInfo]:
    return [ToolInfo(name=n, input_schema={"type": "object", "required": []}) for n in names]


def test_every_bundled_profile_loads_and_lives_in_a_per_source_directory():
    profiles = load_profiles()
    assert profiles, "no crawl profiles bundled"
    files = sorted((PKG / "profiles").rglob("*.profile.yaml"))
    assert len(files) == len(profiles)
    for f in files:
        assert f.parent.parent == PKG / "profiles", f"{f} is not in profiles/<source>/"


@pytest.mark.parametrize("tools,has_resources,expected_group", [
    (("list_allowed_directories", "list_directory", "directory_tree", "read_file",
      "read_text_file", "read_media_file", "write_file", "edit_file", "move_file"), False,
     "filesystem"),
    (("list_schemas", "list_objects", "get_object_details", "execute_sql", "explain_query"),
     False, "postgres"),
    (("query",), True, "postgres"),
    (("search",), True, "gdrive"),
])
def test_profiles_match_by_tool_signature(tools, has_resources, expected_group):
    matched = match_profiles(_tools(*tools), has_resources)
    assert [p.group for p in matched] == [expected_group]


def test_a_query_tool_alone_is_not_a_database():
    assert match_profiles(_tools("query"), False) == []


def test_group_alternatives_pick_the_highest_priority():
    matched = match_profiles(_tools("list_allowed_directories", "list_directory", "read_file",
                                    "read_text_file", "read_media_file"), False)
    assert [p.id for p in matched] == ["mcp-server-filesystem"]
    legacy = match_profiles(_tools("list_allowed_directories", "list_directory", "read_file"),
                            False)
    assert [p.id for p in legacy] == ["mcp-server-filesystem-legacy"]


def test_validation_refuses_write_tools_invented_tools_and_missing_args():
    tools = _tools("list_directory", "write_file") + [
        ToolInfo(name="read_text_file", input_schema={"required": ["path", "encoding"]})]
    step = StepSpec.model_validate({
        "id": "s", "strategy": "documents",
        "list": {"tool": "list_directory", "args": {"path": "{path}"}},
        "read": {"text_tool": "read_text_file", "text_args": {"path": "{path}"},
                 "binary_tool": "write_file"}})
    problems = validate_steps([step], tools, has_resources=False)
    assert any("write_file" in p and "change the source" in p for p in problems)
    assert any("encoding" in p for p in problems)
    ghost = StepSpec.model_validate({"id": "g", "strategy": "documents",
                                     "list": {"tool": "nope"}, "read": {"text_tool": "nope"}})
    assert any("not offered" in p for p in validate_steps([ghost], tools, has_resources=False))


# --- scope and identity --------------------------------------------------------------------------
def test_identity_is_the_full_path_never_the_filename():
    a = item_identity("src", "/data/alpha/notes.txt")
    b = item_identity("src", "/data/beta/notes.txt")
    assert a != b and len(a) == 64
    assert item_identity("other", "/data/alpha/notes.txt") != a      # source-qualified


def test_scope_decisions_name_their_rule_and_reversal():
    scope = Scope(blocked=["**/pricing/**"], exclude=["**/*.tmp"], file_types=[".md", "txt"],
                  max_bytes=100)

    def item(rel, size=None):
        return Item(source_id="s", step_id="x", kind="document", uri=f"/r/{rel}",
                    name=rel.rsplit("/", 1)[-1], rel=rel, size=size)

    cases = {"a/pricing/x.md": "blocked", "a/b.tmp": "excluded", "a/b.pdf": "excluded",
             "a/b.md": "collect", "big.md": "excluded"}
    for rel, want in cases.items():
        d = decide(item(rel, size=1000 if rel == "big.md" else None), scope, collected_so_far=0)
        assert d.outcome == want, (rel, d)
        if want != "collect":
            assert d.rule and d.reverse
    assert Scope(file_types=["md"]).file_types == [".md"]


# --- screen --------------------------------------------------------------------------------------
def test_checksums_not_shape():
    assert luhn_ok("4111 1111 1111 1111") and not luhn_ok("4111 1111 1111 1112")
    assert mod97_ok("GB82 WEST 1234 5698 7654 32") and not mod97_ok("DE44 LSPR 2026 0457 11")


def test_masks_are_the_same_length_so_offsets_survive():
    cfg = load_screen(packs=[])
    text = "Mail a.b@example.org, card 4111 1111 1111 1111, part DE44 LSPR 2026 0457 11."
    masked, red = apply_masks(text, cfg.redactions)
    assert len(masked) == len(text)
    assert "a.b@example.org" not in masked and "4111 1111 1111 1111" not in masked
    assert "DE44 LSPR 2026 0457 11" in masked                      # fails mod-97: kept
    assert {r["rule"] for r in red} >= {"email", "card_number"}


def test_default_screen_outcomes():
    cfg = load_screen(packs=[])
    assert screen_text("hello", cfg).decision == "allow"
    blocked = screen_text("x\n-----BEGIN RSA PRIVATE KEY-----\nabc", cfg)
    assert blocked.decision == "block" and blocked.fired[0]["rule"] == "private_key_block"
    assert blocked.reverse
    review = screen_text("The total contract value is agreed.", cfg)
    assert review.decision == "review" and "approve_override" in review.reverse   # R-022 #2
    assert screen_text("The total contract value is agreed.", cfg,
                       override_allow=True).decision == "allow"
    # an override never lifts a decisive block
    assert screen_text("password = hunter2hunter2", cfg, override_allow=True).decision == "block"


def test_screen_files_refuse_unknown_keys():
    with pytest.raises(ScreenError):
        parse_screen({"version": 1, "surprise": True}, "t")
    with pytest.raises(ScreenError):
        parse_screen({"signals": [{"id": "x", "pattern": "y", "wieght": 2}]}, "t")


def test_a_pack_screen_can_never_drop_the_credential_gate(tmp_path):
    f = tmp_path / "screen.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "decisive": [], "signals": []}), encoding="utf-8")
    cfg = load_screen(path=f)
    assert screen_text("-----BEGIN PRIVATE KEY-----", cfg).decision == "block"


_WS_SCREEN = paths.DATABRIDGE_ROOT / "packs" / "workspace" / "screen.yaml"
_EXPECTED = paths.DATABRIDGE_ROOT / "fixtures" / "workspace_demo" / "expected_outcomes.yaml"


def _corpus_text(path: Path) -> str:
    from databridge_mcp_ingest.extract import extract_text

    if path.suffix == ".pdf":
        pypdf = pytest.importorskip("pypdf")
        return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages)
    if path.suffix == ".docx":
        docx = pytest.importorskip("docx")
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return extract_text(path.read_bytes(), name=path.name).text


def _expected() -> dict:
    if not (_WS_SCREEN.is_file() and _EXPECTED.is_file()):
        return {}
    return (yaml.safe_load(_EXPECTED.read_text(encoding="utf-8")) or {}).get("files") or {}


@pytest.mark.skipif(not _expected(), reason="workspace pack / demo corpus not present")
@pytest.mark.parametrize("rel", sorted(_expected()))
def test_workspace_screen_reproduces_the_expected_outcomes(rel):
    """Agent E's ground truth, evaluated by THIS package's screen over the pack's own rules."""
    spec = _expected()[rel]
    cfg = load_screen(path=_WS_SCREEN)
    text = _corpus_text(_EXPECTED.parent / "files" / rel)
    result = screen_text(text, cfg)
    assert result.decision == spec["decision"], (rel, result.rationale)
    fired = {f["rule"] for f in result.fired}
    assert set(spec.get("rules") or []) <= fired, (rel, fired)
    assert set(spec.get("redacted") or []) <= {r["rule"] for r in result.redactions}
    for kept in spec.get("kept") or []:
        assert kept in result.masked_text


def test_workspace_expectations_cover_the_decisions_they_claim():
    exp = _expected()
    if not exp:
        pytest.skip("demo corpus not present")
    decisions = {v["decision"] for v in exp.values()}
    assert decisions == {"allow", "review", "block"}                # non-vacuous


# --- connections ---------------------------------------------------------------------------------
def test_literal_secrets_are_refused_and_references_are_kept(monkeypatch):
    base = {"id": "x", "transport": {"type": "stdio", "command": "srv"}}
    with pytest.raises(ValueError):
        SourceConnection.model_validate({**base, "transport": {
            "type": "stdio", "command": "srv", "env": {"API_KEY": "abc123"}}})
    conn = SourceConnection.model_validate({**base, "transport": {
        "type": "stdio", "command": "srv", "env": {"API_KEY": "${HOST_KEY}"},
        "env_from": {"TOKEN": "HOST_TOKEN"}}})
    with pytest.raises(ConnectionError_):
        conn.resolved_env()
    monkeypatch.setenv("HOST_KEY", "k1")
    monkeypatch.setenv("HOST_TOKEN", "t1")
    assert conn.resolved_env() == {"API_KEY": "k1", "TOKEN": "t1"}
    assert "k1" not in conn.model_dump_json()                       # never stored expanded


def test_expand_defaults_and_walnut_root(monkeypatch):
    monkeypatch.delenv("WALNUT_ROOT", raising=False)
    monkeypatch.delenv("NOPE", raising=False)
    assert expand("${NOPE:-fallback}") == "fallback"
    assert expand("${WALNUT_ROOT}/x") == f"{paths.DATABRIDGE_ROOT}/x"


def test_infra_declarations_import(tmp_path, monkeypatch):
    infra = paths.DATABRIDGE_ROOT / "infra" / "mcp_servers.yaml"
    if not infra.is_file():
        pytest.skip("infra/mcp_servers.yaml not present")
    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(tmp_path))
    data = yaml.safe_load(infra.read_text(encoding="utf-8"))
    conns = import_declarations(infra, route="direct", advertise=False)
    assert [c.id for c in conns] == [s["id"] for s in data["servers"]]
    assert all((tmp_path / f"{c.id}.yaml").is_file() for c in conns)
    jungle = import_declarations(infra, route="mcpjungle", register=False)
    assert all(c.via == "mcpjungle" and c.jungle_name == c.id for c in jungle)
    for decl, conn in zip(data["servers"], conns):
        exts = (decl.get("scope") or {}).get("include_extensions")
        if exts:
            assert conn.scope.file_types == [e.lower() for e in exts]


# --- chunking ------------------------------------------------------------------------------------
def test_chunks_cover_the_text_with_overlap():
    text = ("word " * 1200).strip()
    spans = chunk_spans(text)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert s2 < e1, "consecutive chunks must overlap"
        assert e1 - s1 <= 1800
    assert chunk_spans("short") == [(0, 5)] and chunk_spans("") == []


# --- the engine names no domain ------------------------------------------------------------------
def test_no_domain_terms_in_this_package():
    spec = importlib.util.spec_from_file_location(
        "_ndb", paths.DATABRIDGE_ROOT / "packages" / "graph" / "tests" /
        "test_no_domain_branching.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    offenders = []
    for f in sorted(PKG.rglob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if mod._WORD.search(line) or mod._MEMBER.search(line):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert list(PKG.rglob("*.py")), "scanned nothing"
    assert not offenders, offenders
