"""Hand-written fakes for the Slack / Confluence connector unit tests (A3).

Each fake is a tiny in-memory platform answering the CONTRACTS §2 paths and shapes through
``httpx.MockTransport``. Page sizes are deliberately small so the connectors' cursor paging is
exercised. Faults (429, timeouts, 500s) are toggled per endpoint.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

import httpx

from databridge_ib.connectors._http import sign_webhook
from databridge_ib.models import DirectoryUser, UserKind

SECRET = "whsec-test"


# --------------------------------------------------------------------------------------------
# directory
# --------------------------------------------------------------------------------------------
class Directory:
    def __init__(self, users: list[DirectoryUser]):
        self._users = {u.id: u for u in users}

    def by_platform_id(self, platform: str, native_id: str) -> Optional[DirectoryUser]:
        for u in self._users.values():
            if u.platform_ids.get(platform) == native_id:
                return u
        return None

    def get(self, user_id: str) -> Optional[DirectoryUser]:
        return self._users.get(user_id)

    def all(self) -> list[DirectoryUser]:
        return list(self._users.values())


def user(uid: str, *, kind: UserKind = UserKind.MEMBER, groups: tuple[str, ...] = (),
         active: bool = True, slack: Optional[str] = None, confluence: Optional[str] = None,
         employment: str = "employee") -> DirectoryUser:
    pids = {"slack": slack or f"U0{uid.split('.')[0].upper()}",
            "confluence": confluence or f"acc-{uid}"}
    return DirectoryUser(id=uid, name=uid.title(), email=f"{uid}@companya.example",
                         employment=employment, kind=kind, active=active, groups=groups,
                         platform_ids=pids)


def people() -> list[DirectoryUser]:
    return [
        user("jdoe", groups=("grp-eng", "grp-payments-eng")),
        user("priya.sre", groups=("grp-eng", "grp-sre")),
        user("sara.sec", groups=("grp-security",)),
        user("leo.ops", groups=("grp-ops",)),
        user("ken.ext", kind=UserKind.CONNECT, employment="contractor"),
        user("gina.guest", kind=UserKind.GUEST, employment="contractor"),
    ]


def signed_headers(body: bytes, *, secret: str = SECRET, ts: Optional[int] = None,
                   delivery: Optional[str] = None) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    delivery = delivery or str(uuid.uuid4())
    return {"X-IB-Delivery": delivery, "X-IB-Timestamp": str(ts),
            "X-IB-Signature": sign_webhook(secret, ts, delivery, body)}


class _Faults:
    def __init__(self) -> None:
        self.rate_limited: set[str] = set()     # endpoint names returning 429
        self.timeout: set[str] = set()          # endpoint names raising ReadTimeout
        self.error: set[str] = set()            # endpoint names returning 500
        self.calls: list[str] = []
        self.rate_limit_once: set[str] = set()  # 429 only the first time

    def apply(self, name: str, request: httpx.Request) -> Optional[httpx.Response]:
        self.calls.append(name)
        if name in self.rate_limit_once:
            self.rate_limit_once.discard(name)
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"ok": False})
        if name in self.rate_limited:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"ok": False})
        if name in self.timeout:
            raise httpx.ReadTimeout("timed out", request=request)
        if name in self.error:
            return httpx.Response(500, json={"ok": False})
        return None


def _page(items: list[Any], cursor: str, size: int) -> tuple[list[Any], str]:
    start = int(cursor or 0)
    chunk = items[start:start + size]
    nxt = str(start + size) if start + size < len(items) else ""
    return chunk, nxt


# --------------------------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------------------------
class FakeSlack(_Faults):
    """Native ids only on the wire (identity rule)."""

    PAGE = 2

    def __init__(self, directory: Directory, *, has_info: bool = True):
        super().__init__()
        self.dir = directory
        self.has_info = has_info          # False: only the CONTRACTS §2 subset (no conversations.info)
        nat = lambda uid: directory.get(uid).platform_ids["slack"]          # noqa: E731
        self.nat = nat
        self.users: dict[str, dict[str, Any]] = {}
        for u in directory.all():
            self.users[u.platform_ids["slack"]] = {
                "id": u.platform_ids["slack"], "deleted": False,
                "is_restricted": u.kind is UserKind.GUEST, "is_ultra_restricted": False,
                "is_stranger": u.kind is UserKind.CONNECT, "profile": {"email": u.email}}
        self.channels: dict[str, dict[str, Any]] = {
            "C0PUB": {"id": "C0PUB", "name": "payments-eng", "is_private": False, "is_ext_shared": False,
                      "is_archived": False, "members": [nat("jdoe"), nat("gina.guest")]},
            "C0PRIV": {"id": "C0PRIV", "name": "db-migration", "is_private": True, "is_ext_shared": False,
                       "is_archived": False, "members": [nat("jdoe"), nat("priya.sre")]},
            "C0VEND": {"id": "C0VEND", "name": "vendor-acme", "is_private": False, "is_ext_shared": True,
                       "is_archived": False, "members": [nat("ken.ext"), nat("jdoe")]},
            "C0OLD": {"id": "C0OLD", "name": "old-incidents", "is_private": True, "is_ext_shared": False,
                      "is_archived": True, "members": [nat("priya.sre")]},
        }
        for ch in self.channels.values():          # real Slack always sends the DM flags too
            ch.setdefault("is_im", False)
            ch.setdefault("is_mpim", False)
        self.threads: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.add_thread("C0PUB", "1728370000.000100", [
            ("jdoe", "Migration status: cutover planned, see PAY-201 and "
                     "https://companya.atlassian.net/wiki/spaces/payment-gateway/pages/cf-1001"),
            ("gina.guest", "Ack, thanks")])
        self.add_thread("C0PUB", "1728370100.000200", [("jdoe", "Second public thread")])
        self.add_thread("C0PRIV", "1728370500.000200", [("priya.sre", "Blocker: CNRY-3f9a1c lock contention")])
        self.add_thread("C0VEND", "1728370600.000300", [("ken.ext", "Vendor update for acme")])
        self.add_thread("C0OLD", "1728300000.000100", [("priya.sre", "Old incident notes")])

    def add_thread(self, channel: str, ts: str, msgs: list[tuple[str, str]]) -> None:
        base = float(ts)
        out = []
        for i, (uid, text) in enumerate(msgs):
            mts = ts if i == 0 else f"{base + i:.6f}"
            out.append({"ts": mts, "user": self.nat(uid), "text": text, "thread_ts": ts})
        self.threads[(channel, ts)] = out

    def reply(self, channel: str, ts: str, uid: str, text: str) -> str:
        msgs = self.threads[(channel, ts)]
        mts = f"{float(msgs[-1]['ts']) + 1:.6f}"
        msgs.append({"ts": mts, "user": self.nat(uid), "text": text, "thread_ts": ts})
        return mts

    # handler ------------------------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith("/slack/api/"), path
        assert request.headers.get("Authorization") == "Bearer svc-test"
        name = path.rsplit("/", 1)[-1]
        fault = self.apply(name, request)
        if fault is not None:
            return fault
        q = request.url.params
        cursor = q.get("cursor", "")
        if name == "conversations.list":
            chans = [{k: v for k, v in c.items() if k != "members"} for c in self.channels.values()]
            chunk, nxt = _page(chans, cursor, self.PAGE)
            return httpx.Response(200, json={"ok": True, "channels": chunk,
                                             "response_metadata": {"next_cursor": nxt}})
        if name == "conversations.info" and self.has_info:
            ch = self.channels.get(q.get("channel", ""))
            if not ch:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            return httpx.Response(200, json={"ok": True, "channel": {k: v for k, v in ch.items()
                                                                     if k != "members"}})
        if name == "conversations.members":
            ch = self.channels.get(q.get("channel", ""))
            if not ch:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            chunk, nxt = _page(ch["members"], cursor, self.PAGE)
            return httpx.Response(200, json={"ok": True, "members": chunk,
                                             "response_metadata": {"next_cursor": nxt}})
        if name == "conversations.history":
            cid = q.get("channel", "")
            if cid not in self.channels:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            parents = []
            for (c, ts), msgs in sorted(self.threads.items()):
                if c == cid:
                    p = {k: v for k, v in msgs[0].items() if k != "thread_ts"}
                    p["reply_count"] = len(msgs) - 1
                    if len(msgs) > 1:
                        p["thread_ts"] = ts
                        p["latest_reply"] = msgs[-1]["ts"]
                    parents.append(p)
            chunk, nxt = _page(parents, cursor, self.PAGE)
            return httpx.Response(200, json={"ok": True, "messages": chunk, "has_more": bool(nxt),
                                             "response_metadata": {"next_cursor": nxt}})
        if name == "conversations.replies":
            key = (q.get("channel", ""), q.get("ts", ""))
            if key[0] not in self.channels:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            if key not in self.threads:
                return httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
            chunk, nxt = _page(self.threads[key], cursor, self.PAGE)
            return httpx.Response(200, json={"ok": True, "messages": chunk, "has_more": bool(nxt),
                                             "response_metadata": {"next_cursor": nxt}})
        if name == "users.info":
            u = self.users.get(q.get("user", ""))
            if not u:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": u})
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"})


# --------------------------------------------------------------------------------------------
# Confluence
# --------------------------------------------------------------------------------------------
class FakeConfluence(_Faults):
    PAGE = 2
    ANC_PAGE = 200                      # ancestors page size (a test drops it to 1 to force paging)

    def __init__(self, directory: Directory):
        super().__init__()
        self.dir = directory
        self.spaces: dict[str, dict[str, Any]] = {
            "sp-1": {"id": "sp-1", "key": "payment-gateway", "name": "Payment Gateway",
                     "read": {"users": [], "groups": ["grp-eng"]}, "anonymous": False},
            "sp-2": {"id": "sp-2", "key": "sec", "name": "Security",
                     "read": {"users": ["sara.sec"], "groups": []}, "anonymous": False},
            "sp-3": {"id": "sp-3", "key": "public", "name": "Public Wiki",
                     "read": {"users": [], "groups": ["grp-eng"]}, "anonymous": True},
        }
        self.pages: dict[str, dict[str, Any]] = {}
        self.add_page("cf-1001", "sp-1", None, "Gateway overview",
                      "<p>Overview. Runbook in <a href=\"https://docs.google.com/document/d/df-7/edit\">doc</a></p>")
        self.add_page("cf-1002", "sp-1", "cf-1001", "Settlement keys", "<p>Key rotation &amp; HSM</p>",
                      restriction={"users": ["jdoe", "priya.sre"], "groups": []})
        self.add_page("cf-1003", "sp-1", "cf-1002", "SRE key ceremony", "<p>Ceremony steps</p>",
                      restriction={"users": [], "groups": ["grp-sre"]})
        self.add_page("cf-1004", "sp-1", "cf-1001", "Old design", "<p>gone</p>", status="trashed")
        self.add_page("cf-2001", "sp-2", None, "Q3 breach report", "<p>Breach CNRY-aa11</p>")
        self.add_page("cf-3001", "sp-3", None, "Public FAQ", "<p>FAQ</p>")

    def acc(self, uid: str) -> str:
        return self.dir.get(uid).platform_ids["confluence"]

    def add_page(self, pid: str, space: str, parent: Optional[str], title: str, body: str, *,
                 restriction: Optional[dict[str, list[str]]] = None, status: str = "current",
                 version: int = 1) -> None:
        self.pages[pid] = {"id": pid, "space": space, "parent": parent, "title": title, "body": body,
                           "restriction": restriction, "status": status, "version": version,
                           "author": "jdoe"}

    def edit(self, pid: str, body: str) -> None:
        self.pages[pid]["body"] = body
        self.pages[pid]["version"] += 1

    # semantics used by the fake permission check ------------------------------------------
    def _ancestors(self, pid: str) -> list[str]:
        out, cur = [], self.pages[pid]["parent"]
        while cur:
            out.insert(0, cur)
            cur = self.pages[cur]["parent"]
        return out

    def can_read(self, uid: str, pid: str) -> bool:
        u = self.dir.get(uid)
        if u is None or not u.active:
            return False
        page = self.pages[pid]
        sp = self.spaces[page["space"]]

        def ok(grant):
            return uid in grant["users"] or bool(set(grant["groups"]) & set(u.groups))

        if not ok(sp["read"]):
            return False
        for p in self._ancestors(pid) + [pid]:
            r = self.pages[p]["restriction"]
            if r and (r["users"] or r["groups"]) and not ok(r):
                return False
        return True

    def _page_json(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"id": p["id"], "title": p["title"], "status": p["status"], "spaceId": p["space"],
                "parentId": p["parent"], "authorId": self.acc(p["author"]),
                "createdAt": "2026-09-01T01:00:00Z",
                "version": {"number": p["version"], "createdAt": "2026-10-01T02:00:00Z"},
                "body": {"storage": {"value": p["body"], "representation": "storage"}}}

    @staticmethod
    def _v2(items: list[Any], cursor: str, size: int, path: str) -> dict[str, Any]:
        chunk, nxt = _page(items, cursor, size)
        links = {"next": f"/wiki/{path}?cursor={nxt}"} if nxt else {}
        return {"results": chunk, "_links": links}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert path.startswith("/confluence/wiki/"), path
        sub = path[len("/confluence/wiki/"):]
        auth = request.headers.get("Authorization")
        q = request.url.params
        cursor = q.get("cursor", "")
        parts = sub.split("/")
        if sub.endswith("permission/check"):
            name = "permission/check"
        elif sub.startswith("rest/api/content/"):
            name = "restriction"
        elif sub == "api/v2/spaces":
            name = "spaces"
        elif sub.endswith("/permissions"):
            name = "space-permissions"
        elif sub.endswith("/ancestors"):
            name = "ancestors"
        elif sub == "api/v2/pages":
            name = "pages"
        else:
            name = "page"
        fault = self.apply(name, request)
        if fault is not None:
            return fault
        if name == "permission/check":
            if auth != "Bearer admin-test":
                return httpx.Response(403, json={"message": "admin only"})
            pid = parts[3]
            if pid not in self.pages:
                return httpx.Response(404, json={"message": "not found"})
            body = json.loads(request.content)
            acc = body["subject"]["identifier"]
            u = self.dir.by_platform_id("confluence", acc)
            return httpx.Response(200, json={"hasPermission": bool(u and self.can_read(u.id, pid)),
                                             "errors": []})
        assert auth == "Bearer svc-test", auth
        if name == "spaces":
            sp = [{"id": s["id"], "key": s["key"], "name": s["name"], "type": "global"}
                  for s in self.spaces.values()]
            return httpx.Response(200, json=self._v2(sp, cursor, self.PAGE, sub))
        if name == "space-permissions":
            s = self.spaces.get(parts[3])
            if not s:
                return httpx.Response(404, json={})
            perms = [{"principal": {"type": "user", "id": self.acc(uid)},
                      "operation": {"key": "read", "targetType": "space"}} for uid in s["read"]["users"]]
            perms += [{"principal": {"type": "group", "id": g},
                       "operation": {"key": "read", "targetType": "space"}} for g in s["read"]["groups"]]
            perms += [{"principal": {"type": "group", "id": "grp-eng"},
                       "operation": {"key": "create", "targetType": "page"}}]
            if s["anonymous"]:
                perms.append({"principal": {"type": "anonymous", "id": "anonymous"},
                              "operation": {"key": "read", "targetType": "space"}})
            return httpx.Response(200, json=self._v2(perms, cursor, self.PAGE, sub))
        if name == "pages":
            sid = q.get("space-id", "")
            items = [self._page_json(p) for p in self.pages.values() if p["space"] == sid]
            return httpx.Response(200, json=self._v2(items, cursor, self.PAGE, sub))
        if name == "ancestors":
            pid = parts[3]
            if pid not in self.pages:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self._v2([{"id": a, "type": "page"}
                                                      for a in self._ancestors(pid)],
                                                     cursor, self.ANC_PAGE, sub))
        if name == "restriction":
            pid = parts[3]
            if pid not in self.pages:
                return httpx.Response(404, json={})
            r = self.pages[pid]["restriction"] or {"users": [], "groups": []}
            return httpx.Response(200, json={"operation": "read", "restrictions": {
                "user": {"results": [{"accountId": self.acc(uid)} for uid in r["users"]]},
                "group": {"results": [{"name": g} for g in r["groups"]]}}})
        if name == "page":
            pid = parts[3]
            p = self.pages.get(pid)
            if not p:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self._page_json(p))
        return httpx.Response(404, json={})


def client_for(handler, token: str) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock",
                        headers={"Authorization": f"Bearer {token}"})


# --------------------------------------------------------------------------------------------
# a small CONTRACTS §1 world for the integration tests against apps/mock_platforms
# --------------------------------------------------------------------------------------------
def small_world() -> dict[str, Any]:
    def u(uid, kind="member", groups=(), employment="employee"):
        short = uid.split(".")[0]
        return {"id": uid, "name": uid.title(), "email": f"{uid}@companya.example",
                "employment": employment, "kind": kind, "active": True, "roles": ["employee"],
                "groups": list(groups),
                "platform_ids": {"slack": f"U0{short.upper()}", "confluence": f"acc-{short}",
                                 "jira": f"acc-{short}", "drive": f"{uid}@companya.example"}}

    users = [u("jdoe", groups=("grp-eng",)), u("priya.sre", groups=("grp-eng", "grp-sre")),
             u("leo.ops", groups=("grp-ops",)), u("ken.ext", kind="connect", employment="contractor"),
             u("gina.guest", kind="guest", employment="contractor")]
    groups = [{"id": "grp-eng", "name": "engineering", "members": ["jdoe", "priya.sre"], "member_groups": []},
              {"id": "grp-sre", "name": "sre", "members": ["priya.sre"], "member_groups": []},
              {"id": "grp-ops", "name": "ops", "members": ["leo.ops"], "member_groups": []}]
    t0 = "2026-10-01T09:00:00+08:00"

    def page(pid, parent, title, body, restriction=None, status="current", version=3):
        return {"id": pid, "space": "payment-gateway", "parent": parent, "title": title,
                "version": version, "status": status, "created": t0, "updated": t0, "author": "jdoe",
                "body": body, "read_restriction": restriction, "history": []}

    def thread(ch, ts, msgs):
        base = float(ts)
        return {"channel": ch, "ts": ts,
                "messages": [{"ts": ts if i == 0 else f"{base + i:.6f}", "user": who, "text": text,
                              "edited": False} for i, (who, text) in enumerate(msgs)]}

    return {
        "meta": {"seed": 7, "now": "2026-10-13T09:00:00+08:00", "tenant": "companya",
                 "domain": "companya.example", "scale": "demo", "generator_version": "1"},
        "directory": {"users": users, "groups": groups},
        "confluence": {
            "spaces": [{"id": "sp-1", "key": "payment-gateway", "name": "Payment Gateway",
                        "read": {"users": [], "groups": ["grp-eng"]}, "anonymous": False},
                       {"id": "sp-2", "key": "ops", "name": "Ops",
                        "read": {"users": ["leo.ops"], "groups": []}, "anonymous": False}],
            "pages": [page("cf-1001", None, "Gateway overview", "Overview of the gateway. See PAY-201."),
                      page("cf-1002", "cf-1001", "Settlement keys", "Key rotation",
                           {"users": ["jdoe", "priya.sre"], "groups": []}),
                      page("cf-1003", "cf-1002", "SRE key ceremony", "Ceremony",
                           {"users": [], "groups": ["grp-sre"]}),
                      page("cf-1004", "cf-1001", "Old design", "gone", status="trashed"),
                      {**page("cf-2001", None, "Ops handbook", "Ops"), "space": "ops"}],
        },
        "jira": {"projects": [], "security_levels": [], "issues": []},
        "slack": {
            "workspace": {"id": "T0COMPA", "domain": "companya"},
            "channels": [
                {"id": "C0PUB", "name": "payments-eng", "type": "public",
                 "members": ["jdoe", "gina.guest"], "archived": False, "created": t0},
                {"id": "C0PRIV", "name": "db-migration", "type": "private",
                 "members": ["jdoe", "priya.sre"], "archived": False, "created": t0},
                {"id": "C0VEND", "name": "vendor-acme", "type": "connect",
                 "members": ["ken.ext", "jdoe"], "archived": False, "created": t0},
                {"id": "C0OLD", "name": "old-incidents", "type": "private",
                 "members": ["priya.sre"], "archived": True, "created": t0}],
            "threads": [thread("C0PUB", "1728370000.000100", [("jdoe", "Cutover see PAY-201"),
                                                             ("gina.guest", "ack")]),
                        thread("C0PRIV", "1728370500.000200", [("priya.sre", "Blocker CNRY-1")]),
                        thread("C0VEND", "1728370600.000300", [("ken.ext", "Vendor update")]),
                        thread("C0OLD", "1728300000.000100", [("priya.sre", "Old notes")])],
        },
        "drive": {"drives": [], "folders": [], "files": []},
        "facts": [], "questions": [], "audit_replay": [],
    }


def directory_from_world(world: dict[str, Any]) -> Directory:
    out = []
    for u in world["directory"]["users"]:
        out.append(DirectoryUser(id=u["id"], name=u["name"], email=u["email"],
                                 employment=u["employment"], kind=UserKind(u["kind"]),
                                 active=u["active"], roles=tuple(u["roles"]), groups=tuple(u["groups"]),
                                 platform_ids=dict(u["platform_ids"])))
    return Directory(out)


class MockEnv:
    """``mock_platforms.app.create_app`` over a temp world, with sync clients and a webhook sink."""

    def __init__(self, tmp_path, create_app, *, svc: str = "svc-it", admin: str = "admin-it",
                 secret: str = SECRET):
        from fastapi.testclient import TestClient
        self.world = small_world()
        path = tmp_path / "world.json"
        path.write_text(json.dumps(self.world), encoding="utf-8")
        self.hooks: list[tuple[str, dict[str, str], bytes]] = []
        self.app = create_app(path, webhook_sink=lambda p, h, b: self.hooks.append((p, h, b)),
                              svc_token=svc, admin_token=admin, webhook_secret=secret)
        self.svc = TestClient(self.app, base_url="http://mock", headers={"Authorization": f"Bearer {svc}"})
        self.admin = TestClient(self.app, base_url="http://mock",
                                headers={"Authorization": f"Bearer {admin}"})
        self.directory = directory_from_world(self.world)

    def admin_post(self, path: str, body: Optional[dict] = None) -> dict[str, Any]:
        r = self.admin.post(path, json=body or {})
        assert r.status_code == 200, (path, r.status_code, r.text)
        return r.json()
