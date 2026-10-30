"""FastAPI factory for the mock platforms (CONTRACTS §2; plan §5.2).

"A supported semantic subset, not a replica": real paths and response shapes for the subset the
Internal Brain's connectors use, with permission semantics computed from ``world.json``.

Mounts::

    /slack/api/*             Slack Web API subset
    /confluence/wiki/*       Confluence Cloud v2 (+ v1 restrictions / permission check)
    /jira/rest/api/3/*       Jira Cloud REST v3 subset
    /drive/v3/*              Google Drive v3 subset
    /admin/*                 simulator mutations (admin token), each firing a signed webhook
    /health                  unauthenticated liveness
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import admin, confluence, drive, jira, slack
from .common import PlatformError, platform_error_handler
from .state import World
from .webhooks import Sink, WebhookEmitter

DEFAULT_WORLD = Path(__file__).resolve().parents[3] / "fixtures" / "enterprise" / "world.json"
#: DNS-rebinding guard: loopback Host names, the in-process test transport's fixed Host, and any
#: extra names in MOCK_ALLOWED_HOSTS (comma-separated). Anything else is a 400 before any route.
TRUSTED_HOSTS = ("127.0.0.1", "localhost", "[::1]", "testserver")


def trusted_hosts() -> list[str]:
    extra = [h.strip().lower() for h in os.environ.get("MOCK_ALLOWED_HOSTS", "").split(",")
             if h.strip()]
    return list(dict.fromkeys(list(TRUSTED_HOSTS) + extra))


def create_app(world_path: str | Path | None = None, *, webhook_sink: Optional[Sink] = None,
               svc_token: Optional[str] = None, admin_token: Optional[str] = None,
               webhook_secret: Optional[str] = None, webhook_url: Optional[str] = None
               ) -> FastAPI:
    path = Path(world_path or os.environ.get("MOCK_WORLD_PATH") or DEFAULT_WORLD)
    svc = svc_token or os.environ.get("MOCK_SVC_TOKEN") or "svc-dev"
    adm = admin_token or os.environ.get("MOCK_ADMIN_TOKEN") or "admin-dev"
    if svc == adm:
        raise ValueError("svc_token and admin_token must differ")
    secret = webhook_secret or os.environ.get("MOCK_WEBHOOK_SECRET") or "whsec-dev"
    url = webhook_url if webhook_url is not None else os.environ.get("MOCK_WEBHOOK_URL")

    emitter = WebhookEmitter(secret, sink=webhook_sink, url=url or None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        emitter.close()

    app = FastAPI(title="Walnut IB mock platforms", lifespan=lifespan,
                  description="A supported semantic subset of Slack, Confluence, Jira and Drive "
                              "- not a replica.")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts())
    app.state.world = World(path)
    app.state.svc_token = svc
    app.state.admin_token = adm
    app.state.webhooks = emitter
    app.add_exception_handler(PlatformError, platform_error_handler)

    app.include_router(slack.router, prefix="/slack/api", tags=["slack"])
    app.include_router(confluence.router, prefix="/confluence/wiki", tags=["confluence"])
    app.include_router(jira.router, prefix="/jira/rest/api/3", tags=["jira"])
    app.include_router(drive.router, prefix="/drive/v3", tags=["drive"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    from .uploads import router as drive_upload_router; app.include_router(drive_upload_router, prefix="/admin", tags=["admin"])  # POST /admin/drive/files/upload

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "world": str(path)}

    return app
