"""Hybrid retrieval: RRF surfaces an exact-ID hit AND a paraphrase hit that each leg alone misses.

The fake embedder models meaning with a tiny concept table (the test's own vocabulary, not engine
logic): "tardy" and "late" mean *overdue*, "remittance" and "settlement" mean *payment*. The ID
``INV-7731`` carries no concept, so only the keyword leg can find the ledger line; the paraphrase
shares no literal word with the question, so only the vector leg can find it.
"""
from __future__ import annotations

import hashlib
import math
import re

import pytest

from retrieval_testkit import write_capture

CONCEPTS = {"overdue": 0, "tardy": 0, "late": 0, "payment": 1, "remittance": 1,
            "settlement": 1, "ledger": 2, "reconciled": 2}
DIM = 3


def concept_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * DIM
        for w in re.findall(r"[a-z]+", t.lower()):
            if w in CONCEPTS:
                v[CONCEPTS[w]] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


DOCS = {
    "id": "Ledger reference INV-7731 was reconciled against the supplier statement at close.",
    "para": "The remittance from the customer is tardy; the settlement was chased twice by finance.",
    "d1": "The library book is late and the fines keep growing with every single week.",
    "d2": "The train was late again this morning because of signal problems on the line.",
    "d3": "The late summer harvest festival draws crowds from the villages nearby every year.",
}
QUERY = "INV-7731 overdue payment"


@pytest.fixture
def corpus(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, configure

    cap, pas = dirs
    harness, _ = fake_harness(embedder=concept_embed)
    configure(harness=harness)
    doc_ids = {}
    with BuildSession("finance-files", screen=screen) as b:
        for name, text in DOCS.items():
            uri = f"file:///corpus/{name}.txt"
            sha = write_capture(cap, text, {"source": "finance-files", "uri": uri,
                                            "title": f"item {name}"})
            doc_ids[name] = b.index_capture(sha).doc_id
    return doc_ids


def _docs(results):
    return [p.doc_id for p in results]


def test_rrf_surfaces_both_hits_each_leg_alone_misses(corpus):
    from databridge_retrieval import search

    k = 2
    vector_only = _docs(search(QUERY, k=k, mode="vector"))
    keyword_only = _docs(search(QUERY, k=k, mode="keyword"))
    hybrid = _docs(search(QUERY, k=k, mode="hybrid"))

    assert corpus["id"] not in vector_only, "the ID has no meaning a vector can match"
    assert corpus["para"] in vector_only
    assert corpus["para"] not in keyword_only, "the paraphrase shares no literal term"
    assert corpus["id"] in keyword_only
    assert corpus["id"] in hybrid and corpus["para"] in hybrid
    assert len(hybrid) == k


def test_passage_shape_and_pointer_is_a_doc_span(corpus):
    from databridge_core.provenance import Trust
    from databridge_retrieval import Passage, search

    res = search(QUERY, k=3)
    assert res and all(isinstance(p, Passage) for p in res)
    for p in res:
        assert p.pointer.kind == "doc_span"
        assert p.pointer.document_id == p.doc_id and p.pointer.source_id == p.source_id
        assert p.pointer.trust is Trust.UNTRUSTED
        assert len(p.pointer.capture_digest) == 64
        assert p["chunk_id"] == p.chunk_id               # dict-style access
    assert res.offline_embedding is False and res.embedding_label is None
    assert [p.score for p in res] == sorted((p.score for p in res), reverse=True)


def test_sources_filter_and_unknown_source_reported(corpus):
    from databridge_retrieval import search

    assert search(QUERY, sources=["nope"]) == []
    res = search(QUERY, sources=["nope"])
    assert any(s["source_id"] == "nope" for s in res.coverage["skipped"])
    assert search(QUERY, sources=["finance-files"])


def test_kinds_filter(corpus):
    from databridge_retrieval import search

    assert search(QUERY, kinds=["prose"])
    assert search(QUERY, kinds=["table"]) == []


def test_fts_query_quotes_terms_and_resists_syntax():
    from databridge_retrieval import fts_query

    assert fts_query('INV-7731 "x" OR NEAR(') is not None
    assert '"INV-7731"' in fts_query("INV-7731 overdue")
    assert fts_query("?? !!") is None


def test_empty_passages_dir_is_an_honest_empty(dirs, fake_harness):
    from databridge_retrieval import Retriever

    h, _ = fake_harness()
    res = Retriever(harness=h).search("anything")
    assert res == [] and res.notices
