"""Act, round-4 Codex review fixes (docs/finals/reviews/round4_codex_cloud_work.md; contract v3.2).

- A1 write-ahead: ``action.requested`` (stage ``accepted``) before the live source check, stage
  ``decided`` before any model call, and stage ``replayed`` before a repeat confirm of a created
  proposal is answered; a ledger failure is the 503 and nothing else happens;
- A2 the draft floor: negation, numbers and names must match the source sentence;
- A5 one create at a time per answer (two proposals confirmed together never both create);
- A7 the proposal caps hold under concurrent requests and on release;
- A8 the unknown-outcome holds are swept when they expire;
- R1 only the first over-limit refusal per person per window is ledgered.

Same world and helpers as test_act.py.
"""
from __future__ import annotations

import threading
import time

import pytest

from databridge_ib import actions as actmod
from databridge_ib.actions import TICKET_OUTCOME_UNKNOWN, supported

from test_act import _ask, _c, _draft_chain, _events, _fail_actions, _propose, _world

CONFIRM = "/ib/act/ticket/confirm"
PROPOSE = "/ib/act/ticket/propose"
DRAFT = "/ib/act/draft"


def _live_checks(svc):
    return sum(c.calls for c in svc.connectors.values())


# ------------------------------------------------------------------------------------------------
# A1: write-ahead order
# ------------------------------------------------------------------------------------------------
def test_draft_ledger_down_is_503_before_any_source_check_or_model_call(tmp_path):
    captured: list = []
    app, svc = _world(tmp_path, chain=_draft_chain(captured, lambda ctx: "x [C1]."))
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    checks, calls = _live_checks(svc), len(captured)
    _fail_actions(svc, ("action.requested",))
    r = jd.post(DRAFT, json={"req_id": rid})
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    assert _live_checks(svc) == checks          # no live Gate 2 ran
    assert len(captured) == calls               # no model was asked
    assert _events(svc, "action.proposed") == []


def test_draft_records_accepted_then_decided_before_the_model_then_proposed(tmp_path):
    seen: list = []
    holder: dict = {}

    def reply(ctx):                             # what the ledger held when the model was asked
        seen.append([(e.kind, getattr(e, "stage", None)) for e in _events(holder["svc"])
                     if e.kind.startswith("action.")])
        sent = ctx.splitlines()[0].split(") ", 1)[1].rstrip(".")
        return f"{sent} [C1]."

    app, svc = _world(tmp_path, chain=_draft_chain([], reply))
    holder["svc"] = svc
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    d = jd.post(DRAFT, json={"req_id": rid}).json()
    assert seen == [[("action.requested", "accepted"), ("action.requested", "decided")]]
    acts = [e for e in _events(svc) if e.kind.startswith("action.")]
    assert [(e.kind, getattr(e, "stage", None)) for e in acts] == [
        ("action.requested", "accepted"), ("action.requested", "decided"), ("action.proposed", None)]
    acc, dec, prop = acts
    assert acc.req_id is None                   # ownership is not known yet: no id (rule 4)
    assert acc.action_id == dec.action_id == prop.action_id == d["action_id"]
    assert dec.req_id == rid and set(dec.doc_ids) >= set(prop.doc_ids)
    assert dec.model_planned is True


def test_decided_ledger_down_is_503_and_no_model_call(tmp_path):
    captured: list = []
    app, svc = _world(tmp_path, chain=_draft_chain(captured, lambda ctx: "x [C1]."))
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    calls = len(captured)
    real = svc.ledger.append

    def append(ev):
        if ev.kind == "action.requested" and ev.stage == "decided":
            raise RuntimeError("ledger down")
        return real(ev)
    svc.ledger.append = append
    r = jd.post(DRAFT, json={"req_id": rid})
    assert r.status_code == 503 and len(captured) == calls


