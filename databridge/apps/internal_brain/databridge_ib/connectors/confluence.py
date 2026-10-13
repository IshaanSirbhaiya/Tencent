"""Confluence connector (A3) — supported subset per plan §4.5.

Documents: one ``SourceDoc`` per page, ``doc_id = "confluence:<page id>"``, ``container`` = space key,
``version`` = ``str(version.number)``.

Permission semantics compiled into ``AclExpr`` (restrictions only ever NARROW)::

    all( space_read = any(users, groups),
         restriction(ancestor_1), …, restriction(ancestor_n),     # each present one = any(users, groups)
         restriction(page) )

* anonymous space access, or any space-read principal that is not a user or group → ``nobody``;
* a page that is not ``current`` (trashed / archived / draft) → ``fetch`` returns None, ``acl`` is
  ``nobody``, Gate 2 says ``vanished``.

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
from ._http import (DirectorySource, SourceFailure, WebhookVerifier, event_time, extract_links,
                    json_of, native_to_ib, now_utc, parse_iso, parse_webhook_body, request_gate2,
                    request_ingest, resolve_directory)
from .base import ConnectorError, WebhookRejected

PLATFORM = "confluence"
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

    def _paged(self, path: str, params: Optional[dict[str, Any]] = None) -> list[Any]:
        out: list[Any] = []
        cursor = ""
        for _ in range(10_000):
            p = dict(params or {}, **({"cursor": cursor} if cursor else {}))
            data = self._get(path, p)
            out.extend(_results(data))
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
        if not page or page.get("status", "current") != "current":
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
        perms = self._paged(f"api/v2/spaces/{space['id']}/permissions")
        users, groups, unsupported = set(), set(), []
        for p in perms:
            if not isinstance(p, dict):
                continue
            op = p.get("operation") or {}
            if op.get("key") != "read" or op.get("targetType", "space") != "space":
                continue
            pr = p.get("principal") or {}
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

    def _restriction(self, page_id: str) -> tuple[Optional[AclExpr], Any]:
        """(expression or None when unrestricted, fingerprint)."""
        data = self._get(f"rest/api/content/{page_id}/restriction/byOperation/read", allow_404=True)
        if not isinstance(data, dict):                      # unreadable restriction: fail closed
            return A.nobody("confluence-restriction-unreadable"), {"unreadable": True}
        r = data.get("restrictions") or {}
        users = sorted({str(x.get("accountId")) for x in ((r.get("user") or {}).get("results") or [])
                        if isinstance(x, dict) and x.get("accountId")})
        groups = sorted({str(x.get("id") or x.get("name")) for x in ((r.get("group") or {}).get("results") or [])
                         if isinstance(x, dict) and (x.get("id") or x.get("name"))})
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
        if not page or page.get("status", "current") != "current":
            return A.nobody("confluence-not-current")
        space = self._space(page.get("spaceId"))
        if space is None:
            return A.nobody("confluence-unknown-space")
        grant, _ = self._space_grant(space)
        if isinstance(grant, AclNobody):
            return grant
        parts: list[AclExpr] = [grant]
        anc = self._get(f"api/v2/pages/{pid}/ancestors", allow_404=True)
        if anc is None:                                     # vanished mid-compile: fail closed
            return A.nobody("confluence-ancestors-unreadable")
        for a in _results(anc):
            if isinstance(a, dict) and a.get("id") and a.get("type", "page") == "page":
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
            if page.get("status", "current") != "current":
                return verdict("vanished", f"page_{page.get('status')}")
            current = str((page.get("version") or {}).get("number", ""))
            if not current:
                return verdict("forbidden", "source_bad_payload")
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
                if pg.get("status", "current") != "current":
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
        if not raw:
            return None
        d = self.directory
        if d.get(raw) is not None:
            return raw
        return native_to_ib(d, PLATFORM, raw) or raw

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
