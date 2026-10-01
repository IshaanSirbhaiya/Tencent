"""The gateway serves ONE shared, file-backed graph store (contracts C1, C2, C5).

* boots on the file ``WALNUT_GRAPH_DB_PATH`` names, and every surface's handle is the same object;
* with the clinic seed off (the production default) ``/brain/*`` is empty but working;
* rows another process commits to that file appear without a restart;
* ``/brain/ingest`` extracts with the REAL model harness when a key is set — proved by injecting a
  recording backend where ``LiteLLMBackend`` would be constructed, so the gateway's own decision
  logic runs and no live model is ever reached — and with gold when ``DATABRIDGE_FORCE_GOLD=1``;
* ingested content is hashed (not the literal "ingest") and dated on the real clock.

The repo conftest runs the suite on ``:memory:`` with the seed ON; every test here overrides
both with ``monkeypatch`` so it exercises the production defaults on a tmp file.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from fastapi.testclient import TestClient

from databridge_graph import GraphNode, upsert_node
from databridge_graph.clinic_seed import SEED_SYSTEM_TIME
from databridge_graph.extractor import gold_extraction_handler
from databridge_graph.packs import active_vocabulary
from databridge_graph.schema import NodeKind, node_id
from databridge_graph.store import GraphStore
from databridge_harness import FakeBackend

from conftest import AUTH_HEADERS
from databridge_gateway import create_app


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "graph" / "workspace.db"          # parent does not exist yet: boot makes it
    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(path))
    monkeypatch.delenv("WALNUT_LOAD_CLINIC_SEED", raising=False)
    return path


def _a_node(label: str) -> GraphNode:
    from databridge_core import SourcePointer

    kind = NodeKind(sorted(active_vocabulary().node_kinds)[0])
    t = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    ptr = SourcePointer(source_id="cli.txt", kind="doc_span", document_id="cli.txt",
                        char_start=0, char_end=len(label), trust=None)
    return GraphNode(id=node_id(kind, label.lower()), kind=kind, label=label, sources=[ptr],
                     valid_from=t, system_from=t)


def test_boots_on_one_shared_file_store_and_is_empty_but_working_without_the_seed(db_path):
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        app = c.app
        assert db_path.is_file()                                   # created, parent included
        assert app.state.graph_store is app.state.brain.store      # ONE object
        assert app.state.graph_store.path == str(db_path.resolve())

        g = c.get("/brain/graph").json()
        assert g["nodes"] == [] and g["edges"] == [] and g["stats"]["nodes"] == 0
        assert g["stats"]["truncated"] is False
        assert c.get("/brain/insights").json() == {"insights": []}
        health = c.get("/health").json()
        assert health["status"] == "ok" and health["graph_nodes"] == 0
        assert health["graph_db_path"] == str(db_path.resolve())
        # a GET must not write the clinic demo into a user's store
        anon = TestClient(c.app)                                  # no credential
        assert anon.post("/brain/graph/seed").status_code == 401  # a write: credential first
        # round 2 (R-019 main #7): GET never mutates, so the seed is not a GET route at all
        assert c.get("/brain/graph/seed").status_code == 405
        r = c.post("/brain/graph/seed", headers=AUTH_HEADERS)
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "DEMO_SEED_DISABLED"
        assert app.state.graph_store.counts()["nodes"] == 0


def test_another_process_writes_are_visible_without_a_restart(db_path):
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        other = GraphStore(str(db_path))                           # the ingest CLI's handle
        try:
            labels = ["Harbour Lantern Works", "Quillon Ferris"]
            for label in labels:
                upsert_node(other, _a_node(label))
            g = c.get("/brain/graph").json()
            assert sorted(n["label"] for n in g["nodes"]) == sorted(labels)
            r = c.post("/brain/reload", headers=AUTH_HEADERS).json()
            assert r["counts"]["nodes"] == len(labels)
            assert c.app.state.reload_graph()["nodes"] == len(labels)
        finally:
            other.close()


def test_the_clinic_seed_is_opt_in(db_path, monkeypatch):
    monkeypatch.setenv("WALNUT_LOAD_CLINIC_SEED", "1")
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        assert c.get("/health").json()["graph_nodes"] > 0
    # and it was written to the SHARED file, not a private in-memory store
    reopened = GraphStore(str(db_path))
    try:
        assert reopened.counts()["nodes"] > 0
    finally:
        reopened.close()


def test_the_graph_payload_is_bounded(db_path):
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        other = GraphStore(str(db_path))
        try:
            labels = [f"Harbour Lantern Works {i}" for i in range(5)]
            for label in labels:
                upsert_node(other, _a_node(label))
        finally:
            other.close()
        g = c.get("/brain/graph", params={"limit": 2}).json()
        assert len(g["nodes"]) == 2 and g["stats"]["truncated"] is True
        assert g["stats"]["nodes"] == len(labels)


# --- C5: the real harness on /brain/ingest -----------------------------------------------------
TEXT = "Quarterly note: Harbour Lantern Works engaged Quillon Ferris for the survey."


def _recording_backend() -> FakeBackend:
    kind = sorted(active_vocabulary().node_kinds)[0]
    i = TEXT.index("Harbour Lantern Works")
    ents = [{"name": "Harbour Lantern Works", "kind": kind, "char_start": i,
             "char_end": i + len("Harbour Lantern Works")}]
    return FakeBackend(gold_extraction_handler(ents, []))


def _boot_with_key(monkeypatch, *, force_gold: bool):
    """Set a key, and put a recording backend where the gateway would construct LiteLLM. The
    gateway's own `detect_llm` → `build_extraction_harness` decision runs untouched."""
    import databridge_harness

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if force_gold:
        monkeypatch.setenv("DATABRIDGE_FORCE_GOLD", "1")
    else:
        monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    recorder = _recording_backend()
    monkeypatch.setattr(databridge_harness, "LiteLLMBackend", lambda *a, **k: recorder)
    return recorder


