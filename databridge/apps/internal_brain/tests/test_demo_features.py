"""The demo-proof features are a server toggle (D-24, SECURITY.md §15.12).

* ``IB_DEMO_FEATURES`` off (the default): ``GET /ib/graph/changes``, ``POST /sim/add-document``,
  ``/sim/check-absent``, ``/sim/engine-refresh`` and ``/sim/engine-status`` are not mounted, so
  each answers byte-for-byte what an unknown route answers, signed in or not; ``/ib/status`` says
  ``demo_features: false``.
* On a deployment (``IB_DEPLOYED=1``) they stay off even with the flag set.
* On: the app built the way ``bootstrap.build_app`` builds it still passes the route allowlist and
  every non-public route needs a session (PLAN L-5).
* The engine refresh: who may start it, single flight, one start per 2 minutes, write-ahead
  ledger, and an in-place publish that works while another connection holds the snapshot open.
"""
from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import ALLOWED_PREFIXES, create_app, iter_routes, mounted_paths
from databridge_ib.bootstrap import build_app, build_services
from databridge_ib.config import IbConfig
from databridge_ib.engine_refresh import EngineRefresher
from databridge_ib.sim import make_sim_router

DEMO_ROUTES = [("GET", "/ib/graph/changes"), ("POST", "/sim/add-document"),
               ("POST", "/sim/check-absent"), ("POST", "/sim/engine-refresh"),
               ("POST", "/sim/engine-status")]
UNKNOWN = {"GET": "/ib/graph/no-such-route", "POST": "/sim/no-such-route"}
PUBLIC_ROUTES = {"/health", "/auth/demo-login", "/webhooks/{platform}"}


def _cfg(tmp_path, **kw) -> IbConfig:
    base = dict(var_root=tmp_path / "var", demo_login=True, demo_features=False,
                engine_graph_db=tmp_path / "graph" / "enterprise.db", engine_token="")
    base.update(kw)
    return IbConfig(**base)


def _client(app, svc, sub):
    tok = authmod.mint(svc.cfg.token_key, sub, list(svc.directory.get(sub).roles))
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


@pytest.fixture
def off_app(tmp_path):
    app = build_app(_cfg(tmp_path), sync=False)
    return app, app.state.svc


@pytest.fixture
def on_app(tmp_path):
    app = build_app(_cfg(tmp_path, demo_features=True), sync=False)
    return app, app.state.svc


# --- toggle off -------------------------------------------------------------------------------------
def test_off_by_default(monkeypatch):
    monkeypatch.delenv("IB_DEMO_FEATURES", raising=False)
    assert IbConfig().demo_features is False and IbConfig().demo_features_on is False
    monkeypatch.setenv("IB_DEMO_FEATURES", "1")
    assert IbConfig().demo_features_on is True
    monkeypatch.setenv("IB_DEMO_FEATURES", "true")          # only the exact value "1" turns it on
    assert IbConfig().demo_features_on is False


def test_off_every_demo_route_is_the_unknown_route_404(off_app):
    app, svc = off_app
    paths = mounted_paths(app)
    for _m, p in DEMO_ROUTES:
        assert p not in paths
    for c in (TestClient(app), _client(app, svc, "jdoe"), _client(app, svc, "admin.it")):
        for method, path in DEMO_ROUTES:
            r = c.request(method, path, json={"platform": "slack", "target": "C0DBMIG", "text": "x"})
            u = c.request(method, UNKNOWN[method], json={})
            assert r.status_code == u.status_code == 404, (method, path)
            assert r.content == u.content and r.headers.get("content-type") == u.headers.get(
                "content-type"), (method, path)


def test_status_reports_the_flag(off_app, on_app):
    for (app, svc), want in ((off_app, False), (on_app, True)):
        body = _client(app, svc, "jdoe").get("/ib/status").json()
        assert body["demo_features"] is want


def test_a_deployment_never_turns_them_on(tmp_path):
    cfg = _cfg(tmp_path, demo_features=True, deployed=True)
    assert cfg.demo_features_on is False
    router = make_sim_router(cfg, lambda r: (lambda: None))
    assert {getattr(r, "path", "") for r in router.routes}.isdisjoint(
        {p for _m, p in DEMO_ROUTES})


# --- toggle on: the app as bootstrap builds it (PLAN L-5) ------------------------------------------
def test_on_routes_are_mounted_allowlisted_and_need_a_session(on_app):
    app, _svc = on_app
    paths = mounted_paths(app)
    for _m, p in DEMO_ROUTES:
        assert p in paths, p
    for p in paths:
        assert p == "/health" or p.startswith(ALLOWED_PREFIXES), p
    c = TestClient(app)
    for r in iter_routes(app):
        path, methods = getattr(r, "path", ""), getattr(r, "methods", set())
        if path in PUBLIC_ROUTES or not methods:
            continue
        m = sorted(methods)[0]
        resp = c.request(m, path.replace("{platform}", "slack"), json={})
        assert resp.status_code == 401, (m, path, resp.status_code)


# --- engine refresh ------------------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def _snapshot(path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE IF NOT EXISTS m(v TEXT)")
    con.execute("DELETE FROM m")
    con.execute("INSERT INTO m VALUES(?)", (marker,))
    con.commit()
    con.close()


