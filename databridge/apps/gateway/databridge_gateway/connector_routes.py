"""``/connectors`` — configuring a source, before it is an adapter.

**The distinction this module rests on.** A *connector* is what an operator configures: which MCP
server, read or write, which adapter-agent skills, and any extraction guidance specific to that
source. An *adapter* is what the engine compiles that configuration into — a governed reader with
a five-verb port, per-field trust, and a policy screen. The operator should never have to think
about the second one. `/builder` compiles a hand-edited declaration; this surface is the
configure-by-search flow that AUTHORS one, the way the founder described it: search for the
server, say read or write, attach skills, add source-specific extraction context — and the fourth
step's output is exactly the first step's compiler input.

**Servers are vendored and pinned, never resolved at runtime.** The catalogue this surface
searches is a reviewed, local set with digests, defined in ``CATALOGUE`` below. That is not a
limitation to apologise for: a server fetched at runtime is a server nobody reviewed, and
published MCP servers are measurably unstable — 41.6% of audited public servers vanish within
three days (arXiv 2608.00150). An entry with no reviewed artefact yet is listed honestly as
``not_yet_vendored`` rather than hidden or half-offered; searching a curated catalogue is the same
ease-of-use as a public registry with the supply-chain hole closed.

**This module never accepts a filesystem path from a caller.** `/builder` has to (an operator
pastes a whole declaration, including ``mcp.vendored_path``), and it earns a containment check for
that. Here the operator only ever picks a ``server_id`` out of ``CATALOGUE`` — every path that
reaches disk comes from this module's own trusted table, never from the request body. That is a
structural difference from `/builder`, not a smaller version of the same check.

**Read and write are not symmetric, and the UI must not pretend they are.** Adapters cannot write
today — `test_no_adapter_verb_can_write_anything` enforces it at the port. Choosing "write" here
builds a declaration whose ``capabilities.write`` names exactly the tools the operator would have
read from, and hands it to the SAME loader `/builder` uses. The loader's own
``WriteCapabilityRefused`` — with its own reasoning about `ActionType`, `automation.mode` and a
reversal path — is the refusal that comes back. Nothing in this module writes that sentence; it
only asks the question honestly enough that the compiler's real answer is the one that ships.

**Skills and extraction context cannot widen a capability.** `packages/adapter/databridge_adapter/
taint.py` names `prompt.instruction` as a TRUSTED SINK: anything a model may *follow* rather than
*quote*. There is no Skill runtime registry in this build (`GOA Submission/11_SKILL_CATALOGUE.md`
§19 describes one; it is not implemented here), so a Skill selection and the extraction-context
free text are recorded as an inert, underscore-prefixed block on the generated declaration —
``_connector`` — which `load_mcp_declaration` strips *before* validating the document
(``data = {k: v for k, v in data.items() if not str(k).startswith("_")}``, `sources/mcp.py`).
That is not a workaround; it is the same mechanism the loader already uses for its own review
comments, applied so that operator prose can be carried on the artefact for a human or a future
Skill runtime to read, while being structurally incapable of reaching `CapabilitiesDecl`,
`trust_class`, `projection` or any other field the compiler actually enforces. Widening what an
adapter may read still requires editing the reviewed template and re-compiling it — never typing
a sentence into this form.
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from .auth import install_auth_handler, require_principal
from pydantic import BaseModel, Field

from .builder_routes import _code_of, _compiled_view

log = logging.getLogger("databridge.gateway.connectors")

#: databridge/apps/gateway/databridge_gateway/ -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

#: Operator prose is configuration, not a document. A few paragraphs, not an upload.
MAX_CONTEXT_CHARS = 4_000

# =====================================================================================
# the catalogue — vendored and pinned, or honestly not
# =====================================================================================
#
# Searched, never resolved at runtime. Every `template` and `vendored_path` is a path a reviewed,
# committed `packs/<pack>/connectors.yaml` chose, not a caller — the request body never carries
# one, so there is no file-existence oracle to guard against here the way `/builder` must guard one.
#
# Two states, both honest:
#   "vendored"          — a reviewed artefact under fixtures/mcp/, pinned by a declaration under
#                          fixtures/packs/<pack>/adapters/. Selectable, configurable, compilable.
#   "not_yet_vendored"  — real integration, no reviewed artefact in THIS repo yet. Searchable, so
#                          an operator can see what is coming, but the compile step refuses with
#                          the reason rather than pretending to configure a server nobody vetted.
#: The file each pack ships, beside its ``ontology.yaml``, listing its servers (HC1, 2026-10-01).
CATALOGUE_FILENAME = "connectors.yaml"
#: The pack whose entries belong to no vertical — they are served with ``pack: None``.
NEUTRAL_CATALOGUE_PACK = "common"

_ENTRY_KEYS = frozenset({"id", "name", "vendor", "summary", "reads", "status", "vendored_path",
                         "template", "not_vendored_reason", "tags"})


class CatalogueError(RuntimeError):
    """A pack's ``connectors.yaml`` is malformed. Fail at import; never serve half a catalogue."""


