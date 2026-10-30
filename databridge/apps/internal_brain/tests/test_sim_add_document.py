"""``POST /sim/add-document`` and ``GET /ib/graph/changes`` ("watch the brain grow").

The REAL mock platforms (in-process), the REAL connectors and the real app routes. A note posted
through the simulator reaches the index only through the mock's signed webhook; the author can then
open it through ``/ib/doc`` and sees it arrive in ``/ib/graph/changes``; a person who cannot read
the channel gets the same 404 as for an unknown id and an empty delta. Personas and containers are
derived from the generated world (who is a member of which channel), not written down."""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import Services, create_app
from databridge_ib.config import IbConfig
from databridge_ib.live import make_live_router
from databridge_ib.sim import _RateLimiter, make_sim_router

from e2e_world import ADMIN, E2E, WORLD, MemAnswers, _Client, load_world

W = load_world()
CHANNELS = {c["id"]: c for c in W["slack"]["channels"]}
USERS = {u["id"]: u for u in W["directory"]["users"]}
AUTHOR = "jdoe"
OUTSIDER = "ken.ext"
ADMIN_USER = next(u["id"] for u in W["directory"]["users"] if "admin_it" in u.get("roles", []))


def _member_channel(uid: str, *, not_member: str | None = None) -> str:
    """A channel ``uid`` is in (and ``not_member`` is not), preferring #db-migration."""
    ok = [c for c in CHANNELS.values() if uid in c.get("members", []) and not c.get("archived")
          and (not_member is None or not_member not in c.get("members", []))]
    ok.sort(key=lambda c: (c["name"] != "db-migration", c["id"]))
    return ok[0]["id"]


CHANNEL = _member_channel(AUTHOR, not_member=OUTSIDER)
CLOSED = next(c["id"] for c in sorted(CHANNELS.values(), key=lambda c: c["id"])
              if AUTHOR not in c.get("members", []) and c.get("type") != "public")
ADMIN_CHANNEL = _member_channel(ADMIN_USER)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    e = E2E(tmp_path_factory.mktemp("sim-add"))
    if "slack" not in e.platforms():
        pytest.skip("no Slack connector")
    e.ingestor.ledger = e.ledger
    return e


def _admin_client(e: E2E):
    return _Client(e.mock, base_url="http://testserver", headers={"Authorization": f"Bearer {ADMIN}"})


def _app(e: E2E, tmp_path, *, demo_login=True, sim_cfg_over=None, limiter=None):
    cfg = IbConfig(var_root=tmp_path / "var", world_path=WORLD, admin_token=ADMIN,
                   demo_login=demo_login, demo_features=True)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=e.ledger, answers=MemAnswers(), pipeline=e.pipeline, ingestor=e.ingestor)
    sim_cfg = dataclasses.replace(cfg, **(sim_cfg_over or {}))
    app = create_app(svc, sim_router_factory=lambda cur, rr: make_sim_router(
        sim_cfg, rr, client_factory=lambda: _admin_client(e), ingestor=e.ingestor, current=cur,
        ledger=e.ledger, rate_limiter=limiter))
    from databridge_ib.app import principal_dependency
    app.include_router(make_live_router(svc, principal_dependency(lambda: app.state.svc)))
    return app, svc


