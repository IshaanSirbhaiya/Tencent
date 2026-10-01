"""MCPJungle registry management — many MCP servers behind one HTTP gateway.

MCPJungle (`ghcr.io/mcpjungle/mcpjungle:latest-stdio`, default `http://127.0.0.1:8080`, override
`MCPJUNGLE_URL`) holds a registry of MCP servers and proxies their tools AND resources. Every
endpoint below was exercised against the live container on 2026-10-01 before it was written here:

    GET    /health                     -> {"status": "ok"}
    GET    /api/v0/servers             -> [{name, transport, enabled, description, url,
                                            command, args, env, session_mode}]
    POST   /api/v0/servers             -> 201 {"server": {...}}   (stdio: command/args/env;
                                                                   streamable_http: url)
    DELETE /api/v0/servers/{name}      -> 204
    GET    /api/v0/tools               -> [{name: "<server>__<tool>", description, input_schema,
                                            enabled, annotations}]
    POST   /api/v0/tools/invoke        -> {content, isError?, structuredContent?}   FLAT body:
                                          {"name": "<server>__<tool>", **arguments}
    GET    /api/v0/resources           -> [{uri: "mcpj://res/<server>/<b64url(original uri)>",
                                            name: "<server>__<name>", mime_type, enabled, ...}]
    POST   /api/v0/resources/read      -> {"contents": [{mimeType, text | blob}]}   body {uri}

Names are the tool/resource namespace, so a server name may not contain `__`. Registering a stdio
server makes MCPJungle spawn it once to read its tool list — a cold `npx -y` can take 20 s+, so
registration has a long timeout.

**Collisions.** A name already registered with the SAME launch config is idempotent (`exists`).
With a different config the default is to refuse (`JungleConflict`); `on_conflict="replace"`
deregisters and re-registers, `on_conflict="rename"` picks `<name>_2`, `<name>_3`… and reports the
name it used (the connection must then carry that `jungle_server`).

**Secrets (FXI3, Codex f1 #7/#12).** MCPJungle STORES every registration body in its own database,
so a registration body never carries a resolved credential. A `${VAR}` reference whose variable
is named like a credential (`*_PASSWORD`, `*_TOKEN`, `*_KEY`...) is sent as the bare reference
`${VAR}` (its `:-default` dropped) and an `env_from` entry as `${HOST_VAR}`: references only.
Non-credential references (paths, ports, the python to launch) are expanded. A literal credential,
a bearer token (`bearer_token_from`, an `Authorization` header) or any other HTTP header is
REFUSED with a plain reason: the aggregator cannot hold them without persisting them - connect
such a server `via: direct`. Error bodies from the aggregator are redacted before they are
quoted. The register result lists the credential references MCPJungle must resolve itself
(`secret_references`).
"""
from __future__ import annotations

import base64
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Literal, Optional, Union

import yaml

from . import paths

SEPARATOR = "__"
DEFAULT_URL = "http://127.0.0.1:8080"
REGISTER_TIMEOUT = 180.0
#: MCPJungle's default `stateless` mode starts the server afresh FOR EVERY CALL (measured
#: 2026-10-01, server-everything `echo`: 1.4-2.8 s per call stateless vs 7-14 ms stateful after a
#: 1 s first call). Ingestion makes hundreds of calls, so registrations default to `stateful`.
DEFAULT_SESSION_MODE = "stateful"
CALL_TIMEOUT = 120.0
LIST_TIMEOUT = 30.0
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
_RES_PREFIX = "mcpj://res/"
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")

OnConflict = Literal["error", "replace", "rename", "skip"]


class JungleError(RuntimeError):
    """MCPJungle is unreachable or refused a request. The message is plain words."""


class JungleConflict(JungleError):
    """A server with this name is registered with a different launch configuration."""


def jungle_url() -> str:
    return (os.environ.get("MCPJUNGLE_URL") or DEFAULT_URL).rstrip("/")


