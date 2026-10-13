"""Confluence Cloud subset, mounted at ``/confluence/wiki`` (CONTRACTS §2).

v2 list endpoints return ``{results, _links:{next, base}}``; ``_links.next`` is a site-relative path
(``/wiki/api/v2/...?cursor=...``) exactly as Atlassian returns it, so a connector whose base URL is
``http://<mock>/confluence`` can GET it as-is. A ``Link: <...>; rel="next"`` header is also sent.

Trashed pages are not found (404) on every endpoint unless ``status=trashed`` is asked for on a
list/get. The permission check (admin token only) evaluates: space read grant (users/groups, nested
groups expanded) AND every read restriction on the page and each ancestor.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from .common import PlatformError, paginate, platform_auth, require_admin_role, world
from .state import World

P = "confluence"
router = APIRouter(dependencies=[Depends(platform_auth(P))])
DEFAULT_STATUSES = ("current", "archived")


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/confluence/wiki"


def _links(request: Request, response: Response, path: str, params: dict[str, Any],
           nxt: Optional[str]) -> dict[str, Any]:
    links: dict[str, Any] = {"base": _base(request)}
    if nxt:
        q = {k: v for k, v in params.items() if v not in (None, "")}
        q["cursor"] = nxt
        links["next"] = f"/wiki/api/v2/{path}?{urlencode(q)}"
        response.headers["Link"] = f'<{links["next"]}>; rel="next"'
    return links


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "+", title).strip("+")


def space_obj(w: World, s: dict) -> dict[str, Any]:
    return {"id": s["id"], "key": s["key"], "name": s.get("name", s["key"]), "type": "global",
            "status": "current", "authorId": None, "homepageId": None,
            "description": None, "_links": {"webui": f"/spaces/{s['key']}"}}


def page_obj(w: World, p: dict, *, body_format: Optional[str], version: Optional[int] = None
             ) -> dict[str, Any]:
    space = w.spaces.get(p["space"], {})
    number = int(p.get("version", 1))
    body = p.get("body", "")
    created_at = p.get("updated") or p.get("created")
    if version is not None and version != number:
        hit = next((h for h in p.get("history", []) if int(h.get("version", -1)) == version), None)
        if hit is None:
            raise PlatformError(P, 404, "not_found", f"Version {version} not found")
        number, body, created_at = version, hit.get("body", ""), hit.get("updated", created_at)
    out: dict[str, Any] = {
        "id": p["id"], "status": p.get("status", "current"), "title": p.get("title", ""),
        "spaceId": space.get("id"), "parentId": p.get("parent"),
        "parentType": "page" if p.get("parent") else None, "position": None,
        "authorId": w.native(P, p.get("author")), "ownerId": w.native(P, p.get("author")),
        "lastOwnerId": None, "createdAt": p.get("created"),
        "version": {"number": number, "createdAt": created_at, "message": "", "minorEdit": False,
                    "authorId": w.native(P, p.get("last_editor") or p.get("author"))},
        "_links": {"webui": f"/spaces/{p['space']}/pages/{p['id']}/{_slug(p.get('title', ''))}",
                   "editui": f"/pages/resumedraft.action?draftId={p['id']}",
                   "tinyui": f"/x/{p['id']}"},
    }
    if body_format:
        out["body"] = {body_format: {"value": body, "representation": body_format}}
    return out


def _page(w: World, page_id: str, statuses: tuple[str, ...] = DEFAULT_STATUSES) -> dict:
    p = w.pages.get(page_id)
    if p is None or p.get("status", "current") not in statuses:
        raise PlatformError(P, 404, "not_found", "Page not found")
    return p


def _space_by_id(w: World, space_id: str) -> dict:
    key = w.space_by_id.get(space_id)
    if key is None:
        raise PlatformError(P, 404, "not_found", "Space not found")
    return w.spaces[key]


def _statuses(status: Optional[str]) -> tuple[str, ...]:
    if not status:
        return DEFAULT_STATUSES
    return tuple(s.strip() for s in status.split(",") if s.strip())


# --------------------------------------------------------------------------------- spaces
@router.get("/api/v2/spaces")
def list_spaces(request: Request, response: Response, cursor: Optional[str] = None,
                limit: Optional[int] = None, keys: Optional[str] = None,
                ids: Optional[str] = None):
    w = world(request)
    with w.lock:
        spaces = sorted(w.spaces.values(), key=lambda s: s["id"])
        if keys:
            want = set(keys.split(","))
            spaces = [s for s in spaces if s["key"] in want]
        if ids:
            want = set(ids.split(","))
            spaces = [s for s in spaces if s["id"] in want]
        page, nxt = paginate(P, "spaces", spaces, cursor, limit)
        res = [space_obj(w, s) for s in page]
    return {"results": res,
            "_links": _links(request, response, "spaces",
                             {"limit": limit, "keys": keys, "ids": ids}, nxt)}


@router.get("/api/v2/spaces/{space_id}")
def get_space(request: Request, space_id: str):
    w = world(request)
    with w.lock:
        return space_obj(w, _space_by_id(w, space_id))


@router.get("/api/v2/spaces/{space_id}/permissions")
def space_permissions(request: Request, response: Response, space_id: str,
                      cursor: Optional[str] = None, limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        s = _space_by_id(w, space_id)
        read = s.get("read") or {}
        perms = []
        for u in read.get("users", []):
            nid = w.native(P, u)
            perms.append({"id": f"{s['id']}-read-user-{nid}",
                          "principal": {"type": "user", "id": nid},
                          "operation": {"key": "read", "targetType": "space"}})
        for g in read.get("groups", []):
            gid = w.resolve_group(g) or g
            perms.append({"id": f"{s['id']}-read-group-{gid}",
                          "principal": {"type": "group", "id": gid},
                          "operation": {"key": "read", "targetType": "space"}})
        page, nxt = paginate(P, f"spaceperm:{s['id']}", perms, cursor, limit)
    return {"results": page,
            "_links": _links(request, response, f"spaces/{space_id}/permissions",
                             {"limit": limit}, nxt)}


# --------------------------------------------------------------------------------- pages
def _list_pages(request: Request, response: Response, path: str, space_ids: Optional[str],
                cursor, limit, body_format, status, sort, title):
    w = world(request)
    statuses = _statuses(status)
    with w.lock:
        pages = list(w.pages.values())
        if space_ids:
            keys = set()
            for sid in space_ids.split(","):
                k = w.space_by_id.get(sid.strip())
                if k:
                    keys.add(k)
            pages = [p for p in pages if p["space"] in keys]
        pages = [p for p in pages if p.get("status", "current") in statuses]
        if title:
            pages = [p for p in pages if p.get("title") == title]
        key = {"id": lambda p: p["id"], "created-date": lambda p: p.get("created") or "",
               "modified-date": lambda p: p.get("updated") or "",
               "title": lambda p: p.get("title") or ""}
        s = (sort or "id")
        desc = s.startswith("-")
        fn = key.get(s.lstrip("-"))
        if fn is None:
            raise PlatformError(P, 400, "invalid_request", f"Unsupported sort {sort!r}")
        pages.sort(key=lambda p: (fn(p), p["id"]), reverse=desc)
        page, nxt = paginate(P, f"pages:{space_ids}:{','.join(statuses)}:{s}:{title}", pages,
                             cursor, limit)
        res = [page_obj(w, p, body_format=body_format) for p in page]
    params = {"space-id": space_ids if path == "pages" else None, "limit": limit,
              "body-format": body_format, "status": status, "sort": sort, "title": title}
    return {"results": res, "_links": _links(request, response, path, params, nxt)}


@router.get("/api/v2/pages")
def list_pages(request: Request, response: Response,
               space_id: Optional[str] = Query(None, alias="space-id"),
               cursor: Optional[str] = None, limit: Optional[int] = None,
               body_format: Optional[str] = Query(None, alias="body-format"),
               status: Optional[str] = None, sort: Optional[str] = None,
               title: Optional[str] = None):
    return _list_pages(request, response, "pages", space_id, cursor, limit, body_format, status,
                       sort, title)


@router.get("/api/v2/spaces/{space_id}/pages")
def list_space_pages(request: Request, response: Response, space_id: str,
                     cursor: Optional[str] = None, limit: Optional[int] = None,
                     body_format: Optional[str] = Query(None, alias="body-format"),
                     status: Optional[str] = None, sort: Optional[str] = None,
                     title: Optional[str] = None):
    w = world(request)
    with w.lock:
        _space_by_id(w, space_id)
    return _list_pages(request, response, f"spaces/{space_id}/pages", space_id, cursor, limit,
                       body_format, status, sort, title)


@router.get("/api/v2/pages/{page_id}")
def get_page(request: Request, page_id: str,
             body_format: Optional[str] = Query(None, alias="body-format"),
             version: Optional[int] = None, status: Optional[str] = None):
    w = world(request)
    with w.lock:
        p = _page(w, page_id, _statuses(status))
        return page_obj(w, p, body_format=body_format, version=version)


@router.get("/api/v2/pages/{page_id}/ancestors")
def page_ancestors(request: Request, response: Response, page_id: str,
                   cursor: Optional[str] = None, limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        _page(w, page_id)
        anc = [{"id": a, "type": "page"} for a in w.page_ancestors(page_id)]
        page, nxt = paginate(P, f"anc:{page_id}", anc, cursor, limit)
    return {"results": page,
            "_links": _links(request, response, f"pages/{page_id}/ancestors", {"limit": limit},
                             nxt)}


@router.get("/api/v2/pages/{page_id}/versions")
def page_versions(request: Request, response: Response, page_id: str,
                  cursor: Optional[str] = None, limit: Optional[int] = None):
    w = world(request)
    with w.lock:
        p = _page(w, page_id)
        vers = [{"number": int(p.get("version", 1)), "createdAt": p.get("updated"),
                 "message": "", "minorEdit": False,
                 "authorId": w.native(P, p.get("last_editor") or p.get("author"))}]
        for h in sorted(p.get("history", []), key=lambda h: -int(h.get("version", 0))):
            vers.append({"number": int(h.get("version", 0)), "createdAt": h.get("updated"),
                         "message": "", "minorEdit": False, "authorId": None})
        page, nxt = paginate(P, f"vers:{page_id}", vers, cursor, limit)
    return {"results": page,
            "_links": _links(request, response, f"pages/{page_id}/versions", {"limit": limit},
                             nxt)}


# --------------------------------------------------------------------------------- v1 restrictions
@router.get("/rest/api/content/{page_id}/restriction/byOperation/read")
def read_restriction(request: Request, page_id: str):
    w = world(request)
    with w.lock:
        p = _page(w, page_id)
        r = p.get("read_restriction") or {"users": [], "groups": []}
        users = []
        for uid in r.get("users", []):
            u = w.users.get(uid, {})
            users.append({"type": "known", "accountId": w.native(P, uid),
                          "accountType": "atlassian", "email": u.get("email"),
                          "publicName": u.get("name", uid), "displayName": u.get("name", uid)})
        groups = []
        for g in r.get("groups", []):
            gid = w.resolve_group(g) or g
            groups.append({"type": "group", "name": w.groups.get(gid, {}).get("name", gid),
                           "id": gid})
    return {"operation": "read",
            "restrictions": {
                "user": {"results": users, "start": 0, "limit": 200, "size": len(users)},
                "group": {"results": groups, "start": 0, "limit": 200, "size": len(groups)}},
            "content": {"id": page_id, "type": "page", "status": p.get("status", "current")},
            "_links": {"base": _base(request)}}


@router.post("/rest/api/content/{page_id}/permission/check")
def permission_check(request: Request, page_id: str, body: dict = Body(...)):
    require_admin_role(request, P)
    subject = body.get("subject") or {}
    op = body.get("operation")
    if subject.get("type") != "user" or not subject.get("identifier"):
        raise PlatformError(P, 400, "invalid_request", "subject must be {type:user, identifier}")
    if op != "read":
        raise PlatformError(P, 400, "invalid_request", "only operation 'read' is supported")
    w = world(request)
    with w.lock:
        _page(w, page_id)                       # trashed or missing -> 404
        uid = w.ib_id(P, subject["identifier"])
        if uid is None:
            return {"hasPermission": False,
                    "errors": [{"message": {"key": "user.not.found",
                                            "args": [subject["identifier"]]}}]}
        return {"hasPermission": w.confluence_can_read(uid, page_id), "errors": []}