def test_propose_ledger_down_is_503_before_any_source_check(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    checks, jira = _live_checks(svc), svc.connectors["jira"]
    _fail_actions(svc, ("action.requested",))
    r = jd.post(PROPOSE, json={"req_id": rid})
    assert r.status_code == 503
    assert _live_checks(svc) == checks and jira.creatable_calls == 0
    assert svc.actions._proposals == {}


def test_a_replay_with_its_receipt_on_record_is_still_ledgered(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    p = _propose(jd, rid)
    first = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert first.status_code == 200
    n = len(_events(svc))
    again = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert again.status_code == 200 and again.json() == first.json()
    new = _events(svc)[n:]
    assert [(e.kind, e.stage, e.action_id, e.req_id) for e in new] == [
        ("action.requested", "replayed", p["proposal_id"], rid)]
    assert len(_events(svc, "action.executed")) == 1           # never a second receipt
    # the replay row is write-ahead too: a ledger failure is the 503, never the stored result
    _fail_actions(svc, ("action.requested",))
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 503 and "issue" not in r.text
    assert len(svc.connectors["jira"].created) == 1


def test_an_invalid_confirm_is_ledgered_before_the_422(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    p = _propose(jd, _ask(jd)["request_id"])
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"], "project": "NOPE"})
    assert r.status_code == 422
    f = _events(svc, "action.failed")[-1]
    assert (f.stage, f.reason_code, f.action_id) == ("confirm", "invalid", p["proposal_id"])
    assert jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]}).status_code == 200


# ------------------------------------------------------------------------------------------------
# A2: the draft floor
# ------------------------------------------------------------------------------------------------
def test_floor_negation_must_match_its_source():
    assert not supported("We will waive charges [C1].", "We will not waive charges.")
    assert not supported("We will not waive charges [C1].", "We will waive charges.")
    assert not supported("We never waive charges [C1].", "We waive charges.")
    assert not supported("We can waive charges [C1].", "We can't waive charges.")
    assert not supported("We will waive charges [C1].", "No charges will be waived. We waive fees.")
    assert supported("We will not waive charges [C1].", "We will not waive charges.")
    assert supported("We won't waive the charges [C1].", "We will not waive charges.")
    assert not supported("账户将被退款", "账户不会被退款")                   # CJK negation dropped


def test_floor_a_changed_number_fails():
    src = "Refunds are paid within 14 business days."
    assert supported("Refunds are paid within 14 business days [C1].", src)
    assert not supported("Refunds are paid within 2 business days [C1].", src)
    assert not supported("Refunds are paid within 14.5 business days [C1].", src)
    # a number that is in the sources, but in another sentence about something else
    two = src + " Escalate after 2 days."
    assert not supported("Refunds are paid within 2 business days [C1].", two)
    assert supported("Escalate after 2 days [C1].", two)


def test_floor_a_novel_name_fails_outright():
    src = "Staff can approve customer refund requests."
    assert supported("Staff can approve customer refund requests [C1].", src)
    assert not supported("Mallory can approve customer refund requests [C1].", src)
    assert not supported("Staff and Mallory can approve customer refund requests [C1].", src)
    assert not supported("Staff can approve customer refund requests via Zorblax [C1].", src)
    # a sentence opener is not a name; a name that is in the source is fine
    assert supported("Currently, staff can approve customer refund requests [C1].", src)
    assert supported("Ana Lim approves the refund requests [C1].",
                     "Ana Lim approves refund requests.")
    # the 80% rule still holds for ordinary words
    assert supported("Staff may approve customer refund requests [C1].", src)
    assert not supported("Staff may quickly approve customer refund appeals [C1].", src)


def test_a_model_draft_that_drops_a_negation_falls_back_to_the_cited_sentences(tmp_path):
    def reply(ctx):
        sent = ctx.splitlines()[0].split(") ", 1)[1].rstrip(".")
        return f"It is not true that {sent[0].lower() + sent[1:]} [C1]."

    app, svc = _world(tmp_path, chain=_draft_chain([], reply))
    jd = _c(app, svc, "jdoe")
    ans = _ask(jd)
    d = jd.post(DRAFT, json={"req_id": ans["request_id"]}).json()
    assert d["model"]["provider"] == "deterministic" and d["model"]["fallback_used"] is True
    assert "not true" not in d["text"]


