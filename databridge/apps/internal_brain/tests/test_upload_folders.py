"""``GET /sim/upload-folders``: the upload sheet's folder picker (D-34, SECURITY.md §15.12).

The REAL mock platforms (in-process), the REAL Drive connector and the real app routes. A person
gets ``[{id, path}]`` for ONLY the folders they may upload into now, checked live with the upload's
own write check; a person who may write nowhere gets ``[]``, byte-identical whoever they are.
Personas and folders are DERIVED from the generated world (who holds which Drive role), not
written down."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import Services, create_app
from databridge_ib.config import IbConfig
from databridge_ib.sim import _RateLimiter
from databridge_ib.uploads import FOLDERS_STAGE, make_uploads_router, reset_uploads

from e2e_world import ADMIN, E2E, WORLD, MemAnswers, _Client, load_world

W = load_world()
USERS = {u["id"]: u for u in W["directory"]["users"]}
FOLDERS = {f["id"]: f for f in W["drive"]["folders"]}
DRIVES = {d["id"]: d for d in W["drive"]["drives"]}
WRITE = {"writer", "fileOrganizer", "organizer", "owner"}
ADMIN_USER = next(u for u, x in USERS.items() if "admin_it" in x.get("roles", []))
REFUSAL = {"detail": "not found"}
KEN = "ken.ext"


# ---------------------------------------------------------------------------------------------
# who holds what, from the world (folder grants, then the shared drive's members unless the
# folder is limited-access)
# ---------------------------------------------------------------------------------------------
def _groups(uid: str) -> set[str]:
    from databridge_ib.directory import Directory
    return set(Directory.from_world(W).get(uid).groups)


def _grants(fid: str) -> list[dict]:
    f = FOLDERS[fid]
    out = list(f.get("permissions", []))
    if not f.get("inherited_permissions_disabled"):
        m = DRIVES[f["drive"]].get("members") or {}
        out += [{"type": "user", "principal": u, "role": m.get("role", "reader")}
                for u in m.get("users", [])]
        out += [{"type": "group", "principal": g, "role": m.get("role", "reader")}
                for g in m.get("groups", [])]
        if f.get("parent"):                       # inherited down the folder chain
            out += [g for g in _grants(f["parent"]) if g not in out]
    return out


def _holds(uid: str, fid: str, roles: set[str] | None) -> bool:
    gs, dom = _groups(uid), USERS[uid]["email"].rsplit("@", 1)[-1]
    for g in _grants(fid):
        if roles is not None and g.get("role") not in roles:
            continue
        if (g["type"] == "user" and g["principal"] == uid) or \
                (g["type"] == "group" and g["principal"] in gs) or \
                (g["type"] == "domain" and g["principal"] == dom):
            return True
    return False


def _eligible(uid: str) -> bool:
    u = USERS[uid]
    return u.get("active", True) and "admin_it" not in u.get("roles", []) and \
        bool(u.get("platform_ids", {}).get("drive"))


def _expected_ids(uid: str) -> set[str]:
    if "admin_it" in USERS[uid].get("roles", []):
        return {f for f, x in FOLDERS.items() if not x.get("trashed")}
    return {f for f in FOLDERS if _holds(uid, f, WRITE)}


def _expected_path(uid: str, fid: str, drive_name=None) -> str:
    """The folder chain the person can open, led by the shared drive's name when Drive gives
    one (``drive_name``: the connector's resolver; the mock has no drives.get, so the id)."""
    admin = "admin_it" in USERS[uid].get("roles", [])
    names, p = [FOLDERS[fid]["name"]], FOLDERS[fid].get("parent")
    while p and (admin or _holds(uid, p, None)):
        names.insert(0, FOLDERS[p]["name"])
        p = FOLDERS[p].get("parent")
    d = FOLDERS[fid]["drive"]
    dn = drive_name(d) if drive_name else d
    return " / ".join([dn, *names] if dn != d else names)


ELIGIBLE = sorted(u for u in USERS if _eligible(u))
WRITERS = [u for u in ELIGIBLE if _expected_ids(u)]
NOBODY = [u for u in ELIGIBLE if not _expected_ids(u) and u != KEN]


# ---------------------------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    e = E2E(tmp_path_factory.mktemp("upload-folders"))
    if "drive" not in e.platforms():
        pytest.skip("no Drive connector")
    return e


class _Cfg:
    def __init__(self, base, **over):
        self._base, self._over = base, over

    def __getattr__(self, name):
        return self._over[name] if name in self._over else getattr(self._base, name)


def _app(e: E2E, tmp_path, *, demo_login=True, demo_features=True, cfg_over=None, limiter=None):
    cfg = IbConfig(var_root=tmp_path / "var", world_path=WORLD, admin_token=ADMIN,
                   demo_login=demo_login)
    svc = Services(cfg=cfg, directory=e.directory, index=e.index, connectors=e.connectors,
                   ledger=e.ledger, answers=MemAnswers(), pipeline=e.pipeline, ingestor=e.ingestor)
    app = create_app(svc)
    from databridge_ib.app import principal_dependency
    router = make_uploads_router(
        svc, principal_dependency(lambda: app.state.svc),
        cfg=_Cfg(cfg, demo_features=demo_features, **(cfg_over or {})),
        client_factory=lambda: _Client(e.mock, base_url="http://testserver",
                                       headers={"Authorization": f"Bearer {ADMIN}"}),
        folders_rate_limiter=limiter or _RateLimiter(n=1000), warm=False)
    app.include_router(router)
    return app, svc


def _as(app, svc, sub):
    u = svc.directory.get(sub)
    tok = authmod.mint(svc.cfg.token_key, sub, list(u.roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


@pytest.fixture
def env(world, tmp_path):
    app, svc = _app(world, tmp_path)
    yield world, app, svc


def _get(app, svc, who, params=None):
    return _as(app, svc, who).get("/sim/upload-folders", params=params)


def _rows(e, mode="upload_folders"):
    return [ev for ev in e.ledger.events if ev.kind == "graph.viewed" and ev.mode == mode]


def _refusals(e):
    return [ev for ev in e.ledger.events if ev.kind == "request.failed"
            and ev.stage == FOLDERS_STAGE]


# ---------------------------------------------------------------------------------------------
# only writable folders, by name
# ---------------------------------------------------------------------------------------------
def test_world_has_writers_and_people_who_write_nowhere():
    assert WRITERS and NOBODY and KEN in USERS


def test_each_person_sees_exactly_the_folders_they_can_write_to(env):
    e, app, svc = env
    for uid in [*ELIGIBLE, ADMIN_USER]:
        r = _get(app, svc, uid)
        assert r.status_code == 200, (uid, r.text)
        got = r.json()
        assert all(set(f) == {"id", "path"} for f in got)
        assert {f["id"] for f in got} == _expected_ids(uid), uid
        for f in got:
            assert f["path"] == _expected_path(uid, f["id"], e.connectors["drive"]._drive_name),                 (uid, f)
        keys = [(f["path"].casefold(), f["id"]) for f in got]
        assert keys == sorted(keys), uid


def test_read_access_is_not_enough(env):
    e, app, svc = env
    readers = [(u, f) for u in ELIGIBLE for f in FOLDERS
               if _holds(u, f, None) and not _holds(u, f, WRITE)]
    assert readers
    for uid, fid in readers[:10]:
        assert fid not in {f["id"] for f in _get(app, svc, uid).json()}


def test_admin_it_sees_every_folder_with_full_paths(env):
    e, app, svc = env
    got = _get(app, svc, ADMIN_USER).json()
    assert {f["id"] for f in got} == set(FOLDERS)
    nested = [f for f in FOLDERS if FOLDERS[f].get("parent")]
    for f in got:
        if f["id"] in nested:
            assert FOLDERS[FOLDERS[f["id"]]["parent"]]["name"] in f["path"]


def test_a_parent_folder_name_is_shown_only_to_people_who_can_open_it(env):
    """A writer on a nested folder who cannot open its parent sees the drive and the folder's
    own name, never the parent's."""
    e, app, svc = env
    nested = next((f for f in sorted(FOLDERS) if FOLDERS[f].get("parent")), None)
    if nested is None:
        pytest.skip("no nested folder in this world")
    parent = FOLDERS[nested]["parent"]
    outsider = next((u for u in ELIGIBLE if not _holds(u, parent, None)
                     and not _holds(u, nested, None)), None)
    if outsider is None:
        pytest.skip("no one outside the parent folder")
    w = e.mock.state.world
    try:
        with w.lock:
            w.drive_item(nested).setdefault("permissions", []).append(
                {"id": "pm-test-folders", "principal": outsider, "role": "writer", "type": "user"})
        got = {f["id"]: f["path"] for f in _get(app, svc, outsider).json()}
        assert nested in got
        assert got[nested] == _expected_path(ADMIN_USER, nested, e.connectors["drive"]._drive_name)             .replace(f"{FOLDERS[parent]['name']} / ", "")
        assert FOLDERS[parent]["name"] not in got[nested].split(" / ")
    finally:
        e.reset()


# ---------------------------------------------------------------------------------------------
# Ken (restricted) and people who may write nowhere: [] and nothing else
# ---------------------------------------------------------------------------------------------
def test_ken_sees_nothing_and_his_reply_is_byte_identical_to_someone_with_no_folders(env):
    e, app, svc = env
    ken = _get(app, svc, KEN)
    other = _get(app, svc, NOBODY[0])
    assert ken.status_code == other.status_code == 200
    assert ken.json() == []
    assert ken.content == other.content
    assert ken.headers.get("content-type") == other.headers.get("content-type")
    assert ken.headers.get("content-length") == other.headers.get("content-length")
    # the folders he can only comment on or read, and every restricted one, are absent
    body = ken.text
    for fid, f in FOLDERS.items():
        assert fid not in body and f["name"] not in body
    for d in DRIVES.values():
        assert d["name"] not in body and d["id"] not in body


def test_identity_comes_only_from_the_token(env):
    e, app, svc = env
    plain = _get(app, svc, KEN)
    for params in ({"user": ADMIN_USER}, {"sub": ADMIN_USER}, {"as": WRITERS[0]}):
        r = _get(app, svc, KEN, params)
        assert r.status_code == 200 and r.content == plain.content


def test_unauthenticated_is_401(env):
    e, app, svc = env
    assert TestClient(app).get("/sim/upload-folders").status_code == 401


# ---------------------------------------------------------------------------------------------
# live, fail closed
# ---------------------------------------------------------------------------------------------
def test_write_permission_is_read_live_from_drive(env):
    """The writer's grant removed at the source WITHOUT a webhook: the folder leaves the list."""
    e, app, svc = env
    pick = next(((u, f) for u in WRITERS for f in sorted(_expected_ids(u))
                 if [p for p in FOLDERS[f].get("permissions", []) if p.get("role") in WRITE]
                 and not FOLDERS[f].get("parent")), None)
    assert pick
    uid, fid = pick
    try:
        assert fid in {f["id"] for f in _get(app, svc, uid).json()}
        for p in FOLDERS[fid].get("permissions", []):
            if p.get("role") in WRITE:
                r = e.admin.post(f"/admin/drive/files/{fid}/permissions/remove?webhook=0",
                                 json={"permission_id": p["id"]})
                assert r.status_code == 200
        assert fid not in {f["id"] for f in _get(app, svc, uid).json()}
    finally:
        e.reset()


def test_a_source_error_on_a_folder_leaves_it_out(env, monkeypatch):
    e, app, svc = env
    from databridge_ib.connectors._http import SourceFailure
    uid = WRITERS[0]

    def boom(*_a, **_k):
        raise SourceFailure("source_429")
    monkeypatch.setattr(e.connectors["drive"], "_permissions", boom)
    r = _get(app, svc, uid)
    assert r.status_code == 200 and r.json() == []


def test_a_source_error_on_the_listing_is_an_empty_list_and_ledgered(env, monkeypatch):
    e, app, svc = env

    def boom(*_a, **_k):
        raise RuntimeError("drive down")
    monkeypatch.setattr(e.connectors["drive"], "_list", boom)
    r = _get(app, svc, ADMIN_USER)
    assert r.status_code == 200 and r.content == _get(app, svc, KEN).content
    assert any(ev.error_code == "source_error" and ev.sub == ADMIN_USER for ev in _refusals(e))


# ---------------------------------------------------------------------------------------------
# ledger, rate limit, switches
# ---------------------------------------------------------------------------------------------
def test_every_call_is_ledgered_before_the_response_counts_only(env):
    e, app, svc = env
    for uid in (WRITERS[0], KEN):
        n = len(_rows(e))
        got = _get(app, svc, uid).json()
        rows = _rows(e)[n:]
        assert len(rows) == 2                       # round 5 #7: accepted (write-ahead), then ok
        pre, row = rows
        assert (pre.sub, pre.outcome, pre.nodes_returned, pre.docs_checked) ==             (uid, "checking", 0, 0)
        assert row.sub == uid and row.nodes_returned == len(got) and row.outcome == "ok"
        assert row.docs_checked >= len(got)
        dumped = row.model_dump_json()
        for f in got:
            assert f["id"] not in dumped and f["path"] not in dumped


def test_ledger_down_is_503_and_no_folder_is_returned(env, monkeypatch):
    e, app, svc = env
    real = e.ledger.append

    def boom(ev):
        if ev.kind == "graph.viewed":
            raise RuntimeError("ledger down")
        return real(ev)
    monkeypatch.setattr(e.ledger, "append", boom)
    r = _get(app, svc, WRITERS[0])
    assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}


def _count_drive_calls(e, monkeypatch) -> list:
    """Every Drive call ``_folders`` can make (list, metadata, permissions), recorded."""
    calls: list = []
    conn = e.connectors["drive"]
    for name in ("_list", "_meta", "_permissions"):
        real = getattr(conn, name)

        def wrap(*a, _real=real, _name=name, **k):
            calls.append((_name, len(_rows(e))))
            return _real(*a, **k)
        monkeypatch.setattr(conn, name, wrap)
    return calls


# round 5 Codex #7 (rule 3): the accepted row comes BEFORE any Drive listing or permission read
def test_the_accepted_row_is_written_before_any_drive_call(env, monkeypatch):
    e, app, svc = env
    calls = _count_drive_calls(e, monkeypatch)
    n = len(_rows(e))
    r = _get(app, svc, WRITERS[0])
    assert r.status_code == 200 and r.json()
    assert calls and all(rows_then >= n + 1 for _name, rows_then in calls)
    pre = _rows(e)[n]
    assert (pre.outcome, pre.nodes_returned, pre.docs_checked) == ("checking", 0, 0)


def test_a_ledger_failure_is_503_with_no_drive_call(env, monkeypatch):
    e, app, svc = env
    calls = _count_drive_calls(e, monkeypatch)

    def boom(ev):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(e.ledger, "append", boom)
    for who in (WRITERS[0], ADMIN_USER, KEN):
        r = _get(app, svc, who)
        assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    assert calls == []                                      # nothing read from Drive


def test_no_ledger_is_503_with_no_drive_call_never_a_silent_release(env, monkeypatch):
    e, app, svc = env
    calls = _count_drive_calls(e, monkeypatch)
    monkeypatch.setattr(svc, "ledger", None)
    for who in (WRITERS[0], KEN):
        r = _get(app, svc, who)
        assert r.status_code == 503 and r.json() == {"error": "temporarily unavailable"}
    assert calls == []


def test_rate_limit_per_person_per_minute_with_one_ledger_row_per_window(world, tmp_path):
    t = [0.0]
    app, svc = _app(world, tmp_path, limiter=_RateLimiter(n=3, clock=lambda: t[0]))
    uid = WRITERS[0]
    for _ in range(3):
        assert _get(app, svc, uid).status_code == 200
    n = len(_refusals(world))
    for _ in range(10):
        r = _get(app, svc, uid)
        assert r.status_code == 404 and r.json() == REFUSAL
    assert [ev.error_code for ev in _refusals(world)[n:]] == ["rate_limited"]
    assert _get(app, svc, KEN).status_code == 200          # per person
    t[0] += 61
    assert _get(app, svc, uid).status_code == 200


def test_reset_clears_the_folder_rate_window(world, tmp_path):
    t = [0.0]
    app, svc = _app(world, tmp_path, limiter=_RateLimiter(n=1, clock=lambda: t[0]))
    uid = WRITERS[0]
    assert _get(app, svc, uid).status_code == 200
    assert _get(app, svc, uid).status_code == 404
    reset_uploads(svc)
    assert _get(app, svc, uid).status_code == 200


@pytest.mark.parametrize("switch,reason", [({"demo_login": False}, "demo_login_off"),
                                           ({"demo_features": False}, "demo_features_off"),
                                           ({"cfg_over": {"deployed": True}}, "deployed")])
def test_a_router_built_with_a_switch_off_refuses_everyone(world, tmp_path, switch, reason):
    app, svc = _app(world, tmp_path, **switch)
    for who in (WRITERS[0], ADMIN_USER):
        r = _get(app, svc, who)
        assert r.status_code == 404 and r.json() == REFUSAL
        assert _refusals(world)[-1].error_code == reason
