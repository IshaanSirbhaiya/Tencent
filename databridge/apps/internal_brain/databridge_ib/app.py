"""The Internal Brain HTTP app — its own process, its own state, an explicit route allowlist (§4.1).

Only these route prefixes may exist (``test_routes_allowlist`` enumerates every mounted route):
``/health``, ``/auth/*``, ``/ib/*``, ``/webhooks/*``, ``/audit/*``, ``/sim/*``. The legacy Walnut gateway
is never imported or mounted here.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import auth as authmod
from .config import IbConfig
from .connectors.base import SourceConnector, WebhookRejected
from .directory import Directory
from .index import Index
from .ingest import Ingestor
from .knowledge_map import build_map
from .llm import ModelChain
from .models import AskRequest, DirectoryUser, SessionStarted
from .pipeline import Pipeline

log = logging.getLogger("databridge_ib.app")

ALLOWED_PREFIXES = ("/health", "/auth/", "/ib/", "/webhooks/", "/audit/", "/sim/")
COOKIE = "ib_session"
EMPLOYEE_ROLES = {"employee", "compliance_officer", "admin_it"}
MAX_WEBHOOK_BYTES = 256 * 1024


@dataclass
class Services:
    cfg: IbConfig
    directory: Directory
    index: Index
    connectors: Mapping[str, SourceConnector]
    ledger: Any
    answers: Any
    pipeline: Pipeline
    ingestor: Ingestor
    projection: Any = None


def principal_dependency(svc_getter: Callable[[], Services]):
    def current(request: Request) -> DirectoryUser:
        svc = svc_getter()
        token = request.cookies.get(COOKIE)
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            token = authz[7:].strip()
        if not token:
            raise HTTPException(401, "authentication required")
        try:
            claims = authmod.verify(svc.cfg.token_key, token,
                                    revoked=authmod.load_revoked(svc.cfg.revoked_path))
        except authmod.TokenError:
            raise HTTPException(401, "invalid session")
        user = svc.directory.get(claims["sub"])
        if user is None or not user.active:
            raise HTTPException(401, "invalid session")
        return user
    return current


def require_role(current, role: str):
    def dep(user: DirectoryUser = Depends(current)) -> DirectoryUser:
        if role not in user.roles:
            raise HTTPException(403, "forbidden")
        return user
    return dep


def create_app(svc: Services, *, audit_router_factory: Optional[Callable] = None,
               sim_router_factory: Optional[Callable] = None) -> FastAPI:
    app = FastAPI(title="Walnut Internal Brain", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.svc = svc
    current = principal_dependency(lambda: app.state.svc)

    @app.get("/health")
    def health():
        return {"ok": True}

    # -- auth --------------------------------------------------------------------------------
    @app.post("/auth/demo-login")
    def demo_login(body: dict, response: Response):
        # Demo sign-in stands in for SSO over SYNTHETIC data only. Off unless IB_DEMO_LOGIN=1; on a
        # public URL set IB_DEMO_PASSCODE so only people given the passcode can pick a persona.
        if not svc.cfg.demo_login:
            raise HTTPException(404, "not found")
        passcode = os.environ.get("IB_DEMO_PASSCODE", "")
        if passcode and not hmac.compare_digest(str(body.get("passcode", "")), passcode):
            raise HTTPException(401, "passcode required")
        user = svc.directory.get(str(body.get("persona", "")))
        if user is None or not user.active:
            raise HTTPException(400, "unknown persona")
        try:                                  # write-ahead: the sign-in is on record before it works
            svc.ledger.append(SessionStarted(sub=user.id, method="demo", roles=tuple(user.roles),
                                             event_time=datetime.now(timezone.utc)))
        except Exception as exc:                                    # noqa: BLE001
            log.error("sign-in not recorded: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        tok = authmod.mint(svc.cfg.token_key, user.id, list(user.roles), ttl_s=8 * 3600)
        response.set_cookie(COOKIE, tok, httponly=True, samesite="lax", max_age=8 * 3600,
                            secure=svc.cfg.deployed)
        return {"sub": user.id, "roles": list(user.roles), "name": user.name}

    @app.post("/auth/logout")
    def logout(request: Request, response: Response, user: DirectoryUser = Depends(current)):
        token = request.cookies.get(COOKIE)
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            token = authz[7:].strip()
        try:                                  # revoke the jti actually used (sec1 #6)
            claims = authmod.verify(svc.cfg.token_key, token or "",
                                    revoked=authmod.load_revoked(svc.cfg.revoked_path))
            authmod.revoke(svc.cfg.revoked_path, claims["jti"])
        except authmod.RevocationUnavailable:     # do not report success for a still-valid token
            return JSONResponse(status_code=503, content={"error": "sign-out could not be recorded; try again"})
        except authmod.TokenError:
            pass
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/auth/whoami")
    def whoami(user: DirectoryUser = Depends(current)):
        return {"sub": user.id, "name": user.name, "roles": list(user.roles),
                "employment": user.employment}

    # -- ask ---------------------------------------------------------------------------------
    @app.post("/ib/ask")
    def ask(req: AskRequest, request: Request, user: DirectoryUser = Depends(current)):
        if not (set(user.roles) & EMPLOYEE_ROLES):
            raise HTTPException(403, "forbidden")
        # the MCP label is honoured only on bearer-token calls (the MCP client's transport), never
        # on the browser's cookie session (CodeBuddy security review CB5 finding 5)
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        channel = ("workbuddy-mcp" if bearer and request.headers.get("x-ib-channel") == "workbuddy-mcp"
                   else "ui")
        try:
            resp = svc.pipeline.ask(user, req.question, channel=channel)
        except Exception as exc:                                    # noqa: BLE001
            log.error("ask failed: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        return json.loads(resp.model_dump_json())

    @app.get("/ib/map")
    def knowledge_map(user: DirectoryUser = Depends(current)):
        if not (set(user.roles) & EMPLOYEE_ROLES):
            raise HTTPException(403, "forbidden")
        try:
            return build_map(user, index=svc.index, directory=svc.directory, ledger=svc.ledger,
                             connectors=svc.connectors, clock=lambda: datetime.now(timezone.utc))
        except Exception as exc:                                    # noqa: BLE001
            log.error("map failed: %s", type(exc).__name__)       # incl. ledger down: fail closed
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

    @app.get("/ib/history")
    def history(user: DirectoryUser = Depends(current)):
        if not (set(user.roles) & EMPLOYEE_ROLES):          # same rule as ask and map
            raise HTTPException(403, "forbidden")
        if svc.projection is None:
            return {"items": []}
        rows = svc.projection.search(svc.projection.filter_type(user=user.id, kind="request.accepted",
                                                                limit=50))
        return {"items": [{"req_id": r.get("req_id"), "question": r.get("question"),
                           "event_time": r.get("event_time")} for r in rows]}

    @app.get("/ib/sources")
    def sources(user: DirectoryUser = Depends(current)):
        if not (set(user.roles) & EMPLOYEE_ROLES):          # same rule as ask and map
            raise HTTPException(403, "forbidden")
        now = datetime.now(timezone.utc)
        out = []
        for p, st in svc.ingestor.status.items():
            last = st.get("last_sync_at")
            lag = (now - datetime.fromisoformat(last)).total_seconds() if last else None
            out.append({"platform": p, "last_sync_at": last,
                        "lag_s": None if lag is None else int(lag), "slo_s": svc.cfg.slo_s,
                        "state": st.get("state"),
                        "within_slo": None if lag is None else lag <= svc.cfg.slo_s})
        return {"sources": out}

    # -- webhooks ------------------------------------------------------------------------------
    @app.post("/webhooks/{platform}")
    async def webhook(platform: str, request: Request):
        conn = svc.connectors.get(platform)
        if conn is None:
            raise HTTPException(404, "not found")
        if int(request.headers.get("content-length") or 0) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "too large")
        body = await request.body()
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "too large")
        try:
            events = conn.parse_webhook({k.lower(): v for k, v in request.headers.items()}, body)
        except WebhookRejected:
            raise HTTPException(401, "rejected")
        from starlette.concurrency import run_in_threadpool
        for ev in events:                     # fetch/embed/index off the event loop (sec1 #23)
            await run_in_threadpool(svc.ingestor.apply_event, ev)
        return Response(status_code=204)

    if audit_router_factory is not None:
        app.include_router(audit_router_factory(current, lambda r: require_role(current, r)))
    if sim_router_factory is not None:
        app.include_router(sim_router_factory(current, lambda r: require_role(current, r)))
    bad = [p for p in mounted_paths(app) if p and p != "/health" and not p.startswith(ALLOWED_PREFIXES)]
    if bad:                                   # the allowlist is enforced, not documented (sec1 #26)
        raise RuntimeError(f"routes outside the allowlist: {bad}")
    return app


def mounted_paths(app: FastAPI) -> list[str]:
    return sorted({getattr(r, "path", "") for r in app.routes})
