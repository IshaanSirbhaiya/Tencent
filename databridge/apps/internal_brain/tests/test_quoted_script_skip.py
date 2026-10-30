"""D-43: approved customer-facing wording is quoted, never sent to a model to be re-worded.

When the deterministic quoted answer over the RELEASED passages (after Gate 1 and the live Gate 2)
cites approved scripts/templates and nothing else, the pipeline serves that quote without calling
the model. Checked here over the generated world, the real mock platforms and connectors, with a
counting fake model behind the real harness:

* Amy's Velquarry question: 0 model calls, the quoted answer, ``reason_code`` set.
* Questions whose quote cites anything else still call the model (Leo on the same question;
  ordinary questions).
* P2: Velquarry vs Bayfront (a real vs a non-existent customer) is byte-identical with the model on;
  and a PAIRED world that adds a restricted "approved" script Amy cannot read gives Amy the same
  decision and the same response as the world without it.
"""
from __future__ import annotations

import copy
import json

import pytest

from databridge_harness import FakeBackend, Harness
from databridge_ib import narrate
from databridge_ib.llm import ModelChain
from databridge_ib.models import REFUSAL_TEXT
from databridge_ib.narrate import QUOTED_APPROVED_SCRIPT, TASK, Passage
from e2e_world import E2E, load_manifest, load_world

WORLD = load_world()
QUESTIONS = {q["id"]: q for q in WORLD["questions"]}
FACTS = {f["id"]: f for f in WORLD["facts"]}
FC_PAIRS = [tuple(p) for p in load_manifest()["p2_pairs"] if QUESTIONS[p[0]]["scenario"] == "FC"]
FAKE = "fake-model"


class Counting:
    """A fake model behind the real harness: cites the first passage, and counts every call."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def __call__(self, model, messages, guided_json=None):
        self.calls.append(messages)
        ctx = messages[-1]["content"].rsplit("\n\nQuestion: ", 1)[0]
        first = ctx.splitlines()[0]
        tag = first[1:first.index("]")]
        body = first.split(") ", 1)[1]
        sent = next((s for s in body.split(". ") if len(s) > 20), body)[:200].rstrip(".")
        return f"{sent} [{tag}]."

    def chain(self) -> ModelChain:
        cfg = {"routing": {TASK: "ib_model"}, "aliases": {"ib_model": {"model": f"openai/{FAKE}"}},
               "profiles": {"cloud": {}, "airgapped": {}}}
        return ModelChain(models=[FAKE], provider="fake",
                          harness_factory=lambda m: Harness(FakeBackend(self), config=cfg,
                                                            profile="cloud", cache=None))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return E2E(tmp_path_factory.mktemp("quoted"))


def _ask(env, persona, question):
    fake = Counting()
    env.pipeline.chain = fake.chain()
    try:
        return env.ask(persona, question), fake
    finally:
        env.pipeline.chain = None


def _body(resp) -> str:
    d = resp.model_dump(mode="json")
    d.pop("request_id")
    d.pop("as_of")
    for c in d["citations"]:
        for k in [k for k in c if k.endswith("_checked_at")]:
            c.pop(k)
    return json.dumps(d, sort_keys=True)


# -- the rule on its own -----------------------------------------------------------------------
def _p(tag, title):
    return Passage(tag=tag, doc_id=f"confluence:{tag}", platform="confluence", container_name="CX",
                   title=title, version="1", updated="2026-10-01", text="Say this to the customer.")


@pytest.mark.parametrize("title, yes", [
    ("Customer account review: approved customer script", True),
    ("Approved reply template for refunds", True),
    ("Customer script (draft, not approved)", False),       # order matters: "approved" first
    ("Script review notes", False),
    ("Approved vendor list", False),
])
def test_title_marker(title, yes):
    assert narrate.is_approved_script(_p("C1", title)) is yes


def test_quote_only_needs_every_cited_passage_to_be_approved_wording():
    s, other = _p("C1", "Approved customer script"), _p("C2", "Product policy")
    assert narrate.quote_only([s, other], "Say this to the customer [C1].")
    assert not narrate.quote_only([s, other], "Say this [C1]. Policy text here [C2].")
    assert not narrate.quote_only([s], REFUSAL_TEXT)
    assert not narrate.quote_only([], "x [C1].")
    assert not narrate.quote_only([s], "no tags at all.")


# -- end to end --------------------------------------------------------------------------------
def test_amy_velquarry_question_skips_the_model(env):
    q = QUESTIONS["FC-q1"]
    resp, fake = _ask(env, q["persona"], q["question"])
    assert fake.calls == []                                           # 0 model calls
    assert resp.model.provider == "deterministic" and resp.model.model == "extractive-v1"
    assert resp.model.reason_code == QUOTED_APPROVED_SCRIPT and resp.model.fallback_used is False
    script = FACTS["CX-review-script"]
    assert {c.doc_id for c in resp.citations} == {script["doc"]}
    assert script["statement"] in " ".join(resp.answer.split())
    # recorded in the ledger as the answer's model
    assert env.last("answer.released").model.reason_code == QUOTED_APPROVED_SCRIPT
    # it is exactly the quote the model-off pipeline serves
    off = env.ask(q["persona"], q["question"])
    assert off.answer == resp.answer
    assert off.model.reason_code is None                    # model off: nothing was skipped
    # the reason is serialized only when set
    assert "reason_code" in resp.model_dump(mode="json")["model"]
    assert "reason_code" not in off.model_dump(mode="json")["model"]


@pytest.mark.parametrize("persona, question", [
    # Leo's quote also cites a product policy page, so the model is asked
    ("leo.ops", QUESTIONS["FC-q1"]["question"]),
    (QUESTIONS["S1-q1"]["persona"], QUESTIONS["S1-q1"]["question"]),
    (QUESTIONS["FC-q3"]["persona"], QUESTIONS["FC-q3"]["question"]),
])
def test_other_questions_still_call_the_model(env, persona, question):
    resp, fake = _ask(env, persona, question)
    assert len(fake.calls) >= 1
    assert resp.model.reason_code is None
    assert resp.model.model == FAKE


@pytest.mark.parametrize("pair", FC_PAIRS, ids=["-".join(p) for p in FC_PAIRS])
def test_p2_real_vs_nonexistent_customer_with_the_model_on(env, pair):
    a, b = (QUESTIONS[x] for x in pair)
    ra, fa = _ask(env, a["persona"], a["question"])
    rb, fb = _ask(env, b["persona"], b["question"])
    assert len(fa.calls) == len(fb.calls)
    assert ra.model == rb.model
    assert _body(ra) == _body(rb)


# -- paired world: a restricted approved script Amy cannot read --------------------------------
def _with_restricted_script(world: dict) -> dict:
    """World A': the real world plus an "approved customer script" page in the FinCrime space
    (readable by the FinCrime group only), worded to rank for Amy's question if she could read it."""
    w = copy.deepcopy(world)
    base = next(p for p in w["confluence"]["pages"] if p["id"] == "cf-1204")
    page = copy.deepcopy(base)
    page.update(id="cf-1299", space="fincrime", history=[], version=1,
                title="FinCrime: approved customer script for restricted accounts",
                body=("Purpose\n\nWhat to tell the customer when their account has been restricted "
                      "after a FinCrime review: tell the customer the account is restricted "
                      "pending review and that we will contact them.\n\nDocument ref: CNRY-0f43a1"))
    w["confluence"]["pages"].append(page)
    return w