def _as(app, svc, sub):
    u = svc.directory.get(sub)
    tok = authmod.mint(svc.cfg.token_key, sub, list(u.roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


@pytest.fixture
def env(world, tmp_path):
    app, svc = _app(world, tmp_path)
    yield world, app, svc
    world.reset()                                    # every test leaves the world as generated


def _post(c, **body):
    return c.post("/sim/add-document", json=body)


def _note(c, text="Copy job resumes Thursday 21:00 SGT; on-call for the restart: priya.sre", **kw):
    return _post(c, platform="slack", target=kw.pop("target", CHANNEL), text=text, **kw)


# ---------------------------------------------------------------------------------------------
# the webhook flow
# ---------------------------------------------------------------------------------------------
def test_added_note_reaches_the_index_by_webhook_and_the_author_can_open_it(env):
    e, app, svc = env
    hooks_before = len(e.hooks)
    r = _note(_as(app, svc, AUTHOR), text="Replica tuning done: the copy job resumes Thursday 21:00 SGT")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"ok", "doc_id", "platform", "container", "at"}
    assert body["ok"] is True and body["platform"] == "slack" and body["container"] == CHANNEL
    assert body["doc_id"].startswith(f"slack:{CHANNEL}:")
    assert len(e.hooks) == hooks_before + 1          # exactly the signed webhook, no direct write
    assert e.hooks[-1][0] == "slack"

    doc = _as(app, svc, AUTHOR).get(f"/ib/doc/{body['doc_id']}")
    assert doc.status_code == 200, doc.text
    d = doc.json()
    assert "the copy job resumes Thursday" in d["excerpt"]
    assert d["author"]["id"] == AUTHOR               # the author is the signed-in person
    assert d["container"] == CHANNEL


def test_restricted_equals_non_existent_for_the_new_note(env):
    e, app, svc = env
    doc_id = _note(_as(app, svc, AUTHOR)).json()["doc_id"]
    ken = _as(app, svc, OUTSIDER)
    hidden = ken.get(f"/ib/doc/{doc_id}")
    unknown = ken.get(f"/ib/doc/slack:{CHANNEL}:1.000001")
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.content == unknown.content


def test_html_is_stripped_and_card_numbers_masked_at_ingest(env):
    e, app, svc = env
    r = _note(_as(app, svc, AUTHOR), text="<b>Card</b> 4111 1111 1111 1111 <script>x</script>ok")
    assert r.status_code == 200, r.text
    row = e.index.doc(r.json()["doc_id"])
    assert "<b>" not in row["text"] and "<script>" not in row["text"]
    assert "4111 1111 1111 1111" not in row["text"]


def test_confluence_page_by_admin_and_never_by_an_employee(env):
    e, app, svc = env
    readable = e.index.readable(e.directory.get(ADMIN_USER), e.directory.epoch)
    spaces = sorted({e.index.doc(d)["container"] for d in readable if d.startswith("confluence:")})
    if not spaces:
        pytest.skip("the IT admin can read no Confluence space in this world")
    page = dict(platform="confluence", target=spaces[0], title="Wellness day", text="Offices closed.")
    r = _post(_as(app, svc, ADMIN_USER), **page)
    assert r.status_code == 200, r.text
    assert r.json()["doc_id"].startswith("confluence:cf-")
    assert _as(app, svc, ADMIN_USER).get(f"/ib/doc/{r.json()['doc_id']}").status_code == 200
    # employees: Slack only (the source models no Confluence write permission to check)
    jd = e.index.readable(e.directory.get(AUTHOR), e.directory.epoch)
    jspace = sorted({e.index.doc(d)["container"] for d in jd if d.startswith("confluence:")})[0]
    page["target"] = jspace
    assert _post(_as(app, svc, AUTHOR), **page).status_code == 404


# ---------------------------------------------------------------------------------------------
# who and where
# ---------------------------------------------------------------------------------------------
def test_unreadable_channel_is_the_same_404_as_an_unknown_one(env):
    e, app, svc = env
    c = _as(app, svc, AUTHOR)
    a, b = _note(c, target=CLOSED), _note(c, target="C0NOPE")
    assert a.status_code == b.status_code == 404
    assert a.content == b.content and a.json() == {"detail": "not found"}
    assert a.headers.get("content-type") == b.headers.get("content-type")


def test_live_membership_is_checked_at_post_time(env):
    """Revoked at the source WITHOUT a webhook: Gate 1 still says readable, the live check refuses."""
    e, app, svc = env
    native = USERS[AUTHOR]["platform_ids"]["slack"]
    r = _admin_client(e).post(f"/admin/slack/channels/{CHANNEL}/members/remove?webhook=0",
                              json={"user": native})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert _note(_as(app, svc, AUTHOR)).status_code == 404
    failed = [ev for ev in e.ledger.events if ev.kind == "request.failed"
              and ev.stage == "sim.add-document"]
    assert failed and failed[-1].sub == AUTHOR and failed[-1].error_code == "not_a_member_now"


def test_demo_login_off_refuses_everyone_with_the_same_404(world, tmp_path):
    """IB_DEMO_LOGIN only (D-24): with demo sign-in off, the IT admin is refused too (A1: the
    refusals are byte-identical and each is ledgered)."""
    app, svc = _app(world, tmp_path, demo_login=False)
    try:
        r = _note(_as(app, svc, AUTHOR))
        assert r.status_code == 404 and r.json() == {"detail": "not found"}
        a = _note(_as(app, svc, ADMIN_USER), target=ADMIN_CHANNEL)
        assert a.status_code == 404 and a.content == r.content
        failed = [ev for ev in world.ledger.events if ev.kind == "request.failed"
                  and ev.stage == "sim.add-document"]
        assert [ev.error_code for ev in failed[-2:]] == ["demo_login_off", "demo_login_off"]
    finally:
        world.reset()


def test_a1_ken_refusals_are_byte_identical(env):
    """Ken posting to a channel he is not in, and to a channel that does not exist."""
    e, app, svc = env
    ken = _as(app, svc, OUTSIDER)
    a, b = _note(ken, target=CHANNEL), _note(ken, target="C0NOPE")
    assert a.status_code == b.status_code == 404 and a.content == b.content
    assert a.json() == {"detail": "not found"}


def test_deployed_refuses_everyone(world, tmp_path):
    app, svc = _app(world, tmp_path, sim_cfg_over={"deployed": True})
    assert _note(_as(app, svc, AUTHOR)).status_code == 404
    assert _note(_as(app, svc, ADMIN_USER), target=ADMIN_CHANNEL).status_code == 404


def test_unauthenticated_is_401(env):
    e, app, svc = env
    assert TestClient(app).post("/sim/add-document", json={"platform": "slack", "target": CHANNEL,
                                                           "text": "x"}).status_code == 401


def test_rate_limit_five_per_minute(world, tmp_path):
    t = [0.0]
    app, svc = _app(world, tmp_path, limiter=_RateLimiter(clock=lambda: t[0]))
    try:
        c = _as(app, svc, AUTHOR)
        for i in range(5):
            assert _note(c, text=f"note {i}").status_code == 200
        r = _note(c, text="one too many")
        assert r.status_code == 429 and r.json() == {"detail": "too many requests"}
        assert world.ledger.events[-1].error_code == "rate_limited"
        t[0] += 61
        assert _note(c, text="a minute later").status_code == 200
    finally:
        world.reset()


@pytest.mark.parametrize("body", [
    {"platform": "slack", "target": CHANNEL, "text": "x", "author": "priya.sre"},   # no author field
    {"platform": "slack", "target": CHANNEL, "text": "x", "user": "priya.sre"},
    {"platform": "slack", "target": CHANNEL, "text": ""},
    {"platform": "slack", "target": CHANNEL, "text": "   "},
    {"platform": "slack", "target": CHANNEL, "text": "x" * 2001},
    {"platform": "slack", "target": "../admin", "text": "x"},
    {"platform": "jira", "target": "PAY", "text": "x"},
    {"platform": "confluence", "target": "runbooks", "text": "no title"},
    {"platform": "confluence", "target": "runbooks", "title": "t" * 121, "text": "x"},
    {"target": CHANNEL, "text": "x"},
])
def test_body_is_validated(env, body):
    e, app, svc = env
    assert _as(app, svc, AUTHOR).post("/sim/add-document", json=body).status_code == 422


# ---------------------------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------------------------
def test_sim_reset_removes_an_added_note(env):
    e, app, svc = env
    doc_id = _note(_as(app, svc, AUTHOR)).json()["doc_id"]
    assert e.index.doc(doc_id) is not None
    r = _as(app, svc, ADMIN_USER).post("/sim/reset")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert e.index.doc(doc_id) is None
    assert _as(app, svc, AUTHOR).get(f"/ib/doc/{doc_id}").status_code == 404


# ---------------------------------------------------------------------------------------------
# GET /ib/graph/changes
# ---------------------------------------------------------------------------------------------
def _graph_views(e):
    return [ev for ev in e.ledger.events if ev.kind == "graph.viewed"]


def test_changes_deliver_the_new_note_only_to_people_who_can_open_it(env):
    e, app, svc = env
    jd, ken = _as(app, svc, AUTHOR), _as(app, svc, OUTSIDER)
    n0 = len(_graph_views(e))
    first_j = jd.get("/ib/graph/changes").json()
    first_k = ken.get("/ib/graph/changes").json()
    assert first_j["nodes"] == first_k["nodes"] == [] and first_j["edges"] == []
    assert len(_graph_views(e)) == n0 + 2            # the first poll of a window is ledgered
    assert _graph_views(e)[-1].docs_returned == 0

    idle = jd.get("/ib/graph/changes", params={"since": first_j["cursor"]}).json()
    assert idle["nodes"] == [] and set(idle) == {"cursor", "nodes", "edges"}
    # round 5 Codex #8: every poll in a window writes its zero-count write-ahead row before Gate 1
    assert len(_graph_views(e)) == n0 + 3
    assert (_graph_views(e)[-1].outcome, _graph_views(e)[-1].docs_returned) == ("checking", 0)

    doc_id = _note(jd).json()["doc_id"]
    d = jd.get("/ib/graph/changes", params={"since": idle["cursor"]}).json()
    assert set(d) == {"cursor", "nodes", "edges"}
    ids = {n["id"] for n in d["nodes"]}
    assert doc_id in ids and f"container:slack:{CHANNEL}" in ids and f"person:{AUTHOR}" in ids
    assert {"source": f"container:slack:{CHANNEL}", "target": doc_id, "kind": "contains"} in d["edges"]
    node = next(n for n in d["nodes"] if n["id"] == doc_id)
    assert node["kind"] == "doc" and node["container"] == f"container:slack:{CHANNEL}"
    assert all(n.get("count") is None for n in d["nodes"] if n["kind"] != "doc")
    gv = _graph_views(e)[-1]
    assert gv.sub == AUTHOR and gv.docs_returned >= 1 and gv.docs_checked >= 1

    again = jd.get("/ib/graph/changes", params={"since": d["cursor"]}).json()
    assert again["nodes"] == []                      # delivered once

    k = ken.get("/ib/graph/changes", params={"since": first_k["cursor"]}).json()
    assert k["nodes"] == [] and k["edges"] == [] and set(k) == {"cursor", "nodes", "edges"}
    assert doc_id not in json.dumps(k)


def test_changes_cursor_is_per_person_and_tamper_proof(env):
    e, app, svc = env
    jd, ken = _as(app, svc, AUTHOR), _as(app, svc, OUTSIDER)
    cur = jd.get("/ib/graph/changes").json()["cursor"]
    _note(jd)
    n = len(_graph_views(e))
    # Ken replaying Jordan's cursor gets a fresh, empty window (never Jordan's delta)
    r = ken.get("/ib/graph/changes", params={"since": cur}).json()
    assert r["nodes"] == [] and len(_graph_views(e)) == n + 1
    forged = cur[:-2] + ("AA" if not cur.endswith("AA") else "BB")
    assert jd.get("/ib/graph/changes", params={"since": forged}).json()["nodes"] == []
    assert jd.get("/ib/graph/changes", params={"since": "x" * 401}).status_code == 422


def test_changes_connect_a_note_to_a_jira_issue_it_mentions(env):
    e, app, svc = env
    readable = e.index.readable(e.directory.get(AUTHOR), e.directory.epoch)
    issues = sorted(d for d in readable if d.startswith("jira:"))
    if not issues:
        pytest.skip("the author can read no Jira issue")
    key = issues[0].split(":", 1)[1]
    jd = _as(app, svc, AUTHOR)
    cur = jd.get("/ib/graph/changes").json()["cursor"]
    doc_id = _note(jd, text=f"Restart plan is tracked in {key}.").json()["doc_id"]
    d = jd.get("/ib/graph/changes", params={"since": cur}).json()
    assert {"source": doc_id, "target": issues[0], "kind": "mentions"} in d["edges"]
    assert issues[0] in {n["id"] for n in d["nodes"]}


def test_changes_ledger_failure_is_503(env, monkeypatch):
    e, app, svc = env

    def boom(ev):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(e.ledger, "append", boom)
    r = _as(app, svc, AUTHOR).get("/ib/graph/changes")
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}


