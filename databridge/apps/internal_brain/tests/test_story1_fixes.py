"""Story 1 ("The Call") workflow-test fixes, with FAKE models only (no network, no spend).

* Fix #1: the answer prompt carries the status-facts rule; the grounding wall is unchanged.
* Fix #2: a quoted answer after a model try says WHY (``model.reason_code``): the wall rejected the
  model's text, or no model replied; no code when no model is configured; P2 holds with the codes.
* D-43: the skip keys on the passages the quote CITES; a restricted document that is NOT approved
  wording, worded to rank for the question, cannot change Amy's decision (paired worlds).
* Fix #4: no HTTP client logger prints a request URL (an endpoint host) at start-up levels.
* Fix #5: the page-or-chat prompt carries the case-overview rule.
* Fix #7: the page check's value counters balance (proposed = kept + dropped).
* The stand-in answer timeout (``IB_MODEL_ANSWER_TIMEOUT_S``) and the preflight's thresholds,
  labels and Rachel's three stage facts (derived from the world's fact manifest).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from databridge_harness import FakeBackend, Harness
from databridge_ib import bootstrap, narrate
from databridge_ib.llm import ModelChain
from databridge_ib.models import REFUSAL_TEXT
from databridge_ib.narrate import (IB_SYSTEM_PROMPT, MODEL_REJECTED_EVIDENCE, MODEL_UNAVAILABLE,
                                   QUOTED_APPROVED_SCRIPT, STATUS_FACTS_RULE, TASK)
from e2e_world import E2E, load_manifest, load_world

from test_quoted_script_skip import FC_PAIRS, Counting, _ask, _body

ROOT = Path(__file__).resolve().parents[3]                                 # databridge/
WORLD = load_world()
QUESTIONS = {q["id"]: q for q in WORLD["questions"]}
FACTS = {f["id"]: f for f in WORLD["facts"]}
FAKE = "fake-model"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    return E2E(tmp_path_factory.mktemp("story1"))


def _cfg():
    return {"routing": {TASK: "ib_model"}, "aliases": {"ib_model": {"model": f"openai/{FAKE}"}},
            "profiles": {"cloud": {}, "airgapped": {}}}


class Scripted:
    """A fake model behind the real harness that returns ``reply(messages)`` and records calls."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, model, messages, guided_json=None):
        self.calls.append(messages)
        return self.reply(messages)

    def chain(self) -> ModelChain:
        return ModelChain(models=[FAKE], provider="fake",
                          harness_factory=lambda m: Harness(FakeBackend(self), config=_cfg(),
                                                            profile="cloud", cache=None))


class _Slow:
    def complete_text(self, *a, **k):
        raise TimeoutError("the model did not reply in time")


def _with(env, chain, persona, question):
    env.pipeline.chain = chain
    try:
        return env.ask(persona, question)
    finally:
        env.pipeline.chain = None


def _first_tag(messages) -> str:
    ctx = messages[-1]["content"].rsplit("\n\nQuestion: ", 1)[0]
    first = ctx.splitlines()[0]
    return first[1:first.index("]")]


RACHEL = QUESTIONS["FC-q3"]                       # the MLRO's case question


# -- fix #1: the prompt rule --------------------------------------------------------------------
def test_the_answer_prompt_carries_the_status_facts_rule(env):
    fake = Counting()
    env.pipeline.chain = fake.chain()
    try:
        env.ask(RACHEL["persona"], RACHEL["question"])
    finally:
        env.pipeline.chain = None
    assert fake.calls, "the model was asked"
    system = [m["content"] for m in fake.calls[0] if m["role"] == "system"]
    assert system == [IB_SYSTEM_PROMPT]
    assert STATUS_FACTS_RULE in system[0]
    for words in ("filing", "decision", "count or number", "each with its tag"):
        assert words in STATUS_FACTS_RULE
    # the rule names no fact, document, person or case of the world
    low = STATUS_FACTS_RULE.lower()
    for f in WORLD["facts"]:
        assert f["doc"].split(":", 1)[-1].lower() not in low
    assert "velquarry" not in low and "stro" not in low and "37" not in low
    # the wall's own rules are still there, unchanged
    assert "Use only facts stated in the passages" in system[0]
    assert "Do not count, total or infer anything the passages do not state." in system[0]
    assert narrate.PROMPT_VERSION != "ib-prompt-2026-10-13.1"            # the version moved on


