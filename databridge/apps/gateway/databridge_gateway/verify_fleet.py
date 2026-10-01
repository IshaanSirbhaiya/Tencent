"""The **evidence fleet** — the real adapters behind `/evidence`'s `Verify` button.

`DEMO_AND_FRONTEND.md` §2.3 calls the Verify sequence "the demo's best ten seconds": show a
determination citing a value, click `Verify` → `VERIFIED`; change the underlying row in front of
the audience; click `Verify` again → `DRIFT_MATERIAL`, old beside new. Nothing about that sequence
survives being faked — a verdict that is decided in this file is a label, not a verification — so
this module builds a **real adapter over a real estate** and calls its real `verify()`.

## What is real here, stated plainly

* the adapter is whatever the estate's **fixture-fleet declaration** names
  (`fixtures/packs/<estate>/fixture_fleet.yaml`), constructed through the same five-verb port the
  orchestrator uses, admitted by a real `PolicyEngine` built from that estate's own `roles.yaml`
  and the shipped purpose bindings;
* the rows are the estate's own DDL and seed, loaded into a private SQLite working copy by the
  loader the declaration names (a `databridge_adapter.` function — the same shared loader the
  adapter and materialise tests use);
* the pointers are minted by the adapter during a real `resolve()`; nothing in this file writes a
  `SourcePointer`;
* the verdict is whatever `verify()` returns. This module never chooses one.

## No estate is named in this file (R-019 round 2, finding 4)

Which identities hold which role, purpose and grant; which reads become citations; which source
the working copy serves; who may drive the mutation control — all of that is the ESTATE's
behaviour, and it lives in the estate's `fixture_fleet.yaml`, validated strictly on load against
the closed vocabularies (`roles.yaml`'s roles, the bound packs' purposes). This module discovers
declarations by file name and names no domain, role, purpose or source itself.

## The one thing that is a deployment decision rather than a measurement

The estate is loaded into a **private working copy** under a temp directory, and the repo fixture
is never opened for writing. `POST /evidence/verify/mutate` writes to that copy and to nothing
else, only for an identity whose declaration carries the explicit write entitlement
(`writes: [evidence.mutate_working_copy]` — a read grant never implies it), and only a cell the
fleet itself read and served to that identity.

## The fixture fleet never speaks for a pointer it did not serve

A pointer is verified against the fixture estate only when THIS fleet minted it and served it to
THE SIGNED-IN identity. Any other pointer — a registered connection's, an unknown source's, or one
served to somebody else — is `UNVERIFIABLE` here: a real source that happens to share a source id
with a fixture adapter must never be verdicted against fixture rows, and a citation served to one
principal must not disclose its value (or its drift) to another.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("databridge.gateway.verify")

#: databridge/apps/gateway/databridge_gateway/ -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
PACKS_ROOT = DATABRIDGE_ROOT / "packs"
#: Where estates declare their fixture fleets: `<root>/<estate>/fixture_fleet.yaml`.
FIXTURE_FLEETS_ROOT = DATABRIDGE_ROOT / "fixtures" / "packs"
FIXTURE_FLEET_FILE = "fixture_fleet.yaml"
#: Names the declaration (`mode`) `/evidence/verify` uses; unset → the first (sorted) declaration
#: that has an `evidence:` section.
EVIDENCE_FLEET_ENV = "WALNUT_EVIDENCE_FLEET"
#: The write entitlement the mutation control requires. A surface grant is a READ grant only.
MUTATE_ENTITLEMENT = "evidence.mutate_working_copy"
#: Only adapter classes and loaders under this package may be named by a declaration.
_ALLOWED_IMPORT_PREFIX = "databridge_adapter."

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class FleetUnavailable(RuntimeError):
    """The fleet could not be built. Carries a CODE-AUTHORED reason (never exception text) so a
    route can say it rather than 500."""


class NoFixtureGrant(FleetUnavailable):
    """The signed-in identity holds no grant in the fixture estate. The message names no other
    identity — which identities DO hold grants is not disclosed to a caller who holds none."""

    def __init__(self) -> None:
        super().__init__("the signed-in identity holds no grant in this fixture estate")


class NoWriteGrant(FleetUnavailable):
    """The signed-in identity may READ in the fixture estate but holds no write entitlement."""

    def __init__(self) -> None:
        super().__init__("the signed-in identity holds a read grant in this fixture estate, not a "
                         "write entitlement; reading a citation never implies being allowed to "
                         "change it")


class FixtureFleetError(ValueError):
    """A `fixture_fleet.yaml` that does not validate. Loud and file-naming (server log only)."""


def error_ref(logger: logging.Logger, context: str, exc: Optional[BaseException] = None,
              **detail: Any) -> str:
    """Log an internal failure in full under a fresh reference and return the reference.

    R-019 round 2, finding 13: exception text, SQL and driver diagnostics go to the server log
    under this reference; the caller gets a stable code and the reference, nothing else."""
    ref = f"ERR-{uuid.uuid4().hex[:10]}"
    if exc is not None:
        logger.error("[%s] %s %s", ref, context, detail or "", exc_info=exc)
    else:
        logger.error("[%s] %s %s", ref, context, detail or "")
    return ref


# =============================================================================================
# the declaration — the estate's behaviour, loaded from its own YAML
# =============================================================================================
@dataclass(frozen=True)
class ReadSpec:
    """One declared read whose cells become verifiable citations."""

    source_id: str
    entity: str
    fields: tuple[str, ...]
    key: tuple[str, ...]
    order_by: tuple[str, ...]
    limit: int
    capability: str = "row_read"
    #: The columns whose cells are offered as citations. A subset of `fields`.
    cite: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterDecl:
    source_id: str
    adapter: str                          # "module:Class", under databridge_adapter.
    engine: bool = False                  # pass the working copy's engine
    capture_store: Optional[str] = None   # a CaptureStore dir under the working copy
    options: tuple[tuple[str, Any], ...] = ()
    write_schema: Optional[str] = None    # schema the mutation control writes (working copy)


@dataclass(frozen=True)
class PrincipalDecl:
    id: str
    role: str
    purpose: str
    grant_id: str
    label: str
    surfaces: frozenset[str]
    writes: frozenset[str]

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "purpose": self.purpose, "grant_id": self.grant_id,
                "label": self.label}


@dataclass(frozen=True)
class FixtureFleetDecl:
    path: Path
    mode: str
    estate_label: str
    purpose_packs: tuple[str, ...]
    roles_yaml: Path
    sources_yaml: Path
    loader: str
    schema_dir: Path
    seed: tuple[str, ...]
    actor_id: str
    actor_role: str
    principals: dict[str, PrincipalDecl]
    default_principal: Optional[str]
    orchestrate_adapters: tuple[AdapterDecl, ...]
    evidence_adapters: tuple[AdapterDecl, ...]
    reads: tuple[ReadSpec, ...]
    pointer_verification_purpose: Optional[str]

    def directory(self, surface: str) -> dict[str, PrincipalDecl]:
        """The identities that may use `surface` (`orchestrate` / `evidence`)."""
        return {k: v for k, v in self.principals.items() if surface in v.surfaces}

    def validate_against(self, role_model: Any, purposes: Any) -> None:
        """Every role must be one `roles.yaml` declares; every purpose one the bound packs
        declare. A declaration naming anything else is refused, never partially loaded."""
        roles = set(getattr(role_model, "roles", {}) or {})
        problems = []
        for p in list(self.principals.values()):
            if p.role not in roles:
                problems.append(f"principal {p.id!r}: role {p.role!r} is not declared")
            if not purposes.declares(p.purpose):
                problems.append(f"principal {p.id!r}: purpose {p.purpose!r} is not declared")
        if self.actor_role not in roles:
            problems.append(f"actor: role {self.actor_role!r} is not declared")
        if (self.pointer_verification_purpose
                and not purposes.declares(self.pointer_verification_purpose)):
            problems.append("evidence.pointer_verification_purpose is not a declared purpose")
        if problems:
            raise FixtureFleetError(f"{self.path}: " + "; ".join(problems))


def _strict(d: Any, allowed: set[str], where: str, required: set[str] = frozenset()) -> dict:
    if not isinstance(d, dict):
        raise FixtureFleetError(f"{where} must be a mapping")
    unknown = set(d) - allowed
    if unknown:
        raise FixtureFleetError(f"{where}: unknown key(s) {sorted(unknown)}")
    missing = set(required) - set(d)
    if missing:
        raise FixtureFleetError(f"{where}: missing key(s) {sorted(missing)}")
    return d


def _strs(v: Any, where: str) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise FixtureFleetError(f"{where} must be a list of non-empty strings")
    return tuple(v)


def _import_path(v: Any, where: str) -> str:
    if not isinstance(v, str) or ":" not in v or not v.startswith(_ALLOWED_IMPORT_PREFIX):
        raise FixtureFleetError(f"{where} must be 'module:name' under {_ALLOWED_IMPORT_PREFIX}")
    return v


def _adapters(raw: Any, where: str) -> tuple[AdapterDecl, ...]:
    if not isinstance(raw, list) or not raw:
        raise FixtureFleetError(f"{where} must be a non-empty list")
    out = []
    for i, a in enumerate(raw):
        w = f"{where}[{i}]"
        _strict(a, {"source_id", "adapter", "engine", "capture_store", "options", "write_schema"},
                w, {"source_id", "adapter"})
        opts = a.get("options") or {}
        if not isinstance(opts, dict) or any(k in ("source_id", "policy", "engine", "capture",
                                                   "sources_yaml") for k in opts):
            raise FixtureFleetError(f"{w}.options must be a mapping of extra keyword arguments")
        ws = a.get("write_schema")
        if ws is not None and not (isinstance(ws, str) and _IDENT.match(ws)):
            raise FixtureFleetError(f"{w}.write_schema must be a plain identifier")
        out.append(AdapterDecl(source_id=str(a["source_id"]),
                               adapter=_import_path(a["adapter"], f"{w}.adapter"),
                               engine=bool(a.get("engine", False)),
                               capture_store=a.get("capture_store"),
                               options=tuple(sorted(opts.items())), write_schema=ws))
    return tuple(out)


def load_fixture_fleet(path: Path) -> FixtureFleetDecl:
    """Parse and structurally validate one `fixture_fleet.yaml`. The closed-vocabulary check
    (`validate_against`) needs the role model and purpose vocabulary, so it runs at build."""
    import yaml

    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    top = {"version", "mode", "estate_label", "purpose_packs", "roles", "sources", "estate",
           "actor", "principals", "default_principal", "orchestrate", "evidence"}
    _strict(raw, top, str(path), {"version", "mode", "roles", "sources", "estate", "actor",
                                  "principals", "purpose_packs"})
    if raw["version"] != 1:
        raise FixtureFleetError(f"{path}: unsupported version {raw['version']!r}")
    base = path.parent
    estate = _strict(raw["estate"], {"loader", "schema_dir", "seed"}, "estate",
                     {"loader", "schema_dir", "seed"})
    actor = _strict(raw["actor"], {"id", "role"}, "actor", {"id", "role"})
    principals: dict[str, PrincipalDecl] = {}
    if not isinstance(raw["principals"], dict) or not raw["principals"]:
        raise FixtureFleetError("principals must be a non-empty mapping")
    for pid, p in raw["principals"].items():
        _strict(p, {"role", "purpose", "grant_id", "label", "surfaces", "writes"},
                f"principals.{pid}", {"role", "purpose", "grant_id", "surfaces"})
        principals[str(pid)] = PrincipalDecl(
            id=str(pid), role=str(p["role"]), purpose=str(p["purpose"]),
            grant_id=str(p["grant_id"]), label=str(p.get("label") or pid),
            surfaces=frozenset(_strs(p["surfaces"], f"principals.{pid}.surfaces")),
            writes=frozenset(_strs(p.get("writes"), f"principals.{pid}.writes")))
    default = raw.get("default_principal")
    if default is not None and default not in principals:
        raise FixtureFleetError("default_principal must be one of principals")
    orch = raw.get("orchestrate")
    orch_adapters: tuple[AdapterDecl, ...] = ()
    if orch is not None:
        _strict(orch, {"adapters"}, "orchestrate", {"adapters"})
        orch_adapters = _adapters(orch["adapters"], "orchestrate.adapters")
    ev = raw.get("evidence")
    ev_adapters: tuple[AdapterDecl, ...] = ()
    reads: list[ReadSpec] = []
    pvp = None
    if ev is not None:
        _strict(ev, {"adapters", "reads", "pointer_verification_purpose"}, "evidence",
                {"adapters", "reads"})
        ev_adapters = _adapters(ev["adapters"], "evidence.adapters")
        pvp = ev.get("pointer_verification_purpose")
        served = {a.source_id for a in ev_adapters}
        for i, r in enumerate(ev["reads"] or []):
            w = f"evidence.reads[{i}]"
            _strict(r, {"source_id", "entity", "fields", "key", "order_by", "limit",
                        "capability", "cite"}, w, {"source_id", "entity", "fields", "key",
                                                    "limit"})
            spec = ReadSpec(source_id=str(r["source_id"]), entity=str(r["entity"]),
                            fields=_strs(r["fields"], f"{w}.fields"),
                            key=_strs(r["key"], f"{w}.key"),
                            order_by=_strs(r.get("order_by"), f"{w}.order_by"),
                            limit=int(r["limit"]), capability=str(r.get("capability",
                                                                      "row_read")),
                            cite=_strs(r.get("cite"), f"{w}.cite"))
            if spec.source_id not in served:
                raise FixtureFleetError(f"{w}: source {spec.source_id!r} has no evidence adapter")
            if not set(spec.cite) <= set(spec.fields):
                raise FixtureFleetError(f"{w}.cite must be a subset of fields")
            reads.append(spec)
    return FixtureFleetDecl(
        path=path, mode=str(raw["mode"]), estate_label=str(raw.get("estate_label") or ""),
        purpose_packs=_strs(raw["purpose_packs"], "purpose_packs"),
        roles_yaml=base / str(raw["roles"]), sources_yaml=base / str(raw["sources"]),
        loader=_import_path(estate["loader"], "estate.loader"),
        schema_dir=base / str(estate["schema_dir"]),
        seed=_strs(estate["seed"], "estate.seed"),
        actor_id=str(actor["id"]), actor_role=str(actor["role"]), principals=principals,
        default_principal=default, orchestrate_adapters=orch_adapters,
        evidence_adapters=ev_adapters, reads=tuple(reads), pointer_verification_purpose=pvp)


_DECL_CACHE: dict[tuple[str, int], FixtureFleetDecl] = {}


def fixture_fleet_decls(root: Optional[Path] = None) -> list[FixtureFleetDecl]:
    """Every valid declaration under `root`, sorted by path. One that does not validate is logged
    and skipped — it never becomes a partially-loaded fleet."""
    base = Path(root) if root is not None else FIXTURE_FLEETS_ROOT
    out = []
    for p in sorted(base.glob(f"*/{FIXTURE_FLEET_FILE}")):
        try:
            key = (str(p), p.stat().st_mtime_ns)
            if key not in _DECL_CACHE:
                _DECL_CACHE[key] = load_fixture_fleet(p)
            out.append(_DECL_CACHE[key])
        except Exception:                                        # noqa: BLE001
            log.exception("fixture fleet declaration %s did not load; skipped", p)
    return out


def fixture_fleet_decl(mode: str, surface: str,
                       root: Optional[Path] = None) -> Optional[FixtureFleetDecl]:
    """The declaration whose `mode` is `mode` and that has a section for `surface`."""
    for d in fixture_fleet_decls(root):
        has = d.orchestrate_adapters if surface == "orchestrate" else d.evidence_adapters
        if d.mode == mode and has:
            return d
    return None


def evidence_fleet_decl(root: Optional[Path] = None) -> Optional[FixtureFleetDecl]:
    """`WALNUT_EVIDENCE_FLEET` when set, else the first declaration with an `evidence:` section."""
    want = os.environ.get(EVIDENCE_FLEET_ENV, "").strip()
    if want:
        return fixture_fleet_decl(want, "evidence", root)
    return next((d for d in fixture_fleet_decls(root) if d.evidence_adapters), None)


def import_declared(path: str) -> Any:
    """`module:name` from a validated declaration (always under `databridge_adapter.`)."""
    if not path.startswith(_ALLOWED_IMPORT_PREFIX):
        raise FixtureFleetError(f"{path!r} is outside {_ALLOWED_IMPORT_PREFIX}")
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


def build_policy(decl: FixtureFleetDecl, surface: str) -> tuple[Any, Any]:
    """`(PolicyEngine, purpose vocabulary)` for a declaration, validated against the closed
    vocabularies first. The directory holds only the identities that may use `surface`."""
    from databridge_adapter import (
        Budget,
        PolicyEngine,
        PrincipalBinding,
        load_purpose_bindings,
        load_role_model,
        purpose_vocabulary,
    )

    purposes = purpose_vocabulary(decl.purpose_packs, PACKS_ROOT)
    role_model = load_role_model(decl.roles_yaml)
    decl.validate_against(role_model, purposes)
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    # Who holds which role is the identity provider's statement, not `roles.yaml`'s — that file
    # says what a ROLE may do. The declaration is the deployment's statement of it.
    directory = {name: PrincipalBinding(roles=(p.role,))
                 for name, p in decl.directory(surface).items()}
    directory[decl.actor_id] = PrincipalBinding(roles=(decl.actor_role,))
    policy = PolicyEngine(role_model=role_model, purpose_binding=binding, directory=directory,
                          default_budget=Budget(max_rows=5_000))
    return policy, purposes


def build_adapter(a: AdapterDecl, *, policy: Any, decl: FixtureFleetDecl, engine: Any,
                  workdir: Path) -> Any:
    kwargs: dict[str, Any] = {"source_id": a.source_id, "policy": policy,
                              "sources_yaml": decl.sources_yaml}
    if a.engine:
        kwargs["engine"] = engine
    if a.capture_store:
        from databridge_adapter.capture import CaptureStore

        kwargs["capture"] = CaptureStore(root=workdir / a.capture_store)
    kwargs.update(dict(a.options))
    return import_declared(a.adapter)(**kwargs)


def _principals_view(decl: Optional[FixtureFleetDecl], surface: str) -> dict[str, dict[str, str]]:
    return {k: v.as_dict() for k, v in (decl.directory(surface) if decl else {}).items()}


# Module-level views of the DEFAULT evidence declaration, for introspection and tests. The fleet
# itself resolves its declaration when it is built and never reads these.
_DEFAULT_DECL = evidence_fleet_decl()
#: identity -> {role, purpose, grant_id, label}, for the identities that may use `/evidence`.
FIXTURE_PRINCIPALS: dict[str, dict[str, str]] = _principals_view(_DEFAULT_DECL, "evidence")
#: The reads whose cells become citations.
READS: tuple[ReadSpec, ...] = _DEFAULT_DECL.reads if _DEFAULT_DECL else ()
POINTER_VERIFICATION_PURPOSE: Optional[str] = (
    _DEFAULT_DECL.pointer_verification_purpose if _DEFAULT_DECL else None)


# =============================================================================================
# what the fleet serves
# =============================================================================================
@dataclass(frozen=True)
class Citation:
    """One cell a real read emitted, with the pointer that grounds it.

    `value_at_read` is what the adapter returned *at the moment of the read* and is recorded here
    rather than trusted from a client: "old vs new" is only meaningful if the old half came from
    the system that emitted it.
    """

    citation_id: str
    source_id: str
    entity: str
    column: str
    row_id: Optional[str]
    value_at_read: Any
    read_at: str
    trust: Optional[str]
    pointer: dict
    #: The purpose the read that minted this citation ran under. Verification re-checks admission
    #: under THIS purpose, so it is recorded rather than assumed.
    purpose: str = ""
    #: The authenticated identity the read ran as.
    read_by: Optional[str] = None


@dataclass
class _Registered:
    adapter: Any
    description: Any
    engine: Any = None
    working_root: Optional[Path] = None
    write_schema: Optional[str] = None
    mutable_columns: set[tuple[str, str]] = field(default_factory=set)


class EvidenceFleet:
    """The adapters this deployment can verify a pointer against, and the reads it has served.

    Built lazily on first use — a gateway that spent boot loading an estate nobody asked about
    would make every other surface slower for one button.
    """

    def __init__(self, decl: Optional[FixtureFleetDecl] = None) -> None:
        self._lock = threading.Lock()
        # A SECOND lock, held around every read and every write. FastAPI serves sync endpoints
        # from a threadpool, so two clicks can enter `resolve()` on the same adapter and the same
        # SQLite connection at once; and a read interleaved with a mutation would produce an
        # "old" value that was never anybody's old value.
        self._io = threading.Lock()
        self._decl = decl
        self._sources: dict[str, _Registered] = {}
        # Per signed-in identity: what one principal's read returned is never served to another.
        self._citations: dict[str, dict[str, Citation]] = {}
        # (principal_id, pointer identity) -> the citation served to THAT principal. There is no
        # global register: a value is only ever reported back to the identity it was served to.
        self._recorded: dict[tuple[str, str], Citation] = {}
        self._built = False
        self._build_error: Optional[str] = None
        self._workdir: Optional[Path] = None
        self._purposes: Any = None
        self._mutations: list[dict] = []

    # -- construction ------------------------------------------------------------------------
    @property
    def decl(self) -> Optional[FixtureFleetDecl]:
        if self._decl is None:
            self._decl = evidence_fleet_decl()
        return self._decl

    def ensure_built(self) -> None:
        with self._lock:
            if self._built:
                if self._build_error:
                    raise FleetUnavailable(self._build_error)
                return
            try:
                self._build()
            except FleetUnavailable as exc:
                self._build_error = str(exc)
                self._built = True
                raise
            except Exception as exc:                             # noqa: BLE001
                ref = error_ref(log, "evidence fleet could not be built", exc)
                self._build_error = (f"the evidence fleet could not be built (reference {ref}; "
                                     f"the detail is in the server log)")
                self._built = True
                raise FleetUnavailable(self._build_error) from exc
            self._built = True

    def _build(self) -> None:
        try:
            import databridge_adapter  # noqa: F401
        except ImportError as exc:                               # pragma: no cover - path guard
            error_ref(log, "databridge_adapter is not importable", exc)
            raise FleetUnavailable(
                "databridge_adapter is not importable from this process. Add 'packages/adapter' "
                "to the root pyproject `pythonpath` and to apps/gateway/serve.py::_PACKAGES "
                "together — the two are asserted to agree") from exc

        decl = self.decl
        if decl is None:
            raise FleetUnavailable(
                "no fixture-fleet declaration in this deployment serves /evidence "
                f"(`fixtures/packs/*/{FIXTURE_FLEET_FILE}` with an `evidence:` section)")
        if not decl.schema_dir.joinpath("schema.sql").is_file():
            raise FleetUnavailable(
                "the declared estate holds no schema.sql, so there is no estate to verify "
                "against. The fleet is not built from a fixture of verdicts; with no rows there is "
                "nothing to re-read")

        policy, self._purposes = build_policy(decl, "evidence")
        self._workdir = Path(tempfile.mkdtemp(prefix="walnut-evidence-"))
        engine = import_declared(decl.loader)(self._workdir / "estate", tables=decl.seed)
        for a in decl.evidence_adapters:
            adapter = build_adapter(a, policy=policy, decl=decl, engine=engine,
                                    workdir=self._workdir)
            self._sources[a.source_id] = _Registered(
                adapter=adapter, description=adapter.describe(),
                engine=engine if a.engine else None,
                working_root=self._workdir if a.engine else None, write_schema=a.write_schema)
        log.info("evidence fleet built from %s: %s (working copy at %s)", decl.path,
                 sorted(self._sources), self._workdir)

    # -- principals --------------------------------------------------------------------------
    def _binding(self, principal_id: Optional[str]) -> PrincipalDecl:
        """The declaration's entry for the SIGNED-IN identity, or `NoFixtureGrant`."""
        decl = self.decl
        info = decl.directory("evidence").get(principal_id or "") if decl else None
        if info is None:
            raise NoFixtureGrant()
        return info

    def _principal(self, principal_id: str, purpose: str):
        from databridge_adapter import Principal

        info = self._binding(principal_id)
        return Principal(actor=self.decl.actor_id, on_behalf_of=principal_id,
                         purpose=purpose, grant_id=info.grant_id)

    def _purpose_scope(self):
        from databridge_adapter import use_purposes

        return use_purposes(self._purposes)

    # -- the served reads --------------------------------------------------------------------
    def _read(self, principal_id: str) -> list[Citation]:
        """Run every declared read once and return the cells it emitted, right now."""
        from databridge_adapter import CapabilityRequest, Projection
        from databridge_core import GroundedValue

        out: list[Citation] = []
        now = datetime.now(timezone.utc).isoformat()
        purpose = self._binding(principal_id).purpose
        with self._purpose_scope():
            principal = self._principal(principal_id, purpose)
            for spec in self.decl.reads:
                reg = self._sources.get(spec.source_id)
                if reg is None:
                    continue
                req = CapabilityRequest(
                    source_id=spec.source_id, capability=spec.capability, principal=principal,
                    projection=Projection(entity=spec.entity, fields=spec.fields, key=spec.key),
                    order_by=spec.order_by, limit=spec.limit)
                result = reg.adapter.resolve(req)
                rows = result.rows
                columns = tuple(rows.columns)
                cite = spec.cite or columns
                for row in rows.rows:
                    cells = dict(zip(columns, row))
                    for column in cite:
                        cell = cells.get(column)
                        # Withheld and Absent are real states and are NOT citations.
                        if not isinstance(cell, GroundedValue):
                            continue
                        pointer = cell.pointer
                        out.append(Citation(
                            citation_id=pointer_identity(pointer),
                            source_id=spec.source_id,
                            entity=spec.entity,
                            column=column,
                            row_id=pointer.row_id,
                            value_at_read=_jsonable(cell.value),
                            read_at=now,
                            trust=getattr(pointer.trust, "value", pointer.trust),
                            pointer=pointer.model_dump(mode="json", exclude_none=True),
                            purpose=purpose,
                            read_by=principal_id,
                        ))
                        reg.mutable_columns.add((spec.entity, column))
        return out

    def citations(self, principal_id: str, *, refresh: bool = False) -> list[Citation]:
        """The cells this gateway offers the SIGNED-IN identity for verification."""
        self.ensure_built()
        self._binding(principal_id)
        with self._io:
            mine = self._citations.get(principal_id)
            if mine and not refresh:
                return sorted(mine.values(), key=lambda c: c.citation_id)
            fresh = self._read(principal_id)
            self._citations[principal_id] = {c.citation_id: c for c in fresh}
            for c in fresh:
                self._recorded.setdefault((principal_id, c.citation_id), c)
            return sorted(self._citations[principal_id].values(), key=lambda c: c.citation_id)

    def recorded(self, pointer, principal_id: Optional[str]) -> Optional[Citation]:
        """The citation this gateway served THIS identity for `pointer`, if it served one.

        R-019 round 2, finding 5: principal-scoped. A pointer served to A and submitted by B
        returns nothing for B — not A's value, not A's read time."""
        if not principal_id:
            return None
        return self._recorded.get((principal_id, pointer_identity(pointer)))

    def current_value(self, pointer, principal_id: str) -> tuple[bool, Any]:
        """`(found, value)` for this pointer's **cell**, as the source reads today, for the
        identity it was served to. Matched on the cell's coordinates, not pointer identity (a
        drift mints a different pointer for the same cell)."""
        self.ensure_built()
        recorded = self.recorded(pointer, principal_id)
        if recorded is None:
            return False, None
        with self._io:
            fresh = self._read(principal_id)
        for citation in fresh:
            if (citation.source_id == recorded.source_id
                    and citation.entity == recorded.entity
                    and citation.column == recorded.column
                    and citation.row_id == recorded.row_id):
                return True, citation.value_at_read
        return False, None

    # -- verification ------------------------------------------------------------------------
    def source_ids(self) -> list[str]:
        return sorted(self._sources)

    def verify(self, pointer, principal_id: str) -> tuple[Any, Optional[str], Optional[str]]:
        """`(verdict, note, source_id)` from the adapter that serves this pointer's source.

        Only a pointer this fleet served to THIS identity is verified against the fixture
        estate (R-019 round 2, findings 2 and 5). Anything else is `UNVERIFIABLE` — the honest
        word for "this deployment cannot answer for it" — and no fixture row is consulted."""
        from databridge_adapter.verdicts import Verdict

        self.ensure_built()
        self._binding(principal_id)
        reg = self._sources.get(pointer.source_id)
        if reg is None:
            return (Verdict.UNVERIFIABLE,
                    f"no adapter in this deployment serves source {pointer.source_id!r}; the "
                    f"evidence fleet serves {self.source_ids()}. A pointer this fleet cannot "
                    f"reach is unverifiable HERE — that is a property of the deployment, not a "
                    f"judgement about the citation",
                    None)
        recorded = self.recorded(pointer, principal_id)
        if recorded is None:
            return (Verdict.UNVERIFIABLE,
                    f"this fixture fleet (serving {self.source_ids()}) verifies only citations it "
                    f"read and served to the signed-in identity, and it did not serve this pointer "
                    f"to you. It may cite a real source that shares this id; it is never "
                    f"verdicted against fixture rows",
                    None)
        # Re-check under the purpose the cited read was made under.
        with self._purpose_scope():
            principal = self._principal(principal_id, recorded.purpose)
            verify_with_note = getattr(reg.adapter, "verify_with_note", None)
            if verify_with_note is not None:
                verdict, note = verify_with_note(pointer, principal=principal)
            else:                                                # pragma: no cover - port floor
                verdict, note = reg.adapter.verify(pointer), None
        return verdict, note, pointer.source_id

    # -- the live mutation (demo step 2) -----------------------------------------------------
    def may_mutate(self, principal_id: Optional[str]) -> bool:
        try:
            return MUTATE_ENTITLEMENT in self._binding(principal_id).writes
        except NoFixtureGrant:
            return False

    def mutate(self, pointer, new_value: Any, principal_id: str) -> dict:
        """Change the cell this pointer cites, in the fleet's **private working copy**.

        * only an identity whose declaration carries the write entitlement may (a read grant is
          never a write grant — R-019 round 2, finding 11);
        * only a cell the fleet read and served to THAT identity may be changed;
        * the row must still exist, and exactly one row must be updated, or nothing is committed;
        * only the working copy is opened; the repo fixture is never written;
        * the change is recorded, so what the audience saw is auditable afterwards.
        """
        from sqlalchemy import text

        self.ensure_built()
        binding = self._binding(principal_id)
        reg = self._sources.get(pointer.source_id)
        if reg is None:
            raise FleetUnavailable("no adapter in this fixture fleet serves that source")
        if reg.working_root is None or reg.engine is None or not reg.write_schema:
            raise FleetUnavailable(
                "that source is not backed by a private working copy this fleet may write, so "
                "this gateway will not write to it")
        citation = self.recorded(pointer, principal_id)
        if citation is None:
            raise FleetUnavailable(
                "this gateway has not served you a citation for that pointer, so it will not "
                "change the cell behind it. Load the citations first — the mutation control exists "
                "to move a value this surface already showed you, and nothing else")
        if MUTATE_ENTITLEMENT not in binding.writes:
            raise NoWriteGrant()
        entity, column = citation.entity, citation.column
        if (entity, column) not in reg.mutable_columns:
            raise FleetUnavailable(
                f"{entity}.{column} is not a column this fleet read, so it is not one it will "
                f"write")
        if citation.row_id is None:
            raise FleetUnavailable("the pointer names no row id; there is no row to change")

        # Identifiers are not parameterisable in SQL, so they are re-derived from the description
        # the ADAPTER gave us (and the declaration's validated schema) — never from the request.
        allowed = _entity_columns(reg.description, entity)
        if column not in allowed:
            raise FleetUnavailable(
                f"{column!r} is not a column of {entity} as the source describes it")
        key = _entity_key(reg.description, entity)
        if len(key) != 1:
            raise FleetUnavailable(
                f"{entity} has key {key}; this control changes one row addressed by one key")
        if not all(_IDENT.match(n) for n in (reg.write_schema, entity, column, key[0])):
            raise FleetUnavailable("an identifier is not plain; no statement is built from it")

        with self._io:
            # `from` is read HERE, not taken from the register: on a second mutation the cited
            # value and the previous value differ.
            before, found = None, False
            for fresh in self._read(principal_id):
                if (fresh.entity == entity and fresh.column == column
                        and fresh.row_id == citation.row_id):
                    before, found = fresh.value_at_read, True
                    break
            if not found:
                raise FleetUnavailable(
                    "the cited row is no longer returned by a fresh read, so there is no cell to "
                    "change; nothing was written")
            with reg.engine.begin() as conn:
                result = conn.execute(
                    text(f"UPDATE {reg.write_schema}.{entity} SET {column} = :v "
                         f"WHERE {key[0]} = :k"),
                    {"v": new_value, "k": citation.row_id})
                if result.rowcount != 1:
                    # Raising inside `begin()` rolls the transaction back.
                    raise FleetUnavailable(
                        f"the update matched {result.rowcount} rows, not exactly one; nothing was "
                        f"committed")
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "source_id": pointer.source_id,
            "entity": entity,
            "column": column,
            "row_id": citation.row_id,
            "by": principal_id,
            "from": before,
            "as_cited": citation.value_at_read,
            "to": _jsonable(new_value),
            "wrote_to": "private working copy (not the repo fixture)",
        }
        self._mutations.append(record)
        return record

    @property
    def mutations(self) -> list[dict]:
        return list(self._mutations)

    def status(self, principal_id: Optional[str] = None) -> dict:
        """What the fleet is, whether it built, and what THIS identity may do with it."""
        try:
            self.ensure_built()
            error = None
        except FleetUnavailable as exc:
            error = str(exc)
        sources = []
        for source_id, reg in sorted(self._sources.items()):
            d = reg.description
            sources.append({
                "source_id": source_id,
                "pinning_tier": getattr(d, "pinning_tier", None),
                "egress_class": getattr(d, "egress_class", None),
                "deletes_detectable": getattr(d, "deletes_detectable", None),
                "detects_benign_drift": bool(getattr(d, "detects_benign_drift", False)),
                "reachable_verdicts": sorted(
                    getattr(v, "value", str(v)) for v in getattr(d, "reachable_verdicts", ())),
                "entities": [e.name for e in getattr(d, "entities", ())],
                "working_copy": ("private (temp directory; not disclosed over HTTP)"
                                 if reg.working_root else None),
            })
        decl = self.decl
        me = decl.directory("evidence").get(principal_id or "") if decl else None
        return {
            "available": error is None,
            "reason": error,
            "estate": decl.estate_label if decl else None,
            "sources": sources,
            # The SIGNED-IN identity's purpose in this estate, or None when it holds no grant here.
            "read_purpose": me.purpose if me else None,
            "has_grant": me is not None,
            "can_mutate": bool(me and MUTATE_ENTITLEMENT in me.writes),
            "verify_purpose": me.purpose if me else None,
            "pointer_verification_purpose": decl.pointer_verification_purpose if decl else None,
            # R-019 round 2, finding 5: only the caller's OWN mutations, and none for an identity
            # with no grant here. Another identity's old/new values are not this caller's to see.
            "mutations": [m for m in self._mutations if me is not None
                          and m.get("by") == principal_id],
        }


