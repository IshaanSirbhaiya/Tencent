"""``POST /admin/drive/files/upload``: an employee's file upload into a Drive folder (admin token).

Body ``{"folder", "name", "mime", "content_b64", "owner"}``:

* ``folder``: an existing, non-trashed Drive folder id; the new file lives in that folder's shared
  drive. It gets NO direct permission entries: everything it can be read by is INHERITED from the
  folder chain and the shared drive, exactly as ``files/{id}/permissions`` computes for every other
  file (a limited-access folder stops the inheritance as usual).
* ``name``: the display name (1..200 characters, no path separators or control characters). It
  is never used as a path: the bytes are kept in memory under a GENERATED file id (``up-<hex>``).
* ``mime``: one of ``UPLOAD_MIMES`` (Word, Excel, PowerPoint, PDF, CSV, Markdown, plain text).
* ``content_b64``: standard base64, at most ``MAX_BYTES`` (5 MiB) decoded. Text types must be UTF-8
  and are stored as text (served by ``alt=media`` like every other text file); Office / PDF files
  are stored as bytes and ``alt=media`` serves the exact bytes back.
* ``owner``: the uploader (an IB user id or a Drive email); shown as ``lastModifyingUser``. Like
  Drive, the insert checks that this ACTIVE user can write to the folder NOW (``writer``,
  ``fileOrganizer``, ``organizer`` or ``owner``, granted to them, a group they are in or their
  domain, directly or inherited down the folder chain and from the shared drive), else ``403
  insufficientFilePermissions`` and nothing changes (round 4 Codex B9). A directory user with the
  ``admin_it`` role stands in for the Workspace admin and may upload into any folder (the Internal
  Brain's own rule).

The upload is recorded in the Drive ``changes`` feed and fires the signed ``content.changed``
webhook for the new file id, like every other admin mutation (``?webhook=0`` applies it silently).
The file is held only in the in-memory world: ``/admin/reset`` reloads ``world.json`` and the file
is gone. Response: ``{"ok", "id", "name", "mimeType", "drive", "folder", "version", "webhook"}``.
"""
from __future__ import annotations

import base64
import binascii
import re
import uuid

from fastapi import APIRouter, Body, Depends, Request

from .admin import _emit, _err, _user
from .common import admin_auth, world

router = APIRouter(dependencies=[Depends(admin_auth)])
WRITE_ROLES = frozenset({"writer", "fileOrganizer", "organizer", "owner"})


def can_write(w, uid: str, folder_id: str) -> bool:
    """``uid`` may add a file to ``folder_id`` now (call with ``w.lock`` held)."""
    u = w.users.get(uid) or {}
    if not w.active(uid):
        return False
    if "admin_it" in (u.get("roles") or []):
        return True
    groups = w.user_groups(uid)
    email = str(u.get("email") or "")
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    for perm, _inh, _src, _kind in w.drive_permission_sources(folder_id):
        if perm.get("role") not in WRITE_ROLES:
            continue
        t, who = perm.get("type"), perm.get("principal")
        if (t == "user" and who == uid) or (t == "group" and who in groups) or \
                (t == "domain" and isinstance(who, str) and domain and who.lower() == domain):
            return True
    return False

MAX_BYTES = 5 * 1024 * 1024
MAX_NAME = 200
UPLOAD_MIMES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/pdf", "text/csv", "text/markdown", "text/plain",
})
_BAD_NAME = re.compile(r"[\\/\x00-\x1f\x7f]")


class MediaBytes(bytes):
    """A binary file's content. The Drive routes treat ``content`` as text in two places: the
    ``size`` field (``content.encode("utf-8")``) and ``alt=media`` (a ``PlainTextResponse``, which
    sends ``bytes`` unchanged). ``encode`` returns the bytes themselves, so ``size`` is the true
    byte count and the download is byte-exact."""

    def encode(self, *_a, **_k) -> bytes:                 # noqa: D401
        return bytes(self)


@router.post("/drive/files/upload")
def drive_upload(request: Request, body: dict = Body(...)):
    w = world(request)
    mime = body.get("mime")
    if mime not in UPLOAD_MIMES:
        raise _err(400, "unsupported mime")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME or _BAD_NAME.search(name):
        raise _err(400, "invalid name")
    raw = body.get("content_b64")
    if not isinstance(raw, str) or len(raw) > (MAX_BYTES * 4) // 3 + 8:
        raise _err(400, "content_b64 is required (at most 5 MiB decoded)")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise _err(400, "content_b64 is not base64") from None
    if not data or len(data) > MAX_BYTES:
        raise _err(400, "content must be 1 byte to 5 MiB")
    if mime.startswith("text/"):
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _err(400, "text content must be UTF-8") from None
    else:
        content = MediaBytes(data)
    with w.lock:
        folder_id = body.get("folder")
        folder = w.folders.get(folder_id) if isinstance(folder_id, str) else None
        if folder is None or folder.get("trashed"):
            raise _err(404, f"unknown folder {folder_id!r}")
        owner = _user(w, "drive", body.get("owner"))
        if not can_write(w, owner, folder_id):
            raise _err(403, "insufficientFilePermissions: the uploader cannot write to this folder")
        fid = f"up-{uuid.uuid4().hex[:20]}"
        now = w.now_iso()
        w.files[fid] = {"id": fid, "name": name.strip(), "mimeType": mime, "folder": folder_id,
                        "drive": folder.get("drive"), "permissions": [], "version": 1,
                        "created": now, "modified": now, "owner": owner, "content": content}
        w.record_drive_change(fid)
        drive_id = folder.get("drive")
    hook = _emit(request, "drive", "content.changed", [fid], user=owner)
    return {"ok": True, "id": fid, "name": name.strip(), "mimeType": mime, "drive": drive_id,
            "folder": folder_id, "version": 1, "webhook": hook}
