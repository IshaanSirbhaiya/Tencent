"""Shared constants and the independent webhook-signature check for the mock-platform tests."""
from __future__ import annotations

import hashlib
import hmac

SVC = "svc-test"
ADMIN = "admin-test"
SECRET = "whsec-test"


def verify_signature(headers: dict[str, str], body: bytes, secret: str = SECRET) -> bool:
    """Independent re-implementation of the receiver's check (CONTRACTS §2 Webhooks)."""
    ts = headers["X-IB-Timestamp"]
    delivery = headers["X-IB-Delivery"]
    want = "sha256=" + hmac.new(secret.encode(), f"{ts}.{delivery}.{body.decode()}".encode(),
                                hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, headers["X-IB-Signature"])
