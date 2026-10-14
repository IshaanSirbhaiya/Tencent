"""``/audit/*`` (CONTRACTS §3): compliance_officer only; every call is itself ledgered.

* ``GET  /audit/events``                   filter -> event rows        (ledgered ``audit.query``)
* ``GET  /audit/doc/{doc_id}/accessors``   who had a document          (ledgered ``audit.query``)
* ``POST /audit/ask``  {"question"}        NL -> closed filter -> rows (ledgered ``audit.query``)
* ``POST /audit/reveal`` {"req_id","reason"}  the answer text          (ledgered ``audit.reveal``
                                            BEFORE the text is read; a non-empty reason is required)
* ``GET  /audit/verify``                   chain + anchors + external head

Write-ahead and fail-closed: the audit event is appended before the response is returned; an append
failure is a 503 and nothing is returned. Failed calls are ledgered too (Codex sec r1 #9): an invalid
filter, a blank question or reason, an unparseable question, an unknown reveal target or a query
failure appends ``audit.query`` with ``outcome`` ``rejected``/``error`` before the 4xx/5xx (and is a
503 if that append fails). A reveal whose stored text does not hash to the sealed ``answer_sha256``
returns 409 with no text and is ledgered with ``outcome: mismatch`` (#7). Requests the framework
rejects before a handler runs (no/invalid token, a body that is not JSON or exceeds a length cap) are
not ledgered — they never reach the audit code. The one exception is ``/audit/verify``: its report carries
no content (counts and hashes only) and is exactly what an officer needs when the ledger is broken,
so it is returned with ``"ledgered": false`` when the ledger refuses the ``audit.query`` append.

``require_role(role)`` comes from the app (auth.py): it returns a FastAPI dependency (a ``Depends``
or a plain callable) that yields the authenticated principal (an object or mapping with ``sub``) and
answers 401/403 itself.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import params as fa_params
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import AuditQueryEvent, AuditReveal
from .anchor import AnchorSink
from .anchor import verify as verify_with_sink
from .content import AnswerStore, ContentStoreUnavailable, sha256_text
from .ledger import AccessLedger, LedgerUnavailable
from .nl_filter import LlmParser, NlFilterError, parse_audit_question
from .query import AuditFilter, AuditProjection

COMPLIANCE_ROLE = "compliance_officer"
REVEAL_MISMATCH = "content does not match the sealed ledger digest"


class AuditAskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=1000)          # blank -> a ledgered 422 in the handler


class RevealBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    req_id: str = Field(max_length=200)             # empty -> a ledgered 404 (unknown target)
    reason: str = Field(max_length=1000)            # blank -> a ledgered 422 in the handler


def _sub_of(principal: Any) -> str:
    if isinstance(principal, str):
        return principal
    if isinstance(principal, dict):
        sub = principal.get("sub")
    else:                       # the app's DirectoryUser carries the subject as ``id``
        sub = getattr(principal, "sub", None) or getattr(principal, "id", None)
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

    def _append_query(sub: str, filt: dict, count: int) -> None:
        try:
            ledger.append(AuditQueryEvent(sub=sub, filter=filt, result_count=count,
                                          event_time=now()))
        except LedgerUnavailable:
            raise _unavailable() from None

    def _ledger_query(sub: str, filt: dict, count: int) -> None:
        _append_query(sub, {**filt, "outcome": "ok"}, count)

    def _refuse(sub: str, op: str, outcome: str, status: int, error: str,
                info: Optional[dict] = None, detail: Any = None) -> NoReturn:
        """Ledger a failed audit call as ``audit.query`` with ``outcome`` (``rejected`` for a bad
        request, ``error`` for a server-side failure), then answer ``status`` (Codex sec r1 #9).
        When the ledger refuses this append the answer is 503 instead: fail closed."""
        _append_query(sub, {"op": op, "outcome": outcome, **(info or {}), "error": error[:300]}, 0)
        raise HTTPException(status_code=status, detail=error if detail is None else detail)

    def _sync() -> None:
        try:
            projection.sync()
        except LedgerUnavailable:
            raise _unavailable() from None

    @router.get("/events")
    def audit_events(principal=dep,
                     user: Optional[str] = Query(None), container: Optional[str] = Query(None),
                     doc: Optional[str] = Query(None), since: Optional[str] = Query(None),
                     until: Optional[str] = Query(None),
                     decision: Optional[str] = Query(None), kind: Optional[str] = Query(None),
                     limit: Optional[str] = Query(None)) -> dict:
        # every parameter arrives as text and is validated here, so a malformed one is a
        # ledgered rejection rather than a framework 422 that never reaches the ledger
        sub = _sub_of(principal)
        raw = {k: v for k, v in dict(user=user, container=container, doc=doc, since=since,
                                     until=until, decision=decision, kind=kind,
                                     limit=limit).items() if v is not None}
        try:
            f, how = AuditFilter.model_validate(raw).windowed(now())
        except (ValidationError, ValueError) as exc:
            _refuse(sub, "events", "rejected", 422, f"invalid audit filter: {_brief(exc)}",
                    {"params": _clip(raw)})
        _sync()
        try:
            rows = projection.search(f)
        except Exception:                                            # noqa: BLE001
            _refuse(sub, "events", "error", 500, "the audit query failed", {"params": _clip(raw)})
        dumped = f.model_dump(mode="json", exclude_none=True)
        _ledger_query(sub, {"op": "events", **dumped, "window": how}, len(rows))
        return {"filter": dumped, "window": _window(dumped, how), "events": rows}

    @router.get("/doc/{doc_id:path}/accessors")
    def audit_accessors(doc_id: str, principal=dep) -> dict:
        sub = _sub_of(principal)
        if not doc_id or len(doc_id) > 300:
            _refuse(sub, "accessors", "rejected", 422, "invalid doc id", {"doc": doc_id[:300]})
        _sync()
        try:
            res = projection.accessors(doc_id)
        except Exception:                                            # noqa: BLE001
            _refuse(sub, "accessors", "error", 500, "the audit query failed", {"doc": doc_id})
        _ledger_query(sub, {"op": "accessors", "doc": doc_id, "truncated": res["truncated"]},
                      len(res["requests"]))
        return res

    @router.post("/ask")
    def audit_ask(body: AuditAskBody, principal=dep) -> dict:
        sub = _sub_of(principal)
        question = body.question.strip()
        if not question:
            _refuse(sub, "ask", "rejected", 422, "a question is required", {"question": ""})
        try:
            parsed = parse_audit_question(question, now(), llm=llm)
        except NlFilterError as exc:
            _refuse(sub, "ask", "rejected", 422, exc.message,
                    {"question": question, "unparsed": exc.unparsed},
                    detail={"error": exc.message, "unparsed": exc.unparsed})
        except Exception:                                            # noqa: BLE001 — 422, not 500
            msg = "the question could not be parsed into a filter"
            _refuse(sub, "ask", "rejected", 422, msg, {"question": question},
                    detail={"error": msg, "unparsed": {"question": question[:300]}})
        _sync()
        try:
            rows = projection.search(parsed.filter)
        except Exception:                                            # noqa: BLE001
            _refuse(sub, "ask", "error", 500, "the audit query failed", {"question": question})
        dumped = parsed.filter.model_dump(mode="json", exclude_none=True)
        how = "; ".join(parsed.notes) or "as asked"
        _ledger_query(sub, {"op": "ask", "question": question, "parser": parsed.source, **dumped},
                      len(rows))
        return {"filter": dumped, "window": _window(dumped, how), "parser": parsed.source,
                "notes": parsed.notes, "events": rows}

    @router.post("/reveal")
    def audit_reveal(body: RevealBody, principal=dep) -> Any:
        sub = _sub_of(principal)
        reason = body.reason.strip()
        info = {"target_req_id": body.req_id}
        if not reason:
            _refuse(sub, "reveal", "rejected", 422, "a reveal needs a reason", info)
        # the reference and the sealed digest come from the VERIFIED ledger, not the projection
        try:
            evs = list(ledger.events(strict=True))
        except LedgerUnavailable:
            raise _unavailable() from None
        released = None
        for le in evs:
            if le.kind == "answer.released" and le.data.get("req_id") == body.req_id:
                released = le.data
        if released is None or not released.get("answer_ref"):
            _refuse(sub, "reveal", "rejected", 404, "no released answer for that request", info)
        ref, sealed = str(released["answer_ref"]), released.get("answer_sha256")
        try:                                                  # write-ahead: ledger, then read
            ledger.append(AuditReveal(sub=sub, target_req_id=body.req_id, reason=reason,
                                      event_time=now()))
        except LedgerUnavailable:
            raise _unavailable() from None
        try:
            stored = store.get(ref)
        except ContentStoreUnavailable:
            _refuse(sub, "reveal", "error", 503, "the answer content store is unavailable", info)
        if stored is None:
            _refuse(sub, "reveal", "error", 404,
                    "the answer content is missing from the content store", info)
        if (stored.req_id != body.req_id or not isinstance(sealed, str)
                or sha256_text(stored.answer) != sealed):
            # never release text the sealed digest does not vouch for (Codex sec r1 #7)
            _append_query(sub, {"op": "reveal", "outcome": "mismatch", **info,
                                "answer_ref": ref, "error": REVEAL_MISMATCH}, 0)
            return JSONResponse(status_code=409, content={"error": REVEAL_MISMATCH})
        return {"req_id": body.req_id, "answer": stored.answer, "answer_ref": ref,
                "answer_sha256": sealed, "content_matches_ledger": True,
                "revealed_by": sub, "reason": reason}

    @router.get("/verify")
    def audit_verify(principal=dep) -> dict:
        # Deliberate exception to write-ahead (Codex sec r1 #10, SECURITY.md §3): the report holds
        # counts and digests only, and a broken ledger is exactly when the officer needs it.
        sub = _sub_of(principal)
        rep = verify_with_sink(ledger, sink) if sink is not None else ledger.verify()
        out = rep.as_dict()
        # the answer text lives outside the sealed chain: re-hash every stored answer against the
        # digest the ledger sealed for it, so an edit to an answer nobody revealed is visible too
        # (CodeBuddy security review CB5 finding 3)
        content = _verify_content(ledger, store)
        out["content"] = content
        out["ok"] = bool(out.get("ok")) and content["intact"]
        try:
            ledger.append(AuditQueryEvent(sub=sub, filter={"op": "verify", "outcome": "ok",
                                                           "verify_ok": out["ok"]},
                                          result_count=rep.records, event_time=now()))
            out["ledgered"] = True
        except LedgerUnavailable:
            out["ledgered"] = False
        return out

    return router


def _verify_content(ledger: AccessLedger, store: AnswerStore) -> dict:
    """Every ``answer.released`` digest vs the text in the content store. Reports COUNTS only (no
    answer references), so the report discloses nothing beyond integrity even on the documented
    write-ahead exception of /audit/verify (Codex review track 5 #2). A release without a reference,
    a missing answer, a mismatch or an unreadable record is a problem; never a silent skip (#3, #6)."""
    checked = missing = mismatched = unreadable = 0
    try:
        for ev in ledger.events(strict=False):
            if ev.kind != "answer.released":
                continue
            checked += 1
            ref, sealed = ev.data.get("answer_ref"), ev.data.get("answer_sha256")
            if not ref or not isinstance(sealed, str):
                unreadable += 1
                continue
            try:
                stored = store.get(str(ref))
                if stored is None:
                    missing += 1
                elif stored.req_id != ev.data.get("req_id") or sha256_text(stored.answer) != sealed:
                    mismatched += 1
            except ContentStoreUnavailable:
                raise
            except Exception:                                       # noqa: BLE001 — corrupt record
                unreadable += 1
    except (ContentStoreUnavailable, LedgerUnavailable):
        return {"checked": checked, "intact": False, "problems": ["content check unavailable"],
                "problem_count": 1}
    problems = [msg for n, msg in ((missing, "answer(s) missing from the content store"),
                                   (mismatched, "answer(s) whose text does not match the sealed digest"),
                                   (unreadable, "release record(s) or stored answer(s) that cannot be read"))
                if n for msg in [f"{n} {msg}"]]
    total = missing + mismatched + unreadable
    return {"checked": checked, "intact": total == 0, "problems": problems, "problem_count": total}


def _clip(d: dict) -> dict:
    return {k: str(v)[:300] for k, v in d.items()}


def _window(dumped: dict, how: str) -> dict:
    """The time window actually searched (both ends; ``until`` is exclusive)."""
    return {"since": dumped.get("since"), "until": dumped.get("until"), "until_exclusive": True,
            "applied": how}


def _brief(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}"
                         for e in exc.errors()[:5])
    return str(exc)[:200]


__all__ = ["make_audit_router", "COMPLIANCE_ROLE", "AuditAskBody", "RevealBody"]