# =============================================================================================
# helpers
# =============================================================================================
def pointer_identity(pointer) -> str:
    """A stable identity string for a pointer — the SAME key the reverse index binds it under."""
    try:
        from databridge_graph.ptr_index import pointer_identity_string

        return pointer_identity_string(pointer)
    except Exception:                                            # noqa: BLE001 - optional dep
        return pointer.model_dump_json(exclude_none=True)


def _entity_columns(description, entity: str) -> tuple[str, ...]:
    for e in getattr(description, "entities", ()):
        if e.name == entity:
            return tuple(f.name for f in e.fields)
    return ()


def _entity_key(description, entity: str) -> tuple[str, ...]:
    for e in getattr(description, "entities", ()):
        if e.name == entity:
            return tuple(e.key or ())
    return ()


def _jsonable(value: Any) -> Any:
    """Values leave here as JSON. A `Decimal` is rendered as a string rather than a float."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def fleet_for(app) -> EvidenceFleet:
    """The one fleet per app. Built lazily, so importing this module costs nothing."""
    found = getattr(app.state, "evidence_fleet", None)
    if not isinstance(found, EvidenceFleet):
        found = EvidenceFleet()
        app.state.evidence_fleet = found
    return found


def dispose_fleet(app) -> None:
    """Drop the working copy. Called on shutdown; safe to call twice."""
    fleet = getattr(app.state, "evidence_fleet", None)
    if not isinstance(fleet, EvidenceFleet):
        return
    for reg in fleet._sources.values():                           # noqa: SLF001 - same module
        try:
            if reg.engine is not None:
                reg.engine.dispose()
        except Exception:                                         # noqa: BLE001
            log.warning("engine dispose failed", exc_info=True)
    if fleet._workdir is not None:                                # noqa: SLF001
        shutil.rmtree(fleet._workdir, ignore_errors=True)         # noqa: SLF001


# =============================================================================================
# who may read a CONNECTION (registered MCP source, or its captures)
# =============================================================================================
#: R-019 round 2, finding 10: an authenticated identity alone does not authorise reading a
#: connection's data. The grant is the OPERATOR's, from the same directory `/federated` enforces
#: (`federated_routes.granted_connections`, env `WALNUT_FEDERATED_GRANTS`): `"*"` = every
#: connection, a list = those connections, no record = nothing. Fail closed; only the local-demo
#: dev principal keeps every connection while the variable is unset.
NOT_GRANTED = object()


def connection_grant(principal: Any) -> Any:
    """``None`` (every connection), a frozenset of connection ids, or :data:`NOT_GRANTED`."""
    try:
        from .federated_routes import _NOT_GRANTED, granted_connections
    except Exception as exc:                                     # noqa: BLE001
        error_ref(log, "the connection grant directory could not be read; nobody is granted",
                  exc)
        return NOT_GRANTED
    try:
        grant = granted_connections(principal)
    except Exception as exc:                                     # noqa: BLE001
        error_ref(log, "the connection grant could not be decided; nobody is granted", exc)
        return NOT_GRANTED
    return NOT_GRANTED if grant is _NOT_GRANTED else grant


def grant_admits(grant: Any, source_id: Optional[str]) -> bool:
    if grant is NOT_GRANTED:
        return False
    return grant is None or (source_id is not None and source_id in grant)


# =============================================================================================
# verification over REGISTERED CONNECTIONS and the CAPTURE STORE (batch 2b, workstream R)
# =============================================================================================
# The fleet above verifies against an opt-in fixture estate. Everything ingested through MCP
# (`packages/mcp_ingest`) carries a different kind of pointer, and these functions verify those:
#
# * a `table_cell` pointer whose `source_id` is a registered connection is RE-READ LIVE through
#   that connection's own MCP session (its crawl plan's SQL tool). Same value → VERIFIED; a
#   different value → DRIFT_MATERIAL with old beside new. The graph is NEVER updated from here.
# * any pointer bound to a capture (`capture_digest`) is checked against the capture store:
#   re-hash the bytes, read the cited span back (`source_capture.snippet_and_verify`, reused), or
#   find the cited cell in the captured table snapshot. The best such a check can say is
#   VERIFIED_AS_OF_CAPTURE.
#
# A capture vouches for a citation only when its metadata is AUTHENTICATED (it carries a
# `meta_digest` that `capture.get_meta` checked against the content address) and that metadata's
# `source` is the pointer's source (R-019 round 2, finding 8). `sightings` are additive and NOT
# covered by the digest, so they bind nothing. An unbound or mismatched capture is UNVERIFIABLE.
#
# Who may ask is decided by the caller (`verify_routes`) through `connection_grant`.


#: "The request did not offer a value" — distinct from an explicit JSON `null` (finding 7).
class _Omitted:
    def __repr__(self) -> str:                                   # pragma: no cover
        return "<omitted>"


OMITTED: Any = _Omitted()


@dataclass
class GenericVerification:
    """What a verification over a connection or a capture found. The route renders it."""

    verdict: Any                         # databridge_adapter.verdicts.Verdict
    note: str
    method: str                          # "live_source" | "capture"
    verified_by: Optional[str]
    cited_value: Any = None
    cited_value_known: bool = False
    current_value: Any = None
    current_value_known: bool = False
    capture_check: Optional[dict] = None
    live_check: Optional[dict] = None


def registered_connection_ids(sources_root: Optional[Path] = None) -> set[str]:
    """Every connection id DECLARED under `WALNUT_SOURCES_DIR` — including declarations that do
    not parse (R-019 round 2, finding 2). A declared id is reserved for that connection whether or
    not its YAML loads today: an invalid declaration must never let its pointers fall through to
    another verifier. Plan / mapping sidecars (`<id>.plan.yaml`) are not declarations."""
    base = sources_root
    load = None
    try:
        from databridge_mcp_ingest import paths as ingest_paths
        from databridge_mcp_ingest.connections import load_connection as load

        base = base or ingest_paths.sources_dir()
    except Exception:                                            # noqa: BLE001 - optional
        env = os.environ.get("WALNUT_SOURCES_DIR", "").strip()
        base = base or (Path(env) if env else None)
    if base is None or not Path(base).is_dir():
        return set()
    out: set[str] = set()
    for p in sorted(Path(base).glob("*.yaml")):
        if p.stem.count(".") > 0:                                # <id>.plan.yaml / .mapping.yaml
            continue
        out.add(p.stem)
        if load is None:
            continue
        try:
            out.add(load(p.stem, base).id)
        except Exception:                                        # noqa: BLE001
            log.warning("connection declaration %s did not load; its id stays reserved", p.name)
    return out


def _verdict(name: str):
    from databridge_adapter.verdicts import Verdict

    return Verdict[name]


#: The column types under which two renderings are compared as NUMBERS. Matched as substrings of
#: the captured snapshot's own column type (`int`, `bigint`, `numeric(12,2)`, `double precision`…).
_NUMERIC_TYPE_WORDS = ("int", "numeric", "decimal", "real", "double", "float", "money", "number",
                       "serial")
#: A number written canonically: no leading zeros, no sign but `-`, no exponent, no padding.
_CANONICAL_NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def _is_number(v: Any) -> bool:
    from decimal import Decimal

    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _numberish(v: Any) -> bool:
    """An actual number, or a string that is a CANONICAL number (no padding, no leading zeros)."""
    return _is_number(v) or (isinstance(v, str) and bool(_CANONICAL_NUMBER.match(v)))


def _same_value(a: Any, b: Any, col_type: Optional[str] = None) -> bool:
    """Equal as the source renders them — and never more generously than that.

    R-019 round 2, finding 6:
    * nothing is stripped: `" ABC "` → `"ABC"` is a change;
    * a declared NON-numeric column compares its exact text only;
    * numeric equivalence (`29750000.00` == `29750000.0`) applies only to a declared numeric
      column, or when BOTH sides are numbers or canonical numeric strings — so `"00123"` vs `123`
      is a change, whatever the column is."""
    from decimal import Decimal, InvalidOperation

    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    sa, sb = str(a), str(b)
    if sa == sb:
        return True
    typed = (col_type or "").lower()
    declared_numeric = bool(typed) and any(w in typed for w in _NUMERIC_TYPE_WORDS)
    if typed and not declared_numeric:
        return False                                   # a declared non-numeric column: text only
    if not declared_numeric and not (_numberish(a) and _numberish(b)):
        return False
    try:
        return Decimal(sa.strip()) == Decimal(sb.strip())
    except (InvalidOperation, ValueError):
        return False


def _column_type(snap: Optional[dict], column: Any) -> Optional[str]:
    """The captured snapshot's declared type for one column, or None when it declares none."""
    for c in (snap or {}).get("columns") or []:
        if isinstance(c, dict) and c.get("name") == column and c.get("type"):
            return str(c["type"])
    return None


