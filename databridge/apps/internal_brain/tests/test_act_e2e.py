"""Act end to end over the generated Company A world: the REAL mock platforms (in-process), the REAL
connectors (including the Jira connector's live "Create issues" check and create call), a real
ledger, projection and answer store. Expectations are derived from the world's fact manifest.

* FC: the account manager's reply draft for the restricted customer carries the approved customer
  script and nothing from the FinCrime case; it is identical to the draft for a customer that does
  not exist (the manifest's P2 pair). She holds no Jira create role, so a ticket is the polite 409.
* S1: jdoe's follow-up ticket for the database migration question is created on the mock Jira, in
  a project he may create in, after his confirm, and the Brain indexes it from the webhook.
"""
from __future__ import annotations

import json

import pytest

from databridge_ib.actions import (ActionService, Fact, NotFound, Unavailable, script_lines,
                                   split_facts)
from databridge_ib.audit.content import AnswerStore
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.config import IbConfig
from databridge_ib.models import REFUSAL_TEXT
from e2e_world import E2E, load_manifest, load_world

WORLD = load_world()
MANIFEST = load_manifest()
FACTS = {f["id"]: f for f in WORLD["facts"]}
QUESTIONS = {q["id"]: q for q in WORLD["questions"]}
FC_PAIRS = [tuple(p) for p in MANIFEST["p2_pairs"] if QUESTIONS[p[0]]["scenario"] == "FC"]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("act-e2e")
    cfg = IbConfig(var_root=tmp / "var")
    ledger = AccessLedger(cfg)
    e = E2E(tmp, ledger=ledger, answers=AnswerStore(cfg))
    e.acts = ActionService(index=e.index, directory=e.directory, connectors=e.connectors,
                           ledger=ledger, answers=e.pipeline.answers,
                           projection=AuditProjection(ledger))
    return e


def _ask(env, qid):
    q = QUESTIONS[qid]
    return q, env.ask(q["persona"], q["question"])


def _strip(d):
    d = json.loads(json.dumps(d))
    for k in ("action_id", "req_id", "as_of"):
        d.pop(k)
    return json.dumps(d, sort_keys=True)


def test_account_manager_draft_is_the_approved_script_only(env):
    q, resp = _ask(env, "FC-q1")
    assert resp.answer != REFUSAL_TEXT
    amy = env.user(q["persona"])
    d = env.acts.draft(amy, resp.request_id, "reply")
    script = FACTS["CX-review-script"]
    assert {c["doc_id"] for c in d["citations"]} == {script["doc"]}
    assert d["model"]["model"] == "approved-script-v1"           # scripted wording: no model
    # the body is exactly the approved clauses, addressed to the customer (derived from the fact)
    want = script_lines([Fact(script["statement"], ("C1",))])
    assert len(want) >= 2 and [b["text"] for b in d["body"]] == [f.text for f in want]
    assert "your account" in d["text"].lower() and "account managers" not in d["text"].lower()
    text = json.dumps(d)
    fincrime = [FACTS[f] for f in q["must_not_contain_facts"] if f.startswith("FINCRIME-")]
    assert fincrime                                                   # non-vacuous
    for f in fincrime:
        assert f["doc"] not in text and f["canary"] not in text
        assert f["statement"][:50] not in text
    assert "velquarry" not in text.lower()
    # every draft sentence comes from a cited sentence of the answer Amy already received
    assert {b["text"] for b in d["body"]} <= {f.text for f in script_lines(split_facts(resp.answer))}


@pytest.mark.parametrize("pair", FC_PAIRS, ids=["-".join(p) for p in FC_PAIRS])
def test_restricted_customer_draft_equals_nonexistent_customer_draft(env, pair):
    outs = []
    for qid in pair:
        q, resp = _ask(env, qid)
        user = env.user(q["persona"])
        try:
            outs.append(_strip(env.acts.draft(user, resp.request_id, "reply")))
        except NotFound:
            outs.append("404")
    assert outs[0] == outs[1]


def test_account_manager_cannot_open_a_ticket_and_hears_why_never(env):
    q, resp = _ask(env, "FC-q1")
    with pytest.raises(Unavailable):
        env.acts.propose_ticket(env.user(q["persona"]), resp.request_id)
    failed = [e for e in env.ledger.events() if e.kind == "action.failed"][-1].event
    assert failed.stage == "propose" and failed.reason_code == "not_permitted"


def test_follow_up_ticket_is_created_on_the_mock_jira_after_confirm(env):
    q, resp = _ask(env, "S1-q1")
    assert resp.citations
    jdoe = env.user(q["persona"])
    world_projects = {p["key"]: p for p in env.world["jira"]["projects"]}
    before = {k for k in env.mock.state.world.issues}
    p = env.acts.propose_ticket(jdoe, resp.request_id)
    assert {k for k in env.mock.state.world.issues} == before        # nothing created yet
    for opt in p["projects"]:                                         # derived from the world
        assert env.mock.state.world.jira_can_create(jdoe.id, opt["key"])
    assert {o["key"] for o in p["projects"]} == {
        k for k in world_projects if env.mock.state.world.jira_can_create(jdoe.id, k)}
    out = env.acts.confirm_ticket(jdoe, p["proposal_id"])
    key = out["issue"]["key"]
    assert key.startswith(p["project"]["key"] + "-") and key not in before
    got = env.svc.get(f"/jira/rest/api/3/issue/{key}").json()["fields"]
    assert got["summary"] == p["summary"]
    assert got["reporter"]["accountId"] == jdoe.platform_ids["jira"]
    assert resp.request_id in json.dumps(got["description"])
    # the webhook reached the Brain: the new issue is indexed and jdoe can read it (reporter)
    assert env.index.doc(f"jira:{key}") is not None
    assert f"jira:{key}" in env.index.readable(jdoe, getattr(env.directory, "epoch", 0))
    kinds = [e.kind for e in env.ledger.events() if e.data.get("req_id") == resp.request_id]
    assert kinds[-3:] == ["action.proposed", "action.confirmed", "action.executed"]
