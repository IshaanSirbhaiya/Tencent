"""Finance dry run 2026-10-02 (wiki/40-reviews/FIN-live-dry-run-2026-10-02.md), fixed by ANS6.

* FIN-11 (compiler side): a document source has no query path, so an agent reads it through its
  read tools. The compiler granted only the tools a step NAMED: "find the memo with list_dir" got
  ``list_dir`` and not the tool that reads the file, so the agent could list the folder and never
  read the memo. Now the tools an APPROVED crawl plan lists and reads the source with (its reading
  path) are granted together; nothing off that path is added.
* Pack gap: a pack with several purposes and no operator setting bound NO purpose, so the finance
  launcher bound the closest one. The pack now marks the purpose compiled agents bind, and ships
  its own answer-contract golden set; a pack that marks none is told so, naming the file.

Two estates, each a real files tree in this repo: the Larkspur workspace files
(`fixtures/workspace_demo/files`) and the finance credit memos (`fixtures/packs/finance/
credit_memos`). The connection, its crawl plan and its tool list are written as `mcp_ingest`
writes them and read back through the real `load_connection_info`; the compiler's model pass is
the real `Harness` over a scripted backend. Expectations are derived from the plan and the
catalogue's own classification, never written down.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from databridge_automation import compile_english
from databridge_automation.catalogue import (
    Catalogue,
    Governance,
    default_agent_purpose,
    default_golden_set,
    load_connection_info,
)
from databridge_automation.compiler import ModelProposal
from databridge_automation.declaration import compile_declaration
from databridge_harness import FakeBackend, Harness
from databridge_workflow_spec import parse_tool

REPO = Path(__file__).resolve().parents[3]
ESTATES = {
    "larkspur-files": REPO / "fixtures" / "workspace_demo" / "files",
    "finance_credit_memos": REPO / "fixtures" / "packs" / "finance" / "credit_memos",
}
#: The files_blob server's tools, as its `tools/list` reports them (read-only annotations).
SERVER_TOOLS = ("list_roots", "list_dir", "stat_file", "read_bytes")


def _declare(tmp: Path, cid: str, root: Path) -> Path:
    """`<id>.yaml`, `<id>.tools.yaml` and an APPROVED `<id>.plan.yaml` (roots -> list -> read), the
    shape `mcp_ingest` writes for a files_blob source."""
    base = tmp / "sources"
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{cid}.yaml").write_text(yaml.safe_dump({
        "id": cid, "title": cid, "purpose": "ingest",
        "transport": {"type": "stdio", "command": "python",
                      "args": ["server.py", "--root", str(root)]}}), encoding="utf-8")
    (base / f"{cid}.tools.yaml").write_text(yaml.safe_dump({"tools": [
        {"name": n, "read_only": True, "destructive": False} for n in SERVER_TOOLS]}),
        encoding="utf-8")
    (base / f"{cid}.plan.yaml").write_text(yaml.safe_dump({
        "source_id": cid, "status": "approved",
        "steps": [{"id": "files", "strategy": "documents",
                   "roots": {"tool": "list_roots", "args": {}},
                   "list": {"tool": "list_dir", "args": {"path": "{path}"}},
                   "read": {"text_tool": None, "binary_tool": "read_bytes",
                            "binary_args": {"path": "{path}"}}}],
        "tools_seen": list(SERVER_TOOLS)}), encoding="utf-8")
    return base / f"{cid}.yaml"


def _first_doc(root: Path) -> str:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))[0]


@pytest.mark.parametrize("cid", sorted(ESTATES))
def test_fin11_a_step_naming_the_listing_tool_is_granted_the_whole_reading_path(tmp_path, cid):
    root = ESTATES[cid]
    assert root.is_dir()
    conn = load_connection_info(_declare(tmp_path, cid, root), ingest_root=tmp_path / "ingest")
    assert conn is not None and not conn.query_capable
    doc = _first_doc(root)
    proposal = {"kind": "agent", "title": "Document summary", "agent_id": "document_summary",
                "instructions": [f"Use list_dir to find {doc} in {cid}.",
                                 "Summarise what it says, citing every fact."],
                "sources": [cid], "tools": [f"source.read:{cid}:list_dir"]}
    fb = FakeBackend(json.dumps(ModelProposal.model_validate(proposal).model_dump()))
    cat = Catalogue(connections=(conn,), kinds=("document",), subject_kinds=("person",),
                    governance=Governance(pack="workspace", purpose="workspace.agent_run"))
    res = compile_english(f"Document summary: summarise {doc} from {cid}. Read only; cite every "
                          f"fact.", cat, harness=Harness(fb, cache=None))
    assert res.spec is not None, [q.text for q in res.open_questions]
    granted = {parse_tool(t)["tool"] for t in res.spec.tools
               if parse_tool(t)["family"] == "source" and parse_tool(t)["connection"] == cid}
    # the reading path is what the APPROVED plan lists and reads with — derived from the plan
    plan = yaml.safe_load((tmp_path / "sources" / f"{cid}.plan.yaml").read_text("utf-8"))
    step = plan["steps"][0]
    path = {step["roots"]["tool"], step["list"]["tool"], step["read"]["binary_tool"]}
    assert set(conn.reading_path) == path
    assert path <= granted                        # the step named list_dir; the memo can be read
    # least privilege holds: a read tool OFF the path is not granted (non-vacuous: it is a read)
    off_path = {t.name for t in conn.read_tools} - path
    assert off_path and not (off_path & granted)
    # every grant says why it is there
    for t in path - {"list_dir"}:
        d = next(d for d in res.spec.derivations if d.field == f"tools[source.read:{cid}:{t}]")
        assert "reading path" in (d.evidence or "")


@pytest.mark.parametrize("cid", sorted(ESTATES))
def test_fin11_an_unapproved_plan_adds_nothing(tmp_path, cid):
    """The reading path is a HUMAN-APPROVED plan's: a proposed plan vouches for nothing."""
    p = _declare(tmp_path, cid, ESTATES[cid])
    plan_file = p.parent / f"{cid}.plan.yaml"
    plan = yaml.safe_load(plan_file.read_text("utf-8"))
    plan["status"] = "proposed"
    plan_file.write_text(yaml.safe_dump(plan), encoding="utf-8")
    conn = load_connection_info(p, ingest_root=tmp_path / "ingest")
    assert conn is not None and conn.reading_path == ()


