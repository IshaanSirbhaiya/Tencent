"""Indexing: pointers resolve, locators, per-chunk screen, incremental, P11 publication refusals."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from retrieval_testkit import prose, write_capture

def _store(pas, source="plant-docs", tier="default"):
    from databridge_retrieval.store import store_filename

    return pas / store_filename(source, tier)


def test_pointer_offsets_slice_the_capture_text_to_the_chunk(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    text = prose(6000)
    sha = write_capture(cap, text, {"source": "plant-docs", "uri": "file:///p/ops.md",
                                    "title": "Ops"})
    with BuildSession("plant-docs", harness=h, screen=screen) as b:
        r = b.index_capture(sha)
    assert r.status == "indexed" and r.chunks >= 3

    res = Retriever(harness=h).search("widget", k=100, mode="keyword")
    assert len(res) == r.chunks, "every stored chunk is reachable, so the check is not vacuous"
    captured = (cap / sha[:2] / f"{sha}.txt").read_text(encoding="utf-8")
    raw = (cap / sha[:2] / sha).read_bytes()
    for p in res:
        assert captured[p.pointer.char_start:p.pointer.char_end] == p.text
        assert hashlib.sha256(raw).hexdigest() == p.pointer.capture_digest == sha
    spans = sorted((p.pointer.char_start, p.pointer.char_end) for p in res)
    assert spans[0][0] == 0 and spans[-1][1] == len(text.rstrip())
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert s2 < e1, "adjacent chunks overlap (15%) — nothing between them is lost"


def test_chunk_sizes_follow_the_constants():
    from databridge_retrieval.chunker import (
        CHUNK_CHARS, MIN_CHUNK_CHARS, OVERLAP_FRACTION, chunk_spans)

    text = prose(20000)
    spans = chunk_spans(text)
    assert len(spans) > 5
    assert all(e - s <= CHUNK_CHARS for s, e in spans)
    assert all(e - s >= MIN_CHUNK_CHARS for s, e in spans)
    overlaps = [e1 - s2 for (s1, e1), (s2, e2) in zip(spans, spans[1:])]
    assert all(0 < o <= CHUNK_CHARS * OVERLAP_FRACTION for o in overlaps)
    # a tiny trailing fragment folds into its predecessor rather than standing alone
    tail = chunk_spans(text[: CHUNK_CHARS + MIN_CHUNK_CHARS // 2])
    assert all(e - s >= MIN_CHUNK_CHARS for s, e in tail)


def test_locators_exact_when_structured_else_flagged_approx(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    pages = [prose(2500, salt=f"p{n}") for n in range(3)]
    paged = "\f".join(pages)
    flat = prose(5000, salt="flat")
    slides_text = prose(3000, salt="s")
    cut = len(slides_text) // 2
    s_paged = write_capture(cap, paged, {"source": "loc", "uri": "file:///a.pdf", "title": "A"})
    s_flat = write_capture(cap, flat, {"source": "loc", "uri": "file:///b.txt", "title": "B"})
    s_flat_pc = write_capture(cap, flat + " extra", {"source": "loc", "uri": "file:///c.pdf",
                                                     "title": "C"})
    s_slides = write_capture(cap, slides_text, {"source": "loc", "uri": "file:///d.pptx",
                                                "title": "D"})
    with BuildSession("loc", harness=h, screen=screen) as b:
        d_paged = b.index_capture(s_paged).doc_id
        d_flat = b.index_capture(s_flat).doc_id
        d_pc = b.index_capture(s_flat_pc, {"page_count": 4}).doc_id
        d_sl = b.index_capture(s_slides, {"locators": [
            {"kind": "slide", "label": "1", "char_start": 0, "char_end": cut},
            {"kind": "slide", "label": "2", "char_start": cut, "char_end": len(slides_text)}]}
        ).doc_id

    res = Retriever(harness=h).search("widget", k=200, mode="keyword")
    by = {}
    for p in res:
        by.setdefault(p.doc_id, []).append(p)
    assert all(not p.approx_locator and p.locator.startswith(("p.", "pp.")) for p in by[d_paged])
    assert {p.locator for p in by[d_paged]} >= {"p. 1", "p. 3"}
    assert all(p.approx_locator and p.locator.startswith("approx. chars") for p in by[d_flat])
    assert all(p.approx_locator and p.locator.startswith("approx. p") for p in by[d_pc])
    assert all(not p.approx_locator and p.locator.startswith("slide") for p in by[d_sl])


def test_per_chunk_screen_excludes_and_records_then_masks_same_length(dirs, fake_harness,
                                                                      screen):
    from databridge_retrieval import BuildSession, Retriever
    from databridge_retrieval import store as st

    cap, pas = dirs
    h, _ = fake_harness()
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    text = (prose(2500, "a") + "\n\n" + key + "\n\n" + prose(2500, "b")
            + "\n\nQuestions to jane.doe@example.com please.")
    sha = write_capture(cap, text, {"source": "sec", "uri": "file:///x.md", "title": "X"})
    with BuildSession("sec", harness=h, screen=screen) as b:
        r = b.index_capture(sha)
    assert r.excluded >= 1 and r.chunks >= 1

    conn = st.connect(_store(pas, "sec"), readonly=True)
    rows = conn.execute("SELECT decision, rules_json, reverse FROM excluded_chunks").fetchall()
    contents = [x[0] for x in conn.execute("SELECT content FROM chunks")]
    conn.close()
    assert len(rows) == r.excluded
    assert all("private_key_block" in json.loads(x["rules_json"]) and x["reverse"] for x in rows)
    assert not any("PRIVATE KEY" in c for c in contents)

    res = Retriever(harness=h).search("jane.doe@example.com please", k=50, mode="keyword")
    hit = [p for p in res if "please" in p.text]
    assert hit and all("@example.com" not in p.text and "[EMAIL]" in p.text for p in hit)
    for p in hit:  # the mask is the same length, so the pointer still addresses the source span
        assert len(text[p.pointer.char_start:p.pointer.char_end]) == len(p.text)


def test_incremental_skips_unchanged_and_reuses_vectors_on_change(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession

    cap, pas = dirs
    h, backend = fake_harness()
    uri = "file:///p/log.md"
    text = prose(6000)
    sha1 = write_capture(cap, text, {"source": "inc", "uri": uri, "title": "Log"})
    embedded = lambda: sum(len(c["inputs"]) for c in backend.embed_calls)  # noqa: E731

    with BuildSession("inc", harness=h, screen=screen) as b:
        first = b.index_capture(sha1)
    n_first = embedded()
    assert first.status == "indexed" and n_first == first.chunks

    with BuildSession("inc", harness=h, screen=screen) as b:
        again = b.index_capture(sha1)
    # unchanged content is not re-embedded; publication embeds ONE stored chunk to learn the
    # current embedding route (R-019 round 3, #5), and that is all
    assert again.status == "unchanged" and embedded() == n_first + 1
    n_first = embedded()

    changed = text + " A closing sentence appended about the widget audit."
    sha2 = write_capture(cap, changed, {"source": "inc", "uri": uri, "title": "Log"})
    with BuildSession("inc", harness=h, screen=screen) as b:
        third = b.index_capture(sha2)
    assert third.status == "indexed" and third.doc_id == first.doc_id   # identity = full path
    assert third.reused_vectors >= 1
    assert embedded() - n_first == third.chunks - third.reused_vectors < third.chunks


def test_empty_publication_is_refused_even_with_force(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, PublishRefused

    cap, pas = dirs
    h, _ = fake_harness()
    b = BuildSession("empty", harness=h, screen=screen, force=True).open()
    with pytest.raises(PublishRefused, match="EMPTY"):
        b.publish()
    blank = write_capture(cap, "   \n\n  ", {"source": "empty", "uri": "file:///blank"})
    b = BuildSession("empty", harness=h, screen=screen).open()
    assert b.index_capture(blank).status == "empty_text"
    with pytest.raises(PublishRefused):
        b.publish()
    assert not _store(pas, "empty").exists()
    assert not list(pas.glob("*.building")) and not list(pas.glob("*.lock"))


def test_shrink_over_half_is_refused_without_force(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, PublishRefused
    from databridge_retrieval import store as st

    cap, pas = dirs
    h, _ = fake_harness()
    shas = [write_capture(cap, prose(400, salt=str(i)), {"source": "sh", "uri": f"file:///{i}"})
            for i in range(4)]
    with BuildSession("sh", harness=h, screen=screen) as b:
        ids = [b.index_capture(s).doc_id for s in shas]

    def published_docs():
        conn = st.connect(_store(pas, "sh"), readonly=True)
        try:
            return st.counts(conn)["documents"]
        finally:
            conn.close()

    before = published_docs()
    assert before == len(shas)
    keep = ids[: (len(ids) // 2) - 1] or ids[:1]       # strictly fewer than half remain
    b = BuildSession("sh", harness=h, screen=screen).open()
    b.retain_only(keep)
    with pytest.raises(PublishRefused, match="shrank"):
        b.publish()
    assert published_docs() == before, "the working store is untouched by a refused publish"

    b = BuildSession("sh", harness=h, screen=screen, force=True).open()
    b.retain_only(keep)
    rep = b.publish()
    assert rep.published and published_docs() == len(keep)


def test_index_capture_entry_point_reads_source_from_capture_json(dirs, fake_harness):
    from databridge_retrieval import configure, index_capture, stats

    cap, pas = dirs
    h, _ = fake_harness()
    configure(harness=h)
    sha = write_capture(cap, prose(900), {"source": "entry", "uri": "file:///e.md",
                                          "title": "E", "mime": "text/markdown",
                                          "retrieved_at": "2026-10-01T09:00:00Z"})
    r = index_capture(sha)
    assert r.status == "indexed"
    (entry,) = stats()
    assert entry["source_id"] == "entry" and entry["documents"] == 1
    assert entry["chunks"] == entry["embeddings"] == r.chunks
    assert entry["search_ram_mb"] is not None and entry["search_ram_mb"] > 0
    assert index_capture(sha).status == "unchanged"


def test_missing_capture_text_is_reported_not_silent(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession

    h, _ = fake_harness()
    b = BuildSession("m", harness=h, screen=screen).open()
    r = b.index_capture("0" * 64, {"uri": "file:///gone"})
    b.abort()
    assert r.status == "missing_capture" and r.detail


def test_single_writer_lock(dirs, fake_harness, screen):
    from databridge_retrieval import BuildInProgress, BuildSession

    h, _ = fake_harness()
    b = BuildSession("lock", harness=h, screen=screen).open()
    with pytest.raises(BuildInProgress):
        BuildSession("lock", harness=h, screen=screen).open()
    b.abort()
    BuildSession("lock", harness=h, screen=screen).open().abort()
