"""`SourceConnection` — a declared source (P5: declare scope, do not discover it).

A connection is YAML under `WALNUT_SOURCES_DIR/<id>.yaml`. It says HOW to reach an MCP server
(stdio command or streamable-HTTP URL, directly or through MCPJungle) and WHAT may be collected
(include / exclude / blocked patterns, file types, size and count caps).

**Secrets are never stored.** A value that must be secret (an API key in the child's environment,
an `Authorization` header) is declared by the NAME of a host environment variable — either
`env_from`/`headers_from` ({NAME: HOST_VAR}) or a `${HOST_VAR}` reference inside the value (the
`infra/mcp_servers.yaml` convention) — and expanded at connect time. A literal value whose key
looks like a credential is refused at registration AND at load, so a pasted key cannot end up in
a YAML file on disk by accident; a `${VAR:-default}` in a credential position is a literal too
(`secret_scan`, Codex f2 #3). `${WALNUT_ROOT}` defaults to the `databridge/` directory.

**Registration authority (Codex f2 #10).** The command allow-list / SSRF / auto_approve policy
applies to every registration unless the DEPLOYMENT runs in operator mode
(`WALNUT_SOURCE_REGISTRATION=operator`, set by the operator — never by a caller).

`import_declarations()` reads `infra/mcp_servers.yaml` (shape version 1) into connections.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import paths

_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
#: A key is secret-looking when one of its words (split on _ - .) is one of these. Word-wise, so
#: `AUTH_PORT` or `KEYBOARD_LAYOUT` are not mistaken for credentials.
_SECRET_WORDS = frozenset({"key", "apikey", "token", "secret", "password", "passwd", "pwd",
                           "credential", "credentials", "cookie", "authorization", "bearer"})


#: FXJ3 (Codex f2 #4): a credential word CONCATENATED onto a name (`PGPASSWORD`, `MYSQLPWD`,
#: `APITOKEN`) is just as secret as a separated one.
_SECRET_SUFFIX = re.compile(r"(?i)(?:passw(?:or)?d|pwd|secret|token|apikey)$")


class _SecretIsh:
    @staticmethod
    def search(name: str) -> bool:
        return any(w in _SECRET_WORDS for w in re.split(r"[_\-.\s]+", name.lower()) if w) \
            or bool(_SECRET_SUFFIX.search(name.strip()))


_SECRETISH = _SecretIsh()
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConnectionError_(ValueError):
    """A connection declaration is malformed or cannot be resolved."""


def expand(value: str) -> str:
    """Expand `${VAR}` / `${VAR:-default}` from the environment. An unset VAR with no default is
    an error — a silently empty path or password is how a connection points at the wrong thing."""
    def repl(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        got = os.environ.get(name)
        if got is None and name == "WALNUT_ROOT":
            got = str(paths.DATABRIDGE_ROOT)
        if got is None:
            if default is None:
                raise ConnectionError_(f"${{{name}}} is referenced but not set")
            return default
        return got

    return _REF.sub(repl, value)


def _literal_secrets(mapping: dict[str, str]) -> list[str]:
    return sorted(k for k, v in mapping.items() if _SECRETISH.search(k) and not _REF.search(v))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StdioTransport(_Strict):
    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_from: dict[str, str] = Field(default_factory=dict)
    """child env var -> HOST env var NAME. The value is read at connect time and never written."""
    cwd: Optional[str] = None

    @field_validator("env")
    @classmethod
    def _no_literal_secrets(cls, v: dict[str, str]) -> dict[str, str]:
        bad = _literal_secrets(v)
        if bad:
            raise ValueError(
                f"env {bad} look like secrets; declare them with env_from: {{NAME: HOST_ENV_VAR}} "
                f"so the value is read from the environment and never stored")
        return v


class HttpTransport(_Strict):
    type: Literal["http"] = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    headers_from: dict[str, str] = Field(default_factory=dict)
    """header name -> HOST env var NAME holding its value."""

    @field_validator("headers")
    @classmethod
    def _no_literal_secrets(cls, v: dict[str, str]) -> dict[str, str]:
        bad = _literal_secrets(v)
        if bad:
            raise ValueError(
                f"headers {bad} look like secrets; declare them with headers_from: "
                f"{{Header: HOST_ENV_VAR}}")
        return v


Transport = Union[StdioTransport, HttpTransport]


class Scope(_Strict):
    """What may be collected. Every item outside it is RECORDED (excluded/blocked), never ignored."""

    roots: list[str] = Field(default_factory=list)
    """Starting paths/ids. Empty ⇒ the plan's roots step (e.g. the server's allowed directories)."""
    include: list[str] = Field(default_factory=list)
    """Glob patterns on the item's path relative to its root; empty ⇒ everything."""
    exclude: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)
    """Glob patterns that must never be collected (e.g. `**/pricing/**`). Ledgered as BLOCKED."""
    file_types: list[str] = Field(default_factory=list)
    """Allowed extensions (".pdf", "md"); empty ⇒ every type an extractor exists for."""
    max_bytes: int = 25 * 1024 * 1024
    max_items: int = 5000
    max_depth: int = 12
    tables_include: list[str] = Field(default_factory=list)
    tables_exclude: list[str] = Field(default_factory=list)
    max_rows_per_table: Optional[int] = None
    """An EXPLICIT row cap per table (C-2 / FIN-18). None (the default) reads every table WHOLE,
    page by page; a cap, when set, is reported per run as "N of M rows" (`coverage`)."""
    table_page_rows: int = 1000
    """Rows per paged read of a table (one SQL statement each)."""
    deny_tools: list[str] = Field(default_factory=list)
    """Tools this connection must never call during ingestion, whatever their names suggest
    (enforced by `client.ReadOnlyClient` in addition to the write-verb pattern)."""

    @field_validator("file_types")
    @classmethod
    def _dotted(cls, v: list[str]) -> list[str]:
        return [t.lower() if t.startswith(".") else f".{t.lower()}" for t in v]