# ---------------------------------------------------------------------------------------------
# POST /sim/check-absent
# ---------------------------------------------------------------------------------------------
PHRASE = "zebra-quokka resume window"


def test_check_absent_counts_before_and_after_an_added_note(env):
    e, app, svc = env
    adm = _as(app, svc, ADMIN_USER)
    r = adm.post("/sim/check-absent", json={"phrase": PHRASE.upper()})
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"phrase", "documents_containing", "checked_at", "index_size"}
    assert b["documents_containing"] == 0 and b["index_size"] == len(e.index.doc_ids())
    q = [ev for ev in e.ledger.events if ev.kind == "audit.query"][-1]
    assert q.sub == ADMIN_USER and q.filter["op"] == "sim.check-absent" and q.result_count == 0
    assert PHRASE.upper() not in json.dumps(q.filter)          # the digest, not the phrase

    assert _note(_as(app, svc, AUTHOR), text=f"Note: the {PHRASE} is Thursday").status_code == 200
    after = adm.post("/sim/check-absent", json={"phrase": PHRASE}).json()
    assert after["documents_containing"] >= 1
    assert set(after) == {"phrase", "documents_containing", "checked_at", "index_size"}


def test_check_absent_is_operators_only_with_the_constant_refusal(env):
    e, app, svc = env
    r = _as(app, svc, AUTHOR).post("/sim/check-absent", json={"phrase": PHRASE})
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    assert [ev for ev in e.ledger.events if ev.kind == "request.failed"][-1].stage ==         "sim.check-absent"
    for bad in ({"phrase": "ab"}, {"phrase": "x" * 81}, {"phrase": "abc", "x": 1}, {}):
        assert _as(app, svc, ADMIN_USER).post("/sim/check-absent", json=bad).status_code == 422


