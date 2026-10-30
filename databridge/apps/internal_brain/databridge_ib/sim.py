"""Demo simulator routes (``/sim/*``) — proxy a FIXED set of platform admin actions to the mock
platforms so the S2/S4 demo moments can be triggered from the UI.

The actions are a closed list: the simulator cannot be used to issue arbitrary admin calls, and it
never writes the IB index directly — changes reach the Internal Brain only the way real changes
do (the platform's signed webhook, or a sync that re-reads the source).

Reset is the one action whose platform change sends no webhook (the mock reloads its world
silently), and the incremental poll compares the platform with its own cursor, which still holds
the pre-reset state, so it would never notice (a revoked member stayed revoked). ``/sim/reset``
therefore runs a FULL reconciliation (``Ingestor.reconcile``) right after the platform reset: every
document, ACL and membership is re-read from the source and the index is repaired to match (a note
added with ``/sim/add-document`` is gone from the platform after the reset, so it leaves the index).

**Demo-proof features (D-24).** ``/sim/add-document``, ``/sim/check-absent``,
``/sim/engine-refresh`` and ``/sim/engine-status`` are mounted ONLY when
``IbConfig.demo_features_on`` (``IB_DEMO_FEATURES=1`` and not ``IB_DEPLOYED=1``). Off, they are
not routes at all, so each answers exactly what an unknown route answers. On, every rule below
still holds.

``/sim/add-document`` ("watch the brain grow") posts a NEW note AS the signed-in person (identity
only from the token; the body names no author):

* **Who.** Refused on a deployment (``IB_DEPLOYED=1``) for everyone, and for everyone while demo
  sign-in is off (``IB_DEMO_LOGIN`` unset), ``admin_it`` included. Any employee may post to Slack.
* **Write permission, checked live at the source.** Slack: the poster must be a CURRENT member of
  the channel (``conversations.info`` + ``conversations.members`` through the Slack connector, at
  post time; any source error is a refusal) AND the channel must already be in their Gate-1
  readable set. Confluence: the mock platforms model no space or page WRITE permission, so a
  Confluence page may be posted by ``admin_it`` only (into a space they can read); employees get
  Slack only.
* **One refusal.** Every refusal (role, deployment, demo sign-in off, unknown container, not a
  member, no platform account, the source unreachable) is the same ``404 {"detail": "not
  found"}``, so the route confirms nothing about containers the person cannot see. Refusals are
  ledgered as ``request.failed`` (``stage="sim.add-document"``; the reason is ledger-only, never
  returned). Too many posts (5 per person per minute, in memory) is ``429 {"detail": "too many
  requests"}`` and is ledgered the same way.
* **Content.** At most 2,000 characters (title 120); HTML tags are stripped before posting; card
  numbers, NRIC and IBANs are masked at ingest like any other document (``mask_doc``).

``/sim/engine-refresh`` (start) and ``/sim/engine-status`` (read) rebuild the Walnut engine
snapshot on ``/dev/brain`` in the background (``engine_refresh.py``): ``admin_it`` or the
snapshot's own person (jdoe), demo sign-in on, never deployed; one job at a time and at most one
start per 2 minutes (``429``). Both are POSTs so the web proxy's existing ``sim/<name>`` rule
covers them.

``/sim/check-absent`` ("not in the brain yet") counts the documents in the WHOLE index (every
document, ignoring ACLs) whose indexed (masked) title or text contains a phrase, exact and
case-insensitive. Because it looks past permissions it is ``admin_it`` ONLY, demo sign-in on, never
on a deployment, 10 per person per minute; it returns only the count and the index size, never a
title or an id. Each check is ledgered write-ahead as ``audit.query`` (``filter.op =
"sim.check-absent"``, the phrase's length and SHA-256, not the phrase; ``result_count`` = the
count; a ledger failure is a 503); refusals are ``request.failed`` (``stage="sim.check-absent"``)
and the same 404.

Ledger: a post is recorded write-ahead (round 3 L5) as ``ingest.sync`` with ``detail.reason =
"sim.add-document"``, ``phase="requested"`` (the person, platform, container, kind, the text's
byte length and SHA-256; never the text or the title) after every check and before the source
call; a ledger failure, or no ledger configured at all (round 4 Codex B8), is a ``503`` and
nothing is posted. After the source call a second record
(``phase="written"``) adds the doc id (its failure is logged; the post already happened). The
note reaches the index through
the mock platform's signed ``content.changed`` webhook, applied by ``Ingestor.apply_event`` (which
ledgers ACL and membership changes only); every later READ of it (``/ib/graph``, ``/ib/graph/changes``,
``/ib/doc``, an answer) is ledgered by that read path.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger("databridge_ib.sim")

#: the same set as ``app.EMPLOYEE_ROLES`` (kept here so the simulator does not import the app module)
EMPLOYEE_ROLES = frozenset({"employee", "compliance_officer", "admin_it"})
NOT_FOUND = "not found"
MAX_TEXT, MAX_TITLE = 2000, 120
RATE_LIMIT, RATE_WINDOW_S = 5, 60.0
CHECK_RATE_LIMIT = 10
_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_TAG = re.compile(r"<[^>]{0,2000}>")


def _strip_html(v: str) -> str:
    return _TAG.sub("", v)


class AddDocumentBody(BaseModel):
    """A new note. No author field: the author is the signed-in person."""
    model_config = ConfigDict(extra="forbid")
    platform: Literal["slack", "confluence"]
    target: str = Field(min_length=1, max_length=64)
    title: Optional[str] = Field(None, max_length=MAX_TITLE)
    text: str = Field(min_length=1, max_length=MAX_TEXT)

    @field_validator("target")
    @classmethod
    def _target(cls, v: str) -> str:
        if not _TARGET.match(v):
            raise ValueError("invalid target")
        return v

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = _strip_html(v).strip()
        if not v:
            raise ValueError("text is required")
        return v

    @field_validator("title")
    @classmethod
    def _title(cls, v: Optional[str]) -> Optional[str]:
        return _strip_html(v).strip() or None if isinstance(v, str) else v

    @model_validator(mode="after")
    def _confluence_title(self) -> "AddDocumentBody":
        if self.platform == "confluence" and not self.title:
            raise ValueError("title is required for a Confluence page")
        return self


class CheckAbsentBody(BaseModel):
    """A phrase to count in the whole index (operators only)."""
    model_config = ConfigDict(extra="forbid")
    phrase: str = Field(min_length=3, max_length=80)

    @field_validator("phrase")
    @classmethod
    def _phrase(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("phrase must be 3 to 80 characters")
        return v


def _storage_html(text: str) -> str:
    """Plain text -> Confluence storage format (escaped; one paragraph per blank-line block)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return "".join("<p>" + html.escape(b).replace("\n", "<br/>") + "</p>" for b in blocks)


