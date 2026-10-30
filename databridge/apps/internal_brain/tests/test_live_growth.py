"""Live growth, the rule-4 properties of ``GET /ib/graph/changes`` (docs/finals/live_growth/PLAN.md
§7.1 L1–L9) and the answerability of an added note (A7, A8).

Real mock platforms in process, real connectors, signed webhooks (``e2e_world.E2E``), the real
app routes plus the sim and live routers. Clocks are injected so cursors are byte-comparable.
Personas and channels are derived from the generated world."""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib import authz as authzmod
from databridge_ib.app import Services, create_app, principal_dependency
from databridge_ib.config import IbConfig
from databridge_ib.live import IDLE_S, make_live_router
from databridge_ib.models import REFUSAL_TEXT
from databridge_ib.sim import make_sim_router

from e2e_world import ADMIN, E2E, WORLD, MemAnswers, _Client, load_world

W = load_world()
CHANNELS = {c["id"]: c for c in W["slack"]["channels"]}
POSTER, OUTSIDER = "jdoe", "ken.ext"
CHANNEL = sorted((c for c in CHANNELS.values() if POSTER in c.get("members", [])
                  and OUTSIDER not in c.get("members", []) and not c.get("archived")),
                 key=lambda c: (c["name"] != "db-migration", c["id"]))[0]["id"]
#: a second member of that channel (Priya on stage), derived from the world
COLLEAGUE = next(m for m in sorted(CHANNELS[CHANNEL]["members"], key=lambda m: m != "priya.sre")
                 if m not in (POSTER, OUTSIDER))
#: the agreed stage sample "shard3-resume" (docs/finals/live_growth; founder, 30 Oct)
NOTE = ("Update on DBMIG-12: replica tuning on ledger-shard-3 is done; the copy job resumes "
        "Thursday 5 Nov 2026, 21:00 SGT from batch 1,812. On-call for the restart: priya.sre.")
QUESTION = "When does the ledger-shard-3 copy job resume?"
NONSENSE = "When does the zebra quokka project launch on Mars?"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    e = E2E(tmp_path_factory.mktemp("live-growth"))
    if "slack" not in e.platforms():
        pytest.skip("no Slack connector")
    e.ingestor.ledger = e.ledger
    return e


def _app(e: E2E, tmp_path, clock: Clock, **live_kw):
    cfg = IbConfig(var_root=tmp_path / "var", world_path=WORLD, admin_token=ADMIN, demo_login=True,
                   demo_features=True)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=e.ledger, answers=MemAnswers(), pipeline=e.pipeline, ingestor=e.ingestor)
    hooks: list = []
    app = create_app(svc, sim_router_factory=lambda cur, rr: make_sim_router(
        cfg, rr, ingestor=e.ingestor, current=cur, ledger=e.ledger,
        client_factory=lambda: _Client(e.mock, base_url="http://testserver",
                                       headers={"Authorization": f"Bearer {ADMIN}"}),
        on_reset=lambda: [h() for h in hooks]))
    live = make_live_router(svc, principal_dependency(lambda: app.state.svc), clock=clock, **live_kw)
    hooks.append(live.tracker.rebaseline)
    app.include_router(live)
    app.state.live_tracker = live.tracker
    return app, svc


