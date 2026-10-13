"""Shared plumbing: bearer auth, per-platform rate limit, platform-shaped errors, opaque cursors."""
from __future__ import annotations

import base64
import binascii
from typing import Any, Callable, Optional, Sequence

from fastapi import Request
from fastapi.responses import JSONResponse

from .state import World

DEFAULT_PAGE = 50
MAX_PAGE = 1000


class PlatformError(Exception):
    def __init__(self, platform: str, status: int, code: str, message: str = "",
                 headers: Optional[dict[str, str]] = None) -> None:
        super().__init__(message or code)
        self.platform = platform
        self.status = status
        self.code = code
        self.message = message or code
        self.headers = headers or {}


def error_body(platform: str, status: int, code: str, message: str) -> dict[str, Any]:
    if platform == "slack":
        return {"ok": False, "error": code}
    if platform == "confluence":
        return {"errors": [{"status": status, "code": code.upper(), "title": message,
                            "detail": None}]}
    if platform == "jira":
        return {"errorMessages": [message], "errors": {}}
    if platform == "drive":
        return {"error": {"code": status, "message": message,
                          "errors": [{"domain": "global", "reason": code, "message": message}]}}
    return {"error": code, "message": message}


async def platform_error_handler(_request: Request, exc: PlatformError) -> JSONResponse:
    return JSONResponse(error_body(exc.platform, exc.status, exc.code, exc.message),
                        status_code=exc.status, headers=exc.headers)


def world(request: Request) -> World:
    return request.app.state.world


def _token(request: Request) -> Optional[str]:
    h = request.headers.get("authorization", "")
    if h[:7].lower() == "bearer ":
        return h[7:].strip() or None
    return None


def platform_auth(platform: str) -> Callable[[Request], str]:
    """Dependency for every platform route: svc or admin token, then the rate-limit toggle."""
    def dep(request: Request) -> str:
        tok = _token(request)
        cfg = request.app.state
        if tok is None or tok not in (cfg.svc_token, cfg.admin_token):
            raise PlatformError(platform, 401, "not_authed" if tok is None else "invalid_auth",
                                "Unauthorized")
        role = "admin" if tok == cfg.admin_token else "svc"
        if cfg.world.ratelimited.get(platform):
            raise PlatformError(platform, 429, "ratelimited", "Rate limit exceeded",
                                headers={"Retry-After": "2"})
        request.state.role = role
        return role
    return dep


def require_admin_role(request: Request, platform: str) -> None:
    """Permission-check endpoints: the svc token is authenticated but not authorised (403)."""
    if getattr(request.state, "role", None) != "admin":
        raise PlatformError(platform, 403, "forbidden",
                            "Administer permission is required to check another user's permissions")


def admin_auth(request: Request) -> None:
    tok = _token(request)
    cfg = request.app.state
    if tok is None or tok not in (cfg.svc_token, cfg.admin_token):
        raise PlatformError("admin", 401, "unauthorized", "Unauthorized")
    if tok != cfg.admin_token:
        raise PlatformError("admin", 403, "forbidden", "admin token required")


# ---------------------------------------------------------------------- pagination
def encode_cursor(kind: str, offset: int) -> str:
    return base64.urlsafe_b64encode(f"{kind}:{offset}".encode()).decode().rstrip("=")


def decode_cursor(platform: str, kind: str, cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        pad = "=" * (-len(cursor) % 4)
        k, _, off = base64.urlsafe_b64decode(cursor + pad).decode().rpartition(":")
        n = int(off)
        if k != kind or n < 0:
            raise ValueError
        return n
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise PlatformError(platform, 400, "invalid_cursor", "Invalid cursor/page token")


def page_size(limit: Optional[int]) -> int:
    if limit is None or limit <= 0:
        return DEFAULT_PAGE
    return min(int(limit), MAX_PAGE)


def paginate(platform: str, kind: str, items: Sequence[Any], cursor: Optional[str],
             limit: Optional[int]) -> tuple[list[Any], Optional[str]]:
    """Slice ``items`` at the cursor; returns (page, next_cursor or None)."""
    start = decode_cursor(platform, kind, cursor)
    n = page_size(limit)
    chunk = list(items[start:start + n])
    nxt = encode_cursor(kind, start + n) if start + n < len(items) else None
    return chunk, nxt
