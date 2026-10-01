"""``/mcp/*`` — the Connectors surface, driving the real MCP aggregator (T-44).

`/connectors` used to be twelve logos and a modal that resolved after 1.1 seconds. This module is
what makes it real: every route here talks to a **live MCPJungle** registry over HTTP, so connecting
a server from the UI genuinely changes what the agent can call, and disconnecting genuinely takes it
away. Nothing on the page is illustrative.

**The one decision this module exists to enforce — READ vs ACT.**
`docs/hackathon/AGENT_CAPABILITIES.md` §"Connecting MCP servers": a server is declared READ or ACT
*when it is connected*, and that declaration decides **which process fronts it**.

    READ  -> registered into the aggregator the agent queries. Tier-1: callable immediately.
    ACT   -> recorded HERE, dispatcher-only, and *never* sent to the aggregator. Tier-3: reachable
             only by the model-free dispatcher, after a named human approves a drafted action.

That split is enforced structurally, not by a label:

1. :func:`mcp_connect` has **two disjoint branches**. The ACT branch never touches
   :meth:`MCPJungleClient.register_server`; there is no code path from ``kind="act"`` to the
   aggregator's ``POST /api/v0/servers``. ``test_act_server_is_never_registered_into_aggregator``
   asserts on the recorded call log that no such call was made.
2. The ACT record lives in a **separate store** (:class:`ActRegistry`) which is not a registry the
   agent reads — the agent reads the aggregator, and the aggregator has never heard of these names.
3. :func:`mcp_invoke` — the "try it" button — refuses any tool whose server is ACT-recorded, and
   additionally refuses any tool whose bare name is on the writer process's allow-list
   (``apps/mcp_server/capabilities.py``, R-34). An act is a drafted action a human approves; it is
   never an ad-hoc button on a settings page.
4. :func:`mcp_servers` cross-checks the two stores and raises ``boundary_violation`` on any name
   that is ACT-recorded *and* present in the aggregator — so if the split is ever broken
   out-of-band (someone runs the mcpjungle CLI by hand), the UI says so instead of hiding it.

**A down registry is a refusal, never an empty list.** "nothing is connected" and "I cannot reach
the registry" are different facts, and a page that renders them identically is lying about the
second one. Every read route returns a typed ``REGISTRY_UNREACHABLE`` refusal naming the URL it
tried.

**Every outbound call is bounded.** Separate timeouts for probe / list / register / invoke — a
stopped container must surface as a refusal in seconds, not as a hung page.

Configuration is env-only, with a local default; no URL, token or host is baked into the repo.

    MCPJUNGLE_URL              default http://127.0.0.1:8080      the aggregator
    WALNUT_READER_MCP_URL      default http://127.0.0.1:8765/mcp  our read-only clinic server
    WALNUT_ACT_REGISTRY_PATH   default <repo>/.databridge/mcp_act_registry.json

Registration into ``create_app()`` is one line, left to the gateway owner (as ``evidence_routes``
was)::

    from .mcp_routes import register_mcp_routes
    register_mcp_routes(app)
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import os
import pathlib
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import httpx
from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import install_auth_handler, require_principal

log = logging.getLogger("databridge.gateway.mcp")

# --- configuration -----------------------------------------------------------------------

DEFAULT_REGISTRY_URL = "http://127.0.0.1:8080"
DEFAULT_READER_URL = "http://127.0.0.1:8765/mcp"

#: Bounded, and bounded differently per call: listing is a DB read, registering makes the
#: aggregator perform an MCP `initialize` handshake against the target, invoking runs a tool.
PROBE_TIMEOUT = 3.0
LIST_TIMEOUT = 6.0
REGISTER_TIMEOUT = 25.0
INVOKE_TIMEOUT = 30.0

#: MCPJungle namespaces a tool as ``<server>__<tool>``, so a server name containing ``__`` makes
#: that split ambiguous. The aggregator rejects it too; rejecting it before the round trip gives
#: the user the reason instead of a 400 relayed out of a container.
_SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9_-]{0,62}[a-zA-Z0-9])?$")

TOOL_SEPARATOR = "__"

KINDS = ("read", "act")
TRANSPORTS = ("streamable_http", "sse")

#: Spelled out for the UI so the consequence of the choice is stated where the choice is made,
#: not in a doc nobody opens. The frontend renders these verbatim beside the radio buttons.
KIND_CONSEQUENCE = {
    "read": ("READ tools are available to the agent immediately. The server joins the aggregator "
             "the agent queries, and its tools become Tier-1 capabilities."),
    "act": ("ACT tools can only run after a human approves a drafted action. The server is "
            "recorded for the model-free dispatcher and is never registered into the aggregator, "
            "so the agent cannot reach it at all."),
}


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def registry_url() -> str:
    return (os.environ.get("MCPJUNGLE_URL") or DEFAULT_REGISTRY_URL).rstrip("/")


def reader_url() -> str:
    return os.environ.get("WALNUT_READER_MCP_URL") or DEFAULT_READER_URL


def _repo_root() -> pathlib.Path:
    """The directory holding both ``Hack4Health/`` and ``databridge/``; cwd if not found.

    Walked from this file rather than trusting the cwd, so uvicorn and pytest agree. Deliberately
    does not import ``databridge_intake.paths`` — the gateway does not declare that package as a
    dependency, and the connectors page should not fail to import because the intake package moved.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "Hack4Health").is_dir() and (parent / "databridge").is_dir():
            return parent
    return pathlib.Path.cwd()                                       # pragma: no cover


