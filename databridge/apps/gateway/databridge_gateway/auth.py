"""Gateway authentication — the one choke point every disclosing route goes through.

**The finding this closes.** Codex batch 3 found the same root cause four independent ways:
``POST /orchestrate/ask`` (and every route beside it) read a principal identity straight off the
HTTP request — a body field (``on_behalf_of``), or nothing at all — and handed it to
``PolicyEngine`` / the graph store / the ledger as fact. A caller who typed
``{"on_behalf_of": "fcc-3"}`` received compliance-officer disclosure. Nothing authenticated the
identity the request claimed.

THREAT MODEL
============
**Defends against:** an HTTP caller who has no credential, or who presents a credential this
deployment did not issue, reaching a route that discloses ledger rows, graph nodes/edges,
pointers, audit traces, live adapter reads, or agent answers — including a caller who supplies
``on_behalf_of`` (or any other request field) hoping it will be trusted as an identity assertion.
After this module, no route accepts an identity from the request body; the identity comes from
:func:`require_principal`, which is derived from a credential the caller must present and this
deployment must have issued.

**Does NOT defend against:** a leaked or guessed bearer token (this is a long-lived shared
secret, not short-lived or MFA-backed); a caller on the same host reading the token out of the
environment or process memory; TLS stripping or any other network-layer attack — this gateway
does not terminate TLS itself; a legitimate token-holder acting outside their normal pattern —
there is no per-request authorization narrower than "this token names this principal";
:class:`databridge_adapter.PolicyEngine` still does the field-level admission decision, unchanged,
once a principal is established. This module answers exactly one question — *who, if anyone, is
making this HTTP request?* — fail-closed, and nothing more.

MECHANISM
=========
A shared-secret bearer token (``Authorization: Bearer <token>``) checked against a directory
loaded once at process start from the ``WALNUT_API_TOKENS`` environment variable: a flat JSON
object ``{"<token>": "<principal_id>"}``. ``<principal_id>`` is a name one of this deployment's
own directories already knows (``orchestrator_fleet.PRINCIPALS``, ``verify_fleet``'s directory, or
the generic ``"staff"`` identity used by pages that carry no adapter-level grant at all). A
missing header, a malformed header, or a token absent from the directory is refused with 401 —
there is no default principal and no silent fallback to one.

This is the "self-hosted deployment" option the fix brief calls acceptable: a real,
non-spoofable mapping from a credential the caller must present to an identity the operator
issued it to. It is not single sign-on, it is not short-lived, and it is not a substitute for
TLS — an operator who needs those puts a real auth proxy in front (see PROXY MODE) and this
module still requires the proxy to say so explicitly before it will trust a header.

PROXY MODE
==========
If the intended production deployment instead terminates authentication in a reverse proxy
(oauth2-proxy, an Azure AD App Proxy, …) and forwards a trusted identity header, that is a real
mechanism too — but only if the gateway is explicitly told a proxy is doing that job, so a
gateway exposed directly to the internet by accident cannot be tricked into trusting a
caller-supplied header of the same name. Set ``WALNUT_AUTH_MODE=proxy`` and
``WALNUT_PROXY_IDENTITY_HEADER=<header-name>`` (e.g. ``X-Auth-Request-User``) together; the header
value is looked up in the SAME ``WALNUT_API_TOKENS``-shaped directory (env
``WALNUT_PROXY_IDENTITY_MAP``, ``{"<header-value>": "<principal_id>"}``) rather than trusted
verbatim, so a proxy that forwards an unrecognised identity still refuses closed. Neither env var
is set anywhere in this repo — there is no proxy in front of the local demo — which is the point:
without both of them, a header of that name is never trusted, however it is spelled.

FAIL CLOSED
===========
With no ``WALNUT_API_TOKENS``, no ``WALNUT_AUTH_MODE=proxy`` pair, and dev mode off, every
disclosing route refuses with 503 ``AUTH_NOT_CONFIGURED`` — this gateway will not silently run
open because nobody set an env var. :func:`require_principal` is the single dependency every
disclosing route in this package takes; nothing calls ``PolicyEngine``, ``EvidenceFleet``,
``OrchestratorFleet``, or reads the graph store on a gated route without going through it first.

DEV MODE
========
``WALNUT_DEV_AUTH=1`` (default unset — nothing sets it except the local demo launcher) adds
exactly one additional token, the fixed string ``"dev-local-only"``, mapped to the orchestrator
fleet's default demo persona (``analyst-7``) for adapter-scoped routes and usable as the generic
``"staff"`` identity everywhere else (`DEV_PRINCIPAL_ID` covers both — see
:func:`orchestrator_fleet.PRINCIPALS` and the graph/trace/intelligence routes, none of which need
anything more specific than "a caller who holds *a* credential"). It is logged loudly — once at
import, and it names itself in the 401 body it prevents — precisely so a demo running open cannot
be mistaken for a demo running secured. It is never read from a request; a caller cannot turn it
on by asking for it.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("databridge.gateway.auth")

#: The one token dev mode adds. Fixed and public (it is printed in this file and in the boot log)
#: — its safety comes entirely from `WALNUT_DEV_AUTH` defaulting to off, never from the string
#: being hard to guess.
DEV_TOKEN = "dev-local-only"
#: The identity the dev token resolves to. `"analyst-7"` is written out as a literal — matching
#: `orchestrator_fleet.DEFAULT_PRINCIPAL` — rather than imported, so this module (used by every
#: route module, including ones `orchestrator_fleet` does not otherwise touch) never imports a
#: route module and risks a cycle. `test_orchestrator_routes.py` pins that the two stay equal.
DEV_PRINCIPAL_ID = "analyst-7"
#: The generic identity for pages that carry no adapter-level `Principal` at all — `/graph`,
#: `/trace`, `/evidence/trace/{id}`, `/intelligence/*`. These routes only need to know "this caller
#: is authenticated"; they never call `orchestrator_fleet.principal_for` / `verify_fleet`'s
#: directory, so `"staff"` need not be a member of either.
STAFF_PRINCIPAL_ID = "staff"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Who the HTTP layer decided is making this request.

    Carries no adapter grant itself — it is a NAME. `orchestrator_fleet.principal_for` and
    `verify_fleet`'s own directory are what turn a name into a `databridge_adapter.Principal` with
    a purpose and a grant_id; this type exists one layer below that, so a route that needs no
    adapter grant (graph, trace, intelligence) can depend on authentication without depending on
    the finance estate's directory too.
    """

    principal_id: str
    dev_mode: bool = False

    # FIN-16 (finance dry run): the approval gates (`mcp_ingest.require_authenticated_approver`,
    # `adapter_agents.principal_of`) record ``dev_only`` and ``authenticated_by`` by reading these
    # two attribute names off the principal. This type only carried ``dev_mode``, so an approval
    # made with the public dev token was logged as ``dev_only: false, authenticated_by: "gateway"``
    # — indistinguishable from a real signed-in person. Both are DERIVED from ``dev_mode`` (which
    # only `_AuthState.resolve` sets, and only for the dev token), never stored separately, so the
    # two cannot disagree.
    @property
    def dev_only(self) -> bool:
        return bool(self.dev_mode)

    @property
    def authenticated_by(self) -> str:
        return DEV_MECHANISM if self.dev_mode else GATEWAY_MECHANISM

    def describe(self) -> dict:
        """What the page may show about who is signed in: the name and how it was established."""
        return {"principal_id": self.principal_id, "dev_only": self.dev_only,
                "authenticated_by": self.authenticated_by}


