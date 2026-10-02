"""Finance dry run (2026-10-02), finding FIN-10 — reproduced offline on TWO estates, then closed.

"What does credit memo CM-2026-0029 recommend?" retrieved CM-2025-0006, -0071, -0062, -0007 and
CM-2026-0011, but not -0029. Reproduced against a copy of the run's own store: the KEYWORD leg
ranked CM-2026-0029 first; every memo reads alike to the VECTOR leg, so memos found by both legs
out-fused the one found by keyword alone under reciprocal rank fusion.

The embedder here models that property, and nothing more: a hashed bag of the ALPHABETIC words
(digits carry no meaning to it), so documents of one template are near-identical vectors and an
identifier is invisible to the vector leg. Corpora: the finance credit memos
(``fixtures/packs/finance/credit_memos``) and the workspace/Larkspur text files
(``fixtures/workspace_demo/files``). Every identifier asked about is DERIVED from the documents,
and every expectation from the indexed passages.
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

import pytest

from retrieval_testkit import write_capture

DATABRIDGE = Path(__file__).resolve().parents[3]
CORPORA = {
    "finance": (DATABRIDGE / "fixtures" / "packs" / "finance" / "credit_memos", "*.md"),
    "workspace": (DATABRIDGE / "fixtures" / "workspace_demo" / "files", "*"),
}
DIM = 64
#: identifier-shaped: letters and digits joined by separators (the shape the fix keys on)
ID_RE = re.compile(r"(?<![0-9A-Za-z-])[A-Z]{2,5}(?:-[A-Z])?-\d{3,4}(?:-\d{2,4})?(?![0-9A-Za-z-])")


def word_embed(texts):
    """Meaning without identifiers: a hashed bag of alphabetic words. Digits are ignored, so two
    memos that differ only in their numbers embed identically — the run's failure condition."""
    out = []
    for t in texts:
        v = [0.0] * DIM
        for w in re.findall(r"[a-z]+", t.lower()):
            v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _texts(name: str) -> dict[str, str]:
    root, pattern = CORPORA[name]
    files = sorted(p for p in root.rglob(pattern)
                   if p.is_file() and p.suffix.lower() in (".md", ".txt"))
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_text("utf-8") for p in files}


@pytest.fixture(params=sorted(CORPORA))
def corpus(request, dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, configure

    cap, _ = dirs
    harness, _ = fake_harness(embedder=word_embed)
    configure(harness=harness)
    texts = _texts(request.param)
    assert len(texts) >= 5, "non-vacuous: a corpus to rank within"
    source = f"{request.param}-docs"
    with BuildSession(source, screen=screen) as b:
        for rel, text in texts.items():
            sha = write_capture(cap, text, {"source": source, "uri": f"file:///{rel}",
                                            "title": rel})
            b.index_capture(sha)
    return request.param, texts


def _ids(texts: dict[str, str]) -> list[str]:
    return sorted({m.group(0) for t in texts.values() for m in ID_RE.finditer(t)})


def _contains(text: str, ident: str) -> bool:
    from databridge_retrieval.search import exact_matches

    return exact_matches(text, [ident]) == 1


def test_an_exact_identifier_ranks_first_in_hybrid_search(corpus):
    """For every identifier in the corpus: the run's question shape, top result holds the id, and
    every passage holding it ranks above every passage that does not."""
    from databridge_retrieval import search

    name, texts = corpus
    ids = _ids(texts)
    assert len(ids) >= 3, ids
    for ident in ids:
        res = search(f"What does {ident} recommend?", k=8, mode="hybrid")
        hits = [_contains(p.text, ident) for p in res]
        assert hits and hits[0], (name, ident, [p.text[:60] for p in res])
        # exact hits form a prefix: none sits below a passage without the id
        assert hits == sorted(hits, reverse=True), (name, ident, hits)
        assert [p.score for p in res] == sorted((p.score for p in res), reverse=True)


def test_the_run_reproduces_without_the_exact_leg(corpus, monkeypatch):
    """Non-vacuous: with the exact leg disabled the same questions DO lose some ids — the fused
    ranking alone is what the dry run saw."""
    import sys

    from databridge_retrieval import search

    search_mod = sys.modules["databridge_retrieval.search"]

    name, texts = corpus
    monkeypatch.setattr(search_mod, "identifier_tokens", lambda _q: [])
    lost = [i for i in _ids(texts)
            if not _contains(search(f"What does {i} recommend?", k=8, mode="hybrid")[0].text, i)]
    if name == "finance":
        assert lost, "the finance memos reproduce FIN-10 without the fix"


def test_a_near_miss_identifier_gets_no_bonus(corpus):
    """An id that is NOT in the corpus (an existing one with a digit appended, so its FTS phrase
    tokens overlap a real one) earns no exact bonus: whole-token matching, not substring."""
    from databridge_retrieval import search
    from databridge_retrieval.search import EXACT_BONUS

    _, texts = corpus
    real = _ids(texts)[0]
    ghost = real + "7"
    assert not any(_contains(t, ghost) for t in texts.values())
    res = search(f"What does {ghost} recommend?", k=8, mode="hybrid")
    assert all(p.score < EXACT_BONUS for p in res)


def test_vector_mode_is_unchanged(corpus):
    """Semantic search keeps its contract: no exact leg, scores are cosines in [-1, 1]."""
    from databridge_retrieval import search

    _, texts = corpus
    res = search(f"What does {_ids(texts)[0]} recommend?", k=5, mode="vector")
    assert res and all(-1.0001 <= p.score <= 1.0001 for p in res)


@pytest.mark.parametrize("query,want", [
    ("What does credit memo CM-2026-0029 recommend?", ["CM-2026-0029"]),
    ("facility CB-F-000302 and INV-2026-0041", ["CB-F-000302", "INV-2026-0041"]),
    ("the 2026 review, Q3", []),
    ("A1B2 and a1b2", ["A1B2"]),
])
def test_identifier_shape(query, want):
    from databridge_retrieval.search import identifier_tokens

    assert identifier_tokens(query) == want
