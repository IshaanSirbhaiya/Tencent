"""Contract test: the adapter-agent manager over a REAL `databridge_mcp_ingest.run_ingest` run.

A real stdio MCP server (mcp_ingest's own synthetic `fixture_server.py`), the real staged pipeline,
the real §5.2 artefacts and §5.3 events, and a `FakeBackend` harness in place of a model. The model
labels every organisation it finds with a kind the ontology does not hold, so the extractor
REFUSES it and mcp_ingest writes the refusal to `refused.jsonl` in its own format.

Everything asserted is DERIVED from the artefacts the run wrote — the refused lines, the captured
digests, the chunk spans, the report — never typed in. mcp_ingest is used read-only.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MI_TESTS = Path(__file__).resolve().parents[2] / "mcp_ingest" / "tests"


def _mi_helpers():
    """mcp_ingest's own fixture helpers (corpus writer, stdio connection) — loaded by path under a
    distinct module name so the two packages' `conftest` modules never collide."""
    spec = importlib.util.spec_from_file_location("_mcp_ingest_fixture_helpers",
                                                  MI_TESTS / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def real_env(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    pytest.importorskip("databridge_mcp_ingest")
    pytest.importorskip("databridge_harness")
    for name, sub in (("WALNUT_SOURCES_DIR", "sources"), ("WALNUT_INGEST_DIR", "ingest"),
                      ("WALNUT_CAPTURE_DIR", "capture"),
                      ("WALNUT_EVENTS_PATH", "events/events.jsonl"),
                      ("WALNUT_GRAPH_DB_PATH", "graph/test.db"),
                      ("WALNUT_ADAPTER_AGENTS_DIR", "agents"), ("WALNUT_RUNS_DIR", "runs")):
        monkeypatch.setenv(name, str(tmp_path / sub))
    monkeypatch.delenv("WALNUT_CRAWL_PROFILES_DIR", raising=False)
    return tmp_path


def test_manager_over_a_real_ingest_run(real_env, spec_factory):
    import databridge_mcp_ingest as mi
    from databridge_core import SourcePointer
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_harness import FakeBackend, Harness
    from databridge_mcp_ingest import capture

    from databridge_adapter_agents import (AdapterAgentManager, AdapterAgentRegistry,
                                           McpIngestService, ProposalLog, ProposalPolicy,
                                           read_events)
    from databridge_adapter_agents.events import EMITTED_BY

    h = _mi_helpers()
    root = real_env / "corpus"
    root.mkdir()
    h.write_corpus(root)
    conn = "files"
    mi.register_connection(h.fs_connection(conn, root))
    assert not mi.propose_plan(conn).problems
    # a connection flag never approves a plan (mcp_ingest, Codex round 3): a named person does
    # ...and that person is an AUTHENTICATED principal object, never a typed name (FXI3)
    from aa_drift_kit import OWNER

    assert mi.approve_plan(conn, principal=OWNER).status == "approved"

    with deployment("common") as vocab:
        known_nodes = set(vocab.node_kinds)
        off = "shipping_line"
        assert off not in known_nodes                                  # truly off-ontology
        organisations = {n for n, k in h.ENTITIES.items() if k == "organisation"}
        assert organisations

        def handler(*, model, messages, guided_json) -> str:
            last = messages[-1]["content"]
            if not last.startswith("DOCUMENT TEXT:\n"):
                return json.dumps({})
            text = last[len("DOCUMENT TEXT:\n"):]
            ents = []
            for name, kind in h.ENTITIES.items():
                i = text.find(name)
                if i >= 0:
                    ents.append({"name": name, "kind": off if name in organisations else kind,
                                 "char_start": i, "char_end": i + len(name)})
            return json.dumps({"entities": ents, "relations": []})

        harness = Harness(FakeBackend(handler), profile="cloud")
        (real_env / "graph").mkdir(parents=True, exist_ok=True)
        store = GraphStore(str(real_env / "graph" / "test.db"))
        manager = AdapterAgentManager(
            service=McpIngestService(), registry=AdapterAgentRegistry(real_env / "agents"),
            store=store, harness=harness, runs_root=real_env / "runs",
            proposal_policy=ProposalPolicy(min_occurrences=2, min_documents=2),
            proposal_log=ProposalLog(real_env / "agents" / "_proposals" / "p.jsonl"))
        spec = manager.create(spec_factory(connections=(conn,)))
        summary = manager.sync(spec.id)

    # --- the run, as mcp_ingest recorded it --------------------------------------------------
    res = summary.connections[0]
    run_dir = real_env / "ingest" / conn / res.ingest_run_id
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == res.report_status
    assert res.status == ("completed" if report["status"] == "ok" else "unverified")
    assert res.status == "completed", report.get("message")
    assert res.counts["inventory"] == report["counts"]["inventory"]
    assert res.counts["refused"] == report["counts"]["refused"]

    # --- events: mcp_ingest owns this run's event; the manager wrote none --------------------
    events = read_events(Path(real_env / "events" / "events.jsonl"))
    run_events = [e for e in events if e["run_id"] == res.ingest_run_id
                  and e["type"] in ("run_completed", "run_failed")]
    assert [e["type"] for e in run_events] == ["run_completed"]
    assert not [e for e in events if e["attrs"].get("emitted_by") == EMITTED_BY]

    # --- proposals: counted from mcp_ingest's own refusal lines ------------------------------
    refused = [r for r in _jsonl(run_dir / "refused.jsonl")
               if f"unknown kind {off!r}" in r["reason"]]
    expected_docs = {r["item_id"] for r in refused}
    assert len(refused) >= 2 and len(expected_docs) >= 2, "the corpus must exercise the rule"
    filed = summary.proposals["filed"]
    assert [p["name"] for p in filed] == [off]
    p = filed[0]
    assert p["occurrences"] == len(refused)
    assert p["document_count"] == len(expected_docs)
    assert {d["document_id"] for d in p["documents"]} <= expected_docs

    captured = {r["item_id"]: r for r in _jsonl(run_dir / "captured.jsonl")}
    chunks = {(r["item_id"], r["chunk"]): (r["char_start"], r["char_end"])
              for r in _jsonl(run_dir / "governance_chunks.jsonl") if r.get("item_id")}
    names_by_doc: dict[str, set[str]] = {}
    for r in refused:
        name = r["reason"].split("entity ", 1)[1].split(":", 1)[0].strip("'\"")
        names_by_doc.setdefault(r["item_id"], set()).add(name)
    assert p["evidence"]
    for ptr in p["evidence"]:
        sp = SourcePointer.model_validate(ptr)
        # exactly the fields mcp_ingest.build.span_pointer mints
        assert sp.kind == "doc_span" and sp.source_id == conn
        assert sp.document_id in expected_docs
        assert sp.capture_digest == captured[sp.document_id]["sha256"]
        assert (sp.char_start, sp.char_end) in {v for k, v in chunks.items()
                                                if k[0] == sp.document_id}
        # the pointer RESOLVES: the captured text at that span holds the refused entity
        text = capture.get_text(sp.capture_digest)
        assert any(n in text[sp.char_start:sp.char_end] for n in names_by_doc[sp.document_id])

    # --- §5.8 provenance on the run record ----------------------------------------------------
    record = json.loads(Path(summary.record_path).read_text(encoding="utf-8"))
    step = record["steps"][0]
    assert step["produced_by"]["agent"] == f"adapter_agent:{spec.id}"
    assert step["produced_by"]["model_assisted"] is True
    assert step["produced_by"]["task_class"] == "entity_extraction"
    assert step["produced_by"]["model"] and record["models_used"]

    # --- health reads the same report ---------------------------------------------------------
    with deployment("common"):
        health = manager.health(spec.id)
    ch = health["connections"][0]
    assert ch["last_success_at"] is not None and ch["consecutive_failures"] == 0
    assert ch["counts"]["captured"] == report["counts"]["captured"]