def _capture_sources(cap) -> set[str]:
    """The source the capture's AUTHENTICATED metadata names as having produced these bytes.

    Only a meta file carrying a `meta_digest` (checked by `capture.get_meta` against the content
    address — a failed check leaves `meta` empty and sets `meta_problem`) binds a source. The
    first sighting's `source` is inside the digest; `sightings[]` are not, so they bind nothing."""
    if getattr(cap, "meta_problem", None):
        return set()
    meta = getattr(cap, "meta", None) or {}
    if not meta.get("meta_digest") or not meta.get("source"):
        return set()
    return {str(meta["source"])}


def _source_binding(pointer, cap) -> tuple[Optional[str], set[str]]:
    """`(problem, sources)`: problem is None when the capture is bound to the pointer's source,
    else `capture_meta_corrupt` / `capture_unbound` / `source_mismatch`."""
    if getattr(cap, "meta_problem", None):
        return "capture_meta_corrupt", set()
    recorded = _capture_sources(cap)
    if not recorded:
        return "capture_unbound", recorded
    if pointer.source_id not in recorded:
        return "source_mismatch", recorded
    return None, recorded


_BINDING_NOTE = {
    "capture_meta_corrupt": ("the capture's metadata failed its digest check (swapped or edited), "
                             "so nothing it says binds it to any source"),
    "capture_unbound": ("the capture's metadata is not authenticated to a source (no digest-"
                        "covered `source`), so it cannot be shown to belong to the cited source "
                        "and vouches for nothing"),
    "source_mismatch": ("the capture this pointer cites was produced by another source; it cannot "
                        "vouch for a citation of this one"),
}


