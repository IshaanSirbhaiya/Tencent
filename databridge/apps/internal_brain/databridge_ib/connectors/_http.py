"""Shared plumbing for the IB connectors (A3): HTTP calls, webhook verification, link extraction.

Two HTTP disciplines, deliberately different:

* **Ingest** (``request_ingest``): retries 429 honouring ``Retry-After`` and retries 5xx/transport
  errors with a short backoff. The crawler can afford to wait.
* **Gate 2** (``request_gate2``): exactly ONE attempt with a hard timeout. Any failure surfaces as
  ``SourceFailure`` so the caller can deny with a reason code. Gate 2 never waits out a rate limit.

Webhooks: ``X-IB-Signature: sha256=<hex HMAC-SHA256(secret, f"{ts}.{delivery}.{body}")>`` — the
delivery id is INSIDE the MAC (Codex sec3 #8/#11: otherwise one captured delivery could be replayed
under fresh ids) — ``X-IB-Timestamp`` within ±300 s, ``X-IB-Delivery`` never seen before. The
replay set is in-process: a second worker or a restart can accept the same delivery once more inside
the window (documented in SECURITY.md; events only invalidate, so a replay costs work, not access).

Pagination: every paged loop goes through ``PageGuard`` — a page cap or a repeated cursor raises
(``ConnectorError`` on ingest, ``SourceFailure("source_incomplete")`` at Gate 2): a partial listing is
never returned as if it were complete (Codex sec3a #12, sec3b #6).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Union, runtime_checkable
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import GATE2_TIMEOUT_S
from ..models import DirectoryUser
from .base import ConnectorError, WebhookRejected

WEBHOOK_WINDOW_S = 300
CHANGE_TYPES = ("content.changed", "content.deleted", "acl.changed", "membership.changed",
                "user.changed")


# --------------------------------------------------------------------------------------------
# Directory seam (the coordinator's directory implements this)
# --------------------------------------------------------------------------------------------
@runtime_checkable
class DirectoryIndex(Protocol):
    def by_platform_id(self, platform: str, native_id: str) -> Optional[DirectoryUser]: ...

    def get(self, user_id: str) -> Optional[DirectoryUser]: ...

    def all(self) -> list[DirectoryUser]: ...


DirectorySource = Union[DirectoryIndex, Callable[[], DirectoryIndex]]


def resolve_directory(source: DirectorySource) -> DirectoryIndex:
    """Accept either the index itself or a zero-arg provider (so a reloaded directory is seen)."""
    if hasattr(source, "by_platform_id"):
        return source                                               # type: ignore[return-value]
    return source()                                                 # type: ignore[operator]


def native_to_ib(directory: DirectoryIndex, platform: str, native_id: Optional[str]) -> Optional[str]:
    if not native_id:
        return None
    u = directory.by_platform_id(platform, native_id)
    return u.id if u else None


def event_user(directory: DirectoryIndex, platform: str, raw: Optional[str],
               looks_native: Callable[[str], bool]) -> Optional[str]:
    """The IB user an event's ``user`` field names (Codex sec3a #10, sec3b #13).

    1. a NATIVE id the directory maps for ``platform`` wins (so native "123" → Alice even when an
       unrelated IB user is literally called "123");
    2. a value in the platform's native id format that the directory does NOT map is unresolved
       (``None``) — never reinterpreted as an IB id;
    3. otherwise it is accepted as an IB id only if the directory knows it; anything else is ``None``.
    """
    if not raw or not isinstance(raw, str):
        return None
    hit = native_to_ib(directory, platform, raw)
    if hit:
        return hit
    if looks_native(raw):
        return None
    return raw if directory.get(raw) is not None else None


MAX_PAGES = 10_000


class PageGuard:
    """Bounds a cursor loop: ``next_page(cursor)`` before each request after the first. Raises when
    the cap is hit with pages remaining or a cursor repeats (a cycle) — never a silent partial."""

    def __init__(self, what: str, *, gate2: bool = False, max_pages: Optional[int] = None):
        self.what = what
        self.gate2 = gate2
        self.max_pages = max_pages if max_pages is not None else MAX_PAGES
        self.pages = 0
        self._seen: set[str] = set()

    def _fail(self, why: str) -> Exception:
        if self.gate2:
            return SourceFailure("source_incomplete", detail=f"{self.what}: {why}")
        return ConnectorError(f"{self.what}: {why} — listing incomplete")

    def next_page(self, cursor: Any) -> None:
        """Account for one more request (with ``cursor``; '' for the first)."""
        self.pages += 1
        if self.pages > self.max_pages:
            raise self._fail(f"more than {self.max_pages} pages")
        key = str(cursor or "")
        if key:
            if key in self._seen:
                raise self._fail("pagination cursor repeated")
            self._seen.add(key)


# --------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------
class SourceFailure(Exception):
    """A Gate-2 call failed. ``reason`` is a ledger reason code; ``status`` the HTTP status if any."""

    def __init__(self, reason: str, status: Optional[int] = None, detail: str = ""):
        super().__init__(f"{reason} ({status}) {detail}".strip())
        self.reason = reason
        self.status = status


def _retry_after_s(resp: httpx.Response, default: float) -> float:
    raw = resp.headers.get("Retry-After", "")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def request_ingest(client: httpx.Client, method: str, url: str, *, params: Any = None,
                   json_body: Any = None, attempts: int = 5, max_wait_s: float = 30.0,
                   sleep: Callable[[float], None] = time.sleep,
                   ok_statuses: tuple[int, ...] = ()) -> httpx.Response:
    """Ingest call with retries. Returns the response for 2xx (or a status in ``ok_statuses``,
    e.g. 404 the caller wants to interpret); raises ``ConnectorError`` once retries are spent."""
    last = "no attempt"
    for attempt in range(attempts):
        try:
            resp = client.request(method, url, params=params, json=json_body)
        except httpx.HTTPError as exc:                              # transport / timeout
            last = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                sleep(min(max_wait_s, 0.5 * (2 ** attempt)))
            continue
        if resp.status_code == 429:
            last = "429 rate limited"
            if attempt + 1 < attempts:
                sleep(min(max_wait_s, _retry_after_s(resp, 1.0)))
            continue
        if resp.status_code >= 500:
            last = f"{resp.status_code} server error"
            if attempt + 1 < attempts:
                sleep(min(max_wait_s, 0.5 * (2 ** attempt)))
            continue
        if resp.is_success or resp.status_code in ok_statuses:
            return resp
        raise ConnectorError(f"{method} {url} -> {resp.status_code}")
    raise ConnectorError(f"{method} {url} failed after {attempts} attempts: {last}")


def request_gate2(client: httpx.Client, method: str, url: str, *, params: Any = None,
                  json_body: Any = None, timeout_s: float = GATE2_TIMEOUT_S,
                  ok_statuses: tuple[int, ...] = ()) -> httpx.Response:
    """One attempt, hard timeout, no retry. Raises ``SourceFailure`` on anything but 2xx (or a
    status listed in ``ok_statuses``, which the caller interprets itself)."""
    try:
        resp = client.request(method, url, params=params, json=json_body, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise SourceFailure("source_timeout", detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise SourceFailure("source_error", detail=str(exc)) from exc
    if resp.status_code == 429:
        raise SourceFailure("source_rate_limited", 429)
    if resp.is_success or resp.status_code in ok_statuses:
        return resp
    raise SourceFailure("source_error", resp.status_code)


def json_of(resp: httpx.Response, *, gate2: bool = False) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        if gate2:
            raise SourceFailure("source_bad_payload", resp.status_code) from exc
        raise ConnectorError(f"non-JSON response from {resp.request.url}") from exc


# --------------------------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------------------------
def sign_webhook(secret: str, timestamp: Union[int, str], delivery_id: str, body: bytes) -> str:
    """``sha256=<hex>`` over ``f"{timestamp}.{delivery_id}.{body}"``, exactly as the mock signs it
    (also used by tests). The delivery id is authenticated, so it cannot be swapped on a replay."""
    msg = str(timestamp).encode() + b"." + str(delivery_id).encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _header(headers: Mapping[str, str], name: str) -> str:
    want = name.lower()
    for k, v in headers.items():
        if k.lower() == want:
            return v
    return ""


class WebhookVerifier:
    """Signature (over timestamp + delivery id + body) + ±300 s window + replayed-delivery set.
    Thread-safe; replay memory is bounded to ids whose timestamps are still inside the window
    (older ones fail the window check anyway). The set is PER PROCESS: multi-worker / restart replay
    inside the window is a documented limitation (SECURITY.md, track 3 disposition)."""

    def __init__(self, secret: str, *, window_s: int = WEBHOOK_WINDOW_S,
                 now: Callable[[], float] = time.time):
        if not secret:
            raise ValueError("webhook secret is required")
        self._secret = secret
        self._window = window_s
        self._now = now
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(self, headers: Mapping[str, str], body: bytes) -> tuple[str, int]:
        delivery = _header(headers, "X-IB-Delivery").strip()
        ts_raw = _header(headers, "X-IB-Timestamp").strip()
        sig = _header(headers, "X-IB-Signature").strip()
        if not delivery or not ts_raw or not sig:
            raise WebhookRejected("missing signature headers")
        try:
            ts = int(ts_raw)
        except ValueError as exc:
            raise WebhookRejected("bad timestamp") from exc
        expected = sign_webhook(self._secret, ts_raw, delivery, body)
        if not hmac.compare_digest(expected.encode(), sig.encode()):
            raise WebhookRejected("bad signature")
        now = self._now()
        if abs(now - ts) > self._window:
            raise WebhookRejected("stale timestamp")
        with self._lock:
            for d, seen_ts in list(self._seen.items()):
                if now - seen_ts > 2 * self._window:
                    del self._seen[d]
            if delivery in self._seen:
                raise WebhookRejected("replayed delivery")
            self._seen[delivery] = float(ts)
        return delivery, ts


def parse_webhook_body(body: bytes, platform: str) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookRejected("body is not JSON") from exc
    if not isinstance(data, dict):
        raise WebhookRejected("body is not an object")
    if data.get("platform") != platform:
        raise WebhookRejected("platform mismatch")
    if data.get("type") not in CHANGE_TYPES:
        raise WebhookRejected("unknown event type")
    ids = data.get("object_ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise WebhookRejected("bad object_ids")
    return data


def event_time(data: Mapping[str, Any], ts: int) -> datetime:
    raw = data.get("at")
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# --------------------------------------------------------------------------------------------
# Links (plan §4.2 step 7): ONLY these shapes, ONLY tenant hosts. Output uses IB doc-id style.
# --------------------------------------------------------------------------------------------
_JIRA_KEY = re.compile(r"(?<![A-Za-z0-9_-])([A-Z]{2,10}-\d+)(?![A-Za-z0-9_])")
_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}|\\^`]+")
_SLACK_TS = re.compile(r"^p(\d{10})(\d{6})$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def extract_links(text: str, *, slack_host: str = "companya.slack.com",
                  confluence_host: str = "companya.atlassian.net",
                  drive_host: str = "docs.google.com") -> tuple[str, ...]:
    """Supported identifiers in ``text``, de-duplicated in first-seen order:

    * Jira keys ``ABC-123``                              → ``jira:ABC-123``
    * ``https://<confluence_host>/wiki/.../pages/<id>``   → ``confluence:<id>``
    * ``https://<slack_host>/archives/<C..>/p<16 digits>`` → ``slack:<channel>:<thread ts>``
      (``?thread_ts=`` wins, so a permalink to a reply resolves to its thread)
    * ``https://<drive_host>/<kind>/d/<id>``             → ``drive:<id>``

    Any other URL — other hosts, look-alike hosts, other path shapes — is ignored.
    """
    out: list[str] = []

    def add(x: str) -> None:
        if x not in out:
            out.append(x)

    for m in _URL.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?")
        try:
            u = urlparse(url)
        except ValueError:
            continue
        host = (u.hostname or "").lower()
        parts = [p for p in u.path.split("/") if p]
        if host == confluence_host and parts[:1] == ["wiki"]:
            if "pages" in parts:
                i = parts.index("pages")
                if i + 1 < len(parts) and _ID.match(parts[i + 1]):
                    add(f"confluence:{parts[i + 1]}")
        elif host == slack_host and len(parts) >= 3 and parts[0] == "archives":
            chan, msg = parts[1], parts[2]
            mm = _SLACK_TS.match(msg)
            if _ID.match(chan) and mm:
                ts = f"{mm.group(1)}.{mm.group(2)}"
                tts = parse_qs(u.query).get("thread_ts", [""])[0]
                if re.fullmatch(r"\d{10}\.\d{6}", tts or ""):
                    ts = tts
                add(f"slack:{chan}:{ts}")
        elif host == drive_host and len(parts) >= 3 and parts[1] == "d" and _ID.match(parts[2]):
            add(f"drive:{parts[2]}")
    for m in _JIRA_KEY.finditer(text or ""):
        add(f"jira:{m.group(1)}")
    return tuple(out)


def utc_from_epoch(ts: Union[str, float, None]) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def parse_iso(raw: Any, default: Optional[datetime] = None) -> datetime:
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return default or datetime.fromtimestamp(0, tz=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