def test_the_grounding_wall_still_refuses_an_invented_status_fact(env):
    """The rule asks for every stated fact; it cannot make the wall accept an unstated one."""
    fake = Scripted(lambda msgs: (f"The account was flagged for 999 transfers [{_first_tag(msgs)}]. "
                                  f"A report was filed under reference ZZ-0000-1 [{_first_tag(msgs)}]."))
    resp = _with(env, fake.chain(), RACHEL["persona"], RACHEL["question"])
    assert fake.calls
    assert "999" not in resp.answer and "ZZ-0000-1" not in resp.answer
    assert resp.model.provider == "deterministic" and resp.model.fallback_used is True
    assert resp.model.reason_code == MODEL_REJECTED_EVIDENCE
    assert env.last("answer.released").model.reason_code == MODEL_REJECTED_EVIDENCE
    off = env.ask(RACHEL["persona"], RACHEL["question"])
    assert resp.answer == off.answer                                     # the quoted answer


# -- fix #2: the reason a quoted answer was served --------------------------------------------
def test_a_model_that_does_not_reply_gives_model_unavailable(env):
    chain = ModelChain(models=["slow-1"], provider="fake", harness_factory=lambda m: _Slow())
    resp = _with(env, chain, RACHEL["persona"], RACHEL["question"])
    assert resp.model.fallback_used is True and resp.model.reason_code == MODEL_UNAVAILABLE
    assert resp.model_dump(mode="json")["model"]["reason_code"] == MODEL_UNAVAILABLE


def test_text_citing_no_released_passage_is_a_rejection(env):
    fake = Scripted(lambda msgs: "The review found nothing unusual [C99].")
    resp = _with(env, fake.chain(), RACHEL["persona"], RACHEL["question"])
    assert resp.model.reason_code == MODEL_REJECTED_EVIDENCE


def test_no_model_configured_carries_no_reason(env):
    resp = env.ask(RACHEL["persona"], RACHEL["question"])
    assert resp.model.provider == "deterministic" and resp.model.fallback_used is False
    assert resp.model.reason_code is None
    assert "reason_code" not in resp.model_dump(mode="json")["model"]


def test_a_model_written_answer_carries_no_reason(env):
    resp, fake = _ask(env, RACHEL["persona"], RACHEL["question"])
    assert fake.calls and resp.model.model == "fake-model" and resp.model.reason_code is None


def test_a_refusal_with_nothing_released_calls_no_model_and_carries_no_reason(env):
    q = QUESTIONS["FC-q1"]
    fake = Scripted(lambda msgs: "x")
    resp = _with(env, fake.chain(), "ken.ext", q["question"])
    assert resp.answer == REFUSAL_TEXT and fake.calls == []
    assert resp.model.reason_code is None and resp.model.fallback_used is False


@pytest.mark.parametrize("notes, rejected, code", [
    (["m1: UngroundedNarration"], False, MODEL_REJECTED_EVIDENCE),
    (["m1: TimeoutError", "m2: UngroundedNarration"], False, MODEL_REJECTED_EVIDENCE),
    (["m1: TimeoutError"], False, MODEL_UNAVAILABLE),
    (["m1: empty"], False, MODEL_UNAVAILABLE),
    (["m1: RateLimitError", "m2: APITimeoutError"], False, MODEL_UNAVAILABLE),
    ([], True, MODEL_REJECTED_EVIDENCE),
    ([], False, MODEL_UNAVAILABLE),
])
def test_fallback_reason(notes, rejected, code):
    assert narrate.fallback_reason(notes, rejected) == code
    assert code in narrate.REASON_CODES


@pytest.mark.parametrize("pair", FC_PAIRS, ids=["-".join(p) for p in FC_PAIRS])
def test_p2_holds_with_the_fallback_reasons(env, pair):
    """Real vs non-existent customer, with a model whose text is always rejected: the same bytes."""
    a, b = (QUESTIONS[x] for x in pair)
    bad = lambda msgs: f"Invented 4242 fact [{_first_tag(msgs)}]."       # noqa: E731
    ra = _with(env, Scripted(bad).chain(), a["persona"], a["question"])
    rb = _with(env, Scripted(bad).chain(), b["persona"], b["question"])
    assert ra.model == rb.model
    assert _body(ra) == _body(rb)


