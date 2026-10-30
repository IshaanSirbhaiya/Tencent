"""Act, round-2 review fixes (docs/finals/reviews/round2_*_act_tools_kit.md; contract v3.1).

- per-person rate limit (429, ``action.failed`` stage ``rate``) and a per-person proposal cap, so one
  person's proposals never evict another person's (Opus #1, Codex #8);
- opaque source links in the ticket (``/ib/open/<token>``): Gate 1 + Gate 2 for whoever clicks, and
  the one 404 when they cannot open it (Opus #2, rule 4);
- confirm re-checks the cited sources for the confirming person (Opus #3, Codex #3);
- an unknown create outcome is never "Nothing was changed"; a repeat confirm replays a known result;
  no second create while the outcome is unknown; a lost ``action.executed`` receipt is replayable
  (Codex #1, Opus #4).

Same world and helpers as test_act.py.
"""
from __future__ import annotations

import re

from databridge_ib import actions as actmod
from databridge_ib.actions import TICKET_OUTCOME_UNKNOWN, TICKET_UNAVAILABLE

from test_act import _ask, _c, _events, _fail_actions, _propose, _world

CONFIRM = "/ib/act/ticket/confirm"
PROPOSE = "/ib/act/ticket/propose"
UNKNOWN_BODY = {"error": "outcome_unknown", "message": TICKET_OUTCOME_UNKNOWN}
POLITE = {"error": "unavailable", "message": TICKET_UNAVAILABLE}
LINK = re.compile(r"Source \d+: (\S+)")


def _links(description):
    return LINK.findall(description)


# ------------------------------------------------------------------------------------------------
# limits
# ------------------------------------------------------------------------------------------------
def test_act_is_rate_limited_per_person_and_ledgered(tmp_path):
    app, svc = _world(tmp_path, can_create={"jdoe": {"PAY"}, "priya": {"PAY"}})
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    rid = _ask(jd)["request_id"]
    for i in range(actmod.RATE_N):
        path = "/ib/act/draft" if i % 2 else PROPOSE
        assert jd.post(path, json={"req_id": rid}).status_code == 200
    r = jd.post(PROPOSE, json={"req_id": rid})
    assert r.status_code == 429 and r.json() == {"detail": "too many requests"}
    assert jd.post("/ib/act/draft", json={"req_id": rid}).status_code == 429
    fail = _events(svc, "action.failed")[-1]
    assert (fail.stage, fail.reason_code, fail.req_id, fail.sub) == ("rate", "rate_limited", None,
                                                                     "jdoe")
    # another person is not affected
    assert pr.post(PROPOSE, json={"req_id": _ask(pr)["request_id"]}).status_code == 200


def test_one_persons_proposals_never_evict_anothers(tmp_path, monkeypatch):
    monkeypatch.setattr(actmod, "MAX_PROPOSALS", 4)
    monkeypatch.setattr(actmod, "MAX_PROPOSALS_PER_PERSON", 2)
    app, svc = _world(tmp_path, can_create={"jdoe": {"PAY"}, "priya": {"PAY"}})
    svc.actions._rate = actmod._Limiter(1000, 60)
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    mine = _propose(jd, _ask(jd)["request_id"])
    prid = _ask(pr)["request_id"]
    theirs = [_propose(pr, prid)["proposal_id"] for _ in range(12)]    # far more than the cap
    # priya's flood replaced only her own oldest proposals
    assert pr.post(CONFIRM, json={"proposal_id": theirs[0]}).status_code == 404
    assert pr.post(CONFIRM, json={"proposal_id": theirs[-1]}).status_code == 200
    assert jd.post(CONFIRM, json={"proposal_id": mine["proposal_id"]}).status_code == 200


def test_a_full_store_refuses_rather_than_evicting_others(tmp_path, monkeypatch):
    monkeypatch.setattr(actmod, "MAX_PROPOSALS", 3)
    monkeypatch.setattr(actmod, "MAX_PROPOSALS_PER_PERSON", 2)
    app, svc = _world(tmp_path, can_create={"jdoe": {"PAY"}, "priya": {"PAY"}})
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    rid, prid = _ask(jd)["request_id"], _ask(pr)["request_id"]
    mine = _propose(jd, rid)
    theirs = [_propose(pr, prid)["proposal_id"] for _ in range(2)]        # store full (3)
    r = jd.post(PROPOSE, json={"req_id": rid})        # jdoe holds 1 < 2: he may not evict priya's
    assert r.status_code == 409 and r.json() == POLITE
    assert _events(svc, "action.failed")[-1].reason_code == "capacity"
    for pid in theirs:
        assert pr.post(CONFIRM, json={"proposal_id": pid}).status_code == 200
    assert jd.post(CONFIRM, json={"proposal_id": mine["proposal_id"]}).status_code == 200


