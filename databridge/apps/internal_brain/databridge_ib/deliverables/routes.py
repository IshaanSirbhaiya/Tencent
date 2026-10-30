"""``POST /ib/deliverables`` and ``POST /ib/deliverables/export``.

Always mounted (the route allowlist test enumerates them). The order on both routes: a valid
session (401), the employee role (403), the kill switch (``IB_DELIVERABLES`` not ``1``: the SAME
404 body as not-found, nothing recorded: a disabled feature is not an access), then the service.
Bodies carry no identity field (``extra="forbid"``: ``user``/``sub``/``as`` are a 422). The body
is parsed and validated AFTER the kill switch (round 3 I1), so with the switch off any body, valid
or not, gets the same 404.

Round 4 B12: after the kill switch, the per-person rate limit is applied (429) BEFORE the body is
read, and the body is read in chunks capped at ``MAX_BODY_BYTES`` (a ``Content-Length`` over the
cap, or a stream that grows past it, is a 413 and is never held whole or parsed).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..models import DirectoryUser
from . import service as svcmod

log = logging.getLogger("databridge_ib.deliverables")
NOT_FOUND = "not found"
#: both bodies are a few hundred bytes at most (``req_id`` <= 200 chars)
MAX_BODY_BYTES = 4096


async def _read_capped(request: Request) -> Optional[bytes]:
    """The body, or None when it is (or claims to be) over ``MAX_BODY_BYTES``; read in chunks so
    an oversized body is never held whole, and never parsed."""
    cl = request.headers.get("content-length")
    if cl is not None and (not cl.strip().isdigit() or int(cl) > MAX_BODY_BYTES):
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > MAX_BODY_BYTES:
            return None
    return bytes(buf)


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

    async def _admit(user: DirectoryUser, op: str) -> Optional[Response]:
        """The rate limit, before the body is read: None to go on, else the 503 response (a 429
        is raised)."""
        got = await run_in_threadpool(_run, lambda: service.admit(user, op), "deliverable " + op)
        return got if isinstance(got, Response) else None

    async def _body(model: type[BaseModel], request: Request) -> Any:
        raw = await _read_capped(request)
        if raw is None:
            raise HTTPException(413, "too large")
        try:
            return model.model_validate_json(raw)
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
        refused = await _admit(user, "create")
        if refused is not None:
            return refused
        body = await _body(DeliverBody, request)
        return await run_in_threadpool(
            _run, lambda: service.create(user, body.req_id, body.kind, body.chart, admitted=True),
            "deliverable")

    @app.post("/ib/deliverables/export", openapi_extra=_schema(ExportBody))
    async def deliverable_export(request: Request, user: DirectoryUser = Depends(current)):
        """A file built on the server from MY stored, checked deliverable, re-checked against the
        sources now. ``deliverable.exported`` before the bytes."""
        _on(user)
        refused = await _admit(user, "export")
        if refused is not None:
            return refused
        body = await _body(ExportBody, request)

        def go():
            data, ctype, disposition = service.export(user, body.deliverable_id, body.format,
                                                      admitted=True)
            return Response(content=data, media_type=ctype.split(";")[0],
                            headers={"Content-Type": ctype, "Content-Disposition": disposition,
                                     "X-Content-Type-Options": "nosniff",
                                     "Cache-Control": "no-store"})
        return await run_in_threadpool(_run, go, "deliverable export")


__all__ = ["mount", "DeliverBody", "ExportBody", "MAX_BODY_BYTES"]