def test_brain_ingest_uses_the_real_model_harness_when_a_key_is_set(db_path, monkeypatch):
    recorder = _boot_with_key(monkeypatch, force_gold=False)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        assert c.get("/health").json()["brain_ingest_backend"] == "real"
        before = len(recorder.calls)
        r = c.post("/brain/ingest", json={"source_path": "note.txt", "text": TEXT},
                   headers=AUTH_HEADERS)
        body = r.json()
        assert r.status_code == 200, body
        # the injected "real" backend did the extraction
        assert len(recorder.calls) > before
        assert body["entities_added"] == 1 and body["new_entities"] == ["Harbour Lantern Works"]

        digest = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
        assert body["content_sha256"] == digest
        store = c.app.state.graph_store
        row = next(r for r in store.open_nodes() if r["label"] == "Harbour Lantern Works")
        ptrs = store.sources_for(row["id"])
        assert ptrs and all(p.capture_digest == f"sha256:{digest}" for p in ptrs)
        assert all(TEXT[p.char_start:p.char_end] == "Harbour Lantern Works" for p in ptrs)
        # the real clock, not the frozen seed instant
        system_from = dt.datetime.fromisoformat(row["system_from"])
        now = dt.datetime.now(dt.timezone.utc)
        assert system_from > SEED_SYSTEM_TIME
        assert abs((now - system_from).total_seconds()) < 3600


def test_force_gold_keeps_ingest_on_the_gold_harness_even_with_a_key(db_path, monkeypatch):
    recorder = _boot_with_key(monkeypatch, force_gold=True)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        assert c.get("/health").json()["brain_ingest_backend"] == "gold"
        r = c.post("/brain/ingest", json={"source_path": "note.txt", "text": TEXT},
                   headers=AUTH_HEADERS)
        assert recorder.calls == []
        # R-019 main #1: the gold harness is a fixed answer for ONE registered fixture. Applied
        # to a document a person dropped in, it would "extract" whatever the gold draft names —
        # so inline text is refused, and nothing reaches the shared store.
        assert r.status_code == 503, r.text
        assert r.json()["refusal"]["code"] == "NO_EXTRACTION_MODEL"
        assert c.app.state.graph_store.counts()["nodes"] == 0