def load_catalogue(packs_dir: Optional[Path] = None) -> tuple[dict[str, Any], ...]:
    """Every pack's catalogue entries, read from ``packs/<pack>/connectors.yaml``.

    HC1 (2026-10-01, SWEEP hardcode audit #8). This was a Python literal in this module, including
    a government-pack entry — one vertical's data compiled into the gateway. Each pack now lists
    its own servers; the general ones live in ``packs/common``. An entry's ``pack`` is the
    directory it was read from (``common`` → ``None``), never a value the file declares, so an
    entry cannot claim to belong to a pack it does not ship with. Every pack on disk is read, as
    the literal listed every entry: what is *configurable* is still decided by ``status`` and by
    the compiler, not by the binding.
    """
    root = Path(packs_dir) if packs_dir is not None else DATABRIDGE_ROOT / "packs"
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(root.glob(f"*/{CATALOGUE_FILENAME}")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = data.get("connectors") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise CatalogueError(f"{path}: expected a top-level `connectors` list")
        pack = path.parent.name
        for row in rows:
            if not isinstance(row, dict):
                raise CatalogueError(f"{path}: every connector must be a mapping")
            unknown = sorted(set(row) - _ENTRY_KEYS)
            if unknown:
                raise CatalogueError(f"{path}: {row.get('id')!r} carries unknown key(s) {unknown}"
                                     f" (`pack` is the directory, never declared)")
            cid = str(row.get("id") or "")
            if not cid or cid in seen:
                raise CatalogueError(f"{path}: connector id {cid!r} is missing or duplicated")
            status = row.get("status")
            if status == "vendored":
                if not (row.get("vendored_path") and row.get("template")):
                    raise CatalogueError(f"{path}: {cid} is vendored but names no vendored_path "
                                         f"and template")
            elif status == "not_yet_vendored":
                if not row.get("not_vendored_reason"):
                    raise CatalogueError(f"{path}: {cid} is not vendored and says no reason why")
            else:
                raise CatalogueError(f"{path}: {cid} has unknown status {status!r}")
            seen.add(cid)
            entries.append({**row, "pack": None if pack == NEUTRAL_CATALOGUE_PACK else pack})
    return tuple(entries)


CATALOGUE: tuple[dict[str, Any], ...] = load_catalogue()

_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in CATALOGUE}


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def _template_info(entry: dict[str, Any]) -> dict[str, Any]:
    """Which tools this server actually exposes, and how it launches — read from the REVIEWED
    template once at import time, never hand-duplicated in ``CATALOGUE`` where it could drift
    from the file the compiler actually reads. This is what the MCP config modal shows for
    "which tools are exposed" and "command/args": real values off the compiled artefact, not a
    second, hand-typed account of it.

    A ``not_yet_vendored`` entry has no template to read, so this is honestly empty rather than
    guessed.
    """
    empty = {"tools_exposed": [], "transport": None, "sandbox": None, "digest": None,
              "declaration_digest": None}
    if entry["status"] != "vendored":
        return empty
    try:
        raw = yaml.safe_load((DATABRIDGE_ROOT / entry["template"]).read_text(encoding="utf-8"))
    except OSError:                                               # pragma: no cover - fixture guard
        return empty
    if not isinstance(raw, dict):                                 # pragma: no cover - fixture guard
        return empty
    caps = raw.get("capabilities") or {}
    mcp = raw.get("mcp") or {}
    return {
        "tools_exposed": list(caps.get("read") or ()),
        "transport": mcp.get("transport"),
        "sandbox": mcp.get("sandbox"),
        "digest": mcp.get("digest"),
        "declaration_digest": mcp.get("declaration_digest"),
    }


