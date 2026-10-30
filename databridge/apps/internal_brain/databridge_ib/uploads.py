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
  ``IB_DEPLOYED=1``. Otherwise ``mount_uploads_router`` does not mount the route at all (round 3
  L3), so it answers what an unknown route answers (404, also without a session) and writes
  nothing. A router built directly with ``make_uploads_router`` still refuses every call.
* **Who.** Identity only from the session (the body names no person; an extra field is a refusal).
  An active person with an employee role.
* **Where.** The folder must exist in Drive now. An employee must hold WRITE on it (``writer``,
  ``fileOrganizer``, ``organizer`` or ``owner``, granted to them, to a group they are in, or to the
  company domain), read LIVE from Drive at upload time (``files/{folder}`` +
  ``files/{folder}/permissions``, one attempt each, any source error is a refusal), and checked
  again right before the Drive write (round 4 Codex B9); Drive itself refuses an uploader without
  WRITE. ``admin_it`` may upload into any existing folder.
* **What.** Extension whitelist (``extract_files.EXT_KIND``); request body at most
  ``MAX_BODY_BYTES`` (7 MiB, enforced on ``Content-Length`` AND while streaming, before any JSON
  parsing or base64 decoding); file at most 5 MiB decoded; bytes must match the extension; zip-bomb,
  XML DTD, macro, PDF page and time limits; the file must yield text (``extract_files``). Text files
  (.csv/.md/.txt) are stored as cleaned UTF-8 text (NFC, control characters and HTML tags removed);
  Office/PDF files are stored as the original bytes and cleaned after extraction. The original
  filename is display-only (path parts dropped, sanitised, capped); the platform stores the bytes
  under a generated id, and no name is ever used as a path.
* **Abuse.** At most ``RATE_LIMIT`` attempts per person per minute (in memory), checked FIRST;
  only the first over-limit refusal per person per window is ledgered (round 3 L3).
* **Ledger.** An accepted upload is recorded write-ahead (round 3 L5) as ``ingest.sync`` with
  ``detail.reason = "sim.upload-file"``, ``phase="requested"`` (person, folder, kind, byte length
  and SHA-256 of the stored bytes; never the bytes or the file name) before the Drive call; a
  ledger failure is a ``503`` and nothing is uploaded. ``phase="written"`` adds the doc id after.
* **One refusal.** EVERY refusal (when, who, where, what, rate, malformed body) is the same
  ``404 {"detail": "not found"}``; the reason is written to the access ledger only, as
  ``request.failed`` with ``stage="sim.upload-file"``. No session is the usual 401.
* **Reset.** ``/sim/reset`` reloads the platforms (the uploaded files are gone) and reconciles the
  index; ``reset_uploads`` (a reset hook) then drops any uploaded document still indexed and
  clears the rate-limit window.

