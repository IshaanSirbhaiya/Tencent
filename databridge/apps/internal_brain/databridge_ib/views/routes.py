"""``POST /ib/views``: a just-in-time view of one of MY released answers.

Always mounted. The order matches ``/ib/deliverables``: a valid session (401), the employee role
(403), the kill switch (the deliverables switch ``IB_DELIVERABLES``; off: the SAME 404 body as
not-found, nothing recorded), then the body (``extra="forbid"``: no identity field), then the
service.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..deliverables import service as dsvc
from ..models import DirectoryUser
from .plan import MAX_INSTRUCTION
from .service import ViewService

log = logging.getLogger("databridge_ib.views")
NOT_FOUND = "not found"


class RewriteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    previous: str = Field("", max_length=2000)


class ViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    req_id: str = Field(min_length=1, max_length=200)
    instruction: str = Field("", max_length=MAX_INSTRUCTION)
    #: ``decide``: page or chat for this answer (``instruction`` = the question as asked);
    #: ``build``: the page, laid out as ``instruction`` asks
    mode: Literal["build", "decide"] = "build"


def mount(app: FastAPI, service: ViewService, current: Callable,
          employee: Callable[[DirectoryUser], None]) -> None:

    def _run(fn: Callable[[], Any]):
        try:
            return fn()
        except dsvc.NotFound:
            raise HTTPException(404, NOT_FOUND) from None
        except dsvc.RateLimited:
            raise HTTPException(429, "too many requests") from None
        except Exception as exc:                                    # noqa: BLE001
            log.error("view failed: %s", type(exc).__name__)        # incl. ledger down
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

    @app.post("/ib/rewrite", openapi_extra={"requestBody": {"required": True, "content": {
        "application/json": {"schema": RewriteBody.model_json_schema()}}}})
    async def rewrite(request: Request, user: DirectoryUser = Depends(current)):
        """A standalone search question from MY message and MY previous question (both my own
        words; no source is read). Before the model call, a counts-only write-ahead row
        (``deliverable.requested`` stage ``rewrite``); the question then asked is ledgered by
        ``/ib/ask`` as usual. The model when connected; otherwise the message unchanged."""
        employee(user)
        if not service.enabled:
            raise HTTPException(404, NOT_FOUND)
        try:
            body = RewriteBody.model_validate_json(await request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False)) from None
        comp = service.composer
        if comp is None:
            return {"question": body.question, "rewritten": False}
        try:            # write-ahead, before the model call: counts only, never the words
            await run_in_threadpool(service.rewrite_requested, user,
                                    len(body.question) + len(body.previous))
        except Exception as exc:                                    # noqa: BLE001
            log.error("rewrite write-ahead failed: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        got, _info, _code = await run_in_threadpool(comp.rewrite, user.id, body.question,
                                                    body.previous)
        if not got or got.strip().lower() == body.question.strip().lower():
            return {"question": body.question, "rewritten": False}
        return {"question": got, "rewritten": True}

    @app.post("/ib/views", openapi_extra={"requestBody": {"required": True, "content": {
        "application/json": {"schema": ViewBody.model_json_schema()}}}})
    async def view(request: Request, user: DirectoryUser = Depends(current)):
        """A one-page view of one of MY released answers, laid out as I asked, filled only from
        the cited sources I can still open. ``deliverable.requested`` before any source read or
        model call; ``deliverable.created`` before the response."""
        employee(user)
        if not service.enabled:
            raise HTTPException(404, NOT_FOUND)
        try:
            body = ViewBody.model_validate_json(await request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False)) from None
        if body.mode == "decide":
            return await run_in_threadpool(
                _run, lambda: service.decide(user, body.req_id, body.instruction))
        return await run_in_threadpool(
            _run, lambda: service.create(user, body.req_id, body.instruction))


__all__ = ["mount", "ViewBody"]