def check_name(name: str) -> str:
    if SEPARATOR in name or not _NAME_RE.match(name or ""):
        raise JungleError(f"MCPJungle server name {name!r} must match {_NAME_RE.pattern} and may "
                          f"not contain {SEPARATOR!r} (the tool namespace separator)")
    return name


# ================================================================================================
# the HTTP API — one persistent keep-alive client per base URL (the warm path for every call)
# ================================================================================================
class JungleAPI:
    """Thin, typed access to the aggregator. `transport` is an `httpx` transport (tests stand a
    stateful fake aggregator behind `httpx.MockTransport` and still exercise these paths)."""

    def __init__(self, base_url: Optional[str] = None, *, transport=None) -> None:
        import httpx

        self.base_url = (base_url or jungle_url()).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, transport=transport,
                                  timeout=httpx.Timeout(CALL_TIMEOUT, connect=5.0))

    # -- transport --------------------------------------------------------------------------
    def _req(self, method: str, path: str, *, body: Optional[dict] = None,
             timeout: Optional[float] = None, ok: Iterable[int] = (200, 201, 204)) -> Any:
        import httpx

        kwargs: dict = {"json": body} if body is not None else {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise JungleError(f"MCPJungle at {self.base_url} is not reachable "
                              f"({type(exc).__name__}: {exc})") from exc
        if r.status_code not in tuple(ok):
            detail = r.text.strip()[:400]
            try:
                j = r.json()
                if isinstance(j, dict) and j.get("error"):
                    detail = str(j["error"])[:400]
            except ValueError:
                pass
            from .secret_scan import redact

            raise JungleError(f"MCPJungle refused {method} {path}: HTTP {r.status_code} "
                              f"{redact(detail)}")
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError as exc:
            raise JungleError(f"MCPJungle returned non-JSON for {path}: {exc}") from exc

    def close(self) -> None:
        self._http.close()

    # -- registry ---------------------------------------------------------------------------
    def health(self) -> dict:
        body = self._req("GET", "/health", timeout=5.0, ok=(200,))
        return body if isinstance(body, dict) else {"status": str(body)}

    def servers(self) -> list[dict]:
        return [s for s in (self._req("GET", "/api/v0/servers", timeout=LIST_TIMEOUT, ok=(200,))
                            or []) if isinstance(s, dict)]

    def register_server(self, body: dict) -> dict:
        check_name(str(body.get("name") or ""))
        got = self._req("POST", "/api/v0/servers", body=body, timeout=REGISTER_TIMEOUT,
                        ok=(200, 201))
        if isinstance(got, dict) and isinstance(got.get("server"), dict):
            return got["server"]
        return got if isinstance(got, dict) else {}

    def deregister_server(self, name: str) -> None:
        self._req("DELETE", f"/api/v0/servers/{check_name(name)}", timeout=REGISTER_TIMEOUT,
                  ok=(200, 204))

    # -- tools ------------------------------------------------------------------------------
    def tools(self) -> list[dict]:
        return [t for t in (self._req("GET", "/api/v0/tools", timeout=LIST_TIMEOUT, ok=(200,))
                            or []) if isinstance(t, dict)]

    def invoke_tool(self, name: str, arguments: dict) -> dict:
        # FLAT body; `name` applied last so an argument cannot retarget the call.
        payload = {**{k: v for k, v in (arguments or {}).items() if k != "name"}, "name": name}
        body = self._req("POST", "/api/v0/tools/invoke", body=payload, ok=(200,))
        return body if isinstance(body, dict) else {"content": [], "isError": False}

    # -- resources --------------------------------------------------------------------------
    def resources(self) -> list[dict]:
        return [r for r in (self._req("GET", "/api/v0/resources", timeout=LIST_TIMEOUT,
                                      ok=(200,)) or []) if isinstance(r, dict)]

    def read_resource(self, jungle_uri: str) -> list[dict]:
        body = self._req("POST", "/api/v0/resources/read", body={"uri": jungle_uri}, ok=(200,))
        return list((body or {}).get("contents") or []) if isinstance(body, dict) else []


_shared: dict[str, JungleAPI] = {}
_shared_lock = threading.Lock()


def shared_api(base_url: Optional[str] = None) -> JungleAPI:
    """One keep-alive API client per base URL for the whole process (the warm route)."""
    url = (base_url or jungle_url()).rstrip("/")
    with _shared_lock:
        api = _shared.get(url)
        if api is None:
            api = _shared[url] = JungleAPI(url)
        return api


# --- resource URIs ------------------------------------------------------------------------------
def encode_resource_uri(server: str, original_uri: str) -> str:
    raw = base64.urlsafe_b64encode(original_uri.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_RES_PREFIX}{server}/{raw}"


def decode_resource_uri(jungle_uri: str) -> tuple[str, str]:
    """`mcpj://res/<server>/<b64url>` -> (server, the server's ORIGINAL uri). Identity is keyed
    on the original URI, so a direct run and a run through MCPJungle mint the same item_id."""
    if not isinstance(jungle_uri, str) or not jungle_uri.startswith(_RES_PREFIX):
        raise JungleError(f"{str(jungle_uri)[:120]!r} is not an MCPJungle resource URI")
    server, sep, enc = jungle_uri[len(_RES_PREFIX):].partition("/")
    # STRICT (Codex f1 #11): a valid server name, the url-safe alphabet only (no padding, no
    # second alphabet tried), a round trip that re-encodes to the same text, and a non-empty
    # original that is not itself an aggregator URI.
    try:
        check_name(server)
    except JungleError as exc:
        raise JungleError(f"MCPJungle resource URI names an invalid server: {exc}") from None
    if not sep or not enc or not _B64URL.match(enc) or len(enc) % 4 == 1:
        raise JungleError(f"could not decode the original URI in {jungle_uri[:120]!r} "
                          f"(not unpadded url-safe base64)")
    try:
        original = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)).decode("utf-8")
    except Exception:                                                # noqa: BLE001
        raise JungleError(f"could not decode the original URI in {jungle_uri[:120]!r}") from None
    if not original.strip() or original.startswith(_RES_PREFIX) \
            or encode_resource_uri(server, original) != jungle_uri:
        raise JungleError(f"the original URI in {jungle_uri[:120]!r} is empty, nested or does "
                          f"not round-trip")
    return server, original


