"""``/builder`` — a declaration goes in, the grants it compiles to come out, or a refusal does.

`DEMO_AND_FRONTEND.md` §2.1 and `AGENT_PLANE.md` D13: two panes, one compiler. **The moment that
sells it is the refusal** — every other builder would deploy a declaration with a hole in it and
let it fail in production.

The two panes are honest about very different amounts of build:

**Adapter Builder — real.** `POST /builder/adapter/compile` runs
`databridge_adapter.sources.mcp.load_mcp_declaration` over the submitted YAML. That is the actual
compiler: it screens the declaration, checks the vendored artefact's digest against the pin, and
refuses — by exception class, with the reason — a `trusted` field with no written justification, a
non-empty `capabilities.write`, a registry URL in the launch position, a sandbox claim the
deployment cannot enforce, or a tool declaration carrying concealed Unicode. Nothing in this module
decides any of that; it hands the loader a file and reports what came back.

**Agent Builder — real too, as of `packages/agentdef`.** `POST /builder/agent/compile` runs
`databridge_agentdef.compile_agent` over the submitted YAML, against the pack's own purpose
vocabulary and the estate's adapter catalogue. It is the same D13 mechanism: delete the
`citation:` line from `packs/finance/agents/credit_memo_drafter.yaml` and the compile comes back
`REFUSED / no_citation_rule`, with the compiler's own sentence about why. Every reason is
collected, not just the first.

If that package is not importable (an older checkout, or a path that was never promoted), the
route answers **501 naming what is missing and the endpoint that will serve it** — the shape
`materialise_routes.py` uses — rather than synthesising a refusal. A hand-written one would be the
worse lie available on a surface whose entire pitch is that refusals are computed.

## Containment on the compile route

The submitted YAML names a file to digest (`mcp.vendored_path`). A route that digests any path a
caller names is a file-existence and content oracle, so the path is required to resolve **under
`fixtures/`** before the loader is ever called, and the body is size-capped. The declaration is
written to a temp file and deleted; nothing a caller submits is persisted, and no MCP server is
launched — `load_mcp_declaration` reads and validates, and `compile_mcp_adapter` (which needs a
live transport) is deliberately not reachable from HTTP.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from .auth import install_auth_handler, require_principal
from pydantic import BaseModel, Field

log = logging.getLogger("databridge.gateway.builder")

#: databridge/apps/gateway/databridge_gateway/ -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

#: Where a declaration may point its `vendored_path`. A server is vendored and pinned, never
#: fetched — and never anywhere a caller chooses.
VENDOR_ROOTS: tuple[Path, ...] = (DATABRIDGE_ROOT / "fixtures",)

#: Adapter declarations shipped with this repo, offered as starting points. Discovered, not listed:
#: a pack that adds one appears without a code change.
DECLARATION_GLOBS = ("packs/*/adapters/*.yaml", "fixtures/packs/*/adapters/*.yaml")

#: Agent declarations, discovered the same way. `packs/<pack>/agents/<agent>.yaml` is the location
#: `databridge_agentdef.load_declaration` treats as part of the artefact's identity.
AGENT_GLOBS = ("packs/*/agents/*.yaml",)

#: A declaration is a review artefact, not an upload. 256 KB is far past any real one.
MAX_DECLARATION_BYTES = 256 * 1024

#: What has to exist before an AGENT declaration can be compiled rather than described. Reported
#: only when `databridge_agentdef` cannot be imported — the package that supplies all four.
AGENT_MISSING_COMPONENTS = (
    "an agent declaration format — the schema for purpose, capability grants, actions, citation "
    "rule, abstention threshold and eval binding (AGENT_PLANE.md D13 §5)",
    "the compiler that turns that declaration into capability grants, a sink policy and a budget "
    "admission, and that REFUSES a declaration with no citation rule",
    "a certified abstention floor per golden set — the (mu-alpha)/(1-alpha) bound the refusal "
    "quotes cannot be computed without one",
    "an ActionType registry, so `automation.mode` on a declared action is checkable rather than "
    "advisory",
)

AGENT_NOT_IMPLEMENTED = (
    "the agent-declaration compiler (`databridge_agentdef`) is not importable from this process. "
    "A compile result is a set of decisions — which capabilities are granted, which sinks are "
    "permitted, what the budget admits, which eval binds — and this endpoint will not invent them "
    "so the pane renders. The refusal this surface is famous for has to be COMPUTED; a "
    "hand-written one would be a screenshot of the claim."
)

#: The route that will serve it. Named so the pane can say what has to land.
AGENT_ENDPOINT = "POST /builder/agent/compile"


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def _refusal(code: str, message: str, status: int = 422, **extra) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "nearest_alternative": None}
    body.update(extra)
    return JSONResponse(status_code=status, content={"refusal": body})


class AdapterCompileRequest(BaseModel):
    declaration: str = Field(..., description="the adapter declaration, as YAML")


class AgentCompileRequest(BaseModel):
    declaration: str = Field(..., description="the agent declaration, as YAML")
    #: Where the submitted text came from, recorded on the decision. Editing in a browser is not a
    #: reviewed pack file, and the decision should not claim it was one.
    declared_in: Optional[str] = None


def _find_declarations(globs: tuple[str, ...] = DECLARATION_GLOBS) -> list[dict]:
    found: list[dict] = []
    for pattern in globs:
        for path in sorted(DATABRIDGE_ROOT.glob(pattern)):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:                                      # pragma: no cover - unreadable
                continue
            rel = path.relative_to(DATABRIDGE_ROOT).as_posix()
            found.append({"id": rel, "path": rel, "bytes": len(text.encode("utf-8")),
                          "pack": rel.split("/")[-3] if "/" in rel else ""})
    return found


def _read_declaration(rel: str) -> Optional[str]:
    """Read a shipped declaration by its repo-relative path, with containment."""
    candidate = (DATABRIDGE_ROOT / rel).resolve()
    # `relative_to`, not a string prefix: `packs_evil/` starts with `packs` and is not under it.
    inside = False
    for root in (DATABRIDGE_ROOT / "packs", DATABRIDGE_ROOT / "fixtures"):
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        inside = True
        break
    if not inside:
        return None
    if not candidate.is_file() or candidate.suffix not in (".yaml", ".yml"):
        return None
    return candidate.read_text(encoding="utf-8")


def _vendored_path_is_contained(data: Any) -> tuple[bool, str]:
    """The one check this module makes before handing the loader a file.

    `load_mcp_declaration` digests `mcp.vendored_path`, so a route that accepts an arbitrary one
    is a content oracle over the host filesystem. The loader's own rule is that a server is
    vendored and pinned; this narrows "vendored" to "under `fixtures/`", which is where the
    vendored servers in this repo live.
    """
    if not isinstance(data, dict):
        return False, "the declaration's top level must be a mapping"
    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        # Let the loader produce its own, better refusal for a malformed declaration.
        return True, ""
    raw = mcp.get("vendored_path")
    if raw is None:
        return True, ""
    text = str(raw)
    if "://" in text:
        return False, (f"mcp.vendored_path {text!r} looks like a URL. A server you fetch at "
                       f"runtime is a server you did not review (AGENT_PLANE §10)")
    resolved = (DATABRIDGE_ROOT / text).resolve()
    for root in VENDOR_ROOTS:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return True, ""
    return False, (
        f"mcp.vendored_path {text!r} resolves outside {[str(r.name) for r in VENDOR_ROOTS]}. This "
        f"route digests the file that path names, so it will only digest a vendored server — "
        f"otherwise the compile button is a file oracle over the host")


def _compiled_view(declaration) -> dict:
    """What a passing adapter declaration compiles TO. Read off the compiled object, never guessed.

    The four headings `DEMO_AND_FRONTEND` §2.1 asks the right pane to show, in the adapter's
    vocabulary: what it may read (capability grants), where anything may go (sink/egress policy),
    what the read costs in guarantees (pinning tier + capture), and what binds it (trust class per
    field, with the written justification behind every `trusted`).
    """
    d = declaration
    projection = d.projection
    fields = tuple(getattr(projection, "fields", ()) or ())
    trust = dict(getattr(d, "trust_class", {}) or {})
    justification = dict(getattr(d, "trusted_justification", {}) or {})
    untrusted_note = dict(getattr(d, "untrusted_note", {}) or {})
    redaction = dict(getattr(d, "redaction", {}) or {})
    caps = getattr(d, "capabilities", None)
    read_caps = tuple(getattr(caps, "read", ()) or ()) if caps is not None else ()
    write_caps = tuple(getattr(caps, "write", ()) or ()) if caps is not None else ()
    egress = getattr(d, "egress", None)
    egress_allow = tuple(getattr(egress, "allow", ()) or ()) if egress is not None else ()
    mcp = getattr(d, "mcp", None)

    return {
        "source_id": d.source_id,
        "title": getattr(d, "title", None),
        "estate": getattr(d, "estate", None),
        "grants": {
            "read": list(read_caps),
            "write": list(write_caps),
            "note": ("a closed allowlist of tool names. A tool the server advertises and this list "
                     "omits can never be invoked."),
        },
        "sink_policy": {
            "egress_allow": list(egress_allow),
            "egress_class": getattr(d, "egress_class", None),
            "note": ("empty means the compiled adapter reaches nothing outbound. A non-empty "
                     "allowlist needs an enforcing proxy, and is refused without one."),
        },
        "pinning": {
            "tier": d.pinning_tier,
            "capture": _plain(getattr(d, "capture", None)),
            "note": getattr(d, "pinning_note", None),
            "deletes_detectable": getattr(d, "deletes_detectable", None),
        },
        "projection": {
            "entity": getattr(projection, "entity", None),
            "key": list(getattr(projection, "key", ()) or ()),
            "fields": list(fields),
        },
        "trust": [{
            "field": name,
            "trust": str(trust.get(name, "untrusted")),
            "justification": justification.get(name),
            "note": untrusted_note.get(name),
            "redaction": list(redaction.get(name) or ()),
        } for name in fields],
        "server": {
            "server_ref": getattr(mcp, "server_ref", None) if mcp else None,
            "vendored_path": getattr(mcp, "vendored_path", None) if mcp else None,
            "digest": getattr(mcp, "digest", None) if mcp else None,
            "declaration_digest": getattr(mcp, "declaration_digest", None) if mcp else None,
            "sandbox": getattr(mcp, "sandbox", None) if mcp else None,
            "transport": getattr(mcp, "transport", None) if mcp else None,
        },
        "verification": _plain(getattr(d, "verification", None)),
    }


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def register_builder_routes(app: FastAPI) -> None:
    """Attach the two panes. One compiles; the other says what has to be built first."""
    install_auth_handler(app)

    @app.get("/builder/adapter/declarations", dependencies=_AUTH)
    def list_declarations():
        """The declarations shipped with this repo, plus one to open by default."""
        found = _find_declarations()
        return {"declarations": found, "default": found[0]["id"] if found else None}

    @app.get("/builder/adapter/declaration", dependencies=_AUTH)
    def get_declaration(id: str):
        text = _read_declaration(id)
        if text is None:
            return _refusal("UNKNOWN_DECLARATION",
                            f"no adapter declaration at {id!r} under packs/ or fixtures/",
                            status=404)
        return {"id": id, "declaration": text}

    @app.post("/builder/adapter/compile", dependencies=_AUTH)
    def compile_adapter(req: AdapterCompileRequest):
        """Compile a submitted adapter declaration. The refusal path is the point.

        Returns 200 with `{"compiled": true, ...}` when the loader accepted it, and 200 with
        `{"compiled": false, "refusal": {...}}` when it did not — a refusal is a RESULT of the
        compile, not a transport failure, and giving it a 4xx would make the browser treat the
        most important answer this surface produces as an error.
        """
        source = req.declaration or ""
        if len(source.encode("utf-8")) > MAX_DECLARATION_BYTES:
            return _refusal("DECLARATION_TOO_LARGE",
                            f"a declaration is a review artefact; this one exceeds "
                            f"{MAX_DECLARATION_BYTES} bytes")
        try:
            from databridge_adapter.sources.mcp import load_mcp_declaration
        except ImportError as exc:                               # pragma: no cover - path guard
            return _refusal(
                "ADAPTER_NOT_IMPORTABLE",
                f"the adapter compiler is not importable from this process ({exc}). Add "
                f"'packages/adapter' to the root pyproject `pythonpath` and to "
                f"apps/gateway/serve.py::_PACKAGES together", status=503)

        try:
            parsed = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            return {"compiled": False, "refusal": {
                "code": "YAML_INVALID", "kind": "YAMLError", "message": str(exc),
                "stage": "parse"}}

        contained, why = _vendored_path_is_contained(parsed)
        if not contained:
            return {"compiled": False, "refusal": {
                "code": "VENDORED_PATH_NOT_CONTAINED", "kind": "RegistryResolutionRefused",
                "message": why, "stage": "containment"}}

        tmp = Path(tempfile.mkdtemp(prefix="walnut-builder-")) / "declaration.yaml"
        try:
            tmp.write_text(source, encoding="utf-8")
            declaration = load_mcp_declaration(tmp, repo_root=DATABRIDGE_ROOT)
        except Exception as exc:                                 # noqa: BLE001
            # EVERY loader refusal lands here by design: `McpDeclarationRefused`,
            # `WriteCapabilityRefused`, `ShellCapabilityRefused`, `ConcealedTextRefused`,
            # `RegistryResolutionRefused`, `SandboxNotEnforceable`, `ServerQuarantined`,
            # `IdentityDecisionRefused` and pydantic's own validation errors. The exception CLASS
            # is the refusal's code — it is the compiler's own vocabulary, and restating it as a
            # table here would give the surface a second opinion about why something was refused.
            message = str(exc)
            return {"compiled": False, "refusal": {
                "code": _code_of(exc), "kind": type(exc).__name__, "message": message,
                "stage": "compile"}}
        finally:
            try:
                tmp.unlink(missing_ok=True)
                tmp.parent.rmdir()
            except OSError:                                      # pragma: no cover - best effort
                pass

        return {"compiled": True, "refusal": None, "compiles_to": _compiled_view(declaration)}

    @app.get("/builder/agent/declarations", dependencies=_AUTH)
    def list_agent_declarations():
        """The agent declarations shipped with this repo. Discovered, not listed."""
        found = _find_declarations(AGENT_GLOBS)
        return {"declarations": found, "default": found[0]["id"] if found else None}

    @app.get("/builder/agent/declaration", dependencies=_AUTH)
    def get_agent_declaration(id: str):
        text = _read_declaration(id)
        if text is None:
            return _refusal("UNKNOWN_DECLARATION",
                            f"no agent declaration at {id!r} under packs/", status=404)
        return {"id": id, "declaration": text}

    @app.post("/builder/agent/compile", dependencies=_AUTH)
    def compile_agent_declaration(req: AgentCompileRequest):
        """Compile a submitted agent declaration through `databridge_agentdef.compile_agent`.

        Same contract as the adapter route: a refusal is a 200 with `compiled: false`, because a
        refusal is the RESULT of a compile and not a transport failure. Every reason the compiler
        found travels, not just the first — fixing one and meeting the next as a surprise is how a
        reviewer stops believing the tool.
        """
        source = req.declaration or ""
        if len(source.encode("utf-8")) > MAX_DECLARATION_BYTES:
            return _refusal("DECLARATION_TOO_LARGE",
                            f"a declaration is a review artefact; this one exceeds "
                            f"{MAX_DECLARATION_BYTES} bytes")
        try:
            from databridge_agentdef import compile_agent, load_catalogue, parse_declaration
            from databridge_agentdef.declaration import text_digest
        except ImportError as exc:                               # pragma: no cover - path guard
            # Not built HERE. Named, with the endpoint that will serve it, rather than faked.
            log.info("agent compiler unavailable: %s", exc)
            return JSONResponse(status_code=501, content={"refusal": {
                "code": "AGENT_COMPILER_NOT_IMPLEMENTED",
                "message": AGENT_NOT_IMPLEMENTED,
                "missing": list(AGENT_MISSING_COMPONENTS),
                "endpoint": AGENT_ENDPOINT,
                "nearest_alternative": (
                    "POST /builder/adapter/compile compiles an ADAPTER declaration for real — the "
                    "same D13 mechanism over the object whose compiler is reachable."),
            }})

        try:
            parsed = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            return {"compiled": False, "refusals": [{
                "code": "YAML_INVALID", "detail": str(exc), "structural": True}],
                "stage": "parse", "reasoning": None}

        where = req.declared_in or "<submitted in the Builder>"
        try:
            declaration = parse_declaration(parsed, where=where, declared_in=where,
                                            declaration_digest=text_digest(source))
        except Exception as exc:                                 # noqa: BLE001
            # A shape error, not a governance one. Kept as its own stage so a reader can tell a
            # typo from a decision the compiler made about the agent's authority.
            return {"compiled": False, "refusals": [{
                "code": _code_of(exc), "detail": str(exc), "structural": True}],
                "stage": "parse", "reasoning": None}

        try:
            catalogue = load_catalogue((declaration.pack,))
            decision = compile_agent(declaration, catalogue=catalogue,
                                     repo_root=DATABRIDGE_ROOT)
        except Exception as exc:                                 # noqa: BLE001
            log.exception("agent compile failed")
            return _refusal("AGENT_COMPILE_FAILED",
                            f"the compiler could not reach a decision: {exc}. No compile result "
                            f"is issued — an error is not a refusal, and rendering it as one "
                            f"would attribute a governance decision to a crash", status=503)

        record = decision.as_record()
        return {
            "compiled": decision.outcome.value == "compiled",
            "outcome": decision.outcome.value,
            "stage": "compile",
            "agent": decision.agent,
            "version": decision.version,
            "pack": decision.pack,
            "reasoning": decision.reasoning,
            "refusals": [r.as_record() for r in decision.refusals],
            "declaration_digest": decision.declaration_digest,
            "catalogue_digest": decision.catalogue_digest,
            "vocabulary_packs": list(decision.vocabulary_packs),
            "compiles_to": record.get("compiled"),
        }

    @app.get("/builder/agent/status", dependencies=_AUTH)
    def agent_status():
        """Whether this deployment can compile an agent declaration, and what it needs if not."""
        try:
            import databridge_agentdef                            # noqa: F401
        except ImportError as exc:                                # pragma: no cover - path guard
            return {"available": False, "endpoint": AGENT_ENDPOINT,
                    "missing": list(AGENT_MISSING_COMPONENTS),
                    "note": f"{AGENT_NOT_IMPLEMENTED} ({exc})"}
        return {"available": True, "endpoint": AGENT_ENDPOINT, "missing": [],
                "note": ("compiled by `databridge_agentdef.compile_agent` against the pack's own "
                         "purpose vocabulary and the estate's adapter catalogue. Deterministic: "
                         "same declaration, same catalogue, same answer, with no model in the "
                         "loop.")}


def _code_of(exc: BaseException) -> str:
    """`WriteCapabilityRefused` -> `WRITE_CAPABILITY_REFUSED`. The compiler's own vocabulary."""
    name = type(exc).__name__
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.upper())
    return "".join(out)
