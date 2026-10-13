"""S1–S5 (+Ops, injection) end to end on the generated Company A world — expectations are DERIVED
from the world's fact manifest, never written down here (repo rule 6).

Per canonical question:
* every ``must_not_contain_facts`` doc is never even CONSIDERED (Gate 1 + Gate 2), and its sentinel
  sentence and canary never appear in the answer;
* every ``must_cite_facts`` doc (on a platform whose connector exists) is RELEASED to the narrator;
* ``expect_refusal`` ⇒ the constant refusal with no citations.
"""
from __future__ import annotations

import pytest

from databridge_ib.models import REFUSAL_TEXT

from e2e_world import E2E, load_world

WORLD = load_world()
CANONICAL = [q for q in WORLD["questions"] if q.get("kind") == "canonical" and not q.get("applies_after")]


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    return E2E(tmp_path_factory.mktemp("e2e"))


def _check(env, q, resp, *, stale_index: bool = False):
    """``stale_index``: the index ACL is deliberately out of date (lost webhook), so Gate 1 may let a
    revoked doc be CONSIDERED — Gate 2 must still keep it out of what is RELEASED."""
    decided = env.last("retrieval.decided")
    considered, released = set(decided.considered), set(decided.released)
    for fid in q.get("must_not_contain_facts", []):
        f = env.facts[fid]
        assert f["doc"] not in released, f"{q['id']}: denied fact {fid} was released"
        if not stale_index:
            assert f["doc"] not in considered, f"{q['id']}: denied fact {fid} was considered"
        assert f["statement"][:60] not in resp.answer, f"{q['id']}: sentinel of {fid} leaked"
        if f.get("canary"):
            assert f["canary"] not in resp.answer
    if q.get("expect_refusal") is True:
        assert resp.answer == REFUSAL_TEXT and resp.citations == (), q["id"]
    for fid in q.get("must_cite_facts", []):
        if env.fact_platform(fid) in env.platforms():
            assert env.facts[fid]["doc"] in released, f"{q['id']}: {fid} not released"


@pytest.mark.parametrize("q", CANONICAL, ids=[q["id"] for q in CANONICAL])
def test_canonical(env, q):
    resp = env.ask(q["persona"], q["question"])
    _check(env, q, resp)


def test_s3_existing_vs_nonexistent_identical(env):
    """Contractor: the Q3 report (exists, restricted) vs the Q7 report (never existed)."""
    import json
    q3 = next(q for q in WORLD["questions"] if q["id"] == "S3-q1")
    q7 = next(q for q in WORLD["questions"] if q["id"] == "S3-q5")

    def body(r):
        d = r.model_dump(mode="json")
        d.pop("request_id"), d.pop("as_of")
        return json.dumps(d, sort_keys=True)
    assert body(env.ask(q3["persona"], q3["question"])) == body(env.ask(q7["persona"], q7["question"]))


@pytest.mark.parametrize("action,qid", [("s4_restrict_page", "S4-q2"), ("s4_remove_member", "S4-q4")])
def test_s4_revocation_live(env, action, qid):
    before = next(q for q in WORLD["questions"] if q["id"] == qid.replace("q2", "q1").replace("q4", "q3"))
    platform = env.fact_platform(before["must_cite_facts"][0])
    if platform not in env.platforms():
        pytest.skip(f"{platform} connector not built yet")
    r1 = env.ask(before["persona"], before["question"])
    _check(env, before, r1)
    env.apply(action)
    after = next(q for q in WORLD["questions"] if q["id"] == qid)
    try:
        r2 = env.ask(after["persona"], after["question"])
        _check(env, after, r2)
    finally:
        env.reset()


def test_s4_revocation_without_webhook(env):
    """Gate 2 alone must deny: the webhook is lost, so the index ACL stays stale."""
    env.deliver = False
    try:
        env.apply("s4_remove_member")
        after = next(q for q in WORLD["questions"] if q["id"] == "S4-q4")
        _check(env, after, env.ask(after["persona"], after["question"]), stale_index=True)
        decided = env.last("retrieval.decided")
        assert any(g.verdict == "forbidden" for g in decided.gate2), (decided.considered, decided.gate2)
    finally:
        env.reset()


def test_s2_freshness_via_webhook(env):
    q = next(q for q in WORLD["questions"] if q["id"] == "S2-q2")
    try:
        env.apply("s2_update")
        resp = env.ask(q["persona"], q["question"])
        _check(env, q, resp)
        step = env.facts["S2-step-4b"]
        assert step["statement"][:40] in resp.answer or any(
            c.doc_id == step["doc"] and c.version == "8" for c in resp.citations)
    finally:
        env.reset()
