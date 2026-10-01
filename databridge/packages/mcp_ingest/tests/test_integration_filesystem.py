"""Against the REAL @modelcontextprotocol/server-filesystem (npx). Marked `integration`: it needs
Node and the npm registry (or a warm npx cache), so it skips when `npx` is absent."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.paths import DATABRIDGE_ROOT

from conftest import write_corpus

pytestmark = pytest.mark.integration

NPX = shutil.which("npx")


def _conn(conn_id: str, root: Path, **scope) -> dict:
    return {"id": conn_id, "auto_approve": True,
            "transport": {"type": "stdio", "command": NPX,
                          "args": ["-y", "@modelcontextprotocol/server-filesystem", str(root)]},
            "scope": {"blocked": ["**/pricing/**"], **scope}}


@pytest.mark.skipif(NPX is None, reason="npx not installed")
def test_real_filesystem_server_end_to_end(walnut_env, tmp_path, common_vocab, fake_harness):
    from databridge_graph.store import GraphStore

    root = tmp_path / "corpus"
    root.mkdir()
    outcomes = write_corpus(root)
    mi.register_connection(_conn("realfs", root))
    plan = mi.propose_plan("realfs")
    assert plan.profiles == ["mcp-server-filesystem"] and plan.status == "approved"
    assert {"write_file", "edit_file", "move_file"} <= set(plan.tools_seen)
    store = GraphStore(str(tmp_path / "g.db"))
    report = mi.run_ingest("realfs", store=store, harness=fake_harness)
    assert report.status == "ok", (report.message, report.verify)
    assert report.counts["captured"] == sum(1 for o in outcomes.values() if o == "collect")
    assert report.counts["blocked"] == len({r.split("/")[0] for r, o in outcomes.items()
                                            if o == "blocked"})
    assert report.counts["pointers"] == report.counts["pointers_resolved"] > 0


DEMO = DATABRIDGE_ROOT / "fixtures" / "workspace_demo" / "files"


@pytest.mark.skipif(NPX is None or not DEMO.is_dir(), reason="npx or the demo corpus missing")
def test_real_server_over_the_workspace_demo_corpus(walnut_env, tmp_path, fake_harness):
    """The demo corpus through the real server with the workspace pack's screen: '#' folders are
    collected, same-named briefs stay distinct, and the screen's block/review/allow split matches
    the corpus's own expected_outcomes.yaml."""
    import yaml
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore

    if not (DATABRIDGE_ROOT / "packs" / "workspace" / "ontology.yaml").is_file():
        pytest.skip("packs/workspace not present")
    expected = yaml.safe_load((DEMO.parent / "expected_outcomes.yaml")
                              .read_text(encoding="utf-8"))["files"]
    with deployment("workspace"):
        mi.register_connection(_conn("demo", DEMO))
        mi.propose_plan("demo")
        report = mi.run_ingest("demo", store=GraphStore(str(tmp_path / "g.db")),
                               harness=fake_harness)
    assert report.verify["ok"], report.verify["problems"]
    run_dir = Path(report.run_dir)

    def rel_of(uri: str) -> str:
        return Path(uri).resolve().relative_to(DEMO.resolve()).as_posix()

    gov = {rel_of(r["uri"]): r["decision"] for r in read_jsonl(run_dir / "governance.jsonl")}
    # not captured or not extracted here → RECORDED (failed / extract_skipped), never silent.
    # (server-filesystem 2026.8.31's read_media_file rejects its own result for non-image
    # binaries, so PDF/DOCX land in failed.jsonl through this server version.)
    unread = {rel_of(r["uri"]) for name in ("extract_skipped", "failed")
              for r in read_jsonl(run_dir / f"{name}.jsonl") if r.get("uri")}
    checked = 0
    for rel, spec in expected.items():
        if rel in unread:
            continue
        assert gov.get(rel) == spec["decision"], (rel, gov.get(rel))
        checked += 1
    assert checked >= sum(1 for r in expected if r.endswith((".md", ".txt")))
    seen_decisions = {spec["decision"] for rel, spec in expected.items() if rel not in unread}
    assert seen_decisions >= {"allow", "block"}
    assert any("#" in rel for rel in gov), "the '#' folder was not collected"


@pytest.mark.skipif(not DEMO.is_dir(), reason="demo corpus missing")
def test_demo_corpus_through_docling_reproduces_every_expected_outcome(walnut_env, tmp_path,
                                                                        fake_harness):
    """Same corpus through the synthetic server (whose read_media_file returns bytes), so the
    PDF/DOCX path runs through docling: EVERY expected decision is reproduced end to end."""
    import yaml
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore

    from conftest import fs_connection
    from databridge_mcp_ingest.extract import docling_available

    if not docling_available():
        pytest.skip("docling not installed")
    if not (DATABRIDGE_ROOT / "packs" / "workspace" / "screen.yaml").is_file():
        pytest.skip("packs/workspace not present")
    expected = yaml.safe_load((DEMO.parent / "expected_outcomes.yaml")
                              .read_text(encoding="utf-8"))["files"]
    with deployment("workspace"):
        mi.register_connection({**fs_connection("demo2", DEMO), "scope": {}})
        mi.propose_plan("demo2")
        report = mi.run_ingest("demo2", store=GraphStore(str(tmp_path / "g.db")),
                               harness=fake_harness)
    assert report.status == "ok", report.message
    run_dir = Path(report.run_dir)
    gov = {Path(r["uri"]).resolve().relative_to(DEMO.resolve()).as_posix(): r
           for r in read_jsonl(run_dir / "governance.jsonl")}
    assert set(gov) == set(expected)
    for rel, spec in expected.items():
        assert gov[rel]["decision"] == spec["decision"], (rel, gov[rel]["rationale"])
        assert set(spec.get("redacted") or []) <= {r["rule"] for r in gov[rel]["redactions"]}