#: Computed once, at import time, from the same reviewed templates ``compile_connector`` loads —
#: not maintained by hand in ``CATALOGUE`` above.
_TEMPLATE_INFO: dict[str, dict[str, Any]] = {e["id"]: _template_info(e) for e in CATALOGUE}


def _catalogue_view(entry: dict[str, Any]) -> dict[str, Any]:
    """What the search surface may see. Excludes nothing — the catalogue IS the trusted table,
    unlike a declaration a caller pasted, so there is no reason to hide a path here. Merges in
    the real, template-derived fields the MCP config modal needs."""
    view = dict(entry)
    view.update(_TEMPLATE_INFO.get(entry["id"], {}))
    return view


# =====================================================================================
# the Skill vocabulary (GOA Submission/11_SKILL_CATALOGUE.md, WS-01..WS-14)
# =====================================================================================
#
# There is no Skill runtime registry in this build. This table is reference vocabulary — names
# and purposes taken from the catalogue document — not a claim that selecting one here activates
# anything. A selection is recorded on the generated declaration's `_connector.skills` block,
# which the loader strips before validating (see module docstring); it can never grant a
# capability the compiled adapter does not already have.
SKILLS: tuple[dict[str, str], ...] = (
    {"id": "WS-01", "skill": "source.describe",
     "purpose": "What a source can prove before any read: entities, fields, trust per field, "
                "pinning tier, egress class, reachable verdicts."},
    {"id": "WS-02", "skill": "source.read",
     "purpose": "Pinned snapshot acquisition and governed projection read — the only way data "
                "enters the system."},
    {"id": "WS-03", "skill": "pointer.resolve",
     "purpose": "Turns a stored pointer back into a value, under policy, with the audit row "
                "written before the value is returned."},
    {"id": "WS-04", "skill": "pointer.verify",
     "purpose": "Answers whether a citation is still good: verified, drifted, expired, vanished, "
                "or unverifiable — never a bare boolean."},
    {"id": "WS-05", "skill": "capture.put",
     "purpose": "Content-addressed capture-on-read, so a T3 citation from this source stays "
                "checkable after the tool call ends."},
    {"id": "WS-06", "skill": "policy.compile",
     "purpose": "Compiles a role model into a frozen decision table and admits a specific "
                "principal before a read begins."},
    {"id": "WS-07", "skill": "entity.candidates",
     "purpose": "Offers ranked identity candidates with evidence for and against. Never decides "
                "who anyone is — a named human always selects."},
    {"id": "WS-08", "skill": "ontology.propose",
     "purpose": "Proposes a new node or edge kind when this source's relations outrun the "
                "vocabulary; a deterministic evaluator disposes."},
    {"id": "WS-09", "skill": "answer.compose",
     "purpose": "Turns resolved pointers into a readable answer, one citation per printed value."},
    {"id": "WS-10", "skill": "claim.check",
     "purpose": "Flags generated prose that asserts more than the facts this source actually "
                "supplied support."},
    {"id": "WS-11", "skill": "plan.author",
     "purpose": "Turns a data request into a materialisation plan against this source's "
                "catalogue — the one place a model reasons semantically."},
    {"id": "WS-12", "skill": "plan.compile",
     "purpose": "Accepts or refuses a materialisation plan against eight structural invariants. "
                "No model, ever."},
    {"id": "WS-13", "skill": "dataset.materialise",
     "purpose": "Builds the requested dataset shape from this source, with a receipt and "
                "Croissant 1.1 metadata."},
    {"id": "WS-14", "skill": "ledger.append",
     "purpose": "Appends the evidence entry for anything resolved from this source. "
                "Append-only; corrections are new rows."},
)

