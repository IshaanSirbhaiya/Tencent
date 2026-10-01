"""INT4 — a person decides a held drift adaptation from the app.

Adapter agents HOLD a drift adaptation that needs a person (breaking, subject-touching, new write
tool, scope widening). Before INT4 no gateway route called `approve_adaptation` /
`reject_adaptation`, so a held part could only be released from the CLI or from code.

What is proven here, over HTTP with the real `AdapterAgentManager` + `AdaptationEngine` (a fake
ingest service only; no MCP server, no model):

* `GET /adapter-agents/{id}/adaptations?status=pending` lists what is held: its kind, what changed,
  why it was held, and when — read from the engine's own record, never restated;
* approve / reject are decided as the AUTHENTICATED principal; a ``by`` in the body is ignored;
* only the agent's owner (the ``by`` of its ``create`` history entry) decides; anyone else is 403;
* deciding something already decided is 409; an unknown adaptation is 404; a rejection needs a
  reason (422), and a rejected part stays held;
* without a credential every new route is 401 (also covered by the derived route sweep in
  `test_platform_routes.test_every_platform_route_refuses_without_a_credential`).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import auth_headers

# `env` is a fixture: importing it here makes it available to this module's tests.
from test_platform_routes import _aa_spec, _mount, env  # noqa: F401

OWNER = "analyst-7"
OTHER = "fcc-3"
AGENT = "held_sources"
CONN = "held-files"


def _seed_held(rt, agent: str, conn: str, aid: str, *, kind: str = "removed") -> dict:
    """A pending adaptation exactly as `AdaptationEngine._handle` files one when a change is
    ASKED, with the connection held for it (no proposal artifact: approval releases the hold)."""
    drift = {"drift_id": f"d_{aid}", "kind": kind, "unit": f"table:{conn}/orders",
             "detail": f"column 'region' {kind} in table 'orders'", "table": "orders",
             "column": "region", "subject_touching": False}
    rec = {"adaptation_id": aid, "agent_id": agent, "connection": conn,
           "unit": drift["unit"], "units": [drift["unit"]], "stage": "pre_run",
           "drift_ids": [drift["drift_id"]], "drifts": [drift],
           "classification": {drift["drift_id"]: {
               "asked": True, "why": f"{kind!r} is in adapt.always_ask"}},
           "action": f"hold table 'orders' until a person decides ({kind})",
           "notes": [], "problems": [],
           "proposal": {"artifact": None, "content": None, "previous": None},
           "hold": {"level": "connection", "via": "connection",
                    "why": f"{kind} needs a person"},
           "observed_shape": None, "evidence": {"drifts": [drift["detail"]]},
           "produced_by": {}, "created_at": "2026-10-01T09:00:00Z", "agent_run_id": "r1",
           "at": "2026-10-01T09:00:00Z", "status": "pending"}
    eng = rt.manager.adaptation
    eng.store.append(agent, rec)
    eng.store.set_hold(agent, conn, aid, {"why": rec["action"], "since": rec["created_at"]})
    return rec


def _app_with_held(env, aids=("ad_one",)):
    app, rt, _ = _mount(env)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        r = c.post("/adapter-agents", json={"spec": _aa_spec(AGENT, [CONN])})
        assert r.status_code == 200, r.text
    recs = [_seed_held(rt, AGENT, CONN, aid) for aid in aids]
    return app, rt, recs


def test_pending_adaptations_are_listed_with_kind_change_reason_and_time(env):
    app, rt, recs = _app_with_held(env, aids=("ad_one", "ad_two"))
    with TestClient(app, headers=auth_headers(OTHER)) as c:            # any principal may read
        r = c.get(f"/adapter-agents/{AGENT}/adaptations", params={"status": "pending"})
    assert r.status_code == 200, r.text
    body = r.json()
    # derived from the engine's own view, and non-vacuous
    expected = [x["adaptation_id"] for x in rt.manager.pending_adaptations(AGENT)]
    assert expected and [a["adaptation_id"] for a in body["adaptations"]] == expected
    assert body["owner"] == OWNER and body["may_decide"] is False
    item = body["adaptations"][0]
    src = recs[0]
    assert item["kinds"] == sorted({d["kind"] for d in src["drifts"]})
    assert item["changed"] == [d["detail"] for d in src["drifts"]]
    assert item["held_because"] == [v["why"] for v in src["classification"].values()
                                    if v["asked"]]
    assert item["created_at"] == src["created_at"]
    assert item["status"] == "pending" and item["connection"] == CONN


def test_the_owner_approves_as_the_authenticated_principal_and_twice_is_409(env):
    app, rt, _ = _app_with_held(env)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        mine = c.get(f"/adapter-agents/{AGENT}/adaptations").json()
        assert mine["may_decide"] is True
        r = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/approve",
                   json={"reason": "the column really was dropped", "by": "mallory"})
        assert r.status_code == 200, r.text
        got = r.json()["adaptation"]
        assert got["status"] == "approved"
        assert got["decided_by"]["principal_id"] == OWNER               # never the body's `by`
        assert got["decided_by"]["authenticated_by"] != ""
        assert rt.manager.adaptation.store.holds(AGENT).get(CONN, {}) == {}   # hold released
        assert c.get(f"/adapter-agents/{AGENT}/adaptations").json()["adaptations"] == []
        again = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/approve")
        assert again.status_code == 409
        assert again.json()["refusal"]["code"] == "ADAPTATION_ALREADY_DECIDED"
        rej = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/reject",
                     json={"reason": "too late"})
        assert rej.status_code == 409


def test_the_owner_rejects_with_a_reason_and_the_part_stays_held(env):
    app, rt, _ = _app_with_held(env)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        blank = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/reject", json={})
        assert blank.status_code == 422
        assert blank.json()["refusal"]["code"] == "REASON_REQUIRED"
        r = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/reject",
                   json={"reason": "revert the source instead", "by": "mallory"})
        assert r.status_code == 200, r.text
        got = r.json()["adaptation"]
        assert got["status"] == "rejected" and got["decided_by"]["principal_id"] == OWNER
        assert got["note"] == "revert the source instead"
        assert rt.manager.adaptation.store.holds(AGENT).get(CONN)        # still held
        listed = c.get(f"/adapter-agents/{AGENT}/adaptations",
                       params={"status": "rejected"}).json()["adaptations"]
        assert [a["adaptation_id"] for a in listed] == ["ad_one"]
        again = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/reject",
                       json={"reason": "again"})
        assert again.status_code == 409


def test_only_the_owner_decides(env):
    app, rt, _ = _app_with_held(env)
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        a = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/approve")
        b = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/reject", json={"reason": "x"})
    for r in (a, b):
        assert r.status_code == 403, r.text
        assert r.json()["refusal"]["code"] == "NOT_ADAPTER_AGENT_OWNER"
    assert [x["status"] for x in rt.manager.adaptations(AGENT)] == ["pending"]


def test_unknown_adaptation_or_agent_is_404_and_anonymous_is_401(env):
    app, _, _ = _app_with_held(env)
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        r = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_nope/approve")
        assert r.status_code == 404 and r.json()["refusal"]["code"] == "UNKNOWN_ADAPTATION"
        g = c.get("/adapter-agents/no_such_agent/adaptations")
        assert g.status_code == 404
    anon = TestClient(app)
    for method, path in (("GET", f"/adapter-agents/{AGENT}/adaptations"),
                         ("POST", f"/adapter-agents/{AGENT}/adaptations/ad_one/approve"),
                         ("POST", f"/adapter-agents/{AGENT}/adaptations/ad_one/reject")):
        assert anon.request(method, path, json={}).status_code == 401


def test_a_decision_covers_exactly_the_changes_it_names(env):
    """R-025 (E2E run 4 Q-09): the view names each held change's drifts and their digest; an
    approval citing another digest is 409 and changes nothing; a held change that bundles an
    already-rejected drift cannot be approved (409), and the rejection stays final."""
    from databridge_adapter_agents.adapt import changes_digest_of

    app, rt, recs = _app_with_held(env, aids=("ad_one", "ad_two"))
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        items = {a["adaptation_id"]: a
                 for a in c.get(f"/adapter-agents/{AGENT}/adaptations").json()["adaptations"]}
        for src in recs:
            item = items[src["adaptation_id"]]
            assert item["drift_ids"] == sorted(src["drift_ids"]) and item["drift_ids"]
            assert item["changes_digest"] == changes_digest_of(src["drift_ids"])
        wrong = changes_digest_of(recs[0]["drift_ids"] + recs[1]["drift_ids"])
        r = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_two/approve",
                   json={"changes_digest": wrong})
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "CHANGES_DIGEST_MISMATCH"
        assert {x["adaptation_id"]: x["status"] for x in rt.manager.adaptations(AGENT)} == \
            {"ad_one": "pending", "ad_two": "pending"}
        # ad_one is rejected; ad_two is then (as before the fix) re-filed carrying its drift too
        assert c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/reject",
                      json={"reason": "not wanted"}).status_code == 200
        rt.manager.adaptation.store.append(AGENT, {
            "adaptation_id": "ad_two",
            "drift_ids": sorted(recs[0]["drift_ids"] + recs[1]["drift_ids"])})
        b = c.post(f"/adapter-agents/{AGENT}/adaptations/ad_two/approve")
        assert b.status_code == 409 and b.json()["refusal"]["code"] == "REJECTED_CHANGE_BUNDLED"
        state = {x["adaptation_id"]: x["status"] for x in rt.manager.adaptations(AGENT)}
        assert state == {"ad_one": "rejected", "ad_two": "pending"}
        assert c.post(f"/adapter-agents/{AGENT}/adaptations/ad_one/approve").status_code == 409