``GET /sim/upload-folders`` (D-34) is the upload sheet's folder picker: ``[{id, path}]`` sorted by
path, ONLY the folders the signed-in person may upload into, each checked live with the same
``_where`` the upload runs (folder exists, WRITE for an employee, any folder for ``admin_it``). It
is mounted under exactly the same switches as ``/sim/upload-file``. A folder that cannot be
checked (source error, past the time budget) is left out; a person who may write nowhere, and any
failure to list, get ``[]``: no counts, no reasons, no hidden names (an ancestor folder's name is
part of a path only when the person can open that ancestor now). ``FOLDERS_RATE_LIMIT`` calls per
person per minute (refusal: the one 404, first over-limit ledgered). Ledger: ``graph.viewed`` with
``mode="upload_folders"`` (counts only): an accepted row (``outcome="checking"``, zero counts)
BEFORE ``_folders`` lists Drive or reads a permission, then the decided row (``outcome="ok"``)
before the response (round 5 Codex #7, rule 3). A ledger failure, or no ledger configured, is a
503 with no Drive call.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import extract_files as X
from .sim import EMPLOYEE_ROLES, LedgerUnavailable, _RateLimiter, write_ahead

log = logging.getLogger("databridge_ib.uploads")

STAGE = "sim.upload-file"
FOLDERS_STAGE = "sim.upload-folders"
NOT_FOUND = "not found"
MAX_BODY_BYTES = 7 * 1024 * 1024
MAX_B64_CHARS = (X.MAX_FILE_BYTES * 4) // 3 + 8
MAX_NAME = 120
RATE_LIMIT, RATE_WINDOW_S = 5, 60.0
FOLDERS_RATE_LIMIT = 10                  # folder-list calls per person per minute
MAX_FOLDERS = 200                        # candidate folders checked live per call
FOLDERS_BUDGET_S = 10.0                  # wall-clock budget for one call's live checks
READ_ROLES = frozenset({"reader", "commenter", "writer", "fileOrganizer", "organizer", "owner"})
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


class _Unavailable(Exception):
    """The write-ahead record could not be written: 503, nothing uploaded."""


class _State:
    """Per-app upload state: the rate limiter and the documents uploaded since the last reset."""

    def __init__(self, limiter: _RateLimiter):
        self.limiter = limiter
        self.lock = threading.Lock()
        self.doc_ids: set[str] = set()
        self.over: dict[str, float] = {}         # person -> when their last over-limit row was written

    def note_over_limit(self, sub: str) -> bool:
        """True for the first over-limit refusal of ``sub`` in a rate window (ledger it), False
        for the rest (not ledgered)."""
        now = self.limiter.clock()
        with self.lock:
            last = self.over.get(sub)
            if last is not None and now - last < self.limiter.window_s:
                return False
            self.over[sub] = now
            return True


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
                        folders_rate_limiter: Optional[_RateLimiter] = None,
                        warm: bool = True, clock: Callable[[], float] = time.monotonic) -> APIRouter:
    """``svc`` is the app's ``Services`` (connectors, directory, ledger, cfg); ``current`` is the
    principal dependency (``app.principal_dependency``). ``cfg`` overrides ``svc.cfg`` (tests);
    ``client_factory`` builds the admin client for the Drive platform (default: the configured
    Drive base URL with the admin token). ``folders_rate_limiter`` limits ``GET
    /sim/upload-folders`` (default ``FOLDERS_RATE_LIMIT`` per minute); ``clock`` times its budget."""
    cfg = cfg if cfg is not None else svc.cfg
    state = _State(rate_limiter or _RateLimiter(RATE_LIMIT, RATE_WINDOW_S))
    fstate = _State(folders_rate_limiter or _RateLimiter(FOLDERS_RATE_LIMIT, RATE_WINDOW_S))
    state.folders = fstate                                          # reset_uploads clears it too
    _STATES[id(svc)] = state
    router = APIRouter(prefix="/sim")
    if warm and _enabled(cfg) is None:     # start the extraction worker now, not on the first upload
        threading.Thread(target=X.warm, name="ib-extract-warm", daemon=True).start()

    def _client() -> httpx.Client:
        if client_factory:
            return client_factory()
        return httpx.Client(base_url=cfg.platform_base["drive"], timeout=20.0,
                            headers={"Authorization": f"Bearer {cfg.admin_token}"})

    def _ledger_refusal(user, reason: str, stage: str = STAGE) -> None:
        led = getattr(svc, "ledger", None)
        if led is None:
            return
        from .models import RequestFailed
        try:
            led.append(RequestFailed(sub=getattr(user, "id", "?"), stage=stage, error_code=reason,
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

    def _grants_write(conn, perms: list, account: str, now_user,
                      roles: frozenset = WRITE_ROLES) -> bool:
        """WRITE (or, with ``roles=READ_ROLES``, any access) for this person from the folder's
        LIVE permission list. Any entry the check cannot read with confidence fails the whole
        check closed."""
        allowed = False
        for p in perms:
            if not isinstance(p, dict) or not isinstance(p.get("deleted", False), bool):
                return False
            if p.get("deleted") or p.get("expirationTime") or p.get("role") not in roles:
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

    def _where(user, folder: str, account: str) -> dict:
        """The folder exists in Drive now and (unless admin_it) this person can write to it now.
        Returns the folder's live metadata."""
        conn = (getattr(svc, "connectors", None) or {}).get("drive")
        if conn is None:
            raise _Refused("no_drive_connector")
        try:
            meta = conn._meta(folder, gate2=True)
            if meta is None or meta.get("trashed") or meta.get("mimeType") != FOLDER_MIME:
                raise _Refused("folder_unknown")
            if "admin_it" in set(user.roles):
                return meta
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
        return meta

    def _can_open(user, conn, fid: str, account: str) -> bool:
        """Any access (reader and up) for this person to folder ``fid``, read LIVE; any doubt is
        False. Decides only whether an ancestor's NAME may appear in a path."""
        if "admin_it" in set(user.roles):
            return True
        try:
            perms = conn._permissions(fid, gate2=True)
        except Exception:                                           # noqa: BLE001 — fail closed
            return False
        now_user = svc.directory.get(user.id)
        if perms is None or now_user is None or not now_user.active:
            return False
        return _grants_write(conn, perms, account, now_user, READ_ROLES)

    def _folders(user) -> tuple[list[dict], int]:
        """The folders this person may upload into NOW, as ``[{id, path}]`` sorted by path, and
        how many folders were checked live. Raises ``_Refused`` (a ledger-only reason) when the
        answer is simply ``[]``."""
        reason = _who(user)
        if reason:
            raise _Refused(reason)
        account = (getattr(user, "platform_ids", None) or {}).get("drive")
        if not account:
            raise _Refused("no_platform_account")
        conn = (getattr(svc, "connectors", None) or {}).get("drive")
        if conn is None:
            raise _Refused("no_drive_connector")
        try:      # every folder the service account sees: candidates only, never returned as such
            listed = conn._list(f"mimeType = '{FOLDER_MIME}' and trashed = false", attempts=1)
        except Exception:                                           # noqa: BLE001 — fail closed
            raise _Refused("source_error") from None
        tree = {str(f["id"]): f for f in listed
                if f.get("mimeType") == FOLDER_MIME and _FOLDER.match(str(f.get("id", "")))}
        start, checked = clock(), 0
        ok: dict[str, dict] = {}
        for fid in sorted(tree)[:MAX_FOLDERS]:
            if clock() - start > FOLDERS_BUDGET_S:
                break                                   # not checked in time: not listed
            checked += 1
            try:
                ok[fid] = _where(user, fid, account)    # the upload's own live check
            except _Refused:
                continue
        opens: dict[str, bool] = {}

        def path_of(fid: str, meta: dict) -> str:
            names = [str(meta.get("name") or fid)]
            seen = {fid}
            parent = next(iter(meta.get("parents") or []), None)
            while parent and parent in tree and parent not in seen:
                seen.add(parent)
                if parent not in opens:
                    opens[parent] = parent in ok or _can_open(user, conn, parent, account)
                if not opens[parent]:
                    break                               # a folder they cannot open: no name
                names.insert(0, str(tree[parent].get("name") or parent))
                parent = next(iter(tree[parent].get("parents") or []), None)
            drive = str(meta.get("driveId") or "")
            dname = conn._drive_name(drive) if drive else ""
            # the shared drive's name leads when Drive gives one (drives.get); never a bare id
            return " / ".join([dname, *names] if dname and dname != drive else names)

        out = [{"id": fid, "path": path_of(fid, meta)} for fid, meta in ok.items()]
        out.sort(key=lambda f: (f["path"].casefold(), f["id"]))
        return out, checked

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
        # round 4 Codex B9: extraction can take up to the time budget, so WRITE is checked LIVE
        # again right before the write (the mock Drive also checks the uploader at insert)
        _where(user, body.folder, account)
        # round 3 L5: write-ahead record of the write (never the bytes or the name); a ledger
        # failure is a 503 and nothing is uploaded
        wrote = {"platform": "drive", "container": body.folder, "kind": label,
                 "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        try:
            write_ahead(getattr(svc, "ledger", None), getattr(user, "id", "?"), STAGE,
                        "requested", wrote)
        except Exception as exc:                                    # noqa: BLE001
            log.error("ledger append for an upload failed: %s", type(exc).__name__)
            raise _Unavailable() from None
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
        try:
            write_ahead(getattr(svc, "ledger", None), getattr(user, "id", "?"), STAGE,
                        "written", {**wrote, "doc_id": doc_id})
        except Exception as exc:                                    # noqa: BLE001 — written
            log.warning("ledger append for an upload result failed: %s", type(exc).__name__)
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
        # round 3 L3: the rate limit comes FIRST, and only the first refusal over the limit in a
        # window is ledgered, so no person can append unbounded ledger rows
        if not state.limiter.allow(getattr(user, "id", "?")):
            if state.note_over_limit(getattr(user, "id", "?")):
                await run_in_threadpool(_ledger_refusal, user, "rate_limited")
            return _refusal()
        try:
            reason = _who(user)
            if reason:
                raise _Refused(reason)
            raw = await _read_capped(request)
            if raw is None:
                raise _Refused("body_too_large")
            return await run_in_threadpool(_upload, raw, user)
        except _Unavailable:
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        except _Refused as exc:
            await run_in_threadpool(_ledger_refusal, user, exc.reason)
            return _refusal()
        except Exception as exc:                                    # noqa: BLE001 — fail closed
            log.warning("upload failed: %s", type(exc).__name__)
            await run_in_threadpool(_ledger_refusal, user, "internal_error")
            return _refusal()

    def _ledger_folders(sub: str, n: int, checked: int, outcome: str = "ok") -> None:
        """``outcome="checking"`` (zero counts) is the accepted row, written BEFORE any Drive
        call; ``"ok"`` is the decided row, before the response (round 5 Codex #7, rule 3). No
        ledger is the same as a ledger that is down: it raises, never a quiet return."""
        led = getattr(svc, "ledger", None)
        if led is None:
            raise LedgerUnavailable("no ledger configured")
        from .config import POLICY_VERSION
        from .models import GraphViewed
        led.append(GraphViewed(sub=sub, mode="upload_folders", outcome=outcome, nodes_returned=n,
                               docs_checked=checked, policy_version=POLICY_VERSION,
                               event_time=datetime.now(timezone.utc)))

    @router.get("/upload-folders")
    async def upload_folders(user=Depends(current)):
        """The upload folder picker (D-34): only the folders this person may upload into now."""
        sub = getattr(user, "id", "?")
        if not fstate.limiter.allow(sub):
            if fstate.note_over_limit(sub):
                await run_in_threadpool(_ledger_refusal, user, "rate_limited", FOLDERS_STAGE)
            return _refusal()
        off = _enabled(cfg)
        if off:                                     # a router built directly with the switches off
            await run_in_threadpool(_ledger_refusal, user, off, FOLDERS_STAGE)
            return _refusal()
        try:                                        # write-ahead: BEFORE any Drive call (#7)
            await run_in_threadpool(_ledger_folders, sub, 0, 0, "checking")
        except Exception as exc:                                    # noqa: BLE001
            log.error("ledger append before the upload folder list failed: %s",
                      type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        try:
            out, checked = await run_in_threadpool(_folders, user)
        except _Refused as exc:                     # no role, no account, cannot list: nothing
            await run_in_threadpool(_ledger_refusal, user, exc.reason, FOLDERS_STAGE)
            out, checked = [], 0
        except Exception as exc:                                    # noqa: BLE001 — fail closed
            log.warning("upload folder list failed: %s", type(exc).__name__)
            await run_in_threadpool(_ledger_refusal, user, "internal_error", FOLDERS_STAGE)
            out, checked = [], 0
        try:                                        # write-ahead: before anything is returned
            await run_in_threadpool(_ledger_folders, sub, len(out), checked)
        except Exception as exc:                                    # noqa: BLE001
            log.error("ledger append for the upload folder list failed: %s", type(exc).__name__)
            return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})
        return JSONResponse(content=out)

    router.upload_state = state                                     # tests / reset_uploads
    router.folders_state = fstate
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
    for st in (state, getattr(state, "folders", None)):
        if st is None:
            continue
        with st.limiter._lock:
            st.limiter._hits.clear()
        with st.lock:
            st.over.clear()
    index = getattr(svc, "index", None)
    dropped = 0
    for doc_id in ids:
        if index is not None and index.doc(doc_id) is not None:
            index.delete(doc_id)
            dropped += 1
    return dropped


def mount_uploads_router(app, svc: Any, reset_hooks: Optional[list] = None) -> None:
    """Mount ``POST /sim/upload-file`` and ``GET /sim/upload-folders`` on a built app (``bootstrap.build_app``), with the app's
    own principal dependency. ``reset_hooks`` is ``build_app``'s list of ``/sim/reset`` hooks:
    ``reset_uploads`` is put FIRST, so the uploads are gone before the live tracker rebaselines."""
    if _enabled(svc.cfg) is not None:
        return          # round 3 L3: off (deployed, demo sign-in off or demo features off) = not a route
    from .app import principal_dependency
    app.include_router(make_uploads_router(svc, principal_dependency(lambda: app.state.svc)))
    if reset_hooks is not None:
        reset_hooks.insert(0, lambda: reset_uploads(svc))


__all__ = ["make_uploads_router", "mount_uploads_router", "reset_uploads", "UploadBody",
           "display_name", "demo_features_on", "MAX_BODY_BYTES", "RATE_LIMIT", "WRITE_ROLES",
           "STAGE", "FOLDERS_STAGE", "FOLDERS_RATE_LIMIT"]