_SKILL_IDS: frozenset[str] = frozenset(s["id"] for s in SKILLS)


# =====================================================================================
# the MCP config modal — env/credential profile, NAMES only
# =====================================================================================
#
# `McpAdapterDeclaration.mcp.env` (`sources/mcp.py`, `McpServerRef.env: Mapping[str, str]`) is a
# REAL, validated field — the environment the launched subprocess sees, constructed explicitly so
# the parent's own secrets are never inherited. That makes it the honest place for "env or
# credential profile" from the founder's spec: an operator DECLARES which named variables this
# connector instance expects (e.g. `AIRTABLE_API_KEY`), and this form never asks for a value. A
# value is a deployment secret filled in by whatever supplies `compile_mcp_adapter`'s own `env=`
# argument at connect time, never typed into a browser and never persisted by this route.
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_ENV_NAMES = 20

#: An adapter agent's declared configuration (Shell §7 below) is recorded, not wired — there is no
#: reachable path from HTTP to a live `AdapterShell`. Bounded the same way `extraction_context` is.
MAX_NAMED_QUERIES_CHARS = 2_000


# =====================================================================================
# request/response shapes
# =====================================================================================
class ComposeRequest(BaseModel):
    server_id: str
    mode: str = Field("read", description="'read' or 'write'")
    skills: tuple[str, ...] = ()
    extraction_context: str = ""
    #: Step 3 (the MCP config modal). Environment variable NAMES this connector instance expects
    #: at connect time — never a value. Lands on the REAL, validated `mcp.env` field.
    env_names: tuple[str, ...] = ()
    #: Step 7 — declared `AdapterShell` configuration. Recorded on `_connector.agent`, inert, the
    #: same treatment as skills and extraction context: it cannot widen a capability because
    #: nothing outside this block ever reads it.
    agent_row_limit: int = Field(200, ge=1, le=100_000)
    agent_named_queries: str = ""
    #: An operator-chosen name for THIS connector instance. Defaults to the catalogue id, so two
    #: connectors against the same server need distinct names or the second refuses on rename
    #: collision the same way any adapter id collision would.
    name: Optional[str] = None


def _refusal(code: str, message: str, status: int = 422, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "nearest_alternative": None}
    body.update(extra)
    return JSONResponse(status_code=status, content={"refusal": body})


