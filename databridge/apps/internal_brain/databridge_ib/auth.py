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
            return _verify(key, token, revoked=revoked, now=now)
        except TokenError:
            raise
        except Exception:                                           # noqa: BLE001
            raise TokenError("malformed token")       # any decoding/type error is a plain 401


def _verify(key: str, token: str, *, revoked: frozenset[str], now: Optional[float]) -> dict:
    if not isinstance(token, str) or len(token) > 4096 or not token.isascii():
        raise TokenError("malformed token")
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
    if not isinstance(claims, dict):
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


class RevocationUnavailable(TokenError):
    """The revocation list exists but cannot be read: authentication fails CLOSED (sec1 #5)."""


def load_revoked(path: Path) -> frozenset[str]:
    p = Path(path)
    if not p.exists():
        return frozenset()                    # no revocations recorded yet
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RevocationUnavailable("revocation list unreadable")
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise RevocationUnavailable("revocation list malformed")
    return frozenset(data)


_REVOKE_LOCK = __import__("threading").Lock()


def revoke(path: Path, jti: str) -> None:
    """Atomic (temp file + replace) and serialised within the process."""
    p = Path(path)
    with _REVOKE_LOCK:
        current = set(load_revoked(p))
        current.add(jti)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(sorted(current)), encoding="utf-8")
        tmp.replace(p)