def _snapshot_for(pointer, capture_root: Optional[Path]) -> tuple[Optional[dict], dict]:
    """The captured table snapshot a `table_cell` pointer cites, and the capture check on it."""
    from .source_capture import read_capture

    cap = read_capture(getattr(pointer, "capture_digest", None), capture_root)
    if cap is None:
        return None, {"status": "no_capture_binding"}
    if not cap.present or cap.bytes_path is None:
        return None, {"status": "capture_missing", "capture_digest": f"sha256:{cap.digest}"}
    problem, recorded = _source_binding(pointer, cap)
    if problem is not None:
        return None, {"status": problem, "capture_digest": f"sha256:{cap.digest}",
                      "pointer_source": pointer.source_id, "capture_sources": sorted(recorded),
                      "source_bound": False}
    recomputed = cap.recompute_digest()
    check = {"capture_digest": f"sha256:{cap.digest}",
             "recomputed_digest": f"sha256:{recomputed}" if recomputed else None,
             "hash_matches": recomputed == cap.digest, "source": cap.source_meta(),
             "source_bound": True}
    if recomputed != cap.digest:
        check["status"] = "hash_mismatch"
        return None, check
    try:
        snap = json.loads(cap.bytes_path.read_bytes())
    except (OSError, ValueError):
        check["status"] = "not_a_table_snapshot"
        return None, check
    if not isinstance(snap, dict) or not isinstance(snap.get("rows"), list):
        check["status"] = "not_a_table_snapshot"
        return None, check
    check["status"] = "ok"
    return snap, check


