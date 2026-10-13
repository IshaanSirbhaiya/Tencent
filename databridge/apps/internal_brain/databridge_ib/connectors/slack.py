"""Slack connector (A3) — supported subset per plan §4.5.

Documents: one ``SourceDoc`` per THREAD, ``doc_id = "slack:<channel>:<thread_ts>"``.

Permission semantics compiled into ``AclExpr``:
* public channel  → ``any(users(members), all(kind(member), everyone))`` — every full member of the
  workspace, plus guests / Connect users only if they are in the channel;
* private channel and Slack Connect (``is_ext_shared``) channel → ``users(members)``;
* archived channel → unchanged (still readable by whoever could read it);
* DMs / group DMs / anything not returned by ``conversations.list`` → ``nobody`` (unsupported).
  ``is_im`` / ``is_mpim`` are checked FIRST, before private / shared (a private-flagged group DM is
  still a DM); a channel object missing any of ``is_im``, ``is_mpim``, ``is_private``,
  ``is_ext_shared`` (or carrying a non-boolean) is UNCLASSIFIED → ``nobody`` / deny, never "public"
  (Codex sec3a #2, #3).

Gate 2 asks Slack itself (``users.info`` + ``conversations.list`` + ``conversations.members`` +
``conversations.replies``) on every call — one attempt each, 2 s timeout, any failure denies. The
returned user / channel ids must be the ones asked for, and ``deleted``, ``is_restricted``,
``is_ultra_restricted`` and ``is_stranger`` must all be present booleans, else deny.

Thread version (``SourceDoc.version``): ``"<latest ts>|<message count>|<digest>"`` where the digest
covers every message's ts, edit ts, author and text — deleting or editing ANY message (not only the
newest) changes it, so Gate 2 reports drift (Codex sec3a #6). The crawler compares the parent's
``reply_count`` with the stored count, so a deleted reply is re-fetched even when the latest-reply
ts does not move; an edit to an older reply that Slack does not surface on the parent is caught by
Gate 2's drift check on the next answer (and the full reconciliation of the next re-fetch).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Optional, Sequence

import httpx

from .. import acl as A
from ..models import (AccessCheck, AclExpr, ChangeBatch, ChangeEvent, DirectoryUser, SourceDoc,
                      UserKind)
from ._http import (DirectorySource, PageGuard, SourceFailure, WebhookVerifier, event_time,
                    event_user, extract_links, json_of, native_to_ib, now_utc, parse_webhook_body,
                    request_gate2, request_ingest, resolve_directory, utc_from_epoch)
from .base import ConnectorError, WebhookRejected

PLATFORM = "slack"
_TS = re.compile(r"^\d{1,12}\.\d{1,9}$")
_CHAN = re.compile(r"^[A-Z0-9]{2,32}$")
#: doc errors that mean "the thread / channel no longer exists" (→ VANISHED)
_GONE = {"thread_not_found", "channel_not_found", "message_not_found", "not_found"}
CURSOR_VERSION = 1


def split_doc_id(doc_id: str) -> Optional[tuple[str, str]]:
    parts = doc_id.split(":")
    if len(parts) != 3 or parts[0] != PLATFORM:
        return None
    chan, ts = parts[1], parts[2]
    if not _CHAN.match(chan) or not _TS.match(ts):
        return None
    return chan, ts


def thread_doc_id(channel: str, ts: str) -> str:
    return f"{PLATFORM}:{channel}:{ts}"


def _ts_key(ts: str) -> float:
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


#: Slack native user ids (U…/W… member ids, B… bots): such a value is never read as an IB user id
_NATIVE_USER = re.compile(r"^[UWB][A-Z0-9]{2,}$")
_CLASS_FLAGS = ("is_im", "is_mpim", "is_private", "is_ext_shared")
#: kinds that are never indexed / never granted
UNSUPPORTED_KINDS = ("dm", "unclassified")


def _channel_kind(ch: Mapping[str, Any]) -> str:
    """dm | unclassified | connect | private | public. DM flags are checked FIRST; any missing or
    non-boolean classification flag ⇒ ``unclassified`` (fail closed — never "public")."""
    if ch.get("is_im") is True or ch.get("is_mpim") is True:
        return "dm"
    if not all(isinstance(ch.get(k), bool) for k in _CLASS_FLAGS):
        return "unclassified"
    if ch["is_ext_shared"]:
        return "connect"
    if ch["is_private"]:
        return "private"
    return "public"


def version_ts(version: Optional[str]) -> str:
    """The latest-message ts inside a thread version (a bare ts from an older cursor passes)."""
    return str(version or "").split("|", 1)[0]


def version_count(version: Optional[str]) -> Optional[int]:
    parts = str(version or "").split("|")
    try:
        return int(parts[1]) if len(parts) == 3 else None
    except ValueError:
        return None


class SlackConnector:
    platform = PLATFORM

    def __init__(self, client: httpx.Client, directory: DirectorySource, webhook_secret: str,
                 admin_client: Optional[httpx.Client] = None, *, prefix: str = "/slack/api",
                 workspace_host: str = "companya.slack.com", sleep=None, now=None):
        self.client = client
        self.admin_client = admin_client          # unused by Slack Gate 2 (bot token suffices)
        self._directory = directory
        self.prefix = prefix.rstrip("/")
        self.workspace_host = workspace_host
        self._sleep = sleep
        self._verifier = WebhookVerifier(webhook_secret, **({"now": now} if now else {}))

    # ---------------------------------------------------------------------------------------
    # low-level calls
    # ---------------------------------------------------------------------------------------
    @property
    def directory(self):
        return resolve_directory(self._directory)

    def _url(self, method: str) -> str:
        return f"{self.prefix}/{method}"

    def _ingest(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        kw = {"sleep": self._sleep} if self._sleep else {}
        data = json_of(request_ingest(self.client, "GET", self._url(method), params=params, **kw))
        if not isinstance(data, dict):
            raise ConnectorError(f"slack {method}: unexpected payload")
        return data

    def _gate2(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        data = json_of(request_gate2(self.client, "GET", self._url(method), params=params), gate2=True)
        if not isinstance(data, dict):
            raise SourceFailure("source_bad_payload")
        return data

    @staticmethod
    def _next(data: Mapping[str, Any]) -> str:
        return ((data.get("response_metadata") or {}).get("next_cursor") or "").strip()

    def _paged(self, method: str, params: dict[str, Any], key: str, *, gate2: bool = False,
               stop=None) -> list[Any]:
        out: list[Any] = []
        cursor = ""
        guard = PageGuard(f"slack {method}", gate2=gate2)
        while True:
            guard.next_page(cursor)                 # cap / cursor cycle ⇒ raise, never a partial
            p = dict(params, **({"cursor": cursor} if cursor else {}))
            data = self._gate2(method, p) if gate2 else self._ingest(method, p)
            if not data.get("ok", False):
                err = str(data.get("error") or "not_ok")
                if gate2:
                    raise SourceFailure("gone" if err in _GONE else "source_error", detail=err)
                if err in _GONE:
                    raise _Gone(err)
                raise ConnectorError(f"slack {method}: {err}")
            page = data.get(key)
            if page is None:
                page = []
            if not isinstance(page, list):
                if gate2:
                    raise SourceFailure("source_bad_payload", detail=f"{method}.{key}")
                raise ConnectorError(f"slack {method}: {key} is not a list")
            out.extend(page)
            if stop is not None and any(stop(x) for x in page):
                break
            cursor = self._next(data)
            if not cursor:
                break
        return out

    def _channels(self, *, gate2: bool = False, want: Optional[str] = None) -> list[dict[str, Any]]:
        stop = (lambda c: isinstance(c, dict) and c.get("id") == want) if want else None
        return [c for c in self._paged("conversations.list",
                                       {"types": "public_channel,private_channel",
                                        "exclude_archived": "false", "limit": 200},
                                       "channels", gate2=gate2, stop=stop)
                if isinstance(c, dict) and c.get("id")]

    def _channel(self, channel: str, *, gate2: bool = False) -> Optional[dict[str, Any]]:
        """``conversations.info`` (real Slack; the mock serves it too); if the endpoint itself is
        missing (HTTP 404) fall back to scanning ``conversations.list`` (the CONTRACTS §2 subset)."""
        params = {"channel": channel}
        if gate2:
            resp = request_gate2(self.client, "GET", self._url("conversations.info"), params=params,
                                 ok_statuses=(404,))
        else:
            kw = {"sleep": self._sleep} if self._sleep else {}
            resp = request_ingest(self.client, "GET", self._url("conversations.info"), params=params,
                                  ok_statuses=(404,), **kw)
        if resp.status_code != 404:
            data = json_of(resp, gate2=gate2)
            if not isinstance(data, dict):
                raise SourceFailure("source_bad_payload") if gate2 else ConnectorError("bad payload")
            if data.get("ok") and isinstance(data.get("channel"), dict):
                if data["channel"].get("id") != channel:          # answered about another channel
                    if gate2:
                        raise SourceFailure("source_ambiguous", detail="channel id mismatch")
                    raise ConnectorError("slack conversations.info: channel id mismatch")
                return data["channel"]
            err = str(data.get("error") or "not_ok")
            if err in _GONE:
                return None
            if gate2:
                raise SourceFailure("source_error", detail=err)
            raise ConnectorError(f"slack conversations.info: {err}")
        for c in self._channels(gate2=gate2, want=channel):
            if c["id"] == channel:
                return c
        return None

    def _members(self, channel: str, *, gate2: bool = False) -> list[str]:
        return sorted({m for m in self._paged("conversations.members", {"channel": channel, "limit": 200},
                                              "members", gate2=gate2) if isinstance(m, str)})

    def _replies(self, channel: str, ts: str) -> Optional[list[dict[str, Any]]]:
        try:
            msgs = self._paged("conversations.replies", {"channel": channel, "ts": ts, "limit": 200},
                               "messages")
        except _Gone:
            return None
        msgs = [m for m in msgs if isinstance(m, dict) and m.get("ts")]
        return msgs or None

    # ---------------------------------------------------------------------------------------
    # documents
    # ---------------------------------------------------------------------------------------
    @staticmethod
    def _version(msgs: Sequence[Mapping[str, Any]]) -> str:
        """``"<latest ts>|<count>|<digest>"`` — any deletion or edit, of ANY message, changes it."""
        best, best_key = "", -1.0
        rows = []
        for m in msgs:
            edited = m.get("edited")
            ets = edited.get("ts") if isinstance(edited, dict) else None
            for cand in (m.get("ts"), ets):
                if cand and _ts_key(str(cand)) > best_key:
                    best, best_key = str(cand), _ts_key(str(cand))
            rows.append([str(m.get("ts") or ""), str(ets or ""), str(m.get("user") or ""),
                         str(m.get("text") or "")])
        rows.sort()
        digest = hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
                                .encode("utf-8")).hexdigest()[:16]
        return f"{best}|{len(rows)}|{digest}"

    def deep_link(self, channel: str, ts: str) -> str:
        return f"https://{self.workspace_host}/archives/{channel}/p{ts.replace('.', '')}"

    def _build_doc(self, ch: Mapping[str, Any], thread_ts: str,
                   msgs: Sequence[Mapping[str, Any]]) -> SourceDoc:
        d = self.directory
        msgs = sorted(msgs, key=lambda m: _ts_key(str(m.get("ts"))))
        lines = []
        for m in msgs:
            who = native_to_ib(d, PLATFORM, m.get("user")) or (m.get("user") or "unknown")
            lines.append(f"{who}: {(m.get('text') or '').strip()}")
        text = "\n".join(lines)
        parent = next((m for m in msgs if str(m.get("ts")) == thread_ts), msgs[0])
        words = (parent.get("text") or "").split()
        head = " ".join(words[:8]) + (" …" if len(words) > 8 else "")
        name = str(ch.get("name") or ch["id"])
        version = self._version(msgs)
        return SourceDoc(
            doc_id=thread_doc_id(ch["id"], thread_ts), platform=PLATFORM, kind="thread",
            container=ch["id"], container_name=name, title=f"#{name}: {head}".strip(),
            text=text, version=version, created_at=utc_from_epoch(thread_ts),
            updated_at=utc_from_epoch(version_ts(version)),
            deep_link=self.deep_link(ch["id"], thread_ts),
            author=native_to_ib(d, PLATFORM, parent.get("user")),
            links=tuple(l for l in extract_links(text, slack_host=self.workspace_host)
                        if l != thread_doc_id(ch["id"], thread_ts)))

    def fetch(self, doc_id: str) -> Optional[SourceDoc]:
        parsed = split_doc_id(doc_id)
        if not parsed:
            return None
        channel, ts = parsed
        ch = self._channel(channel)
        if ch is None or _channel_kind(ch) in UNSUPPORTED_KINDS:
            return None
        msgs = self._replies(channel, ts)
        if not msgs:
            return None
        return self._build_doc(ch, ts, msgs)

    # ---------------------------------------------------------------------------------------
    # ACL
    # ---------------------------------------------------------------------------------------
    def compile_acl(self, ch: Optional[Mapping[str, Any]], native_members: Sequence[str]) -> AclExpr:
        if ch is None:
            return A.nobody("slack-unknown-channel")
        kind = _channel_kind(ch)
        if kind == "dm":
            return A.nobody("slack-dm-unsupported")
        if kind == "unclassified":
            return A.nobody("slack-unclassified-channel")
        d = self.directory
        members = A.users(*[native_to_ib(d, PLATFORM, m) or "" for m in native_members])
        if kind == "public":
            return A.any_of(members, A.all_of(A.AclKind(kinds=(UserKind.MEMBER,)), A.AclEveryone()))
        return members                                    # private and Slack Connect: members only

    def acl(self, doc_id: str) -> AclExpr:
        parsed = split_doc_id(doc_id)
        if not parsed:
            return A.nobody("slack-bad-doc-id")
        channel, _ = parsed
        if channel[:1] in ("D",):
            return A.nobody("slack-dm-unsupported")
        ch = self._channel(channel)
        if ch is None or _channel_kind(ch) in UNSUPPORTED_KINDS:
            return A.nobody(f"slack-{_channel_kind(ch)}-unsupported" if ch is not None
                            else "slack-unknown-channel")
        try:
            members = self._members(channel)
        except _Gone:
            return A.nobody("slack-unknown-channel")
        return self.compile_acl(ch, members)

    # ---------------------------------------------------------------------------------------
    # Gate 2
    # ---------------------------------------------------------------------------------------
    def check_access(self, user: DirectoryUser, doc_id: str,
                     known_version: Optional[str] = None) -> AccessCheck:
        def verdict(v: str, reason: str = "", version: Optional[str] = None) -> AccessCheck:
            return AccessCheck(doc_id=doc_id, verdict=v, reason_code=reason, current_version=version,
                               checked_at=now_utc())

        parsed = split_doc_id(doc_id)
        if not parsed:
            return verdict("forbidden", "bad_doc_id")
        channel, ts = parsed
        if not user.active:
            return verdict("forbidden", "deactivated")
        native = user.platform_ids.get(PLATFORM)
        if not native:
            return verdict("forbidden", "no_platform_identity")
        try:
            info = self._gate2("users.info", {"user": native})
            if not info.get("ok") or not isinstance(info.get("user"), dict):
                return verdict("forbidden", "unknown_user")
            su = info["user"]
            if su.get("id") != native:                       # answered about someone else
                return verdict("forbidden", "source_ambiguous")
            if not all(isinstance(su.get(k), bool) for k in
                       ("deleted", "is_restricted", "is_ultra_restricted", "is_stranger")):
                return verdict("forbidden", "source_bad_payload")    # missing flags never widen
            if su["deleted"]:
                return verdict("forbidden", "deactivated")
            is_guest = su["is_restricted"] or su["is_ultra_restricted"]
            is_stranger = su["is_stranger"]
            if user.kind is not UserKind.MEMBER:      # directory says guest/connect: never widen
                is_guest = is_guest or user.kind is UserKind.GUEST
                is_stranger = is_stranger or user.kind is UserKind.CONNECT

            ch = self._channel(channel, gate2=True)
            if ch is None:
                return verdict("vanished", "channel_gone")
            kind = _channel_kind(ch)
            if kind == "dm":
                return verdict("forbidden", "unsupported_dm")
            if kind == "unclassified":
                return verdict("forbidden", "unsupported_channel")
            members = set(self._members(channel, gate2=True))
            is_member = native in members
            if kind in ("private", "connect"):
                if not is_member:
                    return verdict("forbidden", "not_member")
            elif not is_member and (is_guest or is_stranger):
                return verdict("forbidden", "guest_not_member")

            try:
                msgs = self._paged("conversations.replies", {"channel": channel, "ts": ts, "limit": 200},
                                   "messages", gate2=True)
            except SourceFailure as exc:
                if exc.reason == "gone":
                    return verdict("vanished", "thread_gone")
                raise
            msgs = [m for m in msgs if isinstance(m, dict) and m.get("ts")]
            if not msgs:
                return verdict("vanished", "thread_gone")
            current = self._version(msgs)
        except SourceFailure as exc:
            reason = "source_error" if exc.reason == "gone" else exc.reason
            return verdict("forbidden", reason)
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
            raise ConnectorError("unreadable slack cursor") from exc
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
            raise ConnectorError("unknown slack cursor version")
        return data

    def list_changes(self, cursor: Optional[str]) -> ChangeBatch:
        """Re-lists every channel and thread parent each time (so deletions are seen) and fetches
        replies only for threads whose latest-reply ts moved. Cursor JSON::

            {"v":1, "channels": {"<C>": {"kind": "public|private|connect", "archived": bool,
                                         "members": [native ids], "latest": "<max ts>",
                                         "threads": {"<thread ts>": "<version>"}}}}
        """
        prev = self._load_cursor(cursor)
        prev_ch: dict[str, Any] = (prev or {}).get("channels", {})
        docs: list[SourceDoc] = []
        deleted: list[str] = []
        acl_changed: list[str] = []
        new_ch: dict[str, Any] = {}

        for ch in self._channels():
            cid = ch["id"]
            kind = _channel_kind(ch)
            if kind in UNSUPPORTED_KINDS:                  # DM / unclassified: never indexed
                continue
            members = self._members(cid)
            old = prev_ch.get(cid) or {}
            old_threads: dict[str, str] = old.get("threads", {}) if old else {}
            threads: dict[str, str] = {}
            try:
                parents = self._paged("conversations.history", {"channel": cid, "limit": 200}, "messages")
            except _Gone:
                parents = []
            for p in parents:
                if not isinstance(p, dict) or not p.get("ts"):
                    continue
                if p.get("thread_ts") and p.get("thread_ts") != p.get("ts"):
                    continue                                # a broadcast reply, not a parent
                ts = str(p["ts"])
                hint = str(p.get("latest_reply") or ts)
                if isinstance(p.get("edited"), dict) and p["edited"].get("ts"):
                    hint = max(hint, str(p["edited"]["ts"]), key=_ts_key)
                old_v = old_threads.get(ts)
                rc = p.get("reply_count")
                if rc is None and not p.get("latest_reply") and not p.get("thread_ts"):
                    rc = 0                                # a plain message: Slack omits the count
                same_count = (isinstance(rc, int) and not isinstance(rc, bool)
                              and version_count(old_v) == rc + 1)
                if (old_v is not None and same_count
                        and _ts_key(version_ts(old_v)) >= _ts_key(hint)):
                    threads[ts] = old_v                   # nothing newer, nothing deleted
                    continue
                msgs = self._replies(cid, ts)
                if not msgs:
                    continue
                doc = self._build_doc(ch, ts, msgs)
                if doc.version != old_v:                  # re-fetched but unchanged: not re-emitted
                    docs.append(doc)
                threads[ts] = doc.version
            for ts in old_threads:
                if ts not in threads:
                    deleted.append(thread_doc_id(cid, ts))
            if old and (old.get("members") != members or old.get("kind") != kind
                        or bool(old.get("archived")) != bool(ch.get("is_archived"))):
                # every surviving doc of the channel (content-changed ones too: their ACL moved as well)
                acl_changed.extend(thread_doc_id(cid, ts) for ts in threads if ts in old_threads)
            new_ch[cid] = {"kind": kind, "archived": bool(ch.get("is_archived")), "members": members,
                           "latest": (max((version_ts(v) for v in threads.values()), key=_ts_key)
                                      if threads else ""),
                           "threads": threads}
        for cid, old in prev_ch.items():
            if cid not in new_ch:
                deleted.extend(thread_doc_id(cid, ts) for ts in (old.get("threads") or {}))
        new_cursor = json.dumps({"v": CURSOR_VERSION, "channels": new_ch}, sort_keys=True,
                                separators=(",", ":"))
        return ChangeBatch(docs=tuple(docs), deleted=tuple(deleted), acl_changed=tuple(acl_changed),
                           cursor=new_cursor)

    # ---------------------------------------------------------------------------------------
    # webhooks
    # ---------------------------------------------------------------------------------------
    def _doc_ids(self, ids: Sequence[str], container: Optional[str]) -> tuple[str, ...]:
        out = []
        for raw in ids:
            if raw.startswith(f"{PLATFORM}:"):
                out.append(raw)
            elif ":" in raw:                                    # "<channel>:<ts>"
                out.append(f"{PLATFORM}:{raw}")
            elif _TS.match(raw) and container:
                out.append(thread_doc_id(container, raw))
            else:
                out.append(raw)                                 # e.g. a channel id; left native
        return tuple(out)

    def _ib_user(self, raw: Optional[str]) -> Optional[str]:
        """Native id via the directory FIRST; an unmapped native-format id is unresolved (None);
        an IB id is accepted only if the directory knows it (Codex sec3a #10)."""
        return event_user(self.directory, PLATFORM, raw, lambda v: bool(_NATIVE_USER.match(v)))

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> Sequence[ChangeEvent]:
        delivery, ts = self._verifier.verify(headers, body)
        data = parse_webhook_body(body, PLATFORM)
        container = data.get("container") or None
        etype = data["type"]
        ids = data.get("object_ids") or []
        object_ids = (self._doc_ids(ids, container) if etype in ("content.changed", "content.deleted",
                                                                 "acl.changed") else tuple(ids))
        return [ChangeEvent(platform=PLATFORM, type=etype, object_ids=object_ids, container=container,
                            user=self._ib_user(data.get("user")), delivery_id=delivery,
                            at=event_time(data, ts))]


class _Gone(Exception):
    """Slack answered ``ok:false`` with a not-found error (ingest path)."""


__all__ = ["SlackConnector", "split_doc_id", "thread_doc_id", "WebhookRejected"]
