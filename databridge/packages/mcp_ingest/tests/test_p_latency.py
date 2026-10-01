"""P — ingestion latency and gaps (BUILD_PLAN §11a). Synthetic data, fake model, no network.

Every expectation is DERIVED from the corpus the test writes or from the run's own ledgers — never
a number typed in here — and each assertion has a non-vacuous companion so it cannot pass empty.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import capture
from databridge_mcp_ingest.build import BRIDGE_MAX_SPAN, chunk_spans, store_batch
from databridge_mcp_ingest.ledger import read_jsonl
from databridge_mcp_ingest.paths import DATABRIDGE_ROOT

from conftest import ENTITIES, FAKE_KEY, extraction_handler, fs_connection

_RETRIEVAL = DATABRIDGE_ROOT / "packages" / "retrieval"
if str(_RETRIEVAL) not in sys.path:
    sys.path.insert(0, str(_RETRIEVAL))


def _store(path: Path):
    from databridge_graph.store import GraphStore

    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


def _harness(handler):
    from databridge_harness import FakeBackend, Harness

    backend = FakeBackend(handler)
    return Harness(backend, profile="cloud"), backend


def _extraction_calls(backend) -> list:
    return [c for c in backend.calls if c["messages"][-1]["content"].startswith("DOCUMENT TEXT:")]


def _run(conn_id, root, store, harness, **kw):
    mi.register_connection(fs_connection(conn_id, root))
    mi.propose_plan(conn_id)
    return mi.run_ingest(conn_id, store=store, harness=harness, **kw)


# --- (1) measure: timing fields -----------------------------------------------------------------
def test_every_stage_and_item_carries_non_negative_timings(walnut_env, corpus, common_vocab,
                                                           tmp_path):
    root, _ = corpus
    harness, backend = _harness(extraction_handler([]))
    report = _run("timed", root, _store(tmp_path / "g.db"), harness)
    run_dir = Path(report.run_dir)
    assert report.status == "ok", report.message

    stages = json.loads((run_dir / "stages.json").read_text(encoding="utf-8"))
    for stage in ("collect", "extract", "screen", "build", "index", "verify"):
        assert stages[stage]["seconds"] >= 0, stage
        assert isinstance(stages[stage]["breakdown"], dict), stage

    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    buckets = rep["timings"]["buckets"]
    for name in ("collect.mcp_connect", "collect.mcp_list", "collect.mcp_read",
                 "collect.capture_write", "extract.text", "screen.document",
                 "build.prepare_and_chunk_screen", "build.model_wait", "build.graph_write",
                 "verify.reconcile"):
        assert name in buckets, name
    assert all(b["ms"] >= 0 and b["n"] >= 0 for b in buckets.values())
    # the breakdown in stages.json is the same numbers, grouped by stage
    assert stages["collect"]["breakdown"]["mcp_read"] == buckets["collect.mcp_read"]

    # model metering agrees with what the backend actually saw
    model = rep["timings"]["model"]
    assert model["calls"] == len(_extraction_calls(backend)) > 0
    assert model["tokens"] > 0 and model["latency_ms_sum"] >= 0

    rows = read_jsonl(run_dir / "timings.jsonl")
    assert rows and all(r["ms"] >= 0 for r in rows)
    captured = {r["item_id"] for r in read_jsonl(run_dir / "captured.jsonl")}
    assert captured <= {r["item_id"] for r in rows if r["stage"] == "collect"}
    built = {r["item_id"] for r in read_jsonl(run_dir / "build.jsonl")}
    assert built and built <= {r["item_id"] for r in rows if r["stage"] == "build"}


# --- (2) the model-output cache -----------------------------------------------------------------
def test_a_second_ingest_of_unchanged_content_makes_zero_model_calls(walnut_env, corpus,
                                                                    common_vocab, tmp_path):
    root, _ = corpus
    h1, b1 = _harness(extraction_handler([]))
    first = _run("cache-a", root, _store(tmp_path / "a.db"), h1)
    sent_first = len(_extraction_calls(b1))
    assert sent_first > 0
    assert first.timings["model"]["calls"] == sent_first

    # a NEW harness (empty in-memory cache), a new source (no incremental state), a new graph:
    # only the on-disk cache can explain zero calls
    h2, b2 = _harness(extraction_handler([]))
    second = _run("cache-b", root, _store(tmp_path / "b.db"), h2)
    assert b2.call_count == 0
    assert second.timings["model"]["disk_cache_hits"] == sent_first
    # ... and the graph built from cached output is the same graph
    assert second.counts["entities"] == first.counts["entities"] > 0
    assert second.counts["pointers"] == second.counts["pointers_resolved"]

    # switching the cache off really calls the model again
    h3, b3 = _harness(extraction_handler([]))
    _run("cache-c", root, _store(tmp_path / "c.db"), h3, cache=False)
    assert len(_extraction_calls(b3)) == sent_first


def test_the_cache_misses_when_the_chunk_or_the_vocabulary_changes(walnut_env, tmp_path,
                                                                   common_vocab):
    root = tmp_path / "docs"
    (root / "a").mkdir(parents=True)
    note = root / "a" / "note.md"
    note.write_text("Aldermoor Logistics weekly note.\n", encoding="utf-8")
    h1, b1 = _harness(extraction_handler([]))
    _run("miss", root, _store(tmp_path / "g.db"), h1)
    assert len(_extraction_calls(b1)) == 1
    note.write_text("Aldermoor Logistics weekly note, revised by Tomasz Brennick.\n",
                    encoding="utf-8")
    h2, b2 = _harness(extraction_handler([]))
    _run("miss", root, _store(tmp_path / "g.db"), h2)
    assert len(_extraction_calls(b2)) == 1                       # changed chunk → one new call

    from databridge_graph.packs import deployment
    with deployment("workspace") as widened:
        assert set(widened.node_kinds) != set(common_vocab.node_kinds)
        h3, b3 = _harness(extraction_handler([]))
        _run("miss2", root, _store(tmp_path / "h.db"), h3)
    assert len(_extraction_calls(b3)) == 1                       # new vocabulary → new key


# --- (2) concurrency is correct first ------------------------------------------------------------
def _jittery(calls: list):
    """The conftest extraction handler, with a per-text delay so completion order differs from
    submission order under a pool."""
    inner = extraction_handler(calls)

    def handler(*, model, messages, guided_json):
        time.sleep((hash(messages[-1]["content"]) % 7) / 400.0)
        return inner(model=model, messages=messages, guided_json=guided_json)
    return handler


def _graph_snapshot(store) -> dict:
    def ptrs(eid):
        return sorted(p.model_dump_json() for p in store.sources_for(eid))

    nodes = {(r["id"], r["kind"], r["label"], r["canonical_id"], r["attrs_json"],
              tuple(ptrs(r["id"]))) for r in store.open_nodes()}
    edges = {(r["id"], r["src_id"], r["dst_id"], r["kind"], tuple(ptrs(r["id"])))
             for r in store.open_edges()}
    return {"nodes": nodes, "edges": edges}


def _big_corpus(root: Path) -> None:
    from conftest import write_corpus

    write_corpus(root)
    filler = ("Routes and depots are reviewed each week by the planning group, and every change "
              "is written down. ")
    for i in range(6):
        body = (f"Report {i}. Aldermoor Logistics met Quillon Freight. " + filler * (8 + 5 * i)
                + "Tomasz Brennick signed off. " + filler * 6 + "Priya Venkataraman agreed.\n")
        (root / "reports" / f"r{i}").mkdir(parents=True, exist_ok=True)
        (root / "reports" / f"r{i}" / "report.md").write_text(body, encoding="utf-8")


def test_concurrent_extraction_writes_the_same_graph_as_serial(walnut_env, tmp_path,
                                                               common_vocab, monkeypatch):
    root = tmp_path / "big"
    _big_corpus(root)
    serial_store, pool_store = _store(tmp_path / "s.db"), _store(tmp_path / "p.db")
    h1, b1 = _harness(_jittery([]))
    serial = _run("same", root, serial_store, h1, concurrency=1, cache=False)
    # the SAME connection (item ids hash the source id) with fresh run state, into a fresh graph
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "ingest-pooled"))
    h2, b2 = _harness(_jittery([]))
    pooled = mi.run_ingest("same", store=pool_store, harness=h2, concurrency=4, cache=False)
    assert serial.status == pooled.status == "ok"
    assert serial.run_dir != pooled.run_dir
    multi_chunk = [r for r in read_jsonl(Path(serial.run_dir) / "build.jsonl")
                   if r.get("chunks", 0) > 1]
    assert multi_chunk, "the corpus must exercise more than one chunk per document"
    assert len(_extraction_calls(b1)) == len(_extraction_calls(b2)) > len(multi_chunk)

    a, b = _graph_snapshot(serial_store), _graph_snapshot(pool_store)
    assert a["nodes"] and a["edges"]
    assert a["nodes"] == b["nodes"]
    assert a["edges"] == b["edges"]
    assert serial.counts["pointers"] == pooled.counts["pointers"] > 0


# --- (3) cross-chunk relations ------------------------------------------------------------------
def _relation_handler(pairs: dict, calls: list):
    """Entities as the conftest handler; plus `rel` between two names whenever BOTH are in the
    text it was sent — so a relation is only proposed where the evidence could exist."""
    def handler(*, model, messages, guided_json):
        calls.append(messages)
        last = messages[-1]["content"]
        if not last.startswith("DOCUMENT TEXT:\n"):
            return json.dumps({})
        text = last[len("DOCUMENT TEXT:\n"):]
        ents = [{"name": n, "kind": k, "char_start": text.find(n),
                 "char_end": text.find(n) + len(n)} for n, k in ENTITIES.items() if n in text]
        rels = [{"src_name": s, "rel_type": r, "dst_name": d,
                 "evidence_char_start": min(text.find(s), text.find(d)),
                 "evidence_char_end": max(text.find(s) + len(s), text.find(d) + len(d))}
                for (s, d), r in pairs.items() if s in text and d in text]
        return json.dumps({"entities": ents, "relations": rels})
    return handler


def _planted(root: Path) -> tuple[str, str, str]:
    """A document where a person ends chunk 0 and an organisation opens chunk 1, their joint
    span within BRIDGE_MAX_SPAN but in NO common chunk. Positions are checked, not assumed."""
    person, org = "Priya Venkataraman", "Aldermoor Logistics"
    filler = "The weekly depot schedule was reviewed and every route change was logged. "
    head = "# Planning memo\n\n" + filler * 19
    text = head + person + " was asked to act as coordinator for" + " the regional work." * 20 \
        + " " + org + " confirmed the arrangement. " + filler * 12
    spans = chunk_spans(text)
    p0, o0 = text.index(person), text.index(org)
    holding = lambda s, e: [i for i, (a, b) in enumerate(spans) if a <= s and e <= b]  # noqa
    in_p, in_o = holding(p0, p0 + len(person)), holding(o0, o0 + len(org))
    assert len(spans) >= 2 and in_p and in_o, (spans, p0, o0)
    assert not set(in_p) & set(in_o), "planted pair must not share a chunk"
    assert max(in_o) - min(in_p) == 1, "planted pair must sit in ADJACENT chunks"
    assert (o0 + len(org)) - p0 <= BRIDGE_MAX_SPAN        # evidence the extractor accepts
    (root / "memos").mkdir(parents=True)
    (root / "memos" / "memo.md").write_text(text, encoding="utf-8")
    return person, org, text


@pytest.mark.parametrize("bridges", [True, False])
def test_a_relation_whose_ends_fall_in_adjacent_chunks_is_recovered(walnut_env, tmp_path,
                                                                    common_vocab, bridges):
    root = tmp_path / "planted"
    person, org, text = _planted(root)
    calls: list = []
    harness, _ = _harness(_relation_handler({(person, org): "acts_as"}, calls))
    store = _store(tmp_path / "g.db")
    report = _run(f"bridge-{str(bridges).lower()}", root, store, harness, bridges=bridges)
    assert report.status == "ok" and report.verify["ok"], report.verify["problems"]
    run_dir = Path(report.run_dir)
    edges = [e for e in store.open_edges() if e["kind"] == "acts_as"]
    bridge_rows = [r for r in read_jsonl(run_dir / "governance_chunks.jsonl") if r.get("bridge")]
    if not bridges:
        assert edges == [] and bridge_rows == []                   # the gap is real
        return
    assert len(edges) == 1 and bridge_rows and bridge_rows[0]["decision"] == "allow"
    [ptr] = store.sources_for(edges[0]["id"])
    evidence = capture.get_text(ptr.capture_digest)[ptr.char_start:ptr.char_end]
    assert person in evidence and org in evidence                 # grounded in the document
    assert evidence in text
    # the bridge was screened and ledgered like any chunk, so model calls == allowed windows
    allowed = [r for r in read_jsonl(run_dir / "governance_chunks.jsonl")
               if r.get("item_id") and r["decision"] == "allow"]
    assert len([c for c in calls if c[-1]["content"].startswith("DOCUMENT TEXT:")]) == \
        len(allowed)
    built = read_jsonl(run_dir / "build.jsonl")
    assert sum(r.get("bridge_relations", 0) for r in built) == 1


def test_no_bridge_call_when_no_cross_chunk_pair_exists(walnut_env, corpus, common_vocab,
                                                        tmp_path):
    root, _ = corpus
    harness, _ = _harness(extraction_handler([]))
    report = _run("nobridge", root, _store(tmp_path / "g.db"), harness)
    built = read_jsonl(Path(report.run_dir) / "build.jsonl")
    assert any(r.get("chunks", 0) > 1 for r in built)
    assert sum(r.get("bridges", 0) for r in built) == 0


# --- (3) item_removed closes, never deletes ------------------------------------------------------
def test_item_removed_closes_what_only_it_evidenced_and_deletes_nothing(walnut_env, corpus,
                                                                        common_vocab, tmp_path):
    root, outcomes = corpus
    store = _store(tmp_path / "g.db")
    harness, _ = _harness(extraction_handler([]))
    first = _run("rm", root, store, harness)
    gone_rel = "projects/alpha/notes.txt"
    gone_text = (root / gone_rel).read_text(encoding="utf-8")
    others = "".join((root / r).read_text(encoding="utf-8") for r, o in outcomes.items()
                     if o == "collect" and r != gone_rel and "deploy" not in r)
    only_here = {n for n in ENTITIES if n in gone_text and n not in others}
    shared = {n for n in ENTITIES if n in gone_text and n in others}
    assert only_here and shared, "the corpus must have both kinds of entity for this test"

    gone_item = next(r for r in read_jsonl(Path(first.run_dir) / "build.jsonl")
                     if r["uri"].replace("\\", "/").endswith(gone_rel))
    rows_before = {t: store.fetchone(f"SELECT COUNT(*) c FROM {t}")["c"]
                   for t in ("nodes", "edges", "sources")}
    label_to_id = {r["label"]: r["id"] for r in store.open_nodes()}

    (root / gone_rel).unlink()
    second = mi.run_ingest("rm", store=store, harness=harness)
    assert second.status == "ok"

    # nothing was deleted: every table only grew (closing is an UPDATE of system_to)
    for t, n in rows_before.items():
        assert store.fetchone(f"SELECT COUNT(*) c FROM {t}")["c"] >= n, t

    closed = read_jsonl(Path(second.run_dir) / "removed.jsonl")
    assert [r["item_id"] for r in closed] == [gone_item["item_id"]]
    assert gone_item["anchor"] in closed[0]["nodes_closed"]
    for name in only_here:
        nid = label_to_id[name]
        assert store.open_node_row(nid) is None, name
        versions = store.all_node_versions(nid)
        assert versions and all(v["system_to"] is not None for v in versions)
    for name in shared:
        assert store.open_node_row(label_to_id[name]) is not None, name
    assert store.open_node_row(gone_item["anchor"]) is None
    assert not [e for e in store.open_edges()
                if gone_item["anchor"] in (e["src_id"], e["dst_id"])]
    ev = [e for e in read_jsonl(Path(walnut_env["WALNUT_EVENTS_PATH"]))
          if e["type"] == "item_removed"]
    assert len(ev) == 1 and ev[0]["attrs"]["nodes_closed"] == len(closed[0]["nodes_closed"])


# --- blocked items never reach the model ---------------------------------------------------------
@pytest.mark.parametrize("workers", [1, 4])
def test_items_the_screen_does_not_allow_never_reach_the_model(walnut_env, corpus, common_vocab,
                                                               tmp_path, workers):
    root, _ = corpus
    harness, backend = _harness(extraction_handler([]))
    report = _run(f"gate{workers}", root, _store(tmp_path / "g.db"), harness,
                  concurrency=workers)
    gov = read_jsonl(Path(report.run_dir) / "governance.jsonl")
    not_allowed = [g for g in gov if g["decision"] != "allow"]
    assert not_allowed, "the corpus plants a blocked item"
    sent = "\n".join(c["messages"][-1]["content"] for c in backend.calls)
    for g in not_allowed:
        text = capture.get_text(g["sha256"])
        distinctive = max(text.splitlines(), key=len)
        assert distinctive and distinctive not in sent, g["uri"]
    assert FAKE_KEY.splitlines()[1] not in sent
    built_ids = {r["item_id"] for r in read_jsonl(Path(report.run_dir) / "build.jsonl")}
    assert not built_ids & {g["item_id"] for g in not_allowed}
    assert report.timings["model"]["calls"] == len(_extraction_calls(backend)) > 0


# --- docling cache (unit) ------------------------------------------------------------------------
def test_conversions_are_cached_by_content_hash(walnut_env, monkeypatch):
    from databridge_mcp_ingest import extract

    seen = []

    def fake(data, *, name, mime=None, source_id="capture"):
        seen.append(name)
        return extract.Extracted(text=f"converted {len(data)} bytes", extractor="docling:fake")

    monkeypatch.setattr(extract, "extract_text", fake)
    data = b"%PDF-1.4 synthetic"
    sha = capture.sha256_bytes(data)
    a, hit_a = extract.extract_text_cached(data, sha=sha, name="a/one.pdf")
    b, hit_b = extract.extract_text_cached(data, sha=sha, name="b/two.pdf")
    assert (hit_a, hit_b) == (False, True) and len(seen) == 1
    assert a == b
    assert extract.convert_cache_path(sha, "pdf").is_file()
    monkeypatch.setattr(extract, "CONVERT_CACHE_VERSION", extract.CONVERT_CACHE_VERSION + 1)
    _, hit_c = extract.extract_text_cached(data, sha=sha, name="c.pdf")
    assert hit_c is False and len(seen) == 2                      # a new converter version
    t, hit_t = extract.extract_text_cached(b"plain", sha=capture.sha256_bytes(b"plain"),
                                           name="x.txt")
    assert hit_t is False and len(seen) == 3 and seen[-1] == "x.txt"  # text is never cached
    assert not extract.convert_cache_path(capture.sha256_bytes(b"plain"), "text").exists()


# --- one transaction per document ----------------------------------------------------------------
def test_a_failure_inside_a_document_batch_leaves_nothing_half_written(tmp_path):
    from databridge_core import SourcePointer
    from databridge_graph.kinds import NodeKind
    from databridge_graph.packs import deployment
    from databridge_graph.schema import GraphNode, node_id

    store = _store(tmp_path / "g.db")
    with deployment("common"):
        ptr = SourcePointer(source_id="s", kind="doc_span", document_id="d", char_start=0,
                            char_end=3)
        node = GraphNode(id=node_id(NodeKind("person"), "abc"), kind=NodeKind("person"),
                         label="abc", sources=[ptr], valid_from=store.now(),
                         system_from=store.now())
        with pytest.raises(RuntimeError):
            with store_batch(store):
                store.add_node(node)
                assert store.open_node_row(node.id) is not None    # visible inside the batch
                raise RuntimeError("boom")
        assert store.open_node_row(node.id) is None
        other = sqlite3.connect(tmp_path / "g.db")
        assert other.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
        with store_batch(store):
            store.add_node(node)
        assert other.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
        other.close()


# --- retrieval hook -------------------------------------------------------------------------------
def test_passages_hook_is_a_recorded_no_op_when_off(walnut_env, corpus, common_vocab, tmp_path):
    root, _ = corpus
    harness, _ = _harness(extraction_handler([]))
    report = _run("idx-off", root, _store(tmp_path / "g.db"), harness)
    idx = json.loads((Path(report.run_dir) / "index.json").read_text(encoding="utf-8"))
    assert idx["status"] == "skipped" and "passages" in idx["reason"]
    assert any(n.startswith("passages: skipped") for n in report.notes)


def test_passages_hook_indexes_every_built_document(walnut_env, corpus, common_vocab, tmp_path,
                                                    monkeypatch):
    pytest.importorskip("databridge_retrieval")
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    root, _ = corpus
    harness, _ = _harness(extraction_handler([]))
    report = _run("idx-on", root, _store(tmp_path / "g.db"), harness, passages=True)
    idx = json.loads((Path(report.run_dir) / "index.json").read_text(encoding="utf-8"))
    docs = [r for r in read_jsonl(Path(report.run_dir) / "build.jsonl")
            if r["kind"] == "document"]
    assert idx["status"] == "ok", idx
    assert idx["indexed"] + idx["unchanged"] + sum(idx["other"].values()) == len(docs) > 0
    assert idx["indexed"] > 0
    assert "index.retrieval" in report.timings["buckets"]


# --- sqlite ----------------------------------------------------------------------------------------
def test_the_local_store_is_written_in_wal_mode(walnut_env, corpus, common_vocab, tmp_path,
                                                monkeypatch):
    root, _ = corpus
    store = _store(tmp_path / "g.db")
    report = _run("wal", root, store, None)
    assert store.fetchone("PRAGMA journal_mode")[0].lower() == "wal"
    assert any("journal_mode=wal" in n for n in report.notes)
    monkeypatch.setenv("WALNUT_INGEST_SQLITE_WAL", "0")
    other = _store(tmp_path / "h.db")
    _run("nowal", root, other, None)
    assert other.fetchone("PRAGMA journal_mode")[0].lower() != "wal"


# --- capture metadata extras ---------------------------------------------------------------------
def test_capture_meta_keeps_the_five_and_extra_provenance(walnut_env):
    sha = capture.put_bytes(b"exported doc")
    capture.put_meta(sha, {"source": "s", "uri": "u", "mime": "text/plain", "title": None,
                           "retrieved_at": "t", "converter": "export:text",
                           "derived_from": {"mime": "application/vnd.google-apps.document"},
                           "not_json": threading.Lock()})
    meta = capture.get_meta(sha)
    assert set(capture.META_KEYS) <= set(meta) and meta["title"] is None
    assert meta["converter"] == "export:text" and "not_json" not in meta
