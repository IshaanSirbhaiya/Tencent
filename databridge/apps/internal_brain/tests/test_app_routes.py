"""The IB app exposes only allowlisted routes, all authenticated (Opus B1 / Codex B5), identity only
from the signed token (P8), and fails closed (503) when the pipeline cannot ledger."""
from __future__ import annotations

from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib.app import ALLOWED_PREFIXES, Services, create_app, mounted_paths
from databridge_ib.config import IbConfig
from databridge_ib.index import Index
from databridge_ib.ingest import Ingestor
from databridge_ib.pipeline import Pipeline

from test_pipeline_core import (PRIVATE, PUBLIC, READERS, FakeConnector, MemAnswers, MemLedger,
                                _users)

PUBLIC_ROUTES = {"/health", "/auth/demo-login", "/webhooks/{platform}"}


def _svc(tmp_path, ledger=None, demo=True):
    cfg = IbConfig(var_root=tmp_path / "var")
    object.__setattr__(cfg, "demo_login", demo)
    conn = FakeConnector("slack", [PUBLIC, PRIVATE], READERS)
    idx = Index(tmp_path / "index.sqlite")
    for d in (PUBLIC, PRIVATE):
        idx.upsert(d, conn.acl(d.doc_id))
    directory = _users()
    ledger = ledger or MemLedger()
    pipe = Pipeline(index=idx, directory=directory, connectors={"slack": conn}, ledger=ledger,
                    answers=MemAnswers())
    ing = Ingestor(idx, {"slack": conn}, tmp_path / "cursors.json", directory=directory)
    return Services(cfg=cfg, directory=directory, index=idx, connectors={"slack": conn},
                    ledger=ledger, answers=MemAnswers(), pipeline=pipe, ingestor=ing)


def _employee_roles(svc):
    # test directory users carry the default role tuple; give them the employee role
    for u in svc.directory.all():
        svc.directory._users[u.id] = u.model_copy(update={"roles": ("employee",)})


def test_only_allowlisted_routes(tmp_path):
    app = create_app(_svc(tmp_path))
    for path in mounted_paths(app):
        assert path == "/health" or path.startswith(ALLOWED_PREFIXES), path


def test_every_non_public_route_requires_auth(tmp_path):
    app = create_app(_svc(tmp_path))
    c = TestClient(app)
    for r in app.routes:
        path, methods = getattr(r, "path", ""), getattr(r, "methods", set())
        if path in PUBLIC_ROUTES or not methods:
            continue
        m = sorted(methods)[0]
        resp = c.request(m, path.replace("{platform}", "slack"), json={"question": "x"})
        assert resp.status_code == 401, (m, path, resp.status_code)


def test_identity_from_token_only(tmp_path):
    svc = _svc(tmp_path)
    _employee_roles(svc)
    c = TestClient(create_app(svc))
    tok = authmod.mint(svc.cfg.token_key, "ken.ext", ["employee"])
    r = c.post("/ib/ask", json={"question": "database migration status"},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    # a body that tries to name another principal is rejected by the closed request schema
    r2 = c.post("/ib/ask", json={"question": "database migration status", "user": "priya"},
                headers={"Authorization": f"Bearer {tok}"})
    assert r2.status_code == 422


def test_bad_and_expired_tokens(tmp_path):
    svc = _svc(tmp_path)
    c = TestClient(create_app(svc))
    bad = authmod.mint("other-key", "jdoe", ["employee"])
    assert c.get("/auth/whoami", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    old = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"], now=0)
    assert c.get("/auth/whoami", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_demo_login_cookie_and_disabled(tmp_path):
    svc = _svc(tmp_path)
    _employee_roles(svc)
    c = TestClient(create_app(svc))
    assert c.post("/auth/demo-login", json={"persona": "jdoe"}).status_code == 200
    assert c.get("/auth/whoami").json()["sub"] == "jdoe"
    off = TestClient(create_app(_svc(tmp_path / "off", demo=False)))
    assert off.post("/auth/demo-login", json={"persona": "jdoe"}).status_code == 404


def test_ledger_failure_is_503(tmp_path):
    svc = _svc(tmp_path, ledger=MemLedger(fail_on="request.accepted"))
    _employee_roles(svc)
    c = TestClient(create_app(svc))
    tok = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    r = c.post("/ib/ask", json={"question": "migration"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 503
