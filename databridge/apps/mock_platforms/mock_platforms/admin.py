"""``/admin/*``: the demo simulator's mutations (admin token only; CONTRACTS §2 "Admin endpoints").

Every mutation changes what the platform APIs return AND fires a signed webhook. Person-valued
inputs accept an IB user id or the platform's native id. Webhook ``object_ids`` are native ids:
Slack ``"<channel>:<thread_ts>"``, Confluence page id, Jira issue key, Drive file/folder id.
``container`` is set only when an ACL change spans a container (Slack channel membership, a
Confluence restriction cascading to descendants, a Drive permission removal, a Jira ACL-relevant
edit); plain content events carry ``container: null``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Request

from .common import PlatformError, admin_auth, world
from .state import PLATFORMS, World

router = APIRouter(dependencies=[Depends(admin_auth)])

JIRA_EDITABLE = {"summary", "description", "status", "assignee", "reporter", "security_level",
                 "labels", "comment", "links"}
JIRA_ACL_FIELDS = {"assignee", "reporter", "security_level"}


def _err(status: int, msg: str) -> PlatformError:
    return PlatformError("admin", status, "bad_request" if status == 400 else "not_found", msg)


def _emit(request: Request, platform: str, type_: str, object_ids: list[str], *,
          container: Optional[str] = None, user: Optional[str] = None) -> dict[str, Any]:
    w: World = world(request)
    rec = request.app.state.webhooks.emit(platform, type_, object_ids, at=w.now_iso(),
                                          container=container, user=user)
    return {**rec["payload"], "delivery_id": rec["headers"]["X-IB-Delivery"]}


def _user(w: World, platform: str, raw: Any) -> str:
    uid = w.ib_id(platform, raw) if isinstance(raw, str) else None
    if uid is None:
        raise _err(404, f"unknown user {raw!r}")
    return uid


# ------------------------------------------------------------------------------ global
@router.post("/reset")
def reset(request: Request):
    w = world(request)
    w.reload()
    return {"ok": True, "world": str(w.path)}


@router.post("/ratelimit")
def ratelimit(request: Request, body: dict = Body(...)):
    w = world(request)
    platform = body.get("platform")
    if platform not in PLATFORMS:
        raise _err(400, f"platform must be one of {PLATFORMS}")
    with w.lock:
        w.ratelimited[platform] = bool(body.get("on", True))
        return {"ok": True, "ratelimited": dict(w.ratelimited)}


@router.get("/webhooks")
def sent_webhooks(request: Request, limit: int = 50):
    sent = request.app.state.webhooks.sent[-limit:]
    return {"ok": True, "deliveries": [{"delivery_id": r["headers"]["X-IB-Delivery"],
                                        **r["payload"]} for r in sent]}


# ------------------------------------------------------------------------------ slack
def _slack_members(request: Request, channel_id: str, body: dict, add: bool):
    w = world(request)
    with w.lock:
        c = w.channels.get(channel_id)
        if c is None:
            raise _err(404, f"unknown channel {channel_id}")
        uid = _user(w, "slack", body.get("user"))
        members = c.setdefault("members", [])
        changed = (uid not in members) if add else (uid in members)
        if changed:
            if add:
                members.append(uid)
            else:
                members.remove(uid)
    hook = (_emit(request, "slack", "membership.changed", [channel_id], container=channel_id,
                  user=uid) if changed else None)
    return {"ok": True, "changed": changed, "webhook": hook}


@router.post("/slack/channels/{channel_id}/members/remove")
def slack_member_remove(request: Request, channel_id: str, body: dict = Body(...)):
    return _slack_members(request, channel_id, body, add=False)


@router.post("/slack/channels/{channel_id}/members/add")
def slack_member_add(request: Request, channel_id: str, body: dict = Body(...)):
    return _slack_members(request, channel_id, body, add=True)


@router.post("/slack/threads")
def slack_new_thread(request: Request, body: dict = Body(...)):
    """``{"channel","user","text"}`` starts a thread; optional ``thread_ts`` posts a reply."""
    w = world(request)
    with w.lock:
        ch = body.get("channel")
        if ch not in w.channels:
            raise _err(404, f"unknown channel {ch}")
        uid = _user(w, "slack", body.get("user"))
        text = body.get("text")
        if not isinstance(text, str) or not text:
            raise _err(400, "text is required")
        ts = w.slack_ts_now(ch)
        msg = {"ts": ts, "user": uid, "text": text, "edited": False}
        parent = body.get("thread_ts")
        if parent:
            t = w.threads.get((ch, parent))
            if t is None:
                raise _err(404, f"unknown thread {ch}:{parent}")
            t["messages"].append(msg)
        else:
            t = {"channel": ch, "ts": ts, "messages": [msg]}
            w.threads[(ch, ts)] = t
            w.raw.setdefault("slack", {}).setdefault("threads", []).append(t)
        thread_ts = t["ts"]
    hook = _emit(request, "slack", "content.changed", [f"{ch}:{thread_ts}"], user=uid)
    return {"ok": True, "channel": ch, "ts": ts, "thread_ts": thread_ts, "webhook": hook}


# ------------------------------------------------------------------------------ confluence
def _cf_page(w: World, page_id: str) -> dict:
    p = w.pages.get(page_id)
    if p is None or p.get("status") == "trashed":
        raise _err(404, f"unknown page {page_id}")
    return p


@router.post("/confluence/pages/{page_id}/edit")
def confluence_edit(request: Request, page_id: str, body: dict = Body(...)):
    w = world(request)
    with w.lock:
        p = _cf_page(w, page_id)
        if not isinstance(body.get("body"), str):
            raise _err(400, "body is required")
        p.setdefault("history", []).append({"version": int(p.get("version", 1)),
                                            "updated": p.get("updated"),
                                            "body": p.get("body", "")})
        p["version"] = int(p.get("version", 1)) + 1
        p["body"] = body["body"]
        if isinstance(body.get("title"), str):
            p["title"] = body["title"]
        if body.get("user"):
            p["last_editor"] = _user(w, "confluence", body["user"])
        p["updated"] = w.now_iso()
        version = p["version"]
    hook = _emit(request, "confluence", "content.changed", [page_id], user=p.get("last_editor"))
    return {"ok": True, "id": page_id, "version": version, "webhook": hook}


@router.post("/confluence/pages")
def confluence_create(request: Request, body: dict = Body(...)):
    w = world(request)
    with w.lock:
        space = body.get("space")
        if space not in w.spaces:
            space = w.space_by_id.get(space or "", None)
        if space is None:
            raise _err(404, f"unknown space {body.get('space')!r}")
        parent = body.get("parent")
        if parent is not None:
            pp = _cf_page(w, parent)
            if pp["space"] != space:
                raise _err(400, "parent page is in a different space")
        if not isinstance(body.get("title"), str) or not body["title"]:
            raise _err(400, "title is required")
        nums = [int(i.rsplit("-", 1)[-1]) for i in w.pages if i.rsplit("-", 1)[-1].isdigit()]
        new_id = f"cf-{max(nums, default=1000) + 1}"
        now = w.now_iso()
        author = _user(w, "confluence", body["author"]) if body.get("author") else None
        page = {"id": new_id, "space": space, "parent": parent, "title": body["title"],
                "version": 1, "status": "current", "created": now, "updated": now,
                "author": author, "body": body.get("body", ""), "read_restriction": None,
                "history": []}
        w.pages[new_id] = page
        w.raw.setdefault("confluence", {}).setdefault("pages", []).append(page)
    hook = _emit(request, "confluence", "content.changed", [new_id], user=author)
    return {"ok": True, "id": new_id, "webhook": hook}


def _set_restriction(request: Request, page_id: str, restriction: Optional[dict]):
    w = world(request)
    with w.lock:
        p = _cf_page(w, page_id)
        p["read_restriction"] = restriction
        ids = [page_id, *(d for d in w.page_descendants(page_id)
                          if w.pages[d].get("status") != "trashed")]
        space = p["space"]
    hook = _emit(request, "confluence", "acl.changed", ids, container=space)
    return {"ok": True, "id": page_id, "read_restriction": restriction, "affected": ids,
            "webhook": hook}


@router.post("/confluence/pages/{page_id}/restrict")
def confluence_restrict(request: Request, page_id: str, body: dict = Body(...)):
    w = world(request)
    with w.lock:
        users = [_user(w, "confluence", u) for u in body.get("users") or []]
        groups = []
        for g in body.get("groups") or []:
            gid = w.resolve_group(g)
            if gid is None:
                raise _err(404, f"unknown group {g!r}")
            groups.append(gid)
    return _set_restriction(request, page_id, {"users": users, "groups": groups})


@router.post("/confluence/pages/{page_id}/unrestrict")
def confluence_unrestrict(request: Request, page_id: str):
    return _set_restriction(request, page_id, None)


@router.post("/confluence/pages/{page_id}/trash")
def confluence_trash(request: Request, page_id: str):
    """Extra (not in CONTRACTS §2): trash a page -> 404 everywhere + content.deleted."""
    w = world(request)
    with w.lock:
        p = _cf_page(w, page_id)
        p["status"] = "trashed"
        p["updated"] = w.now_iso()
    hook = _emit(request, "confluence", "content.deleted", [page_id])
    return {"ok": True, "id": page_id, "webhook": hook}


# ------------------------------------------------------------------------------ jira
@router.post("/jira/issues/{key}/edit")
def jira_edit(request: Request, key: str, body: dict = Body(...)):
    w = world(request)
    with w.lock:
        issue = w.issues.get(key)
        if issue is None:
            raise _err(404, f"unknown issue {key}")
        unknown = set(body) - JIRA_EDITABLE - {"user"}
        if unknown:
            raise _err(400, f"unsupported fields {sorted(unknown)}; editable: "
                            f"{sorted(JIRA_EDITABLE)}")
        if "security_level" in body and body["security_level"] is not None \
                and str(body["security_level"]) not in w.levels:
            raise _err(404, f"unknown security level {body['security_level']!r}")
        for f in ("summary", "description", "status", "labels", "links"):
            if f in body:
                issue[f] = body[f]
        for f in ("assignee", "reporter"):
            if f in body:
                issue[f] = None if body[f] is None else _user(w, "jira", body[f])
        if "security_level" in body:
            issue["security_level"] = (None if body["security_level"] is None
                                       else str(body["security_level"]))
        if "comment" in body:
            c = body["comment"]
            c = c if isinstance(c, dict) else {"body": str(c)}
            author = _user(w, "jira", c["author"]) if c.get("author") else None
            issue.setdefault("comments", []).append(
                {"author": author, "created": w.now_iso(), "body": c.get("body", "")})
        issue["updated"] = w.now_iso()
        project = issue["project"]
        editor = _user(w, "jira", body["user"]) if body.get("user") else None
    hooks = [_emit(request, "jira", "content.changed", [key], user=editor)]
    if JIRA_ACL_FIELDS & set(body):
        hooks.append(_emit(request, "jira", "acl.changed", [key], container=project))
    return {"ok": True, "key": key, "webhooks": hooks}


# ------------------------------------------------------------------------------ drive
@router.post("/drive/files/{file_id}/edit")
def drive_edit(request: Request, file_id: str, body: dict = Body(...)):
    w = world(request)
    with w.lock:
        f = w.files.get(file_id)
        if f is None:
            raise _err(404, f"unknown file {file_id}")
        if not isinstance(body.get("content"), str):
            raise _err(400, "content is required")
        f["content"] = body["content"]
        if isinstance(body.get("name"), str):
            f["name"] = body["name"]
        f["version"] = int(f.get("version", 1)) + 1
        f["modified"] = w.now_iso()
        w.record_drive_change(file_id)
        version = f["version"]
    hook = _emit(request, "drive", "content.changed", [file_id])
    return {"ok": True, "id": file_id, "version": version, "webhook": hook}


@router.post("/drive/files/{file_id}/permissions/remove")
def drive_permission_remove(request: Request, file_id: str, body: dict = Body(...)):
    w = world(request)
    with w.lock:
        item = w.drive_item(file_id)
        if item is None:
            raise _err(404, f"unknown file or folder {file_id}")
        pid = body.get("permission_id")
        perms = item.setdefault("permissions", [])
        hit = next((p for p in perms if p.get("id") == pid), None)
        if hit is None:
            raise _err(404, f"permission {pid!r} is not set directly on {file_id}")
        perms.remove(hit)
        ids = [file_id, *(w.drive_descendants(file_id) if w.is_folder(file_id) else [])]
        for i in ids:
            w.record_drive_change(i)
        drive_id = item.get("drive")
    hook = _emit(request, "drive", "acl.changed", ids, container=drive_id)
    return {"ok": True, "id": file_id, "removed": hit, "affected": ids, "webhook": hook}


# ------------------------------------------------------------------------------ directory
@router.post("/directory/users/{user_id}/deactivate")
def deactivate_user(request: Request, user_id: str):
    w = world(request)
    with w.lock:
        if user_id not in w.users:
            raise _err(404, f"unknown user {user_id}")
        w.users[user_id]["active"] = False
        natives = {p: w.native(p, user_id) for p in PLATFORMS}
    hooks = [_emit(request, p, "user.changed", [natives[p]], user=user_id) for p in PLATFORMS]
    return {"ok": True, "id": user_id, "webhooks": hooks}