def _snapshot_rows(snap: dict, row_id: Any) -> list[dict]:
    """EVERY row the pointer's `row_id` names, by the SAME rule ingestion minted it with
    (`mcp_ingest.build.table_rows`: primary-key values joined by ",", else `#<ordinal>`).

    R-019 round 2, finding 12: that encoding is ambiguous — keys `("a,b", "c")` and `("a", "b,c")`
    both read `a,b,c`. The rule cannot change here without re-minting every pointer, so the
    ambiguity is DETECTED instead: a caller that gets more than one row must not pick one."""
    pk = list(snap.get("primary_key") or [])
    out = []
    for n, row in enumerate(snap.get("rows") or []):
        if not isinstance(row, dict):
            continue
        key = ",".join(str(row.get(c)) for c in pk) if pk else f"#{n}"
        if key == str(row_id):
            out.append(row)
    return out


def _snapshot_row(snap: dict, row_id: Any) -> Optional[dict]:
    """The ONE row `row_id` names, or None when it names none or (ambiguously) several."""
    rows = _snapshot_rows(snap, row_id)
    return rows[0] if len(rows) == 1 else None


def _quote_ident(name: Any) -> Optional[str]:
    text = str(name or "")
    return f'"{text}"' if _IDENT.match(text) else None


#: Characters that make a quoted SQL string literal dialect-dependent.
_UNSAFE_LITERAL = re.compile(r"['\\\x00-\x1f\x7f]")


