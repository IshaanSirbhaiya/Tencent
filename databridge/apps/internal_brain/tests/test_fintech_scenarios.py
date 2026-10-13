"""The fintech layer end to end (FC, MK, FEE, SENS) over the generated Company A world: the REAL mock
platforms, the REAL connectors and the IB pipeline (``e2e_world.E2E``). Expectations are DERIVED from
the world's fact manifest (repo rule 6).

* FC   tipping-off-safe compartment: the account manager gets the approved customer script whether
       the customer is under FinCrime review or does not exist at all, byte-identically (P2); a
       direct STR question is refused identically; the MLRO gets the case notes.
* MK   market-scoped answers: Singapore and Indonesia questions cite different market facts.
* FEE  approved-only: the draft v4 fee schedule is never indexed, considered or released.
* SENS ingest masking: raw card numbers, CVVs, IBANs and NRICs never reach the index or an answer.

The harness has no masking hook yet, so the ``env`` fixture builds E2E with an Ingestor whose index
writes go through ``sensitive.mask_doc`` — exactly the one-call ingest patch proposed for
``ingest.py`` (upsert(mask_doc(doc), ...)).
"""
from __future__ import annotations

import json
import re

import pytest

import e2e_world
from databridge_ib import sensitive
from databridge_ib.ingest import Ingestor
from databridge_ib.models import REFUSAL_TEXT
from e2e_world import E2E, load_manifest, load_world
from test_scenarios_e2e import _check

WORLD = load_world()
MANIFEST = load_manifest()
FACTS = {f["id"]: f for f in WORLD["facts"]}
QUESTIONS = {q["id"]: q for q in WORLD["questions"]}
SCENARIOS = ("FC", "MK", "FEE", "SENS")
FINTECH = [q for q in WORLD["questions"]
           if q.get("kind") == "canonical" and q["scenario"] in SCENARIOS and not q.get("applies_after")]
FC_PAIRS = [tuple(p) for p in MANIFEST["p2_pairs"] if QUESTIONS[p[0]]["scenario"] == "FC"]
SENS = [f for f in WORLD["facts"] if f.get("raw")]


class _MaskingIndex:
    """The index as the ingestor sees it once the ingest hook lands: every write is masked."""

    def __init__(self, inner):
        self._inner = inner

    def upsert(self, doc, acl):
        return self._inner.upsert(sensitive.mask_doc(doc), acl)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _MaskingIngestor(Ingestor):
    def __init__(self, index, *args, **kwargs):
        super().__init__(_MaskingIndex(index), *args, **kwargs)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(e2e_world, "Ingestor", _MaskingIngestor)
    try:
        return E2E(tmp_path_factory.mktemp("fintech"))
    finally:
        mp.undo()


def _ask(env, qid):
    q = QUESTIONS[qid]
    return q, env.ask(q["persona"], q["question"])


def _body(resp) -> str:
    """The response with only its per-request identity and clock fields removed."""
    d = resp.model_dump(mode="json")
    d.pop("request_id")
    d.pop("as_of")
    for c in d["citations"]:
        for k in [k for k in c if k.endswith("_checked_at")]:
            c.pop(k)
    return json.dumps(d, sort_keys=True)


def test_suite_is_not_vacuous():
    assert {q["scenario"] for q in FINTECH} == set(SCENARIOS)
    assert FC_PAIRS and SENS


@pytest.mark.parametrize("q", FINTECH, ids=[q["id"] for q in FINTECH])
def test_fintech_canonical(env, q):
    resp = env.ask(q["persona"], q["question"])
    _check(env, q, resp)


# --------------------------------------------------------------------------------------------
# FC: tipping-off-safe compartment
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pair", FC_PAIRS, ids=["-".join(p) for p in FC_PAIRS])
def test_fc_real_vs_nonexistent_customer_is_byte_identical(env, pair):
    """What the asker SEES is identical. (The ledger-only ``released`` set may differ slightly,
    because the two question texts differ and the ranker's vector leg embeds the whole question;
    restricted documents are never scored, so that difference cannot depend on them.)"""
    a, b = pair
    qa, ra = _ask(env, a)
    da = env.last("retrieval.decided")
    _qb, rb = _ask(env, b)
    db = env.last("retrieval.decided")
    assert _body(ra) == _body(rb)
    assert {c.doc_id for c in ra.citations} == {c.doc_id for c in rb.citations}
    restricted = {FACTS[f]["doc"] for f in qa["must_not_contain_facts"]}
    assert restricted                                                 # non-vacuous
    for d in (da, db):
        assert not restricted & (set(d.considered) | set(d.released))


def test_fc_account_manager_gets_only_the_approved_script(env):
    q, resp = _ask(env, "FC-q1")
    script = FACTS["CX-review-script"]
    assert resp.answer != REFUSAL_TEXT
    assert script["doc"] in {c.doc_id for c in resp.citations}
    fincrime = [FACTS[f] for f in q["must_not_contain_facts"] if f.startswith("FINCRIME-")]
    assert fincrime                                                   # non-vacuous
    decided = env.last("retrieval.decided")
    for f in fincrime:
        assert f["doc"] not in decided.considered
        assert f["statement"][:50] not in resp.answer and f["canary"] not in resp.answer
    # nothing the account manager can read names the customer, so the answer cannot either
    assert "harbourline" not in resp.answer.lower()