def test_an_in_flight_proposal_is_never_evicted_and_is_restored(tmp_path):
    """Codex #8: eviction interleaved with a confirm whose write-ahead append fails."""
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    p = _propose(jd, _ask(jd)["request_id"])
    user = svc.directory.get("jdoe")
    got, prev = svc.actions._claim(user, p["proposal_id"])
    assert prev == "pending" and got.state == "inflight"
    with svc.actions._lock:
        svc.actions._evict_locked("jdoe")
    assert p["proposal_id"] in svc.actions._proposals
    svc.actions._release(got)
    assert jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]}).status_code == 200


# ------------------------------------------------------------------------------------------------
# opaque source links
# ------------------------------------------------------------------------------------------------
def test_ticket_links_are_opaque_and_open_only_for_who_may_open_them(tmp_path):
    app, svc = _world(tmp_path)
    jd, pr = _c(app, svc, "jdoe"), _c(app, svc, "priya")
    p = _propose(jd, _ask(jd)["request_id"])
    links = _links(p["description"])
    assert links and len(links) == len({c["doc_id"] for c in p["citations"]})
    for c in p["citations"]:
        for leak in (c["doc_id"], c["deep_link"], c["title"], c["container"], c["container_name"]):
            assert leak not in p["description"]
    unknown = jd.get("/ib/open/" + "0" * 32, follow_redirects=False)
    malformed = jd.get("/ib/open/not-a-token", follow_redirects=False)
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.content == malformed.content and unknown.json() == {"detail": "not found"}
    for c in p["citations"]:
        path = "/ib/open/" + svc.actions.open_link(c["doc_id"]).rsplit("/", 1)[1]
        assert svc.actions.open_link(c["doc_id"]).endswith(path)
        # the proposer can open it: a redirect to the source, re-checked now
        r = jd.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == c["deep_link"]
        # someone the SOURCE denies gets exactly the unknown-id 404 (rule 4)
        svc.connectors[c["platform"]].readers[c["doc_id"]].discard("priya")
        r = pr.get(path, follow_redirects=False)
        assert r.status_code == 404 and r.content == unknown.content
        # and so does the proposer once revoked
        svc.connectors[c["platform"]].readers[c["doc_id"]].discard("jdoe")
        r = jd.get(path, follow_redirects=False)
        assert r.status_code == 404 and r.content == unknown.content
    viewed = _events(svc, "doc.viewed")
    assert viewed and {v.doc_id for v in viewed} >= {c["doc_id"] for c in p["citations"]}
    assert all(v.doc_id != "0" * 32 for v in viewed)