def _literal(value: Any) -> Optional[str]:
    """A key value as a quoted literal, or ``None`` when it cannot be written dialect-safely."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value)
    if _UNSAFE_LITERAL.search(text):
        return None
    return f"'{text}'"


def _sql_parse_spec(connection: str, sources_root: Optional[Path]) -> dict:
    try:
        from databridge_mcp_ingest.plans import load_plan

        plan = load_plan(connection, sources_root)
        for step in plan.steps:
            if step.sql is not None:
                return dict(step.sql.parse or {"mode": "json_rows"})
    except Exception:                                            # noqa: BLE001 - no plan
        pass
    return {"mode": "json_rows"}


def _rows_from(result: Any, spec: dict) -> list[dict]:
    structured = getattr(result, "structured", None)
    if isinstance(structured, list):
        return [r for r in structured if isinstance(r, dict)]
    if isinstance(structured, dict) and isinstance(structured.get("rows"), list):
        return [r for r in structured["rows"] if isinstance(r, dict)]
    from databridge_mcp_ingest.parse import parse_output

    return parse_output(getattr(result, "text", "") or "", spec)


def _cell_from_snapshot(pointer, snap: dict, check: dict) -> tuple[Optional[dict],
                                                                    Optional[GenericVerification]]:
    """The cited row, or the UNVERIFIABLE verdict for a pointer that addresses no single row."""
    rows = _snapshot_rows(snap, pointer.row_id)
    column = pointer.column
    if len(rows) > 1:
        return None, GenericVerification(
            _verdict("UNVERIFIABLE"),
            f"the pointer's row id {pointer.row_id!r} names {len(rows)} captured rows (the "
            f"composite-key encoding is ambiguous for these keys), so no single cited value "
            f"exists and no row is chosen", "capture", None,
            capture_check=dict(check, rows_matched=len(rows)))
    if not rows or column not in rows[0]:
        return None, GenericVerification(
            _verdict("UNVERIFIABLE"),
            f"the captured snapshot holds no row {pointer.row_id!r} with a column {column!r}; the "
            f"pointer does not address anything that was captured", "capture", None,
            capture_check=check)
    return rows[0], None


def _offered_mismatch(pointer, value: Any, cited: Any, snap: dict, check: dict,
                      source: Optional[str]) -> Optional[GenericVerification]:
    """R-019 round 2, finding 7: a citation offered for a value its cell never held is a drift —
    on the live path exactly as on the capture path, decided BEFORE any live read."""
    if value is OMITTED or _same_value(value, cited, _column_type(snap, pointer.column)):
        return None
    return GenericVerification(
        _verdict("DRIFT_MATERIAL"), "the captured cell does not hold the value this citation is "
        "offered for", "capture", source, cited_value=_jsonable(cited), cited_value_known=True,
        capture_check=check)


def verify_table_cell_live(pointer, *, gateway: Any, sources_root: Optional[Path] = None,
                           capture_root: Optional[Path] = None,
                           value: Any = OMITTED) -> GenericVerification:
    """Re-read one cited cell through its connection's MCP session and compare.

    The table, the key columns and the cited column are taken from the CAPTURED snapshot the
    pointer names — never from the request — and each must be a plain identifier, so nothing a
    caller typed reaches a statement except the row's own key values, as quoted literals."""
    source = pointer.source_id
    snap, check = _snapshot_for(pointer, capture_root)
    if snap is None:
        status = check.get("status")
        if status in _BINDING_NOTE:
            return GenericVerification(
                _verdict("UNVERIFIABLE"),
                f"{_BINDING_NOTE[status]}; {source!r} is not re-read on its say-so", "capture",
                None, capture_check=check)
        if status == "hash_mismatch":
            return GenericVerification(
                _verdict("DRIFT_MATERIAL"),
                "the captured snapshot this pointer cites no longer hashes to its recorded digest "
                "— the evidence under the citation changed after it was cited",
                "capture", None, capture_check=check)
        if status == "capture_missing":
            return GenericVerification(
                _verdict("VANISHED"),
                "the capture this pointer cites is not in the capture store, so neither the cited "
                "value nor the row's key can be recovered", "capture", None, capture_check=check)
        return GenericVerification(
            _verdict("UNVERIFIABLE"),
            "this pointer names no captured table snapshot, so there is no cited value to "
            "compare a live read against", "capture", None, capture_check=check)

    row, refusal = _cell_from_snapshot(pointer, snap, check)
    if refusal is not None:
        return refusal
    column = pointer.column
    cited = row.get(column)
    mismatch = _offered_mismatch(pointer, value, cited, snap, check, source)
    if mismatch is not None:
        return mismatch
    base = dict(cited_value=_jsonable(cited), cited_value_known=True, capture_check=check)

    pk = list(snap.get("primary_key") or [])
    schema, table = snap.get("schema"), snap.get("table")
    if not pk:
        return GenericVerification(
            _verdict("VERIFIED_AS_OF_CAPTURE"),
            "the table has no primary key, so the row cannot be re-addressed on the live source; "
            "the citation matches its (source-bound) capture", "capture", source,
            live_check={"attempted": False, "declined": "no_primary_key"}, **base)
    names = [column, *pk, table] + ([schema] if schema else [])
    if any(_quote_ident(n) is None for n in names):
        return GenericVerification(
            _verdict("VERIFIED_AS_OF_CAPTURE"),
            "a table or column name is not a plain identifier, so no statement is built from it; "
            "the citation matches its capture", "capture", source,
            live_check={"attempted": False, "declined": "unsafe_identifier"}, **base)
    literals = {c: _literal(row.get(c)) for c in pk}
    if any(v is None for v in literals.values()):
        return GenericVerification(
            _verdict("VERIFIED_AS_OF_CAPTURE"),
            "a key value is NULL or holds a quote, backslash or control character, so it cannot be "
            "written as a literal every SQL dialect reads the same way and no statement is built "
            "from it; the citation matches its capture", "capture", source,
            live_check={"attempted": False, "declined": "unsafe_key_literal"}, **base)
    where = " AND ".join(f"{_quote_ident(c)} = {literals[c]}" for c in pk)
    table_ref = (f"{_quote_ident(schema)}.{_quote_ident(table)}" if schema
                 else _quote_ident(table))
    sql = f"SELECT {_quote_ident(column)} FROM {table_ref} WHERE {where}"

    # R-019 round 2, finding 13: the statement and any source/driver error text stay in the server
    # log under a reference; the caller gets a stable code and that reference.
    live: dict = {"attempted": True}
    try:
        result = gateway.query(source, sql)
    except Exception as exc:                                     # noqa: BLE001
        result = None
        live.update(error="SOURCE_READ_FAILED",
                    ref=error_ref(log, "live re-read failed", exc, source=source, statement=sql))
    if result is not None and getattr(result, "is_error", False):
        live.update(error="SOURCE_RETURNED_ERROR",
                    ref=error_ref(log, "live re-read returned an error", None, source=source,
                                  statement=sql,
                                  answer=(getattr(result, "text", "") or "")[:500]))
        result = None
    if result is None:
        return GenericVerification(
            _verdict("VERIFIED_AS_OF_CAPTURE"),
            "the live source could not be read just now (see live_check); the citation matches "
            "its capture, which is all this verdict claims", "capture", source,
            live_check=live, **base)
    try:
        rows = _rows_from(result, _sql_parse_spec(source, sources_root))
    except Exception as exc:                                     # noqa: BLE001
        live.update(error="UNPARSEABLE_ANSWER",
                    ref=error_ref(log, "live answer could not be parsed", exc, source=source))
        return GenericVerification(
            _verdict("UNVERIFIABLE"),
            "the live read returned something this gateway could not parse as rows, so no "
            "verdict about the live value is issued", "live_source", source,
            live_check=live, **base)
    live["rows"] = len(rows)
    if not rows:
        return GenericVerification(
            _verdict("VANISHED"),
            f"row {pointer.row_id!r} is no longer returned by {source}", "live_source", source,
            live_check=live, **base)
    if len(rows) > 1:
        return GenericVerification(
            _verdict("UNVERIFIABLE"),
            f"the key matched {len(rows)} rows on the live source, so no single current value "
            f"exists to compare", "live_source", source, live_check=live, **base)
    fresh = rows[0]
    if column not in fresh:
        live["columns_returned"] = sorted(str(k) for k in fresh)
        return GenericVerification(
            _verdict("UNVERIFIABLE"),
            f"the live read returned no column named {column!r}, so there is no current value to "
            f"compare", "live_source", source, live_check=live, **base)
    current = fresh[column]
    base.update(current_value=_jsonable(current), current_value_known=True)
    if _same_value(cited, current, _column_type(snap, column)):
        return GenericVerification(
            _verdict("VERIFIED"), f"re-read {source} live: the cell still holds the cited value",
            "live_source", source, live_check=live, **base)
    return GenericVerification(
        _verdict("DRIFT_MATERIAL"),
        f"re-read {source} live: {table}.{column} for row {pointer.row_id!r} no longer holds the "
        f"cited value. Nothing was updated — the graph keeps the cited value until someone "
        f"re-ingests", "live_source", source, live_check=live, **base)


