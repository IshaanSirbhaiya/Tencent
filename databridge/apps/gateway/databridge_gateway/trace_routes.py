"""``GET /trace`` and ``GET /trace/{request_id}`` — the audit surface.

The recorder is :mod:`trace_recorder`; this module is only its two reads, plus the one line of
policy that matters here: **there is no write route and no seed.** A trace appears because a
request ran and recorded one. A `POST /trace` — even a "for the demo" one — would make every trace
on the surface indistinguishable from one somebody typed, and the surface's entire value is that
you cannot type one.

So with no activity, `GET /trace` returns `{"traces": [], ...}`. That is the true answer.
`apps/frontend` already renders an empty list honestly.

The list is cheap (counts only). The detail carries the ordered steps, the derived view, and the
`defect_list` — the steps the request could *not* record, which is the half of the record most
worth reading.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .trace_recorder import recorder_for

log = logging.getLogger("databridge.gateway.trace")

#: Explicit audit roles (Codex f1 #1). Read from ``WALNUT_AUDIT_ROLES``, a JSON object
#: ``{"<principal_id>": ["trace_admin"]}``. Nothing grants a role implicitly: not dev mode, not the
#: generic ``staff`` identity, not a request field. Unset, malformed or unknown-role config grants
#: NOTHING (fail closed) and is logged.
#:
#: * ``trace_admin``  -- may read every trace on ``/trace``, including ownerless ones.
#:
#: There is NO activity role here (Codex f2 #11). ``/activity``'s operators are BRAIN's
#: (``agent_ownership.is_operator``: ``WALNUT_OPERATORS``, else only the dev principal) -- one
#: rule for decisions and for the feed. The ``activity_operator`` role this list used to document
#: was never consulted, so an administrator granting it got nothing; it is now outside the
#: vocabulary, and a config naming it is logged as an unknown role and grants nothing.
AUDIT_ROLES_ENV = "WALNUT_AUDIT_ROLES"
AUDIT_ROLES = ("trace_admin",)

#: What `GET /trace` returns at most in one page. A trace detail is unbounded in steps, so the
#: list is bounded in rows rather than the store being bounded harder.
MAX_LIST = 200


def _audit_role_directory() -> dict[str, frozenset[str]]:
    raw = os.environ.get(AUDIT_ROLES_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.error("%s is not valid JSON; no audit role is granted to anyone", AUDIT_ROLES_ENV)
        return {}
    if not isinstance(parsed, dict):
        log.error("%s must be a JSON object; no audit role is granted", AUDIT_ROLES_ENV)
        return {}
    out: dict[str, frozenset[str]] = {}
    for principal_id, roles in parsed.items():
        if not isinstance(principal_id, str) or not principal_id or not isinstance(roles, list):
            log.error("%s: entry %r ignored (want principal_id -> [roles])",
                      AUDIT_ROLES_ENV, principal_id)
            continue
        known = {r for r in roles if r in AUDIT_ROLES}
        unknown = [r for r in roles if r not in AUDIT_ROLES]
        if unknown:
            log.error("%s: unknown role(s) %r for %r ignored; the vocabulary is %r",
                      AUDIT_ROLES_ENV, unknown, principal_id, AUDIT_ROLES)
        out[principal_id] = frozenset(known)
    return out


def has_audit_role(principal: AuthenticatedPrincipal, role: str) -> bool:
    """Does this AUTHENTICATED principal hold `role` in ``WALNUT_AUDIT_ROLES``?

    Read per call (the file is tiny and an operator revoking a role should not need a restart).
    A role outside the closed vocabulary is a programming error, not a quiet `False`.
    """
    if role not in AUDIT_ROLES:
        raise ValueError(f"unknown audit role {role!r}; the vocabulary is {AUDIT_ROLES!r}")
    pid = getattr(principal, "principal_id", None)
    if not pid:
        return False
    return role in _audit_role_directory().get(pid, frozenset())


def _refusal(code: str, message: str, status: int = 404) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def register_trace_routes(app: FastAPI) -> None:
    """Attach the two read routes. Nothing here writes a trace.

    R-015: both are gated by `auth.require_principal`. An audit trail is exactly the kind of
    surface that must not be legible to an unauthenticated caller — it names principals, purposes,
    reason codes and pointer origins for every request this gateway has served.
    """
    install_auth_handler(app)

    @app.get("/trace")
    def list_traces(limit: int = 50,
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        # Codex f1 #1: owner-only. A trace is listed to the principal it was recorded for; an
        # explicit `trace_admin` sees all of them. An ownerless trace is admin-only.
        recorder = recorder_for(app)
        return recorder.list_for(principal.principal_id,
                                 admin=has_audit_role(principal, "trace_admin"),
                                 limit=max(1, min(int(limit), MAX_LIST)))

    @app.get("/trace/{request_id}")
    def get_trace(request_id: str,
                  principal: AuthenticatedPrincipal = Depends(require_principal)):
        recorder = recorder_for(app)
        trace = recorder.get_for(request_id, principal.principal_id,
                                 admin=has_audit_role(principal, "trace_admin"))
        if trace is None:
            # 404 means "no trace you may read", and it is deliberately not softened into an
            # empty trace. It is ALSO the answer for somebody else's trace, word for word (Codex
            # f1 #8): the response must not tell a caller whether a private trace exists. The
            # message does not echo the id, so nothing in the body varies with it.
            return _refusal(
                "UNKNOWN_TRACE",
                "no trace with that id is held for you. Traces are recorded by requests as they "
                "run and are never seeded; each is readable by the principal it was recorded for. "
                "The store holds the most recent ones and reports how many of yours it has "
                "evicted at GET /trace.")
        return trace.to_dict()
