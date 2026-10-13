"""Slack Web API subset, mounted at ``/slack/api`` (CONTRACTS §2).

Shapes follow api.slack.com: ``{ok: true, ...}``; method-level errors (unknown channel/user) are
HTTP 200 with ``{ok: false, error}`` exactly as Slack does; pagination is
``response_metadata.next_cursor`` ("" when exhausted). Auth and rate-limit failures are HTTP
401/429 (the contract's choice; real Slack returns 200 ``invalid_auth`` for bad tokens).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from .common import PlatformError, paginate, platform_auth, world
from .state import World

P = "slack"
router = APIRouter(dependencies=[Depends(platform_auth(P))])


def _us(ts: str) -> int:
    s, _, f = str(ts).partition(".")
    return int(s or 0) * 1_000_000 + int((f + "000000")[:6])


def _unix(iso: Optional[str]) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return 0


def _channel(w: World, cid: Optional[str]) -> dict:
    c = w.channels.get(cid or "")
    if c is None:
        raise PlatformError(P, 200, "channel_not_found")
    return c


def is_private(c: dict) -> bool:
    """private and connect channels are members-only. A Slack Connect channel is modelled as a
    PRIVATE shared channel (``is_private`` and ``is_ext_shared`` both true), matching the world's
    facts (only its members may read it). An explicit ``is_private`` in the world wins."""
    if "is_private" in c:
        return bool(c["is_private"])
    return c.get("type", "public") in ("private", "connect")


def channel_obj(w: World, c: dict) -> dict[str, Any]:
    ctype = c.get("type", "public")
    return {
        "id": c["id"],
        "name": c.get("name", c["id"]),
        "is_channel": True,
        "is_group": is_private(c),
        "is_im": False,
        "is_mpim": False,
        "is_private": is_private(c),
        "is_ext_shared": ctype == "connect",
        "is_shared": ctype == "connect",
        "is_org_shared": False,
        "is_archived": bool(c.get("archived", False)),
        "is_general": False,
        "created": _unix(c.get("created")),
        "context_team_id": w.workspace.get("id"),
        "num_members": len(c.get("members", [])),
    }


def user_obj(w: World, uid: str) -> dict[str, Any]:
    u = w.users[uid]
    kind = u.get("kind", "member")
    return {
        "id": w.native(P, uid),
        "team_id": (u.get("team_id") if kind == "connect" and u.get("team_id")
                    else ("T0EXTERNAL" if kind == "connect" else w.workspace.get("id"))),
        "name": uid,
        "real_name": u.get("name", uid),
        "deleted": not u.get("active", True),
        "is_bot": False,
        "is_admin": False,
        "is_restricted": kind == "guest",
        "is_ultra_restricted": bool(u.get("ultra_restricted", False)) and kind == "guest",
        "is_stranger": kind == "connect",
        "profile": {"email": u.get("email"), "real_name": u.get("name", uid),
                    "display_name": u.get("name", uid)},
    }


def message_obj(w: World, m: dict, thread: dict, *, parent: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "message", "user": w.native(P, m.get("user")),
                           "text": m.get("text", ""), "ts": m["ts"]}
    msgs = thread.get("messages", [])
    if len(msgs) > 1 or not parent:
        out["thread_ts"] = thread["ts"]
    if parent and len(msgs) > 1:
        out["reply_count"] = len(msgs) - 1
        out["latest_reply"] = msgs[-1]["ts"]
        users: list[str] = []
        for r in msgs[1:]:
            n = w.native(P, r.get("user"))
            if n not in users:
                users.append(n)
        out["reply_users"] = users
        out["reply_users_count"] = len(users)
    elif parent:
        out["reply_count"] = 0
    if m.get("edited"):
        out["edited"] = {"user": w.native(P, m.get("user")),
                         "ts": m.get("edited_ts") or m["ts"]}
    return out


def _ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, **kw}


@router.get("/conversations.list")
def conversations_list(request: Request, types: str = "public_channel",
                       exclude_archived: bool = False, cursor: Optional[str] = None,
                       limit: Optional[int] = None):
    w = world(request)
    wanted = {t.strip() for t in types.split(",") if t.strip()}
    with w.lock:
        chans = []
        for c in w.channels.values():
            private = is_private(c)
            if private and "private_channel" not in wanted:
                continue
            if not private and "public_channel" not in wanted:
                continue
            if exclude_archived and c.get("archived"):
                continue
            chans.append(channel_obj(w, c))
        chans.sort(key=lambda c: c["id"])
        page, nxt = paginate(P, "conversations.list", chans, cursor, limit)
    return _ok(channels=page, response_metadata={"next_cursor": nxt or ""})


@router.get("/conversations.info")
def conversations_info(request: Request, channel: Optional[str] = None):
    w = world(request)
    with w.lock:
        return _ok(channel=channel_obj(w, _channel(w, channel)))


@router.get("/conversations.members")
def conversations_members(request: Request, channel: Optional[str] = None,
                          cursor: Optional[str] = None, limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        c = _channel(w, channel)
        members = [w.native(P, u) for u in c.get("members", [])]
        page, nxt = paginate(P, f"members:{c['id']}", members, cursor, limit)
    return _ok(members=page, response_metadata={"next_cursor": nxt or ""})


@router.get("/conversations.history")
def conversations_history(request: Request, channel: Optional[str] = None,
                          oldest: Optional[str] = None, latest: Optional[str] = None,
                          inclusive: bool = False, cursor: Optional[str] = None,
                          limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        c = _channel(w, channel)
        lo = _us(oldest) if oldest else None
        hi = _us(latest) if latest else None
        parents = []
        for (ch, ts), t in w.threads.items():
            if ch != c["id"] or not t.get("messages"):
                continue
            v = _us(ts)
            if lo is not None and (v < lo or (v == lo and not inclusive)):
                continue
            if hi is not None and (v > hi or (v == hi and not inclusive)):
                continue
            parents.append(t)
        parents.sort(key=lambda t: _us(t["ts"]), reverse=True)       # newest first, like Slack
        page, nxt = paginate(P, f"history:{c['id']}", parents, cursor, limit)
        msgs = [message_obj(w, t["messages"][0], t, parent=True) for t in page]
    return _ok(messages=msgs, has_more=nxt is not None,
               response_metadata={"next_cursor": nxt or ""})


@router.get("/conversations.replies")
def conversations_replies(request: Request, channel: Optional[str] = None,
                          ts: Optional[str] = None, cursor: Optional[str] = None,
                          limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        c = _channel(w, channel)
        t = w.threads.get((c["id"], ts or ""))
        if t is None:
            # ts may name a reply; Slack resolves it to its thread
            for (ch, _), cand in w.threads.items():
                if ch == c["id"] and any(m["ts"] == ts for m in cand.get("messages", [])):
                    t = cand
                    break
        if t is None:
            raise PlatformError(P, 200, "thread_not_found")
        msgs = t.get("messages", [])
        page, nxt = paginate(P, f"replies:{c['id']}:{t['ts']}", msgs, cursor, limit)
        out = [message_obj(w, m, t, parent=(m["ts"] == t["ts"])) for m in page]
    return _ok(messages=out, has_more=nxt is not None,
               response_metadata={"next_cursor": nxt or ""})


@router.get("/users.info")
def users_info(request: Request, user: Optional[str] = None):
    w = world(request)
    with w.lock:
        uid = w.ib_id(P, user)
        if uid is None:
            raise PlatformError(P, 200, "user_not_found")
        return _ok(user=user_obj(w, uid))


@router.get("/users.list")
def users_list(request: Request, cursor: Optional[str] = None, limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        ids = sorted(w.users)
        page, nxt = paginate(P, "users.list", ids, cursor, limit)
        members = [user_obj(w, uid) for uid in page]
    return _ok(members=members, response_metadata={"next_cursor": nxt or ""})
