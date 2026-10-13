"""Jira connector (B1) — supported subset per plan §4.5.

Documents: one ``SourceDoc`` per issue, ``doc_id = "jira:<KEY>"`` (the links module emits exactly
this shape), ``kind="issue"``, ``container`` = project key, ``version`` = the issue's ``updated``
timestamp string as Jira returns it.

Permission semantics compiled into ``AclExpr``::

    all( browse(project),                       # "Browse projects" holders of the permission scheme
         security_level(issue) )                # only when the issue has a security level

    browse          = any( users, groups,
                           role actors (users / groups) of every projectRole holder,
                           users(reporter)  if a ``reporter`` holder exists,
                           users(assignee)  if an ``assignee`` holder exists )
    security_level  = any( users, groups, role actors of listed roles,
                           users(reporter) / users(assignee) if listed )

Fail-closed rules:
* a holder type outside {user, group, projectRole, reporter, assignee} (custom-field holders,
  application roles, project lead, "anyone", anything unknown) compiles to ``nobody`` for THAT
  branch — inside ``any`` it never grants;
* an unreadable role, a security level that cannot be read or has no members, or an issue that is
  gone compiles the whole issue to ``nobody``;
* comment / worklog visibility restrictions are not modelled (comments are indexed with the issue);
  the generator does not emit them — documented unsupported subset.

Gate 2 calls ``POST permissions/check`` with the ADMIN client (``accountId`` + ``BROWSE_PROJECTS``
for the issue id; real Jira requires Administer Jira, and honours security levels) — one attempt,
2 s timeout, no retry. The issue id and current version come from ``GET issue/{key}`` with the
service client. No admin client ⇒ ``unverifiable``.

Freshness: JQL ``updated >= "<high-water - 2 min>" ORDER BY updated`` with ``nextPageToken`` paging,
plus a periodic full id listing (``full_every_s``) that detects deletions. ACL drift that does not
bump ``updated`` (role actors, browse holders, security-level members) is caught by fingerprints kept
in the cursor; every run re-reads the policy of every project / level in use.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import timedelta
from typing import Any, Callable, Mapping, Optional, Sequence

import httpx

from .. import acl as A
from ..models import AccessCheck, AclExpr, ChangeBatch, ChangeEvent, DirectoryUser, SourceDoc
from ._http import (DirectorySource, SourceFailure, WebhookVerifier, event_time, extract_links,
                    json_of, native_to_ib, now_utc, parse_iso, parse_webhook_body, request_gate2,
                    request_ingest, resolve_directory)
from .base import ConnectorError, WebhookRejected

PLATFORM = "jira"
BROWSE = "BROWSE_PROJECTS"
CURSOR_VERSION = 1
OVERLAP = timedelta(minutes=2)
_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-\d{1,9}$")
#: every issue ever — bounded JQL (real Jira's enhanced search rejects unbounded queries)
FULL_JQL = 'updated >= "1970-01-01T00:00:00+00:00" ORDER BY updated'
LIST_FIELDS = "updated,project,security,reporter,assignee"
SUPPORTED_HOLDERS = ("user", "group", "projectRole", "reporter", "assignee")
_ADF_BLOCKS = {"paragraph", "heading", "codeBlock", "blockquote", "listItem", "tableRow",
               "tableCell", "tableHeader", "panel", "rule", "mediaGroup", "decisionItem", "taskItem"}


def issue_key_of(doc_id: str) -> Optional[str]:
    if not doc_id.startswith(f"{PLATFORM}:"):
        return None
    key = doc_id.split(":", 1)[1]
    return key if _KEY.match(key) else None


def adf_to_text(node: Any) -> str:
    """Atlassian Document Format (v3 rich text) → plain text, one line per block. A plain string
    (v2 API / already-rendered text) passes through."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    lines: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        s = re.sub(r"[ \t]+", " ", "".join(cur)).strip()
        if s:
            lines.append(s)
        cur.clear()

    def walk(n: Any) -> None:
        if isinstance(n, list):
            for c in n:
                walk(c)
            return
        if not isinstance(n, dict):
            return
        t = n.get("type")
        if t == "text":
            cur.append(str(n.get("text") or ""))
            return
        if t == "hardBreak":
            flush()
            return
        if t in ("mention", "emoji", "inlineCard", "status", "date"):
            a = n.get("attrs") or {}
            cur.append(str(a.get("text") or a.get("shortName") or a.get("url") or a.get("timestamp")
                           or ""))
            return
        block = t in _ADF_BLOCKS
        if block:
            flush()
        walk(n.get("content") or [])
        if block:
            flush()

    walk(node)
    flush()
    return "\n".join(lines)


