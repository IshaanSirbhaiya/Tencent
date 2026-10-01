"""`/automation` and `/platform/catalogue` — the trigger engine and what the English compiler sees.

    GET  /automation/status          the engine: running?, specs + next fire, recent firings
    POST /automation/{id}/fire       "run now" through the engine (idempotency key, recorded
                                     firing). Background unless ``wait: true``.
    POST /automation/reload          re-read specs from disk (saves already do this)
    GET  /platform/catalogue         connections + tools, ontology kinds, internal tools,
                                     governance defaults — exactly what `compile_english` gets

The engine is hosted in the gateway lifespan (`platform_runtime.start_platform`); firings are
routed by spec kind (an agent → `run_agent`, an adapter agent → `sync_runner`). A manual firing
records the signed-in principal as ``by``, never a name from the body.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import ENGINE_ENV, platform_of

log = logging.getLogger("databridge.gateway.automation")


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


class FireRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: Optional[str] = None
    idempotency_key: Optional[str] = None
    wait: bool = False


def _recent_firings(engine: Any, limit: int) -> list[dict]:
    path = getattr(getattr(engine, "firings", None), "path", None)
    if path is None or not path.is_file():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def register_automation_routes(app: FastAPI) -> None:
    install_auth_handler(app)

    @app.get("/automation/status")
    def automation_status(limit: int = Query(50, ge=1, le=500),
                          _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt = platform_of(app)
        if rt.engine is None:
            return _refusal("ENGINE_UNAVAILABLE", "; ".join(rt.problems), status=503)
        return {"running": rt.engine_running, "enabled": rt.engine_enabled,
                "enable_with": f"{ENGINE_ENV}=1", **rt.engine.status(),
                "platform_problems": list(rt.problems[-20:]),
                "recent_firings": _recent_firings(rt.engine, limit)}

    @app.post("/automation/reload")
    def automation_reload(_p: AuthenticatedPrincipal = Depends(require_principal)):
        rt = platform_of(app)
        if rt.engine is None:
            return _refusal("ENGINE_UNAVAILABLE", "; ".join(rt.problems), status=503)
        rt.reload_engine()
        return {"specs": sorted(rt.engine.specs)}

    @app.post("/automation/{spec_id}/fire", status_code=202)
    async def automation_fire(spec_id: str, body: Optional[FireRequest] = Body(None),
                              principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt = platform_of(app)
        if rt.engine is None:
            return _refusal("ENGINE_UNAVAILABLE", "; ".join(rt.problems), status=503)
        body = body or FireRequest()
        rt.reload_engine()
        if spec_id not in rt.engine.specs:
            return _refusal("UNKNOWN_SPEC", f"no agent or adapter agent {spec_id!r} is "
                                            f"registered with the engine", status=404)
        # `TriggerEngine.fire` already applies the engine's single admission gate and records a
        # draft/paused spec's firing as `skipped` without running it. The route now says so up
        # front instead of answering 202 "dispatched" (R-019 round 2, platform #4): the firing is
        # awaited (it returns at once, nothing runs) so the skip stays on the audit trail, and
        # the caller gets 409 with that record. Each runner re-checks at execution time too
        # (`run_agent_now`, `manager.sync`).
        from databridge_automation import admission_refusal           # noqa: PLC0415

        refused = admission_refusal(rt.engine.specs[spec_id])
        if refused:
            record = await rt.engine.fire(spec_id, idempotency_key=body.idempotency_key,
                                          by=principal.principal_id)
            return JSONResponse(status_code=409, content={
                "refusal": {"code": "SPEC_NOT_ADMITTED",
                            "message": f"{spec_id!r} cannot run: {refused}",
                            "nearest_alternative": None},
                "firing": record})
        payload = {"question": body.question} if body.question else {}
        coro = rt.engine.fire(spec_id, idempotency_key=body.idempotency_key, payload=payload,
                              by=principal.principal_id)
        if body.wait:
            record = await coro
            return JSONResponse(status_code=200, content={"firing": record})
        task = asyncio.ensure_future(coro)
        rt._tasks.add(task)
        task.add_done_callback(rt._tasks.discard)
        return {"status": "dispatched", "spec_id": spec_id, "by": principal.principal_id,
                "note": "the firing is recorded in GET /automation/status recent_firings"}

    @app.get("/platform/catalogue")
    def platform_catalogue(_p: AuthenticatedPrincipal = Depends(require_principal)):
        from .agents_routes import current_catalogue                   # noqa: PLC0415

        try:
            cat = current_catalogue()
        except Exception as exc:                                      # noqa: BLE001
            return _refusal("CATALOGUE_UNAVAILABLE", f"{type(exc).__name__}: {exc}", status=503)
        gov = cat.governance
        return {**cat.as_record(), "existing_ids": list(cat.existing_ids),
                "governance": {"pack": gov.pack, "purpose": gov.purpose,
                               "golden_set": gov.golden_set, "citation": gov.citation,
                               "trust_ceiling": gov.trust_ceiling}}


__all__ = ["register_automation_routes"]
