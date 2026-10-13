"""The Internal Brain HTTP app — its own process, its own state, an explicit route allowlist (§4.1).

Only these route prefixes may exist (``test_routes_allowlist`` enumerates every mounted route):
``/health``, ``/auth/*``, ``/ib/*``, ``/webhooks/*``, ``/audit/*``, ``/sim/*``. The legacy Walnut gateway
is never imported or mounted here.
"""
from __future__ import annotations

import json
import logging
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
from .llm import ModelChain
from .models import AskRequest, DirectoryUser
from .pipeline import Pipeline

log = logging.getLogger("databridge_ib.app")

ALLOWED_PREFIXES = ("/health", "/auth/", "/ib/", "/webhooks/", "/audit/", "/sim/")
COOKIE = "ib_session"
EMPLOYEE_ROLES = {"employee", "compliance_officer", "admin_it"}


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
        if not svc.cfg.demo_login:
            raise HTTPException(404, "not found")
        user = svc.directory.get(str(body.get("persona", "")))
        if user is None or not user.active:
            raise HTTPException(400, "unknown persona")
        tok = authmod.mint(svc.cfg.token_key, user.id, list(user.roles), ttl_s=8 * 3600)
        response.set_cookie(COOKIE, tok, httponly=True, samesite="lax", max_age=8 * 3600)
        return {"sub": user.id, "roles": list(user.roles), "name": user.name}

    @app.post("/auth/logout")
    def logout(response: Response, user: DirectoryUser = Depends(current)):
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
        channel = "workbuddy-mcp" if request.headers.get("x-ib-channel") == "workbuddy-mcp" else "ui"
        try:
            resp = svc.pipeline.ask(user, req.question, channel=channel)
        except Exception as exc:                                    # noqa: BLE001
            log.error("ask failed: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        return json.loads(resp.model_dump_json())

    @app.get("/ib/history")
    def history(user: DirectoryUser = Depends(current)):
        if svc.projection is None:
            return {"items": []}
        rows = svc.projection.search(svc.projection.filter_type(user=user.id, kind="request.accepted",
                                                                limit=50))
        return {"items": [{"req_id": r.get("req_id"), "question": r.get("question"),
                           "event_time": r.get("event_time")} for r in rows]}

    @app.get("/ib/sources")
    def sources(user: DirectoryUser = Depends(current)):
        now = datetime.now(timezone.utc)
        out = []
        for p, st in svc.ingestor.status.items():
            last = st.get("last_event_at") or st.get("last_sync_at")
            lag = (now - datetime.fromisoformat(last)).total_seconds() if last else None
            out.append({"platform": p, "last_sync_at": st.get("last_sync_at"),
                        "lag_s": None if lag is None else int(lag),
                        "last_event_at": st.get("last_event_at"), "slo_s": svc.cfg.slo_s,
                        "state": st.get("state"),
                        "within_slo": None if lag is None else lag <= svc.cfg.slo_s})
        return {"sources": out}

    # -- webhooks ------------------------------------------------------------------------------
    @app.post("/webhooks/{platform}")
    async def webhook(platform: str, request: Request):
        conn = svc.connectors.get(platform)
        if conn is None:
            raise HTTPException(404, "not found")
        body = await request.body()
        try:
            events = conn.parse_webhook({k.lower(): v for k, v in request.headers.items()}, body)
        except WebhookRejected:
            raise HTTPException(401, "rejected")
        for ev in events:
            svc.ingestor.apply_event(ev)
        return Response(status_code=204)

    if audit_router_factory is not None:
        app.include_router(audit_router_factory(current, lambda r: require_role(current, r)))
    if sim_router_factory is not None:
        app.include_router(sim_router_factory(current, lambda r: require_role(current, r)))
    return app


def mounted_paths(app: FastAPI) -> list[str]:
    return sorted({getattr(r, "path", "") for r in app.routes})
