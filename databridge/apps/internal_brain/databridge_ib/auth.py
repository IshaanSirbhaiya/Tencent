"""Signed, short-lived principal tokens (plan §4.9, P8).

Identity is ONLY ever this token's ``sub`` — from the ``ib_session`` cookie (UI) or an
``Authorization: Bearer`` header (MCP). No request body, tool argument or model output can name a
principal. Tokens are HMAC-SHA256 over canonical JSON: ``{sub, roles, exp, aud:"ib", jti}``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Optional

AUD = "ib"
MAX_TTL_S = 8 * 3600


class TokenError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint(key: str, sub: str, roles: list[str], *, ttl_s: int = 3600,
         now: Optional[float] = None) -> str:
    ttl_s = max(60, min(int(ttl_s), MAX_TTL_S))
    now = time.time() if now is None else now
    claims = {"sub": sub, "roles": sorted(roles), "exp": int(now + ttl_s), "aud": AUD,
              "jti": uuid.uuid4().hex}
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    sig = _b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(key: str, token: str, *, revoked: frozenset[str] = frozenset(),
           now: Optional[float] = None) -> dict:
    try:
        body, sig = token.split(".", 1)
    except (ValueError, AttributeError):
        raise TokenError("malformed token")
    want = _b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(want, sig):
        raise TokenError("bad signature")
    try:
        claims = json.loads(_unb64(body))
    except Exception:                                               # noqa: BLE001
        raise TokenError("malformed claims")
    now = time.time() if now is None else now
    if claims.get("aud") != AUD:
        raise TokenError("wrong audience")
    if not isinstance(claims.get("exp"), int) or claims["exp"] < now:
        raise TokenError("expired")
    if claims.get("jti") in revoked:
        raise TokenError("revoked")
    if not claims.get("sub"):
        raise TokenError("no subject")
    return claims


def load_revoked(path: Path) -> frozenset[str]:
    try:
        return frozenset(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return frozenset()


def revoke(path: Path, jti: str) -> None:
    current = set(load_revoked(path))
    current.add(jti)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(sorted(current)), encoding="utf-8")
