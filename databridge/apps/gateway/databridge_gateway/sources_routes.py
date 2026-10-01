"""`/sources` — connect an MCP server, approve its crawl plan, run it, watch it (BUILD_PLAN §6 U).

    GET  /sources                              every registered connection + plan status + last run
    POST /sources                              register one (connection | command | url | mcpjungle)
                                               or import infra declarations (mode: import)
    GET  /sources/{id}                         connection, tools seen, plan, mapping, last run
    POST /sources/{id}/plan                    propose a crawl plan (connects, lists tools)
    POST /sources/{id}/plan/approve            approve it — approved_by = the signed-in principal
    POST /sources/{id}/mapping/approve         approve the table mapping — same rule
    POST /sources/{id}/overrides               approve / revoke ONE screen override (item or row
                                               + content digest) — same rule
    POST /sources/{id}/runs                    start an ingest in the background → {run_id}
    GET  /sources/{id}/runs                    runs on disk + any in flight
    GET  /sources/{id}/runs/{run_id}           report + stages + live progress + verify
    POST /sources/{id}/runs/{run_id}/verify    re-run reconciliation for one run
    GET  /sources/{id}/health                  plan / last run / failure streak / age

Every route requires `auth.require_principal` (these disclose what a deployment is connected to).
Approval is attributed to the principal, never to a name in the body. After a run finishes —
success or failure — `app.state.reload_graph()` is called so every surface sees the new rows.

**Registration policy (R-019, Codex batch 2 #1/#2).** Registering a ``command`` connection means
"this gateway may launch that program" — host code execution. Authentication proves WHO asks, not
that they may run programs on the host, so over HTTP it is OFF by default:

* a stdio (command) transport is refused unless its executable is on the operator's allow-list,
  ``WALNUT_COMMAND_SOURCES`` (comma-separated), matched by EXACT RESOLVED PATH (a bare name is
  resolved on the gateway's PATH; extensions are kept; case is folded only on Windows). Unset →
  no command source can be registered over HTTP (the CLI and the operator's
  ``infra/mcp_servers.yaml`` import are unaffected). A package runner / interpreter / shell
  (``npx``, ``uvx``, ``python``…) is refused EVEN WHEN LISTED unless the operator also sets
  ``WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK=1``: its arguments choose the code it runs, so
  listing it is listing arbitrary code execution. ``env``/``env_from``/``cwd`` and ``${VAR}``
  references cannot be set over HTTP;
* an http transport that forwards host secrets (``headers_from``) is refused unless its host is
  on ``WALNUT_SOURCE_SECRET_HOSTS``; and every http source's host must resolve only to public
  addresses — private, loopback, link-local and the other non-global ranges are refused unless
  the host is on ``WALNUT_SOURCE_ALLOWED_HOSTS`` (SSRF);
* ``auto_approve`` is refused over HTTP: an approval is made by the signed-in principal on
  ``/plan/approve``, never by a flag in a registration body;
* ``mode: import`` reads only the operator's default declarations file; a body-supplied ``path``
  is refused (a principal must not choose which file on the host is read as declarations);
* approvals are BOUND to the connection revision (a digest of its execution-relevant fields): a
  plan records the revision it was proposed and approved for, a run is admitted only for that
  revision and re-checks it when it starts, and replacement, proposal, approval and admission are
  serialised per source. Replacing a connection whose execution-relevant fields changed
  supersedes its plan (approved or not) and revokes its table-mapping approvals; a connection is
  not replaced while it runs.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import (PLAN_APPROVED_NOTE, PLAN_REVISION_NOTE, PLAN_SUPERSEDED, JobBusy,
                               now_iso, plan_binding, platform_of, reload_graph)

log = logging.getLogger("databridge.gateway.sources")


#: source and run ids name files and directories; no separators, no leading dot.
_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


#: Operator allow-list of executables a command source may launch (comma-separated).
COMMAND_SOURCES_ENV = "WALNUT_COMMAND_SOURCES"
#: Operator acceptance that an allow-listed PACKAGE RUNNER / interpreter may be launched with
#: caller-chosen arguments — which is arbitrary code execution (R-019 round 2, platform #3).
RUNNER_RISK_ENV = "WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK"
#: Operator allow-list of hosts an http source may send host secrets (`headers_from`) to.
SECRET_HOSTS_ENV = "WALNUT_SOURCE_SECRET_HOSTS"
#: Operator allow-list of hosts an http source may reach although they resolve to a private,
#: loopback, link-local or otherwise non-global address (R-019 round 2, platform #5).
ALLOWED_HOSTS_ENV = "WALNUT_SOURCE_ALLOWED_HOSTS"
#: The fields that decide WHAT a connection runs or reads; a change to any of them after a plan
#: was approved revokes that approval (the title is presentation and is not among them).
_EXECUTION_FIELDS = ("transport", "via", "jungle_server", "scope", "purpose", "document_kind",
                     "tools_hint")
#: Executables whose ARGUMENTS choose the code they run: a package runner fetches and executes
#: whatever package the caller names; an interpreter or shell runs whatever script or command the
#: caller passes. Allow-listing one of these by name is allow-listing arbitrary code, so it is
#: refused unless the operator sets `RUNNER_RISK_ENV=1`. This is a SAFETY list, and it cannot be
#: complete: any executable that takes caller-chosen arguments can be abused, which is why the
#: whole feature is off by default and why the risk is reported (R-019 round 2).
_CODE_RUNNERS = frozenset({
    "npx", "pnpx", "bunx", "uvx", "pipx", "uv", "npm", "pnpm", "yarn", "bun", "deno", "node",
    "python", "python3", "py", "pythonw", "ruby", "perl", "php", "java", "dotnet",
    "sh", "bash", "zsh", "dash", "fish", "cmd", "powershell", "pwsh", "wsl", "env",
    "docker", "podman", "npm-cli", "corepack"})
#: The MCPJungle aggregator address the gateway writes itself (operator-controlled by env).
_JUNGLE_DEFAULT_URL = "${MCPJUNGLE_URL:-http://127.0.0.1:8080}"
#: `${VAR}` references are expanded from the HOST environment at connect time.
_ENV_REF = re.compile(r"\$\{")


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in (os.environ.get(name) or "").split(",") if x.strip()]


def _which(cmd: str) -> Optional[str]:
    """PATH lookup (the gateway's PATH, which the operator controls). A seam for tests."""
    return shutil.which(cmd)


def _resolve_exe(cmd: str) -> Optional[str]:
    """The exact executable ``cmd`` names, as an OS-normalised absolute path, or None.

    A command with a path is that path (extension KEPT, so ``agent.exe`` is not ``agent.cmd``);
    a bare name is what the gateway's PATH resolves it to. ``os.path.normcase`` folds case only
    where the filesystem does (Windows) — ``/opt/Tool`` and ``/opt/tool`` stay different on
    POSIX (R-019 round 2, platform #6)."""
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


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def command_policy_refusal(command: str) -> Optional[str]:
    """None when ``command`` may be launched by a source registered over HTTP, else why not.

    Matching is by EXACT RESOLVED PATH: the requested command and every allow-list entry are
    resolved to the executable file they name, and only an identical file matches. An allowed
    executable that is a package runner, interpreter or shell is still refused unless the
    operator explicitly accepted that risk (`RUNNER_RISK_ENV`)."""
    allowed = _env_list(COMMAND_SOURCES_ENV)
    if not allowed:
        return (f"registering a command source over HTTP is disabled on this gateway: it would "
                f"let a signed-in caller launch a program on the host. An operator enables it by "
                f"listing the exact executables in {COMMAND_SOURCES_ENV}.")
    want = _resolve_exe(command)
    if want is None:
        return f"{command!r} does not resolve to an executable on this gateway"
    if not any(_resolve_exe(entry) == want for entry in allowed):
        return (f"{command!r} ({want}) is not an allowed executable for a command source "
                f"({COMMAND_SOURCES_ENV}={','.join(allowed)})")
    if _runner_name(want) in _CODE_RUNNERS and not _truthy(RUNNER_RISK_ENV):
        return (f"{command!r} is a package runner / interpreter: its arguments choose the code "
                f"it runs, so allowing it lets any signed-in caller run arbitrary code on the "
                f"host. An operator who accepts that risk sets {RUNNER_RISK_ENV}=1.")
    return None


def secret_host_refusal(url: str, headers_from: dict) -> Optional[str]:
    """None when an http source may forward host secrets to ``url``'s host, else why not."""
    if not headers_from:
        return None
    host = (urlsplit(url).hostname or "").lower()
    allowed = {h.lower() for h in _env_list(SECRET_HOSTS_ENV)}
    if host and host in allowed:
        return None
    return (f"this source would send host secrets ({sorted(headers_from)}) to {host or url!r}, "
            f"which is not on the operator's {SECRET_HOSTS_ENV} allow-list")


def _resolve_host(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to. A seam for tests (which never touch the network)."""
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _non_global(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (not ip.is_global) or ip.is_multicast


def destination_refusal(url: str) -> Optional[str]:
    """SSRF policy for an http source registered over HTTP (R-019 round 2, platform #5).

    Only http(s); no ``${VAR}`` host-environment reference (it would be expanded into the URL at
    connect time — a secret in a query string); and every address the host resolves to must be
    global — private, loopback, link-local, shared, reserved, unspecified and multicast are
    refused unless the operator lists the host in `ALLOWED_HOSTS_ENV`. Unresolvable → refused
    (a destination that cannot be checked is not allowed)."""
    if _ENV_REF.search(url or ""):
        return ("a URL registered over HTTP may not reference host environment variables "
                "(${...}); the gateway would expand them into the request it sends")
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
        return (f"{host!r} resolves to a non-public address ({', '.join(bad)}): a source may not "
                f"point the gateway at its own network unless the operator lists the host in "
                f"{ALLOWED_HOSTS_ENV}")
    return None


def _env_refs_in(values: Any) -> bool:
    if isinstance(values, dict):
        return any(_env_refs_in(v) for v in values.values())
    if isinstance(values, (list, tuple)):
        return any(_env_refs_in(v) for v in values)
    return isinstance(values, str) and bool(_ENV_REF.search(values))


def registration_refusal(body: "RegisterSource", spec: Optional[dict]) -> Optional[tuple[str, str]]:
    """The HTTP registration policy (module docstring). (code, message) or None."""
    if body.auto_approve or (spec and spec.get("auto_approve")):
        return ("AUTO_APPROVE_NOT_OVER_HTTP",
                "auto_approve cannot be set over HTTP: a plan is approved by the signed-in "
                "principal on POST /sources/{id}/plan/approve")
    if body.mode == "import":
        if body.path is not None:
            return ("IMPORT_PATH_NOT_ALLOWED",
                    "mode 'import' reads only the operator's declarations file; a request may "
                    "not choose which file on the host is read")
        return None
    transport = (spec or {}).get("transport") or {}
    if transport.get("type") == "stdio":
        # What the child runs with is part of what it runs: an `env` (NODE_OPTIONS,
        # PYTHONSTARTUP…) or a `cwd` can make an allowed executable load other code, and
        # `env_from` hands it host secrets. Over HTTP none of them may be chosen by the caller;
        # an operator declares them in the declarations file.
        chosen = [k for k in ("env", "env_from", "cwd") if transport.get(k)]
        if chosen:
            return ("COMMAND_ENV_NOT_OVER_HTTP",
                    f"a command source registered over HTTP may not set {chosen}: they change "
                    f"what the launched program executes or hand it host secrets")
        if _env_refs_in([transport.get("command"), transport.get("args")]):
            return ("ENV_REFERENCE_NOT_OVER_HTTP",
                    "a command source registered over HTTP may not reference host environment "
                    "variables (${...}) in its command or arguments")
        why = command_policy_refusal(str(transport.get("command") or ""))
        if why:
            return ("COMMAND_SOURCES_DISABLED", why)
    elif transport.get("type") == "http":
        why = secret_host_refusal(str(transport.get("url") or ""),
                                  dict(transport.get("headers_from") or {}))
        if why:
            return ("SECRET_HOST_NOT_ALLOWED", why)
        if _env_refs_in(transport.get("headers")):
            return ("ENV_REFERENCE_NOT_OVER_HTTP",
                    "http headers registered over HTTP may not reference host environment "
                    "variables (${...}); declare forwarded secrets with headers_from")
        url = str(transport.get("url") or "")
        if not (spec.get("via") == "mcpjungle" and url == _JUNGLE_DEFAULT_URL):
            why = destination_refusal(url)
            if why:
                return ("SOURCE_DESTINATION_NOT_ALLOWED", why)
    return None


def _execution_view(conn: Any) -> dict:
    data = _dump(conn) or {}
    return {k: data.get(k) for k in _EXECUTION_FIELDS}


def connection_revision(conn: Any) -> str:
    """A digest of everything that decides what a connection runs or reads. An approval is bound
    to it (R-019 round 2, platform #1): approved for revision X runs only against revision X."""
    view = json.dumps(_execution_view(conn), sort_keys=True, default=str)
    return hashlib.sha256(view.encode("utf-8")).hexdigest()[:32]


def plan_stale_reason(plan: Any, revision: str, *, for_run: bool) -> Optional[str]:
    """Why ``plan`` may not be approved (``for_run=False``) or run (``True``) against the
    connection at ``revision`` — or None. Computed from the plan's own binding notes."""
    proposed_for = plan_binding(plan, PLAN_REVISION_NOTE)
    if proposed_for == PLAN_SUPERSEDED:
        return ("the plan was proposed for a connection that has since been replaced; propose "
                "a new plan for the current connection")
    if proposed_for is not None and proposed_for != revision:
        return ("the plan was proposed for a different revision of this connection; propose "
                "again")
    if for_run:
        approved_for = plan_binding(plan, PLAN_APPROVED_NOTE)
        if approved_for is not None and approved_for != revision:
            return ("the plan was approved for a different revision of this connection; it "
                    "must be proposed and approved again")
    return None


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _dump(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return obj


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


class RegisterSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["connection", "command", "url", "mcpjungle", "import"] = "connection"
    #: mode=connection: a full `SourceConnection` declaration.
    connection: Optional[dict] = None
    #: mode=command|url|mcpjungle: the pieces; the scope is the declared scope (§4 — every item
    #: outside it is recorded, never ignored).
    id: Optional[str] = None
    title: str = ""
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_from: dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None
    headers_from: dict[str, str] = Field(default_factory=dict)
    jungle_server: Optional[str] = None
    scope: dict = Field(default_factory=dict)
    auto_approve: bool = False
    replace: bool = False
    #: mode=import: `infra/mcp_servers.yaml` (default) through `route`.
    path: Optional[str] = None
    route: Literal["direct", "mcpjungle"] = "direct"
    only: Optional[list[str]] = None


class ApprovePlan(BaseModel):
    """Optional body of `/plan/approve`. Who approves is ALWAYS the principal — a body name is
    ignored (``extra="ignore"``). ``vouch_read_tools`` is that principal's explicit, per-tool
    confirmation that an unclassified step tool only reads (EF3 / Codex b): the package accepts
    it for unknown tools only, and never for a write."""
    model_config = ConfigDict(extra="ignore")
    vouch_read_tools: list[str] = Field(default_factory=list)


class ApproveMapping(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tables: Optional[list[str]] = None


class OverrideAction(BaseModel):
    """Body of `/sources/{id}/overrides`. An override lifts ONE item (or row ref) with ONE content
    digest out of the screen's REVIEW band — never out of a block. Who decides is always the
    signed-in principal; a body name is ignored."""
    model_config = ConfigDict(extra="ignore")
    action: Literal["approve", "revoke"] = "approve"
    item_id: str
    digest: str
    reason: str


def _connection_spec(req: RegisterSource) -> dict:
    if req.mode == "connection":
        if not req.connection:
            raise ValueError("mode 'connection' needs a 'connection' declaration")
        return dict(req.connection)
    if not req.id:
        raise ValueError(f"mode {req.mode!r} needs an 'id'")
    base: dict = {"id": req.id, "title": req.title or req.id, "scope": dict(req.scope),
                  "auto_approve": req.auto_approve}
    if req.mode == "command":
        if not req.command:
            raise ValueError("mode 'command' needs a 'command'")
        base["transport"] = {"type": "stdio", "command": req.command, "args": list(req.args),
                             "env": dict(req.env), "env_from": dict(req.env_from)}
    elif req.mode == "url":
        if not req.url:
            raise ValueError("mode 'url' needs a 'url'")
        base["transport"] = {"type": "http", "url": req.url,
                             "headers_from": dict(req.headers_from)}
    else:                                                             # mcpjungle
        base["via"] = "mcpjungle"
        base["jungle_server"] = req.jungle_server or req.id
        # The MCPJungle route calls the aggregator's HTTP API (`MCPJUNGLE_URL`); the transport
        # is the aggregator's address, expanded at connect time, never stored expanded.
        base["transport"] = {"type": "http",
                             "url": req.url or "${MCPJUNGLE_URL:-http://127.0.0.1:8080}"}
    return base


def _plan_or_none(ingest: Any, source_id: str) -> Optional[Any]:
    try:
        return ingest.load_plan(source_id)
    except Exception:                                                 # noqa: BLE001
        return None


def _mapping_or_none(ingest: Any, source_id: str) -> Optional[Any]:
    try:
        return ingest.load_mapping(source_id)
    except Exception:                                                 # noqa: BLE001
        return None


def _runs(ingest: Any, source_id: str) -> list[dict]:
    try:
        return list(ingest.list_runs(source_id))
    except Exception:                                                 # noqa: BLE001
        return []


def _summary(app: FastAPI, conn: Any) -> dict:
    rt = platform_of(app)
    sid = conn.id
    plan = _plan_or_none(rt.ingest, sid)
    runs = _runs(rt.ingest, sid)
    live = rt.jobs.busy("ingest", sid)
    transport = getattr(conn, "transport", None)
    return {"id": sid, "title": getattr(conn, "title", "") or sid,
            "via": getattr(conn, "via", "direct"),
            "transport": getattr(transport, "type", None),
            "plan_status": getattr(plan, "status", None),
            "last_run": runs[-1] if runs else None,
            "running": ({"run_id": live["run_id"], "progress": live["progress"]} if live else None)}


def _health(app: FastAPI, source_id: str) -> dict:
    rt = platform_of(app)
    plan = _plan_or_none(rt.ingest, source_id)
    runs = [r for r in _runs(rt.ingest, source_id) if r.get("status") != "incomplete"]
    consecutive = 0
    for r in reversed(runs):
        if r.get("status") == "failed":
            consecutive += 1
        else:
            break
    # Every SUCCESSFUL status counts (R-019 round 2, platform #8): a valid run that found nothing
    # (`empty`) is a sync, not "never synced". The same allow-list the job status uses.
    last_ok = next((r for r in reversed(runs) if r.get("status") in _RUN_SUCCESS), None)
    issues: list[str] = []
    if plan is None:
        issues.append("no crawl plan — propose one")
    elif plan.status != "approved":
        issues.append(f"the crawl plan is {plan.status!r}; approve it before it can run")
    if getattr(plan, "problems", None):
        issues.extend(f"plan problem: {p}" for p in plan.problems)
    if not runs:
        issues.append("never run")
    elif last_ok is None:
        issues.append("no successful run yet")
    if consecutive:
        issues.append(f"the last {consecutive} run(s) failed")
    live = rt.jobs.busy("ingest", source_id)
    if live:
        overall = "running"
    elif consecutive:
        overall = "failing"
    elif plan is None or plan.status != "approved":
        overall = "awaiting_approval"
    elif last_ok is None:
        overall = "never_synced"
    else:
        overall = "healthy"
    return {"source_id": source_id, "overall": overall,
            "plan_status": getattr(plan, "status", None),
            "last_run": runs[-1] if runs else None,
            "last_success": last_ok, "consecutive_failures": consecutive,
            "running": ({"run_id": live["run_id"], "progress": live["progress"]} if live else None),
            "issues": issues, "generated_at": now_iso()}


#: Report statuses that are a SUCCESSFUL run (§5.2: ok | empty | failed | unverified). An
#: allow-list: anything else — including a status this code has never seen, or none — is not
#: reported as "completed" (R-019 platform #7).
_RUN_SUCCESS = {"ok", "empty"}


def job_status_for_run(status: Optional[str]) -> str:
    if status in _RUN_SUCCESS:
        return "completed"
    if status == "unverified":
        return "unverified"
    return "failed"


def admission_refusal_at_start(rt: Any, source_id: str, pinned: Optional[str]) -> Optional[str]:
    """Re-check, when the job STARTS, that the connection is still the revision the run was
    admitted for and its plan is still approved for it (R-019 round 2, platform #1)."""
    with rt.source_lock(source_id):
        try:
            conn = rt.ingest.load_connection(source_id)
            plan = rt.ingest.load_plan(source_id)
        except Exception as exc:                                      # noqa: BLE001
            return f"the connection or its plan could not be re-read: {exc}"
        rev = connection_revision(conn)
        if pinned is not None and rev != pinned:
            return ("the connection changed after this run was admitted; it runs only against "
                    "the connection its plan was approved for")
        if getattr(plan, "status", None) != "approved":
            return f"the crawl plan is no longer approved ({getattr(plan, 'status', None)!r})"
        return plan_stale_reason(plan, rev, for_run=True)


def _ingest_job(app: FastAPI, source_id: str, run_id: str, job_id: str,
                pinned_revision: Optional[str] = None) -> None:
    """The background ingest. Progress events land on the job; the run's own artefacts (§5.2)
    are the durable record. The graph is reloaded however the run ends."""
    rt = platform_of(app)
    try:
        refused = admission_refusal_at_start(rt, source_id, pinned_revision)
        if refused:
            log.warning("ingest %s/%s refused at start: %s", source_id, run_id, refused)
            rt.jobs.finish(job_id, status="refused", error=refused)
            return
        report = rt.ingest.run_ingest(
            source_id, store=getattr(app.state, "graph_store", None), harness=rt.harness,
            progress_cb=lambda ev: rt.jobs.progress(job_id, ev), run_id=run_id)
        data = _dump(report) or {}
        status = data.get("status") if isinstance(data, dict) else None
        rt.jobs.finish(job_id, status=job_status_for_run(status), result=data, run_status=status)
    except Exception as exc:                                          # noqa: BLE001
        log.exception("ingest %s/%s failed", source_id, run_id)
        rt.jobs.finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        rt.jobs.update(job_id, graph_counts=reload_graph(app))


def register_sources_routes(app: FastAPI) -> None:
    install_auth_handler(app)

    def _need_ingest():
        rt = platform_of(app)
        if rt.ingest is None:
            return None, _refusal("INGEST_UNAVAILABLE",
                                  "databridge_mcp_ingest is not importable in this gateway: "
                                  + "; ".join(rt.problems), status=503)
        return rt, None

    def _need_connection(rt, source_id: str):
        if not _ID_RE.match(source_id or ""):
            return None, _refusal("BAD_SOURCE_ID", f"{source_id!r} is not a source id",
                                  status=400)
        try:
            return rt.ingest.load_connection(source_id), None
        except Exception as exc:                                      # noqa: BLE001
            return None, _refusal("UNKNOWN_SOURCE", str(exc), status=404)

    @app.get("/sources")
    def sources_list(_p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        out, problems = [], []
        try:
            conns = rt.ingest.list_connections()
        except Exception as exc:                                      # noqa: BLE001
            return _refusal("SOURCES_UNREADABLE", str(exc), status=500)
        for c in conns:
            try:
                out.append(_summary(app, c))
            except Exception as exc:                                  # noqa: BLE001
                problems.append(f"{getattr(c, 'id', '?')}: {exc}")
        # A stored connection the package REFUSED to load (a defaulted credential, a literal
        # secret, a malformed field) is never listed; it is shown here as invalid, with its
        # reason and never its value (E2E run 2 N-10; R-022 sources cross-track 6).
        report = getattr(rt.ingest, "connection_problems", None)
        try:
            refused = list(report() or []) if callable(report) else []
        except Exception as exc:                                      # noqa: BLE001
            refused = [{"id": "?", "error": f"the refused-connection report failed: {exc}"}]
        problems.extend(f"{p.get('id', '?')}: refused at load: {p.get('error', '')}"
                        for p in refused)
        return {"sources": out, "problems": problems}

    @app.post("/sources")
    def sources_register(body: RegisterSource = Body(...),
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        revoked: list[str] = []
        try:
            spec = None if body.mode == "import" else _connection_spec(body)
        except ValueError as exc:
            return _refusal("BAD_CONNECTION", str(exc))
        refused = registration_refusal(body, spec)
        if refused:
            log.warning("sources: %s registration refused: %s", principal.principal_id,
                        refused[0])
            return _refusal(refused[0], refused[1], status=403)
        mapping_revoked: dict[str, list[str]] = {}
        try:
            if body.mode == "import":
                conns = rt.ingest.import_declarations(None, route=body.route, only=body.only,
                                                      auto_approve=False)
            else:
                sid = str(spec.get("id") or "")
                # Serialised with plan proposal/approval and run admission for this source
                # (R-019 round 2, platform #1), so no approval is checked against one connection
                # and then run against its replacement.
                with rt.source_lock(sid):
                    previous = None
                    if body.replace and _ID_RE.match(sid):
                        try:
                            previous = rt.ingest.load_connection(sid)
                        except Exception:                             # noqa: BLE001
                            previous = None
                    if previous is not None and rt.jobs.busy("ingest", sid):
                        return _refusal("RUN_IN_PROGRESS",
                                        f"{sid!r} is running an ingest; a connection is not "
                                        f"replaced under a run that was approved for it",
                                        status=409)
                    conns = [rt.ingest.register_connection(spec, overwrite=body.replace)]
                    if previous is not None and \
                            _execution_view(previous) != _execution_view(conns[0]):
                        why = ("the connection was replaced; its approvals covered the previous "
                               "transport/scope")
                        # The plan is superseded whether it was approved or only proposed: a plan
                        # proposed for the old connection is not approvable for the new one.
                        if rt.ingest.revoke_plan_approval(conns[0].id, by=principal.principal_id,
                                                          reason=why):
                            revoked.append(conns[0].id)
                        # ...and so is every approved table mapping (R-019 round 2, platform #2).
                        tables = rt.ingest.revoke_mapping_approval(
                            conns[0].id, by=principal.principal_id, reason=why)
                        if tables:
                            mapping_revoked[conns[0].id] = tables
        except Exception as exc:                                      # noqa: BLE001
            code = "SOURCE_EXISTS" if "already exists" in str(exc) else "BAD_CONNECTION"
            return _refusal(code, str(exc), status=409 if code == "SOURCE_EXISTS" else 422)
        log.info("sources: %s registered %s", principal.principal_id,
                 [c.id for c in conns])
        # A write connection is never crawled, so no plan proposal ever lists its tools: its
        # live tool list is advertised NOW, where the automation catalogue reads it (E2E F-08,
        # EF3). Outside the source lock (it connects to the server); a failure is reported, not
        # fatal — the connection is registered either way.
        adverts: dict[str, dict] = {}
        for conn in conns:
            if getattr(conn, "purpose", "ingest") != "agent_write":
                continue
            try:
                path = rt.ingest.write_tool_advert(conn.id)
                adverts[conn.id] = {"written": True, "path": str(path)}
            except Exception as exc:                                  # noqa: BLE001
                log.warning("sources: tool advert for %s failed: %s", conn.id, exc)
                adverts[conn.id] = {"written": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"registered": [_dump(c) for c in conns], "by": principal.principal_id,
                "plan_approval_revoked": revoked, "mapping_approval_revoked": mapping_revoked,
                "tool_advert": adverts}

    @app.get("/sources/{source_id}")
    def sources_get(source_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        conn, err = _need_connection(rt, source_id)
        if err:
            return err
        plan = _plan_or_none(rt.ingest, source_id)
        runs = _runs(rt.ingest, source_id)
        return {"connection": _dump(conn),
                "tools": list(getattr(plan, "tools_seen", []) or []),
                "tools_note": (None if plan is not None else
                               "tools are listed when a crawl plan is proposed"),
                "plan": _dump(plan), "mapping": _dump(_mapping_or_none(rt.ingest, source_id)),
                "last_run": runs[-1] if runs else None, "runs": len(runs),
                "running": rt.jobs.busy("ingest", source_id)}

    @app.post("/sources/{source_id}/plan")
    def sources_plan(source_id: str,
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        with rt.source_lock(source_id):
            conn, err = _need_connection(rt, source_id)
            if err:
                return err
            try:
                rt.ingest.propose_plan(source_id, harness=rt.harness)
                # Bound to the connection revision it was proposed FOR (R-019 round 2, #1).
                plan = rt.ingest.stamp_plan(source_id, PLAN_REVISION_NOTE,
                                            connection_revision(conn))
            except Exception as exc:                                  # noqa: BLE001
                return _refusal("PLAN_FAILED", f"{type(exc).__name__}: {exc}", status=502)
        return {"plan": _dump(plan), "proposed_by": principal.principal_id}

    @app.post("/sources/{source_id}/plan/approve")
    def sources_plan_approve(source_id: str, body: Optional[ApprovePlan] = Body(None),
                             principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        with rt.source_lock(source_id):
            conn, err = _need_connection(rt, source_id)
            if err:
                return err
            rev = connection_revision(conn)
            current = _plan_or_none(rt.ingest, source_id)
            stale = plan_stale_reason(current, rev, for_run=False) if current is not None else None
            if stale:
                return _refusal("PLAN_STALE", stale, status=409)
            try:
                # The principal OBJECT, never its id: mcp_ingest refuses a string (INT3).
                rt.ingest.approve_plan(source_id, principal=principal,
                                       vouch_read_tools=(body.vouch_read_tools if body else []))
                # The approval covers THIS revision of the connection, and only it (#1).
                plan = rt.ingest.stamp_plan(source_id, PLAN_APPROVED_NOTE, rev)
            except Exception as exc:                                  # noqa: BLE001
                return _refusal("PLAN_NOT_APPROVABLE", str(exc), status=409)
        return {"plan": _dump(plan)}

    @app.post("/sources/{source_id}/mapping/approve")
    def sources_mapping_approve(source_id: str, body: Optional[ApproveMapping] = Body(None),
                                principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        try:
            mapping = rt.ingest.approve_mapping(source_id, principal=principal,
                                                tables=(body.tables if body else None))
        except Exception as exc:                                      # noqa: BLE001
            return _refusal("MAPPING_NOT_APPROVABLE", str(exc), status=409)
        return {"mapping": _dump(mapping)}

    @app.post("/sources/{source_id}/overrides")
    def sources_override(source_id: str, body: OverrideAction,
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Approve or revoke one screen override (R-022 build cross-track 4). The package checks
        the principal, the digest's shape and the reason, and appends to the append-only
        override log; nothing here widens what it accepts."""
        rt, err = _need_ingest()
        if err:
            return err
        _, err = _need_connection(rt, source_id)
        if err:
            return err
        act = rt.ingest.approve_override if body.action == "approve" else rt.ingest.revoke_override
        try:
            rec = act(source_id, body.item_id, digest=body.digest, principal=principal,
                      reason=body.reason)
        except Exception as exc:                                      # noqa: BLE001
            return _refusal("OVERRIDE_REFUSED", str(exc), status=409)
        return {"override": rec}

    @app.post("/sources/{source_id}/runs", status_code=202)
    def sources_run(source_id: str, background: BackgroundTasks,
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        with rt.source_lock(source_id):
            conn, err = _need_connection(rt, source_id)
            if err:
                return err
            plan = _plan_or_none(rt.ingest, source_id)
            if plan is None or getattr(plan, "status", None) != "approved":
                return _refusal("PLAN_NOT_APPROVED",
                                f"the crawl plan for {source_id!r} is "
                                f"{getattr(plan, 'status', 'missing')!r}; propose and approve it "
                                f"before running (P5)", status=409)
            rev = connection_revision(conn)
            stale = plan_stale_reason(plan, rev, for_run=True)
            if stale:
                return _refusal("PLAN_STALE", stale, status=409)
            run_id = rt.ingest.new_run_id()
            try:
                # Admitted and marked busy under the source lock, so a replacement cannot slip
                # in between: `sources_register` refuses to replace a connection while it runs.
                job = rt.jobs.start("ingest", source_id, by=principal.principal_id,
                                    run_id=run_id, source_id=source_id,
                                    connection_revision=rev)
            except JobBusy as busy:
                return _refusal("RUN_IN_PROGRESS", str(busy), status=409)
        background.add_task(_ingest_job, app, source_id, run_id, job["job_id"], rev)
        return {"run_id": run_id, "job_id": job["job_id"], "status": "running",
                "started_by": principal.principal_id}

    @app.get("/sources/{source_id}/runs")
    def sources_runs(source_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        if not _ID_RE.match(source_id or ""):
            return _refusal("BAD_SOURCE_ID", f"{source_id!r} is not a source id", status=400)
        on_disk = _runs(rt.ingest, source_id)
        seen = {r.get("run_id") for r in on_disk}
        live = [{"run_id": j["run_id"], "status": j["status"], "progress": j["progress"],
                 "started": j["started"]}
                for j in rt.jobs.list("ingest", source_id)
                if j["status"] == "running" and j.get("run_id") not in seen]
        return {"source_id": source_id, "runs": on_disk, "in_flight": live}

    @app.get("/sources/{source_id}/runs/{run_id}")
    def sources_run_get(source_id: str, run_id: str,
                        _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        if not _ID_RE.match(run_id or "") or not _ID_RE.match(source_id or ""):
            return _refusal("BAD_RUN_ID", f"{source_id!r}/{run_id!r} is not a run id",
                            status=400)
        job = next((j for j in rt.jobs.list("ingest", source_id) if j.get("run_id") == run_id),
                   None)
        d = rt.ingest.run_dir(source_id, run_id)
        if job is None and not Path(d).is_dir():
            return _refusal("UNKNOWN_RUN", f"no run {run_id!r} for source {source_id!r}",
                            status=404)
        report = _read_json(Path(d) / "report.json")
        return {"source_id": source_id, "run_id": run_id,
                "status": (job["status"] if job and job["status"] == "running"
                           else (report or {}).get("status") or (job or {}).get("status")),
                "report": report,
                "stages": _read_json(Path(d) / "stages.json"),
                "checkpoint": _read_json(Path(d) / "checkpoint.json"),
                "verify": _read_json(Path(d) / "verify.json"),
                "progress": (job or {}).get("progress"),
                "events": (job or {}).get("events", []),
                "error": (job or {}).get("error"),
                "graph_counts": (job or {}).get("graph_counts"),
                "started_by": (job or {}).get("by")}

    @app.post("/sources/{source_id}/runs/{run_id}/verify")
    def sources_run_verify(source_id: str, run_id: str,
                           _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        if not _ID_RE.match(run_id or "") or not _ID_RE.match(source_id or ""):
            return _refusal("BAD_RUN_ID", f"{source_id!r}/{run_id!r} is not a run id",
                            status=400)
        if not Path(rt.ingest.run_dir(source_id, run_id)).is_dir():
            return _refusal("UNKNOWN_RUN", f"no run {run_id!r} for source {source_id!r}",
                            status=404)
        try:
            return rt.ingest.verify_run(source_id, run_id,
                                        store=getattr(app.state, "graph_store", None))
        except Exception as exc:                                      # noqa: BLE001
            return _refusal("VERIFY_FAILED", f"{type(exc).__name__}: {exc}", status=500)

    @app.get("/sources/{source_id}/health")
    def sources_health(source_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _need_ingest()
        if err:
            return err
        _, err = _need_connection(rt, source_id)
        if err:
            return err
        return _health(app, source_id)


__all__ = ["register_sources_routes", "RegisterSource", "COMMAND_SOURCES_ENV",
           "SECRET_HOSTS_ENV", "ALLOWED_HOSTS_ENV", "RUNNER_RISK_ENV", "command_policy_refusal",
           "secret_host_refusal", "destination_refusal", "registration_refusal",
           "job_status_for_run", "connection_revision", "plan_stale_reason"]