# ------------------------------------------------------------------------------------------------
# A5 / A7 / A8: concurrency and holds
# ------------------------------------------------------------------------------------------------
def test_two_proposals_confirmed_together_never_both_create(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    a, b = _propose(jd, rid), _propose(jd, rid)
    jira = svc.connectors["jira"]
    entered, go = threading.Event(), threading.Event()
    real = type(jira).create_issue

    def slow(user, project_key, summary, description):
        entered.set()
        assert go.wait(5)
        return real(jira, user, project_key, summary, description)
    jira.create_issue = slow
    user = svc.directory.get("jdoe")
    out: dict = {}

    def first():
        out["a"] = svc.actions.confirm_ticket(user, a["proposal_id"])
    t = threading.Thread(target=first)
    t.start()
    assert entered.wait(5)                      # A's create is in flight at the source
    with pytest.raises(actmod.OutcomeUnknown):
        svc.actions.confirm_ticket(user, b["proposal_id"])
    go.set()
    t.join(5)
    assert out["a"]["status"] == "created" and len(jira.created) == 1
    f = _events(svc, "action.failed")[-1]
    assert (f.reason_code, f.action_id) == ("outcome_unknown_hold", b["proposal_id"])
    # B was never consumed: once A is done it may be confirmed (a second ticket on purpose)
    assert svc.actions._proposals[b["proposal_id"]].state == "pending"


def test_the_store_cap_holds_under_concurrent_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr(actmod, "MAX_PROPOSALS", 3)
    monkeypatch.setattr(actmod, "MAX_PROPOSALS_PER_PERSON", 2)
    app, svc = _world(tmp_path, can_create={"jdoe": {"PAY"}, "priya": {"PAY"}})
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    rid, prid = _ask(jd)["request_id"], _ask(pr)["request_id"]
    for _ in range(2):
        _propose(pr, prid)                      # 2 of 3 slots: priya's, live
    gate = threading.Barrier(2, timeout=2)
    real = svc.ledger.append

    def append(ev):                             # both proposers reach the record together
        if ev.kind == "action.proposed":
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
        return real(ev)
    svc.ledger.append = append
    codes: list = []
    ts = [threading.Thread(target=lambda: codes.append(
        jd.post(PROPOSE, json={"req_id": rid}).status_code)) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert sorted(codes) == [200, 409]
    assert len(svc.actions._proposals) <= actmod.MAX_PROPOSALS


def test_a_released_proposal_respects_the_per_person_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(actmod, "MAX_PROPOSALS_PER_PERSON", 2)
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    user = svc.directory.get("jdoe")
    first = _propose(jd, rid)
    p, prev = svc.actions._claim(user, first["proposal_id"])
    assert prev == "pending"
    for _ in range(2):
        _propose(jd, rid)                       # in-flight ones are not pending: 2 more fit
    svc.actions._release(p)
    pending = [q for q in svc.actions._proposals.values() if q.sub == "jdoe" and q.state == "pending"]
    assert len(pending) <= actmod.MAX_PROPOSALS_PER_PERSON
    assert first["proposal_id"] in {q.proposal_id for q in pending}   # the one in use is kept


def test_expired_unknown_holds_are_swept(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    old = time.monotonic() - actmod.UNKNOWN_HOLD_S - 1
    for i in range(50):
        svc.actions._unknown[(f"someone{i}", f"r{i}")] = old
    svc.actions._unknown[("live", "r")] = time.monotonic()
    _propose(jd, rid)
    assert set(svc.actions._unknown) == {("live", "r")}


# ------------------------------------------------------------------------------------------------
# R1: over-limit refusals are not ledgered one by one
# ------------------------------------------------------------------------------------------------
def test_only_the_first_over_limit_act_call_per_window_is_ledgered(tmp_path):
    app, svc = _world(tmp_path)
    svc.actions._rate = actmod._Limiter(1, 60)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    assert jd.post(DRAFT, json={"req_id": rid}).status_code == 200
    for _ in range(5):
        assert jd.post(DRAFT, json={"req_id": rid}).status_code == 429
    rate = [f for f in _events(svc, "action.failed") if f.stage == "rate"]
    assert len(rate) == 1
    assert len(_events(svc, "action.requested")) == 1      # refused calls record no attempt


def test_unknown_body_is_unchanged():
    assert TICKET_OUTCOME_UNKNOWN.startswith("We could not confirm whether the ticket was created")


def test_ticket_description_states_the_link_rule_once_and_ends_with_a_reference():
    d = actmod.ticket_description("r" * 32, ["https://x/ib/open/a", "https://x/ib/open/b"])
    assert d.count("opens only for people") == 1
    assert d.splitlines()[-1] == "Reference: " + "r" * 32
    assert "(request " not in d