class SourceConnection(_Strict):
    id: str
    title: str = ""
    transport: Transport = Field(discriminator="type")
    via: Literal["direct", "mcpjungle"] = "direct"
    jungle_server: Optional[str] = None
    """The server's name inside MCPJungle (tools are `<server>__<tool>`). Defaults to `id`."""
    scope: Scope = Field(default_factory=Scope)
    auto_approve: bool = False
    """READ BUT IGNORED since EF3 (Codex round 3, Critical): a connection flag is not a person,
    so it approves nothing. Kept so older connection files still load; proposals report it."""
    tools_hint: dict[str, Any] = Field(default_factory=dict)
    """The declaration's `tools_hint` (`infra/mcp_servers.yaml`): the tools the planner should
    reach for, and — on a write connection — the WRITE tool the automation catalogue advertises
    (E2E F-08). A claim, not a grant: the tool layer still classifies every tool."""
    writes_for: list[str] = Field(default_factory=list)
    """For `purpose: agent_write`: the ingest connection(s) whose data this writer changes."""
    aliases: list[str] = Field(default_factory=list)
    """Other names people use for this source ("CRM"), so English resolves to it (EF4)."""
    document_kind: Optional[str] = None
    """Node kind for the per-item document anchor. Default: the single kind binding to the
    `document` meta-kind in the active vocabulary (recorded when there is a choice)."""
    trust: Literal["untrusted"] = "untrusted"
    """Third-party MCP content is untrusted until a named human justifies otherwise (§4)."""
    purpose: Literal["ingest", "agent_write"] = "ingest"
    """`ingest`: crawled read-only into the graph. `agent_write`: a write-capable connection that
    agents act through — it is NEVER crawled (plans and runs refuse it); ingest the same source
    through a separate `ingest` connection with read-only credentials."""

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"connection id {v!r} must match {_ID_RE.pattern} (it names files)")
        return v

    @model_validator(mode="after")
    def _jungle_needs_name(self) -> "SourceConnection":
        if self.via == "mcpjungle" and "__" in (self.jungle_server or self.id):
            raise ValueError("an MCPJungle server name may not contain '__' (its tool separator)")
        return self

    @property
    def jungle_name(self) -> str:
        return self.jungle_server or self.id

    def resolved_env(self) -> dict[str, str]:
        """The child env: literal non-secret values plus env_from lookups. Missing ⇒ error."""
        t = self.transport
        if not isinstance(t, StdioTransport):
            return {}
        out = {k: expand(v) for k, v in t.env.items()}
        for child, host in t.env_from.items():
            value = os.environ.get(host)
            if value is None:
                raise ConnectionError_(
                    f"connection {self.id!r}: env_from {child} <- ${host}, but {host} is not set")
            out[child] = value
        return out

    def resolved_headers(self) -> dict[str, str]:
        t = self.transport
        if not isinstance(t, HttpTransport):
            return {}
        out = {k: expand(v) for k, v in t.headers.items()}
        for header, host in t.headers_from.items():
            value = os.environ.get(host)
            if value is None:
                raise ConnectionError_(
                    f"connection {self.id!r}: headers_from {header} <- ${host}, but it is not set")
            out[header] = value
        return out


    def resolved_command(self) -> tuple[str, list[str], Optional[str]]:
        t = self.transport
        if not isinstance(t, StdioTransport):
            raise ConnectionError_(f"connection {self.id!r} is not a stdio connection")
        return expand(t.command), [expand(a) for a in t.args], (expand(t.cwd) if t.cwd else None)

    def resolved_url(self) -> str:
        t = self.transport
        if not isinstance(t, HttpTransport):
            raise ConnectionError_(f"connection {self.id!r} is not an http connection")
        return expand(t.url)