# ------------------------------------------------------------------------------------------------
# the pack gap: a default purpose and golden set declared by the pack, or a clear refusal
# ------------------------------------------------------------------------------------------------
PACKS = REPO / "packs"


def test_pack_finance_declares_the_compiled_agent_purpose_and_golden_set():
    purposes = yaml.safe_load((PACKS / "finance" / "purposes.yaml").read_text("utf-8"))
    marked = [p["id"] for p in purposes["purposes"] if p.get("default_for_compiled_agents")]
    assert len(purposes["purposes"]) > 1 and len(marked) == 1
    assert default_agent_purpose("finance", PACKS) == marked[0]
    gold = default_golden_set("finance", PACKS)
    assert gold is not None and (REPO / gold).is_file()
    records = [json.loads(line) for line in (REPO / gold).read_text("utf-8").splitlines()
               if line.strip() and not line.startswith("#")]
    assert records and all(r.get("id") and r.get("ask") for r in records)
    # every abstention says why and what must be reported (the workspace set's contract)
    assert all(r.get("why") and r.get("outcome") for r in records if r["expect_abstain"])


def test_pack_golden_values_are_the_fixture_values():
    """The finance golden set's values are READ from the estate's seed SQL: re-derive each cited
    cell from the fixture and compare (a typed value would drift silently)."""
    import re
    import sqlite3
    from decimal import Decimal

    db = sqlite3.connect(":memory:")
    for f in sorted((REPO / "fixtures" / "packs" / "finance" / "core_banking" / "seed").glob(
            "*.sql")):
        sql = f.read_text("utf-8")
        for m in re.finditer(r"INSERT INTO \w+\.(\w+) \(([^)]*)\) VALUES(.*?);\s*$", sql,
                             re.S | re.M):
            table, cols, vals = m.groups()
            cols = [c.strip() for c in cols.split(",")]
            db.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ('
                       + ", ".join(f'"{c}"' for c in cols) + ")")
            db.execute(f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES {vals}')
    gold = REPO / default_golden_set("finance", PACKS)
    checked = 0
    for line in gold.read_text("utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        rec = json.loads(line)
        for _field, exp in rec["expect"].items():
            if "cite" not in exp:
                continue
            m = re.fullmatch(r"[\w-]+\.\w+\.(\w+)/([^#]+)#(\w+)", exp["cite"])
            table, key, col = m.groups()
            keycol = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')][0]
            got = db.execute(f'SELECT "{col}" FROM "{table}" WHERE "{keycol}" = ?',
                             (key,)).fetchone()[0]
            if isinstance(got, (int, float)):
                assert Decimal(str(got)) == Decimal(exp["value"])
            else:
                assert str(got) == exp["value"]
            checked += 1
        if "cites" in rec["expect"].get("watchlist_facilities", {}):
            want = {k for (k,) in db.execute(
                "SELECT facility_id FROM facility WHERE lower(trim(status)) = 'watchlist'")}
            cited = {c.split("/")[1].split("#")[0]
                     for c in rec["expect"]["watchlist_facilities"]["cites"]}
            assert cited == want and rec["expect"]["watchlist_facilities"]["value"] == \
                str(len(want))
            checked += 1
    assert checked >= 4


def test_a_pack_that_marks_no_default_is_refused_with_the_file_to_fix(tmp_path):
    root = tmp_path / "packs"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "purposes.yaml").write_text(yaml.safe_dump({"pack": "demo", "purposes": [
        {"id": "demo.one", "description": "a", "subject_scoped": False},
        {"id": "demo.two", "description": "b", "subject_scoped": False}]}), encoding="utf-8")
    assert default_agent_purpose("demo", root) is None
    assert default_golden_set("demo", root) is None
    out = compile_declaration({"agent": "x", "pack": "demo", "purpose": ""},
                              Catalogue(governance=Governance(pack="demo")))
    assert out.outcome == "not_compiled"
    assert "packs/demo/purposes.yaml" in out.reason
    assert "default_for_compiled_agents" in out.reason
    # one purpose needs no mark
    (root / "demo" / "purposes.yaml").write_text(yaml.safe_dump({"pack": "demo", "purposes": [
        {"id": "demo.one", "description": "a", "subject_scoped": False}]}), encoding="utf-8")
    assert default_agent_purpose("demo", root) == "demo.one"
