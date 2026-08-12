"""retrieval.py — the BM25 entity linker, unit-tested against synthetic candidates.

Kept separate from `test_query.py` (which exercises `link_entities` end-to-end against the real
clinic graph) so the canonical-id matching rule can be pinned in isolation, with a label
deliberately chosen so label-token coverage alone is NOT enough to pass `MIN_LABEL_COVERAGE` — only
the canonical-id override can. Most real catalogue entries print their id as readable words (`chest
xray` == label tokens of "Chest X-Ray"), which is exactly why the bug this file pins stayed
invisible in `packages/evals/e_retrieval.py`'s score: the override was silently a no-op for those,
and nothing forced it to actually fire.
"""
from __future__ import annotations

from databridge_graph.retrieval import Bm25Index, Candidate


def test_a_multi_token_canonical_id_can_still_be_matched_exactly():
    """The bug this pins: the canonical-id override compared a RAW string (`"full_panel_specific_
    code"`) against `q_set`, which is built by `tokens()` — and `tokens()` SPLITS on underscore.
    `"full_panel_specific_code" in {"full", "panel", "specific", "code"}` can never be true; only a
    canonical id with no separators at all (`WELL2`) could ever hit the branch. Any multi-word
    catalogue id (`chest_xray`, `full_blood_count`, and this synthetic one) was silently unmatchable
    through this path.

    The label here is chosen so plain label-token coverage falls BELOW `MIN_LABEL_COVERAGE` (2 of
    6 tokens = 0.33) — only the canonical-id override, now comparing tokens to tokens, can lift
    coverage to 1.0 and let the candidate through at all.
    """
    target = Candidate(id="test:synthetic", label="Full Panel Alpha Beta Gamma Delta",
                       canonical_id="full_panel_specific_code", attr_text="")
    # A second, unrelated candidate — a single-node index has degenerate IDF (every term is in
    # 100% of documents), which would mask this bug behind an unrelated scoring artefact.
    other = Candidate(id="test:other", label="Resting Ecg", canonical_id="resting_ecg",
                      attr_text="a cardiac screening test")
    index = Bm25Index([target, other])

    results = index.search("full panel specific code", top_k=3)
    ids = [r.id for r in results]
    assert "test:synthetic" in ids, (
        "an exact (tokenised) canonical-id match must surface the candidate even when its own "
        "label does not share enough words with the question")
    hit = next(r for r in results if r.id == "test:synthetic")
    assert hit.coverage == 1.0, "the canonical-id override must report full coverage, not the raw label overlap"


def test_a_canonical_id_that_only_partially_overlaps_the_question_does_not_get_the_override():
    """The override requires the FULL canonical id, not one of its tokens — otherwise a question
    that happens to share one word with an id would be treated as naming that exact entity."""
    target = Candidate(id="test:synthetic", label="Full Panel Alpha Beta Gamma Delta",
                       canonical_id="full_panel_specific_code", attr_text="")
    other = Candidate(id="test:other", label="Resting Ecg", canonical_id="resting_ecg",
                      attr_text="a cardiac screening test")
    index = Bm25Index([target, other])

    # Only "full" overlaps; "panel"/"specific"/"code" are absent, so the canonical id is not a
    # subset of the question's tokens and the override must not fire.
    results = index.search("what is full coverage", top_k=3)
    assert all(r.id != "test:synthetic" for r in results), results
