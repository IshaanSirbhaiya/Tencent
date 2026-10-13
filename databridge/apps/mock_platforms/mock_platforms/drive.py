"""Google Drive v3 subset, mounted at ``/drive/v3`` (CONTRACTS §2).

* ``files`` lists files AND folders (folders carry ``application/vnd.google-apps.folder``) with
  ``nextPageToken`` pagination and a ``q`` subset: ``'<id>' in parents``, ``trashed = false``,
  ``mimeType =|!= '...'``, ``name = '...'``, ``name contains '...'``,
  ``modifiedTime|createdTime <op> '<iso>'`` joined by ``and``. Unknown clauses are a 400.
* ``files/{id}/permissions`` returns direct AND inherited entries, one permission per principal,
  each with ``permissionDetails:[{permissionType, role, inherited, inheritedFrom}]``. Inheritance
  walks up the folder chain and stops at a folder with ``inheritedPermissionsDisabled: true``;
  shared-drive membership is inherited from the drive (``permissionType: "member"``).
* ``changes`` is fed by every admin mutation on Drive.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from .common import PlatformError, page_size, paginate, platform_auth, world
from .state import FOLDER_MIME, World

P = "drive"
router = APIRouter(dependencies=[Depends(platform_auth(P))])
ROLE_RANK = {"reader": 1, "commenter": 2, "writer": 3, "fileOrganizer": 4, "organizer": 5,
             "owner": 6}


def item_obj(w: World, item_id: str) -> dict[str, Any]:
    folder = w.is_folder(item_id)
    x = w.drive_item(item_id) or {}
    parent = w.drive_parent(item_id) or x.get("drive")
    out: dict[str, Any] = {
        "kind": "drive#file", "id": item_id, "name": x.get("name", ""),
        "mimeType": FOLDER_MIME if folder else x.get("mimeType", "application/octet-stream"),
        "parents": [parent] if parent else [],
        "driveId": x.get("drive"), "teamDriveId": x.get("drive"),
        "version": str(x.get("version", 1)),
        "createdTime": x.get("created"), "modifiedTime": x.get("modified") or x.get("created"),
        "webViewLink": (f"https://drive.google.com/drive/folders/{item_id}" if folder
                        else f"https://drive.google.com/file/d/{item_id}/view"),
        "inheritedPermissionsDisabled": bool(x.get("inherited_permissions_disabled", False)),
        "trashed": bool(x.get("trashed", False)),
    }
    if not folder:
        out["size"] = str(len((x.get("content") or "").encode("utf-8")))
        if x.get("owner"):
            out["lastModifyingUser"] = {"emailAddress": w.native(P, x["owner"])}
    return out


def _item(w: World, item_id: str) -> dict:
    x = w.drive_item(item_id)
    if x is None or x.get("trashed"):
        raise PlatformError(P, 404, "notFound", f"File not found: {item_id}.")
    return x


def _dt(w: World, v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise PlatformError(P, 400, "invalidQuery", f"Invalid date {v!r}")
    return dt if dt.tzinfo else dt.replace(tzinfo=w.tz)


_SPLIT_AND = re.compile(r"\s+and\s+(?=(?:[^']*'[^']*')*[^']*$)", re.I)
_Q = r"'((?:[^'\\]|\\.)*)'"


def _unq(s: str) -> str:
    return s.replace("\\'", "'").replace("\\\\", "\\")


def run_q(w: World, q: Optional[str], ids: list[str]) -> list[str]:
    preds = []
    for clause in (_SPLIT_AND.split(q.strip()) if q and q.strip() else []):
        c = clause.strip()
        if (m := re.fullmatch(_Q + r"\s+in\s+parents", c, re.I)):
            pid = _unq(m.group(1))
            preds.append(lambda i, pid=pid: (w.drive_parent(i) or
                                             (w.drive_item(i) or {}).get("drive")) == pid)
        elif (m := re.fullmatch(r"trashed\s*=\s*(true|false)", c, re.I)):
            want = m.group(1).lower() == "true"
            preds.append(lambda i, want=want: bool((w.drive_item(i) or {}).get("trashed")) == want)
        elif (m := re.fullmatch(r"mimeType\s*(=|!=)\s*" + _Q, c, re.I)):
            op, val = m.group(1), _unq(m.group(2))
            preds.append(lambda i, op=op, val=val:
                         (item_obj(w, i)["mimeType"] == val) == (op == "="))
        elif (m := re.fullmatch(r"name\s*(=|contains)\s*" + _Q, c, re.I)):
            op, val = m.group(1).lower(), _unq(m.group(2))
            preds.append(lambda i, op=op, val=val:
                         ((w.drive_item(i) or {}).get("name", "") == val) if op == "="
                         else val.lower() in (w.drive_item(i) or {}).get("name", "").lower())
        elif (m := re.fullmatch(r"(modifiedTime|createdTime)\s*(>=|<=|>|<|=)\s*" + _Q, c, re.I)):
            field = "modified" if m.group(1).lower() == "modifiedtime" else "created"
            op, val = m.group(2), _dt(w, _unq(m.group(3)))
            cmp = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
                   ">": lambda a, b: a > b, "<": lambda a, b: a < b,
                   "=": lambda a, b: a == b}[op]

            def pred(i, field=field, cmp=cmp, val=val):
                x = w.drive_item(i) or {}
                d = _dt(w, x.get(field) or x.get("created"))
                return d is not None and cmp(d, val)
            preds.append(pred)
        else:
            raise PlatformError(P, 400, "invalidQuery", f"Invalid Value: unsupported q clause '{c}'")
    return [i for i in ids if all(p(i) for p in preds)]


@router.get("/files")
def list_files(request: Request, q: Optional[str] = None, pageToken: Optional[str] = None,
               pageSize: Optional[int] = None, driveId: Optional[str] = None,
               corpora: Optional[str] = None, includeItemsFromAllDrives: bool = False,
               supportsAllDrives: bool = False, fields: Optional[str] = None,
               orderBy: Optional[str] = None):
    w = world(request)
    with w.lock:
        ids = [i for i in [*sorted(w.folders), *sorted(w.files)]
               if not driveId or (w.drive_item(i) or {}).get("drive") == driveId]
        ids = run_q(w, q, ids)
        if orderBy:
            key = orderBy.split(",")[0].strip()
            desc = key.endswith(" desc")
            key = key.replace(" desc", "").strip()
            fld = {"modifiedTime": "modified", "createdTime": "created", "name": "name"}.get(key)
            if fld is None:
                raise PlatformError(P, 400, "invalidParameter", f"Unsupported orderBy {orderBy!r}")
            ids.sort(key=lambda i: ((w.drive_item(i) or {}).get(fld) or "", i), reverse=desc)
        page, nxt = paginate(P, f"files:{q}:{driveId}:{orderBy}", ids, pageToken, pageSize)
        out: dict[str, Any] = {"kind": "drive#fileList", "incompleteSearch": False,
                               "files": [item_obj(w, i) for i in page]}
    if nxt:
        out["nextPageToken"] = nxt
    return out


@router.get("/files/{file_id}")
def get_file(request: Request, file_id: str, alt: Optional[str] = None,
             fields: Optional[str] = None, supportsAllDrives: bool = False):
    w = world(request)
    with w.lock:
        x = _item(w, file_id)
        if alt == "media":
            if w.is_folder(file_id):
                raise PlatformError(P, 403, "fileNotDownloadable",
                                    "Only files with binary content can be downloaded.")
            mt = x.get("mimeType") or "text/plain"
            # leniency: real Drive refuses alt=media on Google-native docs (export instead)
            return PlainTextResponse(x.get("content", ""), media_type=(
                "text/plain" if mt.startswith("application/vnd.google-apps") else mt))
        return item_obj(w, file_id)


@router.get("/files/{file_id}/export")
def export_file(request: Request, file_id: str, mimeType: str = Query(...)):
    """Google-native documents are read through export in the real API; the mock serves the
    stored plain text for ``text/plain`` (and ``text/html``) exports."""
    w = world(request)
    with w.lock:
        x = _item(w, file_id)
        if w.is_folder(file_id):
            raise PlatformError(P, 403, "fileNotExportable", "Folders cannot be exported.")
        if mimeType not in ("text/plain", "text/html"):
            raise PlatformError(P, 400, "badRequest", f"Unsupported export mimeType {mimeType}")
        return PlainTextResponse(x.get("content", ""), media_type=mimeType)


def permissions_for(w: World, item_id: str) -> list[dict[str, Any]]:
    merged: dict[tuple, dict[str, Any]] = {}
    for perm, inherited, src, ptype in w.drive_permission_sources(item_id):
        t = perm.get("type", "user")
        principal = perm.get("principal")
        key = (t, None if t == "anyone" else principal)
        role = perm.get("role", "reader")
        detail: dict[str, Any] = {"permissionType": ptype, "role": role, "inherited": inherited}
        if inherited:
            detail["inheritedFrom"] = src
        obj = merged.get(key)
        if obj is None:
            obj = {"kind": "drive#permission", "id": perm.get("id"), "type": t, "role": role,
                   "deleted": False, "permissionDetails": []}
            if t == "user":
                obj["emailAddress"] = w.native(P, principal)
                obj["displayName"] = w.users.get(principal or "", {}).get("name", principal)
            elif t == "group":
                gid = w.resolve_group(principal or "") or principal
                obj["emailAddress"] = w.group_email(gid)
                obj["displayName"] = w.groups.get(gid, {}).get("name", gid)
            elif t == "domain":
                obj["domain"] = principal
                obj["allowFileDiscovery"] = bool(perm.get("allowFileDiscovery", False))
            elif t == "anyone":
                obj["allowFileDiscovery"] = bool(perm.get("allowFileDiscovery", False))
            merged[key] = obj
        elif ROLE_RANK.get(role, 0) > ROLE_RANK.get(obj["role"], 0):
            obj["role"] = role
        obj["permissionDetails"].append(detail)
    return list(merged.values())


@router.get("/files/{file_id}/permissions")
def list_permissions(request: Request, file_id: str, pageToken: Optional[str] = None,
                     pageSize: Optional[int] = None, supportsAllDrives: bool = False,
                     fields: Optional[str] = None):
    w = world(request)
    with w.lock:
        _item(w, file_id)
        perms = permissions_for(w, file_id)
        page, nxt = paginate(P, f"perms:{file_id}", perms, pageToken, pageSize)
    out: dict[str, Any] = {"kind": "drive#permissionList", "permissions": page}
    if nxt:
        out["nextPageToken"] = nxt
    return out


@router.get("/changes/startPageToken")
def start_page_token(request: Request, driveId: Optional[str] = None,
                     supportsAllDrives: bool = False):
    w = world(request)
    with w.lock:
        return {"kind": "drive#startPageToken", "startPageToken": str(len(w.drive_changes) + 1)}


@router.get("/changes")
def list_changes(request: Request, pageToken: str = Query(...), pageSize: Optional[int] = None,
                 driveId: Optional[str] = None, includeItemsFromAllDrives: bool = False,
                 supportsAllDrives: bool = False, fields: Optional[str] = None):
    w = world(request)
    try:
        start = int(pageToken) - 1
    except ValueError:
        raise PlatformError(P, 400, "invalidParameter", "Invalid Value: pageToken")
    with w.lock:
        if start < 0 or start > len(w.drive_changes):
            raise PlatformError(P, 400, "invalidParameter", "Invalid Value: pageToken")
        n = page_size(pageSize)
        chunk = w.drive_changes[start:start + n]
        changes = []
        for c in chunk:
            x = w.drive_item(c["fileId"])
            if driveId and (x or {}).get("drive") != driveId:
                continue
            removed = c["removed"] or x is None or bool((x or {}).get("trashed"))
            ch: dict[str, Any] = {"kind": "drive#change", "changeType": "file",
                                  "time": c["time"], "removed": removed,
                                  "fileId": c["fileId"], "driveId": (x or {}).get("drive")}
            if not removed:
                ch["file"] = item_obj(w, c["fileId"])
            changes.append(ch)
        out: dict[str, Any] = {"kind": "drive#changeList", "changes": changes}
        if start + n < len(w.drive_changes):
            out["nextPageToken"] = str(start + n + 1)
        else:
            out["newStartPageToken"] = str(len(w.drive_changes) + 1)
    return out