# -- D-43: a restricted, NON-script document cannot change the skip ---------------------------
def _with_restricted_policy(world: dict) -> dict:
    """World A'': the real world plus a restricted FinCrime page that is NOT approved wording,
    worded to rank for Amy's question. If Amy could read it, her quote could cite it and the model
    would be asked; she cannot, so her decision and response must not move."""
    w = copy.deepcopy(world)
    base = next(p for p in w["confluence"]["pages"] if p["id"] == "cf-1204")
    page = copy.deepcopy(base)
    page.update(id="cf-1298", space="fincrime", history=[], version=1,
                title="FinCrime handling notes for restricted accounts",
                body=("Notes\n\nWhy an account has been restricted, and what the account manager "
                      "should tell the customer: the restriction follows a review of the "
                      "account.\n\nDocument ref: CNRY-0f43a2"))
    w["confluence"]["pages"].append(page)
    return w


@pytest.fixture(scope="module")
def paired_policy(tmp_path_factory):
    import sys
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from evals.internal_brain.oracle import Oracle, filter_world, write_world
    tmp = tmp_path_factory.mktemp("paired_policy")
    wa = _with_restricted_policy(WORLD)
    oracle = Oracle(wa)
    persona = QUESTIONS["FC-q1"]["persona"]
    assert not oracle.can_read(persona, "confluence:cf-1298")
    assert oracle.can_read("mlro.tan", "confluence:cf-1298")
    wb, _rep = filter_world(wa, oracle.unreadable(persona))
    (tmp / "a").mkdir()
    (tmp / "b").mkdir()
    ea = E2E(tmp / "a", world_path=write_world(wa, tmp / "world_a.json"))
    eb = E2E(tmp / "b", world_path=write_world(wb, tmp / "world_b.json"))
    assert ea.index.doc("confluence:cf-1298") is not None
    assert eb.index.doc("confluence:cf-1298") is None
    return ea, eb


def test_a_hidden_non_script_document_does_not_change_the_skip(paired_policy):
    ea, eb = paired_policy
    q = QUESTIONS["FC-q1"]
    ra, fa = _ask(ea, q["persona"], q["question"])
    rb, fb = _ask(eb, q["persona"], q["question"])
    assert fa.calls == [] and fb.calls == []                         # skipped in both worlds
    assert ra.model == rb.model and ra.model.reason_code == QUOTED_APPROVED_SCRIPT
    assert _body(ra) == _body(rb)
    assert "confluence:cf-1298" not in ea.last("retrieval.decided").considered
    # the MLRO, who can read it, is not skipped there: the rule follows what the asker can read
    rm, fm = _ask(ea, "mlro.tan", q["question"])
    assert rm.model.reason_code != QUOTED_APPROVED_SCRIPT


# -- fix #4: log hygiene --------------------------------------------------------------------------
def test_http_client_request_lines_never_reach_the_log(caplog, monkeypatch):
    names = bootstrap.QUIET_LOGGERS
    saved = {n: logging.getLogger(n).level for n in names}
    host = "model-endpoint.example.invalid"

    def request() -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as c:
            c.post(f"https://{host}/v1/chat/completions", json={})
    try:
        # control: at INFO the client does print the URL, so the test can see a leak
        logging.getLogger("httpx").setLevel(logging.INFO)
        with caplog.at_level(logging.INFO):
            request()
        assert host in caplog.text
        caplog.clear()
        monkeypatch.setattr(logging, "basicConfig", lambda **kw: None)    # leave pytest's handlers
        bootstrap.configure_logging()
        with caplog.at_level(logging.INFO):
            for n in names:                                  # what the service sets at start-up
                assert logging.getLogger(n).level == logging.WARNING, n
            request()
            logging.getLogger("databridge_ib.llm").info("an IB line still logs")
        assert host not in caplog.text and "HTTP Request" not in caplog.text
        assert "an IB line still logs" in caplog.text
    finally:
        for n, lvl in saved.items():
            logging.getLogger(n).setLevel(lvl)


def test_http_loggers_quiet_names_cover_the_sdk_clients():
    for n in ("httpx", "httpx2", "httpcore"):
        assert n in bootstrap.QUIET_LOGGERS


