"""Google Drive connector (B1) — supported subset per plan §4.5.

Documents: one ``SourceDoc`` per FILE, ``doc_id = "drive:<file id>"``, ``kind="file"``,
``container`` = shared-drive id, ``container_name`` = drive name, ``version`` = ``str(version)``.
Only text-like files are indexed: ``text/plain``, ``text/markdown``, ``text/csv``, Google Docs
(exported as ``text/plain``) and Google Sheets (exported as ``text/csv``). Every other mimeType
(images, PDFs, slides, binaries) is SKIPPED — never indexed, so never answerable; folders are not
documents.

Permission semantics compiled into ``AclExpr`` (inheritance WIDENS)::

    any( users(type=user grants),            # principal = email → directory.by_platform_id("drive")
         groups(type=group grants),          # group email → group id (``group_resolver``)
         domain(type=domain grant)  )        # ONLY the verified company domain; others grant nothing

``GET files/{id}/permissions`` already returns the inherited entries (``permissionDetails``) and
stops inheritance at a limited-access folder (``inheritedPermissionsDisabled``), and shows shared-
drive membership as inherited ``member`` entries — the connector compiles exactly that list.

Fail-closed rules (whole file → ``nobody``):
* any ``anyone`` grant ("anyone with the link"): plan §4.5 says IB denies link-shared files, so a
  link-shared file never enters an answer even for its named grantees — a deliberate choice: the
  owner has published the file beyond the company's identity boundary, and we will not be the
  system that repeats it;
* an expiring permission (``expirationTime``), an unknown permission type or an unknown role;
* a file whose permissions cannot be read.

Gate 2: Drive has no per-user "can X read this" API in our subset (and the real API has none
either), so Gate 2 RE-READS the file metadata and ``files/{id}/permissions`` with the service
client at answer time and evaluates the freshly compiled policy for the user NOW — one attempt
each, 2 s timeout, any failure denies (``source_*``); trashed / 404 ⇒ ``vanished``. Group and
domain membership come from the IB directory (cache of the identity source).

Freshness: the ``changes`` feed (``changes/startPageToken`` then ``changes?pageToken=``); the cursor
keeps the page token, the folder tree and per-file version + permission fingerprints. Removed /
trashed ⇒ ``deleted``; a changed permission fingerprint ⇒ ``acl_changed``; ANY change to a folder ⇒
``acl_changed`` for every descendant file (its inherited entries may have moved).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping, Optional, Sequence

import httpx

from .. import acl as A
from ..models import (AccessCheck, AclDomain, AclExpr, AclNobody, ChangeBatch, ChangeEvent,
                      DirectoryUser, SourceDoc)
from ._http import (DirectorySource, SourceFailure, WebhookVerifier, event_time, extract_links,
                    json_of, native_to_ib, now_utc, parse_iso, parse_webhook_body, request_gate2,
                    request_ingest, resolve_directory)
from .base import ConnectorError, WebhookRejected

PLATFORM = "drive"
CURSOR_VERSION = 1
FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
#: mimeType → ordered read strategies: ("export", target mime) or ("media", None)
READ_PLAN: dict[str, tuple[tuple[str, Optional[str]], ...]] = {
    "text/plain": (("media", None),),
    "text/markdown": (("media", None),),
    "text/x-markdown": (("media", None),),
    "text/csv": (("media", None),),
    GDOC: (("export", "text/plain"), ("media", None)),
    GSHEET: (("export", "text/csv"), ("export", "text/plain"), ("media", None)),
}
READ_ROLES = {"owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"}
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def file_id_of(doc_id: str) -> Optional[str]:
    if not doc_id.startswith(f"{PLATFORM}:"):
        return None
    fid = doc_id.split(":", 1)[1]
    return fid if _FILE_ID.match(fid) else None


def supported_mime(mime: Optional[str]) -> bool:
    return (mime or "") in READ_PLAN


def _fp(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def _parent(meta: Mapping[str, Any]) -> Optional[str]:
    ps = meta.get("parents") or []
    return str(ps[0]) if isinstance(ps, list) and ps else None


class DriveConnector:
    platform = PLATFORM

    def __init__(self, client: httpx.Client, directory: DirectorySource, webhook_secret: str,
                 admin_client: Optional[httpx.Client] = None, *, prefix: str = "/drive/v3",
                 company_domain: str = "companya.example",
                 group_resolver: Optional[Callable[[str], Optional[str]]] = None,
                 drive_names: Optional[Mapping[str, str]] = None, page_size: int = 100,
                 sleep=None, now=None):
        self.client = client
        self.admin_client = admin_client          # unused: Drive Gate 2 re-reads with the svc client
        self._directory = directory
        self.prefix = prefix.rstrip("/")
        self.company_domain = company_domain.lower()
        self._group = group_resolver or self._default_group
        self._drive_names: dict[str, str] = dict(drive_names or {})
        self.page_size = page_size
        self._sleep = sleep
        self._verifier = WebhookVerifier(webhook_secret, **({"now": now} if now else {}))

    @property
    def directory(self):
        return resolve_directory(self._directory)

    def _default_group(self, email: str) -> Optional[str]:
        """Group email ``<group id>@<company domain>`` → group id; any other domain → None."""
        local, _, dom = (email or "").rpartition("@")
        return local if local and dom.lower() == self.company_domain else None

    # ---------------------------------------------------------------------------------------
    # low-level calls
    # ---------------------------------------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.prefix}/{path.lstrip('/')}"

    def _call(self, path: str, params: Optional[dict[str, Any]] = None, *, gate2: bool = False,
              ok: tuple[int, ...] = (404,), attempts: int = 5) -> httpx.Response:
        if gate2:
            return request_gate2(self.client, "GET", self._url(path), params=params, ok_statuses=ok)
        kw = {"sleep": self._sleep} if self._sleep else {}
        return request_ingest(self.client, "GET", self._url(path), params=params, ok_statuses=ok,
                              attempts=attempts, **kw)

    def _json(self, path: str, params: Optional[dict[str, Any]] = None, *, gate2: bool = False,
              attempts: int = 5) -> Any:
        resp = self._call(path, params, gate2=gate2, attempts=attempts)
        if resp.status_code == 404:
            return None
        return json_of(resp, gate2=gate2)

    def _meta(self, fid: str, *, gate2: bool = False, attempts: int = 5) -> Optional[dict[str, Any]]:
        data = self._json(f"files/{fid}", {"fields": "*", "supportsAllDrives": "true"}, gate2=gate2,
                          attempts=attempts)
        if data is not None and not isinstance(data, dict):
            if gate2:
                raise SourceFailure("source_bad_payload")
            raise ConnectorError("drive files.get: unexpected payload")
        return data if data and data.get("id") else None

    def _permissions(self, fid: str, *, gate2: bool = False) -> Optional[list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        token = ""
        for _ in range(10_000):
            params: dict[str, Any] = {"supportsAllDrives": "true", "pageSize": self.page_size,
                                      "fields": "nextPageToken,permissions(*)"}
            if token:
                params["pageToken"] = token
            data = self._json(f"files/{fid}/permissions", params, gate2=gate2)
            if data is None:
                return None
            if not isinstance(data, dict) or not isinstance(data.get("permissions", []), list):
                if gate2:
                    raise SourceFailure("source_bad_payload")
                raise ConnectorError("drive permissions.list: unexpected payload")
            out.extend(p for p in data.get("permissions") or [] if isinstance(p, dict))
            token = str(data.get("nextPageToken") or "")
            if not token:
                break
        return out

    def _list(self, q: str, *, attempts: int = 5) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = ""
        for _ in range(10_000):
            params: dict[str, Any] = {"q": q, "pageSize": self.page_size, "corpora": "allDrives",
                                      "includeItemsFromAllDrives": "true", "supportsAllDrives": "true",
                                      "fields": "nextPageToken,files(*)"}
            if token:
                params["pageToken"] = token
            resp = self._call("files", params, ok=(), attempts=attempts)
            data = json_of(resp)
            if not isinstance(data, dict):
                raise ConnectorError("drive files.list: unexpected payload")
            out.extend(f for f in data.get("files") or [] if isinstance(f, dict) and f.get("id"))
            token = str(data.get("nextPageToken") or "")
            if not token:
                break
        return out

    def _descendant_files(self, folder_id: str, *, attempts: int = 5) -> list[str]:
        """Every non-folder item under ``folder_id`` (BFS over ``'<id>' in parents``)."""
        files: list[str] = []
        seen = {folder_id}
        frontier = [folder_id]
        while frontier:
            nxt = []
            for fid in frontier:
                for item in self._list(f"'{fid}' in parents and trashed = false", attempts=attempts):
                    iid = str(item["id"])
                    if iid in seen:
                        continue
                    seen.add(iid)
                    if item.get("mimeType") == FOLDER_MIME:
                        nxt.append(iid)
                    else:
                        files.append(iid)
            frontier = nxt
        return files

    def _drive_name(self, drive_id: Optional[str]) -> str:
        if not drive_id:
            return "My Drive"
        if drive_id not in self._drive_names:
            name = drive_id
            try:
                data = self._json(f"drives/{drive_id}", attempts=2)
                if isinstance(data, dict) and data.get("name"):
                    name = str(data["name"])
            except ConnectorError:
                pass                                # name is cosmetic; the id is still correct
            self._drive_names[drive_id] = name
        return self._drive_names[drive_id]

    # ---------------------------------------------------------------------------------------
    # documents
    # ---------------------------------------------------------------------------------------
    @staticmethod
    def deep_link(meta: Mapping[str, Any]) -> str:
        return str(meta.get("webViewLink") or f"https://docs.google.com/document/d/{meta['id']}")

    def _content(self, meta: Mapping[str, Any]) -> Optional[str]:
        fid = str(meta["id"])
        for how, target in READ_PLAN.get(str(meta.get("mimeType") or ""), ()):
            if how == "export":
                resp = self._call(f"files/{fid}/export", {"mimeType": target}, ok=(400, 403, 404))
            else:
                resp = self._call(f"files/{fid}", {"alt": "media", "supportsAllDrives": "true"},
                                  ok=(400, 403, 404))
            if resp.is_success:
                return resp.text
        return None                                 # gone, or no strategy the source accepted

    def _build_doc(self, meta: Mapping[str, Any], text: str) -> SourceDoc:
        fid = str(meta["id"])
        modified = parse_iso(meta.get("modifiedTime"))
        drive_id = meta.get("driveId") or meta.get("teamDriveId")
        doc_id = f"{PLATFORM}:{fid}"
        editor = (meta.get("lastModifyingUser") or {}).get("emailAddress") \
            if isinstance(meta.get("lastModifyingUser"), dict) else None
        return SourceDoc(
            doc_id=doc_id, platform=PLATFORM, kind="file", container=str(drive_id or "my-drive"),
            container_name=self._drive_name(drive_id), title=str(meta.get("name") or fid),
            text=text, version=str(meta.get("version", "")),
            created_at=parse_iso(meta.get("createdTime"), modified), updated_at=modified,
            deep_link=self.deep_link(meta), author=self._user_id(editor),
            links=tuple(l for l in extract_links(text) if l != doc_id))

    def _doc_from_meta(self, meta: Mapping[str, Any]) -> Optional[SourceDoc]:
        if meta.get("trashed") or meta.get("mimeType") == FOLDER_MIME or not supported_mime(meta.get("mimeType")):
            return None
        text = self._content(meta)
        if text is None:
            return None
        return self._build_doc(meta, text)

    def fetch(self, doc_id: str) -> Optional[SourceDoc]:
        fid = file_id_of(doc_id)
        if not fid:
            return None
        meta = self._meta(fid)
        return self._doc_from_meta(meta) if meta else None

    # ---------------------------------------------------------------------------------------
    # ACL
    # ---------------------------------------------------------------------------------------
    def _user_id(self, email: Optional[str]) -> Optional[str]:
        if not email:
            return None
        d = self.directory
        return native_to_ib(d, PLATFORM, email) or native_to_ib(d, PLATFORM, email.lower())

    def compile_acl(self, perms: Sequence[Mapping[str, Any]]) -> AclExpr:
        grants: list[AclExpr] = []
        for p in perms:
            if p.get("deleted"):
                continue                            # a deleted account grants nothing
            t = p.get("type")
            if t == "anyone":
                return A.nobody("drive-anyone-with-link")
            if p.get("expirationTime"):
                return A.nobody("drive-expiring-permission")
            if t not in ("user", "group", "domain"):
                return A.nobody(f"drive-unsupported-permission-type-{t}")
            if p.get("role") not in READ_ROLES:
                return A.nobody(f"drive-unsupported-role-{p.get('role')}")
            if t == "user":
                grants.append(A.users(self._user_id(p.get("emailAddress")) or ""))
            elif t == "group":
                grants.append(A.groups(self._group(str(p.get("emailAddress") or "")) or ""))
            else:
                dom = str(p.get("domain") or "").lower()
                grants.append(AclDomain(domain=dom) if dom and dom == self.company_domain
                              else A.nobody("drive-foreign-domain"))
        return A.any_of(*grants)

    @staticmethod
    def perms_fingerprint(perms: Optional[Sequence[Mapping[str, Any]]]) -> str:
        if perms is None:
            return _fp(None)
        rows = sorted(json.dumps({k: p.get(k) for k in ("type", "role", "emailAddress", "domain",
                                                          "expirationTime", "deleted")},
                                 sort_keys=True) for p in perms)
        return _fp(rows)

    def acl(self, doc_id: str) -> AclExpr:
        fid = file_id_of(doc_id)
        if not fid:
            return A.nobody("drive-bad-doc-id")
        meta = self._meta(fid)
        if meta is None or meta.get("trashed"):
            return A.nobody("drive-file-gone")
        if meta.get("mimeType") == FOLDER_MIME:
            return A.nobody("drive-folder-not-a-document")
        perms = self._permissions(fid)
        if perms is None:
            return A.nobody("drive-permissions-unreadable")
        return self.compile_acl(perms)

    # ---------------------------------------------------------------------------------------
    # Gate 2
    # ---------------------------------------------------------------------------------------
    def check_access(self, user: DirectoryUser, doc_id: str,
                     known_version: Optional[str] = None) -> AccessCheck:
        """Re-read metadata + permissions NOW and evaluate for ``user`` (no per-user check API)."""
        def verdict(v: str, reason: str = "", version: Optional[str] = None) -> AccessCheck:
            return AccessCheck(doc_id=doc_id, verdict=v, reason_code=reason, current_version=version,
                               checked_at=now_utc())

        fid = file_id_of(doc_id)
        if not fid:
            return verdict("forbidden", "bad_doc_id")
        if not user.active:
            return verdict("forbidden", "deactivated")
        if not user.platform_ids.get(PLATFORM):
            return verdict("forbidden", "no_platform_identity")
        try:
            meta = self._meta(fid, gate2=True)
            if meta is None or meta.get("trashed"):
                return verdict("vanished", "file_gone")
            if meta.get("mimeType") == FOLDER_MIME:
                return verdict("forbidden", "not_a_file")
            current = str(meta.get("version") or "")
            if not current:
                return verdict("forbidden", "source_bad_payload")
            perms = self._permissions(fid, gate2=True)
            if perms is None:
                return verdict("vanished", "file_gone")
            expr = self.compile_acl(perms)
            if not A.allows(expr, user):
                return verdict("forbidden", "unsupported_acl" if isinstance(expr, AclNobody)
                               else "not_permitted")
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
            raise ConnectorError("unreadable drive cursor") from exc
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION or not data.get("token"):
            raise ConnectorError("unknown drive cursor version")
        return data

    def _start_token(self) -> str:
        data = self._json("changes/startPageToken", {"supportsAllDrives": "true"})
        tok = data.get("startPageToken") if isinstance(data, dict) else None
        if not tok:
            raise ConnectorError("drive changes.getStartPageToken: no token")
        return str(tok)

    def _changes(self, token: str) -> tuple[list[dict[str, Any]], str]:
        out: list[dict[str, Any]] = []
        for _ in range(10_000):
            data = self._json("changes", {"pageToken": token, "pageSize": self.page_size,
                                          "includeItemsFromAllDrives": "true",
                                          "supportsAllDrives": "true"})
            if not isinstance(data, dict):
                raise ConnectorError("drive changes.list: unexpected payload")
            out.extend(c for c in data.get("changes") or [] if isinstance(c, dict))
            if data.get("newStartPageToken"):
                return out, str(data["newStartPageToken"])
            token = str(data.get("nextPageToken") or "")
            if not token:
                raise ConnectorError("drive changes.list: neither nextPageToken nor newStartPageToken")
        raise ConnectorError("drive changes.list: too many pages")

    @staticmethod
    def _node(meta: Mapping[str, Any]) -> dict[str, Any]:
        return {"p": _parent(meta), "d": meta.get("driveId") or meta.get("teamDriveId"),
                "f": meta.get("mimeType") == FOLDER_MIME}

    @staticmethod
    def _descendants(tree: Mapping[str, Mapping[str, Any]], roots: set[str]) -> set[str]:
        children: dict[str, list[str]] = {}
        for iid, n in tree.items():
            if n.get("p"):
                children.setdefault(str(n["p"]), []).append(iid)
        out: set[str] = set()
        frontier = list(roots)
        seen = set(roots)
        while frontier:
            nxt = []
            for r in frontier:
                for c in children.get(r, []):
                    if c not in seen:
                        seen.add(c)
                        out.add(c)
                        nxt.append(c)
            frontier = nxt
        return out

    def list_changes(self, cursor: Optional[str]) -> ChangeBatch:
        """First call: take the changes start token, then list every item. Later calls: drain the
        changes feed from the stored token. Cursor JSON::

            {"v":1, "token": "<changes page token>",
             "tree":  {"<item id>": {"p": parent, "d": drive id, "f": is_folder}},
             "files": {"<file id>": {"v": "<version>", "acl": "<permissions fingerprint>"}}}
        """
        prev = self._load_cursor(cursor)
        docs: list[SourceDoc] = []
        deleted: list[str] = []
        acl_changed: list[str] = []
        if prev is None:
            token = self._start_token()             # BEFORE listing: nothing slips between them
            tree: dict[str, Any] = {}
            files: dict[str, Any] = {}
            for meta in self._list("trashed = false"):
                iid = str(meta["id"])
                tree[iid] = self._node(meta)
                if tree[iid]["f"]:
                    continue
                doc = self._doc_from_meta(meta)
                if doc is None:
                    continue
                perms = self._permissions(iid)
                if perms is None:
                    continue
                docs.append(doc)
                files[iid] = {"v": doc.version, "acl": self.perms_fingerprint(perms)}
            return ChangeBatch(docs=tuple(docs), cursor=self._dump(token, tree, files))

        tree = dict(prev.get("tree") or {})
        files = dict(prev.get("files") or {})
        changes, token = self._changes(str(prev["token"]))
        metas: dict[str, Optional[dict[str, Any]]] = {}
        touched: set[str] = set()
        changed_folders: set[str] = set()
        changed_drives: set[str] = set()
        for ch in changes:
            if ch.get("changeType") == "drive":
                if ch.get("driveId"):
                    changed_drives.add(str(ch["driveId"]))
                continue
            fid = str(ch.get("fileId") or "")
            if not fid:
                continue
            f = ch.get("file") if isinstance(ch.get("file"), dict) else None
            gone = bool(ch.get("removed")) or bool((f or {}).get("trashed"))
            if (tree.get(fid) or {}).get("f") or (f or {}).get("mimeType") == FOLDER_MIME:
                changed_folders.add(fid)
            if gone:
                metas[fid] = None
            elif f is not None:
                metas[fid] = f
            touched.add(fid)

        forced = self._descendants(tree, changed_folders)          # under the OLD tree
        for fid, meta in metas.items():                            # then apply moves / new items
            if meta is not None:
                tree[fid] = self._node(meta)
        forced |= self._descendants(tree, changed_folders)         # …and the NEW tree
        forced |= {i for i, n in tree.items() if n.get("d") in changed_drives}
        forced = {i for i in forced if not (tree.get(i) or {}).get("f")}

        for fid in sorted((touched | forced) - changed_folders):
            if fid in metas:
                meta = metas[fid]
            else:
                meta = self._meta(fid)
            was = files.get(fid)
            if meta is None or meta.get("trashed") or not supported_mime(meta.get("mimeType")):
                if was is not None:
                    deleted.append(f"{PLATFORM}:{fid}")
                    files.pop(fid, None)
                if meta is None or meta.get("trashed"):
                    tree.pop(fid, None)
                continue
            tree[fid] = self._node(meta)
            version = str(meta.get("version", ""))
            perms = self._permissions(fid)
            if perms is None:                       # vanished between the feed and now
                if was is not None:
                    deleted.append(f"{PLATFORM}:{fid}")
                    files.pop(fid, None)
                tree.pop(fid, None)
                continue
            fp = self.perms_fingerprint(perms)
            if was is None or was.get("v") != version:
                doc = self._doc_from_meta(meta)
                if doc is None:
                    if was is not None:
                        deleted.append(f"{PLATFORM}:{fid}")
                        files.pop(fid, None)
                    continue
                docs.append(doc)
            if was is not None and (fid in forced or was.get("acl") != fp):
                acl_changed.append(f"{PLATFORM}:{fid}")
            files[fid] = {"v": version, "acl": fp}
        for fid in changed_folders:
            if metas.get(fid, {}) is None:          # folder removed: forget it (files handled above)
                tree.pop(fid, None)
        return ChangeBatch(docs=tuple(docs), deleted=tuple(deleted), acl_changed=tuple(acl_changed),
                           cursor=self._dump(token, tree, files))

    @staticmethod
    def _dump(token: str, tree: Mapping[str, Any], files: Mapping[str, Any]) -> str:
        return json.dumps({"v": CURSOR_VERSION, "token": token, "tree": tree, "files": files},
                          sort_keys=True, separators=(",", ":"))

    # ---------------------------------------------------------------------------------------
    # webhooks
    # ---------------------------------------------------------------------------------------
    def _ib_user(self, raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        d = self.directory
        if d.get(raw) is not None:
            return raw
        return self._user_id(raw) or raw

    def _expand_acl_ids(self, ids: Sequence[str]) -> tuple[str, ...]:
        """Folder ids → their descendant FILES (an ACL change on a folder moves every inherited
        entry below it). Lookup failures keep the raw id: never drop an invalidation."""
        out: list[str] = []

        def add(x: str) -> None:
            if x not in out:
                out.append(x)

        for raw in ids:
            fid = raw.split(":", 1)[1] if raw.startswith(f"{PLATFORM}:") else raw
            try:
                meta = self._meta(fid, attempts=2) if _FILE_ID.match(fid) else None
                if meta is not None and meta.get("mimeType") == FOLDER_MIME:
                    for c in self._descendant_files(fid, attempts=2):
                        add(f"{PLATFORM}:{c}")
                    continue
            except ConnectorError:
                pass
            add(f"{PLATFORM}:{fid}")
        return tuple(out)

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> Sequence[ChangeEvent]:
        delivery, ts = self._verifier.verify(headers, body)
        data = parse_webhook_body(body, PLATFORM)
        etype = data["type"]
        ids = data.get("object_ids") or []
        if etype == "acl.changed":
            object_ids = self._expand_acl_ids(ids)
        elif etype in ("content.changed", "content.deleted"):
            object_ids = tuple(i if i.startswith(f"{PLATFORM}:") else f"{PLATFORM}:{i}" for i in ids)
        else:
            object_ids = tuple(ids)
        return [ChangeEvent(platform=PLATFORM, type=etype, object_ids=object_ids,
                            container=data.get("container") or None,
                            user=self._ib_user(data.get("user")), delivery_id=delivery,
                            at=event_time(data, ts))]


__all__ = ["DriveConnector", "file_id_of", "supported_mime", "WebhookRejected"]