#: How a principal was established, as recorded on every approval it makes.
GATEWAY_MECHANISM = "gateway"
DEV_MECHANISM = "gateway:dev-token"


def _load_token_directory() -> dict[str, str]:
    raw = os.environ.get("WALNUT_API_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.error(
            "WALNUT_API_TOKENS is not valid JSON; ignoring it entirely. A config that failed to "
            "parse must not silently become 'nobody is checked' — every disclosing route will "
            "refuse until this is fixed.")
        return {}
    if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        log.error(
            "WALNUT_API_TOKENS must be a flat JSON object of {token: principal_id} strings; "
            "ignoring it entirely.")
        return {}
    return dict(parsed)


def _load_proxy_directory() -> dict[str, str]:
    raw = os.environ.get("WALNUT_PROXY_IDENTITY_MAP", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.error("WALNUT_PROXY_IDENTITY_MAP is not valid JSON; ignoring it entirely.")
        return {}
    if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        log.error("WALNUT_PROXY_IDENTITY_MAP must be {header_value: principal_id}; ignoring it.")
        return {}
    return dict(parsed)


def _dev_mode_enabled() -> bool:
    return os.environ.get("WALNUT_DEV_AUTH", "0").strip().lower() in ("1", "true", "yes", "on")


def _proxy_mode_enabled() -> tuple[bool, Optional[str]]:
    """`(enabled, header_name)`. Both `WALNUT_AUTH_MODE=proxy` AND a header name must be present —
    naming a header alone must never be enough to start trusting it (see PROXY MODE above)."""
    mode = os.environ.get("WALNUT_AUTH_MODE", "").strip().lower()
    header = os.environ.get("WALNUT_PROXY_IDENTITY_HEADER", "").strip()
    if mode == "proxy" and header:
        return True, header
    return False, None


class _AuthState:
    """Read once per process. A module-level singleton rather than `app.state`, because the
    environment it reads is a process property — the same reasoning `llm.detect_llm()` uses."""

    def __init__(self) -> None:
        self.tokens = _load_token_directory()
        self.dev_mode = _dev_mode_enabled()
        self.proxy_mode, self.proxy_header = _proxy_mode_enabled()
        self.proxy_directory = _load_proxy_directory() if self.proxy_mode else {}

        if self.dev_mode:
            self.tokens = {**self.tokens, DEV_TOKEN: DEV_PRINCIPAL_ID}
            log.warning(
                "WALNUT_DEV_AUTH=1: this gateway accepts the fixed dev token %r, mapped to "
                "principal %r. This is a LOCAL-DEMO-ONLY setting. It must never be set in any "
                "deployment reachable by anyone but the person running the demo on their own "
                "machine — start-databridge.ps1 sets it explicitly for exactly that case, and "
                "nothing else in this repo does.", DEV_TOKEN, DEV_PRINCIPAL_ID)

        if self.proxy_mode:
            log.warning(
                "WALNUT_AUTH_MODE=proxy: this gateway trusts the %r header as an identity IF AND "
                "ONLY IF the value it carries is a key in WALNUT_PROXY_IDENTITY_MAP. Set this only "
                "when a real auth proxy sits in front of this process and strips/overwrites any "
                "caller-supplied copy of that header before it reaches here — this module has no "
                "way to verify that from inside the process.", self.proxy_header)

        if not self.tokens and not self.proxy_mode:
            log.warning(
                "no WALNUT_API_TOKENS and no WALNUT_AUTH_MODE=proxy configured: every disclosing "
                "route on this gateway will refuse with 503 AUTH_NOT_CONFIGURED until one of them "
                "is set. This is fail-closed by design — see databridge_gateway.auth for the "
                "mechanism and its threat model.")

    def resolve(self, *, bearer_token: Optional[str],
                proxy_identity: Optional[str]) -> Optional[AuthenticatedPrincipal]:
        if self.proxy_mode and proxy_identity:
            principal_id = self.proxy_directory.get(proxy_identity)
            if principal_id is not None:
                return AuthenticatedPrincipal(principal_id=principal_id)
            # An identity the proxy forwarded but this deployment's map does not recognise falls
            # through to the token check below rather than refusing immediately — a caller might
            # legitimately hold a bearer token even when a proxy is also in front.
        if not bearer_token:
            return None
        principal_id = self.tokens.get(bearer_token)
        if principal_id is None:
            return None
        return AuthenticatedPrincipal(
            principal_id=principal_id, dev_mode=self.dev_mode and bearer_token == DEV_TOKEN)


_STATE: Optional[_AuthState] = None


def _state() -> _AuthState:
    global _STATE
    if _STATE is None:
        _STATE = _AuthState()
    return _STATE


def log_auth_state_at_boot() -> None:
    """Force the process-lifetime auth state to build (and log) NOW rather than on first request.

    Called once from `main.create_app()`. Without this, the loud warnings in `_AuthState.__init__`
    (dev mode on, nothing configured at all, …) would only fire the moment the first request hit a
    gated route — which could be minutes after the operator has stopped watching the boot log, and
    is precisely the wrong moment for "this gateway is running open" to go unnoticed.
    """
    _state()


def reset_auth_state_for_tests() -> None:
    """Test-only: forces the next call to `_state()` to re-read the environment.

    Production code never calls this — the process-lifetime cache is intentional there, the same
    way `llm.detect_llm()`'s callers rely on the environment being read once. Tests call it after
    `monkeypatch.setenv`/`delenv` so a changed `WALNUT_API_TOKENS` actually takes effect.
    """
    global _STATE
    _STATE = None


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class AuthRequired(HTTPException):
    """The one shape every auth refusal takes — same envelope as every other refusal in this
    gateway (`{"refusal": {"code", "message", "nearest_alternative"}}`), so a client does not need
    a second refusal parser for the 401/503 case."""

    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        super().__init__(status_code=status_code,
                          detail={"refusal": {"code": code, "message": message,
                                              "nearest_alternative": None}})


def require_principal(
        request: Request,
        authorization: Optional[str] = Header(default=None),
) -> AuthenticatedPrincipal:
    """The one dependency every disclosing route takes.

    Raises 401 (no/unknown credential) or 503 (nothing configured to check one against); never
    returns a default principal. Attaches the resolved principal to `request.state.principal` too,
    so a route that wants it without re-declaring the dependency's return value (e.g. an inner
    helper called from more than one route) can read `request.state.principal`.
    """
    state = _state()
    if not state.tokens and not state.proxy_mode:
        raise AuthRequired(
            "AUTH_NOT_CONFIGURED",
            "this gateway has no principal directory configured (WALNUT_API_TOKENS) and no auth "
            "proxy declared (WALNUT_AUTH_MODE=proxy), so it refuses to disclose anything rather "
            "than run open. See databridge_gateway.auth for how to configure it.",
            status_code=503)

    token = _extract_bearer(authorization)
    proxy_identity = request.headers.get(state.proxy_header) if state.proxy_header else None
    principal = state.resolve(bearer_token=token, proxy_identity=proxy_identity)
    if principal is None:
        raise AuthRequired(
            "UNAUTHENTICATED",
            "this route discloses data and requires 'Authorization: Bearer <token>' naming a "
            "principal this deployment's directory knows. No credential, or one not in the "
            "directory, is refused — there is no default identity.")
    request.state.principal = principal
    return principal


async def _auth_exception_handler(_request: Request, exc: AuthRequired) -> JSONResponse:
    """FastAPI's default `HTTPException` handler wraps `detail` as `{"detail": ...}`, which would
    put this module's refusal one key deeper than every OTHER refusal in this gateway
    (`{"refusal": {...}}` at the top level, from every route module's own `_refusal` helper). This
    keeps `AuthRequired` on the same shape, so a client needs exactly one refusal parser."""
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


def install_auth_handler(app: FastAPI) -> None:
    """Register the shape-preserving handler above. Called once per app from every
    `register_*_routes` function that gates a route with `require_principal` — safe to call more
    than once on the same `app` (FastAPI simply keeps the last registration for the same
    exception type), which is what happens when `main.create_app()` composes all of them."""
    app.add_exception_handler(AuthRequired, _auth_exception_handler)


# ---------------------------------------------------------------------------------------------
# Who am I — the page's sign-in chip (FIN-16)
# ---------------------------------------------------------------------------------------------
def register_auth_routes(app: FastAPI) -> None:
    """``GET /auth/whoami`` — the principal THIS request authenticated as, and how.

    It exists so a page can say plainly when it is acting under the public dev token: an approval
    made from such a page is recorded ``dev_only: true`` and the page should not look like a real
    sign-in. Gated like every other route (no credential → 401), it names only the caller itself.
    Idempotent: composing several route modules that each call it registers it once."""
    install_auth_handler(app)
    if getattr(app.state, "_auth_routes_registered", False):
        return
    app.state._auth_routes_registered = True

    @app.get("/auth/whoami")
    def auth_whoami(principal: AuthenticatedPrincipal = Depends(require_principal)) -> dict:
        return principal.describe()


# ---------------------------------------------------------------------------------------------
# Allowed browser origins (FIN-19)
# ---------------------------------------------------------------------------------------------
#: The origins a browser may call this gateway from when nothing is configured: the one local
#: frontend (D-FE-6). A second deployment (another port, another host) names its own.
DEFAULT_CORS_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
CORS_ORIGINS_ENV = "WALNUT_CORS_ORIGINS"


def cors_allowed_origins(raw: Optional[str] = None) -> list[str]:
    """The CORS allow-list, from ``WALNUT_CORS_ORIGINS`` (comma- or whitespace-separated), else
    `DEFAULT_CORS_ORIGINS`.

    Fails closed on anything that is not an exact ``scheme://host[:port]`` origin: a wildcard
    (``*``), a path, a query or a non-http(s) scheme is DROPPED and logged — never widened to
    "every origin". If nothing valid is left the default is used, so a typo cannot open the
    gateway to every site, nor silently close it to the one frontend that was working."""
    from urllib.parse import urlsplit

    text = os.environ.get(CORS_ORIGINS_ENV, "") if raw is None else raw
    out: list[str] = []
    for token in text.replace(",", " ").split():
        origin = token.strip().rstrip("/")
        try:
            parts = urlsplit(origin)
            port_ok = parts.port is None or 0 < parts.port < 65536
        except ValueError:
            parts, port_ok = None, False
        if (parts is None or not port_ok or parts.scheme not in ("http", "https")
                or not parts.hostname or "*" in origin or parts.path or parts.query
                or parts.fragment or parts.username or parts.password):
            log.error("%s: %r is not an exact http(s) origin; ignored (a wildcard or a path is "
                      "never accepted)", CORS_ORIGINS_ENV, token)
            continue
        if origin not in out:
            out.append(origin)
    return out or list(DEFAULT_CORS_ORIGINS)