@pytest.fixture(scope="module")
def paired(tmp_path_factory):
    import sys
    from pathlib import Path
    root = str(Path(__file__).resolve().parents[3])                    # databridge/
    if root not in sys.path:
        sys.path.insert(0, root)
    from evals.internal_brain.oracle import Oracle, filter_world, write_world
    tmp = tmp_path_factory.mktemp("paired")
    wa = _with_restricted_script(WORLD)
    oracle = Oracle(wa)
    persona = QUESTIONS["FC-q1"]["persona"]
    assert not oracle.can_read(persona, "confluence:cf-1299")          # restricted for Amy
    assert oracle.can_read("mlro.tan", "confluence:cf-1299")           # but it is real
    wb, _rep = filter_world(wa, oracle.unreadable(persona))
    (tmp / "a").mkdir()
    (tmp / "b").mkdir()
    ea = E2E(tmp / "a", world_path=write_world(wa, tmp / "world_a.json"))
    eb = E2E(tmp / "b", world_path=write_world(wb, tmp / "world_b.json"))
    assert ea.index.doc("confluence:cf-1299") is not None              # indexed in A'
    assert eb.index.doc("confluence:cf-1299") is None
    return ea, eb


@pytest.mark.parametrize("qid", ["FC-q1", "FC-q2"])
def test_restricted_script_does_not_change_the_decision(paired, qid):
    ea, eb = paired
    q = QUESTIONS[qid]
    ra, fa = _ask(ea, q["persona"], q["question"])
    rb, fb = _ask(eb, q["persona"], q["question"])
    assert fa.calls == [] and fb.calls == []
    assert ra.model == rb.model and ra.model.reason_code == QUOTED_APPROVED_SCRIPT
    assert _body(ra) == _body(rb)
    assert "confluence:cf-1299" not in ea.last("retrieval.decided").considered
