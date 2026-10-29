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
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import auth as authmod
from .config import IbConfig
from .connectors.base import SourceConnector, WebhookRejected
from .directory import Directory
from .index import Index
from .ingest import Ingestor
from . import authz, docview, graph
from .knowledge_map import build_map
from .llm import ModelChain
from .models import AskRequest, DirectoryUser, SessionStarted, TraceViewed
from .pipeline import Pipeline

log = logging.getLogger("databridge_ib.app")

ALLOWED_PREFIXES = ("/health", "/auth/", "/ib/", "/webhooks/", "/audit/", "/sim/")
COOKIE = "ib_session"
EMPLOYEE_ROLES = {"employee", "compliance_officer", "admin_it"}
MAX_WEBHOOK_BYTES = 256 * 1024
LOOPBACK = {"127.0.0.1", "::1"}
MAX_REPLAY_AGE = timedelta(days=400)
#: the asker's trace receipt: stage names and the ledger kinds that prove each stage completed
TRACE_STAGES = (("accept", "request.accepted"), ("gate1", "retrieval.decided"),
                ("gate2", "retrieval.decided"), ("seal", "retrieval.decided"),
                ("compose", "answer.released"), ("release", "answer.released"))
STATUS_LEDGER_TTL_S = 15.0
NOT_FOUND = "not found"


class OpenableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_ids: list[str] = Field(max_length=graph.MAX_OPENABLE)


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

    def _replay_time(request: Request, bearer: bool) -> Optional[datetime]:
        """The demo-history seeding switch (scripts/seed_demo_activity.py). Applies only when the
        caller sends ``X-IB-Replay-Key``; then EVERY condition must hold or the call is a 403
        (never silently recorded as live activity): the server has ``IB_REPLAY_KEY`` (>= 24 chars)
        and is not deployed, the call is a bearer-token call from loopback, the key matches, and
        ``X-IB-Replay-At`` is an ISO time within the last 400 days and not in the future."""
        sent = request.headers.get("x-ib-replay-key")
        if sent is None:
            return None
        key = svc.cfg.replay_key
        host = request.client.host if request.client else ""
        if not (key and len(key) >= 24 and not svc.cfg.deployed and bearer and host in LOOPBACK
                and hmac.compare_digest(sent.encode(), key.encode())):
            raise HTTPException(403, "forbidden")
        try:
            at = datetime.fromisoformat(request.headers.get("x-ib-replay-at", ""))
        except ValueError:
            raise HTTPException(422, "invalid replay time") from None
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if not now - MAX_REPLAY_AGE <= at <= now:
            raise HTTPException(422, "invalid replay time")
        return at

    def _employee(user: DirectoryUser) -> None:
        if not (set(user.roles) & EMPLOYEE_ROLES):
            raise HTTPException(403, "forbidden")

    _ledger_ok_cache: dict[str, Any] = {"at": None, "ok": None}
    _ledger_ok_lock = threading.Lock()

    def _ledger_ok() -> Optional[bool]:
        """Whether the access ledger verifies, re-checked at most every 15 s (a verification costs
        O(records); an employee must not be able to trigger one per request)."""
        head = getattr(svc.ledger, "head", None)
        if head is None:
            return None
        with _ledger_ok_lock:
            last = _ledger_ok_cache["at"]
            if last is not None and time.monotonic() - last < STATUS_LEDGER_TTL_S:
                return _ledger_ok_cache["ok"]
            try:
                head()
                ok = True
            except Exception:                                       # noqa: BLE001
                ok = False
            _ledger_ok_cache.update(at=time.monotonic(), ok=ok)
            return ok

    @app.get("/ib/status")
    def status(user: DirectoryUser = Depends(current)):
        """Model and ledger status for any signed-in employee (never on the public /health).
        Model ids and booleans only: never a key, a base URL or any other secret."""
        _employee(user)
        from . import llm as llmmod
        chain = getattr(svc.pipeline, "chain", None)
        configured = llmmod.model_configured()
        return {"models": {"provider": llmmod.provider_label() if configured else None,
                           "chain": llmmod.configured_models(),
                           "active": chain is not None,
                           "active_chain": list(chain.models) if chain is not None else [],
                           "fallback": {"provider": "deterministic", "model": "extractive-v1"}},
                "key_configured": configured,
                "force_offline": os.environ.get("IB_FORCE_OFFLINE") == "1",
                "ledger": {"ok": _ledger_ok()}}

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
    def _ask_kwargs(request: Request) -> dict:
        """Channel label and the replay switch, the same for /ib/ask and /ib/ask/stream."""
        # the MCP label is honoured only on bearer-token calls (the MCP client's transport), never
        # on the browser's cookie session (CodeBuddy security review CB5 finding 5)
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        channel = ("workbuddy-mcp" if bearer and request.headers.get("x-ib-channel") == "workbuddy-mcp"
                   else "ui")
        replay_at = _replay_time(request, bearer)        # None unless the seeding switch applies
        if replay_at is not None:
            return {"channel": "replay", "event_time": replay_at, "replay": True}
        return {"channel": channel}

    @app.post("/ib/ask")
    def ask(req: AskRequest, request: Request, user: DirectoryUser = Depends(current)):
        if not (set(user.roles) & EMPLOYEE_ROLES):
            raise HTTPException(403, "forbidden")
        kwargs = _ask_kwargs(request)
        try:
            resp = svc.pipeline.ask(user, req.question, **kwargs)
        except Exception as exc:                                    # noqa: BLE001
            log.error("ask failed: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        return json.loads(resp.model_dump_json())

    @app.post("/ib/ask/stream")
    def ask_stream(req: AskRequest, request: Request, user: DirectoryUser = Depends(current)):
        """/ib/ask as Server-Sent Events (plan v2 B12, decision D-20). Same identity, roles,
        channel label, replay switch and ledger records as /ib/ask: it runs the same
        ``Pipeline.ask`` with a progress callback. Events, in order:

        * ``stage`` ``{"name": <accepted|gate1|gate2|sealed|composing|grounded|released>, "ok": true}``
          as each real stage completes: names only, never a count, id or reason (rule 4), and the
          same seven names for every successful request;
        * ``answer``: exactly the /ib/ask JSON, sent only after ``released`` (D-08: no answer text
          before grounding and the write-ahead ``answer.released``);
        * ``done`` ``{}``.

        Any failure (including the ledger) ends the stream with ``error``
        ``{"error": "temporarily unavailable"}`` and no ``answer``. Auth and role failures, and a
        refused replay switch, are ordinary 401/403/422 responses before the stream starts. The
        pipeline runs on its own thread, so a client that disconnects does not change what is
        ledgered (as with /ib/ask, the request completes)."""
        if not (set(user.roles) & EMPLOYEE_ROLES):
            raise HTTPException(403, "forbidden")
        kwargs = _ask_kwargs(request)
        events: "queue.Queue[Optional[tuple[str, dict]]]" = queue.Queue()

        def on_stage(name: str) -> None:
            events.put(("stage", {"name": name, "ok": True}))

        def work() -> None:
            try:
                resp = svc.pipeline.ask(user, req.question, on_stage=on_stage, **kwargs)
                events.put(("answer", json.loads(resp.model_dump_json())))
                events.put(("done", {}))
            except Exception as exc:                                # noqa: BLE001
                log.error("ask stream failed: %s", type(exc).__name__)
                events.put(("error", {"error": "temporarily unavailable"}))
            finally:
                events.put(None)

        threading.Thread(target=work, name="ib-ask-stream", daemon=True).start()

        def stream():
            while True:
                item = events.get()
                if item is None:
                    return
                kind, data = item
                payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
                yield f"event: {kind}\ndata: {payload}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform",
                                          "X-Accel-Buffering": "no"})

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
                           "event_time": r.get("event_time"), "channel": r.get("channel"),
                           "synthetic_replay": bool(r.get("synthetic_replay"))} for r in rows]}

    @app.get("/ib/trace/{req_id}")
    def trace(req_id: str, user: DirectoryUser = Depends(current)):
        """The public-safe RECEIPT of one of MY requests (Codex/Opus plan reviews):
        stage names with ok/skipped (from which sealed records exist, never from timings), the
        total latency rounded to 100 ms, the model, and the cited documents I can STILL open
        (Gate 1 + live Gate 2 now; the rest are omitted silently, with no count). Per-request
        ordinals and record hashes, never global ledger seq numbers. Never the released or
        considered sets, Gate-2 verdicts or reason codes (those stay in the compliance journey).
        Another person's request and an unknown id give the same 404. Ledgered (trace.viewed)."""
        _employee(user)
        now = datetime.now(timezone.utc)

        def _record(outcome: str, n: int) -> None:
            svc.ledger.append(TraceViewed(sub=user.id, outcome=outcome, citations_returned=n,
                                          event_time=now))

        try:
            if svc.projection is None or not req_id or len(req_id) > 200:
                _record("not_found", 0)
                raise HTTPException(404, NOT_FOUND)
            svc.projection.sync()
            res = (svc.projection.reconstruct(req_id) if svc.projection.owns(req_id, user.id)
                   else None)
            if res is None or res.get("sub") != user.id:
                _record("not_found", 0)
                raise HTTPException(404, NOT_FOUND)
            cited: list[tuple[str, Any]] = []
            for c in res.get("citations") or []:
                d = c.get("doc_id") if isinstance(c, dict) else None
                if d and d not in [x for x, _ in cited]:
                    cited.append((d, c.get("version")))
            ok, _ = authz.authorize(user, [d for d, _ in cited], index=svc.index,
                                    connectors=svc.connectors,
                                    directory_epoch=getattr(svc.directory, "epoch", 0))
            citations = [{**authz.public_meta(ok[d]), "cited_version": v}
                         for d, v in cited if d in ok]
            kinds = {e.get("kind") for e in res.get("events") or []}
            lat = res.get("latency_ms")
            body = {"req_id": req_id, "question": res.get("question"),
                    "channel": res.get("channel"), "event_time": res.get("event_time"),
                    "synthetic_replay": bool(res.get("synthetic_replay")),
                    "status": res.get("status"), "policy_version": res.get("policy_version"),
                    "model": res.get("model"), "prompt_version": res.get("prompt_version"),
                    "latency_ms_rounded": (int(round(lat / 100.0)) * 100
                                           if isinstance(lat, (int, float)) else None),
                    "stages": [{"name": n, "status": "ok" if k in kinds else "skipped"}
                               for n, k in TRACE_STAGES],
                    "dropped_sentences_count": res.get("dropped_sentences_count"),
                    "citations": citations, "answer_sha256": res.get("answer_sha256"),
                    "records": [{"ordinal": i + 1, "kind": e.get("kind"), "hash": e.get("hash")}
                                for i, e in enumerate(res.get("events") or [])]}
            _record("ok", len(citations))
        except HTTPException:
            raise
        except Exception as exc:                                    # noqa: BLE001
            log.error("trace failed: %s", type(exc).__name__)       # incl. ledger down: fail closed
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        return body

    # -- developer views: graph and document (authz.py rules; ledgered before returning) -------
    @app.get("/ib/graph")
    def knowledge_graph(focus: Optional[str] = Query(None, max_length=300),
                        q: Optional[str] = Query(None, max_length=500),
                        limit: int = Query(graph.DEFAULT_LIMIT, ge=1, le=graph.MAX_LIMIT),
                        user: DirectoryUser = Depends(current)):
        _employee(user)
        try:
            return graph.build_graph(user, index=svc.index, directory=svc.directory,
                                     ledger=svc.ledger, connectors=svc.connectors,
                                     clock=lambda: datetime.now(timezone.utc), focus=focus, q=q,
                                     limit=limit)
        except graph.NotFound:
            raise HTTPException(404, NOT_FOUND) from None
        except Exception as exc:                                    # noqa: BLE001
            log.error("graph failed: %s", type(exc).__name__)       # incl. ledger down: fail closed
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

    @app.post("/ib/graph/openable")
    def graph_openable(body: OpenableBody, user: DirectoryUser = Depends(current)):
        _employee(user)
        if any(len(d) > 300 for d in body.doc_ids):
            raise HTTPException(422, "invalid id")
        try:
            return graph.openable(user, body.doc_ids, index=svc.index, directory=svc.directory,
                                  ledger=svc.ledger, connectors=svc.connectors,
                                  clock=lambda: datetime.now(timezone.utc))
        except Exception as exc:                                    # noqa: BLE001
            log.error("openable failed: %s", type(exc).__name__)    # incl. ledger down
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

    @app.get("/ib/doc/{doc_id}")
    def document(doc_id: str, q: Optional[str] = Query(None, max_length=500),
                 user: DirectoryUser = Depends(current)):
        _employee(user)
        try:
            return docview.view_doc(user, doc_id, index=svc.index, directory=svc.directory,
                                    ledger=svc.ledger, connectors=svc.connectors,
                                    ingest_status=svc.ingestor.status,
                                    clock=lambda: datetime.now(timezone.utc), q=q)
        except docview.NotFound:
            raise HTTPException(404, NOT_FOUND) from None
        except Exception as exc:                                    # noqa: BLE001
            log.error("doc failed: %s", type(exc).__name__)         # incl. ledger down: fail closed
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

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
