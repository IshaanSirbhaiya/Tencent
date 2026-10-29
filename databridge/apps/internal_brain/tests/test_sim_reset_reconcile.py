"""Simulator Reset must undo a revocation at once (QA_LIVE §4.1).

Revoke reaches the IB by webhook; the mock's ``/admin/reset`` reloads its world silently, and the
incremental cursor still holds the post-revoke membership, so a poll sees no change. ``/sim/reset``
therefore runs a FULL reconciliation right after the platform reset. Expectations are derived from
the world's fact manifest (S1-q1: jdoe's headline question, and the Slack fact it must cite)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_ib.sim import make_sim_router

from e2e_world import ADMIN, E2E, WORLD, _Client, load_world

W = load_world()
HEADLINE = next(q for q in W["questions"] if q["id"] == "S1-q1")
FACTS = {f["id"]: f for f in W["facts"]}
SLACK_DOCS = [FACTS[f]["doc"] for f in HEADLINE["must_cite_facts"]
              if FACTS[f]["doc"].startswith("slack:")]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    e = E2E(tmp_path_factory.mktemp("sim-reset"))
    if "slack" not in e.platforms() or not SLACK_DOCS:
        pytest.skip("the headline question cites no Slack fact in this world")
    e.ingestor.ledger = e.ledger                     # ledger the syncs, as bootstrap does
    cfg = SimpleNamespace(world_path=WORLD, platform_base={}, admin_token=ADMIN)
    app = FastAPI()
    app.include_router(make_sim_router(
        cfg, lambda role: (lambda: SimpleNamespace(id="admin.it", roles=(role,))),
        client_factory=lambda: _Client(e.mock, base_url="http://mock",
                                       headers={"Authorization": f"Bearer {ADMIN}"}),
        ingestor=e.ingestor))
    return e, TestClient(app)


@pytest.fixture
def env(world):
    yield world
    world[0].reset()                                 # every test leaves the world as generated


def _slack_released(e: E2E) -> set[str]:
    resp = e.ask(HEADLINE["persona"], HEADLINE["question"])
    released = set(e.last("retrieval.decided").released)
    cited = {c.doc_id for c in resp.citations}
    return {d for d in SLACK_DOCS if d in released} | {d for d in SLACK_DOCS if d in cited}


def test_reset_restores_a_revoked_member_without_waiting_for_a_poll(env):
    e, sim = env
    assert _slack_released(e) == set(SLACK_DOCS)                 # before: Slack is cited

    assert sim.post("/sim/revoke-channel").status_code == 200    # webhook -> index ACL updated
    assert _slack_released(e) == set()

    r = sim.post("/sim/reset")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and set(body["reconciled"]) == e.platforms()
    assert _slack_released(e) == set(SLACK_DOCS)                 # immediately, no poll

    syncs = [ev for ev in e.ledger.events if ev.kind == "ingest.sync"
             and ev.detail.get("reason") == "sim.reset"]
    assert {ev.platform for ev in syncs} == e.platforms()
    assert all(ev.detail.get("full") is True for ev in syncs)


def test_plain_incremental_sync_after_a_silent_reset_would_not_restore(env):
    """The bug this guards: without the full reconciliation, the cursor hides the reset."""
    e, sim = env
    assert sim.post("/sim/revoke-channel").status_code == 200
    e.admin_post("/admin/reset", {})                              # what the old /sim/reset did
    e.ingestor.sync_all()                                         # an ordinary poll tick
    assert _slack_released(e) == set()
    e.ingestor.reconcile("test")
    assert _slack_released(e) == set(SLACK_DOCS)


def test_reset_reports_a_platform_it_could_not_reconcile(env, monkeypatch):
    e, sim = env
    broken = sorted(e.platforms())[0]

    def boom(*a, **k):
        raise RuntimeError("source down")
    monkeypatch.setattr(e.connectors[broken], "list_changes", boom)
    body = sim.post("/sim/reset").json()
    assert body["ok"] is False and body["reconcile_failed"] == [broken]
    assert broken not in body["reconciled"]