#: `snippet_and_verify` statuses → the lattice. A capture check proves "matches what was
#: captured" at best, so the positive outcome is VERIFIED_AS_OF_CAPTURE, never VERIFIED.
_CAPTURE_STATUS_VERDICT = {
    "verified": "VERIFIED_AS_OF_CAPTURE",
    "hash_mismatch": "DRIFT_MATERIAL",
    "value_not_in_span": "DRIFT_MATERIAL",
    "capture_missing": "VANISHED",
    "span_out_of_range": "UNVERIFIABLE",
    "no_text": "UNVERIFIABLE",
    "no_bytes": "UNVERIFIABLE",
    # The bytes still hash to the digest, but the text the span was read from is not bound to
    # them, so the hash vouches for nothing that was highlighted.
    "text_unbound": "UNVERIFIABLE",
    # The capture's metadata failed `capture.get_meta`'s digest check (swapped or edited).
    "capture_meta_corrupt": "UNVERIFIABLE",
}


def capture_present(pointer, capture_root: Optional[Path] = None) -> bool:
    """Whether the pointer names a capture that is actually in the store."""
    from .source_capture import read_capture

    cap = read_capture(getattr(pointer, "capture_digest", None), capture_root)
    return bool(cap is not None and cap.present)


def verify_by_capture(pointer, *, value: Any = OMITTED, capture_root: Optional[Path] = None,
                      source_registered: bool = False) -> Optional[GenericVerification]:
    """A capture-bound pointer checked against the capture store, or ``None`` when this path
    cannot speak for it (no capture binding; or the capture is absent AND the source is not a
    registered connection — then a VANISHED from here would be a claim about the wrong store).

    `value` is :data:`OMITTED` when the request offered none; an explicit ``None`` is a value."""
    if not getattr(pointer, "capture_digest", None):
        return None
    if str(pointer.kind) == "table_cell":
        snap, check = _snapshot_for(pointer, capture_root)
        if check.get("status") == "capture_missing" and not source_registered:
            return None
        if snap is None:
            status = check.get("status")
            name = {"hash_mismatch": "DRIFT_MATERIAL", "capture_missing": "VANISHED"}.get(
                status, "UNVERIFIABLE")
            note = _BINDING_NOTE.get(status) or f"capture check: {status}"
            return GenericVerification(_verdict(name), note, "capture", None,
                                       capture_check=check)
        row, refusal = _cell_from_snapshot(pointer, snap, check)
        if refusal is not None:
            return refusal
        cited = row.get(pointer.column)
        mismatch = _offered_mismatch(pointer, value, cited, snap, check, pointer.source_id)
        if mismatch is not None:
            return mismatch
        return GenericVerification(
            _verdict("VERIFIED_AS_OF_CAPTURE"), "the captured snapshot still hashes to its digest, "
            "is bound to this source, and holds the cited cell", "capture", pointer.source_id,
            cited_value=_jsonable(cited), cited_value_known=True, capture_check=check)

    from .source_capture import read_capture, snippet_and_verify

    cap = read_capture(pointer.capture_digest, capture_root)
    if cap is not None and cap.present:
        problem, recorded = _source_binding(pointer, cap)
        if problem is not None:
            return GenericVerification(
                _verdict("UNVERIFIABLE"), _BINDING_NOTE[problem], "capture", None,
                capture_check={"capture_digest": f"sha256:{cap.digest}",
                               "verify": {"status": problem},
                               "pointer_source": pointer.source_id,
                               "capture_sources": sorted(recorded)})
        if value is None:
            return GenericVerification(
                _verdict("UNVERIFIABLE"), "a null value cannot be located in a text span, so the "
                "citation cannot be checked for it", "capture", None,
                capture_check={"capture_digest": f"sha256:{cap.digest}",
                               "verify": {"status": "null_value_offered"}})
    out = snippet_and_verify(pointer, value=None if value is OMITTED else value,
                             root=capture_root)
    if out is None:
        return None
    status = (out.get("verify") or {}).get("status")
    if status == "capture_missing" and not source_registered:
        return None
    if status not in _CAPTURE_STATUS_VERDICT:
        log.warning("capture check status %r has no named verdict; reported as UNVERIFIABLE",
                    status)
    name = _CAPTURE_STATUS_VERDICT.get(status, "UNVERIFIABLE")
    detail = (out.get("verify") or {}).get("detail") or f"capture check: {status}"
    snippet = out.get("snippet") or {}
    return GenericVerification(
        _verdict(name), detail, "capture", pointer.source_id,
        cited_value=snippet.get("match"), cited_value_known=snippet.get("match") is not None,
        capture_check=out)


def default_source_gateway():
    """The real `SourceGateway` over `databridge_mcp_ingest` sessions, or ``None``."""
    try:
        from databridge_agent_runtime.tools import McpSourceGateway
    except Exception:                                            # noqa: BLE001 - optional
        return None
    return McpSourceGateway()