def test_build_app_quiets_the_http_loggers(monkeypatch):
    seen = []
    monkeypatch.setattr(bootstrap, "quiet_http_loggers", lambda: seen.append(1))
    monkeypatch.setattr(bootstrap, "build_services", lambda cfg: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        bootstrap.build_app()
    assert seen == [1]


# -- fix #5: page or chat ------------------------------------------------------------------------
def test_the_decide_prompt_carries_the_case_overview_rule():
    from databridge_ib.views import composer as cmod
    from test_views_composer import FakeClaude
    fake = FakeClaude(lambda kind, text: json.dumps({"render": "page", "title": "Case overview",
                                                     "reason": "a case overview"}))
    comp = cmod.ClaudeComposer(cap_usd=10.0, client=fake)
    got, info, code = comp.decide("mlro.tan", "Why is the account restricted, and what should I say?",
                                  "The account was restricted [C1]. 3 of 9 transfers [C2].",
                                  "[C1] (confluence · FinCrime · Case record · v1 · 2026-10-01) x\n"
                                  "[C2] (drive · FinCrime · Analysis · v1 · 2026-10-02) y")
    assert got is not None and got.render == "page" and code == "none"
    system = fake.calls[0]["system"][0]["text"]
    assert system == cmod.DECIDE_SYSTEM and cmod.CASE_OVERVIEW_RULE in system
    low = cmod.CASE_OVERVIEW_RULE.lower()
    assert "status overview" in low and "two or more different documents" in low
    assert "velquarry" not in low
    # an approved script read out stays in the chat, by the prompt's own words
    assert "wording quoted from one document" in system


# -- fix #7: the page check's counters balance --------------------------------------------------
def _balanced(c) -> bool:
    return c.values_proposed == c.values_kept + c.values_dropped


@pytest.mark.parametrize("text, tags", [
    ("SG fee is 0.35% and HK fee is 0.4%", ["C1"]),                    # kept
    ("SG fee is 0.99%", ["C1"]),                                       # a value no source holds
    ("Zorblax Holdings sets 0.35% for 4 markets", ["C1"]),             # a name no source holds
    ("SG fee is 0.35%", ["C9"]),                                       # a tag outside the evidence
    ("Lorem ipsum dolor 0.35% 0.4%", ["C1"]),                          # bound to nothing
])
def test_sentence_checks_balance(text, tags):
    from databridge_ib.deliverables import verify as V
    from test_deliverables_verify import ev
    c = V.Check()
    V.check_sentence(text, tags, ev("SG fee 0.35%\nHK fee 0.4%"), c)
    assert _balanced(c), c.public()


def test_retract_moves_an_item_and_its_values_to_dropped():
    from databridge_ib.deliverables import verify as V
    c = V.Check(values_proposed=3, values_kept=3, items_proposed=1, items_kept=1)
    c.retract("label", 3)
    assert (c.items_kept, c.items_dropped, c.values_kept, c.values_dropped) == (0, 1, 0, 3)
    assert _balanced(c)


# -- the stand-in's answer timeout --------------------------------------------------------------
@pytest.mark.parametrize("raw, want", [("", 12.0), ("20", 20.0), ("0.1", 1.0), ("999", 120.0),
                                       ("abc", 12.0), ("nan", 12.0)])
def test_answer_timeout_from_the_environment(monkeypatch, raw, want):
    from databridge_ib import claude
    monkeypatch.setenv("IB_MODEL_ANSWER_TIMEOUT_S", raw)
    assert claude.answer_timeout_s() == want


def test_the_answer_call_uses_the_timeout_without_a_retry(monkeypatch, tmp_path):
    from databridge_ib import claude
    from databridge_ib.model_guard import ModelGuard
    monkeypatch.setenv("IB_MODEL_ANSWER_TIMEOUT_S", "7")
    seen = {}

    class Client:
        def with_options(self, **kw):
            seen.update(kw)
            return self

        @property
        def beta(self):
            return SimpleNamespace(messages=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            raise TimeoutError("slow")

    guard = ModelGuard(tmp_path / "guard")
    b = claude.ClaudeBackend(guard, client=Client())
    with pytest.raises(TimeoutError):
        b.generate(model="anthropic/x", messages=[{"role": "user", "content": "q"}])
    assert seen == {"timeout": 7.0, "max_retries": 0}


# -- the preflight: thresholds, labels, Rachel's facts ------------------------------------------
def _preflight():
    spec = importlib.util.spec_from_file_location("demo_preflight_story1",
                                                  ROOT / "scripts" / "demo_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("model, limit", [
    ({"provider": "deterministic", "model": "extractive-v1", "fallback_used": False}, 8.0),
    ({"provider": "deterministic", "model": "extractive-v1", "fallback_used": False,
      "reason_code": "quoted_approved_script"}, 8.0),                              # D-43: no call
    ({"provider": "deterministic", "model": "extractive-v1", "fallback_used": True,
      "reason_code": "model_rejected_evidence"}, 15.0),                            # a model was tried
    ({"provider": "standin", "model": "standin-1", "fallback_used": False}, 15.0),
    ({"provider": "x", "model": "hy3", "fallback_used": False}, 15.0),
    (None, 8.0),
])
def test_preflight_thresholds(model, limit):
    pf = _preflight()
    assert (pf.QUOTED_MAX_S, pf.MODEL_MAX_S) == (8.0, 15.0)
    assert pf.ask_limit_s(model) == limit
    assert pf.ask_limit_s(model, 5.0, 20.0) == (20.0 if limit == 15.0 else 5.0)


def test_amys_measured_model_answer_is_inside_the_model_threshold():
    """The QA run: Amy at 10.9 s failed the old 8 s bar. With a model call it is judged at 15 s;
    with the D-43 skip there is no model call and the quote is far under 8 s."""
    pf = _preflight()
    assert 10.9 <= pf.ask_limit_s({"provider": "standin", "model": "standin-1"})
    assert 10.9 > pf.ask_limit_s({"provider": "deterministic", "model": "extractive-v1",
                                  "reason_code": "quoted_approved_script"})


@pytest.mark.parametrize("model, want", [
    ({"provider": "standin", "model": "standin-1"}, "AI model"),
    ({"provider": "openai-compatible", "model": "my-model-7b"}, "AI model"),
    ({"provider": "x", "model": "hy3"}, "Hy3"),
    ({"provider": "deterministic", "model": "extractive-v1"}, "quoted (no model)"),
    ({"provider": "deterministic", "model": "extractive-v1", "reason_code": "quoted_approved_script"},
     "approved wording, quoted"),
    ({"provider": "deterministic", "model": "extractive-v1", "fallback_used": True,
      "reason_code": "model_unavailable"}, "quoted, the model did not reply in time"),
])
def test_preflight_rows_never_print_a_raw_model_id(model, want):
    got = _preflight().answer_by(model)
    assert got == want
    assert "standin" not in got and "tokenhub" not in got.lower()


def _rachel_body(sentences: list[tuple[str, str]]) -> dict:
    """An answer of ``(sentence, fact_id)`` pairs, each citing its fact's own document."""
    tags, cites, parts = {}, [], []
    for text, fid in sentences:
        doc = FACTS[fid]["doc"]
        if doc not in tags:
            tags[doc] = f"C{len(tags) + 1}"
            cites.append({"doc_id": doc, "tag": tags[doc]})
        parts.append(f"{text.rstrip('.')} [{tags[doc]}].")
    return {"answer": " ".join(parts), "citations": cites}


def test_preflight_rachel_facts_all_present():
    pf = _preflight()
    body = _rachel_body([(FACTS[k]["statement"], k) for k in pf.RACHEL_FACTS])
    got = pf.rachel_facts(body)
    assert len(got) == 3 and all(ok for _lab, ok in got), got


def test_preflight_rachel_facts_missing_ones_are_named():
    pf = _preflight()
    trig, dev, strf = pf.RACHEL_FACTS
    # the STR record cited, but its filing not said; the device link left out; a generic rule
    # sentence saying "filed" from ANOTHER document must not count as this case's filing
    rule_doc = next(f["id"] for f in WORLD["facts"]
                    if f["doc"] not in {FACTS[k]["doc"] for k in pf.RACHEL_FACTS}
                    and "mlro.tan" in f.get("permitted", []))
    body = _rachel_body([(FACTS[trig]["statement"], trig),
                         ("The decision record is confidential to the review team", strf),
                         ("Telling anyone that a report has been filed is an offence", rule_doc)])
    got = dict(pf.rachel_facts(body))
    labels = list(got)
    assert got[labels[0]] is False                  # STR filed: not said with the STR record
    assert got[labels[1]] is True                   # the trigger count
    assert got[labels[2]] is False                  # the "X of Y" device link
    assert labels[2].split(" (")[0].count(" of ") == 1


def test_preflight_rachel_facts_without_a_manifest(tmp_path):
    assert _preflight().rachel_facts({"answer": "x"}, tmp_path / "missing.json") == []
