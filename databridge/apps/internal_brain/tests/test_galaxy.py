"""``GET /ib/galaxy`` (docs/finals/live_growth/GRAPH_PERF_DIAGNOSIS.md, Fix 1): the graph page's overview
and knowledge map from ONE live pass.

Tests 1 to 6 of the diagnosis doc §5 (test 7 is the web e2e ``tests/e2e/galaxy.spec.ts``):

1. the galaxy equals ``{overview: /ib/graph?limit=300, map: /ib/map}`` (ignoring ``as_of``) for the
   widest employee, the contractor and a guest;
2. exactly one ``check_access`` per readable document per request;
3. write-ahead: both view events before the response, any ledger failure a 503 with no source call
   after it;
4. a revocation whose webhook was lost is absent from both bodies, counts included;
5. P2: a world WITH restricted documents gives the same galaxy as the world WITHOUT them;
6. deadline: undecided documents make the overview incomplete with null counts, and the map leaves
   them out.

Real mock platforms in process, real connectors, signed webhooks (``e2e_world.E2E``) for 1 to 4 and
6; the paired synthetic worlds of ``test_graph_doc`` for 5. Personas, channels and pages are derived
from the generated world and its fact manifest.
"""
from __future__ import annotations

import collections
import json
import threading

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib import authz as authzmod
from databridge_ib import galaxy as galaxymod
from databridge_ib.app import Services, create_app
from databridge_ib.config import IbConfig

from e2e_world import ADMIN, E2E, WORLD, MemAnswers, MemLedger, load_manifest, load_world

W = load_world()
M = load_manifest()
USERS = {u["id"]: u for u in W["directory"]["users"]}
WIDEST = M["s4_actions"]["s4_remove_member"]["user"]        # the stage's main persona (Jordan)
CONTRACTOR = sorted(u for u, x in USERS.items() if x.get("kind") == "connect" and x.get("active"))[0]
GUEST = sorted(u for u, x in USERS.items() if x.get("kind") == "guest" and x.get("active"))[0]
LIMIT = 300                                                  # what the graph page asks for


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return E2E(tmp_path_factory.mktemp("galaxy"))


def _app(e: E2E, tmp_path, ledger=None):
    cfg = IbConfig(var_root=tmp_path / "var", world_path=WORLD, admin_token=ADMIN, demo_login=True)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=ledger if ledger is not None else MemLedger(), answers=MemAnswers(),
                   pipeline=e.pipeline, ingestor=e.ingestor)
    return create_app(svc), svc


