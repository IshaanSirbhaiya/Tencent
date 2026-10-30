"""Mutable in-memory state loaded from ``world.json`` (CONTRACTS §1), plus directory helpers.

Every person-valued field in the world holds an IB user id; the routers translate to and from
native ids (``platform_ids``) with ``native()`` / ``ib_id()``. Group membership is expanded
transitively through ``member_groups`` (nested groups) — the permission computations below always
use the expanded view.

Permission semantics (the ones the mock *computes* for its permission-check endpoints) live here so
they are next to the data they read:

* Confluence: space read grant AND every read restriction on the page and its ancestors.
* Jira: Browse (role actors + users/groups + reporter/assignee) AND issue security level membership.
* Drive: ``drive_permissions`` — direct + inherited entries, stopping at a limited-access folder.

A deactivated user (``active: false``) never has permission anywhere.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

PLATFORMS = ("slack", "confluence", "jira", "drive")
FOLDER_MIME = "application/vnd.google-apps.folder"


class World:
    def __init__(self, world_path: str | Path) -> None:
        self.path = Path(world_path)
        self.lock = threading.RLock()
        self.ratelimited: dict[str, bool] = {p: False for p in PLATFORMS}
        self.reload()

    # ------------------------------------------------------------------ loading
    def reload(self) -> None:
        with self.lock:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.load(raw)

    def load(self, raw: dict[str, Any]) -> None:
        with self.lock:
            self.raw = copy.deepcopy(raw)
            w = self.raw
            self.meta: dict[str, Any] = w.get("meta", {})
            d = w.get("directory", {})
            self.users: dict[str, dict] = {u["id"]: u for u in d.get("users", [])}
            self.groups: dict[str, dict] = {g["id"]: g for g in d.get("groups", [])}
            self.group_by_name: dict[str, str] = {g.get("name", g["id"]): g["id"]
                                                  for g in self.groups.values()}
            cf = w.get("confluence", {})
            self.spaces: dict[str, dict] = {s["key"]: s for s in cf.get("spaces", [])}
            self.space_by_id: dict[str, str] = {s["id"]: s["key"] for s in self.spaces.values()}
            self.pages: dict[str, dict] = {p["id"]: p for p in cf.get("pages", [])}
            jr = w.get("jira", {})
            self.projects: dict[str, dict] = {p["key"]: p for p in jr.get("projects", [])}
            self.levels: dict[str, dict] = {lv["id"]: lv for lv in jr.get("security_levels", [])}
            self.issues: dict[str, dict] = {i["key"]: i for i in jr.get("issues", [])}
            self.issue_by_id: dict[str, str] = {str(i["id"]): i["key"] for i in self.issues.values()}
            # The calling service account (round 2b finding 3). Real Jira authorises POST /issue for
            # the CALLER: "Create issues" in the project, plus "Modify reporter" to set someone else
            # as reporter. It never checks the reporter's own permissions. ``create``: True (every
            # project), False, or a list of project keys.
            sa = jr.get("service_account") or {}
            self.jira_service: dict[str, Any] = {
                "account_id": sa.get("account_id", "acc-svc-internal-brain"),
                "name": sa.get("name", "Internal Brain (service account)"),
                "create": sa.get("create", True),
                "modify_reporter": sa.get("modify_reporter", True)}
            sl = w.get("slack", {})
            self.workspace: dict[str, Any] = sl.get("workspace", {"id": "T0MOCK", "domain": "mock"})
            self.channels: dict[str, dict] = {c["id"]: c for c in sl.get("channels", [])}
            self.threads: dict[tuple[str, str], dict] = {(t["channel"], t["ts"]): t
                                                         for t in sl.get("threads", [])}
            dr = w.get("drive", {})
            self.drives: dict[str, dict] = {x["id"]: x for x in dr.get("drives", [])}
            self.folders: dict[str, dict] = {x["id"]: x for x in dr.get("folders", [])}
            self.files: dict[str, dict] = {x["id"]: x for x in dr.get("files", [])}
            self.drive_changes: list[dict[str, Any]] = []   # Drive changes feed (append-only)
            self.ratelimited = {p: False for p in PLATFORMS}
            self._native_index: dict[str, dict[str, str]] = {}
            self._rebuild_native_index()

    def _rebuild_native_index(self) -> None:
        idx: dict[str, dict[str, str]] = {p: {} for p in PLATFORMS}
        for uid, u in self.users.items():
            for p in PLATFORMS:
                idx[p][self.native(p, uid)] = uid
            if u.get("email"):
                idx["drive"].setdefault(u["email"].lower(), uid)
        self._native_index = idx

    # ------------------------------------------------------------------ time
    @property
    def tz(self) -> timezone:
        try:
            dt = datetime.fromisoformat(self.meta["now"])
            if dt.tzinfo is not None:
                off = dt.utcoffset() or timedelta(0)
                return timezone(off)
        except Exception:
            pass
        return timezone.utc

    def now_iso(self) -> str:
        return datetime.now(self.tz).isoformat(timespec="seconds")

    @property
    def domain(self) -> str:
        return self.meta.get("domain", "example.com")

    # ------------------------------------------------------------------ identity
    def native(self, platform: str, uid: Optional[str]) -> Optional[str]:
        """IB user id -> the platform's native id (falls back to the IB id when none recorded)."""
        if uid is None:
            return None
        u = self.users.get(uid)
        if u is None:
            return uid
        pid = (u.get("platform_ids") or {}).get(platform)
        if pid:
            return pid
        if platform == "drive" and u.get("email"):
            return u["email"]
        return uid

    def ib_id(self, platform: str, native_or_ib: Optional[str]) -> Optional[str]:
        """Native id (or an IB id) -> IB user id; None when unknown."""
        if native_or_ib is None:
            return None
        hit = self._native_index.get(platform, {}).get(native_or_ib)
        if hit is None and platform == "drive":
            hit = self._native_index["drive"].get(native_or_ib.lower())
        if hit is not None:
            return hit
        return native_or_ib if native_or_ib in self.users else None

    def active(self, uid: Optional[str]) -> bool:
        u = self.users.get(uid or "")
        return bool(u) and bool(u.get("active", True))

    def resolve_group(self, gid_or_name: str) -> Optional[str]:
        if gid_or_name in self.groups:
            return gid_or_name
        return self.group_by_name.get(gid_or_name)

    def group_email(self, gid: str) -> str:
        g = self.groups.get(gid, {})
        return g.get("email") or f"{gid}@{self.domain}"

    def group_by_email(self, email: str) -> Optional[str]:
        for gid in self.groups:
            if self.group_email(gid).lower() == email.lower():
                return gid
        return None

    def expanded_members(self, gid: str, _seen: Optional[set[str]] = None) -> set[str]:
        """Transitive user members of a group (nested ``member_groups`` flattened, cycle-safe)."""
        seen = _seen if _seen is not None else set()
        if gid in seen:
            return set()
        seen.add(gid)
        g = self.groups.get(gid)
        if g is None:
            return set()
        out = set(g.get("members", []))
        for sub in g.get("member_groups", []):
            out |= self.expanded_members(sub, seen)
        return out

    def user_groups(self, uid: str) -> set[str]:
        """Every group the user belongs to, directly or through nesting."""
        direct = set((self.users.get(uid) or {}).get("groups", []))
        out: set[str] = set()
        for gid in self.groups:
            if uid in self.expanded_members(gid):
                out.add(gid)
        # user.groups may list direct memberships too; expand to their parents
        changed = True
        out |= {g for g in direct if g in self.groups}
        while changed:
            changed = False
            for gid, g in self.groups.items():
                if gid not in out and any(sub in out for sub in g.get("member_groups", [])):
                    out.add(gid)
                    changed = True
        return out

    def in_principals(self, uid: str, users: Iterable[str], groups: Iterable[str]) -> bool:
        if uid in set(users):
            return True
        return bool(self.user_groups(uid) & {self.resolve_group(g) or g for g in groups})

    # ------------------------------------------------------------------ confluence
    def page_ancestors(self, page_id: str) -> list[str]:
        """Ancestor page ids, root first (Confluence v2 order)."""
        chain: list[str] = []
        seen = {page_id}
        cur = self.pages.get(page_id, {}).get("parent")
        while cur and cur in self.pages and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = self.pages[cur].get("parent")
        return list(reversed(chain))

    def page_descendants(self, page_id: str) -> list[str]:
        out: list[str] = []
        frontier = [page_id]
        while frontier:
            nxt = []
            for pid in frontier:
                for cid, c in self.pages.items():
                    if c.get("parent") == pid and cid not in out and cid != page_id:
                        out.append(cid)
                        nxt.append(cid)
            frontier = nxt
        return out

    def confluence_can_read(self, uid: Optional[str], page_id: str) -> bool:
        if uid is None or not self.active(uid):
            return False
        page = self.pages[page_id]
        space = self.spaces.get(page["space"])
        if space is None:
            return False
        read = space.get("read") or {}
        if not self.in_principals(uid, read.get("users", []), read.get("groups", [])):
            return False
        for pid in [*self.page_ancestors(page_id), page_id]:
            r = self.pages[pid].get("read_restriction")
            if r is None:
                continue
            if not self.in_principals(uid, r.get("users", []), r.get("groups", [])):
                return False
        return True

    # ------------------------------------------------------------------ jira
    def role_holders(self, project_key: str, role: str) -> tuple[list[str], list[str]]:
        r = (self.projects.get(project_key, {}).get("roles") or {}).get(role) or {}
        return list(r.get("users", [])), list(r.get("groups", []))

    def _in_roles(self, uid: str, project_key: str, roles: Iterable[str]) -> bool:
        for role in roles:
            users, groups = self.role_holders(project_key, role)
            if self.in_principals(uid, users, groups):
                return True
        return False

    def jira_can_browse_project(self, uid: Optional[str], project_key: str,
                                issue: Optional[dict] = None) -> bool:
        if uid is None or not self.active(uid):
            return False
        proj = self.projects.get(project_key)
        if proj is None:
            return False
        b = proj.get("browse") or {}
        if self._in_roles(uid, project_key, b.get("roles", [])):
            return True
        if self.in_principals(uid, b.get("users", []), b.get("groups", [])):
            return True
        if issue is not None:
            if b.get("reporter") and issue.get("reporter") == uid:
                return True
            if b.get("assignee") and issue.get("assignee") == uid:
                return True
        return False

    def jira_can_create(self, uid: Optional[str], project_key: str) -> bool:
        """"Create issues" in a project. The world may give a project a ``create`` holder set
        (``roles``/``users``/``groups``); without one, Jira's default scheme applies here: the
        project's ``Developers`` role (Viewers can browse but not create). The default applies
        only when the project has NO ``create`` key: an empty holder set means nobody may create
        (round 4 Codex A3)."""
        if uid is None or not self.active(uid):
            return False
        proj = self.projects.get(project_key)
        if proj is None:
            return False
        c = proj["create"] if "create" in proj else {"roles": ["Developers"]}
        if not isinstance(c, dict):
            return False                    # a malformed holder set grants nothing (fail closed)
        if self._in_roles(uid, project_key, c.get("roles", [])):
            return True
        return self.in_principals(uid, c.get("users", []), c.get("groups", []))

    def jira_service_can_create(self, project_key: str) -> bool:
        """"Create issues" for the calling service account (what real Jira checks on POST /issue)."""
        c = self.jira_service.get("create", True)
        return project_key in self.projects and (c is True or (isinstance(c, list) and project_key in c))

    def jira_service_can_modify_reporter(self) -> bool:
        return bool(self.jira_service.get("modify_reporter", True))

    def next_issue(self, project_key: str) -> tuple[str, str]:
        """``(key, id)`` for a new issue in ``project_key``: the next number and the next id."""
        nums = [int(k.rsplit("-", 1)[1]) for k, i in self.issues.items()
                if i.get("project") == project_key and k.rsplit("-", 1)[-1].isdigit()]
        ids = [int(i["id"]) for i in self.issues.values() if str(i.get("id", "")).isdigit()]
        return f"{project_key}-{max(nums, default=0) + 1}", str(max(ids, default=20000) + 1)

    def jira_can_browse_issue(self, uid: Optional[str], issue_key: str) -> bool:
        issue = self.issues.get(issue_key)
        if issue is None or uid is None or not self.active(uid):
            return False
        if not self.jira_can_browse_project(uid, issue["project"], issue):
            return False
        lvl_id = issue.get("security_level")
        if lvl_id is None:
            return True
        lvl = self.levels.get(str(lvl_id))
        if lvl is None:
            return False                       # unknown level: fail closed
        m = lvl.get("members") or {}
        if self.in_principals(uid, m.get("users", []), m.get("groups", [])):
            return True
        if self._in_roles(uid, issue["project"], m.get("roles", [])):
            return True
        if m.get("reporter") and issue.get("reporter") == uid:
            return True
        if m.get("assignee") and issue.get("assignee") == uid:
            return True
        return False

    # ------------------------------------------------------------------ slack
    def slack_ts_now(self, channel: str) -> str:
        """A ts strictly greater than every ts already in ``channel`` (microsecond precision)."""
        def us(ts: str) -> int:
            s, _, f = ts.partition(".")
            return int(s) * 1_000_000 + int((f + "000000")[:6])
        latest = 0
        for (ch, _), t in self.threads.items():
            if ch != channel:
                continue
            for m in t.get("messages", []):
                latest = max(latest, us(m["ts"]))
        cand = max(int(time.time() * 1_000_000), latest + 100)
        return f"{cand // 1_000_000}.{cand % 1_000_000:06d}"

    # ------------------------------------------------------------------ drive
    def drive_item(self, item_id: str) -> Optional[dict]:
        return self.files.get(item_id) or self.folders.get(item_id)

    def is_folder(self, item_id: str) -> bool:
        return item_id in self.folders

    def drive_parent(self, item_id: str) -> Optional[str]:
        if item_id in self.files:
            return self.files[item_id].get("folder")
        if item_id in self.folders:
            return self.folders[item_id].get("parent")
        return None

    def drive_descendants(self, folder_id: str) -> list[str]:
        out: list[str] = []
        frontier = [folder_id]
        while frontier:
            nxt = []
            for fid in frontier:
                for sid, s in self.folders.items():
                    if s.get("parent") == fid and sid not in out:
                        out.append(sid)
                        nxt.append(sid)
                out.extend(xid for xid, x in self.files.items() if x.get("folder") == fid)
            frontier = nxt
        return out

    def drive_permission_sources(self, item_id: str) -> list[tuple[dict, bool, Optional[str], str]]:
        """``(perm, inherited, inherited_from, permission_type)`` for an item, item first.

        Walks up the folder chain; a folder with ``inherited_permissions_disabled`` contributes its
        own permissions and then STOPS the walk (nothing above it, not even shared-drive
        membership). Otherwise shared-drive members are inherited from the drive.
        """
        item = self.drive_item(item_id)
        if item is None:
            return []
        out: list[tuple[dict, bool, Optional[str], str]] = []
        for p in item.get("permissions", []):
            out.append((p, False, None, "file"))
        if item.get("inherited_permissions_disabled"):
            return out
        cur = self.drive_parent(item_id)
        seen = {item_id}
        while cur and cur in self.folders and cur not in seen:
            seen.add(cur)
            f = self.folders[cur]
            for p in f.get("permissions", []):
                out.append((p, True, cur, "file"))
            if f.get("inherited_permissions_disabled"):
                return out
            cur = f.get("parent")
        drive_id = item.get("drive")
        drive = self.drives.get(drive_id or "")
        if drive is not None:
            members = drive.get("members") or {}
            for u in members.get("users", []):
                out.append(({"id": f"member-{drive_id}-{u}", "type": "user", "principal": u,
                             "role": members.get("role", "reader")}, True, drive_id, "member"))
            for g in members.get("groups", []):
                out.append(({"id": f"member-{drive_id}-{g}", "type": "group", "principal": g,
                             "role": members.get("role", "reader")}, True, drive_id, "member"))
        return out

    def record_drive_change(self, item_id: str, *, removed: bool = False) -> None:
        self.drive_changes.append({"fileId": item_id, "removed": removed, "time": self.now_iso()})