# ================================================================================================
# registration bodies — from infra/mcp_servers.yaml declarations or from a connection
# ================================================================================================
_REF_ANY = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _secretish_var(name: str) -> bool:
    from .connections import _SECRETISH

    return _SECRETISH.search(name)


def _expand_keeping_secret_refs(value: str) -> str:
    """Expand `${VAR}` references EXCEPT those naming a credential, which are kept as the bare
    reference `${VAR}` (a `:-default` is dropped — a default is a literal)."""
    from .connections import expand

    def repl(m: re.Match) -> str:
        if _secretish_var(m.group(1)):
            return "${" + m.group(1) + "}"
        return expand(m.group(0))

    return _REF_ANY.sub(repl, value)


def _secret_refs(values) -> list[str]:
    out: set[str] = set()
    for v in values:
        if v is None:
            continue
        out |= {m.group(1) for m in _REF_ANY.finditer(str(v)) if _secretish_var(m.group(1))}
    return sorted(out)


def _refuse_literals(name: str, command: str, args: list, env: dict) -> None:
    from .secret_scan import find_literal_secrets

    found = find_literal_secrets({"type": "stdio", "command": command, "args": args, "env": env})
    if found:
        raise JungleError(f"{name}: the registration would hand MCPJungle (which stores it) a "
                          f"literal secret at {', '.join(f'{w} ({r})' for w, r in found)}; "
                          f"reference it as ${{VAR}} or connect via: direct")


def _refuse_url_secrets(name: str, url: str) -> None:
    """A URL with `user:password@`, a credential-named query parameter, a key shape or a
    defaulted credential reference is refused before it can reach the aggregator's registry."""
    from .secret_scan import find_literal_secrets

    found = find_literal_secrets({"type": "http", "url": url})
    if found:
        raise JungleError(f"{name}: the URL carries a credential "
                          f"({', '.join(r for _, r in found)}); MCPJungle would store it. "
                          f"Connect this server via: direct")