def _engine_app(tmp_path, *, build=None, demo_login=True):
    cfg = _cfg(tmp_path, demo_features=True, demo_login=demo_login)
    svc = build_services(cfg)
    clock = Clock()
    builds: list = []

    def fake_build(out):
        builds.append(out)
        _snapshot(out, f"v{len(builds) + 1}")

    reloads: list = []
    eng = EngineRefresher(cfg, build=build or fake_build, reload=lambda: reloads.append(1) or True,
                          clock=clock, run_inline=True)
    app = create_app(svc, sim_router_factory=lambda cur, rr: make_sim_router(
        cfg, rr, ingestor=svc.ingestor, current=cur, ledger=svc.ledger, engine=eng))
    return app, svc, eng, clock, builds, reloads



def test_engine_refresh_rebuilds_publishes_in_place_and_reloads(tmp_path):
    app, svc, eng, clock, builds, reloads = _engine_app(tmp_path)
    target = svc.cfg.engine_graph_db
    _snapshot(target, "v1")
    held = sqlite3.connect(str(target))                     # the gateway holds the file open
    try:
        assert held.execute("SELECT v FROM m").fetchone()[0] == "v1"
        jd = _client(app, svc, "jdoe")
        st = jd.post("/sim/engine-status").json()
        assert st["state"] == "idle" and set(st) == {"state", "step", "started_at", "finished_at",
                                                     "snapshot_at", "reloaded"}
        r = jd.post("/sim/engine-refresh")
        assert r.status_code == 200, r.text
        body = jd.post("/sim/engine-status").json()
        assert body["state"] == "done" and body["reloaded"] is True and body["finished_at"]
        assert len(builds) == 1 and reloads == [1]
        assert held.execute("SELECT v FROM m").fetchone()[0] == "v2"   # same open handle, new rows
        assert not builds[0].exists()                       # the side file is cleaned up
    finally:
        held.close()


def test_engine_refresh_once_per_two_minutes_and_ledgered_write_ahead(tmp_path):
    app, svc, eng, clock, builds, reloads = _engine_app(tmp_path)
    adm = _client(app, svc, "admin.it")
    seen = []
    real = svc.ledger.append

    def spy(ev):
        seen.append(ev)
        return real(ev)
    svc.ledger.append = spy                                 # type: ignore[method-assign]
    assert adm.post("/sim/engine-refresh").status_code == 200
    clock.t += 60
    r = adm.post("/sim/engine-refresh")
    assert r.status_code == 429 and r.json() == {"detail": "too many requests"}
    assert len(builds) == 1
    clock.t += 61
    assert adm.post("/sim/engine-refresh").status_code == 200 and len(builds) == 2
    phases = [e.detail.get("phase") for e in seen
              if e.kind == "ingest.sync" and e.detail.get("reason") == "engine.refresh"]
    assert phases == ["started", "done", "started", "done"]
    assert any(e.kind == "request.failed" and e.error_code == "rate_limited" for e in seen)


def test_engine_refresh_ledger_down_is_503_and_nothing_runs(tmp_path):
    app, svc, eng, clock, builds, reloads = _engine_app(tmp_path)

    def boom(ev):
        raise RuntimeError("ledger down")
    svc.ledger.append = boom                                # type: ignore[method-assign]
    r = _client(app, svc, "jdoe").post("/sim/engine-refresh")
    assert r.status_code == 503 and builds == []


def test_engine_refresh_is_jdoe_or_admin_only_with_demo_login(tmp_path):
    app, svc, eng, clock, builds, reloads = _engine_app(tmp_path)
    ken = _client(app, svc, "ken.ext")
    a = ken.post("/sim/engine-refresh")
    b = ken.post("/sim/engine-status")
    c = _client(app, svc, "jdoe").post("/sim/add-document",
                                       json={"platform": "slack", "target": "C0NOPE", "text": "x"})
    assert a.status_code == b.status_code == c.status_code == 404
    assert a.content == b.content == c.content
    assert builds == []
    app2, svc2, *_rest, builds2, _r2 = _engine_app(tmp_path / "off", demo_login=False)
    assert _client(app2, svc2, "jdoe").post("/sim/engine-refresh").status_code == 404
    assert builds2 == []


def test_a_failed_build_never_touches_the_live_snapshot(tmp_path):
    def bad_build(out):
        _snapshot(out, "half")
        raise RuntimeError("build failed")
    app, svc, eng, clock, builds, reloads = _engine_app(tmp_path, build=bad_build)
    _snapshot(svc.cfg.engine_graph_db, "v1")
    _client(app, svc, "jdoe").post("/sim/engine-refresh")
    st = eng.status()
    assert st["state"] == "failed" and reloads == []
    con = sqlite3.connect(str(svc.cfg.engine_graph_db))
    assert con.execute("SELECT v FROM m").fetchone()[0] == "v1"
    con.close()


def test_background_job_is_single_flight(tmp_path):
    import threading
    gate = threading.Event()

    def slow_build(out):
        gate.wait(5)
        _snapshot(out, "slow")
    cfg = _cfg(tmp_path, demo_features=True)
    eng = EngineRefresher(cfg, build=slow_build, reload=lambda: False)
    assert eng.start() is True
    assert eng.status()["state"] == "running" and eng.may_start() == "running"
    assert eng.start() is False                             # a second start while running
    gate.set()
    for _ in range(100):
        if eng.status()["state"] != "running":
            break
        time.sleep(0.05)
    assert eng.status()["state"] == "done" and eng.status()["reloaded"] is False
