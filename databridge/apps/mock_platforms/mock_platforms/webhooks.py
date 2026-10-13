"""Signed change webhooks, mock -> Internal Brain (CONTRACTS §2 "Webhooks").

Every admin mutation calls ``WebhookEmitter.emit``. The body is
``{"platform","type","object_ids","container","user","at"}`` and is signed with
``X-IB-Signature: sha256=<hex HMAC-SHA256(secret, f"{timestamp}.{delivery_id}.{body}")>`` — the
``X-IB-Delivery`` id is inside the MAC, so a captured delivery cannot be replayed under a new id.

Delivery goes either to an in-process ``sink(platform, headers, body_bytes)`` (tests) or, when a
``webhook_url`` is configured, to ``POST {webhook_url}/webhooks/{platform}`` on a single background
worker (fire-and-forget, short timeout, failures logged and never raised). Every emitted delivery
is also kept in ``self.sent`` so tests and the demo can inspect what was sent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, Sequence

import httpx

log = logging.getLogger("mock_platforms.webhooks")

EVENT_TYPES = ("content.changed", "content.deleted", "acl.changed", "membership.changed",
               "user.changed")

Sink = Callable[[str, dict[str, str], bytes], Any]


def sign(secret: str, timestamp: str, delivery_id: str, body: bytes | str) -> str:
    """``sha256=<hex>`` over ``f"{timestamp}.{delivery_id}.{body}"`` (body as the exact UTF-8 text
    sent)."""
    text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.{delivery_id}.{text}".encode("utf-8"),
                   hashlib.sha256)
    return "sha256=" + mac.hexdigest()


class WebhookEmitter:
    def __init__(self, secret: str, *, sink: Optional[Sink] = None,
                 url: Optional[str] = None, timeout_s: float = 2.0) -> None:
        self.secret = secret
        self.sink = sink
        self.url = url.rstrip("/") if url else None
        self.timeout_s = timeout_s
        self.sent: list[dict[str, Any]] = []
        self._pool: Optional[ThreadPoolExecutor] = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="mock-webhook")
            if self.url and sink is None else None)

    def emit(self, platform: str, type_: str, object_ids: Sequence[str], *, at: str,
             container: Optional[str] = None, user: Optional[str] = None) -> dict[str, Any]:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown webhook type {type_!r}")
        payload = {"platform": platform, "type": type_, "object_ids": list(object_ids),
                   "container": container, "user": user, "at": at}
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        delivery = str(uuid.uuid4())
        headers = {"Content-Type": "application/json",
                   "X-IB-Delivery": delivery,
                   "X-IB-Timestamp": ts,
                   "X-IB-Signature": sign(self.secret, ts, delivery, body)}
        record = {"platform": platform, "headers": headers, "body": body, "payload": payload}
        self.sent.append(record)
        if self.sink is not None:
            try:
                self.sink(platform, dict(headers), body)
            except Exception:  # a broken sink must never break the mutation
                log.exception("webhook sink raised for %s %s", platform, type_)
        elif self._pool is not None:
            self._pool.submit(self._post, platform, headers, body)
        return record

    def _post(self, platform: str, headers: dict[str, str], body: bytes) -> None:
        target = f"{self.url}/webhooks/{platform}"
        try:
            r = httpx.post(target, content=body, headers=headers, timeout=self.timeout_s)
            if r.status_code >= 300:
                log.warning("webhook %s -> %s returned %s", headers["X-IB-Delivery"], target,
                            r.status_code)
        except Exception as exc:  # fire-and-forget: log, never raise
            log.warning("webhook %s -> %s failed: %s", headers["X-IB-Delivery"], target, exc)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
