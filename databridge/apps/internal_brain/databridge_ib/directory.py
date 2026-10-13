"""The identity directory — who a principal is, which groups they are in, their native account ids.

It is a CACHE of the company's identity source (for the demo: ``world.json``'s ``directory``;
native platforms stay authoritative and Gate 2 asks them). Nested groups are expanded once at load.
Every change bumps ``epoch`` so Gate-1 readable-set caches never survive a directory change.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterable, Optional

from .models import DirectoryUser, UserKind


def _expand_groups(groups: list[dict]) -> dict[str, set[str]]:
    """group id → every user id in it, member_groups expanded transitively (cycle-safe)."""
    direct = {g["id"]: set(g.get("members", [])) for g in groups}
    children = {g["id"]: list(g.get("member_groups", [])) for g in groups}
    out: dict[str, set[str]] = {}

    def walk(gid: str, seen: frozenset) -> set[str]:
        if gid in out:
            return out[gid]
        if gid in seen:
            return set()
        users = set(direct.get(gid, set()))
        for child in children.get(gid, []):
            users |= walk(child, seen | {gid})
        return users

    for gid in direct:
        out[gid] = walk(gid, frozenset())
    return out


class Directory:
    def __init__(self, users: Iterable[DirectoryUser]) -> None:
        self._lock = threading.Lock()
        self._users: dict[str, DirectoryUser] = {u.id: u for u in users}
        self._by_native: dict[tuple[str, str], str] = {}
        self.epoch = 1
        self._reindex()

    # -- construction ---------------------------------------------------------------------------
    @classmethod
    def from_world(cls, world: dict) -> "Directory":
        d = world.get("directory", {})
        members_of = _expand_groups(d.get("groups", []))
        user_groups: dict[str, set[str]] = {}
        for gid, uids in members_of.items():
            for uid in uids:
                user_groups.setdefault(uid, set()).add(gid)
        users = []
        for u in d.get("users", []):
            groups = set(u.get("groups", [])) | user_groups.get(u["id"], set())
            # a user listed in a child group is also in every ancestor group
            for gid, uids in members_of.items():
                if u["id"] in uids:
                    groups.add(gid)
            users.append(DirectoryUser(
                id=u["id"], name=u.get("name", u["id"]), email=u["email"],
                employment=u.get("employment", "employee"),
                kind=UserKind(u.get("kind", "member")), active=bool(u.get("active", True)),
                roles=tuple(u.get("roles", ["employee"])), groups=tuple(sorted(groups)),
                platform_ids=dict(u.get("platform_ids", {}))))
        return cls(users)

    @classmethod
    def from_world_file(cls, path: Path) -> "Directory":
        return cls.from_world(json.loads(Path(path).read_text(encoding="utf-8")))

    def _reindex(self) -> None:
        self._by_native = {(p, nid): u.id for u in self._users.values()
                           for p, nid in u.platform_ids.items()}

    # -- DirectoryIndex protocol (connectors/_http.py) ------------------------------------------
    def get(self, user_id: str) -> Optional[DirectoryUser]:
        return self._users.get(user_id)

    def by_platform_id(self, platform: str, native_id: str) -> Optional[DirectoryUser]:
        uid = self._by_native.get((platform, native_id))
        return self._users.get(uid) if uid else None

    def all(self) -> list[DirectoryUser]:
        return list(self._users.values())

    # -- mutation (webhook user.changed / admin) ------------------------------------------------
    def set_active(self, user_id: str, active: bool) -> None:
        with self._lock:
            u = self._users.get(user_id)
            if u is None or u.active == active:
                return
            self._users[user_id] = u.model_copy(update={"active": active})
            self.epoch += 1
