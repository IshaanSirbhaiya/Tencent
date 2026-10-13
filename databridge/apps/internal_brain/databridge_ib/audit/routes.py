"""``/audit/*`` (CONTRACTS §3): compliance_officer only; every call is itself ledgered.

* ``GET  /audit/events``                   filter -> event rows        (ledgered ``audit.query``)
* ``GET  /audit/doc/{doc_id}/accessors``   who had a document          (ledgered ``audit.query``)
* ``POST /audit/ask``  {"question"}        NL -> closed filter -> rows (ledgered ``audit.query``)
* ``POST /audit/reveal`` {"req_id","reason"}  the answer text          (ledgered ``audit.reveal``
                                            BEFORE the text is read; a non-empty reason is required)
* ``GET  /audit/verify``                   chain + anchors + external head

Write-ahead and fail-closed: the audit event is appended before the response is returned; an append
failure is a 503 and nothing is returned. The one exception is ``/audit/verify``: its report carries
no content (counts and hashes only) and is exactly what an officer needs when the ledger is broken,
so it is returned with ``"ledgered": false`` when the ledger refuses the ``audit.query`` append.

``require_role(role)`` comes from the app (auth.py): it returns a FastAPI dependency (a ``Depends``
or a plain callable) that yields the authenticated principal (an object or mapping with ``sub``) and
answers 401/403 itself.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import params as fa_params
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import AuditQueryEvent, AuditReveal
from .anchor import AnchorSink
from .anchor import verify as verify_with_sink
from .content import AnswerStore, ContentStoreUnavailable, sha256_text
from .ledger import AccessLedger, LedgerUnavailable
from .nl_filter import LlmParser, parse_audit_question
from .query import AuditFilter, AuditProjection

COMPLIANCE_ROLE = "compliance_officer"


class AuditAskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1000)


class RevealBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    req_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


def _sub_of(principal: Any) -> str:
    if isinstance(principal, str):
        return principal
    if isinstance(principal, dict):
        sub = principal.get("sub")
    else:
        sub = getattr(principal, "sub", None)
    if not sub:
        raise HTTPException(status_code=401, detail="not authenticated")
    return str(sub)


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail={"code": "AUDIT_LEDGER_UNAVAILABLE",
                                                  "message": "the access ledger is unavailable; "
                                                             "nothing was returned"})


def make_audit_router(ledger: AccessLedger, store: AnswerStore, projection: AuditProjection,
                      require_role: Callable[[str], Any], *,
                      sink: Optional[AnchorSink] = None,
                      llm: Optional[LlmParser] = None,
                      clock: Optional[Callable[[], dt.datetime]] = None) -> APIRouter:
    dep = require_role(COMPLIANCE_ROLE)
    if not isinstance(dep, fa_params.Depends):
        dep = Depends(dep)
    now = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    router = APIRouter(prefix="/audit", tags=["audit"])

    def _ledger_query(sub: str, filt: dict, count: int) -> None:
        try:
            ledger.append(AuditQueryEvent(sub=sub, filter=filt, result_count=count,
                                          event_time=now()))
        except LedgerUnavailable:
            raise _unavailable() from None

    def _sync() -> None:
        try:
            projection.sync()
        except LedgerUnavailable:
            raise _unavailable() from None

    @router.get("/events")
    def audit_events(principal=dep,
                     user: Optional[str] = Query(None), container: Optional[str] = Query(None),
                     doc: Optional[str] = Query(None), since: Optional[dt.datetime] = Query(None),
                     until: Optional[dt.datetime] = Query(None),
                     decision: Optional[str] = Query(None), kind: Optional[str] = Query(None),
                     limit: int = Query(100)) -> dict:
        sub = _sub_of(principal)
        try:
            f = AuditFilter.model_validate(
                {k: v for k, v in dict(user=user, container=container, doc=doc, since=since,
                                       until=until, decision=decision, kind=kind,
                                       limit=limit).items() if v is not None}).bounded(now())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid audit filter: "
                                                        f"{_brief(exc)}") from None
        _sync()
        rows = projection.search(f)
        _ledger_query(sub, {"op": "events", **f.model_dump(mode="json", exclude_none=True)},
                      len(rows))
        return {"filter": f.model_dump(mode="json", exclude_none=True), "events": rows}

    @router.get("/doc/{doc_id:path}/accessors")
    def audit_accessors(doc_id: str, principal=dep) -> dict:
        sub = _sub_of(principal)
        if not doc_id or len(doc_id) > 300:
            raise HTTPException(status_code=422, detail="invalid doc id")
        _sync()
        res = projection.accessors(doc_id)
        _ledger_query(sub, {"op": "accessors", "doc": doc_id}, len(res["requests"]))
        return res

    @router.post("/ask")
    def audit_ask(body: AuditAskBody, principal=dep) -> dict:
        sub = _sub_of(principal)
        parsed = parse_audit_question(body.question, now(), llm=llm)
        _sync()
        rows = projection.search(parsed.filter)
        _ledger_query(sub, {"op": "ask", "question": body.question, "parser": parsed.source,
                            **parsed.filter.model_dump(mode="json", exclude_none=True)}, len(rows))
        return {"filter": parsed.filter.model_dump(mode="json", exclude_none=True),
                "parser": parsed.source, "notes": parsed.notes, "events": rows}

    @router.post("/reveal")
    def audit_reveal(body: RevealBody, principal=dep) -> dict:
        sub = _sub_of(principal)
        reason = body.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="a reveal needs a reason")
        _sync()
        rec = projection.reconstruct(body.req_id)
        if rec is None or not rec.get("answer_ref"):
            raise HTTPException(status_code=404, detail="no released answer for that request")
        try:                                                  # write-ahead: ledger, then read
            ledger.append(AuditReveal(sub=sub, target_req_id=body.req_id, reason=reason,
                                      event_time=now()))
        except LedgerUnavailable:
            raise _unavailable() from None
        try:
            stored = store.get(rec["answer_ref"])
        except ContentStoreUnavailable:
            raise HTTPException(status_code=503, detail="the answer content store is "
                                                        "unavailable") from None
        if stored is None:
            raise HTTPException(status_code=404, detail="the answer content is missing from the "
                                                        "content store")
        matches = sha256_text(stored.answer) == rec.get("answer_sha256")
        return {"req_id": body.req_id, "answer": stored.answer, "answer_ref": stored.answer_ref,
                "answer_sha256": rec.get("answer_sha256"), "content_matches_ledger": matches,
                "revealed_by": sub, "reason": reason}

    @router.get("/verify")
    def audit_verify(principal=dep) -> dict:
        sub = _sub_of(principal)
        rep = verify_with_sink(ledger, sink) if sink is not None else ledger.verify()
        out = rep.as_dict()
        try:
            ledger.append(AuditQueryEvent(sub=sub, filter={"op": "verify"},
                                          result_count=rep.records, event_time=now()))
            out["ledgered"] = True
        except LedgerUnavailable:
            out["ledgered"] = False
        return out

    return router


def _brief(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}"
                         for e in exc.errors()[:5])
    return str(exc)[:200]


__all__ = ["make_audit_router", "COMPLIANCE_ROLE", "AuditAskBody", "RevealBody"]