def _no_http_credentials(name: str, headers: dict, bearer_from: Optional[str] = None) -> None:
    if bearer_from or headers:
        what = sorted(headers) or ["bearer_token_from"]
        raise JungleError(f"{name}: HTTP credentials/headers {what} cannot be registered with "
                          f"MCPJungle — it would store the resolved value, and headers other "
                          f"than a bearer token are not proxied at all. Connect this server "
                          f"via: direct (the value is then read from the host environment at "
                          f"connect time and never stored)")


def body_from_declaration(decl: dict) -> dict:
    """The `mcpjungle:` block of an `infra/mcp_servers.yaml` server → POST /api/v0/servers body.
    Paths in that block are CONTAINER paths. Non-credential `${VAR:-default}` references are
    expanded now; credential references are kept as references (module docstring)."""
    name = check_name(str(decl.get("id") or ""))
    block = decl.get("mcpjungle") or {}
    transport = str(block.get("transport") or "stdio")
    body: dict = {"name": name, "transport": transport,
                  "description": str(decl.get("description") or decl.get("title") or name)[:500],
                  "session_mode": str(block.get("session_mode") or DEFAULT_SESSION_MODE)}
    if transport == "stdio":
        body["command"] = _expand_keeping_secret_refs(str(block.get("command") or ""))
        body["args"] = [_expand_keeping_secret_refs(str(a)) for a in block.get("args") or []]
        env = {str(k): _expand_keeping_secret_refs(str(v))
               for k, v in (block.get("env") or {}).items()}
        _refuse_literals(name, body["command"], body["args"], env)
        if env:
            body["env"] = env
    else:
        _no_http_credentials(name, dict(block.get("headers") or {}),
                             block.get("bearer_token_from"))
        _refuse_url_secrets(name, str(block.get("url") or ""))
        body["url"] = _expand_keeping_secret_refs(str(block.get("url") or ""))
        _refuse_url_secrets(name, body["url"])
        if _secret_refs([body["url"]]):
            raise JungleError(f"{name}: the URL carries a credential; connect via: direct")
    if not body.get("command") and not body.get("url"):
        raise JungleError(f"{name}: the mcpjungle block needs a command (stdio) or a url")
    return body


_LOOPBACK = re.compile(r"^(https?://)(localhost|127\.0\.0\.1|\[::1\])(?=[:/]|$)", re.I)


def body_from_connection(conn, *, description: str = "",
                         container_host: Optional[str] = "host.docker.internal",
                         session_mode: str = "stateful") -> dict:
    """An ad-hoc connection → registration body, from its own transport (HOST paths — right when
    the server needs no host files, e.g. an `npx -y` package or a remote URL).

    MCPJungle runs in a container, where `localhost` is the container itself: a loopback URL is
    rewritten to `container_host` (observed 2026-10-01 — the aggregator refuses the registration
    with "connection refused … use 'host.docker.internal'"). Pass `container_host=None` to keep it.
    Credentials are never resolved into the body (module docstring): `env_from` becomes a
    `${HOST_VAR}` reference; HTTP headers / bearer tokens are refused."""
    from .connections import HttpTransport

    name = check_name(conn.jungle_name)
    body: dict = {"name": name, "description": (description or conn.title or conn.id)[:500],
                  "session_mode": session_mode}
    t = conn.transport
    if isinstance(t, HttpTransport):
        _no_http_credentials(name, {**dict(t.headers or {}), **dict(t.headers_from or {})})
        body["transport"] = "streamable_http"
        # FXJ3 (Codex f2 #5): the SAME full URL scan as `body_from_declaration`, on the declared
        # URL AND on its expansion (a non-credential `${URL}` may expand to `user:pw@` or
        # `?token=`). MCPJungle stores the body, so a URL credential is refused, never sent.
        _refuse_url_secrets(name, t.url)
        url = _expand_keeping_secret_refs(t.url)
        _refuse_url_secrets(name, url)
        if _secret_refs([url]):
            raise JungleError(f"{name}: the URL carries a credential reference; connect via: "
                              f"direct")
        body["url"] = _LOOPBACK.sub(rf"\g<1>{container_host}", url) if container_host else url
    else:
        command = _expand_keeping_secret_refs(t.command)
        args = [_expand_keeping_secret_refs(a) for a in t.args]
        env = {k: _expand_keeping_secret_refs(v) for k, v in t.env.items()}
        env.update({child: "${" + host + "}" for child, host in t.env_from.items()})
        _refuse_literals(name, command, args, env)
        body.update({"transport": "stdio", "command": command, "args": args})
        if env:
            body["env"] = env
    return body


