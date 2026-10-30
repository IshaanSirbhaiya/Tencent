"""``POST /ib/deliverables`` and ``POST /ib/deliverables/export``.

Always mounted (the route allowlist test enumerates them). The order on both routes: a valid
session (401), the employee role (403), the kill switch (``IB_DELIVERABLES`` not ``1``: the SAME
404 body as not-found, nothing recorded: a disabled feature is not an access), then the service.
Bodies carry no identity field (``extra="forbid"``: ``user``/``sub``/``as`` are a 422). The body
is parsed and validated AFTER the kill switch (round 3 I1), so with the switch off any body, valid
or not, gets the same 404.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..models import DirectoryUser
from . import service as svcmod

log = logging.getLogger("databridge_ib.deliverables")
NOT_FOUND = "not found"


class DeliverBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    req_id: str = Field(min_length=1, max_length=200)
    kind: Literal["table", "chart", "analysis"]
    chart: Literal["auto", "bar", "line"] = "auto"


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deliverable_id: str = Field(min_length=1, max_length=64)
    format: Literal["csv", "pptx"]


def mount(app: FastAPI, service: svcmod.DeliverableService, current: Callable,
          employee: Callable[[DirectoryUser], None]) -> None:

    def _run(fn: Callable[[], Any], what: str):
        try:
            return fn()
        except svcmod.NotFound:
            raise HTTPException(404, NOT_FOUND) from None
        except svcmod.RateLimited:
            raise HTTPException(429, "too many requests") from None
        except svcmod.Invalid:
            raise HTTPException(422, "invalid") from None
        except Exception as exc:                                    # noqa: BLE001
            log.error("%s failed: %s", what, type(exc).__name__)    # incl. ledger down
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

    def _on(user: DirectoryUser) -> None:
        employee(user)
        if not service.enabled:
            raise HTTPException(404, NOT_FOUND)

    async def _body(model: type[BaseModel], request: Request) -> Any:
        try:
            return model.model_validate_json(await request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False)) from None

    def _schema(model: type[BaseModel]) -> dict[str, Any]:
        return {"requestBody": {"required": True, "content": {"application/json": {
            "schema": model.model_json_schema()}}}}

    @app.post("/ib/deliverables", openapi_extra=_schema(DeliverBody))
    async def deliverable(request: Request, user: DirectoryUser = Depends(current)):
        """A table, chart or analysis from one of MY released answers and the cited sources I can
        still open. ``deliverable.requested`` before any source read or model call;
        ``deliverable.created`` before the response."""
        _on(user)
        body = await _body(DeliverBody, request)
        return await run_in_threadpool(
            _run, lambda: service.create(user, body.req_id, body.kind, body.chart), "deliverable")

    @app.post("/ib/deliverables/export", openapi_extra=_schema(ExportBody))
    async def deliverable_export(request: Request, user: DirectoryUser = Depends(current)):
        """A file built on the server from MY stored, checked deliverable, re-checked against the
        sources now. ``deliverable.exported`` before the bytes."""
        _on(user)
        body = await _body(ExportBody, request)

        def go():
            data, ctype, disposition = service.export(user, body.deliverable_id, body.format)
            return Response(content=data, media_type=ctype.split(";")[0],
                            headers={"Content-Type": ctype, "Content-Disposition": disposition,
                                     "X-Content-Type-Options": "nosniff",
                                     "Cache-Control": "no-store"})
        return await run_in_threadpool(_run, go, "deliverable export")


__all__ = ["mount", "DeliverBody", "ExportBody"]