class _RateLimiter:
    """At most ``n`` accepted posts per person per ``window_s`` (in memory, per process)."""

    def __init__(self, n: int = RATE_LIMIT, window_s: float = RATE_WINDOW_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.n, self.window_s, self.clock = n, window_s, clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(now)
            return True


def _manifest(world_path: Path) -> dict:
    try:
        return json.loads((Path(world_path).parent / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class LedgerUnavailable(RuntimeError):
    """No ledger is configured, so a write-ahead record cannot be made (round 4 Codex B8)."""


def write_ahead(led: Any, sub: str, stage: str, phase: str, detail: dict) -> None:
    """Record a write to a source made on ``sub``'s behalf (``/sim/add-document``,
    ``/sim/upload-file``; round 3 L5) as ``ingest.sync`` with ``detail.reason = stage``,
    ``detail.phase`` (``requested`` before the source call, ``written`` with the doc id after) and
    ``detail.by = sub``. Never the text, title or file name. No ledger configured is the same as a
    ledger that is down (round 4 Codex B8): ``LedgerUnavailable`` is raised, never a quiet return,
    so the write cannot go ahead unaudited. Any ledger error propagates (the caller answers 503
    before the write)."""
    if led is None:
        raise LedgerUnavailable("no ledger configured")
    from .models import SourceChangeEvent
    led.append(SourceChangeEvent(kind="ingest.sync", platform=detail.get("platform"),
                                 event_time=datetime.now(timezone.utc),
                                 detail={"reason": stage, "phase": phase, "by": sub, **detail}))


def make_sim_router(cfg, require_role: Callable[[str], Callable], *,
                    client_factory: Callable[[], httpx.Client] | None = None,
                    ingestor=None, current: Optional[Callable] = None, ledger: Any = None,
                    rate_limiter: Optional[_RateLimiter] = None,
                    on_reset: Optional[Callable[[], None]] = None,
                    engine: Any = None) -> APIRouter:
    """``current`` is the principal dependency (``app.principal_dependency``); without it, the
    demo-proof routes are ``admin_it`` only. ``ledger`` (default: ``ingestor.ledger``) records
    their refusals. ``on_reset`` runs after ``/sim/reset``'s reconciliation (the live change
    tracker's rebaseline, ``live.ChangeTracker.rebaseline``). ``engine`` is the
    ``engine_refresh.EngineRefresher`` (default: one built from ``cfg``)."""
    router = APIRouter(prefix="/sim")
    admin = require_role("admin_it")
    limiter = rate_limiter or _RateLimiter()
    demo_on = bool(getattr(cfg, "demo_features_on", False))

    def _client() -> httpx.Client:
        if client_factory:
            return client_factory()
        return httpx.Client(base_url=cfg.platform_base["confluence"], timeout=10,
                            headers={"Authorization": f"Bearer {cfg.admin_token}"})

    def _call(path: str, body: dict) -> dict:
        with _client() as c:
            r = c.post(path, json=body)
        if r.status_code >= 400:
            raise HTTPException(502, f"simulator action failed ({r.status_code})")
        return {"ok": True, "action": path, "at": datetime.now(timezone.utc).isoformat()}

    def _find(kind: str) -> dict:
        m = _manifest(cfg.world_path).get("demo_actions", {})
        if kind not in m:
            raise HTTPException(409, f"demo action '{kind}' is not defined in the world manifest")
        return m[kind]

    @router.post("/runbook-update")
    def runbook_update(_u=Depends(admin)):
        a = _find("runbook_update")              # {"page_id": ..., "body": ...}
        return _call(f"/admin/confluence/pages/{a['page_id']}/edit", {"body": a["body"]})

    @router.post("/revoke-channel")
    def revoke_channel(_u=Depends(admin)):
        a = _find("revoke_channel")              # {"channel": ..., "user": ...}
        return _call(f"/admin/slack/channels/{a['channel']}/members/remove", {"user": a["user"]})

    @router.post("/restrict-page")
    def restrict_page(_u=Depends(admin)):
        a = _find("restrict_page")               # {"page_id": ..., "users": [...], "groups": [...]}
        return _call(f"/admin/confluence/pages/{a['page_id']}/restrict",
                     {"users": a.get("users", []), "groups": a.get("groups", [])})

    @router.post("/reset")
    def reset(_u=Depends(admin)):
        out = _call("/admin/reset", {})
        if ingestor is not None:
            rec = ingestor.reconcile("sim.reset")
            out["reconciled"] = [r["platform"] for r in rec["platforms"]]
            if rec["failed"]:
                # the platforms are reset but the index could not be repaired for these: say so
                # (the poller's next full reconciliation retries them)
                out["ok"] = False
                out["reconcile_failed"] = rec["failed"]
        if on_reset is not None:
            try:
                on_reset()
            except Exception as exc:                                # noqa: BLE001
                log.warning("reset hook failed: %s", type(exc).__name__)
        return out

    # -- "watch the brain grow": a new note, posted as the signed-in person --------------------
    class _Refused(Exception):
        def __init__(self, reason: str):
            self.reason = reason

    def _ledger_refusal(user, reason: str, stage: str = "sim.add-document") -> None:
        led = ledger if ledger is not None else getattr(ingestor, "ledger", None)
        if led is None:
            return
        from .models import RequestFailed
        try:
            led.append(RequestFailed(sub=getattr(user, "id", "?"), stage=stage,
                                     error_code=reason, event_time=datetime.now(timezone.utc)))
        except Exception as exc:                                    # noqa: BLE001
            log.warning("ledger append for an add-document refusal failed: %s",
                        type(exc).__name__)

    class _Unavailable(Exception):
        """The write-ahead record could not be written: 503, nothing posted."""

    def _ledger_write(user, stage: str, phase: str, detail: dict) -> None:
        """A write to a source on the person's behalf (round 3 L5), as ``ingest.sync`` with
        ``detail.reason = stage``; raises when the ledger fails (the caller decides)."""
        write_ahead(ledger if ledger is not None else getattr(ingestor, "ledger", None),
                    getattr(user, "id", "?"), stage, phase, detail)

    def _may_add(user, platform: str) -> Optional[str]:
        """None when allowed, else the (ledger-only) reason."""
        if bool(getattr(cfg, "deployed", False)):
            return "deployed"
        if not getattr(user, "active", True):
            return "inactive"
        if not bool(getattr(cfg, "demo_login", False)):
            return "demo_login_off"              # IB_DEMO_LOGIN only, for everyone (D-24)
        roles = set(getattr(user, "roles", ()) or ())
        if not roles & EMPLOYEE_ROLES:
            return "role"
        if "admin_it" not in roles and platform != "slack":
            return "confluence_admin_only"       # the source models no write permission to check
        return None

    def _gate1_container(user, platform: str, container: str) -> bool:
        """Gate 1 over the container: at least one of its documents is in the person's readable
        set. Unknown container or no index to ask: False (fail closed)."""
        if ingestor is None:
            return False
        index = ingestor.index
        directory = getattr(ingestor, "directory", None)
        readable = index.readable(user, getattr(directory, "epoch", 0))
        return any(d in readable for d in index.docs_in_container(platform, container))

    def _slack_member_now(account: str, channel: str) -> bool:
        """Live at the source, at post time: the channel exists and ``account`` is a member."""
        conn = (getattr(ingestor, "connectors", None) or {}).get("slack")
        if conn is None:
            return False
        try:
            if conn._channel(channel, gate2=True) is None:
                return False
            return account in set(conn._members(channel, gate2=True))
        except Exception:                                           # noqa: BLE001 — fail closed
            return False

    def _add(body: AddDocumentBody, user) -> dict:
        reason = _may_add(user, body.platform)
        if reason:
            raise _Refused(reason)
        if not _gate1_container(user, body.platform, body.target):
            raise _Refused("container_not_readable")    # unknown ≡ not readable (one answer)
        account = (getattr(user, "platform_ids", None) or {}).get(body.platform)
        if not account:
            raise _Refused("no_platform_account")
        if body.platform == "slack":
            if not _slack_member_now(account, body.target):
                raise _Refused("not_a_member_now")
            path, payload = "/admin/slack/threads", {"channel": body.target, "user": account,
                                                     "text": body.text}
        else:
            path, payload = "/admin/confluence/pages", {"space": body.target,
                                                        "title": body.title,
                                                        "body": _storage_html(body.text),
                                                        "author": account}
        if not limiter.allow(getattr(user, "id", "?")):
            raise _Refused("rate_limited")
        # round 3 L5: the write is recorded write-ahead (who, where, what kind, the text's length
        # and SHA-256, never the text or title); a ledger failure is a 503 and nothing is posted
        wrote = {"platform": body.platform, "container": body.target,
                 "kind": "thread" if body.platform == "slack" else "page",
                 "bytes": len(body.text.encode("utf-8")),
                 "sha256": hashlib.sha256(body.text.encode("utf-8")).hexdigest()}
        try:
            _ledger_write(user, "sim.add-document", "requested", wrote)
        except Exception as exc:                                    # noqa: BLE001
            log.error("ledger append for add-document failed: %s", type(exc).__name__)
            raise _Unavailable() from None
        try:
            with _client() as c:
                r = c.post(path, json=payload)
        except httpx.HTTPError:
            raise _Refused("source_unreachable") from None
        if r.status_code >= 400:
            raise _Refused(f"source_{r.status_code}")
        data = r.json()
        if body.platform == "slack":
            doc_id = f"slack:{data['channel']}:{data['thread_ts']}"
        else:
            doc_id = f"confluence:{data['id']}"
        try:
            _ledger_write(user, "sim.add-document", "written", {**wrote, "doc_id": doc_id})
        except Exception as exc:                                    # noqa: BLE001 — written
            log.warning("ledger append for add-document result failed: %s", type(exc).__name__)
        return {"ok": True, "doc_id": doc_id, "platform": body.platform, "container": body.target,
                "at": datetime.now(timezone.utc).isoformat()}

    def add_document(body: AddDocumentBody, user):
        try:
            return _add(body, user)
        except _Unavailable:
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        except _Refused as exc:
            _ledger_refusal(user, exc.reason)
            if exc.reason == "rate_limited":
                return JSONResponse(status_code=429, content={"detail": "too many requests"})
            return JSONResponse(status_code=404, content={"detail": NOT_FOUND})

    principal = current if current is not None else admin   # no principal dependency: admin only
    if demo_on:
        @router.post("/add-document")
        def add_document_route(body: AddDocumentBody, user=Depends(principal)):
            return add_document(body, user)

    # -- "not in the brain yet": an operator's count over the WHOLE index ----------------------
    def check_absent(body: CheckAbsentBody, user):
        reason = None
        if bool(getattr(cfg, "deployed", False)):
            reason = "deployed"
        elif "admin_it" not in set(getattr(user, "roles", ()) or ()):
            reason = "role"
        elif not bool(getattr(cfg, "demo_login", False)):
            reason = "demo_login_off"
        elif ingestor is None:
            reason = "no_index"
        elif not check_limiter.allow(getattr(user, "id", "?")):
            reason = "rate_limited"
        if reason:
            _ledger_refusal(user, reason, stage="sim.check-absent")
            if reason == "rate_limited":
                return JSONResponse(status_code=429, content={"detail": "too many requests"})
            return JSONResponse(status_code=404, content={"detail": NOT_FOUND})
        index = ingestor.index
        needle = body.phrase.casefold()
        ids = index.doc_ids()
        hits = 0
        for d in ids:
            row = index.doc(d)
            if row is None:
                continue
            hay = f"{row.get('title') or ''}\n{row.get('text') or ''}".casefold()
            if needle in hay:
                hits += 1
        now = datetime.now(timezone.utc)
        led = ledger if ledger is not None else getattr(ingestor, "ledger", None)
        if led is not None:                          # write-ahead: a failure is a 503
            from .models import AuditQueryEvent
            try:
                led.append(AuditQueryEvent(
                    sub=user.id, result_count=hits, event_time=now,
                    filter={"op": "sim.check-absent", "phrase_chars": len(body.phrase),
                            "phrase_sha256": hashlib.sha256(
                                body.phrase.encode("utf-8")).hexdigest()}))
            except Exception as exc:                                # noqa: BLE001
                log.error("ledger append for check-absent failed: %s", type(exc).__name__)
                return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        return {"phrase": body.phrase, "documents_containing": hits,
                "checked_at": now.isoformat(), "index_size": len(ids)}

    check_limiter = _RateLimiter(n=CHECK_RATE_LIMIT)
    if demo_on:
        @router.post("/check-absent")
        def check_absent_route(body: CheckAbsentBody, user=Depends(principal)):
            return check_absent(body, user)

    # -- engine refresh: rebuild the Walnut engine snapshot (/dev/brain) -----------------------
    if demo_on:
        from .engine_refresh import SNAPSHOT_PERSONA, EngineRefresher
        from .models import SourceChangeEvent
        refresher = engine if engine is not None else EngineRefresher(cfg)
        router.engine = refresher                                   # type: ignore[attr-defined]

        def _engine_refusal(user) -> Optional[str]:
            if bool(getattr(cfg, "deployed", False)):
                return "deployed"
            if not getattr(user, "active", True):
                return "inactive"
            if not bool(getattr(cfg, "demo_login", False)):
                return "demo_login_off"
            roles = set(getattr(user, "roles", ()) or ())
            if "admin_it" not in roles and getattr(user, "id", None) != SNAPSHOT_PERSONA:
                return "role"
            return None

        def _ledger_engine(sub: str, phase: str) -> None:
            led = ledger if ledger is not None else getattr(ingestor, "ledger", None)
            if led is not None:
                led.append(SourceChangeEvent(kind="ingest.sync", event_time=datetime.now(timezone.utc),
                                             detail={"reason": "engine.refresh", "phase": phase,
                                                     "by": sub}))

        def _end(sub: str):
            def hook(outcome: str) -> None:
                try:
                    _ledger_engine(sub, outcome)
                except Exception as exc:                            # noqa: BLE001
                    log.warning("ledger append for an engine refresh end failed: %s",
                                type(exc).__name__)
            return hook

        @router.post("/engine-status")
        def engine_status(user=Depends(principal)):
            reason = _engine_refusal(user)
            if reason:
                _ledger_refusal(user, reason, stage="sim.engine-status")
                return JSONResponse(status_code=404, content={"detail": NOT_FOUND})
            return refresher.status()

        @router.post("/engine-refresh")
        def engine_refresh(user=Depends(principal)):
            reason = _engine_refusal(user)
            if reason:
                _ledger_refusal(user, reason, stage="sim.engine-refresh")
                return JSONResponse(status_code=404, content={"detail": NOT_FOUND})
            # round 3 L4: claim the job slot atomically, THEN record, THEN run, so two
            # concurrent starts write one "started" record for the one build
            verdict = refresher.reserve()
            if verdict == "running":
                return refresher.status()                           # single flight: same job
            if verdict == "rate_limited":
                _ledger_refusal(user, "rate_limited", stage="sim.engine-refresh")
                return JSONResponse(status_code=429, content={"detail": "too many requests"})
            try:                                                    # write-ahead
                _ledger_engine(user.id, "started")
            except Exception as exc:                                # noqa: BLE001
                refresher.release()
                log.error("ledger append for an engine refresh failed: %s", type(exc).__name__)
                return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
            refresher.run(on_end=_end(user.id))
            return refresher.status()

    return router