def test_open_link_is_write_ahead_and_needs_a_session(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    p = _propose(jd, _ask(jd)["request_id"])
    path = "/ib/open/" + _links(p["description"])[0].rsplit("/", 1)[1]
    from fastapi.testclient import TestClient
    assert TestClient(app).get(path, follow_redirects=False).status_code == 401
    _fail_actions(svc, ("doc.viewed",))
    r = jd.get(path, follow_redirects=False)
    assert r.status_code == 503 and "location" not in r.headers


def test_open_link_survives_a_restart_and_differs_by_key(tmp_path):
    from databridge_ib.app import open_link_key
    a, b = open_link_key("k" * 40), open_link_key("k" * 40)
    assert a == b and a != open_link_key("j" * 40)
    assert actmod.open_token(a, "jira:PAY-1") == actmod.open_token(b, "jira:PAY-1")
    assert "PAY" not in actmod.open_token(a, "jira:PAY-1")


# ------------------------------------------------------------------------------------------------
# confirm re-checks the cited sources
# ------------------------------------------------------------------------------------------------
def test_confirm_rechecks_the_cited_sources_for_the_confirming_person(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    p = _propose(jd, rid)
    c = p["citations"][-1]                       # revoke ONE cited document at its source
    svc.connectors[c["platform"]].readers[c["doc_id"]].discard("jdoe")
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 409 and r.json() == POLITE           # never which one
    assert c["doc_id"] not in r.text
    assert svc.connectors["jira"].created == []
    fail = _events(svc, "action.failed")[-1]
    assert (fail.stage, fail.reason_code) == ("confirm", "sources_changed")
    kinds = [e.kind for e in _events(svc) if e.req_id == rid][3:]
    assert kinds == ["action.proposed", "action.confirmed", "action.failed"]


# ------------------------------------------------------------------------------------------------
# unknown outcomes, replay, receipts
# ------------------------------------------------------------------------------------------------
def _committed_then_timeout(jira):
    """Jira commits the issue, then the response is lost (a client timeout)."""
    from databridge_ib.connectors._http import SourceFailure
    real = type(jira).create_issue

    def create(user, project_key, summary, description):
        real(jira, user, project_key, summary, description)
        raise SourceFailure("source_timeout")
    jira.create_issue = create


def test_unknown_outcome_is_not_nothing_changed_and_blocks_a_second_create(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    jira = svc.connectors["jira"]
    p = _propose(jd, rid)
    _committed_then_timeout(jira)
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 409 and r.json() == UNKNOWN_BODY
    assert "Nothing was changed" not in r.text and len(jira.created) == 1
    fail = _events(svc, "action.failed")[-1]
    assert fail.stage == "execute" and fail.reason_code == "outcome_unknown:source_timeout"
    del jira.create_issue                        # the source is healthy again
    # the same proposal never creates again
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 409 and r.json() == UNKNOWN_BODY
    # nor does a new proposal for the same answer while the outcome is unknown
    r = jd.post(PROPOSE, json={"req_id": rid})
    assert r.status_code == 409 and r.json() == UNKNOWN_BODY
    assert len(jira.created) == 1


def test_a_proposal_made_before_an_unknown_outcome_is_held_too(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    jira = svc.connectors["jira"]
    first, second = _propose(jd, rid), _propose(jd, rid)
    jira.fail_create = "source_timeout"
    assert jd.post(CONFIRM, json={"proposal_id": first["proposal_id"]}).json() == UNKNOWN_BODY
    jira.fail_create = None
    r = jd.post(CONFIRM, json={"proposal_id": second["proposal_id"]})
    assert r.status_code == 409 and r.json() == UNKNOWN_BODY and jira.created == []


def test_malformed_success_and_5xx_are_unknown_4xx_is_definite(tmp_path):
    from databridge_ib.connectors._http import SourceFailure
    cases = [(lambda *a: {}, UNKNOWN_BODY),
             (lambda *a: (_ for _ in ()).throw(SourceFailure("source_error", 502)), UNKNOWN_BODY),
             (lambda *a: (_ for _ in ()).throw(SourceFailure("source_bad_payload", 201)),
              UNKNOWN_BODY),
             (lambda *a: (_ for _ in ()).throw(SourceFailure("source_error")), UNKNOWN_BODY),
             (lambda *a: (_ for _ in ()).throw(SourceFailure("source_error", 400)), POLITE),
             (lambda *a: (_ for _ in ()).throw(SourceFailure("source_rate_limited", 429)), POLITE)]
    for i, (fn, want) in enumerate(cases):
        app, svc = _world(tmp_path / str(i))
        jd = _c(app, svc, "jdoe")
        p = _propose(jd, _ask(jd)["request_id"])
        svc.connectors["jira"].create_issue = fn
        r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
        assert (r.status_code, r.json()) == (409, want), i
        # a definite failure: a repeat confirm is the same polite message, never a create
        again = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
        assert (again.status_code, again.json()) == (409, want), i


def test_lost_executed_receipt_is_503_then_replayable_without_a_second_create(tmp_path):
    app, svc = _world(tmp_path)
    jd = _c(app, svc, "jdoe")
    rid = _ask(jd)["request_id"]
    p = _propose(jd, rid)
    real = svc.ledger.append
    _fail_actions(svc, ("action.executed",))
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    assert len(svc.connectors["jira"].created) == 1
    # still down: the replay re-tries the receipt and is the 503 again, never a second create
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 503 and len(svc.connectors["jira"].created) == 1
    svc.ledger.append = real
    r = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert r.status_code == 200 and r.json()["issue"]["key"] == svc.connectors["jira"].created[0]["key"]
    assert [e.result_ref for e in _events(svc, "action.executed")] == [r.json()["issue"]["key"]]
    again = jd.post(CONFIRM, json={"proposal_id": p["proposal_id"]})
    assert again.json() == r.json() and len(_events(svc, "action.executed")) == 1
    assert len(svc.connectors["jira"].created) == 1
