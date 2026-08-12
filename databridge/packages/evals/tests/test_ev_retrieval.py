"""EV-RETRIEVAL gate (`PLAN.md` §6.5).

The gates below are deliberately *below* the current score. An eval whose threshold equals its
measured value turns every future improvement attempt into a red build, and tempts the next person
to edit `PAIRS` instead of the ranker.

The one exception is the out-of-domain gate, which is pinned at **1.00**. Returning entities for a
question the graph cannot answer is not a quality regression, it is the bug this phase existed to
fix — retrieval declining is what stops "what is the meaning of life" reaching the model at all.
"""
from __future__ import annotations

from databridge_evals.e_retrieval import run_eval
from databridge_graph.clinic_seed import fresh_clinic_store
from databridge_graph.query import link_entities


def test_the_eval_reports_a_score_above_the_gate():
    r = run_eval()
    assert r.score >= 0.90, (
        f"EV-RETRIEVAL {r.correct}/{r.total} = {r.score:.2f}\n"
        + "\n".join(f"  MISS {q!r} expected={e!r} got={g!r}" for q, e, g in r.found_wrong))


def test_retrieval_declines_every_out_of_domain_question():
    """No floor at all is what let the old scorer answer anything. This is the load-bearing half."""
    r = run_eval()
    assert r.decline_rate_out_of_domain == 1.0, (
        "retrieval returned entities for a question the graph cannot answer:\n"
        + "\n".join(f"  {q!r} -> {g!r}" for q, e, g in r.found_wrong if e is None))


def test_the_meaning_of_life_returns_nothing():
    """The recorded false positive, pinned on its own so a regression names itself.

    `link_entities` used a SUBSTRING test, so the token "life" matched "…Life Assurance" and this
    question returned four insurers (reproduced against a live gateway)."""
    store = fresh_clinic_store()
    assert link_entities(store, "What is the meaning of life?") == []


def test_a_named_package_is_still_found():
    """The floor must not be so high that it refuses the questions the demo is built on."""
    store = fresh_clinic_store()
    ids = [n.id for n in link_entities(store, "What does the WELL2 package include?")]
    assert "package:blphs-well2" in ids


def test_documents_are_never_returned_as_the_answer_entity():
    """Documents are PROVENANCE — the answer cites them, they are never the subject. Addressed by
    `document_node_id` instead."""
    store = fresh_clinic_store()
    for question in ("chit", "referral letter", "Meridian Life Assurance"):
        for node in link_entities(store, question, top_k=5):
            assert node.kind.value != "document", f"{question!r} returned a DOCUMENT node"


def test_ranking_is_deterministic():
    """Same store, same question, same order — twice. A demo that reorders between runs is a demo
    nobody trusts, and BM25 ties are common in a corpus this small."""
    store = fresh_clinic_store()
    q = "Which patients does Meridian Life Assurance cover?"
    assert [n.id for n in link_entities(store, q, top_k=5)] == \
           [n.id for n in link_entities(store, q, top_k=5)]