def _same_config(existing: dict, body: dict) -> bool:
    keys = ("transport", "command", "args", "url", "session_mode")
    norm = lambda d, k: (list(d.get(k) or []) if k == "args" else (d.get(k) or ""))  # noqa: E731
    if any(norm(existing, k) != norm(body, k) for k in keys):
        return False
    return dict(existing.get("env") or {}) == dict(body.get("env") or {})


# ================================================================================================
# the registry manager
# ================================================================================================
class JungleRegistry:
    def __init__(self, api: Optional[JungleAPI] = None) -> None:
        self.api = api or shared_api()

    # -- reads ------------------------------------------------------------------------------
    def list_servers(self) -> list[dict]:
        return self.api.servers()

    def server_names(self) -> set[str]:
        return {str(s.get("name")) for s in self.api.servers()}

    def server_tools(self, name: str, *, tools: Optional[list[dict]] = None) -> list[dict]:
        """Tools of ONE server, with the `<server>__` prefix stripped from `name`."""
        prefix = f"{check_name(name)}{SEPARATOR}"
        out = []
        for t in (tools if tools is not None else self.api.tools()):
            full = str(t.get("name") or "")
            if full.startswith(prefix):
                out.append({**t, "name": full[len(prefix):], "qualified_name": full})
        return out

    def tools_by_server(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self.api.tools():
            server, sep, tool = str(t.get("name") or "").partition(SEPARATOR)
            if sep:
                out.setdefault(server, []).append(tool)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def health(self) -> dict:
        """Gateway up? + per server: enabled, tool count, resource count, latency of the listing.
        A tool count of zero for an enabled server usually means it failed to start inside the
        container — the aggregator keeps the registration even so."""
        t0 = time.perf_counter()
        try:
            status = self.api.health()
        except JungleError as exc:
            return {"ok": False, "url": self.api.base_url, "error": str(exc), "servers": []}
        health_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        servers, tools = self.api.servers(), self.api.tools()
        try:
            resources = self.api.resources()
        except JungleError:
            resources = []
        list_ms = (time.perf_counter() - t1) * 1000
        rows = []
        for s in servers:
            name = str(s.get("name"))
            prefix = f"{name}{SEPARATOR}"
            n_tools = sum(1 for t in tools if str(t.get("name", "")).startswith(prefix))
            n_res = sum(1 for r in resources if str(r.get("name", "")).startswith(prefix))
            rows.append({"name": name, "enabled": s.get("enabled", True),
                         "transport": s.get("transport"), "tools": n_tools, "resources": n_res,
                         "ok": bool(s.get("enabled", True)) and (n_tools + n_res) > 0})
        return {"ok": status.get("status") == "ok", "url": self.api.base_url,
                "health_ms": round(health_ms, 1), "list_ms": round(list_ms, 1), "servers": rows}

    # -- writes -----------------------------------------------------------------------------
    def register(self, body: dict, *, on_conflict: OnConflict = "error") -> dict:
        """Register one server. Returns {name, requested, status: registered|exists|replaced|
        renamed|skipped, tools}."""
        requested = check_name(str(body.get("name") or ""))
        existing = {str(s.get("name")): s for s in self.api.servers()}
        name, status = requested, "registered"
        if requested in existing:
            if _same_config(existing[requested], body):
                return {"name": requested, "requested": requested, "status": "exists",
                        "tools": len(self.server_tools(requested))}
            if on_conflict == "skip":
                return {"name": requested, "requested": requested, "status": "skipped",
                        "reason": "a different server is registered under this name"}
            if on_conflict == "replace":
                self.api.deregister_server(requested)
                status = "replaced"
            elif on_conflict == "rename":
                n = 2
                while f"{requested}_{n}" in existing:
                    n += 1
                name, status = check_name(f"{requested}_{n}"), "renamed"
            else:
                raise JungleConflict(
                    f"MCPJungle already has a server named {requested!r} with a different "
                    f"launch config; pass on_conflict='replace' or 'rename'")
        self.api.register_server({**body, "name": name})
        refs = _secret_refs([body.get("command"), *(body.get("args") or []),
                             *(body.get("env") or {}).values(), body.get("url")])
        out = {"name": name, "requested": requested, "status": status,
               "tools": len(self.server_tools(name))}
        if refs:
            out["secret_references"] = refs
            out["note"] = ("credential references were registered as ${VAR}, not values: "
                           "MCPJungle must resolve them in its own environment")
        return out

    def deregister(self, name: str, *, missing_ok: bool = True) -> bool:
        if name not in self.server_names():
            if missing_ok:
                return False
            raise JungleError(f"MCPJungle has no server named {name!r}")
        self.api.deregister_server(name)
        return True

    def register_many(self, bodies: Iterable[dict], *, on_conflict: OnConflict = "error"
                      ) -> list[dict]:
        """Bulk register; one failure never stops the rest (each result says what happened)."""
        out = []
        for body in bodies:
            try:
                out.append(self.register(body, on_conflict=on_conflict))
            except JungleError as exc:
                out.append({"name": body.get("name"), "requested": body.get("name"),
                            "status": "failed", "error": str(exc)})
        return out

    def deregister_many(self, names: Iterable[str]) -> dict[str, bool]:
        present = self.server_names()
        out = {}
        for n in names:
            if n in present:
                self.api.deregister_server(n)
                out[n] = True
            else:
                out[n] = False
        return out

    def register_declarations(self, path: Optional[Path] = None, *,
                              only: Optional[list[str]] = None,
                              on_conflict: OnConflict = "error") -> list[dict]:
        """Register every server in `infra/mcp_servers.yaml` (or `only` those ids) that has an
        `mcpjungle:` block. A declaration that cannot be expanded (unset `${VAR}`) is reported as
        failed, not skipped silently."""
        from .connections import declarations_path

        path = path or declarations_path()                          # N-22: configurable
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        bodies, failed = [], []
        for decl in data.get("servers") or []:
            if only and decl.get("id") not in only:
                continue
            if not decl.get("mcpjungle"):
                failed.append({"name": decl.get("id"), "status": "skipped",
                               "reason": "no mcpjungle block"})
                continue
            try:
                bodies.append(body_from_declaration(decl))
            except Exception as exc:                                  # noqa: BLE001
                failed.append({"name": decl.get("id"), "status": "failed", "error": str(exc)})
        return self.register_many(bodies, on_conflict=on_conflict) + failed

    def register_connection(self, conn, *, on_conflict: OnConflict = "error",
                            description: str = "") -> dict:
        return self.register(body_from_connection(conn, description=description),
                             on_conflict=on_conflict)


def registry(base_url: Optional[str] = None, *, transport=None) -> JungleRegistry:
    api = JungleAPI(base_url, transport=transport) if transport is not None else \
        shared_api(base_url)
    return JungleRegistry(api)


__all__ = ["JungleAPI", "JungleRegistry", "JungleError", "JungleConflict", "registry",
           "shared_api", "jungle_url", "check_name", "body_from_declaration",
           "body_from_connection", "encode_resource_uri", "decode_resource_uri", "SEPARATOR"]
