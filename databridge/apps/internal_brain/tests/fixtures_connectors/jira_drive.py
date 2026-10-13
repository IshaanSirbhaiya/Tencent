"""Hand-written fakes for the Jira / Drive connector unit tests (B1), plus a CONTRACTS §1 world with
Jira and Drive content for the integration tests against ``apps/mock_platforms``.

Same discipline as the Slack / Confluence fakes: tiny in-memory platforms answering the real
paths and shapes through ``httpx.MockTransport``, small page sizes so paging is exercised, faults
toggled per endpoint. Each fake also carries an INDEPENDENT reference implementation of the
permission semantics (``can_browse`` / ``ib_can_read``) the tests compare the connectors against.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from databridge_ib.models import DirectoryUser, UserKind

from . import SECRET, Directory, _Faults, _page, directory_from_world, small_world

COMPANY = "companya.example"


# --------------------------------------------------------------------------------------------
# directory
# --------------------------------------------------------------------------------------------
def person(uid: str, *, groups: tuple[str, ...] = (), kind: UserKind = UserKind.MEMBER,
           domain: str = COMPANY, employment: str = "employee", active: bool = True) -> DirectoryUser:
    email = f"{uid}@{domain}"
    return DirectoryUser(id=uid, name=uid.replace(".", " ").title(), email=email,
                         employment=employment, kind=kind, active=active, groups=groups,
                         platform_ids={"jira": f"acc-{uid}", "drive": email,
                                       "slack": f"U0{uid.split('.')[0].upper()}",
                                       "confluence": f"acc-{uid}"})


def jd_people() -> list[DirectoryUser]:
    return [
        person("jdoe", groups=("grp-eng", "grp-payments-eng")),
        person("priya.sre", groups=("grp-eng", "grp-sre")),
        person("sara.sec", groups=("grp-security",)),
        person("leo.ops", groups=("grp-ops",)),
        person("mia.pm", groups=("grp-product",)),
        person("ken.ext", kind=UserKind.CONNECT, domain="partner.example", employment="contractor"),
    ]


def adf(text: Optional[str]) -> dict[str, Any]:
    paras = [p for p in (text or "").split("\n\n") if p.strip()]
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paras]}


# --------------------------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------------------------
class FakeJira(_Faults):
    PAGE = 2
    BASE = datetime.fromisoformat("2026-10-10T10:00:00+08:00")

    def __init__(self, directory: Directory):
        super().__init__()
        self.dir = directory
        self.projects: dict[str, dict[str, Any]] = {
            "PAY": {"id": "10001", "key": "PAY", "name": "Payments",
                    "roles": {"Developers": {"users": [], "groups": ["grp-payments-eng"]},
                              "Viewers": {"users": ["leo.ops"], "groups": []}},
                    "browse": {"roles": ["Developers", "Viewers"], "users": [], "groups": [],
                               "reporter": True, "assignee": True, "extra": []}},
            "SEC": {"id": "10002", "key": "SEC", "name": "Security",
                    "roles": {"Responders": {"users": [], "groups": ["grp-security"]}},
                    "browse": {"roles": ["Responders"], "users": [], "groups": [],
                               "reporter": False, "assignee": False, "extra": []}},
        }
        self.role_ids = {"Developers": "10002", "Viewers": "10003", "Responders": "10004"}
        self.levels: dict[str, dict[str, Any]] = {
            "10100": {"project": "PAY", "name": "Security only",
                      "members": {"users": [], "groups": ["grp-security"], "roles": [],
                                  "reporter": True, "assignee": False, "extra": []}},
            "10101": {"project": "PAY", "name": "Developers only",
                      "members": {"users": [], "groups": [], "roles": ["Developers"],
                                  "reporter": False, "assignee": False, "extra": []}},
        }
        self.issues: dict[str, dict[str, Any]] = {}
        self._tick = 0
        self.add_issue("PAY-1", "20001", "Migrate ledger DB",
                       "Move the ledger to the new cluster.\n\nRunbook: "
                       "https://companya.atlassian.net/wiki/spaces/pg/pages/cf-1001 and SEC-1",
                       reporter="jdoe", assignee="priya.sre",
                       comments=[("priya.sre", "Replica lag is fine.")], links=["PAY-3"])
        self.add_issue("PAY-2", "20002", "Card data exposure review", "Security review CNRY-77",
                       reporter="mia.pm", assignee="jdoe", level="10100")
        self.add_issue("PAY-3", "20003", "Rotate settlement keys", "Key rotation steps",
                       reporter="leo.ops", assignee=None, level="10101")
        self.add_issue("PAY-4", "20004", "Vendor SLA follow-up", "Acme SLA", reporter="ken.ext",
                       assignee=None)
        self.add_issue("SEC-1", "20101", "Pen-test findings", "Findings list", reporter="sara.sec",
                       assignee="jdoe")

    # mutation helpers ---------------------------------------------------------------------
    def _now(self) -> str:
        self._tick += 1
        return (self.BASE + timedelta(minutes=10 * self._tick)).isoformat()

    def add_issue(self, key: str, iid: str, summary: str, description: str, *, reporter: str,
                  assignee: Optional[str], level: Optional[str] = None,
                  comments: Optional[list[tuple[str, str]]] = None,
                  links: Optional[list[str]] = None, status: str = "In Progress") -> None:
        t = self._now()
        self.issues[key] = {"id": iid, "key": key, "project": key.split("-")[0], "summary": summary,
                            "description": description, "status": status, "reporter": reporter,
                            "assignee": assignee, "security_level": level, "created": t,
                            "updated": t, "comments": [{"author": a, "body": b, "created": t}
                                                       for a, b in (comments or [])],
                            "links": list(links or [])}

    def edit(self, key: str, **fields: Any) -> None:
        self.issues[key].update(fields)
        self.issues[key]["updated"] = self._now()

    # independent reference semantics --------------------------------------------------------
    def _in(self, uid: str, users: list[str], groups: list[str]) -> bool:
        u = self.dir.get(uid)
        return uid in users or bool(set(groups) & set(u.groups))

    def _in_roles(self, uid: str, project: str, roles: list[str]) -> bool:
        return any(self._in(uid, **self.projects[project]["roles"][r]) for r in roles)

    def can_browse(self, uid: str, key: str) -> bool:
        u = self.dir.get(uid)
        i = self.issues.get(key)
        if u is None or not u.active or i is None:
            return False
        b = self.projects[i["project"]]["browse"]
        ok = (self._in_roles(uid, i["project"], b["roles"]) or self._in(uid, b["users"], b["groups"])
              or (b["reporter"] and i["reporter"] == uid) or (b["assignee"] and i["assignee"] == uid))
        if not ok:
            return False
        if i["security_level"] is None:
            return True
        m = self.levels[i["security_level"]]["members"]
        return (self._in(uid, m["users"], m["groups"]) or self._in_roles(uid, i["project"], m["roles"])
                or (m["reporter"] and i["reporter"] == uid) or (m["assignee"] and i["assignee"] == uid))

    # wire shapes ------------------------------------------------------------------------------
    def acc(self, uid: Optional[str]) -> Optional[str]:
        return None if uid is None else self.dir.get(uid).platform_ids["jira"]

    def _uref(self, uid: Optional[str]) -> Optional[dict[str, Any]]:
        if uid is None:
            return None
        return {"accountId": self.acc(uid), "displayName": self.dir.get(uid).name, "active": True}

    def issue_json(self, i: dict[str, Any], fields: str = "*all") -> dict[str, Any]:
        p = self.projects[i["project"]]
        lvl = i["security_level"]
        f = {"summary": i["summary"], "description": adf(i["description"]),
             "status": {"name": i["status"]},
             "project": {"id": p["id"], "key": p["key"], "name": p["name"]},
             "reporter": self._uref(i["reporter"]), "assignee": self._uref(i["assignee"]),
             "security": {"id": lvl, "name": self.levels.get(lvl, {}).get("name")} if lvl else None,
             "created": i["created"], "updated": i["updated"],
             "comment": {"comments": [{"author": self._uref(c["author"]), "body": adf(c["body"]),
                                       "created": c["created"]} for c in i["comments"]]},
             "issuelinks": [{"type": {"name": "Relates", "outward": "relates to",
                                      "inward": "relates to"}, "outwardIssue": {"key": k}}
                            for k in i["links"]]}
        if fields not in ("*all", "*navigable"):
            want = {x.strip() for x in fields.split(",")}
            f = {k: v for k, v in f.items() if k in want}
        return {"id": i["id"], "key": i["key"], "fields": f}

    def _holders(self, project: str, spec: dict[str, Any], *, role_key: str = "roles") -> list[dict]:
        out: list[dict[str, Any]] = []
        out += [{"type": "projectRole", "parameter": self.role_ids[r]} for r in spec.get(role_key, [])]
        out += [{"type": "user", "parameter": self.acc(u), "value": self.acc(u)} for u in spec["users"]]
        out += [{"type": "group", "parameter": f"name of {g}", "value": g} for g in spec["groups"]]
        if spec["reporter"]:
            out.append({"type": "reporter"})
        if spec["assignee"]:
            out.append({"type": "assignee"})
        return out + list(spec.get("extra", []))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith("/jira/rest/api/3/"), path
        sub = path[len("/jira/rest/api/3/"):]
        parts = sub.split("/")
        q = request.url.params
        auth = request.headers.get("Authorization")
        if sub == "permissions/check":
            name = "check"
        elif sub == "search/jql":
            name = "search"
        elif sub.startswith("issue/"):
            name = "issue"
        elif sub.endswith("/permissionscheme"):
            name = "scheme"
        elif "/role/" in sub:
            name = "role"
        elif sub.startswith("issuesecurityschemes/level/member"):
            name = "level"
        else:
            name = "other"
        fault = self.apply(name, request)
        if fault is not None:
            return fault
        if name == "check":
            if auth != "Bearer admin-test":
                return httpx.Response(403, json={"errorMessages": ["admin only"]})
            body = json.loads(request.content)
            u = self.dir.by_platform_id("jira", body["accountId"])
            out = []
            for e in body["projectPermissions"]:
                allowed = []
                for iid in e["issues"]:
                    key = next((k for k, i in self.issues.items() if str(i["id"]) == str(iid)), None)
                    if u and key and self.can_browse(u.id, key):
                        allowed.append(iid)
                for perm in e["permissions"]:
                    out.append({"permission": perm, "issues": allowed, "projects": []})
            return httpx.Response(200, json={"projectPermissions": out, "globalPermissions": []})
        assert auth == "Bearer svc-test", auth
        if name == "search":
            jql = q.get("jql", "")
            hits = list(self.issues.values())
            import re
            m = re.search(r'updated\s*>=\s*"([^"]+)"', jql)
            if m:
                since = datetime.fromisoformat(m.group(1))
                hits = [i for i in hits if datetime.fromisoformat(i["updated"]) >= since]
            hits.sort(key=lambda i: (i["updated"], i["key"]))
            size = int(q.get("maxResults", self.PAGE))
            chunk, nxt = _page(hits, q.get("nextPageToken", ""), min(size, self.PAGE))
            body = {"issues": [self.issue_json(i, q.get("fields", "*all")) for i in chunk],
                    "isLast": not nxt}
            if nxt:
                body["nextPageToken"] = nxt
            return httpx.Response(200, json=body)
        if name == "issue":
            i = self.issues.get(parts[1]) or next((x for x in self.issues.values()
                                                  if x["id"] == parts[1]), None)
            if i is None:
                return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
            return httpx.Response(200, json=self.issue_json(i, q.get("fields", "*all")))
        if name == "scheme":
            p = self.projects.get(parts[1])
            if p is None:
                return httpx.Response(404, json={})
            perms = [{"id": n, "holder": h, "permission": "BROWSE_PROJECTS"}
                     for n, h in enumerate(self._holders(p["key"], p["browse"]))]
            perms.append({"id": 999, "holder": {"type": "group", "value": "grp-ops"},
                          "permission": "EDIT_ISSUES"})                  # not Browse: ignored
            return httpx.Response(200, json={"id": int(p["id"]), "permissions": perms})
        if name == "role":
            p = self.projects.get(parts[1])
            role = next((r for r, rid in self.role_ids.items() if rid == parts[3]), None)
            if p is None or role not in p["roles"]:
                return httpx.Response(404, json={})
            r = p["roles"][role]
            actors = [{"type": "atlassian-user-role-actor", "actorUser": {"accountId": self.acc(u)}}
                      for u in r["users"]]
            actors += [{"type": "atlassian-group-role-actor",
                        "actorGroup": {"name": f"name of {g}", "groupId": g}} for g in r["groups"]]
            actors += list(r.get("extra", []))
            return httpx.Response(200, json={"id": int(parts[3]), "name": role, "actors": actors})
        if name == "level":
            lid = q.get("levelId", "")
            lv = self.levels.get(lid)
            if lv is None:
                return httpx.Response(200, json={"values": [], "isLast": True, "startAt": 0})
            vals = [{"id": n, "issueSecurityLevelId": lid, "holder": h}
                    for n, h in enumerate(self._holders(lv["project"], lv["members"]))]
            start = int(q.get("startAt", 0))
            chunk = vals[start:start + self.PAGE]
            return httpx.Response(200, json={"values": chunk, "startAt": start,
                                             "isLast": start + self.PAGE >= len(vals)})
        return httpx.Response(404, json={})


# --------------------------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------------------------
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
FOLDER = "application/vnd.google-apps.folder"


class FakeDrive(_Faults):
    PAGE = 2

    def __init__(self, directory: Directory):
        super().__init__()
        self.dir = directory
        self.drives = {"0AEng": {"name": "Engineering", "members": {"users": [], "groups": ["grp-eng"]}},
                       "0ACo": {"name": "Company", "members": {"users": [], "groups": []}}}
        self.items: dict[str, dict[str, Any]] = {}
        self.changes: list[dict[str, Any]] = []
        self.folder("fd-1", "0AEng", None, "Postmortems", [])
        self.folder("fd-2", "0AEng", "fd-1", "Security (limited)",
                    [("pf-2", "group", "grp-security")], limited=True)
        self.folder("fd-3", "0ACo", None, "Policies", [("pf-3", "domain", COMPANY)])
        self.folder("fd-4", "0ACo", "fd-3", "Ops", [("pf-4", "user", "leo.ops")])
        self.folder("fd-5", "0ACo", None, "Product", [("pf-5", "group", "grp-product")])
        self.file("df-1", "fd-1", "Outage RCA", "text/plain",
                  "Root cause: cert expiry. Follow-ups PAY-1, see "
                  "https://docs.google.com/document/d/df-2/edit")
        self.file("df-2", "fd-2", "Breach evidence", GDOC, "Evidence CNRY-2222",
                  [("p-2", "user", "priya.sre")])
        self.file("df-3", "fd-3", "Pricing sheet", GSHEET, "market,price\nSG,0.35%")
        self.file("df-4", "fd-4", "Ops notes", "text/markdown", "# Ops\nrunbook")
        self.file("df-5", "fd-1", "Public summary", GDOC, "Summary", [("p-5", "anyone", None)])
        self.file("df-6", "fd-5", "Logo", "image/png", "\x89PNG")
        self.file("df-7", "fd-5", "Launch plan", "text/csv", "a,b",
                  [("p-7", "user", "mia.pm", "2026-12-31T00:00:00Z")])
        self.file("df-8", "fd-5", "Partner brief", "text/plain", "brief",
                  [("p-8", "domain", "partner.example")])

    # mutation helpers ---------------------------------------------------------------------
    @staticmethod
    def _perm(spec: tuple) -> dict[str, Any]:
        pid, t, principal = spec[0], spec[1], spec[2]
        p = {"id": pid, "type": t, "principal": principal, "role": "reader"}
        if len(spec) > 3:
            p["expirationTime"] = spec[3]
        return p

    def folder(self, fid: str, drive: str, parent: Optional[str], name: str, perms: list[tuple],
               *, limited: bool = False) -> None:
        self.items[fid] = {"id": fid, "drive": drive, "parent": parent, "name": name, "mime": FOLDER,
                           "perms": [self._perm(p) for p in perms], "limited": limited,
                           "version": 1, "trashed": False, "content": None}

    def file(self, fid: str, folder: Optional[str], name: str, mime: str, content: str,
             perms: Optional[list[tuple]] = None, *, record: bool = False) -> None:
        drive = self.items[folder]["drive"] if folder else "0AEng"
        self.items[fid] = {"id": fid, "drive": drive, "parent": folder, "name": name, "mime": mime,
                           "perms": [self._perm(p) for p in perms or []], "limited": False,
                           "version": 1, "trashed": False, "content": content}
        if record:
            self.record(fid)

    def record(self, fid: str, removed: bool = False) -> None:
        self.changes.append({"fileId": fid, "removed": removed})

    def edit(self, fid: str, content: str) -> None:
        self.items[fid]["content"] = content
        self.items[fid]["version"] += 1
        self.record(fid)

    def remove_perm(self, fid: str, pid: str) -> None:
        """Real Drive reports the item itself; the connector must expand folders on its own."""
        self.items[fid]["perms"] = [p for p in self.items[fid]["perms"] if p["id"] != pid]
        self.record(fid)

    def trash(self, fid: str) -> None:
        self.items[fid]["trashed"] = True
        self.record(fid)

    # semantics ------------------------------------------------------------------------------
    def effective(self, fid: str) -> list[tuple[dict[str, Any], bool, str]]:
        """(perm, inherited, permissionType) — item, then parents until a limited folder, then the
        shared-drive membership."""
        it = self.items[fid]
        out = [(p, False, "file") for p in it["perms"]]
        if it["limited"]:
            return out
        cur = it["parent"]
        while cur:
            f = self.items[cur]
            out += [(p, True, "file") for p in f["perms"]]
            if f["limited"]:
                return out
            cur = f["parent"]
        m = self.drives[it["drive"]]["members"]
        out += [({"id": f"member-{u}", "type": "user", "principal": u, "role": "organizer"}, True,
                 "member") for u in m["users"]]
        out += [({"id": f"member-{g}", "type": "group", "principal": g, "role": "organizer"}, True,
                 "member") for g in m["groups"]]
        return out

    def ib_can_read(self, uid: str, fid: str) -> bool:
        """IB policy: anyone-link or expiring grant ⇒ nobody; domain only for the company domain."""
        u = self.dir.get(uid)
        if u is None or not u.active:
            return False
        perms = [p for p, _, _ in self.effective(fid)]
        if any(p["type"] == "anyone" or p.get("expirationTime") for p in perms):
            return False
        for p in perms:
            if p["type"] == "user" and p["principal"] == uid:
                return True
            if p["type"] == "group" and p["principal"] in u.groups:
                return True
            if p["type"] == "domain" and p["principal"] == COMPANY and u.domain == COMPANY:
                return True
        return False

    # wire shapes ------------------------------------------------------------------------------
    def meta(self, it: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "drive#file", "id": it["id"], "name": it["name"], "mimeType": it["mime"],
                "parents": [it["parent"] or it["drive"]], "driveId": it["drive"],
                "version": str(it["version"]), "createdTime": "2026-09-01T02:00:00Z",
                "modifiedTime": f"2026-10-0{min(it['version'], 9)}T02:00:00Z",
                "webViewLink": f"https://docs.google.com/document/d/{it['id']}/edit",
                "inheritedPermissionsDisabled": it["limited"], "trashed": it["trashed"],
                "lastModifyingUser": {"emailAddress": f"jdoe@{COMPANY}"}}

    def perm_json(self, p: dict[str, Any], inherited: bool, ptype: str) -> dict[str, Any]:
        out = {"kind": "drive#permission", "id": p["id"], "type": p["type"], "role": p["role"],
               "permissionDetails": [{"permissionType": ptype, "role": p["role"],
                                      "inherited": inherited}]}
        if p["type"] == "user":
            out["emailAddress"] = self.dir.get(p["principal"]).platform_ids["drive"]
        elif p["type"] == "group":
            out["emailAddress"] = f"{p['principal']}@{COMPANY}"
        elif p["type"] == "domain":
            out["domain"] = p["principal"]
        if p.get("expirationTime"):
            out["expirationTime"] = p["expirationTime"]
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith("/drive/v3/"), path
        assert request.headers.get("Authorization") == "Bearer svc-test"
        sub = path[len("/drive/v3/"):]
        parts = sub.split("/")
        q = request.url.params
        if sub == "files":
            name = "list"
        elif sub.startswith("changes"):
            name = "changes"
        elif sub.startswith("drives/"):
            name = "drives"
        elif sub.endswith("/permissions"):
            name = "permissions"
        elif sub.endswith("/export"):
            name = "export"
        elif q.get("alt") == "media":
            name = "media"
        else:
            name = "get"
        fault = self.apply(name, request)
        if fault is not None:
            return fault
        notfound = httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
        if name == "drives":
            d = self.drives.get(parts[1])
            return httpx.Response(200, json={"id": parts[1], "name": d["name"]}) if d else notfound
        if name == "list":
            items = sorted(self.items.values(), key=lambda x: x["id"])
            for clause in (q.get("q") or "").split(" and "):
                c = clause.strip()
                if c == "trashed = false":
                    items = [i for i in items if not i["trashed"]]
                elif c.endswith(" in parents"):
                    pid = c.split("'")[1]
                    items = [i for i in items if (i["parent"] or i["drive"]) == pid]
                elif c:
                    return httpx.Response(400, json={"error": {"message": f"bad q {c}"}})
            chunk, nxt = _page(items, q.get("pageToken", ""), self.PAGE)
            body = {"files": [self.meta(i) for i in chunk]}
            if nxt:
                body["nextPageToken"] = nxt
            return httpx.Response(200, json=body)
        if name == "changes":
            if parts[-1] == "startPageToken":
                return httpx.Response(200, json={"startPageToken": str(len(self.changes) + 1)})
            start = int(q["pageToken"]) - 1
            chunk = self.changes[start:start + self.PAGE]
            out = []
            for c in chunk:
                it = self.items.get(c["fileId"])
                removed = c["removed"] or it is None or it["trashed"]
                ch = {"changeType": "file", "fileId": c["fileId"], "removed": removed}
                if not removed:
                    ch["file"] = self.meta(it)
                out.append(ch)
            body = {"changes": out}
            if start + self.PAGE < len(self.changes):
                body["nextPageToken"] = str(start + self.PAGE + 1)
            else:
                body["newStartPageToken"] = str(len(self.changes) + 1)
            return httpx.Response(200, json=body)
        it = self.items.get(parts[1])
        if it is None:
            return notfound
        if name == "get":
            return httpx.Response(200, json=self.meta(it))
        if name == "permissions":
            perms = [self.perm_json(p, inh, pt) for p, inh, pt in self.effective(it["id"])]
            chunk, nxt = _page(perms, q.get("pageToken", ""), self.PAGE)
            body = {"permissions": chunk}
            if nxt:
                body["nextPageToken"] = nxt
            return httpx.Response(200, json=body)
        if name == "export":
            want = {GDOC: ("text/plain",), GSHEET: ("text/csv", "text/plain")}.get(it["mime"])
            if want is None:
                return httpx.Response(403, json={"error": {"message": "fileNotExportable"}})
            if q.get("mimeType") not in want:
                return httpx.Response(400, json={"error": {"message": "badRequest"}})
            return httpx.Response(200, text=it["content"])
        if name == "media":
            if it["mime"].startswith("application/vnd.google-apps"):
                return httpx.Response(403, json={"error": {"message": "fileNotDownloadable"}})
            return httpx.Response(200, text=it["content"])
        return httpx.Response(404, json={})


# --------------------------------------------------------------------------------------------
# integration world: small_world() + Jira + Drive
# --------------------------------------------------------------------------------------------
def jira_drive_world() -> dict[str, Any]:
    w = copy.deepcopy(small_world())
    users = w["directory"]["users"]
    template = users[0]
    for uid, groups in (("sara.sec", ["grp-security"]), ("mia.pm", ["grp-product"])):
        short = uid.split(".")[0]
        users.append({**copy.deepcopy(template), "id": uid, "name": uid.title(),
                      "email": f"{uid}@{COMPANY}", "groups": groups,
                      "platform_ids": {"slack": f"U0{short.upper()}", "confluence": f"acc-{short}",
                                       "jira": f"acc-{short}", "drive": f"{uid}@{COMPANY}"}})
    w["directory"]["groups"] += [
        {"id": "grp-security", "name": "security", "members": ["sara.sec"], "member_groups": []},
        {"id": "grp-product", "name": "product", "members": ["mia.pm"], "member_groups": []}]
    t = "2026-10-0{}T10:00:00+08:00"

    def issue(iid, key, summary, reporter, assignee, level=None, n=1, comments=(), links=()):
        return {"id": iid, "key": key, "project": key.split("-")[0], "summary": summary,
                "description": f"{summary} details.\n\nSecond paragraph for {key}.",
                "status": "In Progress", "reporter": reporter, "assignee": assignee,
                "security_level": level, "created": t.format(1), "updated": t.format(n),
                "comments": [{"author": a, "created": t.format(n), "body": b} for a, b in comments],
                "links": list(links)}

    w["jira"] = {
        "projects": [
            {"id": "10001", "key": "PAY", "name": "Payments",
             "roles": {"Developers": {"users": [], "groups": ["grp-eng"]},
                       "Viewers": {"users": ["leo.ops"], "groups": []}},
             "browse": {"roles": ["Developers", "Viewers"], "users": [], "groups": [],
                        "reporter": True, "assignee": True}},
            {"id": "10002", "key": "SEC", "name": "Security",
             "roles": {"Responders": {"users": [], "groups": ["grp-security"]}},
             "browse": {"roles": ["Responders"], "users": [], "groups": [],
                        "reporter": False, "assignee": False}}],
        "security_levels": [
            {"id": "10100", "project": "PAY", "name": "Security only",
             "members": {"users": [], "groups": ["grp-security"], "roles": [], "reporter": True,
                         "assignee": False}},
            {"id": "10101", "project": "PAY", "name": "Developers only",
             "members": {"users": [], "groups": [], "roles": ["Developers"], "reporter": False,
                         "assignee": False}}],
        "issues": [
            issue("20001", "PAY-1", "Migrate ledger DB", "jdoe", "priya.sre", n=2,
                  comments=[("priya.sre", "Replica lag is fine.")], links=["PAY-3"]),
            issue("20002", "PAY-2", "Card data exposure review", "mia.pm", "jdoe", "10100", n=3),
            issue("20003", "PAY-3", "Rotate settlement keys", "leo.ops", None, "10101", n=4),
            issue("20004", "PAY-4", "Vendor SLA follow-up", "ken.ext", None, n=5),
            issue("20101", "SEC-1", "Pen-test findings", "sara.sec", "jdoe", n=6)],
    }

    def fold(fid, drive, parent, name, perms, limited=False):
        return {"id": fid, "drive": drive, "parent": parent, "name": name, "permissions": perms,
                "inherited_permissions_disabled": limited}

    def f(fid, folder, drive, name, mime, content, perms=()):
        return {"id": fid, "drive": drive, "folder": folder, "name": name, "mimeType": mime,
                "version": 2, "created": t.format(1), "modified": t.format(2), "content": content,
                "permissions": list(perms)}

    def p(pid, typ, principal):
        return {"id": pid, "type": typ, "principal": principal, "role": "reader"}

    w["drive"] = {
        "drives": [{"id": "0AEng", "name": "Engineering", "members": {"users": [], "groups": ["grp-eng"]}},
                   {"id": "0ACo", "name": "Company", "members": {"users": [], "groups": []}}],
        "folders": [fold("fd-1", "0AEng", None, "Postmortems", []),
                    fold("fd-2", "0AEng", "fd-1", "Security", [p("pf-2", "group", "grp-security")],
                         limited=True),
                    fold("fd-3", "0ACo", None, "Policies", [p("pf-3", "domain", COMPANY)]),
                    fold("fd-4", "0ACo", "fd-3", "Ops", [p("pf-4", "user", "leo.ops")]),
                    fold("fd-5", "0ACo", None, "Product", [p("pf-5", "group", "grp-product")])],
        "files": [f("df-1", "fd-1", "0AEng", "Outage RCA", "text/plain", "Root cause. See PAY-1."),
                  f("df-2", "fd-2", "0AEng", "Breach evidence", GDOC, "Evidence CNRY-2222",
                    [p("p-2", "user", "priya.sre")]),
                  f("df-3", "fd-3", "0ACo", "Handbook", GDOC, "Company handbook"),
                  f("df-4", "fd-4", "0ACo", "Ops notes", "text/plain", "Ops runbook"),
                  f("df-5", "fd-1", "0AEng", "Public summary", GDOC, "Summary",
                    [{"id": "p-5", "type": "anyone", "principal": None, "role": "reader"}]),
                  f("df-6", "fd-5", "0ACo", "Roadmap", "text/plain", "Roadmap"),
                  f("df-7", "fd-5", "0ACo", "Logo", "image/png", "png")],
    }
    return w


class JiraDriveMockEnv:
    """``mock_platforms.app.create_app`` over ``jira_drive_world()`` (mirrors ``MockEnv``)."""

    def __init__(self, tmp_path, create_app, *, svc: str = "svc-it", admin: str = "admin-it",
                 secret: str = SECRET):
        from fastapi.testclient import TestClient
        self.world = jira_drive_world()
        path = tmp_path / "world.json"
        path.write_text(json.dumps(self.world), encoding="utf-8")
        self.hooks: list[tuple[str, dict[str, str], bytes]] = []
        self.app = create_app(path, webhook_sink=lambda pl, h, b: self.hooks.append((pl, h, b)),
                              svc_token=svc, admin_token=admin, webhook_secret=secret)
        self.svc = TestClient(self.app, base_url="http://mock", headers={"Authorization": f"Bearer {svc}"})
        self.admin = TestClient(self.app, base_url="http://mock",
                                headers={"Authorization": f"Bearer {admin}"})
        self.directory = directory_from_world(self.world)

    def admin_post(self, path: str, body: Optional[dict] = None) -> dict[str, Any]:
        r = self.admin.post(path, json=body or {})
        assert r.status_code == 200, (path, r.status_code, r.text)
        return r.json()
