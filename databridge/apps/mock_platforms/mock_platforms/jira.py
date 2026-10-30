"""Jira Cloud REST v3 subset, mounted at ``/jira/rest/api/3`` (CONTRACTS §2).

* ``search/jql`` uses the enhanced-search pagination (``nextPageToken`` / ``isLast``) and a JQL
  subset: ``project = X`` / ``project in (X, Y)``, ``key = K`` / ``key in (...)``,
  ``updated|created <op> "<iso or yyyy-MM-dd HH:mm>"`` joined by AND, ``ORDER BY updated|created|key
  [ASC|DESC]``. Anything else is a 400, as real Jira rejects unparseable JQL.
* v3 returns rich text (description, comment bodies) as **ADF** documents, like the real API.
* ``permissions/check`` (admin token only) = Browse (role actors + users/groups + reporter/assignee
  holders) AND, when the issue has a security level, level membership; ``CREATE_ISSUES`` for
  projects (``World.jira_can_create``: the project's ``create`` holders, by default its
  ``Developers`` role).
* ``POST issue`` creates an issue the way Jira Cloud's ``POST /rest/api/3/issue`` does (fields
  ``project``, ``summary``, ``issuetype``, ``description`` as ADF, optional ``reporter`` and
  ``labels``); 201 ``{id, key, self}``. Like Jira Cloud, it authorises the CALLER (the service
  account: ``World.jira_service_can_create``), not the reporter; a caller without "Create issues"
  in the project gets a 403. The reporter's own permission is checked by the Internal Brain's live
  ``creatable`` re-check, not here. A ``content.changed`` webhook follows.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query, Request

from .common import PlatformError, paginate, platform_auth, require_admin_role, world
from .state import World

P = "jira"
router = APIRouter(dependencies=[Depends(platform_auth(P))])
BROWSE = "BROWSE_PROJECTS"
CREATE = "CREATE_ISSUES"
ISSUE_TYPES = ("Task", "Story", "Bug")
MAX_SUMMARY = 255


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/jira/rest/api/3"


def adf(text: Optional[str]) -> dict[str, Any]:
    paras = [p for p in (text or "").split("\n\n") if p.strip()]
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]}
                        for p in paras]}


def user_ref(w: World, uid: Optional[str]) -> Optional[dict[str, Any]]:
    if uid is None:
        return None
    u = w.users.get(uid, {})
    return {"accountId": w.native(P, uid), "accountType": "atlassian",
            "displayName": u.get("name", uid), "active": bool(u.get("active", True))}


def _service_ref(w: World) -> dict[str, Any]:
    """The calling service account as Jira shows it (creator of every issue the IB creates)."""
    sa = w.jira_service
    return {"accountId": sa["account_id"], "accountType": "app", "displayName": sa["name"],
            "active": True}


def role_ids(w: World) -> dict[str, int]:
    names = sorted({r for p in w.projects.values() for r in (p.get("roles") or {})})
    return {n: 10002 + i for i, n in enumerate(names)}


def _role_name(w: World, name_or_id: str) -> Optional[str]:
    ids = role_ids(w)
    if name_or_id in ids:
        return name_or_id
    for n, i in ids.items():
        if str(i) == str(name_or_id):
            return n
    return None


def project_obj(w: World, request: Request, p: dict) -> dict[str, Any]:
    base = _base(request)
    return {"id": str(p["id"]), "key": p["key"], "name": p.get("name", p["key"]),
            "self": f"{base}/project/{p['id']}", "projectTypeKey": "software",
            "roles": {r: f"{base}/project/{p['id']}/role/{role_ids(w)[r]}"
                      for r in (p.get("roles") or {})}}


def issue_obj(w: World, request: Request, i: dict, fields: Optional[str] = None
              ) -> dict[str, Any]:
    base = _base(request)
    proj = w.projects.get(i["project"], {"id": None, "key": i["project"], "name": i["project"]})
    lvl = w.levels.get(str(i["security_level"])) if i.get("security_level") is not None else None
    comments = [{"id": str(10000 + n), "author": user_ref(w, c.get("author")),
                 "body": adf(c.get("body")), "created": c.get("created"),
                 "updated": c.get("created")}
                for n, c in enumerate(i.get("comments", []))]
    links = []
    for n, k in enumerate(i.get("links", [])):
        other = w.issues.get(k)
        out_issue: dict[str, Any] = {"key": k}
        if other is not None:
            out_issue["id"] = str(other["id"])
            out_issue["self"] = f"{base}/issue/{other['id']}"
        links.append({"id": f"{i['id']}{n:02d}", "outwardIssue": out_issue,
                      "type": {"name": "Relates", "inward": "relates to",
                               "outward": "relates to"}})
    status = i.get("status", "To Do")
    cat = ("done" if status.lower() in ("done", "closed", "resolved")
           else "new" if status.lower() in ("to do", "open", "backlog") else "indeterminate")
    f: dict[str, Any] = {
        "summary": i.get("summary", ""),
        "description": adf(i.get("description")),
        "status": {"name": status, "statusCategory": {"key": cat}},
        "project": {"id": str(proj.get("id")), "key": proj["key"],
                    "name": proj.get("name", proj["key"])},
        "issuetype": {"name": i.get("issuetype", "Task")},
        "reporter": (_service_ref(w) if i.get("reporter") is None and i.get("reporter_is_service")
                     else user_ref(w, i.get("reporter"))),
        "creator": (_service_ref(w) if i.get("created_by_service") else user_ref(w, i.get("reporter"))),
        "assignee": user_ref(w, i.get("assignee")),
        "security": ({"id": str(lvl["id"]), "name": lvl.get("name"),
                      "self": f"{base}/securitylevel/{lvl['id']}"} if lvl else None),
        "created": i.get("created"),
        "updated": i.get("updated"),
        "labels": list(i.get("labels", [])),
        "comment": {"comments": comments, "total": len(comments), "maxResults": len(comments),
                    "startAt": 0},
        "issuelinks": links,
    }
    if fields and fields not in ("*all", "*navigable"):
        want = {x.strip() for x in fields.split(",") if x.strip()}
        f = {k: v for k, v in f.items() if k in want}
    return {"id": str(i["id"]), "key": i["key"], "self": f"{base}/issue/{i['id']}", "fields": f}


def _issue(w: World, key_or_id: str) -> dict:
    key = key_or_id if key_or_id in w.issues else w.issue_by_id.get(str(key_or_id))
    if key is None:
        raise PlatformError(P, 404, "not_found",
                            "Issue does not exist or you do not have permission to see it.")
    return w.issues[key]


def _project(w: World, key_or_id: str) -> dict:
    if key_or_id in w.projects:
        return w.projects[key_or_id]
    for p in w.projects.values():
        if str(p["id"]) == str(key_or_id):
            return p
    raise PlatformError(P, 404, "not_found",
                        f"No project could be found with key '{key_or_id}'.")


# ------------------------------------------------------------------------------------- JQL
_SPLIT_AND = re.compile(r"\s+AND\s+(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", re.I)
_LIST = r"\(\s*([^)]*)\)"


def _jql_dt(w: World, raw: str) -> datetime:
    s = raw.strip().strip('"').strip("'").replace("/", "-")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise PlatformError(P, 400, "invalid_jql", f"Date value '{raw}' for field is invalid.")
    return dt if dt.tzinfo else dt.replace(tzinfo=w.tz)


def _issue_dt(w: World, v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=w.tz)


def _vals(raw: str) -> list[str]:
    return [x.strip().strip('"').strip("'") for x in raw.split(",") if x.strip()]


def run_jql(w: World, jql: Optional[str]) -> list[dict]:
    text = (jql or "").strip()
    order_field, desc = "id", False
    m = re.search(r"\s*ORDER\s+BY\s+(\w+)(?:\s+(ASC|DESC))?\s*$", text, re.I)
    if m:
        order_field = m.group(1).lower()
        desc = (m.group(2) or "ASC").upper() == "DESC"
        text = text[:m.start()].strip()
        if order_field not in ("updated", "created", "key", "id"):
            raise PlatformError(P, 400, "invalid_jql", f"Unsupported ORDER BY {order_field}")
    preds = []
    for clause in (_SPLIT_AND.split(text) if text else []):
        c = clause.strip()
        if (mm := re.fullmatch(r"project\s*=\s*\"?([\w-]+)\"?", c, re.I)):
            want = {mm.group(1)}
            preds.append(lambda i, want=want: i["project"] in want
                         or str(w.projects.get(i["project"], {}).get("id")) in want)
        elif (mm := re.fullmatch(r"project\s+in\s*" + _LIST, c, re.I)):
            want = set(_vals(mm.group(1)))
            preds.append(lambda i, want=want: i["project"] in want
                         or str(w.projects.get(i["project"], {}).get("id")) in want)
        elif (mm := re.fullmatch(r"(?:key|issuekey|id)\s*=\s*\"?([\w-]+)\"?", c, re.I)):
            want = {mm.group(1)}
            preds.append(lambda i, want=want: i["key"] in want or str(i["id"]) in want)
        elif (mm := re.fullmatch(r"(?:key|issuekey|id)\s+in\s*" + _LIST, c, re.I)):
            want = set(_vals(mm.group(1)))
            preds.append(lambda i, want=want: i["key"] in want or str(i["id"]) in want)
        elif (mm := re.fullmatch(r"(updated|created)\s*(>=|<=|>|<|=)\s*(\"[^\"]*\"|'[^']*'|\S+)",
                                 c, re.I)):
            field, op, val = mm.group(1).lower(), mm.group(2), _jql_dt(w, mm.group(3))
            cmp = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
                   ">": lambda a, b: a > b, "<": lambda a, b: a < b,
                   "=": lambda a, b: a == b}[op]
            preds.append(lambda i, f=field, cmp=cmp, val=val:
                         (d := _issue_dt(w, i.get(f))) is not None and cmp(d, val))
        else:
            raise PlatformError(P, 400, "invalid_jql",
                                f"Error in the JQL Query: unsupported clause '{c}'")
    hits = [i for i in w.issues.values() if all(p(i) for p in preds)]
    if order_field in ("updated", "created"):
        hits.sort(key=lambda i: (_issue_dt(w, i.get(order_field)) or datetime.min.replace(
            tzinfo=w.tz), int(i["id"]) if str(i["id"]).isdigit() else 0), reverse=desc)
    elif order_field == "key":
        hits.sort(key=lambda i: (i["project"], int(i["key"].rsplit("-", 1)[-1])
                                 if i["key"].rsplit("-", 1)[-1].isdigit() else 0), reverse=desc)
    else:
        hits.sort(key=lambda i: (len(str(i["id"])), str(i["id"])), reverse=desc)
    return hits


def _search(request: Request, jql, next_page_token, max_results, fields):
    w = world(request)
    with w.lock:
        hits = run_jql(w, jql)
        page, nxt = paginate(P, f"jql:{jql}", hits, next_page_token, max_results)
        out: dict[str, Any] = {"issues": [issue_obj(w, request, i, fields) for i in page],
                               "isLast": nxt is None}
    if nxt:
        out["nextPageToken"] = nxt
    return out


@router.get("/search/jql")
def search_get(request: Request, jql: Optional[str] = None,
               nextPageToken: Optional[str] = None, maxResults: Optional[int] = None,
               fields: Optional[str] = None):
    return _search(request, jql, nextPageToken, maxResults, fields)


@router.post("/search/jql")
def search_post(request: Request, body: dict = Body(default_factory=dict)):
    fields = body.get("fields")
    if isinstance(fields, list):
        fields = ",".join(fields)
    return _search(request, body.get("jql"), body.get("nextPageToken"), body.get("maxResults"),
                   fields)


# ------------------------------------------------------------------------------------- issues
@router.get("/issue/{key_or_id}")
def get_issue(request: Request, key_or_id: str, fields: Optional[str] = None):
    w = world(request)
    with w.lock:
        return issue_obj(w, request, _issue(w, key_or_id), fields)


def _adf_to_text(node: Any) -> str:
    """Plain text of an ADF document: one paragraph per block, text nodes joined."""
    if not isinstance(node, dict):
        return ""
    paras = []
    for block in node.get("content") or []:
        if isinstance(block, dict):
            paras.append("".join(str(t.get("text", "")) for t in block.get("content") or []
                                 if isinstance(t, dict)))
    return "\n\n".join(p for p in paras if p)


def _field_error(field: str, message: str) -> PlatformError:
    err = PlatformError(P, 400, "invalid_request", message)
    err.field = field                                   # type: ignore[attr-defined]
    return err


@router.post("/issue", status_code=201)
def create_issue(request: Request, body: dict = Body(...)):
    """Jira Cloud ``POST /rest/api/3/issue`` (subset), authorised like real Jira: for the CALLER.

    The caller is the token's service account. It needs "Create issues" in the project (else 403) and
    "Modify reporter" to set ``reporter`` (else 400 on ``reporter``). The reporter's OWN permissions are
    never checked, exactly as in Jira Cloud: so the Internal Brain's live ``creatable`` re-check is the
    only thing that stops an issue for someone who may not create one (round 2b finding 3). The reporter
    must be a known, active account (else 400 on ``reporter``); with no reporter, the caller is the
    reporter. ``creator`` is always the service account."""
    w = world(request)
    f = body.get("fields") if isinstance(body, dict) else None
    if not isinstance(f, dict):
        raise _field_error("fields", "fields is required")
    proj_ref = f.get("project") if isinstance(f.get("project"), dict) else {}
    summary = f.get("summary")
    itype = (f.get("issuetype") or {}).get("name") if isinstance(f.get("issuetype"), dict) else None
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY \
            or "\n" in summary:
        raise _field_error("summary", "You must specify a summary of the issue.")
    if itype not in ISSUE_TYPES:
        raise _field_error("issuetype", "The issue type selected is invalid.")
    labels = f.get("labels") or []
    if not isinstance(labels, list) or not all(isinstance(x, str) and x and " " not in x
                                               for x in labels):
        raise _field_error("labels", "Labels must be strings without spaces.")
    with w.lock:
        try:
            proj = _project(w, str(proj_ref.get("key") or proj_ref.get("id") or ""))
        except PlatformError:
            raise _field_error("project", "valid project is required") from None
        if not w.jira_service_can_create(proj["key"]):
            raise PlatformError(P, 403, "forbidden",
                                "You do not have permission to create issues in this project.")
        rep = f.get("reporter")
        account = rep.get("accountId") if isinstance(rep, dict) else None
        uid = None
        if account:
            if not w.jira_service_can_modify_reporter():
                raise _field_error("reporter", "Field 'reporter' cannot be set. It is not on the "
                                               "appropriate screen, or unknown.")
            uid = w.ib_id(P, account)
            if uid is None or not w.active(uid):
                raise _field_error("reporter", "Specified reporter is not a user.")
        key, iid = w.next_issue(proj["key"])
        now = w.now_iso()
        issue = {"id": iid, "key": key, "project": proj["key"], "summary": summary.strip(),
                 "description": _adf_to_text(f.get("description")), "status": "To Do",
                 "issuetype": itype, "reporter": uid, "reporter_is_service": uid is None,
                 "created_by_service": True, "assignee": None, "security_level": None,
                 "labels": list(labels), "comments": [], "links": [], "created": now,
                 "updated": now}
        w.issues[key] = issue
        w.issue_by_id[iid] = key
        out = {"id": iid, "key": key, "self": f"{_base(request)}/issue/{iid}"}
    # outside the world lock: an in-process webhook sink may read the new issue back
    request.app.state.webhooks.emit(P, "content.changed", [key], at=now, user=uid)
    return out


@router.get("/securitylevel/{level_id}")
def get_security_level(request: Request, level_id: str):
    w = world(request)
    with w.lock:
        lv = w.levels.get(level_id)
        if lv is None:
            raise PlatformError(P, 404, "not_found", "Security level not found")
        return {"id": str(lv["id"]), "name": lv.get("name"), "description": "",
                "self": f"{_base(request)}/securitylevel/{lv['id']}"}


# ------------------------------------------------------------------------------------- projects
@router.get("/project/search")
def project_search(request: Request, startAt: int = 0, maxResults: int = 50):
    w = world(request)
    with w.lock:
        projs = sorted(w.projects.values(), key=lambda p: str(p["id"]))
        page = projs[startAt:startAt + maxResults]
        return {"values": [project_obj(w, request, p) for p in page], "startAt": startAt,
                "maxResults": maxResults, "total": len(projs),
                "isLast": startAt + maxResults >= len(projs)}


@router.get("/project/{key_or_id}")
def get_project(request: Request, key_or_id: str):
    w = world(request)
    with w.lock:
        return project_obj(w, request, _project(w, key_or_id))


@router.get("/project/{key_or_id}/role")
def project_roles(request: Request, key_or_id: str):
    w = world(request)
    with w.lock:
        return project_obj(w, request, _project(w, key_or_id))["roles"]


@router.get("/project/{key_or_id}/role/{role}")
def project_role(request: Request, key_or_id: str, role: str):
    w = world(request)
    with w.lock:
        p = _project(w, key_or_id)
        name = _role_name(w, role)
        if name is None or name not in (p.get("roles") or {}):
            raise PlatformError(P, 404, "not_found", f"Role '{role}' not found in project")
        users, groups = w.role_holders(p["key"], name)
        actors: list[dict[str, Any]] = []
        for n, uid in enumerate(users):
            u = w.users.get(uid, {})
            actors.append({"id": 20000 + n, "displayName": u.get("name", uid),
                           "type": "atlassian-user-role-actor",
                           "actorUser": {"accountId": w.native(P, uid)}})
        for n, g in enumerate(groups):
            gid = w.resolve_group(g) or g
            gname = w.groups.get(gid, {}).get("name", gid)
            actors.append({"id": 30000 + n, "displayName": gname, "name": gname,
                           "type": "atlassian-group-role-actor",
                           "actorGroup": {"name": gname, "displayName": gname, "groupId": gid}})
        rid = role_ids(w)[name]
        return {"self": f"{_base(request)}/project/{p['id']}/role/{rid}", "name": name,
                "id": rid, "description": "", "actors": actors}


def _holder(w: World, kind: str, value: Optional[str] = None) -> dict[str, Any]:
    if kind == "user":
        nid = w.native(P, value)
        return {"type": "user", "parameter": nid, "value": nid}
    if kind == "group":
        gid = w.resolve_group(value or "") or value
        return {"type": "group", "parameter": w.groups.get(gid, {}).get("name", gid),
                "value": gid}
    if kind == "projectRole":
        rid = str(role_ids(w).get(value or "", value))
        return {"type": "projectRole", "parameter": rid, "value": rid}
    return {"type": kind}


@router.get("/project/{key_or_id}/permissionscheme")
def project_permission_scheme(request: Request, key_or_id: str, expand: Optional[str] = None):
    w = world(request)
    with w.lock:
        return _scheme(w, request, _project(w, key_or_id))


@router.get("/permissionscheme/{scheme_id}")
def permission_scheme(request: Request, scheme_id: str, expand: Optional[str] = None):
    w = world(request)
    with w.lock:
        return _scheme(w, request, _project(w, scheme_id))


def _scheme(w: World, request: Request, p: dict) -> dict[str, Any]:
    b = p.get("browse") or {}
    holders = ([_holder(w, "projectRole", r) for r in b.get("roles", [])]
               + [_holder(w, "user", u) for u in b.get("users", [])]
               + [_holder(w, "group", g) for g in b.get("groups", [])]
               + ([_holder(w, "reporter")] if b.get("reporter") else [])
               + ([_holder(w, "assignee")] if b.get("assignee") else []))
    perms = [{"id": int(f"{p['id']}{n:02d}") if str(p["id"]).isdigit() else n,
              "holder": h, "permission": BROWSE} for n, h in enumerate(holders)]
    return {"id": int(p["id"]) if str(p["id"]).isdigit() else p["id"],
            "name": f"{p['key']} permission scheme",
            "self": f"{_base(request)}/permissionscheme/{p['id']}", "permissions": perms}


@router.get("/issuesecurityschemes/level/member")
def security_level_members(request: Request, levelId: list[str] = Query(default=[]),
                           startAt: int = 0, maxResults: int = 50):
    w = world(request)
    ids = [x for raw in levelId for x in raw.split(",") if x]
    with w.lock:
        values = []
        for lid in ids:
            lv = w.levels.get(lid)
            if lv is None:
                continue
            m = lv.get("members") or {}
            proj = w.projects.get(lv.get("project"), {})
            holders = ([_holder(w, "user", u) for u in m.get("users", [])]
                       + [_holder(w, "group", g) for g in m.get("groups", [])]
                       + [_holder(w, "projectRole", r) for r in m.get("roles", [])]
                       + ([_holder(w, "reporter")] if m.get("reporter") else [])
                       + ([_holder(w, "assignee")] if m.get("assignee") else []))
            for n, h in enumerate(holders):
                values.append({"id": f"{lid}{n:02d}", "issueSecurityLevelId": str(lid),
                               "issueSecuritySchemeId": str(proj.get("id", "")), "holder": h})
        page = values[startAt:startAt + maxResults]
        return {"values": page, "startAt": startAt, "maxResults": maxResults,
                "total": len(values), "isLast": startAt + maxResults >= len(values)}


# ------------------------------------------------------------------------------------- check
@router.post("/permissions/check")
def permissions_check(request: Request, body: dict = Body(...)):
    require_admin_role(request, P)
    account = body.get("accountId")
    if not account:
        raise PlatformError(P, 400, "invalid_request", "accountId is required")
    w = world(request)
    out = []
    with w.lock:
        uid = w.ib_id(P, account)
        for entry in body.get("projectPermissions") or []:
            issues = entry.get("issues") or []
            projects = entry.get("projects") or []
            for perm in entry.get("permissions") or []:
                allowed_issues, allowed_projects = [], []
                if perm == BROWSE and uid is not None:
                    for iid in issues:
                        key = w.issue_by_id.get(str(iid))
                        if key and w.jira_can_browse_issue(uid, key):
                            allowed_issues.append(iid)
                    for pid in projects:
                        try:
                            pk = _project(w, str(pid))["key"]
                        except PlatformError:
                            continue
                        if w.jira_can_browse_project(uid, pk):
                            allowed_projects.append(pid)
                elif perm == CREATE and uid is not None:
                    for pid in projects:
                        try:
                            pk = _project(w, str(pid))["key"]
                        except PlatformError:
                            continue
                        if w.jira_can_create(uid, pk):
                            allowed_projects.append(pid)
                out.append({"permission": perm, "issues": allowed_issues,
                            "projects": allowed_projects})
    return {"projectPermissions": out, "globalPermissions": []}