def _as(app, svc, sub):
    tok = authmod.mint(svc.cfg.token_key, sub, list(svc.directory.get(sub).roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


def _strip(body: dict) -> str:
    body = json.loads(json.dumps(body))
    body.pop("as_of", None)
    return json.dumps(body, sort_keys=True)


def _galaxy(c, **params):
    r = c.get("/ib/galaxy", params={"limit": LIMIT, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"overview", "map"}
    return body


def _map_ids(m: dict) -> set[str]:
    return {i["doc_id"] for p in m["platforms"] for c in p["containers"] for i in c["items"]}


class _Counting:
    """Counts ``check_access`` per (user, doc) on every connector of ``e`` while installed."""

    def __init__(self, e: E2E):
        self.e, self.calls, self.lock, self.saved = e, collections.Counter(), threading.Lock(), {}

    def __enter__(self):
        for p, conn in self.e.connectors.items():
            orig = conn.check_access
            self.saved[p] = orig

            def wrap(user, doc_id, *a, _o=orig, **k):
                with self.lock:
                    self.calls[(user.id, doc_id)] += 1
                return _o(user, doc_id, *a, **k)
            conn.check_access = wrap
        return self

    def __exit__(self, *exc):
        for p, orig in self.saved.items():
            self.e.connectors[p].check_access = orig

    def total(self) -> int:
        with self.lock:
            return sum(self.calls.values())


def _readable(e: E2E, sub: str) -> set[str]:
    """The Gate-1 documents the overview works on (readable ids that have an index row)."""
    return {d for d in e.index.readable(e.user(sub), e.directory.epoch) if e.index.doc(d)}


# ---- 1 + 2: same bodies as the two views; one live check per readable document --------------------
@pytest.mark.parametrize("sub", [WIDEST, CONTRACTOR, GUEST])
def test_galaxy_equals_overview_and_map_with_one_check_per_document(world, tmp_path, sub):
    app, svc = _app(world, tmp_path)
    c = _as(app, svc, sub)
    with _Counting(world) as count:
        gal = _galaxy(c)
    readable = _readable(world, sub)
    # (2) exactly one live check per readable document, for this caller only
    assert set(count.calls) == {(sub, d) for d in readable}
    assert all(n == 1 for n in count.calls.values()), "a document was checked twice"
    # (1) the same bodies the two single views give
    over = c.get("/ib/graph", params={"limit": LIMIT})
    mp = c.get("/ib/map")
    assert over.status_code == mp.status_code == 200
    assert _strip(gal["overview"]) == _strip(over.json())
    assert _strip(gal["map"]) == _strip(mp.json())
    assert gal["overview"]["sub"] == gal["map"]["sub"] == sub
    assert gal["overview"]["mode"] == "overview" and gal["overview"]["complete"] is True
    if sub == WIDEST:
        assert gal["map"]["totals"]["items"] > 0


def test_galaxy_needs_an_employee_and_a_valid_limit(world, tmp_path):
    app, svc = _app(world, tmp_path)
    users = world.directory._users
    saved = users[GUEST]
    users[GUEST] = saved.model_copy(update={"roles": ()})      # roles come from the directory
    try:
        no_role = _as(app, svc, GUEST)
        assert no_role.get("/ib/galaxy").status_code == 403 == no_role.get("/ib/map").status_code
    finally:
        users[GUEST] = saved
    assert TestClient(app).get("/ib/galaxy").status_code == 401
    c = _as(app, svc, GUEST)
    for bad in (0, 301, "x"):
        assert c.get("/ib/galaxy", params={"limit": bad}).status_code == 422
    assert svc.ledger.events == []                              # nothing decided, nothing ledgered


# ---- 3: write-ahead ---------------------------------------------------------------------------------
def test_both_view_events_are_appended_before_the_response(world, tmp_path):
    app, svc = _app(world, tmp_path)
    gal = _galaxy(_as(app, svc, CONTRACTOR))
    kinds = [e.kind for e in svc.ledger.events]
    assert sorted(kinds) == ["graph.viewed", "map.viewed"]
    g = next(e for e in svc.ledger.events if e.kind == "graph.viewed")
    m = next(e for e in svc.ledger.events if e.kind == "map.viewed")
    assert g.sub == m.sub == CONTRACTOR and g.mode == "overview" and g.outcome == "ok"
    assert g.docs_checked == len(_readable(world, CONTRACTOR))
    assert g.nodes_returned == len(gal["overview"]["nodes"])
    assert g.edges_returned == len(gal["overview"]["edges"])
    assert m.docs_listed == gal["map"]["totals"]["items"]
    assert m.containers_listed == gal["map"]["totals"]["containers"]
    assert m.items_returned == len(_map_ids(gal["map"]))


@pytest.mark.parametrize("fail_on", ["graph.viewed", "map.viewed"])
def test_a_ledger_failure_is_a_503_with_no_body_and_no_source_call_after_it(world, tmp_path,
                                                                            fail_on):
    """Whichever of the two appends fails, nothing of either view leaves, and no source is asked
    anything after the failure."""
    with _Counting(world) as count:
        class Down(MemLedger):
            at_failure = None

            def append(self, ev):
                if ev.kind == fail_on:
                    Down.at_failure = count.total()
                    raise RuntimeError("ledger down")
                return super().append(ev)
        led = Down()
        app, svc = _app(world, tmp_path, ledger=led)
        r = _as(app, svc, CONTRACTOR).get("/ib/galaxy", params={"limit": LIMIT})
        assert r.status_code == 503
        assert r.json() == {"error": "temporarily unavailable"}     # the one fixed envelope
        assert Down.at_failure is not None and count.total() == Down.at_failure
    assert [e.kind for e in led.events] == (["graph.viewed"] if fail_on == "map.viewed" else [])


# ---- 4: silent revocation (webhook lost) -------------------------------------------------------------
def test_a_revocation_whose_webhook_was_lost_is_absent_from_both_bodies(world, tmp_path):
    """A Slack member removal the index never hears of: Gate 1 still says yes, the source says no.
    The galaxy then equals the galaxy of the same revocation DELIVERED (Gate 1 no longer offers the
    documents): no node, no item, no count is left of them."""
    spec = M["s4_actions"]["s4_remove_member"]
    channel, sub = spec["channel"], spec["user"]
    gone = {world.facts[f]["doc"] for f in spec["revokes_facts"]}
    app, svc = _app(world, tmp_path)
    c = _as(app, svc, sub)
    try:
        before = _galaxy(c)
        assert gone <= _map_ids(before["map"])                      # readable before the change
        world.deliver = False                                       # the webhook is lost
        world.apply("s4_remove_member")
        assert gone <= _readable(world, sub)                        # Gate 1 does not know
        silent = _galaxy(c)
        ids = _map_ids(silent["map"])
        assert not (gone & ids)
        assert not any(i.startswith(f"slack:{channel}:") for i in ids)
        nodes = {n["id"]: n for n in silent["overview"]["nodes"]}
        assert f"container:slack:{channel}" not in nodes
        assert not (gone & set(nodes))
        lost = len(_map_ids(before["map"]) - ids)
        assert silent["map"]["totals"]["items"] == before["map"]["totals"]["items"] - lost
        world.reset()                                               # the same change, delivered
        world.apply("s4_remove_member")
        assert not (gone & _readable(world, sub))
        delivered = _galaxy(c)
        assert _strip(silent["overview"]) == _strip(delivered["overview"])
        assert _strip(silent["map"]) == _strip(delivered["map"])
    finally:
        world.reset()


# ---- 5: P2 differential (restricted == does not exist) ---------------------------------------------
# A small paired world in the shape of test_graph_doc's (built here: that module imports the real
# AccessLedger). Restricted documents share a project with permitted ones, a permitted thread mentions
# a permitted issue, and a permitted issue links to the restricted thread.
_CANARY = "CNRY-galaxy-57"


def _sd(doc_id, title, text, *, container, author="", links=(), kind="thread", days=3):
    from datetime import timedelta
    from databridge_ib.models import SourceDoc
    from test_pipeline_core import NOW
    platform = doc_id.split(":", 1)[0]
    return SourceDoc(doc_id=doc_id, platform=platform, kind=kind, container=container,
                     container_name=container, title=title, text=text, version="1",
                     created_at=NOW - timedelta(days=days), updated_at=NOW - timedelta(days=days),
                     deep_link=f"https://x/{doc_id}", author=author or None, links=tuple(links))


_P2_DOCS = {
    "slack:t1": dict(title="migration status", text="Cutover on Friday. Tracking in PAY-1.",
                     container="db-migration", author="priya", links=("jira:PAY-1",)),
    "slack:t2": dict(title="leads only", text=f"Vendor contract at risk {_CANARY}. See PAY-2.",
                     container="db-migration-leads", author="priya", links=("jira:PAY-2",)),
    "jira:PAY-1": dict(title="Database migration epic", text="Epic for the cutover.",
                       container="PAY", author="jdoe", kind="issue", days=2),
    "jira:PAY-2": dict(title="Vendor contract escalation", text=f"Confidential {_CANARY}.",
                       container="PAY", author="priya", kind="issue", days=1),
    "jira:PAY-3": dict(title="Replication lag follow-up", text="Thread: slack:t2",
                       container="PAY", author="jdoe", kind="issue", links=("slack:t2",), days=4),
}
_P2_READERS = {"slack:t1": {"jdoe", "priya", "ken.ext"}, "slack:t2": {"priya"},
               "jira:PAY-1": {"jdoe", "priya", "ken.ext"}, "jira:PAY-2": {"priya"},
               "jira:PAY-3": {"jdoe", "priya"}}
_P2_SECRETS = (_CANARY, "leads only", "Vendor contract", "PAY-2", "slack:t2", "db-migration-leads")


def _p2_world(tmp_path, with_restricted: bool):
    from databridge_ib.index import Index
    from databridge_ib.ingest import Ingestor
    from databridge_ib.pipeline import Pipeline
    from test_pipeline_core import FakeConnector, _users
    tmp_path.mkdir(parents=True, exist_ok=True)
    ids = [d for d in _P2_DOCS if with_restricted or "priya" not in _P2_READERS[d]
           or len(_P2_READERS[d]) > 1]
    docs = [_sd(d, **_P2_DOCS[d]) for d in ids]
    readers = {d: _P2_READERS[d] for d in ids}
    conns = {p: FakeConnector(p, [d for d in docs if d.platform == p], readers)
             for p in ("slack", "jira")}
    idx = Index(tmp_path / "index.sqlite")
    for d in docs:
        idx.upsert(d, conns[d.platform].acl(d.doc_id))
    directory = _users()
    for uid in ("jdoe", "priya", "ken.ext"):
        directory._users[uid] = directory.get(uid).model_copy(update={"roles": ("employee",)})
    cfg = IbConfig(var_root=tmp_path / "var")
    led = MemLedger()
    svc = Services(cfg=cfg, directory=directory, index=idx, connectors=conns, ledger=led,
                   answers=MemAnswers(),
                   pipeline=Pipeline(index=idx, directory=directory, connectors=conns, ledger=led,
                                     answers=MemAnswers()),
                   ingestor=Ingestor(idx, conns, tmp_path / "c.json", directory=directory))
    return create_app(svc), svc


@pytest.mark.parametrize("sub", ["ken.ext", "jdoe"])
def test_galaxy_is_identical_with_and_without_restricted_docs(tmp_path, sub):
    a, sa = _p2_world(tmp_path / "a", True)
    b, sb = _p2_world(tmp_path / "b", False)
    assert len(sa.index.doc_ids()) > len(sb.index.doc_ids())
    ra = _as(a, sa, sub).get("/ib/galaxy", params={"limit": LIMIT})
    rb = _as(b, sb, sub).get("/ib/galaxy", params={"limit": LIMIT})
    assert ra.status_code == rb.status_code == 200
    ga, gb = ra.json(), rb.json()
    assert _map_ids(ga["map"])                                  # the view is not trivially empty
    for part in ("overview", "map"):
        assert _strip(ga[part]) == _strip(gb[part]), part
    text = json.dumps(ga)
    for s in _P2_SECRETS:
        assert s not in text


# ---- 6: the one deadline ---------------------------------------------------------------------------
def test_undecided_documents_make_the_overview_incomplete_and_leave_the_map(world, tmp_path,
                                                                           monkeypatch):
    """``authorize`` leaves the newest half of the work list undecided (as a deadline would): the
    overview says ``complete: false`` and gives no number that depends on them, and the map lists
    only what was confirmed."""
    real = authzmod.authorize
    held: set[str] = set()

    def partial(user, doc_ids, **kw):
        ids = list(doc_ids)
        late = set(ids[: max(1, len(ids) // 2)])
        held.update(late)
        ok, n = real(user, [d for d in ids if d not in late], **kw)
        if kw.get("undecided") is not None:
            kw["undecided"].update(late)
        return ok, n
    monkeypatch.setattr(authzmod, "authorize", partial)
    app, svc = _app(world, tmp_path)
    gal = _galaxy(_as(app, svc, CONTRACTOR))
    over, mp = gal["overview"], gal["map"]
    assert held and over["complete"] is False
    ids = _map_ids(mp)
    assert ids and not (held & ids)
    assert mp["totals"]["items"] == len(ids)
    nodes = {n["id"]: n for n in over["nodes"]}
    assert not (held & set(nodes))
    rows = {d: world.index.doc(d) for d in held}
    for d, row in rows.items():                     # a place or platform with an undecided document
        for nid in (f"container:{row['platform']}:{row['container']}", f"platform:{row['platform']}"):
            if nid in nodes:
                assert nodes[nid]["count"] is None and nodes[nid]["degree_hidden_safe"] is None, nid
    g = next(e for e in svc.ledger.events if e.kind == "graph.viewed")
    assert g.docs_checked == len(_readable(world, CONTRACTOR)) - len(held)


def test_an_expired_deadline_shows_nothing_and_no_exact_count(world, tmp_path, monkeypatch):
    """The real deadline: with no budget left nothing is decided, so nothing is shown or counted."""
    monkeypatch.setattr(galaxymod, "GALAXY_CHECK_TIMEOUT_S", 0.0)
    app, svc = _app(world, tmp_path)
    with _Counting(world) as count:
        gal = _galaxy(_as(app, svc, CONTRACTOR))
    assert count.total() == 0
    assert gal["overview"]["complete"] is False and gal["overview"]["nodes"] == []
    assert gal["map"]["totals"] == {"items": 0, "containers": 0, "platforms": 0}
    g = next(e for e in svc.ledger.events if e.kind == "graph.viewed")
    assert g.docs_checked == 0                                  # nothing was decided live