def _as(app, svc, sub):
    tok = authmod.mint(svc.cfg.token_key, sub, list(svc.directory.get(sub).roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


def _poll(c, cursor=None):
    r = c.get("/ib/graph/changes", params={"since": cursor} if cursor else None)
    assert r.status_code == 200, r.text
    return r


def _payload(cursor: str) -> dict:
    p64 = cursor.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(p64 + "=" * (-len(p64) % 4)))


@pytest.fixture
def env(world, tmp_path):
    clock = Clock()
    app, svc = _app(world, tmp_path, clock)
    yield world, app, svc, clock
    world.reset()


def _post(c, text=NOTE, target=CHANNEL):
    """Post as ``c``; the webhook is applied, then the client's app scans (stamps at this clock),
    as the next 3-second poll would."""
    r = c.post("/sim/add-document", json={"platform": "slack", "target": target, "text": text})
    assert r.status_code == 200, r.text
    c.app.state.live_tracker.scan()
    return r.json()["doc_id"]


# --- L1, L2, L3: a note Ken cannot open never reaches Ken, byte for byte, with no source call -----
def test_note_never_reaches_ken_byte_identical_and_without_a_source_call(world, tmp_path,
                                                                        monkeypatch):
    clock_a = Clock()
    app_a, svc_a = _app(world, tmp_path / "a", clock_a)            # world A: sees the note arrive
    try:
        priya_a, ken_a = _as(app_a, svc_a, POSTER), _as(app_a, svc_a, OUTSIDER)
        c_p, c_k = _poll(priya_a).json()["cursor"], _poll(ken_a).json()["cursor"]
        clock_a.t += 1
        doc_id = _post(priya_a)
        # world B: built AFTER the post, so for its tracker the note is part of the baseline
        clock_b = Clock(clock_a.t)
        app_b, svc_b = _app(world, tmp_path / "b", clock_b)
        ken_b = _as(app_b, svc_b, OUTSIDER)
        clock_b.t = clock_a.t - 1
        c_kb = _poll(ken_b).json()["cursor"]
        assert c_kb == c_k                                          # same caller, same clock
        clock_a.t += 1
        clock_b.t = clock_a.t

        calls = []
        real = authzmod.gates.check_all

        def counting(*a, **k):
            calls.append(a[1] if len(a) > 1 else k.get("doc_ids"))
            return real(*a, **k)
        monkeypatch.setattr(authzmod.gates, "check_all", counting)
        r_a = _poll(ken_a, c_k)
        assert calls == []                                          # L3: no source call for Ken
        r_b = _poll(ken_b, c_kb)
        assert r_a.content == r_b.content                           # L2: byte-identical
        assert set(r_a.json()) == {"cursor", "nodes", "edges"}
        assert r_a.json()["nodes"] == [] and r_a.json()["edges"] == []

        p = _poll(priya_a, c_p).json()                              # L1: Priya's side
        assert calls                                                # her note was checked live
        ids = {n["id"] for n in p["nodes"]}
        assert doc_id in ids and f"container:slack:{CHANNEL}" in ids
        assert {"source": f"container:slack:{CHANNEL}", "target": doc_id,
                "kind": "contains"} in p["edges"]
    finally:
        world.reset()


# --- L5, L6: the cursor depends only on the caller and the clock -----------------------------------
def test_cursor_payload_is_caller_and_clock_only_even_when_a_candidate_is_denied(env):
    e, app, svc, clock = env
    priya = _as(app, svc, POSTER)
    cur = _poll(priya).json()["cursor"]
    assert set(_payload(cur)) == {"v", "s", "t"} and _payload(cur)["s"] == POSTER
    clock.t += 1
    _post(priya)
    native = svc.directory.get(POSTER).platform_ids["slack"]
    r = e.admin.post(f"/admin/slack/channels/{CHANNEL}/members/remove?webhook=0",
                     json={"user": native})                         # revoked, no webhook (L6)
    assert r.status_code == 200
    clock.t += 1
    body = _poll(priya, cur).json()
    assert body["nodes"] == [] and body["edges"] == []             # Gate 1 yes, live check no
    assert _payload(body["cursor"]) == {"v": 1, "s": POSTER, "t": clock.t}


# --- L7: someone else's, forged, garbage and expired cursors start a new window ----------------------
def test_foreign_forged_garbage_or_expired_cursor_starts_a_new_window(env):
    e, app, svc, clock = env
    priya, ken = _as(app, svc, POSTER), _as(app, svc, OUTSIDER)
    cur = _poll(priya).json()["cursor"]
    clock.t += 1
    _post(priya)
    clock.t += 1
    n = len([x for x in e.ledger.events if x.kind == "graph.viewed"])
    p64, sig = cur.split(".")
    flipped = f"{p64}.{('A' if sig[0] != 'A' else 'B')}{sig[1:]}"
    for c, since in ((ken, cur), (priya, flipped), (priya, "garbage"), (priya, "a.b.c")):
        body = _poll(c, since).json()
        assert body["nodes"] == [] and body["edges"] == []
    assert len([x for x in e.ledger.events if x.kind == "graph.viewed"]) == n + 4   # each a window
    clock.t += IDLE_S + 1
    assert _poll(priya, cur).json()["nodes"] == []                  # expired: no back-fill


# --- L9: the cap is applied after the live check ------------------------------------------------------
def test_cap_applies_after_the_live_check(world, tmp_path, monkeypatch):
    clock = Clock()
    app, svc = _app(world, tmp_path, clock, max_docs=3)
    try:
        priya = _as(app, svc, POSTER)
        cur = _poll(priya).json()["cursor"]
        ids = []
        for i in range(5):
            clock.t += 1
            ids.append(_post(priya, text=f"Batch note {i}: copy job status {i}"))
        newest = ids[-1]
        real = authzmod.authorize

        def deny_newest(user, doc_ids, **kw):
            got, n = real(user, doc_ids, **kw)
            got.pop(newest, None)
            return got, n
        monkeypatch.setattr("databridge_ib.live.authz.authorize", deny_newest)
        clock.t += 1
        body = _poll(priya, cur).json()
        docs = [n["id"] for n in body["nodes"] if n["kind"] == "doc"]
        assert newest not in docs
        assert set(docs) == set(ids[1:4])                           # the newest 3 AUTHORISED ones
    finally:
        world.reset()


# --- A7: the note is answerable at once by its channel's members; Ken's refusal is the constant one --
def _answer_body(r) -> str:
    d = r.model_dump(mode="json")
    d.pop("request_id", None), d.pop("as_of", None)
    return json.dumps(d, sort_keys=True)


def test_added_note_is_answerable_by_members_and_ken_gets_the_constant_refusal(env):
    e, app, svc, clock = env
    ken_before = _answer_body(e.ask(OUTSIDER, QUESTION))
    doc_id = _post(_as(app, svc, POSTER))
    for member in (POSTER, COLLEAGUE):                              # jdoe and Priya
        resp = e.ask(member, QUESTION)
        assert doc_id in {c.doc_id for c in resp.citations}, member
        assert "21:00" in resp.answer, member
    ken = e.ask(OUTSIDER, QUESTION)
    assert ken.answer == REFUSAL_TEXT and ken.citations == ()
    assert _answer_body(ken) == _answer_body(e.ask(OUTSIDER, NONSENSE))
    assert _answer_body(ken) == ken_before                          # the note changed nothing for Ken


# --- L4: Ken's graph and map are unchanged by the note ------------------------------------------------
def _strip_as_of(body: dict) -> str:
    body = json.loads(json.dumps(body))
    body.pop("as_of", None)
    return json.dumps(body, sort_keys=True)


def test_ken_graph_and_map_unchanged_by_the_note(env):
    e, app, svc, clock = env
    ken = _as(app, svc, OUTSIDER)
    before = [ken.get(p) for p in ("/ib/graph", "/ib/map")]
    _post(_as(app, svc, POSTER))
    after = [ken.get(p) for p in ("/ib/graph", "/ib/map")]
    for b, a in zip(before, after):
        assert b.status_code == a.status_code == 200
        assert _strip_as_of(b.json()) == _strip_as_of(a.json())


# --- A6: masking before indexing, in the excerpt, in the answer and nowhere in the ledger ------------
def test_sensitive_values_in_a_note_are_masked_everywhere(env):
    from databridge_ib.sensitive import mask
    e, app, svc, clock = env
    card, nric = "4111 1111 1111 1111", "S1234567D"
    assert mask(card)[0] != card and mask(nric)[0] != nric          # both are masked shapes
    text = (f"Vendor deposit for the ledger-shard-3 restart was paid with card {card}; "
            f"approver ID {nric}.")
    jd = _as(app, svc, POSTER)
    doc_id = _post(jd, text=text)
    row = e.index.doc(doc_id)
    assert card not in row["text"] and nric not in row["text"]
    assert mask(card)[0] in row["text"] and mask(nric)[0] in row["text"]
    excerpt = jd.get(f"/ib/doc/{doc_id}")
    assert excerpt.status_code == 200 and card not in excerpt.text and nric not in excerpt.text
    resp = e.ask(POSTER, "Which card paid the vendor deposit for the ledger-shard-3 restart?")
    assert card not in resp.answer and nric not in resp.answer
    raw = json.dumps([ev.model_dump(mode="json") for ev in e.ledger.events])
    for v in (card, nric, card.replace(" ", "")):
        assert v not in raw


# --- L8: a link to a document Ken cannot open adds no node and no edge for Ken -----------------------
def test_link_to_a_document_ken_cannot_open_adds_no_node_or_edge(env):
    e, app, svc, clock = env
    shared = sorted(c["id"] for c in CHANNELS.values() if OUTSIDER in c.get("members", [])
                    and not c.get("archived") and len(c.get("members", [])) > 1)
    if not shared:
        pytest.skip("Ken shares no channel in this world")
    channel = shared[0]
    peer = next(m for m in sorted(CHANNELS[channel]["members"]) if m != OUTSIDER)
    ken_u = e.directory.get(OUTSIDER)
    hidden = sorted(d for d in e.index.doc_ids() if d.startswith("jira:")
                    and d not in e.index.readable(ken_u, e.directory.epoch))
    if not hidden:
        pytest.skip("Ken can open every Jira issue in this world")
    key = hidden[0].split(":", 1)[1]
    ken, poster = _as(app, svc, OUTSIDER), _as(app, svc, peer)
    replies = []
    for text in (f"Vendor follow-up is tracked in {key}.", "Vendor follow-up is tracked."):
        cur = _poll(ken).json()["cursor"]
        clock.t += 1
        doc_id = _post(poster, text=text, target=channel)
        clock.t += 1
        body = _poll(ken, cur).json()
        ids = {n["id"] for n in body["nodes"]}
        assert doc_id in ids and f"container:slack:{channel}" in ids
        assert hidden[0] not in ids and hidden[0] not in json.dumps(body["edges"])
        replies.append(sorted((n["kind"], n["id"] == doc_id) for n in body["nodes"]))
        clock.t += IDLE_S + 1                                       # next round: a new window
    assert replies[0] == replies[1]                                 # the same shape either way


# --- A9: a ledger outage on a non-empty poll is a 503 ------------------------------------------------
def test_ledger_down_on_a_non_empty_poll_is_503(env, monkeypatch):
    e, app, svc, clock = env
    jd = _as(app, svc, POSTER)
    cur = _poll(jd).json()["cursor"]
    clock.t += 1
    _post(jd)
    clock.t += 1

    def boom(ev):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(e.ledger, "append", boom)
    r = jd.get("/ib/graph/changes", params={"since": cur})
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    assert _as(app, svc, OUTSIDER).get("/ib/graph/changes").status_code == 503   # a new window too


# --- L-4: polls are ledgered as their own mode -------------------------------------------------------
def test_polls_are_ledgered_as_changes_not_focus(env):
    e, app, svc, clock = env
    jd = _as(app, svc, POSTER)
    cur = _poll(jd).json()["cursor"]
    clock.t += 1
    _post(jd)
    clock.t += 1
    assert _poll(jd, cur).json()["nodes"]
    views = [ev for ev in e.ledger.events if ev.kind == "graph.viewed" and ev.sub == POSTER]
    assert views[-1].mode == views[-2].mode == "changes" and views[-1].docs_returned >= 1


# --- A8: reset removes the note and does not look like a burst of updates ----------------------------
def test_reset_removes_the_note_and_rebaselines_the_feed(env):
    e, app, svc, clock = env
    priya = _as(app, svc, POSTER)
    cur = _poll(priya).json()["cursor"]
    clock.t += 1
    doc_id = _post(priya)
    clock.t += 1
    cur = _poll(priya, cur).json()["cursor"]
    clock.t += 1
    assert _as(app, svc, "admin.it").post("/sim/reset").status_code == 200
    assert e.index.doc(doc_id) is None
    assert _as(app, svc, POSTER).get(f"/ib/doc/{doc_id}").status_code == 404
    clock.t += 1
    assert _poll(priya, cur).json()["nodes"] == []                  # no "updated" burst
