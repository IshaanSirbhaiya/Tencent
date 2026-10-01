"""Codex ROUND-3 review of packages/retrieval (wiki/40-reviews/R-019-retrieval.md, "Round 3") — one
test per REAL finding. Each was run against the round-2 code first and failed there; the R-019
round-3 table records the failure each one showed."""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from retrieval_testkit import prose, write_capture


def _screen_of(fn, source="test-screen", calls=None):
    from databridge_retrieval.screen import ChunkScreen, Screen

    def evaluate(text, *, override_allow=False):
        if calls is not None:
            calls.append(override_allow)
        decision = fn(text, override_allow)
        return ChunkScreen(decision, 0.0, [] if decision == "allow" else ["test_rule"],
                           "" if decision == "allow" else "reverse: approve it", decision, text)

    return Screen(evaluate, source)


class _Renamed:
    """A harness whose embedding route reports ``model`` — a config change of the embed route."""

    def __init__(self, inner, model):
        self._inner, self.model = inner, model

    def embed(self, texts, **kw):
        return dataclasses.replace(self._inner.embed(texts, **kw), model=self.model)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _store(pas, source):
    from databridge_retrieval import store as st
    from databridge_retrieval.store import store_filename

    return st.connect(pas / store_filename(source, "default"), readonly=True)


# --- Critical #1: no caller keyword admits review-band content -----------------------------------
def test_no_caller_keyword_admits_review_band_content(dirs, fake_harness):
    from databridge_retrieval import BuildSession, index_capture

    cap, pas = dirs
    h, _ = fake_harness()
    calls: list[bool] = []
    gate = _screen_of(lambda t, ov: "allow" if ov or "REVIEWME" not in t else "review",
                      calls=calls)
    plain = write_capture(cap, prose(400, "p"), {"source": "gate", "uri": "file:///p.md"})
    rev = write_capture(cap, prose(300, "r") + " REVIEWME",
                        {"source": "gate", "uri": "file:///r.md"})
    with BuildSession("gate", harness=h, screen=gate) as b:
        assert b.index_capture(plain).status == "indexed"
        via_kwarg = b.index_capture(rev, override_allow=True)
        via_text = b.index_text(prose(300, "r") + " REVIEWME", capture_digest=rev,
                                meta={"uri": "file:///r.md"}, override_allow=True)
    assert via_kwarg.status == via_text.status == "approval_unverified"
    assert via_kwarg.chunks == via_text.chunks == 0
    conn = _store(pas, "gate")
    try:
        leaked = conn.execute("SELECT COUNT(*) FROM chunks WHERE content LIKE '%REVIEWME%'"
                              ).fetchone()[0]
    finally:
        conn.close()
    assert leaked == 0
    assert calls and not any(calls), "the screen was never asked to lift a review, even to mask"


def test_module_entry_point_refuses_an_unverified_override(dirs, fake_harness, monkeypatch):
    import databridge_retrieval.indexer as ix

    cap, pas = dirs
    h, _ = fake_harness()
    gate = _screen_of(lambda t, ov: "allow" if ov or "REVIEWME" not in t else "review")
    monkeypatch.setattr(ix, "load_chunk_screen", lambda *a, **k: gate)
    base = write_capture(cap, prose(400, "b"), {"source": "gate2", "uri": "file:///b.md"})
    rev = write_capture(cap, prose(300, "r") + " REVIEWME",
                        {"source": "gate2", "uri": "file:///r.md"})
    assert ix.index_capture(base, harness=h).status == "indexed"
    res = ix.index_capture(rev, harness=h, override_allow=True)
    assert res.status == "approval_unverified" and res.chunks == 0