def act_registry_path() -> pathlib.Path:
    override = os.environ.get("WALNUT_ACT_REGISTRY_PATH")
    if override:
        return pathlib.Path(override)
    # `.databridge/` is already git-ignored — local operator state, never repo content.
    return _repo_root() / ".databridge" / "mcp_act_registry.json"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# --- refusals ----------------------------------------------------------------------------


def _refusal(code: str, message: str, status: int = 422,
             nearest_alternative: Optional[str] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": nearest_alternative}})


def _unreachable(url: str, detail: str) -> JSONResponse:
    """503 — the registry could not be reached.

    Never an empty list. A page that renders "cannot reach the registry" as "nothing connected"
    tells the operator the system is fine when it is down, which is the more expensive of those two
    mistakes by a wide margin.
    """
    return _refusal(
        "REGISTRY_UNREACHABLE",
        f"The MCP aggregator at {url} did not answer ({detail}). This is NOT the same as "
        f"'no servers are connected' — nothing can be listed, connected or invoked until it is "
        f"back. Start it with: docker start mcpjungle",
        status=503,
        nearest_alternative="GET /mcp/status")


# --- the aggregator client ---------------------------------------------------------------


class RegistryUnreachable(RuntimeError):
    """The aggregator did not answer at all (connection refused, DNS failure, timeout)."""


class RegistryRejected(RuntimeError):
    """The aggregator answered, and said no."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class MCPJungleClient:
    """The MCPJungle HTTP API, as observed against a live container on 2026-08-13.

    Every endpoint and payload below was confirmed by hand before being written here — the runbook's
    lesson (`MCP_RUNBOOK.md` §5) is that a green suite proves nothing about whether the thing
    answers, so none of these paths is a guess::

        GET    /health                  -> {"status":"ok"}
        GET    /api/v0/servers          -> [{"name","transport","enabled","description","url",...}]
        GET    /api/v0/tools            -> [{"name","description","input_schema","enabled",...}]
        POST   /api/v0/servers          -> 201 {"server":{...}}   | 400/500 {"error":"..."}
        DELETE /api/v0/servers/{name}   -> 204                    | 404 {"error":"..."}
        POST   /api/v0/tools/invoke     -> 200 {"content":[...],"isError":bool}

    The invoke payload is **flat**: ``{"name": "<server>__<tool>", **arguments}``. Nesting the
    arguments under ``input`` / ``arguments`` / ``params`` sends that wrapper through as the tool's
    argument dict, and the tool then fails its own schema validation — which reads like a bug in the
    tool. It is not; it is the wrapper.
    """

    def __init__(self, base_url: Optional[str] = None, *,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self.base_url = (base_url or registry_url()).rstrip("/")
        # `transport` exists so the suite can stand a whole fake aggregator behind
        # `httpx.MockTransport` and still exercise THIS class — the paths, the payload shapes, the
        # status handling. A test that stubs the client's own methods instead would pass while the
        # request shape was wrong, which is the exact defect class §5 of MCP_RUNBOOK.md describes.
        self._transport = transport

    # -- transport ------------------------------------------------------------------------
    def _request(self, method: str, path: str, *, timeout: float,
                 json_body: Optional[dict] = None) -> httpx.Response:
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client:
                return client.request(method, f"{self.base_url}{path}", json=json_body)
        except httpx.HTTPError as exc:
            raise RegistryUnreachable(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return (response.text or "").strip()[:400] or f"HTTP {response.status_code}"
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])[:400]
        return json.dumps(body)[:400]

    def _json(self, method: str, path: str, *, timeout: float,
              json_body: Optional[dict] = None, ok: Iterable[int] = (200, 201, 204)) -> Any:
        response = self._request(method, path, timeout=timeout, json_body=json_body)
        if response.status_code not in tuple(ok):
            raise RegistryRejected(response.status_code, self._error_detail(response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:                                   # pragma: no cover — defensive
            raise RegistryRejected(response.status_code,
                                   f"registry returned non-JSON: {exc}") from exc

    # -- the API --------------------------------------------------------------------------
    def health(self) -> dict:
        body = self._json("GET", "/health", timeout=PROBE_TIMEOUT, ok=(200,))
        return body if isinstance(body, dict) else {"status": str(body)}

    def servers(self) -> list[dict]:
        body = self._json("GET", "/api/v0/servers", timeout=LIST_TIMEOUT, ok=(200,))
        return [s for s in (body or []) if isinstance(s, dict)]

    def tools(self) -> list[dict]:
        body = self._json("GET", "/api/v0/tools", timeout=LIST_TIMEOUT, ok=(200,))
        return [t for t in (body or []) if isinstance(t, dict)]

    def register_server(self, *, name: str, url: str, description: str,
                        transport: str = "streamable_http") -> dict:
        body = self._json("POST", "/api/v0/servers", timeout=REGISTER_TIMEOUT, ok=(200, 201),
                          json_body={"name": name, "url": url, "description": description,
                                     "transport": transport})
        if isinstance(body, dict) and isinstance(body.get("server"), dict):
            return body["server"]
        return body if isinstance(body, dict) else {}

    def deregister_server(self, name: str) -> None:
        self._json("DELETE", f"/api/v0/servers/{name}", timeout=REGISTER_TIMEOUT, ok=(200, 204))

    def invoke_tool(self, name: str, arguments: dict) -> dict:
        # Flat payload — see the class docstring. `name` is applied last so a caller cannot
        # retarget the invocation through a "name" key inside the argument dict.
        payload = {**{k: v for k, v in arguments.items() if k != "name"}, "name": name}
        body = self._json("POST", "/api/v0/tools/invoke", timeout=INVOKE_TIMEOUT, ok=(200,),
                          json_body=payload)
        return body if isinstance(body, dict) else {"content": [], "isError": False, "raw": body}


# --- our reader server's own health ------------------------------------------------------


def probe_reader(url: Optional[str] = None, *, timeout: float = PROBE_TIMEOUT) -> dict:
    """Is ``brain_reader_server.py`` actually up?

    A real MCP ``initialize`` over streamable HTTP, not a TCP poke — the aggregator holds a cached
    tool list in its own DB, so "four tools are listed" survives the reader being dead. The only
    honest check is to speak the protocol to it.

    ``Accept: application/json, text/event-stream`` is mandatory; without it the transport answers
    ``406 Not Acceptable`` and the failure looks like the server being broken
    (`MCP_RUNBOOK.md` §4.1).
    """
    target = url or reader_url()
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "walnut-gateway-probe", "version": "0"}}}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                target, json=request,
                headers={"Accept": "application/json, text/event-stream",
                         "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        return {"url": target, "reachable": False, "server_info": None,
                "detail": f"{type(exc).__name__}: {exc}"}
    if response.status_code >= 400:
        return {"url": target, "reachable": False, "server_info": None,
                "detail": f"HTTP {response.status_code}"}
    return {"url": target, "reachable": True,
            "server_info": _server_info(response.text), "detail": None}


def _server_info(text: str) -> Optional[dict]:
    """``result.serverInfo`` out of a JSON or SSE-framed ``initialize`` reply."""
    for line in text.splitlines():
        candidate = line[len("data:"):].strip() if line.startswith("data:") else line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        result = parsed.get("result")
        info = result.get("serverInfo") if isinstance(result, dict) else None
        if isinstance(info, dict):
            return info
    return None


# --- the ACT registry: dispatcher-only, and deliberately not the aggregator ---------------


@dataclass(frozen=True)
class ActServer:
    name: str
    url: str
    description: str
    recorded_at: str


class ActRegistry:
    """Servers declared ACT — recorded by us, for the dispatcher, and by nobody else.

    This is a **different store from the aggregator on purpose**. The agent's reachable surface is
    "whatever the aggregator lists"; if ACT servers were kept there with a flag, read-ness would be
    a column somebody has to remember to check — which is exactly the failure R-34 was. Here, an ACT
    server's URL never reaches the process the agent talks to.

    JSON-file-backed (atomic replace) so a connection survives a gateway restart, or purely
    in-memory when ``path`` is ``None`` — which is how the tests run.
    """

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self.path = path
        self._memory: dict[str, ActServer] = self._load() if path is not None else {}

    # -- persistence ----------------------------------------------------------------------
    def _load(self) -> dict[str, ActServer]:
        assert self.path is not None
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            rows = raw.get("servers", []) if isinstance(raw, dict) else []
        except (OSError, ValueError) as exc:                        # pragma: no cover — defensive
            log.warning("act registry at %s unreadable (%s); starting empty", self.path, exc)
            return {}
        out: dict[str, ActServer] = {}
        for row in rows:
            if not isinstance(row, dict) or "name" not in row or "url" not in row:
                continue                                            # pragma: no cover
            out[str(row["name"])] = ActServer(
                name=str(row["name"]), url=str(row["url"]),
                description=str(row.get("description", "")),
                recorded_at=str(row.get("recorded_at", "")))
        return out

    def _flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "servers": [asdict(s) for s in self._memory.values()]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- the surface ----------------------------------------------------------------------
    def names(self) -> frozenset[str]:
        return frozenset(self._memory)

    def list(self) -> list[ActServer]:
        return sorted(self._memory.values(), key=lambda s: s.name)

    def get(self, name: str) -> Optional[ActServer]:
        return self._memory.get(name)

    def add(self, *, name: str, url: str, description: str) -> ActServer:
        server = ActServer(name=name, url=url, description=description, recorded_at=_now())
        self._memory[name] = server
        self._flush()
        return server

    def remove(self, name: str) -> bool:
        if name not in self._memory:
            return False
        del self._memory[name]
        self._flush()
        return True


# --- shaping -----------------------------------------------------------------------------


def _split_tool_name(qualified: str) -> tuple[Optional[str], str]:
    """``"walnut_clinic_read__brain_query"`` -> ``("walnut_clinic_read", "brain_query")``."""
    if TOOL_SEPARATOR in qualified:
        server, _, tool = qualified.partition(TOOL_SEPARATOR)
        return server, tool
    return None, qualified


@functools.lru_cache(maxsize=1)
def _write_tool_names() -> frozenset[str]:
    """Tool names that live in the WRITER process (``apps/mcp_server/capabilities.py``).

    Read from the manifest rather than copied, so adding a write capability there automatically
    makes it un-invocable here. If the mcp_server package is not importable (the gateway does not
    declare it as a dependency; ``serve.py`` puts it on the path) the check simply does not fire —
    it is a second belt over the process split, never the thing holding the trousers up.
    """
    try:
        from capabilities import WRITE_TOOL_NAMES            # type: ignore[import-not-found]
    except Exception:                                        # pragma: no cover — path-dependent
        return frozenset()
    return frozenset(WRITE_TOOL_NAMES)


def _tool_view(tool: dict, *, kind: str) -> dict:
    name = str(tool.get("name", ""))
    server, bare = _split_tool_name(name)
    enabled = bool(tool.get("enabled", True))
    return {
        "name": name,
        "server": server,
        "tool": bare,
        "description": tool.get("description") or "",
        "input_schema": tool.get("input_schema") or {},
        "enabled": enabled,
        "kind": kind,
        # What the UI's "try it" button keys off. An ACT tool is never invocable from a button;
        # neither is a write tool that somehow surfaced on the read endpoint.
        "invocable": kind == "read" and enabled and bare not in _write_tool_names(),
    }


def _tools_by_server(tools: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for tool in tools:
        server, _ = _split_tool_name(str(tool.get("name", "")))
        grouped.setdefault(server or "", []).append(_tool_view(tool, kind="read"))
    return grouped


def _read_server_view(server: dict, tools: list[dict]) -> dict:
    return {
        "name": str(server.get("name", "")),
        "kind": "read",
        "url": server.get("url") or "",
        "description": server.get("description") or "",
        "transport": server.get("transport") or "",
        "enabled": bool(server.get("enabled", True)),
        # The whole point of the split, stated per row rather than left to the reader to infer.
        "agent_reachable": True,
        "in_aggregator": True,
        "tools_known": True,
        "tools": tools,
        "recorded_at": None,
        "boundary_violation": False,
    }


def _act_server_view(server: ActServer, *, in_aggregator: bool) -> dict:
    return {
        "name": server.name,
        "kind": "act",
        "url": server.url,
        "description": server.description,
        "transport": "streamable_http",
        "enabled": True,
        "agent_reachable": False,
        "in_aggregator": in_aggregator,
        # We never connect to an ACT server from here — listing its tools would mean opening a
        # session with an act-capable endpoint from the process that also holds the agent.
        "tools_known": False,
        "tools": [],
        "recorded_at": server.recorded_at,
        # True only if the aggregator has somehow been pointed at an ACT server out-of-band. The
        # UI shows it loudly rather than letting it pass as a normal row.
        "boundary_violation": in_aggregator,
    }


def _rejected(base_url: str, exc: RegistryRejected, what: str) -> JSONResponse:
    return _refusal("REGISTRY_REJECTED",
                    f"The aggregator at {base_url} refused {what} (HTTP {exc.status}): "
                    f"{exc.detail}", status=502)


# --- routes ------------------------------------------------------------------------------


def register_mcp_routes(app: FastAPI, *, client: Optional[MCPJungleClient] = None,
                        act_registry: Optional[ActRegistry] = None,
                        reader_probe: Optional[Callable[[], dict]] = None) -> FastAPI:
    """Mount ``/mcp/*``. Every collaborator is injectable so the suite needs no Docker."""
    install_auth_handler(app)
    app.state.mcp_client = client or MCPJungleClient()
    app.state.mcp_act_registry = (
        act_registry if act_registry is not None else ActRegistry(act_registry_path()))
    app.state.mcp_reader_probe = reader_probe or probe_reader

    def _client(request: Request) -> MCPJungleClient:
        return request.app.state.mcp_client

    def _acts(request: Request) -> ActRegistry:
        return request.app.state.mcp_act_registry

    # -- status ---------------------------------------------------------------------------
    @app.get("/mcp/status", dependencies=_AUTH)
    def mcp_status(request: Request) -> Any:
        """What is actually up. Answers 200 even when everything is down — that IS the answer."""
        api = _client(request)
        try:
            health = api.health()
            registry = {"url": api.base_url, "reachable": True,
                        "status": health.get("status"), "detail": None}
        except RegistryUnreachable as exc:
            registry = {"url": api.base_url, "reachable": False, "status": None,
                        "detail": str(exc)}
        except RegistryRejected as exc:
            registry = {"url": api.base_url, "reachable": False, "status": None,
                        "detail": f"HTTP {exc.status}: {exc.detail}"}
        return {"registry": registry, "reader": request.app.state.mcp_reader_probe(),
                "act_recorded": len(_acts(request).names()), "checked_at": _now()}

    # -- servers --------------------------------------------------------------------------
    @app.get("/mcp/servers", dependencies=_AUTH)
    def mcp_servers(request: Request) -> Any:
        api, acts = _client(request), _acts(request)
        try:
            registered = api.servers()
            tools = api.tools()
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _rejected(api.base_url, exc, "the listing request")

        grouped = _tools_by_server(tools)
        aggregator_names = {str(s.get("name", "")) for s in registered}
        views = [_read_server_view(s, grouped.get(str(s.get("name", "")), [])) for s in registered]
        views += [_act_server_view(a, in_aggregator=a.name in aggregator_names)
                  for a in acts.list()]
        violations = [v["name"] for v in views if v["boundary_violation"]]
        if violations:
            log.error("capability boundary violated: ACT servers present in the agent's "
                      "aggregator: %s", violations)
        return {
            "registry": {"url": api.base_url, "reachable": True},
            "servers": sorted(views, key=lambda v: (v["kind"], v["name"])),
            "counts": {"read": len(registered), "act": len(acts.names()), "tools": len(tools)},
            "boundary_violations": violations,
            "kind_consequence": KIND_CONSEQUENCE,
            "checked_at": _now(),
        }

    @app.get("/mcp/tools", dependencies=_AUTH)
    def mcp_tools(request: Request) -> Any:
        api = _client(request)
        try:
            tools = api.tools()
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _rejected(api.base_url, exc, "the tool listing")
        views = [_tool_view(t, kind="read") for t in tools]
        return {"registry": {"url": api.base_url, "reachable": True},
                "tools": sorted(views, key=lambda t: t["name"]),
                "note": ("Only READ tools appear here. ACT servers are never registered into this "
                         "aggregator, so the agent's whole callable surface is this list."),
                "checked_at": _now()}

    @app.post("/mcp/servers", dependencies=_AUTH, status_code=201)
    def mcp_connect(request: Request, body: dict = Body(...)) -> Any:
        """Connect a server. ``kind`` decides which process fronts it — read the two branches."""
        api, acts = _client(request), _acts(request)

        kind = str(body.get("kind", "")).strip().lower()
        if kind not in KINDS:
            return _refusal(
                "INVALID_KIND",
                "kind must be 'read' or 'act', and it is not inferred from the URL or the tool "
                "names. READ joins the aggregator the agent queries; ACT is recorded for the "
                "model-free dispatcher and is never registered into the aggregator. A server "
                "cannot be both.")

        name = str(body.get("name", "")).strip()
        if TOOL_SEPARATOR in name or not _SERVER_NAME_RE.match(name):
            return _refusal(
                "INVALID_SERVER_NAME",
                f"'{name}' is not a usable server name. Use letters, digits, '-' and single '_' "
                f"(1-64 chars, no leading or trailing separator, and no '__' — the aggregator "
                f"namespaces tools as <server>__<tool>, so '__' inside a name makes that split "
                f"ambiguous).")

        url = str(body.get("url", "")).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return _refusal("INVALID_SERVER_URL",
                            f"'{url}' is not an http(s) URL with a host.")

        transport = str(body.get("transport") or "streamable_http").strip().lower()
        if transport not in TRANSPORTS:
            return _refusal("INVALID_TRANSPORT",
                            f"transport must be one of {list(TRANSPORTS)}.")
        description = str(body.get("description", "")).strip()

        # The aggregator must be reachable even to record an ACT server: the boundary claim is
        # "this name is NOT in the agent's registry", and that cannot be asserted while the
        # registry is unreadable. Refusing here is what keeps the claim honest.
        try:
            existing = {str(s.get("name", "")) for s in api.servers()}
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _rejected(api.base_url, exc, "the listing request")

        if name in existing or acts.get(name) is not None:
            return _refusal("DUPLICATE_SERVER",
                            f"A server named '{name}' is already connected. Disconnect it first "
                            f"— a name cannot change sides of the READ/ACT boundary in place.",
                            status=409)

        # ---- branch 1: ACT — recorded here, and nowhere the agent can see -----------------
        if kind == "act":
            recorded = acts.add(name=name, url=url, description=description)
            log.info("ACT server recorded (dispatcher-only, NOT registered into the aggregator): "
                     "%s -> %s", name, url)
            return {"server": _act_server_view(recorded, in_aggregator=False),
                    "kind": "act",
                    "registered_into_aggregator": False,
                    "consequence": KIND_CONSEQUENCE["act"]}

        # ---- branch 2: READ — into the aggregator the agent queries -----------------------
        try:
            server = api.register_server(name=name, url=url, description=description,
                                         transport=transport)
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _refusal(
                "REGISTRY_REJECTED",
                f"The aggregator refused to register '{name}' at {url} (HTTP {exc.status}): "
                f"{exc.detail}", status=502)

        try:
            tools = _tools_by_server(api.tools()).get(name, [])
        except (RegistryUnreachable, RegistryRejected):             # pragma: no cover — defensive
            tools = []
        log.info("READ server registered into the aggregator: %s -> %s (%d tools)",
                 name, url, len(tools))
        return {"server": _read_server_view({**server, "name": name}, tools),
                "kind": "read",
                "registered_into_aggregator": True,
                "consequence": KIND_CONSEQUENCE["read"]}

    @app.delete("/mcp/servers/{name}", dependencies=_AUTH)
    def mcp_disconnect(request: Request, name: str) -> Any:
        api, acts = _client(request), _acts(request)
        if acts.get(name) is not None:
            acts.remove(name)
            log.info("ACT server record removed: %s", name)
            return {"disconnected": name, "kind": "act", "was_in_aggregator": False}
        try:
            existing = {str(s.get("name", "")) for s in api.servers()}
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _rejected(api.base_url, exc, "the listing request")
        if name not in existing:
            return _refusal("UNKNOWN_SERVER", f"No server named '{name}' is connected.",
                            status=404)
        try:
            api.deregister_server(name)
        except RegistryUnreachable as exc:                          # pragma: no cover — raced
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _refusal("REGISTRY_REJECTED",
                            f"The aggregator refused to deregister '{name}' "
                            f"(HTTP {exc.status}): {exc.detail}", status=502)
        log.info("READ server deregistered from the aggregator: %s", name)
        return {"disconnected": name, "kind": "read", "was_in_aggregator": True}

    # -- invoke ---------------------------------------------------------------------------
    @app.post("/mcp/tools/{name}/invoke", dependencies=_AUTH)
    def mcp_invoke(request: Request, name: str, body: dict = Body(default={})) -> Any:
        """Run a READ tool, because a human clicked "try it".

        Three refusals stand before the call, and the first two run **before** the aggregator is
        contacted at all: an ACT server's tool, a writer-process tool, and a tool the aggregator
        does not list. An act does not happen because somebody pressed a button on a settings page
        — it happens because a draft was approved and a model-free dispatcher executed it (Tier 3).
        """
        api, acts = _client(request), _acts(request)
        server, bare = _split_tool_name(name)

        if server and acts.get(server) is not None:
            return _refusal(
                "ACT_TOOL_NOT_INVOCABLE",
                f"'{name}' belongs to '{server}', which is connected as an ACT server. ACT tools "
                f"are never invoked from this surface: they run only through the approval path — "
                f"the agent drafts, a named human approves, and the model-free dispatcher "
                f"executes. There is no ad-hoc button for an act.",
                status=403,
                nearest_alternative="draft the action on /intelligence and approve it there")

        if bare in _write_tool_names():
            return _refusal(
                "WRITE_TOOL_NOT_INVOCABLE",
                f"'{bare}' is a tool of the writer process "
                f"(apps/mcp_server/capabilities.py). It is not part of the agent-reachable read "
                f"surface and is not invocable here (R-34).", status=403)

        arguments = {k: v for k, v in (body or {}).items() if k != "name"}
        try:
            known = {str(t.get("name", "")) for t in api.tools()}
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _rejected(api.base_url, exc, "the tool listing")
        if name not in known:
            return _refusal(
                "UNKNOWN_TOOL",
                f"The aggregator does not expose a tool named '{name}'. Its whole surface is "
                f"{sorted(known)}.", status=404)

        started = time.perf_counter()
        try:
            result = api.invoke_tool(name, arguments)
        except RegistryUnreachable as exc:
            return _unreachable(api.base_url, str(exc))
        except RegistryRejected as exc:
            return _refusal("TOOL_INVOCATION_FAILED",
                            f"The aggregator returned HTTP {exc.status} for '{name}': "
                            f"{exc.detail}", status=502)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

        raw_content = result.get("content")
        content = [c for c in raw_content if isinstance(c, dict)] if isinstance(raw_content, list) \
            else []
        return {
            "tool": name,
            "server": server,
            "arguments": arguments,
            # The aggregator's own verdict, relayed rather than reinterpreted: a tool that failed
            # its schema check answers HTTP 200 with isError=true, and flattening that into a
            # success would be the page lying about the call it just made.
            "is_error": bool(result.get("isError", False)),
            "content": content,
            "text": "\n".join(str(c.get("text", "")) for c in content),
            "raw": result,
            "duration_ms": elapsed_ms,
            "invoked_at": _now(),
        }

    return app