def _fp(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def _acc(ref: Any) -> Optional[str]:
    return str(ref["accountId"]) if isinstance(ref, dict) and ref.get("accountId") else None


class JiraConnector:
    platform = PLATFORM

    def __init__(self, client: httpx.Client, directory: DirectorySource, webhook_secret: str,
                 admin_client: Optional[httpx.Client] = None, *, prefix: str = "/jira/rest/api/3",
                 site_host: str = "companya.atlassian.net",
                 group_resolver: Optional[Callable[[str], Optional[str]]] = None,
                 page_size: int = 50, full_every_s: float = 3600.0, sleep=None, now=None):
        self.client = client
        self.admin_client = admin_client
        self._directory = directory
        self.prefix = prefix.rstrip("/")
        self.site_host = site_host
        self._group = group_resolver or (lambda g: g)
        self.page_size = page_size
        self.full_every_s = full_every_s
        self._sleep = sleep
        self._clock: Callable[[], float] = now or time.time
        self._verifier = WebhookVerifier(webhook_secret, **({"now": now} if now else {}))

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

    def _search(self, jql: str, fields: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = ""
        for _ in range(10_000):
            params: dict[str, Any] = {"jql": jql, "fields": fields, "maxResults": self.page_size}
            if token:
                params["nextPageToken"] = token
            data = self._get("search/jql", params)
            if not isinstance(data, dict):
                raise ConnectorError("jira search: unexpected payload")
            out.extend(i for i in (data.get("issues") or []) if isinstance(i, dict) and i.get("key"))
            token = str(data.get("nextPageToken") or "")
            if not token or data.get("isLast") is True:
                break
        return out

    def _issue(self, key: str, fields: str = "*all") -> Optional[dict[str, Any]]:
        data = self._get(f"issue/{key}", {"fields": fields}, allow_404=True)
        return data if isinstance(data, dict) and data.get("key") else None

    # ---------------------------------------------------------------------------------------
    # documents
    # ---------------------------------------------------------------------------------------
    def deep_link(self, key: str) -> str:
        return f"https://{self.site_host}/browse/{key}"

    def _who(self, ref: Any) -> str:
        acc = _acc(ref)
        uid = native_to_ib(self.directory, PLATFORM, acc)
        name = (ref or {}).get("displayName") if isinstance(ref, dict) else None
        if uid and name:
            return f"{name} ({uid})"
        return uid or name or acc or "unassigned"

    def _build_doc(self, issue: Mapping[str, Any]) -> SourceDoc:
        key = str(issue["key"])
        f = issue.get("fields") or {}
        d = self.directory
        summary = str(f.get("summary") or "").strip()
        status = ((f.get("status") or {}).get("name")) if isinstance(f.get("status"), dict) else f.get("status")
        lines = [summary]
        desc = adf_to_text(f.get("description"))
        if desc:
            lines.append(desc)
        lines.append(f"Status: {status or 'unknown'}")
        lines.append(f"Reporter: {self._who(f.get('reporter'))}")
        lines.append(f"Assignee: {self._who(f.get('assignee'))}")
        linked = []
        for ln in f.get("issuelinks") or []:
            if not isinstance(ln, dict):
                continue
            typ = ln.get("type") or {}
            for side, verb in (("outwardIssue", typ.get("outward")), ("inwardIssue", typ.get("inward"))):
                other = ln.get(side)
                if isinstance(other, dict) and other.get("key"):
                    linked.append(f"{verb or 'relates to'} {other['key']}")
        if linked:
            lines.append("Linked issues: " + "; ".join(linked))
        comments = ((f.get("comment") or {}).get("comments")) if isinstance(f.get("comment"), dict) else None
        for c in comments or []:
            if not isinstance(c, dict):
                continue
            acc = _acc(c.get("author"))
            who = native_to_ib(d, PLATFORM, acc) or acc or "unknown"
            body = adf_to_text(c.get("body")).replace("\n", " ")
            lines.append(f"{who}: {body}")
        text = "\n".join(x for x in lines if x)
        proj = f.get("project") or {}
        pkey = str(proj.get("key") or key.rsplit("-", 1)[0])
        updated_raw = str(f.get("updated") or "")
        updated = parse_iso(updated_raw)
        doc_id = f"{PLATFORM}:{key}"
        return SourceDoc(
            doc_id=doc_id, platform=PLATFORM, kind="issue", container=pkey,
            container_name=str(proj.get("name") or pkey), title=f"{key}: {summary}",
            text=text, version=updated_raw, created_at=parse_iso(f.get("created"), updated),
            updated_at=updated, deep_link=self.deep_link(key),
            author=native_to_ib(d, PLATFORM, _acc(f.get("reporter"))),
            links=tuple(l for l in extract_links(text, confluence_host=self.site_host) if l != doc_id))

    def fetch(self, doc_id: str) -> Optional[SourceDoc]:
        key = issue_key_of(doc_id)
        if not key:
            return None
        issue = self._issue(key)
        if issue is None or str(issue.get("key")) != key:        # gone, or moved to another key
            return None
        return self._build_doc(issue)

    # ---------------------------------------------------------------------------------------
    # ACL
    # ---------------------------------------------------------------------------------------
    def _role_actors(self, project: str, role_id: str) -> Optional[dict[str, Any]]:
        """Normalised actors of one project role, or None when the role cannot be read."""
        data = self._get(f"project/{project}/role/{role_id}", allow_404=True)
        if not isinstance(data, dict):
            return None
        users, groups, unsupported = set(), set(), set()
        for a in data.get("actors") or []:
            if not isinstance(a, dict):
                continue
            t = a.get("type")
            if t == "atlassian-user-role-actor" and _acc(a.get("actorUser")):
                users.add(_acc(a.get("actorUser")))
            elif t == "atlassian-group-role-actor" and isinstance(a.get("actorGroup"), dict):
                g = a["actorGroup"]
                gid = g.get("groupId") or g.get("name")
                if gid:
                    groups.add(str(gid))
                else:
                    unsupported.add("group-without-id")
            else:
                unsupported.add(str(t))
        return {"users": sorted(users), "groups": sorted(groups), "unsupported": sorted(unsupported)}

    def _norm_holders(self, holders: Sequence[Any], project: str) -> dict[str, Any]:
        users, groups, unsupported = set(), set(), set()
        roles: dict[str, Any] = {}
        reporter = assignee = False
        for h in holders:
            if not isinstance(h, dict):
                continue
            t = h.get("type")
            if t == "user" and (h.get("parameter") or h.get("value")):
                users.add(str(h.get("parameter") or h.get("value")))
            elif t == "group" and (h.get("value") or h.get("parameter")):
                groups.add(str(h.get("value") or h.get("parameter")))
            elif t == "projectRole" and (h.get("parameter") or h.get("value")):
                rid = str(h.get("parameter") or h.get("value"))
                if rid not in roles:
                    roles[rid] = self._role_actors(project, rid)
            elif t == "reporter":
                reporter = True
            elif t == "assignee":
                assignee = True
            else:                                       # custom field, application role, lead, …
                unsupported.add(str(t))
        return {"users": sorted(users), "groups": sorted(groups), "roles": roles,
                "reporter": reporter, "assignee": assignee, "unsupported": sorted(unsupported)}

    def _project_policy(self, project: str) -> Optional[dict[str, Any]]:
        """Browse-projects holders of the project's permission scheme (normalised), or None."""
        data = self._get(f"project/{project}/permissionscheme", {"expand": "permissions"},
                         allow_404=True)
        if not isinstance(data, dict):
            return None
        perms = data.get("permissions")
        if perms is None and data.get("id") is not None:     # real Jira: scheme ref only
            full = self._get(f"permissionscheme/{data['id']}", {"expand": "permissions"},
                             allow_404=True)
            perms = full.get("permissions") if isinstance(full, dict) else None
        if not isinstance(perms, list):
            return None
        holders = [p.get("holder") for p in perms if isinstance(p, dict) and p.get("permission") == BROWSE]
        return self._norm_holders(holders, project)

    def _level_policy(self, level_id: str, project: str) -> Optional[dict[str, Any]]:
        holders: list[Any] = []
        start = 0
        for _ in range(10_000):
            data = self._get("issuesecurityschemes/level/member",
                             {"levelId": level_id, "startAt": start, "maxResults": 50}, allow_404=True)
            if not isinstance(data, dict):
                return None
            vals = [v for v in (data.get("values") or []) if isinstance(v, dict)]
            holders.extend(v.get("holder") for v in vals
                           if str(v.get("issueSecurityLevelId", level_id)) == str(level_id))
            start += len(vals)
            if data.get("isLast", True) or not vals:
                break
        return self._norm_holders(holders, project)

    def _branch(self, pol: Mapping[str, Any], reporter: Optional[str], assignee: Optional[str],
                what: str) -> AclExpr:
        d = self.directory
        parts: list[AclExpr] = [A.users(*[native_to_ib(d, PLATFORM, a) or "" for a in pol["users"]]),
                                A.groups(*[self._group(g) or "" for g in pol["groups"]])]
        for rid in sorted(pol["roles"]):
            act = pol["roles"][rid]
            if act is None:
                parts.append(A.nobody(f"jira-{what}-role-unreadable"))
                continue
            parts.append(A.users(*[native_to_ib(d, PLATFORM, a) or "" for a in act["users"]]))
            parts.append(A.groups(*[self._group(g) or "" for g in act["groups"]]))
            for t in act["unsupported"]:
                parts.append(A.nobody(f"jira-{what}-unsupported-role-actor-{t}"))
        if pol["reporter"]:
            parts.append(A.users(native_to_ib(d, PLATFORM, reporter) or ""))
        if pol["assignee"]:
            parts.append(A.users(native_to_ib(d, PLATFORM, assignee) or ""))
        for t in pol["unsupported"]:
            parts.append(A.nobody(f"jira-{what}-unsupported-holder-{t}"))
        return A.any_of(*parts)

    def _compile(self, project_pol: Optional[Mapping[str, Any]], level_id: Optional[str],
                 level_pol: Optional[Mapping[str, Any]], reporter: Optional[str],
                 assignee: Optional[str]) -> AclExpr:
        if project_pol is None:
            return A.nobody("jira-permission-scheme-unreadable")
        parts = [self._branch(project_pol, reporter, assignee, "browse")]
        if level_id is not None:
            if level_pol is None:
                return A.nobody("jira-security-level-unreadable")
            parts.append(self._branch(level_pol, reporter, assignee, "level"))
        return A.all_of(*parts)

    @staticmethod
    def _meta(issue: Mapping[str, Any]) -> dict[str, Any]:
        f = issue.get("fields") or {}
        sec = f.get("security")
        return {"id": str(issue.get("id") or ""), "u": str(f.get("updated") or ""),
                "p": str((f.get("project") or {}).get("key") or str(issue["key"]).rsplit("-", 1)[0]),
                "l": str(sec["id"]) if isinstance(sec, dict) and sec.get("id") is not None else None,
                "r": _acc(f.get("reporter")), "a": _acc(f.get("assignee"))}

    def acl(self, doc_id: str) -> AclExpr:
        key = issue_key_of(doc_id)
        if not key:
            return A.nobody("jira-bad-doc-id")
        issue = self._issue(key, LIST_FIELDS)
        if issue is None or str(issue.get("key")) != key:
            return A.nobody("jira-issue-gone")
        m = self._meta(issue)
        level_pol = self._level_policy(m["l"], m["p"]) if m["l"] is not None else None
        return self._compile(self._project_policy(m["p"]), m["l"], level_pol, m["r"], m["a"])

    # ---------------------------------------------------------------------------------------
    # Gate 2
    # ---------------------------------------------------------------------------------------
    def check_access(self, user: DirectoryUser, doc_id: str,
                     known_version: Optional[str] = None) -> AccessCheck:
        def verdict(v: str, reason: str = "", version: Optional[str] = None) -> AccessCheck:
            return AccessCheck(doc_id=doc_id, verdict=v, reason_code=reason, current_version=version,
                               checked_at=now_utc())

        key = issue_key_of(doc_id)
        if not key:
            return verdict("forbidden", "bad_doc_id")
        if not user.active:
            return verdict("forbidden", "deactivated")
        account = user.platform_ids.get(PLATFORM)
        if not account:
            return verdict("forbidden", "no_platform_identity")
        if self.admin_client is None:
            return verdict("unverifiable", "no_admin_checker")
        try:
            iresp = request_gate2(self.client, "GET", self._url(f"issue/{key}"),
                                  params={"fields": "updated"}, ok_statuses=(404,))
            if iresp.status_code == 404:
                return verdict("vanished", "issue_gone")
            issue = json_of(iresp, gate2=True)
            if not isinstance(issue, dict) or not issue.get("id"):
                return verdict("forbidden", "source_bad_payload")
            if str(issue.get("key")) != key:
                return verdict("vanished", "issue_moved")
            issue_id = str(issue["id"])
            current = str((issue.get("fields") or {}).get("updated") or "")
            if not current:
                return verdict("forbidden", "source_bad_payload")
            cresp = request_gate2(self.admin_client, "POST", self._url("permissions/check"),
                                  json_body={"accountId": account, "projectPermissions": [
                                      {"issues": [int(issue_id) if issue_id.isdigit() else issue_id],
                                       "permissions": [BROWSE]}]})
            data = json_of(cresp, gate2=True)
            entries = data.get("projectPermissions") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                return verdict("forbidden", "source_bad_payload")
            browse = [e for e in entries if isinstance(e, dict) and e.get("permission") == BROWSE]
            if len(browse) != 1 or not isinstance(browse[0].get("issues"), list):
                return verdict("forbidden", "source_ambiguous")
            if issue_id not in {str(x) for x in browse[0]["issues"]}:
                return verdict("forbidden", "not_permitted")
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
            raise ConnectorError("unreadable jira cursor") from exc
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
            raise ConnectorError("unknown jira cursor version")
        return data

    def list_changes(self, cursor: Optional[str]) -> ChangeBatch:
        """Incremental JQL since the high-water ``updated`` minus 2 min, plus a full id listing
        every ``full_every_s`` (and on the first crawl) to see deletions. Cursor JSON::

            {"v":1, "hw": "<max updated>", "last_full": <epoch s>,
             "issues": {"<KEY>": {"id","u","p","l","r","a"}},      # updated, project, level, people
             "acl": {"<KEY>": "<fp(project policy, level policy, reporter, assignee)>"}}
        """
        prev = self._load_cursor(cursor)
        now = self._clock()
        full = prev is None or (now - float(prev.get("last_full", 0))) >= self.full_every_s
        prev_issues: dict[str, Any] = (prev or {}).get("issues", {})
        prev_acl: dict[str, str] = (prev or {}).get("acl", {})

        changed: dict[str, dict[str, Any]] = {}
        if prev is None:
            for it in self._search(FULL_JQL, "*all"):
                changed[str(it["key"])] = it
            listing: Optional[dict[str, dict[str, Any]]] = {k: self._meta(v) for k, v in changed.items()}
        else:
            since = parse_iso(prev.get("hw")) - OVERLAP
            jql = f'updated >= "{since.isoformat(timespec="seconds")}" ORDER BY updated'
            for it in self._search(jql, "*all"):
                changed[str(it["key"])] = it
            listing = ({str(it["key"]): self._meta(it) for it in self._search(FULL_JQL, LIST_FIELDS)}
                       if full else None)

        issues: dict[str, dict[str, Any]] = dict(listing) if listing is not None else dict(prev_issues)
        for k, it in changed.items():
            issues[k] = self._meta(it)
        if listing is not None:                  # moved past the incremental window: fetch directly
            for k, m in list(issues.items()):
                if k not in changed and k in prev_issues and prev_issues[k].get("u") != m["u"]:
                    it = self._issue(k)
                    if it is None:
                        issues.pop(k)
                    else:
                        changed[k] = it
                        issues[k] = self._meta(it)
        deleted = tuple(f"{PLATFORM}:{k}" for k in prev_issues if k not in issues)

        docs = [self._build_doc(changed[k]) for k in sorted(changed)
                if k in issues and (prev_issues.get(k) or {}).get("u") != issues[k]["u"]]

        project_fp: dict[str, str] = {}
        for p in sorted({m["p"] for m in issues.values()}):
            project_fp[p] = _fp(self._project_policy(p))
        level_fp: dict[str, str] = {}
        for p, l in sorted({(m["p"], m["l"]) for m in issues.values() if m["l"] is not None}):
            level_fp[f"{p}:{l}"] = _fp(self._level_policy(l, p))
        new_acl: dict[str, str] = {}
        acl_changed: list[str] = []
        for k, m in sorted(issues.items()):
            new_acl[k] = _fp({"p": project_fp[m["p"]],
                              "l": level_fp.get(f"{m['p']}:{m['l']}") if m["l"] is not None else None,
                              "lid": m["l"], "r": m["r"], "a": m["a"]})
            if k in prev_acl and prev_acl[k] != new_acl[k]:
                acl_changed.append(f"{PLATFORM}:{k}")

        hws = [parse_iso(m["u"]) for m in issues.values() if m["u"]]
        hw = max(hws).isoformat() if hws else (prev or {}).get("hw", "1970-01-01T00:00:00+00:00")
        new_cursor = json.dumps({"v": CURSOR_VERSION, "hw": hw,
                                 "last_full": now if full else float(prev.get("last_full", 0)),
                                 "issues": issues, "acl": new_acl},
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


__all__ = ["JiraConnector", "issue_key_of", "adf_to_text", "WebhookRejected"]