# --- Critical #2: a screening-policy change re-screens unchanged captures ------------------------
def test_screen_policy_change_rescreens_unchanged_captures(dirs, fake_harness):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    permissive = _screen_of(lambda t, ov: "allow", "policy-v1")
    strict = _screen_of(lambda t, ov: "block" if "CONFIDENTIAL" in t else "allow", "policy-v2")
    assert permissive.digest != strict.digest
    shas = [write_capture(cap, prose(300, "a") + " CONFIDENTIAL",
                          {"source": "pol", "uri": "file:///a.md"}),
            write_capture(cap, prose(300, "b"), {"source": "pol", "uri": "file:///b.md"})]
    with BuildSession("pol", harness=h, screen=permissive) as b:
        for s in shas:
            b.index_capture(s)
    hits = lambda: [p for p in Retriever(harness=h).search("CONFIDENTIAL", k=5, mode="keyword")
                    if "CONFIDENTIAL" in p.text]  # noqa: E731
    assert hits(), "searchable under the permissive policy (non-vacuous)"

    # the pack's screen changes; nothing is re-indexed — publication alone must apply it
    with BuildSession("pol", harness=h, screen=strict):
        pass
    assert not hits(), "a now-blocked passage stayed searchable"
    conn = _store(pas, "pol")
    try:
        excluded = conn.execute("SELECT COUNT(*) FROM exclusion_events WHERE "
                                "event='chunk_excluded'").fetchone()[0]
    finally:
        conn.close()
    assert excluded >= 1

    # and the inverse: relaxed rules readmit what was excluded
    with BuildSession("pol", harness=h, screen=permissive) as b:
        for s in shas:
            b.index_capture(s)
    assert hits()


def test_local_screen_digest_follows_content_not_path(tmp_path):
    from databridge_retrieval.screen import _local

    # resolved here, not imported from ``conftest``: collected beside another package's suite,
    # ``import conftest`` can resolve to that package's conftest
    WORKSPACE_SCREEN = Path(__file__).resolve().parents[3] / "packs" / "workspace" / "screen.yaml"
    assert WORKSPACE_SCREEN.is_file()
    body = WORKSPACE_SCREEN.read_text(encoding="utf-8")
    a, b, c = tmp_path / "a.yaml", tmp_path / "b.yaml", tmp_path / "c.yaml"
    a.write_text(body, encoding="utf-8")
    b.write_text(body, encoding="utf-8")
    c.write_text(body + "\n# a comment only\n", encoding="utf-8")
    assert _local(a).digest == _local(b).digest == _local(c).digest
    import yaml

    data = yaml.safe_load(body)
    data.setdefault("thresholds", {})["review"] = float(
        (data.get("thresholds") or {}).get("review", 2)) + 1
    c.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert _local(c).digest != _local(a).digest