def register_connector_routes(app: FastAPI) -> None:
    """Mount the configure-by-search Connector Builder."""
    install_auth_handler(app)

    @app.get("/connectors/catalogue", dependencies=_AUTH)
    def list_catalogue(q: str = ""):
        """Search the vendored catalogue. Client-side filtering would work identically — this
        exists server-side so the search surface and the compile step read the same table."""
        needle = q.strip().lower()

        def matches(e: dict[str, Any]) -> bool:
            if not needle:
                return True
            haystack = " ".join([
                e["name"], e["summary"], str(e.get("vendor") or ""),
                " ".join(e.get("tags", ())), " ".join(e.get("reads", ())),
            ]).lower()
            return needle in haystack

        results = [_catalogue_view(e) for e in CATALOGUE if matches(e)]
        results.sort(key=lambda e: (e["status"] != "vendored", e["name"]))
        vendored = sum(1 for e in CATALOGUE if e["status"] == "vendored")
        return {
            "servers": results,
            "counts": {"vendored": vendored, "not_yet_vendored": len(CATALOGUE) - vendored,
                       "total": len(CATALOGUE)},
        }

    @app.get("/connectors/catalogue/{server_id}", dependencies=_AUTH)
    def get_catalogue_entry(server_id: str):
        entry = _BY_ID.get(server_id)
        if entry is None:
            return _refusal("UNKNOWN_SERVER", f"no catalogue entry {server_id!r}", status=404)
        return _catalogue_view(entry)

    @app.get("/connectors/skills", dependencies=_AUTH)
    def list_skills():
        return {
            "skills": list(SKILLS),
            "note": ("Declared capability selections, not a runtime grant. There is no Skill "
                     "runtime registry in this build (GOA Submission/11_SKILL_CATALOGUE.md §19 "
                     "describes one); a selection here is recorded on the generated declaration "
                     "and read by a human or a future registry, never interpreted as a "
                     "capability by the compiler."),
        }

    @app.post("/connectors/compile", dependencies=_AUTH)
    def compile_connector(req: ComposeRequest):
        """Compose a declaration from the wizard's steps and hand it to the SAME compiler
        `/builder` uses. A refusal is a RESULT of the compile — 200, `compiled: false` — never a
        transport error, for the same reason `/builder/adapter/compile` treats it that way: the
        most important answer this surface gives must not read as a bug in the browser.
        """
        entry = _BY_ID.get(req.server_id)
        if entry is None:
            return _refusal("UNKNOWN_SERVER", f"no catalogue entry {req.server_id!r}", status=404)

        if req.mode not in ("read", "write"):
            return _refusal("UNKNOWN_MODE", f"mode must be 'read' or 'write'; got {req.mode!r}")

        unknown_skills = sorted(set(req.skills) - _SKILL_IDS)
        if unknown_skills:
            return _refusal("UNKNOWN_SKILL",
                            f"{unknown_skills} is not in the Skill catalogue "
                            f"(GOA Submission/11_SKILL_CATALOGUE.md WS-01..WS-14)")

        if len(req.extraction_context) > MAX_CONTEXT_CHARS:
            return _refusal("CONTEXT_TOO_LARGE",
                            f"extraction context is configuration, not a document; this one "
                            f"exceeds {MAX_CONTEXT_CHARS} characters")

        invalid_env = sorted(n for n in req.env_names if not ENV_NAME_RE.match(n))
        if invalid_env:
            return _refusal("INVALID_ENV_NAME",
                            f"{invalid_env} must be UPPER_SNAKE_CASE environment variable names "
                            f"(e.g. AIRTABLE_API_KEY) — this form records the NAME only, never a "
                            f"value")
        if len(req.env_names) > MAX_ENV_NAMES:
            return _refusal("TOO_MANY_ENV_NAMES",
                            f"a connector declares at most {MAX_ENV_NAMES} env var names; got "
                            f"{len(req.env_names)}")

        if len(req.agent_named_queries) > MAX_NAMED_QUERIES_CHARS:
            return _refusal("AGENT_NAMED_QUERIES_TOO_LARGE",
                            f"declared named queries are configuration, not a document; this "
                            f"exceeds {MAX_NAMED_QUERIES_CHARS} characters")

        if entry["status"] != "vendored":
            # Not the loader's refusal — there is nothing to hand the loader yet. Still computed
            # from the catalogue's own recorded state, never invented per request, and it is the
            # SAME sentence the search card already showed before this button was clicked.
            return {
                "compiled": False,
                "declaration": None,
                "refusal": {
                    "code": "SERVER_NOT_VENDORED",
                    "kind": "NotYetVendored",
                    "stage": "catalogue",
                    "message": (
                        f"{entry['name']} is not vendored in this deployment: "
                        f"{entry.get('not_vendored_reason', 'no reviewed artefact yet.')} "
                        f"Servers are vendored and pinned before they can be configured — never "
                        f"fetched at runtime — so this cannot proceed until a reviewed artefact "
                        f"lands under fixtures/mcp/ with a digest-pinned declaration."),
                },
            }

        template_path = DATABRIDGE_ROOT / entry["template"]
        raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):                            # pragma: no cover - fixture guard
            return _refusal("CATALOGUE_TEMPLATE_INVALID",
                            f"the reviewed template for {entry['id']!r} is not a mapping",
                            status=500)

        name = (req.name or "").strip()
        if name:
            raw["adapter"] = name
            raw["source_id"] = name

        if req.mode == "write":
            # Never a hand-written refusal. Ask for exactly the write access implied by the read
            # allowlist the operator just picked, and let the loader's own CapabilitiesDecl
            # refuse it by name.
            caps = dict(raw.get("capabilities") or {})
            caps["write"] = list(caps.get("read") or ())
            raw["capabilities"] = caps

        if req.env_names:
            # A REAL, validated field (`McpServerRef.env`) — not the inert `_connector` block.
            # Names only; the value is left blank for the deployment's own secret store to fill in
            # at connect time, and this route never sees or persists one.
            mcp = dict(raw.get("mcp") or {})
            declared_env = dict(mcp.get("env") or {})
            for env_name in req.env_names:
                declared_env.setdefault(env_name, "")
            mcp["env"] = declared_env
            raw["mcp"] = mcp

        # Skills, extraction context, and the declared adapter-agent configuration: an inert,
        # underscore-prefixed block. `load_mcp_declaration` strips every top-level key starting
        # with "_" before validating, so this can be read by a human (or a future Skill runtime /
        # Shell wiring) without ever being able to widen a capability.
        raw["_connector"] = {
            "built_by": "connector-builder",
            "catalogue_id": entry["id"],
            "mode_requested": req.mode,
            "skills": list(req.skills),
            "extraction_context": req.extraction_context,
            "agent": {
                "row_limit": req.agent_row_limit,
                "named_queries": req.agent_named_queries,
            },
        }

        text = (
            f"# Generated by the Connector Builder from {entry['template']}\n"
            f"# The projection, trust classes and redaction rules below are the REVIEWED "
            f"template's own; this surface does not let an operator edit them.\n\n"
            + yaml.safe_dump(raw, sort_keys=False)
        )

        try:
            from databridge_adapter.sources.mcp import load_mcp_declaration
        except ImportError as exc:                               # pragma: no cover - path guard
            return _refusal(
                "ADAPTER_NOT_IMPORTABLE",
                f"the adapter compiler is not importable from this process ({exc})", status=503)

        tmp = Path(tempfile.mkdtemp(prefix="walnut-connector-")) / "declaration.yaml"
        try:
            tmp.write_text(text, encoding="utf-8")
            declaration = load_mcp_declaration(tmp, repo_root=DATABRIDGE_ROOT)
        except Exception as exc:                                 # noqa: BLE001
            # Every loader refusal lands here — `WriteCapabilityRefused` on a "write" request,
            # `ServerQuarantined` if the vendored artefact drifted since it was reviewed, and so
            # on. The exception CLASS is the refusal's code, exactly as `/builder` treats it: this
            # module does not get a second opinion about why the compiler said no.
            return {
                "compiled": False,
                "declaration": text,
                "refusal": {"code": _code_of(exc), "kind": type(exc).__name__,
                           "message": str(exc), "stage": "compile"},
            }
        finally:
            try:
                tmp.unlink(missing_ok=True)
                tmp.parent.rmdir()
            except OSError:                                      # pragma: no cover - best effort
                pass

        return {
            "compiled": True,
            "declaration": text,
            "refusal": None,
            "compiles_to": _compiled_view(declaration),
            "connector": {"mode_requested": req.mode, "skills": list(req.skills),
                         "extraction_context": req.extraction_context,
                         "env_names": list(req.env_names),
                         "agent": {"row_limit": req.agent_row_limit,
                                  "named_queries": req.agent_named_queries}},
        }
