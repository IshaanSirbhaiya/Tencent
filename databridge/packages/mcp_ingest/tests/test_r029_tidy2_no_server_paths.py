"""R-029 TIDY2 item 7 (SETX cross-track): the run artefacts a person reads name no SERVER path.

`report.json` (its notes and message — "screen rules: C:\\...\\screen.yaml") and a discovery's
`documents.items[].uri` (shown on the setup page) carried absolute server paths. They now carry the
path relative to the source's root (a document) or to the deployment directory it lies in (a
rules file), never the server's own path. Expectations are derived from the run's own ledgers and
the directories the test created.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import runner
from databridge_mcp_ingest.ledger import read_jsonl

from conftest import fs_connection
from conftest import test_principal as _principal

SID = "tidy2-paths"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))


def _server_dirs(root: Path, tmp_path: Path) -> list[str]:
    repo = Path(runner.__file__).resolve().parents[3]
    dirs = {str(root.resolve()), str(tmp_path.resolve()), str(repo), str(Path.home().resolve())}
    out = set()
    for d in dirs:
        out |= {d, d.replace("\\", "/"), d.replace("\\", "\\\\")}
    return sorted(out, key=len, reverse=True)


def _assert_no_server_path(text: str, dirs: list[str], where: str) -> None:
    low = text.lower()
    for d in dirs:
        assert d.lower() not in low, (where, d, text[:500])
    assert not re.search(r"(?<![\w/])[A-Za-z]:[\\/]", text), (where, text[:500])


def test_the_report_and_the_discovery_name_no_server_path(walnut_env, corpus, fixture_db,
                                                           common_vocab, fake_harness, tmp_path):
    from databridge_graph.store import GraphStore

    root, _ = corpus
    db, _ = fixture_db
    mi.register_connection({**fs_connection(SID, root, db=db), "auto_approve": False})
    plan = mi.propose_plan(SID)
    unknown = [t for t, c in plan.tool_classes.items() if c.startswith("unclassified")]
    mi.approve_plan(SID, principal=_principal("Dana"), vouch_read_tools=unknown)
    gpath = Path(walnut_env["WALNUT_GRAPH_DB_PATH"])
    gpath.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(str(gpath))
    dirs = _server_dirs(root, tmp_path)

    disc = mi.run_ingest(SID, store=store, harness=fake_harness, capture_only=True)
    run_dir = Path(disc.run_dir)
    inv = {r["item_id"]: r for r in read_jsonl(run_dir / "inventory.jsonl")}
    # non-vacuous: the source names its documents by their absolute server path
    assert any(str(root.resolve()).lower() in str(r.get("uri")).lower() for r in inv.values())
    man = json.loads((run_dir / runner.DISCOVERY_FILE).read_text(encoding="utf-8"))
    items = man["documents"]["items"]
    assert items                                                          # non-vacuous
    for it in items:
        assert it["uri"] == inv[it["item_id"]]["rel"], it                # relative to the root
    _assert_no_server_path(json.dumps(man["documents"]), dirs, "discovery documents")

    rep = mi.run_ingest(SID, store=store, harness=fake_harness)
    for rd in (run_dir, Path(rep.run_dir)):
        body = json.loads((rd / "report.json").read_text(encoding="utf-8"))
        assert body["notes"]                                              # non-vacuous
        assert any("screen rules" in n for n in body["notes"])
        for field in ("notes", "message"):
            _assert_no_server_path(json.dumps(body.get(field)), dirs, f"report {field}")


@pytest.mark.parametrize("text", [
    "path:/opt/private/report.json", "see file:///opt/private/report.json now",
    "uri=file:///C:/secret/dir/a.md", r"x C:\secret\dir\a.md", "at /var/lib/walnut/x.db"])
def test_public_text_leaves_no_absolute_path(text):
    """R-029 Codex f1 #8: a POSIX path right after a colon, and a file URI, are server paths too."""
    from databridge_mcp_ingest.ledger import public_text

    out = public_text(text)
    for secret in ("/opt/private", "secret", "/var/lib"):
        assert secret not in out, out
    assert out.count("...") == 1                                          # replaced, not deleted


def test_public_text_keeps_urls_and_relative_paths():
    from databridge_mcp_ingest.ledger import public_text

    for text in ("see https://host.example/a/b?x=1", "docs/overview.md", "ratio 3/4"):
        assert public_text(text) == text