def test_fc_direct_str_question_is_refused(env):
    for qid in ("FC-q4", "FC-q5"):
        q, resp = _ask(env, qid)
        assert q["expect_refusal"] is True
        assert resp.answer == REFUSAL_TEXT and resp.citations == ()


def test_fc_mlro_gets_the_case_notes(env):
    q, resp = _ask(env, "FC-q3")
    assert resp.answer != REFUSAL_TEXT
    cited = {c.doc_id for c in resp.citations}
    assert cited & {FACTS[f]["doc"] for f in q["must_cite_facts"]}
    released = set(env.last("retrieval.decided").released)
    assert {FACTS[f]["doc"] for f in q["must_cite_facts"]} <= released


# --------------------------------------------------------------------------------------------
# MK: market-scoped answers
# --------------------------------------------------------------------------------------------
def test_mk_singapore_and_indonesia_answers_cite_different_market_facts(env):
    """Each market's own fact is cited for its own question, and the two answers differ. (The
    deterministic extractive answerer ranks sentences by raw term overlap, so a sentence from the
    other market's page may also be quoted; that is a narrator property, not an access one.)"""
    q1, r1 = _ask(env, "MK-q1")
    c1 = {c.doc_id for c in r1.citations}
    q2, r2 = _ask(env, "MK-q2")
    c2 = {c.doc_id for c in r2.citations}
    sg = {FACTS[f]["doc"] for f in q1["must_cite_facts"]}
    idn = {FACTS[f]["doc"] for f in q2["must_cite_facts"] if "ID" in FACTS[f].get("markets", [])}
    assert sg and idn and sg.isdisjoint(idn)
    assert sg <= c1 and idn <= c2
    assert r1.answer != r2.answer
    sg_stmt = FACTS[q1["must_cite_facts"][0]]["statement"]
    assert sg_stmt in " ".join(r1.answer.split())


# --------------------------------------------------------------------------------------------
# FEE: approved-only
# --------------------------------------------------------------------------------------------
def test_fee_draft_is_never_indexed_considered_or_released(env):
    draft = FACTS["FEE-draft"]
    assert env.index.doc(draft["doc"]) is None
    q, resp = _ask(env, "FEE-q1")
    decided = env.last("retrieval.decided")
    assert draft["doc"] not in decided.considered and draft["doc"] not in decided.released
    assert draft["statement"][:50] not in resp.answer and draft["canary"] not in resp.answer
    v3 = FACTS["FEE-v3"]
    assert v3["doc"] in {c.doc_id for c in resp.citations}
    # even a question aimed squarely at the draft gets nothing from it
    resp2 = env.ask("leo.ops", "What does the draft v4 SG fee schedule propose for the FX "
                               "conversion fee?")
    assert draft["statement"][:50] not in resp2.answer and draft["canary"] not in resp2.answer
    assert draft["doc"] not in env.last("retrieval.decided").considered


# --------------------------------------------------------------------------------------------
# SENS: masked at ingest
# --------------------------------------------------------------------------------------------
def _forms(raw: str) -> set[str]:
    return {raw, re.sub(r"[ -]", "", raw)}


@pytest.mark.parametrize("f", SENS, ids=[f["id"] for f in SENS])
def test_sens_raw_value_is_in_the_source_but_not_in_the_index(env, f):
    platform = f["doc"].split(":", 1)[0]
    src = env.connectors[platform].fetch(f["doc"])
    assert src is not None and f["raw"] in src.text                   # the source really has it
    row = env.index.doc(f["doc"])
    assert row is not None
    assert not any(x in row["text"] or x in row["title"] for x in _forms(f["raw"]))
    assert f["masked"] in row["text"]
    assert f["statement"] in row["text"]                              # the evidence survives


def test_sens_raw_values_appear_nowhere_in_the_index(env):
    raws = set().union(*(_forms(f["raw"]) for f in SENS))
    for d in env.index.doc_ids():
        row = env.index.doc(d)
        blob = f"{row['title']}\n{row['text']}"
        assert not any(r in blob for r in raws), d


@pytest.mark.parametrize("qid", [q["id"] for q in FINTECH if q["scenario"] == "SENS"])
def test_sens_answers_cite_the_doc_without_the_raw_value(env, qid):
    q, resp = _ask(env, qid)
    assert resp.answer != REFUSAL_TEXT
    docs = {FACTS[f]["doc"] for f in q["must_cite_facts"]}
    assert docs & {c.doc_id for c in resp.citations}
    for f in SENS:
        assert not any(x in resp.answer for x in _forms(f["raw"])), (qid, f["id"])
    ledger = json.dumps([e.model_dump(mode="json") for e in env.ledger.events])
    assert not any(x in ledger for f in SENS for x in _forms(f["raw"]))
