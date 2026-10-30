"""``POST /sim/upload-file``: a presenter uploads a REAL file (Word, Excel, PowerPoint, PDF, CSV,
Markdown or plain text, including a WhatsApp chat export) into a Google Drive folder, as the
signed-in person, and the Internal Brain learns it the way it learns any Drive change.

The file goes to the Drive platform like an employee's upload (``POST /admin/drive/files/upload``
on the mock platforms); Drive's signed ``content.changed`` webhook fires; the Drive connector
fetches the file and extracts its text with Walnut's own convert readers (``extract_files``); the
ingest path masks it (``mask_doc``) and indexes it with the ACL Drive computes for it (inherited
from the folder). This route NEVER writes the index.

Rules (contract: ``docs/finals/contracts/uploads.md``; ``docs/tencent/SECURITY.md`` §15.12):

* **When.** Demo only: ``IB_DEMO_LOGIN=1`` AND ``IB_DEMO_FEATURES=1``, and never when
  ``IB_DEPLOYED=1``. Otherwise every call is the refusal below, for every person.
* **Who.** Identity only from the session (the body names no person; an extra field is a refusal).
  An active person with an employee role.
* **Where.** The folder must exist in Drive now. An employee must hold WRITE on it (``writer``,
  ``fileOrganizer``, ``organizer`` or ``owner``, granted to them, to a group they are in, or to the
  company domain), read LIVE from Drive at upload time (``files/{folder}`` +
  ``files/{folder}/permissions``, one attempt each, any source error is a refusal). ``admin_it``
  may upload into any existing folder.
* **What.** Extension whitelist (``extract_files.EXT_KIND``); request body at most
  ``MAX_BODY_BYTES`` (7 MiB, enforced on ``Content-Length`` AND while streaming, before any JSON
  parsing or base64 decoding); file at most 5 MiB decoded; bytes must match the extension; zip-bomb,
  XML DTD, macro, PDF page and time limits; the file must yield text (``extract_files``). Text files
  (.csv/.md/.txt) are stored as cleaned UTF-8 text (NFC, control characters and HTML tags removed);
  Office/PDF files are stored as the original bytes and cleaned after extraction. The original
  filename is display-only (path parts dropped, sanitised, capped); the platform stores the bytes
  under a generated id, and no name is ever used as a path.
* **Abuse.** At most ``RATE_LIMIT`` attempts per person per minute (in memory).
* **One refusal.** EVERY refusal (when, who, where, what, rate, malformed body) is the same
  ``404 {"detail": "not found"}``; the reason is written to the access ledger only, as
  ``request.failed`` with ``stage="sim.upload-file"``. No session is the usual 401.
* **Reset.** ``/sim/reset`` reloads the platforms (the uploaded files are gone) and reconciles the
  index; ``reset_uploads`` (a reset hook) then drops any uploaded document still indexed and
  clears the rate-limit window.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import extract_files as X
from .sim import EMPLOYEE_ROLES, _RateLimiter

log = logging.getLogger("databridge_ib.uploads")

STAGE = "sim.upload-file"
NOT_FOUND = "not found"
MAX_BODY_BYTES = 7 * 1024 * 1024
MAX_B64_CHARS = (X.MAX_FILE_BYTES * 4) // 3 + 8
MAX_NAME = 120
RATE_LIMIT, RATE_WINDOW_S = 5, 60.0
WRITE_ROLES = frozenset({"writer", "fileOrganizer", "organizer", "owner"})
FOLDER_MIME = "application/vnd.google-apps.folder"
_FOLDER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_NAME_DROP = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿<>:\"|?*]")
_TAG = re.compile(r"<[^>]{0,2000}>")


class UploadBody(BaseModel):
    """No person field: the uploader is the signed-in person."""
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_b64: str = Field(min_length=1, max_length=MAX_B64_CHARS)
    folder: str = Field(min_length=1, max_length=128, pattern=_FOLDER.pattern)
    title: Optional[str] = Field(None, max_length=MAX_NAME)


class _Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _State:
    """Per-app upload state: the rate limiter and the documents uploaded since the last reset."""

    def __init__(self, limiter: _RateLimiter):
        self.limiter = limiter
        self.lock = threading.Lock()
        self.doc_ids: set[str] = set()


_STATES: dict[int, _State] = {}


def _refusal() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": NOT_FOUND})


def demo_features_on(cfg: Any) -> bool:
    """``IB_DEMO_FEATURES=1`` (read from the config when it carries ``demo_features``, else from
    the environment, the way ``IbConfig`` reads its switches)."""
    v = getattr(cfg, "demo_features", None)
    if v is not None:
        return bool(v)
    return os.environ.get("IB_DEMO_FEATURES", "").strip() == "1"


def display_name(filename: str, title: Optional[str], kind: str) -> str:
    """Display-only name: path parts dropped, NFC, control / reserved characters and HTML tags
    removed, capped at ``MAX_NAME`` with the whitelisted extension kept. Never a filesystem path."""
    ext = next(e for e, k in X.EXT_KIND.items() if k == kind)
    base = re.split(r"[\\/]", _TAG.sub("", filename))[-1]       # tags out first: "</b>" has a "/"
    stem = base[: base.rfind(".")] if "." in base else base
    raw = title if title and title.strip() else stem
    raw = re.sub(r"[\\/]", " ", raw)
    clean = _NAME_DROP.sub("", _TAG.sub("", unicodedata.normalize("NFC", raw)))
    clean = re.sub(r"\s+", " ", clean).strip(" .") or "upload"
    if clean.lower().endswith(ext):
        clean = clean[: -len(ext)].rstrip(" .") or "upload"
    return clean[: MAX_NAME - len(ext)] + ext


def make_uploads_router(svc: Any, current: Callable, *, cfg: Any = None,
                        client_factory: Optional[Callable[[], httpx.Client]] = None,
                        rate_limiter: Optional[_RateLimiter] = None,
                        warm: bool = True) -> APIRouter:
    """``svc`` is the app's ``Services`` (connectors, directory, ledger, cfg); ``current`` is the
    principal dependency (``app.principal_dependency``). ``cfg`` overrides ``svc.cfg`` (tests);
    ``client_factory`` builds the admin client for the Drive platform (default: the configured
    Drive base URL with the admin token)."""
    cfg = cfg if cfg is not None else svc.cfg
    state = _State(rate_limiter or _RateLimiter(RATE_LIMIT, RATE_WINDOW_S))
    _STATES[id(svc)] = state
    router = APIRouter(prefix="/sim")
    if warm and _enabled(cfg) is None:     # start the extraction worker now, not on the first upload
        threading.Thread(target=X.warm, name="ib-extract-warm", daemon=True).start()

    def _client() -> httpx.Client:
        if client_factory:
            return client_factory()
        return httpx.Client(base_url=cfg.platform_base["drive"], timeout=20.0,
                            headers={"Authorization": f"Bearer {cfg.admin_token}"})

    def _ledger_refusal(user, reason: str) -> None:
        led = getattr(svc, "ledger", None)
        if led is None:
            return
        from .models import RequestFailed
        try:
            led.append(RequestFailed(sub=getattr(user, "id", "?"), stage=STAGE, error_code=reason,
                                     event_time=datetime.now(timezone.utc)))
        except Exception as exc:                                    # noqa: BLE001
            log.warning("ledger append for an upload refusal failed: %s", type(exc).__name__)

    def _who(user) -> Optional[str]:
        """None when this person may upload at all, else the (ledger-only) reason."""
        reason = _enabled(cfg)
        if reason:
            return reason
        if not getattr(user, "active", True):
            return "inactive"
        if not set(getattr(user, "roles", ()) or ()) & EMPLOYEE_ROLES:
            return "role"
        return None

    def _grants_write(conn, perms: list, account: str, now_user) -> bool:
        """WRITE for this person from the folder's LIVE permission list. Any entry the check
        cannot read with confidence fails the whole check closed."""
        allowed = False
        for p in perms:
            if not isinstance(p, dict) or not isinstance(p.get("deleted", False), bool):
                return False
            if p.get("deleted") or p.get("expirationTime") or p.get("role") not in WRITE_ROLES:
                continue
            t = p.get("type")
            if t == "user":
                email = p.get("emailAddress")
                if not isinstance(email, str):
                    return False
                allowed |= email.lower() == account.lower()
            elif t == "group":
                email = p.get("emailAddress")
                if not isinstance(email, str):
                    return False
                gid = conn._group(email)
                allowed |= bool(gid) and gid in set(now_user.groups)
            elif t == "domain":
                dom = p.get("domain")
                if not isinstance(dom, str):
                    return False
                dom = dom.lower()
                allowed |= dom == conn.company_domain and now_user.domain == dom
            # "anyone" (link sharing) never grants an upload here
        return allowed

    def _where(user, folder: str, account: str) -> None:
        """The folder exists in Drive now and (unless admin_it) this person can write to it now."""
        conn = (getattr(svc, "connectors", None) or {}).get("drive")
        if conn is None:
            raise _Refused("no_drive_connector")
        try:
            meta = conn._meta(folder, gate2=True)
            if meta is None or meta.get("trashed") or meta.get("mimeType") != FOLDER_MIME:
                raise _Refused("folder_unknown")
            if "admin_it" in set(user.roles):
                return
            perms = conn._permissions(folder, gate2=True)
        except _Refused:
            raise
        except Exception:                                           # noqa: BLE001 — fail closed
            raise _Refused("source_error") from None
        if perms is None:
            raise _Refused("folder_unknown")
        now_user = svc.directory.get(user.id)       # group membership as the directory knows it now
        if now_user is None or not now_user.active \
                or now_user.platform_ids.get("drive") != account:
            raise _Refused("identity_changed")
        if not _grants_write(conn, perms, account, now_user):
            raise _Refused("no_write_permission")

    def _upload(raw: bytes, user) -> dict:
        try:
            body = UploadBody.model_validate_json(raw)
        except ValidationError:
            raise _Refused("bad_body") from None
        kind = X.kind_for_filename(body.filename)
        if kind is None:
            raise _Refused("type")
        try:
            data = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise _Refused("bad_base64") from None
        if not data or len(data) > X.MAX_FILE_BYTES:
            raise _Refused("too_large" if data else "empty")
        account = (getattr(user, "platform_ids", None) or {}).get("drive")
        if not account:
            raise _Refused("no_platform_account")
        _where(user, body.folder, account)
        label = kind
        try:
            X.extract(kind, data)                    # the same checks + readers the connector runs
            if kind in X.TEXT_KINDS:                 # text is stored cleaned (NFC, no tags/ctrl)
                text = X.clean_text(data.decode("utf-8"))
                if not text:
                    raise X.Refused("no_text")
                if kind == "txt" and X.is_whatsapp(text):
                    label = "whatsapp"
                data = text.encode("utf-8")
        except X.Refused as exc:
            raise _Refused(f"file_{exc.reason}") from None
        payload = {"folder": body.folder, "name": display_name(body.filename, body.title, kind),
                   "mime": X.KIND_MIME[kind], "content_b64": base64.b64encode(data).decode("ascii"),
                   "owner": account}
        try:
            with _client() as c:
                r = c.post("/admin/drive/files/upload", json=payload)
        except httpx.HTTPError:
            raise _Refused("source_unreachable") from None
        if r.status_code >= 400:
            raise _Refused(f"source_{r.status_code}")
        try:
            out = r.json()
            fid = str(out["id"])
        except (ValueError, KeyError, TypeError):
            raise _Refused("source_bad_payload") from None
        doc_id = f"drive:{fid}"
        with state.lock:
            state.doc_ids.add(doc_id)
        return {"ok": True, "doc_id": doc_id, "platform": "drive",
                "container": str(out.get("drive") or "my-drive"), "kind": label,
                "at": datetime.now(timezone.utc).isoformat()}

    async def _read_capped(request: Request) -> Optional[bytes]:
        """The body, or None when it is (or claims to be) over ``MAX_BODY_BYTES``; read in
        chunks so an oversized body is never held whole, and never parsed or decoded."""
        cl = request.headers.get("content-length")
        if cl is not None:
            if not cl.strip().isdigit() or int(cl) > MAX_BODY_BYTES:
                return None
        buf = bytearray()
        async for chunk in request.stream():
            buf.extend(chunk)
            if len(buf) > MAX_BODY_BYTES:
                return None
        return bytes(buf)

    @router.post("/upload-file")
    async def upload_file(request: Request, user=Depends(current)):
        try:
            reason = _who(user)
            if reason:
                raise _Refused(reason)
            if not state.limiter.allow(getattr(user, "id", "?")):
                raise _Refused("rate_limited")
            raw = await _read_capped(request)
            if raw is None:
                raise _Refused("body_too_large")
            return await run_in_threadpool(_upload, raw, user)
        except _Refused as exc:
            await run_in_threadpool(_ledger_refusal, user, exc.reason)
            return _refusal()
        except Exception as exc:                                    # noqa: BLE001 — fail closed
            log.warning("upload failed: %s", type(exc).__name__)
            await run_in_threadpool(_ledger_refusal, user, "internal_error")
            return _refusal()

    router.upload_state = state                                     # tests / reset_uploads
    return router


def _enabled(cfg: Any) -> Optional[str]:
    """None when uploads are switched on, else the (ledger-only) reason."""
    if bool(getattr(cfg, "deployed", False)):
        return "deployed"
    if not bool(getattr(cfg, "demo_login", False)):
        return "demo_login_off"
    if not demo_features_on(cfg):
        return "demo_features_off"
    return None


def reset_uploads(svc: Any) -> int:
    """After ``/sim/reset``: drop every document uploaded since the last reset that is still in
    the index (the platform reset already removed the files; reconciliation normally removed the
    rows too), and clear the upload rate-limit window. Returns how many rows were dropped."""
    state = _STATES.get(id(svc))
    if state is None:
        return 0
    with state.lock:
        ids, state.doc_ids = sorted(state.doc_ids), set()
    with state.limiter._lock:
        state.limiter._hits.clear()
    index = getattr(svc, "index", None)
    dropped = 0
    for doc_id in ids:
        if index is not None and index.doc(doc_id) is not None:
            index.delete(doc_id)
            dropped += 1
    return dropped


def mount_uploads_router(app, svc: Any, reset_hooks: Optional[list] = None) -> None:
    """Mount ``POST /sim/upload-file`` on a built app (``bootstrap.build_app``), with the app's
    own principal dependency. ``reset_hooks`` is ``build_app``'s list of ``/sim/reset`` hooks:
    ``reset_uploads`` is put FIRST, so the uploads are gone before the live tracker rebaselines."""
    from .app import principal_dependency
    app.include_router(make_uploads_router(svc, principal_dependency(lambda: app.state.svc)))
    if reset_hooks is not None:
        reset_hooks.insert(0, lambda: reset_uploads(svc))


__all__ = ["make_uploads_router", "mount_uploads_router", "reset_uploads", "UploadBody",
           "display_name", "demo_features_on", "MAX_BODY_BYTES", "RATE_LIMIT", "WRITE_ROLES",
           "STAGE"]