def test_check_absent_refused_when_demo_login_off_or_deployed(world, tmp_path):
    app, svc = _app(world, tmp_path, demo_login=False)
    r = _as(app, svc, ADMIN_USER).post("/sim/check-absent", json={"phrase": PHRASE})
    assert r.status_code == 404 and r.json() == {"detail": "not found"}
    # deployed: the demo-proof routes are not mounted at all (the unknown-route 404)
    app, svc = _app(world, tmp_path, sim_cfg_over={"deployed": True})
    adm = _as(app, svc, ADMIN_USER)
    r = adm.post("/sim/check-absent", json={"phrase": PHRASE})
    u = adm.post("/sim/no-such-route", json={"phrase": PHRASE})
    assert r.status_code == u.status_code == 404 and r.content == u.content



def test_a_successful_post_is_ledgered_write_ahead(env):
    """Round 3 L5: ``requested`` (person, container, kind, length and SHA-256 of the text) before
    the source call, ``written`` with the doc id after; never the text."""
    import hashlib
    e, app, svc = env
    text = "Write-ahead note: replica lag cleared at 21:05 SGT"
    n = len([ev for ev in e.ledger.events if ev.kind == "ingest.sync"])
    r = _note(_as(app, svc, AUTHOR), text=text)
    assert r.status_code == 200, r.text
    rows = [ev for ev in e.ledger.events if ev.kind == "ingest.sync"
            and ev.detail.get("reason") == "sim.add-document"]
    req, done = rows[-2:]
    assert len([ev for ev in e.ledger.events if ev.kind == "ingest.sync"]) >= n + 2
    assert (req.detail["phase"], done.detail["phase"]) == ("requested", "written")
    assert req.detail["by"] == AUTHOR and req.detail["container"] == CHANNEL
    assert req.detail["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert done.detail["doc_id"] == r.json()["doc_id"]
    assert "replica lag" not in repr(req.detail) + repr(done.detail)


def test_add_document_ledger_down_is_503_and_nothing_is_posted(env, monkeypatch):
    e, app, svc = env
    real = e.ledger.append

    def boom(ev):
        if ev.kind == "ingest.sync":
            raise RuntimeError("ledger down")
        return real(ev)
    monkeypatch.setattr(e.ledger, "append", boom)
    hooks = len(e.hooks)
    r = _note(_as(app, svc, AUTHOR), text="this must not be posted")
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    assert len(e.hooks) == hooks


def test_write_ahead_without_a_ledger_fails_closed():
    """Round 4 Codex B8: no ledger configured is the same as a ledger that is down. The source write
    must not go ahead unaudited, so ``write_ahead`` raises instead of returning quietly."""
    from databridge_ib.sim import LedgerUnavailable, write_ahead
    with pytest.raises(LedgerUnavailable):
        write_ahead(None, AUTHOR, "sim.add-document", "requested", {"platform": "slack"})


def test_add_document_without_a_ledger_is_503_and_nothing_is_posted(world, tmp_path, monkeypatch):
    """Round 4 Codex B8, end to end: a sim router built with no ledger (and an ingestor without
    one) answers 503 for a post that would otherwise succeed, and nothing reaches the source."""
    e = world
    monkeypatch.setattr(e.ingestor, "ledger", None)
    cfg = IbConfig(var_root=tmp_path / "var", world_path=WORLD, admin_token=ADMIN,
                   demo_login=True, demo_features=True)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=e.ledger, answers=MemAnswers(), pipeline=e.pipeline, ingestor=e.ingestor)
    app = create_app(svc, sim_router_factory=lambda cur, rr: make_sim_router(
        cfg, rr, client_factory=lambda: _admin_client(e), ingestor=e.ingestor, current=cur,
        ledger=None))
    hooks = len(e.hooks)
    try:
        r = _note(_as(app, svc, AUTHOR), text="this must not be posted without an audit row")
        assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
        assert len(e.hooks) == hooks
    finally:
        world.reset()
