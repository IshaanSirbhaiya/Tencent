"""The on-disk catalogue adapter (WALNUT_SOURCES_DIR) and the CLI, end to end on a temp `var/`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from databridge_automation import load_catalogue, to_adapter_catalogue
from databridge_automation.catalogue import WRITE_VERB, classify_tool, folder_paths


def _write_source(base: Path, ingest: Path) -> None:
    base.mkdir(parents=True)
    (base / "larkspur-files.yaml").write_text(yaml.safe_dump({
        "id": "larkspur-files", "title": "Larkspur files",
        "transport": {"type": "stdio", "command": "npx", "args": ["server-filesystem", "/data"]},
        "scope": {"include": ["Contracts/**", "Follow-ups/**"]}}), encoding="utf-8")
    (base / "larkspur-files.plan.yaml").write_text(yaml.safe_dump({
        "source_id": "larkspur-files", "status": "approved",
        "steps": [{"id": "s1", "kind": "documents"}],
        "tools_seen": ["list_directory", "read_text_file", "write_file", "move_file"]}),
        encoding="utf-8")
    run = ingest / "larkspur-files" / "20261001T090000Z"
    run.mkdir(parents=True)
    rows = [{"rel": "Contracts/msa.pdf"}, {"rel": "Invoices/2026/i-1.pdf"}, {"rel": "readme.md"}]
    (run / "inventory.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                         encoding="utf-8")


def test_load_catalogue_from_sources_dir(tmp_path):
    src, ingest = tmp_path / "sources", tmp_path / "ingest"
    _write_source(src, ingest)
    cat = load_catalogue(src, pack="workspace", ingest_root=ingest)
    (c,) = cat.connections
    assert c.id == "larkspur-files" and c.plan_status == "approved" and c.kind == "files"
    assert {t.name for t in c.write_tools} == {"write_file", "move_file"}
    assert {t.name for t in c.read_tools} == {"list_directory", "read_text_file"}
    # folders from the latest inventory AND the declared scope
    assert set(c.locations) == set(folder_paths(["Contracts/msa.pdf", "Invoices/2026/i-1.pdf"])) \
        | {"Contracts", "Follow-ups"}
    # the workspace ontology is the bound vocabulary
    assert "contract" in cat.kinds and "person" in cat.subject_kinds


def test_missing_sources_dir_is_an_empty_catalogue(tmp_path):
    cat = load_catalogue(tmp_path / "nope", pack="workspace")
    assert cat.connections == ()


def test_adapter_catalogue_never_advertises_a_write_as_a_read(tmp_path):
    src, ingest = tmp_path / "sources", tmp_path / "ingest"
    _write_source(src, ingest)
    adverts = to_adapter_catalogue(load_catalogue(src, pack="workspace", ingest_root=ingest))
    from databridge_agent_runtime.mapping import source_id_for, tool_capability
    files = adverts.get(source_id_for("larkspur-files"))       # the runtime's names (W2)
    assert files.writes == {tool_capability("write_file"), tool_capability("move_file")}
    assert not (files.reads & files.writes)
    assert adverts.get("graph").advertises("search")


def test_write_verb_agrees_with_ingest():
    import sys
    p = str(Path(__file__).resolve().parents[2] / "mcp_ingest")
    if p not in sys.path:
        sys.path.append(p)
    ingest = pytest.importorskip("databridge_mcp_ingest.plans",
                                 reason="mcp_ingest (or its MCP SDK) is not importable here")
    assert WRITE_VERB.pattern == ingest.WRITE_VERB.pattern
    extra = getattr(ingest, "WRITE_VERB_EXTRA", None)
    if extra is not None:                                            # R-017 #6 / S4
        from databridge_automation.catalogue import WRITE_VERB_EXTRA
        assert WRITE_VERB_EXTRA.pattern == extra.pattern


@pytest.mark.parametrize("name,hints,write", [
    ("read_text_file", {}, False), ("write_file", {}, True), ("query", {}, False),
    ("execute_sql", {"read_only": False}, True), ("search", {"destructive": True}, True),
])
def test_classify_tool(name, hints, write):
    assert classify_tool(name, **hints).write is write


def test_cli_compile_and_fire(tmp_path, monkeypatch, capsys):
    from databridge_automation.__main__ import main

    src, ingest = tmp_path / "sources", tmp_path / "ingest"
    _write_source(src, ingest)
    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(src))
    monkeypatch.setenv("WALNUT_AGENTS_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("WALNUT_ADAPTER_AGENTS_DIR", str(tmp_path / "adapter_agents"))
    monkeypatch.setenv("WALNUT_AUTOMATION_DIR", str(tmp_path / "automation"))
    monkeypatch.setenv("WALNUT_EVENTS_PATH", str(tmp_path / "events" / "events.jsonl"))
    monkeypatch.setattr("databridge_automation.catalogue.paths.var_dir", lambda: tmp_path)
    text = "When a new contract lands in the Contracts folder, summarise its payment terms."
    assert main(["compile", text, "--offline", "--json", "--save"]) == 0
    out = capsys.readouterr().out
    record = json.loads(out[: out.rindex("}") + 1])
    assert record["spec"]["trigger"]["type"] == "event"
    agent = record["spec_id"]
    assert (tmp_path / "agents" / f"{agent}.workflow.yaml").exists()
    # a saved DRAFT never runs, not even by hand (R-017 #1)
    assert main(["fire", agent, "--record-only"]) == 1
    # once active (here: flipped on disk, as an approved activation would) it fires
    from databridge_workflow_spec import dump_spec, load_spec
    wf_path = tmp_path / "agents" / f"{agent}.workflow.yaml"
    wf_path.write_text(dump_spec(load_spec(wf_path).model_copy(update={"status": "active"})),
                       encoding="utf-8")
    # no runner: refused outright, nothing recorded (Codex batch 2 #7) ...
    assert main(["fire", agent]) == 2
    # ... and an explicit record-only firing is recorded but exits 1: nothing ran
    assert main(["fire", agent, "--record-only"]) == 1
    fired = [json.loads(l) for l in (tmp_path / "automation" / "firings.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    assert [(f["status"], f.get("reason")) for f in fired if f["status"] != "pending"] == \
        [("skipped", "status is draft"), ("no_runner", "no runner is attached; the firing is "
                                                      "recorded only")]
    assert main(["compile", "Every weekday at 9am, pull deals from Salesforce.", "--offline"]) == 1