# --- Critical #3: unknown document type is None and recorded ------------------------------------
def test_unknown_document_type_is_none_and_recorded(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    sha = write_capture(cap, prose(500, "u"), {"source": "ty", "uri": "file:///share/README"})
    with BuildSession("ty", harness=h, screen=screen) as b:
        r = b.index_capture(sha)
    conn = _store(pas, "ty")
    try:
        doc_type = conn.execute("SELECT doc_type FROM documents WHERE doc_id=?",
                                (r.doc_id,)).fetchone()[0]
        events = [x[0] for x in conn.execute(
            "SELECT event FROM exclusion_events WHERE doc_id=?", (r.doc_id,))]
    finally:
        conn.close()
    assert doc_type is None
    assert "document_type_unknown" in events
    assert not Retriever(harness=h).search("widget", k=5, kinds=["text"])
    assert Retriever(harness=h).search("widget", k=5), "the document is still searchable"


# --- High #4: the shrink guard refuses catastrophic passage loss ---------------------------------
def test_catastrophic_chunk_loss_is_refused_but_a_small_edit_is_not(dirs, fake_harness, screen,
                                                                    monkeypatch):
    import databridge_retrieval.indexer as ix
    from databridge_retrieval import BuildSession, PublishRefused
    from databridge_retrieval.chunker import CHUNK_CHARS

    monkeypatch.setattr(ix, "SHRINK_MIN_CHUNKS", 10)
    cap, pas = dirs
    h, _ = fake_harness()
    big = write_capture(cap, prose(CHUNK_CHARS * (ix.SHRINK_MIN_CHUNKS + 3), "big"),
                        {"source": "sh", "uri": "file:///book.md"})
    with BuildSession("sh", harness=h, screen=screen) as b:
        n = b.index_capture(big).chunks
    assert n >= ix.SHRINK_MIN_CHUNKS, "the store is large enough for the rule (non-vacuous)"
    cut = write_capture(cap, prose(300, "cut"), {"source": "sh", "uri": "file:///book.md"})
    b = BuildSession("sh", harness=h, screen=screen).open()
    assert b.index_capture(cut).chunks < ix.SHRINK_FLOOR * n
    with pytest.raises(PublishRefused, match="chunks"):
        b.publish()
    # force is the explicit path for an intended shrink
    with BuildSession("sh", harness=h, screen=screen, force=True) as b:
        b.index_capture(cut)

    small = write_capture(cap, prose(CHUNK_CHARS * 4, "sm"),
                          {"source": "sm", "uri": "file:///s.md"})
    with BuildSession("sm", harness=h, screen=screen) as b:
        m = b.index_capture(small).chunks
    assert 1 < m < ix.SHRINK_MIN_CHUNKS
    with BuildSession("sm", harness=h, screen=screen) as b:       # a normal edit publishes
        b.index_capture(write_capture(cap, prose(300, "sm2"),
                                      {"source": "sm", "uri": "file:///s.md"}))


# --- High #5: an embedding-route change re-embeds an unchanged store -----------------------------
def test_embedding_route_change_reembeds_an_unchanged_store(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    base, _ = fake_harness()
    a, b_ = _Renamed(base, "test/model-a"), _Renamed(base, "test/model-b")
    shas = [write_capture(cap, prose(600, str(i)), {"source": "em", "uri": f"file:///{i}.md"})
            for i in range(2)]
    with BuildSession("em", harness=a, screen=screen) as s:
        for x in shas:
            s.index_capture(x)
    with BuildSession("em", harness=b_, screen=screen) as s:
        assert {s.index_capture(x).status for x in shas} == {"unchanged"}
    conn = _store(pas, "em")
    try:
        models = {r[0] for r in conn.execute("SELECT DISTINCT model FROM embeddings")}
    finally:
        conn.close()
    assert models == {"test/model-b"}
    res = Retriever(harness=b_).search("widget inspection", k=3, mode="vector")
    assert res and any(c.get("leg") == "vector" for c in res.coverage["searched"])


# --- High #6: lock fencing ------------------------------------------------------------------------
def test_publication_verifies_lock_ownership_and_heartbeats_independently(dirs, fake_harness,
                                                                           screen, monkeypatch):
    import databridge_retrieval.indexer as ix
    from databridge_retrieval import BuildInProgress, BuildSession

    monkeypatch.setattr(ix, "HEARTBEAT_SECONDS", 0.05)
    cap, pas = dirs
    h, _ = fake_harness()
    b = BuildSession("fe", harness=h, screen=screen).open()
    b.index_capture(write_capture(cap, prose(500), {"source": "fe", "uri": "file:///f.md"}))
    old = time.time() - 10_000
    os.utime(b.lock_path, (old, old))
    time.sleep(0.5)                                  # no work happens; the heartbeat still beats
    assert b.lock_path.stat().st_mtime > old + 5_000

    # another builder took the lock over (e.g. we were suspended past the stale window)
    foreign = json.dumps({"pid": 1, "host": "other-host", "token": "theirs", "at": "now"})
    b.lock_path.write_text(foreign, encoding="utf-8")
    os.utime(b.lock_path, (old, old))
    time.sleep(0.3)
    assert b.lock_path.stat().st_mtime < old + 1, "the heartbeat touched a lock it does not own"
    with pytest.raises(BuildInProgress, match="ownership"):
        b.publish()
    assert not b.published_path.exists(), "a builder that lost its lock published anyway"
    assert b.lock_path.read_text(encoding="utf-8") == foreign


# --- High #8: the rerank budget is enforced ----------------------------------------------------------
def test_rerank_prompt_stays_within_the_budget(fake_harness):
    import importlib

    from databridge_core.provenance import SourcePointer, Trust
    from databridge_retrieval import Passage, Retriever

    srch = importlib.import_module("databridge_retrieval.search")

    def passage(i, text):
        ptr = SourcePointer(source_id="s", kind="doc_span", document_id="d", char_start=0,
                            char_end=len(text), capture_digest="0" * 64, trust=Trust.UNTRUSTED)
        return Passage(chunk_id=f"c{i}", doc_id="d", source_id="s", text=text, locator="",
                       approx_locator=True, score=1.0 / (i + 1), pointer=ptr)

    n = 5 * srch.MAX_K
    ps = [passage(i, f"p{i} " + "y" * 5000) for i in range(n)]
    h, backend = fake_harness(responses=json.dumps({"keep": [{"index": 0, "relevance": 0.9}]}))
    out, note = Retriever(harness=h)._rerank("which passage answers?", ps, "brain_analysis")
    (call,) = backend.calls
    user = next(m["content"] for m in call["messages"] if m["role"] == "user")
    assert len(user) <= srch.RERANK_CHAR_BUDGET
    assert user.count("[extract truncated]") <= srch.RERANK_MAX_CANDIDATES
    assert len(out) == n and out[0].chunk_id == "c0", "nothing unseen or cut is dropped"
    assert "not sent" in note


# --- High #9: one damaged store degrades coverage, it does not abort retrieval -------------------
def test_one_damaged_store_reduces_coverage_only(dirs, fake_harness, screen, monkeypatch):
    from databridge_retrieval import BuildSession, Retriever, router, stats
    from databridge_retrieval.store import store_filename

    cap, pas = dirs
    h, _ = fake_harness()
    for src in ("good", "legacy", "third"):
        with BuildSession(src, harness=h, screen=screen) as b:
            b.index_capture(write_capture(cap, prose(700, src),
                                          {"source": src, "uri": f"file:///{src}.md"}))
    legacy = pas / store_filename("legacy", "default")
    conn = sqlite3.connect(str(legacy))
    conn.execute("DROP TABLE centroids")
    conn.commit()
    conn.close()
    assert {e["source_id"] for e in stats()} == {"good", "legacy", "third"}
    res = Retriever(harness=h).search("widget", k=10)
    assert {p.source_id for p in res} == {"good", "legacy", "third"}

    third = store_filename("third", "default")
    real_rows = Retriever._rows

    def rows(path, file, cids):
        if file == third:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real_rows(path, file, cids)

    monkeypatch.setattr(Retriever, "_rows", staticmethod(rows))
    real_cent = router.centroids

    def cent(root, entry):
        if entry["file"] == store_filename("good", "default"):
            raise sqlite3.DatabaseError("malformed")
        return real_cent(root, entry)

    monkeypatch.setattr(router, "centroids", cent)
    res = Retriever(harness=h, route_top=2).search("widget", k=10)
    assert res and "third" not in {p.source_id for p in res}
    assert any(s["store"] == third and "malformed" in s["reason"]
               for s in res.coverage["skipped"])


# --- Medium #10: keyword ranks are per store; BM25 never crosses stores ---------------------------
def test_keyword_rank_zero_hits_of_different_stores_score_alike(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("dense", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, "alpha " * 40 + prose(200, "d"),
                                      {"source": "dense", "uri": "file:///d.md"}))
    with BuildSession("sparse", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(900, "s") + " alpha",
                                      {"source": "sparse", "uri": "file:///s.md"}))
    for mode in ("keyword", "hybrid"):
        res = Retriever(harness=h).search("alpha", k=2, mode=mode)
        if mode == "keyword":
            assert {p.source_id for p in res} == {"dense", "sparse"}
            assert res[0].score == res[1].score, "a store's BM25 decided a cross-store order"


# --- Medium #11: re-indexing twice before a flush leaves no orphan embeddings --------------------
def test_reindexing_one_document_twice_before_a_flush_publishes(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("tw", harness=h, screen=screen, embed_flush=100_000) as b:
        b.index_capture(write_capture(cap, prose(900, "v1"),
                                      {"source": "tw", "uri": "file:///t.md"}))
        b.index_capture(write_capture(cap, prose(900, "v2"),
                                      {"source": "tw", "uri": "file:///t.md"}))
    conn = _store(pas, "tw")
    try:
        c, e = (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("chunks", "embeddings"))
    finally:
        conn.close()
    assert c == e > 0


# --- Medium #12: malformed or partial locator metadata is never presented as exact ---------------
def test_partial_or_malformed_locator_metadata_is_approximate():
    from databridge_retrieval.chunker import chunk_document
    from databridge_retrieval.locators import regions_for

    text = prose(4000, "loc")
    cases = {
        "partial": [{"kind": "page", "label": "1", "char_start": 0, "char_end": 10}],
        "out_of_bounds": [{"kind": "page", "label": "1", "char_start": 0,
                           "char_end": len(text) * 10}],
        "overlapping": [{"kind": "page", "label": "1", "char_start": 0, "char_end": 3000},
                        {"kind": "page", "label": "2", "char_start": 1000,
                         "char_end": len(text)}],
    }
    for name, locs in cases.items():
        chunks = chunk_document(text, regions=regions_for(text, {"locators": locs}))
        assert chunks and all(c.approx_locator for c in chunks), name
    half = len(text) // 2
    whole = [{"kind": "page", "label": "1", "char_start": 0, "char_end": half},
             {"kind": "page", "label": "2", "char_start": half, "char_end": len(text)}]
    chunks = chunk_document(text, regions=regions_for(text, {"locators": whole}))
    assert chunks and not any(c.approx_locator for c in chunks), "valid full cover is exact"


# --- Medium #13: the single-flight registry does not grow without bound --------------------------
def test_matrix_cache_loader_registry_is_released(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession
    from databridge_retrieval.store import store_filename
    from databridge_retrieval.vectors import MatrixCache

    cap, pas = dirs
    h, _ = fake_harness()
    srcs = [f"m{i}" for i in range(3)]
    for src in srcs:
        with BuildSession(src, harness=h, screen=screen) as b:
            b.index_capture(write_capture(cap, prose(400, src),
                                          {"source": src, "uri": f"file:///{src}.md"}))
    cache = MatrixCache(max_stores=1)
    conn = _store(pas, srcs[0])
    model = conn.execute("SELECT model FROM embeddings LIMIT 1").fetchone()[0]
    conn.close()
    for src in srcs * 2:
        ids, m = cache.get(pas / store_filename(src, "default"), model)
        assert ids
    assert not cache._loading, "per-key loader state outlived its load"


# --- Medium #14: blank or zero-vector queries return no arbitrary evidence -----------------------
def test_blank_and_zero_vector_queries_return_no_arbitrary_passages(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, backend = fake_harness()
    with BuildSession("bq", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(900), {"source": "bq", "uri": "file:///q.md"}))
    for q in ("", "   ", "?!"):
        for mode in ("hybrid", "vector", "keyword"):
            res = Retriever(harness=h).search(q, k=3, mode=mode)
            assert list(res) == [] and res.notices, (q, mode)
    zero, _ = fake_harness(embedder=lambda ts: [[0.0] * 8 for _ in ts])
    res = Retriever(harness=zero).search("widget", k=3, mode="vector")
    assert list(res) == [] and any("zero" in n for n in res.notices)
    hyb = Retriever(harness=zero).search("widget", k=3, mode="hybrid")
    assert hyb and any("zero" in n for n in hyb.notices), "the keyword leg still answers"