# --- persistence --------------------------------------------------------------------------------
def connection_path(source_id: str, root: Optional[Path] = None) -> Path:
    return (root or paths.sources_dir()) / f"{source_id}.yaml"


def save_connection(conn: SourceConnection, root: Optional[Path] = None) -> Path:
    """Persist a connection. Every write path goes through here, so the literal-secret scan is
    enforced HERE (FXI3, Codex f1 #5): a secret-bearing model is refused, never written."""
    refuse_literal_secrets(conn)
    path = connection_path(conn.id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = conn.model_dump(mode="json", exclude_defaults=False)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def load_connection(source_id: str, root: Optional[Path] = None) -> SourceConnection:
    path = connection_path(source_id, root)
    if not path.is_file():
        raise ConnectionError_(f"no connection {source_id!r} at {path}")
    # FXJ3 (Codex f2 #7): scanned EXACTLY like a registration — the raw YAML first (a refusal
    # never depends on, and a validation error never echoes, a value), then a redacted validation,
    # then the model. A legacy or hand-edited file holding a literal secret (`?token=...`,
    # `PGPASSWORD: ...`, a defaulted credential) is refused at load, never used.
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _refuse_raw(data)
    conn = _validate_redacted(data)
    refuse_literal_secrets(conn)
    return conn


def _connection_files(base: Path) -> list[Path]:
    # plan/mapping files share the directory: `<id>.plan.yaml`, `<id>.mapping.yaml`
    return [p for p in sorted(base.glob("*.yaml")) if p.stem.count(".") == 0]


def list_connections(root: Optional[Path] = None) -> list[SourceConnection]:
    """Every stored connection that passes the SAME policy as registration (`load_connection`).
    A file that fails it (a literal secret, a defaulted credential, a malformed field) is never
    returned and never raises out of a listing (E2E run 2, N-10): it is reported, with its reason,
    by `connection_problems`, and logged."""
    base = root or paths.sources_dir()
    if not base.is_dir():
        return []
    out: list[SourceConnection] = []
    for p in _connection_files(base):
        try:
            out.append(load_connection(p.stem, base))
        except Exception as exc:                                     # noqa: BLE001
            import logging

            from .secret_scan import redact

            logging.getLogger(__name__).warning("connection %s refused at load: %s", p.stem,
                                                redact(str(exc))[:300])
    return out


def connection_problems(root: Optional[Path] = None) -> list[dict]:
    """`[{id, path, error}]` for every stored connection file that is REFUSED at load (the policy
    `list_connections` applies), so a listing can show it as invalid instead of failing. The
    error names where and which rule — never the value."""
    from .secret_scan import redact

    base = root or paths.sources_dir()
    if not base.is_dir():
        return []
    out: list[dict] = []
    for p in _connection_files(base):
        try:
            load_connection(p.stem, base)
        except Exception as exc:                                     # noqa: BLE001
            out.append({"id": p.stem, "path": str(p), "error": redact(str(exc))[:500]})
    return out


def refuse_literal_secrets(conn: SourceConnection) -> None:
    """Codex round 3 (c) + FXI3 (Codex f1 #5): a connection may name a secret (an env-var NAME,
    `${VAR}`), never hold one — in its transport AND in every other persisted field
    (`tools_hint`, `title`, `aliases`, `scope`, ...). Raises naming WHERE and WHICH rule — never
    the value (`secret_scan`)."""
    _refuse_found(conn.id, conn.transport)
    _refuse_found_in(conn.id, conn.model_dump(mode="json", exclude={"transport"}))


def _refuse_found_in(cid: str, data: Any) -> None:
    from .secret_scan import find_literal_secrets_in

    found = find_literal_secrets_in(data)
    if found:
        where = ", ".join(f"{loc} ({rule})" for loc, rule in found)
        raise ConnectionError_(
            f"connection {cid!r} holds a literal secret at {where}; nothing was saved")


def _refuse_found(cid: str, transport) -> None:
    from .secret_scan import find_literal_secrets

    found = find_literal_secrets(transport)
    if found:
        where = ", ".join(f"{loc} ({rule})" for loc, rule in found)
        raise ConnectionError_(
            f"connection {cid!r} holds a literal secret at {where}. Put the value in the "
            f"host environment and reference it by NAME (env_from / headers_from, or ${{VAR}}); "
            f"nothing was saved")


def _refuse_raw(spec: Any) -> None:
    """Scan a RAW declaration before it is validated, so a refusal never depends on (and a
    validation error never echoes) the value."""
    if not isinstance(spec, dict):
        return
    cid = str(spec.get("id") or "?")
    raw_t = spec.get("transport")
    if isinstance(raw_t, dict):
        _refuse_found(cid, raw_t)
    _refuse_found_in(cid, {k: v for k, v in spec.items() if k != "transport"})


def _validate_redacted(spec) -> SourceConnection:
    """`model_validate`, but a validation error never carries the input values back out (pydantic
    echoes `input_value`, which for a refused env/header IS the secret)."""
    from pydantic import ValidationError

    try:
        return SourceConnection.model_validate(spec)
    except ValidationError as exc:
        parts = [f"{'.'.join(str(x) for x in e.get('loc') or ())}: {e.get('msg')}"
                 for e in exc.errors(include_input=False, include_url=False)]
        raise ConnectionError_("invalid connection (a literal secret or bad field; values "
                               "withheld): " + "; ".join(parts)) from None


# --- registration policy (FXI3, Codex f1 #8) ------------------------------------------------------
#: The same operator allow-lists the gateway reads (sources_routes.py), enforced HERE so every
#: caller that registers on behalf of a REMOTE principal is covered by the package itself.
COMMAND_SOURCES_ENV = "WALNUT_COMMAND_SOURCES"
RUNNER_RISK_ENV = "WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK"
SECRET_HOSTS_ENV = "WALNUT_SOURCE_SECRET_HOSTS"
ALLOWED_HOSTS_ENV = "WALNUT_SOURCE_ALLOWED_HOSTS"
#: Executables whose ARGUMENTS choose the code they run (see the gateway's list; kept equal).
CODE_RUNNERS = frozenset({
    "npx", "pnpx", "bunx", "uvx", "pipx", "uv", "npm", "pnpm", "yarn", "bun", "deno", "node",
    "python", "python3", "py", "pythonw", "ruby", "perl", "php", "java", "dotnet",
    "sh", "bash", "zsh", "dash", "fish", "cmd", "powershell", "pwsh", "wsl", "env",
    "docker", "podman", "npm-cli", "corepack"})
#: The aggregator address an MCPJungle-routed connection carries (operator-controlled by env).
JUNGLE_DEFAULT_URL = "${MCPJUNGLE_URL:-http://127.0.0.1:8080}"
Origin = Literal["operator", "remote"]
#: FXJ3 (Codex f2 #10): the DEPLOYMENT's registration mode — set by the operator in the process
#: environment (the local launcher `start-databridge.ps1` and the test fixtures declare
#: `operator`), never by a caller. Unset, or anything else: `remote` (fail closed).
REGISTRATION_MODE_ENV = "WALNUT_SOURCE_REGISTRATION"


def registration_mode() -> Origin:
    """`operator` only when the operator set `WALNUT_SOURCE_REGISTRATION=operator`; otherwise
    `remote`, and the command allow-list, SSRF and auto_approve policy apply."""
    v = (os.environ.get(REGISTRATION_MODE_ENV) or "").strip().lower()
    return "operator" if v == "operator" else "remote"


def effective_origin(requested: Optional[str] = None) -> Origin:
    """The authority a registration actually gets. A caller may NARROW it (`remote` always
    holds); it can never WIDEN it: `operator` (or no say) is granted only in operator mode."""
    if requested == "remote":
        return "remote"
    if requested not in (None, "operator"):
        raise ConnectionError_(f"origin must be 'operator' or 'remote', not {requested!r}")
    return registration_mode()


class RegistrationIncomplete(ConnectionError_):
    """A WRITE connection was saved but its live tool advert could not be written (Codex f2 #11):
    the registration is NOT complete, because the automation catalogue cannot see its tools.
    `connections` lists what was saved; `failures` is `[{id, error}]`. Finish it with
    `plans.advertise_write_tools([id])` once the server is reachable."""

    def __init__(self, message: str, *, connections: list, failures: list[dict]) -> None:
        super().__init__(message)
        self.code = "TOOL_ADVERT_FAILED"
        self.connections = connections
        self.failures = failures


class RegistrationRefused(ConnectionError_):
    """A registration the package policy refuses. `code` is a closed, machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in (os.environ.get(name) or "").split(",") if x.strip()]


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _which(cmd: str) -> Optional[str]:
    import shutil

    return shutil.which(cmd)


def _resolve_exe(cmd: str) -> Optional[str]:
    c = (cmd or "").strip()
    if not c:
        return None
    if "/" in c or "\\" in c or os.sep in c:
        return os.path.normcase(os.path.realpath(os.path.abspath(c)))
    found = _which(c)
    return os.path.normcase(os.path.realpath(found)) if found else None


def _runner_name(resolved: str) -> str:
    base = os.path.basename(resolved).lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def command_refusal(command: str) -> Optional[str]:
    """None when a REMOTE registration may launch `command`, else why not: it must resolve to the
    exact file an entry of `WALNUT_COMMAND_SOURCES` resolves to, and a package runner /
    interpreter / shell needs `WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK=1` as well."""
    allowed = _env_list(COMMAND_SOURCES_ENV)
    if not allowed:
        return (f"registering a command source on behalf of a remote caller is disabled: it "
                f"would let that caller launch a program on the host. An operator enables it by "
                f"listing the exact executables in {COMMAND_SOURCES_ENV}.")
    want = _resolve_exe(command)
    if want is None:
        return f"{command!r} does not resolve to an executable on this host"
    if not any(_resolve_exe(entry) == want for entry in allowed):
        return f"{command!r} ({want}) is not an allowed executable ({COMMAND_SOURCES_ENV})"
    if _runner_name(want) in CODE_RUNNERS and not _truthy(RUNNER_RISK_ENV):
        return (f"{command!r} is a package runner / interpreter: its arguments choose the code "
                f"it runs. An operator who accepts that risk sets {RUNNER_RISK_ENV}=1.")
    return None


def _resolve_host(host: str, port: int) -> list[str]:
    import socket

    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _non_global(address: str) -> bool:
    import ipaddress

    ip = ipaddress.ip_address(address.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (not ip.is_global) or ip.is_multicast


def destination_refusal(url: str) -> Optional[str]:
    """SSRF policy: http(s) only, no `${VAR}` in the URL, and every address the host resolves to
    must be global unless the host is on `WALNUT_SOURCE_ALLOWED_HOSTS`. Unresolvable -> refused."""
    from urllib.parse import urlsplit

    if "${" in (url or ""):
        return "a URL registered by a remote caller may not reference host environment variables"
    parts = urlsplit(url or "")
    if parts.scheme.lower() not in ("http", "https"):
        return f"{url!r} is not an http(s) URL"
    host = (parts.hostname or "").lower()
    if not host:
        return f"{url!r} names no host"
    if host in {h.lower() for h in _env_list(ALLOWED_HOSTS_ENV)}:
        return None
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        addresses = _resolve_host(host, port)
    except (OSError, ValueError) as exc:
        return f"{host!r} could not be resolved to check it ({type(exc).__name__})"
    if not addresses:
        return f"{host!r} resolved to no address"
    bad = sorted({a for a in addresses if _non_global(a)})
    if bad:
        return (f"{host!r} resolves to a non-public address ({', '.join(bad)}); an operator may "
                f"list the host in {ALLOWED_HOSTS_ENV}")
    return None


def _has_env_ref(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_env_ref(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_env_ref(v) for v in value)
    return isinstance(value, str) and "${" in value


def registration_refusal(spec: Union[SourceConnection, dict], *,
                         origin: Origin = "remote") -> Optional[tuple[str, str]]:
    """The registration policy — `(code, message)` or None. `origin="operator"` (the operator's
    own CLI / declarations file) is not restricted; `origin="remote"` (anything registering on
    behalf of a caller who is not the host's operator — the gateway) is:
      * `auto_approve` is refused (only a principal approves, on the approve route);
      * a stdio transport: no `env` / `env_from` / `cwd`, no `${VAR}` in command or args, and the
        command must pass `command_refusal`;
      * an http transport: `headers_from` only to a host on `WALNUT_SOURCE_SECRET_HOSTS`, no
        `${VAR}` in headers, and the URL must pass `destination_refusal` (SSRF) — except the
        operator's own aggregator address on an MCPJungle route."""
    if origin == "operator":
        return None
    data = spec.model_dump(mode="json") if isinstance(spec, SourceConnection) else dict(spec or {})
    if data.get("auto_approve"):
        return ("AUTO_APPROVE_NOT_ALLOWED", "auto_approve cannot be set by a remote caller: a "
                                            "plan is approved by an authenticated principal")
    t = data.get("transport") or {}
    if t.get("type", "stdio") == "stdio":
        chosen = [k for k in ("env", "env_from", "cwd") if t.get(k)]
        if chosen:
            return ("COMMAND_ENV_NOT_ALLOWED",
                    f"a command source registered by a remote caller may not set {chosen}")
        if _has_env_ref([t.get("command"), t.get("args")]):
            return ("ENV_REFERENCE_NOT_ALLOWED", "a command source registered by a remote caller "
                                                 "may not reference host environment variables")
        why = command_refusal(str(t.get("command") or ""))
        if why:
            return ("COMMAND_SOURCES_DISABLED", why)
        return None
    from urllib.parse import urlsplit

    url = str(t.get("url") or "")
    if t.get("headers_from"):
        host = (urlsplit(url).hostname or "").lower()
        if not host or host not in {h.lower() for h in _env_list(SECRET_HOSTS_ENV)}:
            return ("SECRET_HOST_NOT_ALLOWED",
                    f"this source would send host secrets ({sorted(t['headers_from'])}) to "
                    f"{host or url!r}, which is not on {SECRET_HOSTS_ENV}")
    if _has_env_ref(t.get("headers")):
        return ("ENV_REFERENCE_NOT_ALLOWED", "headers registered by a remote caller may not "
                                             "reference host environment variables")
    if not (data.get("via") == "mcpjungle" and url == JUNGLE_DEFAULT_URL):
        why = destination_refusal(url)
        if why:
            return ("SOURCE_DESTINATION_NOT_ALLOWED", why)
    return None


def _refuse_by_policy(conn: SourceConnection, origin: Origin, requested: Optional[str]) -> None:
    refused = registration_refusal(conn, origin=origin)
    if refused:
        code, msg = refused
        if requested in (None, "operator") and origin == "remote":
            msg += (f" (this deployment registers in remote mode: the operator's own tools get "
                    f"operator authority only when the operator sets "
                    f"{REGISTRATION_MODE_ENV}=operator in the process environment)")
        raise RegistrationRefused(code, msg)


def _advertise_one(conn: SourceConnection, root: Optional[Path], client) -> Optional[str]:
    """Write a WRITE connection's live tool advert. Returns the error text when it could not be
    written; None when it was, or when the connection is not a write connection."""
    if getattr(conn, "purpose", "ingest") != "agent_write":
        return None
    from .plans import write_tool_advert                            # lazy: plans imports us
    from .secret_scan import redact

    try:
        write_tool_advert(conn.id, client=client, root=root)
        return None
    except Exception as exc:                                         # noqa: BLE001
        return redact(f"{type(exc).__name__}: {exc}")[:500]


def register_connection(spec: Union[SourceConnection, dict], *, root: Optional[Path] = None,
                        overwrite: bool = True, origin: Optional[Origin] = None,
                        advertise: bool = True, client: Any = None) -> SourceConnection:
    """Validate and persist a connection declaration. Returns the validated model.

    Literal secrets are refused (raw declaration first, so no validation error can echo one).
    The registration POLICY (`registration_refusal`: command allow-list, SSRF, no auto_approve)
    applies unless the DEPLOYMENT is in operator mode (`registration_mode`, Codex f2 #10). A caller
    may pass `origin="remote"` to narrow it; it can never pass `"operator"` to widen it.

    A WRITE connection (`purpose: agent_write`) is registered together with its live tool advert
    (Codex f2 #11). If the advert cannot be written, `RegistrationIncomplete` is raised: the
    connection is saved (so `plans.advertise_write_tools` can finish the job), but the registration
    is reported as incomplete, never as a success. `advertise=False` is only for a caller that
    writes and reports the advert itself, or for a test about something else. `client` is a live
    session to list the tools through, instead of opening one."""
    requested = origin
    if isinstance(spec, SourceConnection):
        conn = spec
    else:
        _refuse_raw(spec)
        conn = _validate_redacted(spec)
    refuse_literal_secrets(conn)
    _refuse_by_policy(conn, effective_origin(requested), requested)
    path = connection_path(conn.id, root)
    if path.exists() and not overwrite:
        raise ConnectionError_(f"connection {conn.id!r} already exists at {path}")
    save_connection(conn, root)
    if advertise:
        err = _advertise_one(conn, root, client)
        if err is not None:
            raise RegistrationIncomplete(
                f"connection {conn.id!r} was saved but its write-tool advert FAILED ({err}); the "
                f"registration is incomplete until plans.advertise_write_tools([{conn.id!r}]) "
                f"succeeds", connections=[conn], failures=[{"id": conn.id, "error": err}])
    return conn


def register_connections(specs: list, *, root: Optional[Path] = None,
                         overwrite: bool = True, origin: Optional[Origin] = None,
                         advertise: bool = True) -> list[dict]:
    """Register MANY connections at once. One bad declaration never stops the rest: every spec
    gets a result `{id, ok, error, connection, incomplete}` in input order. A write connection
    whose advert failed is `ok: False, incomplete: True`, WITH its saved `connection`."""
    from .secret_scan import redact

    out: list[dict] = []
    for spec in specs:
        sid = spec.id if isinstance(spec, SourceConnection) else (spec or {}).get("id")
        try:
            conn = register_connection(spec, root=root, overwrite=overwrite, origin=origin,
                                       advertise=advertise)
            out.append({"id": conn.id, "ok": True, "error": None, "connection": conn,
                        "incomplete": False})
        except RegistrationIncomplete as exc:
            out.append({"id": sid, "ok": False, "error": redact(str(exc)),
                        "connection": exc.connections[0], "incomplete": True})
        except Exception as exc:                                     # noqa: BLE001
            out.append({"id": sid, "ok": False, "error": redact(str(exc)), "connection": None,
                        "incomplete": False})
    return out


def stdio_connection(source_id: str, command: str, args: Optional[list[str]] = None, *,
                     env: Optional[dict[str, str]] = None,
                     env_from: Optional[dict[str, str]] = None, cwd: Optional[str] = None,
                     via: str = "direct", jungle_server: Optional[str] = None,
                     title: str = "", scope: Optional[dict] = None,
                     auto_approve: bool = False) -> SourceConnection:
    """An ad-hoc stdio server (`npx -y <pkg>`, `uvx <pkg>`, `python server.py`), validated."""
    transport: dict = {"type": "stdio", "command": command, "args": list(args or []),
                       "env": dict(env or {}), "env_from": dict(env_from or {})}
    if cwd:
        transport["cwd"] = cwd
    return _checked({
        "id": source_id, "title": title or source_id, "transport": transport, "via": via,
        "jungle_server": jungle_server, "scope": scope or {}, "auto_approve": auto_approve})


def _checked(raw: dict) -> SourceConnection:
    """FXJ3 (Codex f2 #8): the public factories scan the RAW declaration and validate it redacted,
    like every other entry point, so a refused literal is never echoed in a pydantic error."""
    _refuse_raw(raw)
    conn = _validate_redacted(raw)
    refuse_literal_secrets(conn)
    return conn


def http_connection(source_id: str, url: str, *, headers: Optional[dict[str, str]] = None,
                    headers_from: Optional[dict[str, str]] = None, via: str = "direct",
                    jungle_server: Optional[str] = None, title: str = "",
                    scope: Optional[dict] = None, auto_approve: bool = False) -> SourceConnection:
    """An ad-hoc streamable-HTTP server, validated. Secret headers by host env-var NAME only."""
    return _checked({
        "id": source_id, "title": title or source_id,
        "transport": {"type": "http", "url": url, "headers": dict(headers or {}),
                      "headers_from": dict(headers_from or {})},
        "via": via, "jungle_server": jungle_server, "scope": scope or {},
        "auto_approve": auto_approve})


def with_route(conn: SourceConnection, via: str, *,
               jungle_server: Optional[str] = None) -> SourceConnection:
    """The same source over the other route. The id is kept, so item identities (and therefore
    pointers) do not depend on the route."""
    if via not in ("direct", "mcpjungle"):
        raise ConnectionError_(f"route must be direct or mcpjungle, not {via!r}")
    data = conn.model_dump(mode="json")
    data.update({"via": via, "jungle_server": jungle_server if via == "mcpjungle" else None})
    return SourceConnection.model_validate(data)


# --- infra/mcp_servers.yaml ------------------------------------------------------------------------
#: E2E run 2, N-22: the operator's declarations file. `WALNUT_MCP_DECLARATIONS` (or the launcher's
#: older `WALNUT_MCP_SERVERS`, which nothing read before); a relative path is resolved against the
#: `databridge/` root. Default: `<databridge>/infra/mcp_servers.yaml`.
DECLARATIONS_ENV = "WALNUT_MCP_DECLARATIONS"


def declarations_path() -> Path:
    raw = (os.environ.get(DECLARATIONS_ENV) or os.environ.get("WALNUT_MCP_SERVERS") or "").strip()
    if not raw:
        return (paths.DATABRIDGE_ROOT / "infra" / "mcp_servers.yaml").resolve()
    p = Path(raw)
    return p if p.is_absolute() else (paths.DATABRIDGE_ROOT / p).resolve()


def import_declarations(path: Optional[Path] = None, *, route: str = "direct",
                        only: Optional[list[str]] = None, root: Optional[Path] = None,
                        auto_approve: bool = False, register: bool = True,
                        advertise: bool = True, client: Any = None) -> list[SourceConnection]:
    """Turn `infra/mcp_servers.yaml` (version 1) server declarations into connections.

    With `register=True` this IS a registration (Codex f2 #10/#11). The deployment's registration
    policy applies (`registration_mode`: the manifest's interpreters and `${VAR}` references need
    operator mode). Every WRITE connection gets its live tool advert; if any advert fails,
    `RegistrationIncomplete` is raised AFTER every declaration has been processed, carrying the
    saved `connections` and the `failures`.

    `route` picks `direct` (launch the server's stdio command here) or `mcpjungle` (call it through
    the aggregator, where it is registered under its `id`). The declaration's `scope` maps onto
    the connection scope: `include_extensions` → file_types, `max_bytes`; for a `database` source
    `roots` are schemas (→ tables_include `<schema>.*`); for other classes a root of "." means
    "the server's own roots" and is dropped. `${...}` references are kept verbatim — they are
    expanded at connect time, never written out expanded.
    """
    path = path or declarations_path()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ConnectionError_(f"{path}: expected version 1, got {data.get('version')!r}")
    if route not in ("direct", "mcpjungle"):
        raise ConnectionError_(f"route must be direct or mcpjungle, not {route!r}")
    out: list[SourceConnection] = []
    for decl in data.get("servers") or []:
        sid = str(decl.get("id") or "")
        if only and sid not in only:
            continue
        direct = decl.get("direct") or {}
        if direct.get("transport", "stdio") == "stdio":
            transport: dict = {"type": "stdio", "command": direct.get("command", ""),
                               "args": [str(a) for a in direct.get("args") or []],
                               "env": {k: str(v) for k, v in (direct.get("env") or {}).items()}}
        else:
            transport = {"type": "http", "url": direct.get("url", "")}
        raw_scope = decl.get("scope") or {}
        scope: dict = {}
        if raw_scope.get("include_extensions"):
            scope["file_types"] = list(raw_scope["include_extensions"])
        if raw_scope.get("max_bytes"):
            scope["max_bytes"] = int(raw_scope["max_bytes"])
        # C-2: a table row cap is set only when the operator DECLARES one (none = whole tables)
        if raw_scope.get("max_rows_per_table"):
            scope["max_rows_per_table"] = int(raw_scope["max_rows_per_table"])
        if raw_scope.get("table_page_rows"):
            scope["table_page_rows"] = int(raw_scope["table_page_rows"])
        roots = [str(r) for r in raw_scope.get("roots") or []]
        if decl.get("source_class") == "database":
            if roots:
                scope["tables_include"] = [f"{r}.*" for r in roots]
        else:
            roots = [r for r in roots if r not in (".", "./")]
            if roots:
                scope["roots"] = roots
        raw = {
            "id": sid, "title": decl.get("title") or sid, "transport": transport,
            "via": route, "jungle_server": sid if route == "mcpjungle" else None,
            "scope": scope, "auto_approve": auto_approve,
            "purpose": decl.get("purpose") or "ingest",
            "tools_hint": dict(decl.get("tools_hint") or {}),
            "writes_for": ([decl["writes_for"]] if isinstance(decl.get("writes_for"), str)
                           else [str(w) for w in decl.get("writes_for") or []]),
            "aliases": [str(a) for a in decl.get("aliases") or []]}
        # FXI3 (Codex f1 #6): scan the RAW declaration first and validate without echoing
        # inputs, so a refused literal can never come back out in a pydantic error message.
        _refuse_raw(raw)
        conn = _validate_redacted(raw)
        refuse_literal_secrets(conn)
        if register:
            _refuse_by_policy(conn, effective_origin(None), None)
            save_connection(conn, root)
        out.append(conn)
    if register and advertise:
        failures = []
        for c in out:
            err = _advertise_one(c, root, client)
            if err is not None:
                failures.append({"id": c.id, "error": err})
        if failures:
            raise RegistrationIncomplete(
                f"{len(failures)} write connection(s) were saved but their tool advert FAILED: "
                + "; ".join(f"{f['id']}: {f['error']}" for f in failures)
                + " (finish with plans.advertise_write_tools once the server is reachable)",
                connections=out, failures=failures)
    return out


__all__ = ["import_declarations", "expand", "SourceConnection", "StdioTransport", "HttpTransport",
           "Scope", "ConnectionError_", "register_connection", "register_connections",
           "stdio_connection", "http_connection", "with_route", "load_connection",
           "save_connection", "list_connections", "connection_path", "refuse_literal_secrets",
           "registration_refusal", "command_refusal", "destination_refusal",
           "RegistrationRefused", "RegistrationIncomplete", "registration_mode",
           "effective_origin", "REGISTRATION_MODE_ENV", "connection_problems",
           "declarations_path", "DECLARATIONS_ENV"]
