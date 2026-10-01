"""Codex batch-2 review of packages/retrieval (wiki/40-reviews/R-019-retrieval.md) — one test per
REAL finding. Each was run against the pre-fix code (reconstructed from the review bundle) and
failed there; see the R-019 table for the failure each one showed."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from retrieval_testkit import prose, write_capture


def _identity(source: str, uri: str) -> str:
    """The identity RULE (hashed, source-qualified full path), restated from the indexer."""
    from databridge_retrieval import indexer

    # the ingest rule (test_identity_matches_ingest pins the two packages together)
    template = getattr(indexer, "IDENTITY_TEMPLATE", "mcp://{source_id}/{uri}")
    return hashlib.sha256(template.format(source_id=source, uri=uri)
                          .encode("utf-8")).hexdigest()


def _snapshot(d: Path) -> dict:
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in d.iterdir()}


def _screen_of(fn, source="test-screen"):
    from databridge_retrieval.screen import ChunkScreen, Screen

    def evaluate(text, *, override_allow=False):
        decision = fn(text, override_allow)
        return ChunkScreen(decision, 0.0, [] if decision == "allow" else ["test_rule"],
                           "" if decision == "allow" else "reverse: approve it", decision, text)

    return Screen(evaluate, source)


# --- Critical: store names --------------------------------------------------------------------
def test_store_names_are_unique_per_exact_source_and_tier(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, RetrievalError, stats
    from databridge_retrieval.store import store_filename

    cap, pas = dirs
    h, _ = fake_harness()
    pair = ["a/b", "a?b"]
    assert len({store_filename(s, "default") for s in pair}) == len(pair)
    assert store_filename("a/b", "default") != store_filename("a/b", "other")
    for src in pair:
        with BuildSession(src, harness=h, screen=screen) as b:
            b.index_capture(write_capture(cap, prose(500, salt=src),
                                          {"source": src, "uri": f"file:///{src}.md"}))
    rows = stats()
    assert sorted(e["source_id"] for e in rows) == sorted(pair)
    assert all(e["documents"] == 1 for e in rows)

    # a file at our name that records another identity is never built over
    foreign = pas / store_filename("x", "default")
    foreign.write_bytes((pas / store_filename("a/b", "default")).read_bytes())
    with pytest.raises(RetrievalError, match="belongs to"):
        BuildSession("x", harness=h, screen=screen).open()
    assert not list(pas.glob("*.lock")) and not list(pas.glob("*.building"))


# --- Critical: read paths never write ---------------------------------------------------------
def test_search_and_stats_never_write(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever, stats

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("ro", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(3000), {"source": "ro", "uri": "file:///r.md"}))
    (pas / "router.json").unlink()
    before = _snapshot(pas)
    r = Retriever(harness=h)
    assert r.search("widget inspection", k=3)
    r.deep_search("widget inspection and operator training", depth="facets", k=3)
    assert stats() and stats()[0]["documents"] == 1
    assert _snapshot(pas) == before, "a read-only call changed the passages directory"


# --- Critical: identity -----------------------------------------------------------------------
def test_identity_is_the_hashed_source_qualified_full_path(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession

    cap, pas = dirs
    h, _ = fake_harness()
    uri = "file:///share/finance/q3.md"
    good = write_capture(cap, prose(700, "g"), {"source": "id", "uri": uri})
    other = write_capture(cap, prose(700, "o"), {"source": "id", "uri": "file:///share/o.md"})
    nopath = write_capture(cap, prose(700, "n"), {"source": "id"})
    b = BuildSession("id", harness=h, screen=screen).open()
    try:
        r = b.index_capture(good, {"item_id": _identity("id", uri)})
        assert r.status == "indexed" and r.doc_id == _identity("id", uri)
        # a caller id that is not the hashed path is refused, never adopted
        clash = b.index_capture(other, {"item_id": r.doc_id})
        assert clash.status == "identity_mismatch" and clash.chunks == 0
        assert b.index_capture(nopath).status == "missing_path"
        docs = [x[0] for x in b.conn.execute("SELECT doc_id FROM documents")]
        assert docs == [r.doc_id]
    finally:
        b.abort()


def test_identity_matches_ingest(dirs):
    """The two packages must name a document identically, or a passage pointer and a graph
    pointer to the same document would disagree."""
    ingest = Path(__file__).resolve().parents[2] / "mcp_ingest"
    sys.path.insert(0, str(ingest))
    try:
        crawl = pytest.importorskip("databridge_mcp_ingest.crawl")
    finally:
        sys.path.remove(str(ingest))
    from databridge_retrieval import doc_id_for

    for src, uri in [("files", "file:///a/b c.md"), ("pg", "public.invoices"),
                     ("s", "mcpj://res/x/abc")]:
        assert doc_id_for({"uri": uri}, source_id=src) == crawl.item_identity(src, uri)


# --- Critical: every published pointer resolves ------------------------------------------------
def test_publication_guarantees_every_pointer_resolves(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever
    from databridge_retrieval import store as st
    from databridge_retrieval.store import store_filename

    cap, pas = dirs
    h, _ = fake_harness()
    text_a = prose(2500, "a") + "\n\nQuestions to jane.doe@example.com please."
    sha_a = write_capture(cap, text_a, {"source": "pv", "uri": "file:///a.md"})
    sha_b = write_capture(cap, prose(2500, "b"), {"source": "pv", "uri": "file:///b.md"})
    with BuildSession("pv", harness=h, screen=screen) as b:
        b.index_capture(sha_a)
        doc_b = b.index_capture(sha_b).doc_id

    (cap / sha_b[:2] / f"{sha_b}.txt").unlink()           # B's capture text disappears
    sha_c = write_capture(cap, prose(2500, "c"), {"source": "pv", "uri": "file:///c.md"})
    b = BuildSession("pv", harness=h, screen=screen).open()
    b.index_capture(sha_c)
    # text that is not the capture's own .txt can never become pointers
    assert b.index_text("not the capture", capture_digest=sha_c,
                        meta={"uri": "file:///c2.md"}).status == "capture_mismatch"
    rep = b.publish()
    assert [d["doc_id"] for d in rep.dropped] == [doc_b]

    res = Retriever(harness=h).search("widget please", k=200, mode="keyword")
    assert res and doc_b not in {p.doc_id for p in res}
    masked = 0
    for p in res:
        txt = (cap / p.pointer.capture_digest[:2] / f"{p.pointer.capture_digest}.txt")
        span = txt.read_text(encoding="utf-8")[p.pointer.char_start:p.pointer.char_end]
        if p.pointer.derivation is None:
            assert span == p.text
        else:
            masked += 1
            assert p.pointer.derivation.startswith("chunk_screen.mask")
            assert screen.evaluate(span).masked_text == p.text != span
    assert masked >= 1, "the masked passage is present, so the derivation check is not vacuous"

    conn = st.connect(pas / store_filename("pv", "default"), readonly=True)
    events = [r[0] for r in conn.execute(
        "SELECT event FROM exclusion_events WHERE doc_id=?", (doc_b,))]
    conn.close()
    assert events == ["document_unresolvable"]


# --- Critical: override authority ---------------------------------------------------------------
def test_override_authority_is_never_read_from_metadata(dirs, fake_harness):
    from databridge_retrieval import BuildSession

    cap, pas = dirs
    h, _ = fake_harness()
    gate = _screen_of(lambda t, ov: "allow" if ov or "REVIEWME" not in t else "review")
    docs = {name: write_capture(cap, prose(300, name) + " REVIEWME",
                                {"source": "ov", "uri": f"file:///{name}.md",
                                 **({"override_allow": True} if name == "capjson" else {})})
            for name in ("capjson", "callermeta", "kwarg")}
    plain = write_capture(cap, prose(300, "plain"), {"source": "ov", "uri": "file:///plain.md"})
    with BuildSession("ov", harness=h, screen=gate) as b:
        b.index_capture(plain)              # the store is not empty, so it publishes
        from_capture = b.index_capture(docs["capjson"])
        from_meta = b.index_capture(docs["callermeta"], {"override_allow": True})
        from_kwarg = b.index_capture(docs["kwarg"], override_allow=True)
    assert from_capture.chunks == 0 and from_capture.excluded >= 1
    assert from_meta.chunks == 0 and from_meta.excluded >= 1
    # R-019 round 3 (Critical #1): a caller keyword is not an approval record either — refused
    assert from_kwarg.status == "approval_unverified" and from_kwarg.chunks == 0


# --- Critical: exclusion history is append-only -------------------------------------------------
def test_exclusion_history_is_append_only(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession
    from databridge_retrieval import store as st
    from databridge_retrieval.store import store_filename

    cap, pas = dirs
    h, _ = fake_harness()
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    uri = "file:///k.md"
    sha1 = write_capture(cap, prose(2500, "k") + "\n\n" + key + "\n\n" + prose(2500, "l"),
                         {"source": "ap", "uri": uri})
    keep = write_capture(cap, prose(900, "keep"), {"source": "ap", "uri": "file:///keep.md"})
    with BuildSession("ap", harness=h, screen=screen) as b:
        first = b.index_capture(sha1)
        b.index_capture(keep)
    assert first.excluded >= 1
    store = pas / store_filename("ap", "default")

    def history():
        conn = st.connect(store, readonly=True)
        try:
            return [tuple(r) for r in conn.execute(
                "SELECT event, doc_id, chunk_index FROM exclusion_events ORDER BY event_id")]
        finally:
            conn.close()

    h1 = history()
    assert len(h1) == first.excluded
    sha2 = write_capture(cap, prose(5000, "clean"), {"source": "ap", "uri": uri})
    with BuildSession("ap", harness=h, screen=screen) as b:
        assert b.index_capture(sha2).excluded == 0      # the key is gone from the new version
    assert history() == h1, "re-indexing erased the record of what was excluded before"
    with BuildSession("ap", harness=h, screen=screen) as b:
        b.retain_only([_identity("ap", "file:///keep.md")])
    h3 = history()
    assert h3[: len(h1)] == h1 and h3[-1][:2] == ("document_removed", first.doc_id)

    conn = sqlite3.connect(str(store))
    try:
        for sql in ("DELETE FROM exclusion_events", "UPDATE exclusion_events SET event='x'"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
    finally:
        conn.close()


# --- High: embedding dimensions ------------------------------------------------------------------
def test_embedding_dimension_is_enforced_and_routed(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    calls = {"n": 0}

    def drifting(texts):
        calls["n"] += 1
        dim = 3 if calls["n"] == 1 else 4
        return [[1.0] + [0.0] * (dim - 1) for _ in texts]

    h, _ = fake_harness(embedder=drifting)
    shas = [write_capture(cap, prose(400, str(i)), {"source": "dm", "uri": f"file:///{i}"})
            for i in range(2)]
    b = BuildSession("dm", harness=h, screen=screen, embed_flush=1).open()
    try:
        with pytest.raises(RuntimeError, match="dimension changed"):
            for s in shas:
                b.index_capture(s)
            b.flush()
    finally:
        b.abort()

    h3, _ = fake_harness(embedder=lambda ts: [[1.0, 0.0, 0.0] for _ in ts])
    with BuildSession("dm", harness=h3, screen=screen) as b:
        for s in shas:
            b.index_capture(s)
    h4, _ = fake_harness(embedder=lambda ts: [[1.0, 0.0, 0.0, 0.0] for _ in ts])
    res = Retriever(harness=h4).search("widget", k=3, mode="hybrid")     # no crash
    assert any("dim" in s["reason"] for s in res.coverage["skipped"])
    assert res, "the keyword leg still answers when the vector leg cannot"


# --- High: the lock --------------------------------------------------------------------------------
def test_a_live_lock_is_never_taken_over_however_old(dirs, fake_harness, screen):
    from databridge_retrieval import BuildInProgress, BuildSession

    cap, pas = dirs
    h, _ = fake_harness()
    probe = BuildSession("lk", harness=h, screen=screen)
    pas.mkdir(parents=True, exist_ok=True)
    probe.lock_path.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}),
                               encoding="utf-8")
    old = time.time() - 30 * 3600
    os.utime(probe.lock_path, (old, old))
    with pytest.raises(BuildInProgress):
        BuildSession("lk", harness=h, screen=screen).open()

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    probe.lock_path.write_text(json.dumps({"pid": dead.pid, "host": socket.gethostname()}),
                               encoding="utf-8")
    orphan = pas / (probe.published_path.name + ".deadbeef.building")
    orphan.write_bytes(b"x")
    b = BuildSession("lk", harness=h, screen=screen).open()    # fresh mtime, dead owner: taken
    assert not orphan.exists(), "a dead owner's staging file is reclaimed"
    assert b.staging_path != BuildSession("lk", harness=h, screen=screen).staging_path
    b.abort()
    assert not probe.lock_path.exists()


# --- High: one loader per matrix ---------------------------------------------------------------------
def test_concurrent_cache_misses_load_the_matrix_once(dirs, fake_harness, screen, monkeypatch):
    from databridge_retrieval import BuildSession
    from databridge_retrieval import vectors
    from databridge_retrieval.store import store_filename

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("mc", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(3000), {"source": "mc", "uri": "file:///m"}))
    real, loads = vectors.load_matrix, []

    def slow(conn, model):
        loads.append(model)
        time.sleep(0.2)
        return real(conn, model)

    monkeypatch.setattr(vectors, "load_matrix", slow)
    path = pas / store_filename("mc", "default")
    model = h.embed(["x"]).model
    n = 6
    barrier, got = threading.Barrier(n), []
    workers = [threading.Thread(target=lambda: (barrier.wait(),
                                                got.append(vectors.MATRICES.get(path, model))))
               for _ in range(n)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    assert len(got) == n and len(loads) == 1
    assert len({id(m) for _, m in got}) == 1, "every caller shares the one copy"


# --- High: keyword failures are coverage, not silence ---------------------------------------------
def test_keyword_leg_failure_is_reported_not_counted_as_searched(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever
    from databridge_retrieval.store import store_filename

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("kf", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(900), {"source": "kf", "uri": "file:///k"}))
    conn = sqlite3.connect(str(pas / store_filename("kf", "default")))
    for trig in ("chunks_ai", "chunks_ad", "chunks_au"):
        conn.execute(f"DROP TRIGGER {trig}")
    conn.execute("DROP TABLE chunks_fts")
    conn.commit()
    conn.close()
    res = Retriever(harness=h).search("widget", k=3, mode="keyword")
    assert res == []
    assert not [s for s in res.coverage["searched"] if s["leg"] == "keyword"]
    assert any("keyword leg failed" in s["reason"] for s in res.coverage["skipped"])
    assert any("keyword leg failed" in n for n in res.notices)


# --- High: kinds filter before the vector cut --------------------------------------------------------
def test_kind_filter_applies_before_vector_ranking(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    marker = "zzcsvmarker"
    h, _ = fake_harness(embedder=lambda ts: [[0.3, 1.0] if marker in t else [1.0, 0.0]
                                             for t in ts])
    k = 1
    pool = max(4 * k, 40)                                  # the vector leg's per-store cut
    with BuildSession("kd", harness=h, screen=screen) as b:
        for i in range(pool + 5):                          # more prose than the cut holds
            b.index_capture(write_capture(cap, prose(200, f"p{i}"),
                                          {"source": "kd", "uri": f"file:///p{i}.md"}))
        table = b.index_capture(write_capture(cap, f"id,note\n1,{marker} widget row\n" * 8,
                                              {"source": "kd", "uri": "file:///t.csv"}))
    res = Retriever(harness=h).search("widget", k=k, kinds=["table"], mode="vector")
    assert [p.doc_id for p in res] == [table.doc_id]


# --- High: shrink guard counts retrievable documents ------------------------------------------------
def test_shrink_of_retrievable_documents_is_refused(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, PublishRefused

    cap, pas = dirs
    h, _ = fake_harness()
    n = 4
    with BuildSession("rv", harness=h, screen=screen) as b:
        for i in range(n):
            b.index_capture(write_capture(cap, prose(500, f"v1-{i}"),
                                          {"source": "rv", "uri": f"file:///{i}.md"}))
    # blocks every NEW version; a policy change re-screens the untouched doc too (R-019 r3 #2),
    # so the rule must leave it allowed for this test to reach the retrievable-floor check
    block_all = _screen_of(lambda t, ov: "block" if "v2-" in t else "allow")
    b = BuildSession("rv", harness=h, screen=block_all).open()
    for i in range(n - 1):                         # a new version of all but one, all blocked
        r = b.index_capture(write_capture(cap, prose(500, f"v2-{i}"),
                                          {"source": "rv", "uri": f"file:///{i}.md"}))
        assert r.chunks == 0
    with pytest.raises(PublishRefused, match="retrievable"):
        b.publish()


# --- Medium ---------------------------------------------------------------------------------------
def test_keyword_merge_uses_per_store_rank(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("dense", harness=h, screen=screen) as b:
        for i in range(20):
            body = ("alpha " * 30 if i < 3 else "") + prose(200, f"d{i}")
            b.index_capture(write_capture(cap, body, {"source": "dense",
                                                      "uri": f"file:///d{i}.md"}))
    with BuildSession("sparse", harness=h, screen=screen) as b:
        for i in range(2):
            b.index_capture(write_capture(cap, prose(900, f"s{i}") + " alpha",
                                          {"source": "sparse", "uri": f"file:///s{i}.md"}))
    res = Retriever(harness=h).search("alpha", k=2, mode="keyword")
    assert {p.source_id for p in res} == {"dense", "sparse"}


def test_deep_search_keeps_the_same_document_from_two_sources(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    text = prose(700, "shared")
    ids = {}
    for src in ("left", "right"):
        with BuildSession(src, harness=h, screen=screen) as b:
            sha = write_capture(cap, text, {"source": src, "uri": "file:///same.md"})
            ids[src] = b.index_capture(sha, {"uri": "file:///same.md"}).doc_id
    assert ids["left"] != ids["right"]
    res = Retriever(harness=h).deep_search("widget inspection", depth="facets", k=4)
    assert {p.source_id for p in res} == {"left", "right"}


def test_rerank_keeps_truncated_extracts_it_did_not_judge(fake_harness, monkeypatch):
    from databridge_core.provenance import SourcePointer, Trust
    from databridge_retrieval import Passage, Retriever
    import importlib

    srch = importlib.import_module("databridge_retrieval.search")

    def passage(i, text):
        ptr = SourcePointer(source_id="s", kind="doc_span", document_id="d", char_start=0,
                            char_end=len(text), capture_digest="0" * 64, trust=Trust.UNTRUSTED)
        return Passage(chunk_id=f"c{i}", doc_id="d", source_id="s", text=text, locator="",
                       approx_locator=True, score=1.0 / (i + 1), pointer=ptr)

    budget = 200            # the budget now covers the question and labels too (R-019 r3 #8)
    monkeypatch.setattr(srch, "RERANK_CHAR_BUDGET", budget)
    monkeypatch.setattr(srch, "RERANK_MIN_SHARE", 10)
    ps = [passage(0, "short answer"), passage(1, "x" * (budget * 3)), passage(2, "short miss")]
    h, backend = fake_harness(responses=json.dumps({"keep": [{"index": 0, "relevance": 0.9}]}))
    out, note = Retriever(harness=h)._rerank("q", ps, "brain_analysis")
    assert [p.chunk_id for p in out] == ["c0", "c1"]            # c2 was judged whole and dropped
    assert "truncated" in note


def test_k_is_validated_at_the_boundary(dirs, fake_harness):
    import importlib

    from databridge_retrieval import Retriever

    max_k = getattr(importlib.import_module("databridge_retrieval.search"), "MAX_K", 500)
    h, _ = fake_harness()
    r = Retriever(harness=h)
    for bad in (-1, 0, max_k + 1, True, 2.0):
        with pytest.raises(ValueError, match="k must be"):
            r.search("anything", k=bad)
        with pytest.raises(ValueError, match="k must be"):
            r.deep_search("anything", k=bad)
