"""Confluence connector (A3) — supported subset per plan §4.5.

Documents: one ``SourceDoc`` per page, ``doc_id = "confluence:<page id>"``, ``container`` = space key,
``version`` = ``str(version.number)``.

Permission semantics compiled into ``AclExpr`` (restrictions only ever NARROW)::

    all( space_read = any(users, groups),
         restriction(ancestor_1), …, restriction(ancestor_n),     # each present one = any(users, groups)
         restriction(page) )

* anonymous space access, or any space-read principal that is not a user or group → ``nobody``;
* a page that is not ``current`` (trashed / archived / draft) → ``fetch`` returns None, ``acl`` is
  ``nobody``, Gate 2 says ``vanished``; a page with NO status is not assumed current (Codex sec3a #13);
* ancestors are paged to the end (a page cap or a repeated cursor raises ``ConnectorError``); a
  malformed ancestors payload, or an ancestor that is not a ``page`` → ``nobody`` (sec3a #4, #5);
* a restriction payload must carry ``restrictions.user.results`` and ``restrictions.group.results``
  lists of well-formed entries of a known principal type, and must not be truncated; ``{}``, a
  missing key, an unknown principal type or a malformed entry → ``nobody`` — only an EXPLICITLY
  empty restriction means "unrestricted" (sec3a #5).

Gate 2 also requires the page id it asked about, ``status == "current"`` stated explicitly and an
integer ``version.number`` ≥ 1; anything else denies (sec3a #13).

Native groups: ``group_resolver`` maps a Confluence group (id, else name) to an IB group id; the
default is the identity because the synthetic world shares one group namespace. A real deployment
must inject a tenant-scoped mapping (documented: SECURITY.md track 3, sec3a #7).

Gate 2 calls ``POST rest/api/content/{id}/permission/check`` with the ADMIN client (real Confluence
requires an admin caller — the documented super-reader trade-off), one attempt, 2 s timeout.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlparse

import httpx

from .. import acl as A
from ..models import AccessCheck, AclExpr, AclNobody, ChangeBatch, ChangeEvent, DirectoryUser, SourceDoc
from ._http import (DirectorySource, PageGuard, SourceFailure, WebhookVerifier, event_time,
                    event_user, extract_links, json_of, native_to_ib, now_utc, parse_iso,
                    parse_webhook_body, request_gate2, request_ingest, resolve_directory)
from .base import ConnectorError, WebhookRejected

PLATFORM = "confluence"
#: Atlassian account ids (24-hex, "<n>:<uuid>") and the synthetic world's "acc-…" form: such a
#: value in an event is never read as an IB user id
_NATIVE_ACCOUNT = re.compile(r"^(?:[0-9a-f]{24}|\d+:[0-9a-f-]{36}|acc-[A-Za-z0-9._-]+)$")
_USER_TYPES = (None, "known", "user")
_GROUP_TYPES = (None, "group")


class _Malformed(ConnectorError):
    """A permission structure Confluence returned that cannot be read with confidence."""
_PAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CURSOR_VERSION = 1
_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table|pre|blockquote)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def page_id_of(doc_id: str) -> Optional[str]:
    if not doc_id.startswith(f"{PLATFORM}:"):
        return None
    pid = doc_id.split(":", 1)[1]
    return pid if _PAGE_ID.match(pid) else None


def storage_to_text(value: str) -> str:
    text = _BLOCK.sub("\n", value or "")
    text = html.unescape(_TAG.sub("", text))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _results(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        r = data.get("results")
        return r if isinstance(r, list) else []
    return []


def _next_cursor(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    nxt = ((data.get("_links") or {}).get("next") or "") if isinstance(data.get("_links"), dict) else ""
    if nxt:
        return (parse_qs(urlparse(nxt).query).get("cursor") or [""])[0]
    return str(data.get("next_cursor") or data.get("nextCursor") or "")


def _fp(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


class ConfluenceConnector:
    platform = PLATFORM

    def __init__(self, client: httpx.Client, directory: DirectorySource, webhook_secret: str,
                 admin_client: Optional[httpx.Client] = None, *, prefix: str = "/confluence/wiki",
                 site_host: str = "companya.atlassian.net",
                 group_resolver: Optional[Callable[[str], Optional[str]]] = None,
                 sleep=None, now=None):
        self.client = client
        self.admin_client = admin_client
        self._directory = directory
        self.prefix = prefix.rstrip("/")
        self.site_host = site_host
        self._group = group_resolver or (lambda g: g)
        self._sleep = sleep
        self._verifier = WebhookVerifier(webhook_secret, **({"now": now} if now else {}))
        self._spaces: dict[str, dict[str, Any]] = {}       # space id -> space (metadata only)

    @property
    def directory(self):
        return resolve_directory(self._directory)

    # ---------------------------------------------------------------------------------------
    # low-level calls (ingest: retried)
    # ---------------------------------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"

    def _get(self, path: str, params: Optional[dict[str, Any]] = None, *,
             allow_404: bool = False) -> Any:
        kw = {"sleep": self._sleep} if self._sleep else {}
        resp = request_ingest(self.client, "GET", self._url(path), params=params,
                              ok_statuses=(404,) if allow_404 else (), **kw)
        if resp.status_code == 404:
            return None
        return json_of(resp)

    def _paged(self, path: str, params: Optional[dict[str, Any]] = None, *,
               allow_404: bool = False) -> Optional[list[Any]]:
        """Every result across all pages. A page cap / repeated cursor raises ``ConnectorError``;
        a page that is not ``{"results": [...]}`` (or a list) raises ``_Malformed``; a 404 on the
        first page returns None when ``allow_404``."""
        out: list[Any] = []
        cursor = ""
        guard = PageGuard(f"confluence {path}")
        while True:
            guard.next_page(cursor)
            p = dict(params or {}, **({"cursor": cursor} if cursor else {}))
            data = self._get(path, p, allow_404=allow_404)
            if data is None:
                if not out and guard.pages == 1:
                    return None
                raise ConnectorError(f"confluence {path}: vanished while paging")
            if isinstance(data, list):
                page = data
            elif isinstance(data, dict) and isinstance(data.get("results"), list):
                page = data["results"]
            else:
                raise _Malformed(f"confluence {path}: unexpected page shape")
            out.extend(page)
            cursor = _next_cursor(data)
            if not cursor:
                break
        return out

    def _all_spaces(self) -> list[dict[str, Any]]:
        spaces = [s for s in self._paged("api/v2/spaces", {"limit": 50}) if isinstance(s, dict) and s.get("id")]
        self._spaces = {str(s["id"]): s for s in spaces}
        return spaces

    def _space(self, space_id: Any) -> Optional[dict[str, Any]]:
        sid = str(space_id)
        if sid not in self._spaces:
            self._all_spaces()
        return self._spaces.get(sid)

    def _page(self, page_id: str) -> Optional[dict[str, Any]]:
        data = self._get(f"api/v2/pages/{page_id}", {"body-format": "storage"}, allow_404=True)
        return data if isinstance(data, dict) else None

    # ---------------------------------------------------------------------------------------
    # documents
    # ---------------------------------------------------------------------------------------
    def deep_link(self, space_key: str, page_id: str) -> str:
        return f"https://{self.site_host}/wiki/spaces/{space_key}/pages/{page_id}"

    def _build_doc(self, page: Mapping[str, Any], space: Mapping[str, Any]) -> SourceDoc:
        pid = str(page["id"])
        raw = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
        text = storage_to_text(raw)
        ver = page.get("version") or {}
        updated = parse_iso(ver.get("createdAt"))
        created = parse_iso(page.get("createdAt"), updated)
        key = str(space.get("key") or space.get("id"))
        links = extract_links(raw + "\n" + text, confluence_host=self.site_host)
        return SourceDoc(
            doc_id=f"{PLATFORM}:{pid}", platform=PLATFORM, kind="page", container=key,
            container_name=str(space.get("name") or key), title=str(page.get("title") or pid),
            text=text, version=str(ver.get("number", "")), created_at=created, updated_at=updated,
            deep_link=self.deep_link(key, pid),
            author=native_to_ib(self.directory, PLATFORM, page.get("authorId")),
            links=tuple(l for l in links if l != f"{PLATFORM}:{pid}"))

    def fetch(self, doc_id: str) -> Optional[SourceDoc]:
        pid = page_id_of(doc_id)
        if not pid:
            return None
        page = self._page(pid)
        if not page or page.get("status") != "current":
            return None                                     # trashed / archived / draft → VANISHED
        space = self._space(page.get("spaceId"))
        if space is None:
            return None
        return self._build_doc(page, space)

    # ---------------------------------------------------------------------------------------
    # ACL
    # ---------------------------------------------------------------------------------------
    def _space_grant(self, space: Mapping[str, Any]) -> tuple[AclExpr, Any]:
        """(expression, fingerprint) for the space read permission."""
        try:
            perms = self._paged(f"api/v2/spaces/{space['id']}/permissions", allow_404=True)
        except _Malformed:
            perms = None
        if perms is None:
            return (A.nobody("confluence-space-permissions-unreadable"),
                    {"unreadable": True, "space": str(space.get("id"))})
        users, groups, unsupported = set(), set(), []
        for p in perms:
            if not isinstance(p, dict) or not isinstance(p.get("operation"), dict):
                unsupported.append("malformed-entry")
                continue
            op = p["operation"]
            if op.get("key") != "read" or op.get("targetType", "space") != "space":
                continue
            pr = p.get("principal")
            if not isinstance(pr, dict):
                unsupported.append("malformed-principal")
                continue
            ptype, pid = pr.get("type"), pr.get("id")
            if ptype == "user" and pid:
                users.add(str(pid))
            elif ptype == "group" and pid:
                groups.add(str(pid))
            else:
                unsupported.append(str(ptype))
        fp = {"users": sorted(users), "groups": sorted(groups), "unsupported": sorted(unsupported),
              "anonymous": bool(space.get("anonymous"))}
        if space.get("anonymous") or unsupported:
            return A.nobody("confluence-anonymous-or-unsupported-space-principal"), fp
        d = self.directory
        expr = A.any_of(A.users(*[native_to_ib(d, PLATFORM, u) or "" for u in users]),
                        A.groups(*[self._group(g) or "" for g in groups]))
        return expr, fp

    @staticmethod
    def _parse_restriction(data: Any) -> tuple[list[str], list[str]]:
        """(user account ids, group ids-or-names) of a read restriction, or ``_Malformed``. Only an
        explicitly empty pair of result lists means "no restriction" (sec3a #5)."""
        if not isinstance(data, dict) or not isinstance(data.get("restrictions"), dict):
            raise _Malformed("no restrictions object")
        r = data["restrictions"]
        out: dict[str, set[str]] = {"user": set(), "group": set()}
        for kind in ("user", "group"):
            sec = r.get(kind)
            if not isinstance(sec, dict) or not isinstance(sec.get("results"), list):
                raise _Malformed(f"no {kind}.results list")
            res = sec["results"]
            links = sec.get("_links")
            size = sec.get("size")
            total = sec.get("totalSize")
            if (isinstance(links, dict) and links.get("next")) or \
                    (isinstance(size, int) and size > len(res)) or \
                    (isinstance(total, int) and total > len(res)):
                raise _Malformed(f"{kind} restriction list truncated")
            for x in res:
                if not isinstance(x, dict):
                    raise _Malformed(f"malformed {kind} entry")
                t = x.get("type")
                if kind == "user":
                    if t not in _USER_TYPES or not isinstance(x.get("accountId"), str) \
                            or not x["accountId"]:
                        raise _Malformed(f"unsupported user principal {t!r}")
                    out["user"].add(x["accountId"])
                else:
                    gid = x.get("id") or x.get("name")
                    if t not in _GROUP_TYPES or not isinstance(gid, str) or not gid:
                        raise _Malformed(f"unsupported group principal {t!r}")
                    out["group"].add(gid)
        return sorted(out["user"]), sorted(out["group"])

    def _restriction(self, page_id: str) -> tuple[Optional[AclExpr], Any]:
        """(expression or None when unrestricted, fingerprint)."""
        data = self._get(f"rest/api/content/{page_id}/restriction/byOperation/read", allow_404=True)
        try:
            users, groups = self._parse_restriction(data)
        except _Malformed as exc:                           # unreadable restriction: fail closed
            return A.nobody("confluence-restriction-unreadable"), {"unreadable": str(exc)}
        fp = {"users": users, "groups": groups}
        if not users and not groups:
            return None, fp
        d = self.directory
        return A.any_of(A.users(*[native_to_ib(d, PLATFORM, u) or "" for u in users]),
                        A.groups(*[self._group(g) or "" for g in groups])), fp

    def acl(self, doc_id: str) -> AclExpr:
        pid = page_id_of(doc_id)
        if not pid:
            return A.nobody("confluence-bad-doc-id")
        page = self._page(pid)
        if not page or page.get("status") != "current":       # no status ≠ current (sec3a #13)
            return A.nobody("confluence-not-current")
        space = self._space(page.get("spaceId"))
        if space is None:
            return A.nobody("confluence-unknown-space")
        grant, _ = self._space_grant(space)
        if isinstance(grant, AclNobody):
            return grant
        parts: list[AclExpr] = [grant]
        try:                                                # EVERY page of ancestors (sec3a #4)
            anc = self._paged(f"api/v2/pages/{pid}/ancestors", allow_404=True)
        except _Malformed:
            return A.nobody("confluence-ancestors-unreadable")
        if anc is None:                                     # vanished mid-compile: fail closed
            return A.nobody("confluence-ancestors-unreadable")
        for a in anc:
            if not isinstance(a, dict) or not isinstance(a.get("id"), (str, int)) or a.get("id") == "":
                return A.nobody("confluence-ancestor-malformed")
            if a.get("type", "page") != "page":             # folder / whiteboard / …: unsupported
                return A.nobody(f"confluence-unsupported-ancestor-{a.get('type')}")
            expr, _ = self._restriction(str(a["id"]))
            if expr is not None:
                parts.append(expr)
        own, _ = self._restriction(pid)
        if own is not None:
            parts.append(own)
        return A.all_of(*parts)

    # ---------------------------------------------------------------------------------------
    # Gate 2
    # ---------------------------------------------------------------------------------------
    def check_access(self, user: DirectoryUser, doc_id: str,
                     known_version: Optional[str] = None) -> AccessCheck:
        def verdict(v: str, reason: str = "", version: Optional[str] = None) -> AccessCheck:
            return AccessCheck(doc_id=doc_id, verdict=v, reason_code=reason, current_version=version,
                               checked_at=now_utc())

        pid = page_id_of(doc_id)
        if not pid:
            return verdict("forbidden", "bad_doc_id")
        if not user.active:
            return verdict("forbidden", "deactivated")
        account = user.platform_ids.get(PLATFORM)
        if not account:
            return verdict("forbidden", "no_platform_identity")
        if self.admin_client is None:
            return verdict("unverifiable", "no_admin_checker")
        try:
            resp = request_gate2(self.admin_client, "POST",
                                 self._url(f"rest/api/content/{pid}/permission/check"),
                                 json_body={"subject": {"type": "user", "identifier": account},
                                            "operation": "read"},
                                 ok_statuses=(404,))
            if resp.status_code == 404:
                return verdict("vanished", "page_gone")
            data = json_of(resp, gate2=True)
            if not isinstance(data, dict) or not isinstance(data.get("hasPermission"), bool):
                return verdict("forbidden", "source_bad_payload")
            if not data["hasPermission"]:
                return verdict("forbidden", "not_permitted")
            if data.get("errors"):
                return verdict("forbidden", "source_ambiguous")
            vresp = request_gate2(self.client, "GET", self._url(f"api/v2/pages/{pid}"),
                                  ok_statuses=(404,))
            if vresp.status_code == 404:
                return verdict("vanished", "page_gone")
            page = json_of(vresp, gate2=True)
            if not isinstance(page, dict):
                return verdict("forbidden", "source_bad_payload")
            if str(page.get("id")) != pid:                   # answered about another page
                return verdict("forbidden", "source_ambiguous")
            status = page.get("status")
            if not isinstance(status, str) or not status:    # no status is NOT "current"
                return verdict("forbidden", "source_bad_payload")
            if status != "current":
                return verdict("vanished", f"page_{status}")
            ver = page.get("version")
            number = ver.get("number") if isinstance(ver, dict) else None
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                return verdict("forbidden", "source_bad_payload")
            current = str(number)
        except SourceFailure as exc:
            return verdict("forbidden", exc.reason)
        except Exception:                                   # noqa: BLE001 — fail closed, always
            return verdict("forbidden", "source_error")
        if known_version and known_version != current:
            return verdict("drift_material", "updated_since_indexed", current)
        return verdict("verified", "ok", current)

    # ---------------------------------------------------------------------------------------
    # crawl
    # ---------------------------------------------------------------------------------------
    @staticmethod
    def _load_cursor(cursor: Optional[str]) -> Optional[dict[str, Any]]:
        if not cursor:
            return None
        try:
            data = json.loads(cursor)
        except ValueError as exc:
            raise ConnectorError("unreadable confluence cursor") from exc
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
            raise ConnectorError("unknown confluence cursor version")
        return data

    def list_changes(self, cursor: Optional[str]) -> ChangeBatch:
        """Full re-list of spaces and current pages each time (so deletions are seen). Cursor JSON::

            {"v":1, "pages": {"<page id>": "<version>"}, "acl": {"<page id>": "<effective-ACL fp>"}}

        The effective-ACL fingerprint covers the space read grant, every ancestor's read restriction
        (via ``parentId`` chain) and the page's own — a changed ancestor restriction marks the whole
        subtree ``acl_changed``.
        """
        prev = self._load_cursor(cursor)
        prev_pages: dict[str, str] = (prev or {}).get("pages", {})
        prev_acl: dict[str, str] = (prev or {}).get("acl", {})
        docs: list[SourceDoc] = []
        pages: dict[str, dict[str, Any]] = {}
        page_space: dict[str, dict[str, Any]] = {}
        space_fp: dict[str, Any] = {}
        for space in self._all_spaces():
            _, fp = self._space_grant(space)
            space_fp[str(space["id"])] = fp
            for pg in self._paged("api/v2/pages", {"space-id": space["id"], "body-format": "storage",
                                                   "limit": 50}):
                if not isinstance(pg, dict) or not pg.get("id"):
                    continue
                if pg.get("status") != "current":            # absent status: not assumed current
                    continue
                pages[str(pg["id"])] = pg
                page_space[str(pg["id"])] = space

        own_fp = {pid: self._restriction(pid)[1] for pid in pages}

        def chain(pid: str) -> list[Any]:
            out, seen, cur = [], set(), pid
            while cur and cur not in seen:
                seen.add(cur)
                pg = pages.get(cur)
                if pg is None:                              # parent not current / outside crawl
                    out.append({"missing": cur})
                    break
                out.append(own_fp.get(cur))
                cur = str(pg.get("parentId") or "") or None
            return out

        new_pages: dict[str, str] = {}
        new_acl: dict[str, str] = {}
        acl_changed: list[str] = []
        for pid, pg in pages.items():
            version = str((pg.get("version") or {}).get("number", ""))
            new_pages[pid] = version
            new_acl[pid] = _fp({"space": space_fp[str(page_space[pid]["id"])], "chain": chain(pid)})
            if prev_pages.get(pid) != version:
                docs.append(self._build_doc(pg, page_space[pid]))
            if pid in prev_acl and prev_acl[pid] != new_acl[pid]:
                acl_changed.append(f"{PLATFORM}:{pid}")
        deleted = tuple(f"{PLATFORM}:{pid}" for pid in prev_pages if pid not in pages)
        new_cursor = json.dumps({"v": CURSOR_VERSION, "pages": new_pages, "acl": new_acl},
                                sort_keys=True, separators=(",", ":"))
        return ChangeBatch(docs=tuple(docs), deleted=deleted, acl_changed=tuple(acl_changed),
                           cursor=new_cursor)

    # ---------------------------------------------------------------------------------------
    # webhooks
    # ---------------------------------------------------------------------------------------
    def _ib_user(self, raw: Optional[str]) -> Optional[str]:
        """Native account id via the directory FIRST; an unmapped account-id-shaped value is
        unresolved (None); an IB id is accepted only if the directory knows it (sec3a #10)."""
        return event_user(self.directory, PLATFORM, raw, lambda v: bool(_NATIVE_ACCOUNT.match(v)))

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> Sequence[ChangeEvent]:
        delivery, ts = self._verifier.verify(headers, body)
        data = parse_webhook_body(body, PLATFORM)
        etype = data["type"]
        ids = data.get("object_ids") or []
        if etype in ("content.changed", "content.deleted", "acl.changed"):
            ids = [i if i.startswith(f"{PLATFORM}:") else f"{PLATFORM}:{i}" for i in ids]
        return [ChangeEvent(platform=PLATFORM, type=etype, object_ids=tuple(ids),
                            container=data.get("container") or None,
                            user=self._ib_user(data.get("user")), delivery_id=delivery,
                            at=event_time(data, ts))]


__all__ = ["ConfluenceConnector", "page_id_of", "storage_to_text", "WebhookRejected"]
