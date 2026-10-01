"""The **orchestrator fleets** — what `/orchestrate` asks.

## Two kinds of fleet, selected by `WALNUT_FLEET`

* **Default — `ConnectionsFleet`** (bottom of this file): the sources THIS deployment connected
  (`databridge_mcp_ingest.list_connections`), the shared graph store and the passage index. The
  asker is the signed-in identity (`auth.require_principal`), and what it may read is the
  OPERATOR's connection grant (R-019 round 2, finding 10) — never authentication alone.
* **`WALNUT_FLEET=<mode>` — `OrchestratorFleet`**: an opt-in fixture estate, built entirely from
  the estate's own declaration (`fixtures/packs/<estate>/fixture_fleet.yaml`, whose `mode` names
  it). Its identity directory, purposes, grants and adapters are that YAML's, validated against
  the closed vocabularies; this module names no estate (R-019 round 2, finding 4).

## The fixture fleet

Mirrors `verify_fleet.py`'s argument one level up: nothing about the loop this surface demonstrates
survives being faked, so this builds a real `databridge_orchestrator.Orchestrator` over real
adapters, admitted by a real `PolicyEngine` built from the estate's own `roles.yaml`. The
declaration wires more than one adapter on purpose — the orchestrator fans a question out to more
than one source, and each source's admission is independent, so one can answer while another comes
back `Withheld` with a reason code.

The declaration parser, the policy builder and the adapter builder are `verify_fleet`'s — one
implementation, two surfaces — and each fleet keeps its own private working copy.

## The offline path is not a stub

With no key set, `/orchestrate` must still answer. `_GoldPlanner` (below) reads the
`AdapterShell`'s own brief (schema, no data) and the caller's question, scores each described
ENTITY by keyword overlap, and proposes reading every field of the best-scoring entity. It never
sees a row and never invents an entity or field the brief did not name — anything it proposes still
goes through `AdapterShell.validate`, exactly as a real model's proposal would.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from .llm import build_query_harness, detect_llm
from .verify_fleet import (
    NOT_GRANTED,
    FixtureFleetDecl,
    build_adapter,
    build_policy,
    error_ref,
    fixture_fleet_decl,
    fixture_fleet_decls,
    import_declared,
)

log = logging.getLogger("databridge.gateway.orchestrator_fleet")

#: databridge/apps/gateway/databridge_gateway/ -> databridge/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

#: The fixture surface this module serves.
SURFACE = "orchestrate"


def _default_orchestrate_decl() -> Optional[FixtureFleetDecl]:
    return next((d for d in fixture_fleet_decls() if d.orchestrate_adapters), None)


_DEFAULT_DECL = _default_orchestrate_decl()
#: The default orchestrate declaration's identity directory, for introspection and tests:
#: identity -> {role, purpose, grant_id, label}. The fleet reads its OWN declaration, not this.
PRINCIPALS: dict[str, dict[str, str]] = (
    {k: v.as_dict() for k, v in _DEFAULT_DECL.directory(SURFACE).items()}
    if _DEFAULT_DECL else {})
#: The declaration's demo persona (`auth.DEV_PRINCIPAL_ID` is written to match it).
DEFAULT_PRINCIPAL: Optional[str] = _DEFAULT_DECL.default_principal if _DEFAULT_DECL else None


class FleetUnavailable(RuntimeError):
    """The fleet could not be built. Carries a CODE-AUTHORED reason (never exception text)."""


# =============================================================================================
# the offline planner — a deterministic stand-in for "a model plans a read"
# =============================================================================================
_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall((text or "").lower()))


def _score(question_tokens: frozenset[str], vocabulary: frozenset[str]) -> int:
    score = len(question_tokens & vocabulary)
    for qt in question_tokens:
        if len(qt) < 4:
            continue
        for vt in vocabulary:
            if qt != vt and len(vt) >= 4 and (qt in vt or vt in qt):
                score += 1
    return score


class _GoldPlanner:
    """A `Backend` (see `databridge_harness.backends.Backend`) that never leaves this process.

    Deterministic, offline, and honest about being neither of those things a real model is: it
    reads the brief `AdapterShell.describe_for_model()` already sent — schema and trust labels,
    no data — picks the entity whose declared name/field vocabulary best overlaps the question,
    and proposes every field of it. `AdapterShell.validate` still screens the result, so a
    malformed proposal from this planner fails exactly the way a bad one from a real model would.
    """

    name = "gold_orchestrator_planner"

    def generate(self, *, model: str, messages: list[dict], json_mode: bool = False,
                 guided_json: Optional[dict] = None, seed: Optional[int] = None,
                 **kwargs: Any) -> Any:
        from databridge_harness.backends import RawCompletion

        brief_text = messages[0]["content"] if messages else "{}"
        question = messages[-1]["content"] if len(messages) > 1 else ""
        try:
            # `AdapterShell.describe_for_model` puts the JSON brief after a prose preamble with no
            # fixed separator between them (just the model's own instructions running straight
            # into it) — so this finds the JSON by its first `{`, rather than assuming a blank
            # line that describe_for_model never promises.
            brief = json.loads(brief_text[brief_text.index("{"):])
        except (ValueError, IndexError):
            brief = {}
        proposal = self._propose(brief, question)
        return RawCompletion(text=json.dumps(proposal), tokens_in=0, tokens_out=0, cost_usd=0.0,
                             model_used=self.name)

    @staticmethod
    def _propose(brief: dict, question: str) -> dict:
        entities: dict = brief.get("entities") or {}
        if not entities:
            return {"capability": "", "entity": "", "escalate": True,
                    "escalation_reason": "the brief describes no entities"}
        tokens = _tokenize(question)
        best_name, best_score = None, -1
        for name, info in entities.items():
            vocab = _tokenize(name)
            for field_name in (info.get("fields") or {}):
                vocab |= _tokenize(field_name)
            s = _score(tokens, vocab)
            if s > best_score:
                best_name, best_score = name, s
        entity = entities[best_name]
        fields = list((entity.get("fields") or {}).keys())
        key = list(entity.get("key") or [])
        capabilities = list(brief.get("capabilities") or [])
        capability = "row_read" if "row_read" in capabilities else (
            "message_read" if "message_read" in capabilities else
            (capabilities[0] if capabilities else ""))
        return {"capability": capability, "entity": best_name, "fields": fields,
               "order_by": key, "limit": 25}



# =============================================================================================
# the fixture fleet
# =============================================================================================
class OrchestratorFleet:
    """The `Orchestrator`, its adapters, and the harness they share — all from one estate's
    declaration. Built lazily."""

    def __init__(self, decl: Optional[FixtureFleetDecl] = None) -> None:
        self._lock = threading.Lock()
        self._decl = decl
        self._orchestrator: Any = None
        self._handles: tuple[Any, ...] = ()
        self._built = False
        self._build_error: Optional[str] = None
        self._workdir: Optional[Path] = None
        self._engine: Any = None
        self._purposes: Any = None
        self._llm: dict = {}

    @property
    def decl(self) -> Optional[FixtureFleetDecl]:
        return self._decl

    @property
    def mode(self) -> Optional[str]:
        return self._decl.mode if self._decl else None

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
                ref = error_ref(log, "orchestrator fleet could not be built", exc)
                self._build_error = (f"the orchestrator fleet could not be built (reference "
                                     f"{ref}; the detail is in the server log)")
                self._built = True
                raise FleetUnavailable(self._build_error) from exc
            self._built = True

    def _build(self) -> None:
        try:
            from databridge_adapter import use_purposes
            from databridge_orchestrator import AdapterHandle, Orchestrator
        except ImportError as exc:                               # pragma: no cover - path guard
            error_ref(log, "orchestrator fleet dependencies are not importable", exc)
            raise FleetUnavailable(
                "the orchestrator fleet's dependencies are not importable from this process. "
                "'packages/orchestrator' and 'packages/adapter' must be on both the root "
                "pyproject `pythonpath` and serve.py::_PACKAGES") from exc

        decl = self._decl
        if decl is None or not decl.orchestrate_adapters:
            raise FleetUnavailable("no fixture-fleet declaration serves /orchestrate for this "
                                   "mode; there is no estate to ask")
        if not decl.sources_yaml.is_file() or not decl.roles_yaml.is_file():
            raise FleetUnavailable("the declared estate has no sources/roles declaration; there "
                                   "is no estate to ask")

        policy, self._purposes = build_policy(decl, SURFACE)
        self._use_purposes = use_purposes
        self._workdir = Path(tempfile.mkdtemp(prefix="walnut-orchestrator-"))
        self._engine = import_declared(decl.loader)(self._workdir / "estate", tables=decl.seed)
        handles = []
        for a in decl.orchestrate_adapters:
            adapter = build_adapter(a, policy=policy, decl=decl, engine=self._engine,
                                    workdir=self._workdir)
            handles.append(AdapterHandle(source_id=a.source_id, adapter=adapter,
                                         description=adapter.describe()))

        self._llm = detect_llm()
        harness = build_query_harness(self._llm)
        if harness is None:
            from databridge_harness import Harness as _Harness

            harness = _Harness(_GoldPlanner())

        self._handles = tuple(handles)
        self._orchestrator = Orchestrator(handles, harness=harness)
        log.info("orchestrator fleet built from %s: %s (llm backend: %s, working copy at %s)",
                 decl.path, [h.source_id for h in handles], self._llm.get("backend"),
                 self._workdir)

    # -- reading ---------------------------------------------------------------------------
    @property
    def orchestrator(self):
        self.ensure_built()
        return self._orchestrator

    def purpose_scope(self):
        """Bind this fleet's purpose vocabulary for one call — context-local, entered fresh per
        operation, so a concurrently-serving request cannot widen another's vocabulary."""
        self.ensure_built()
        return self._use_purposes(self._purposes)

    def _directory(self) -> dict:
        return self._decl.directory(SURFACE) if self._decl else {}

    def principal_for(self, on_behalf_of: str):
        from databridge_adapter import Principal

        self.ensure_built()
        info = self._directory().get(on_behalf_of)
        if info is None:
            # The refusal names nobody else: which identities DO hold grants in this estate is
            # not disclosed to a caller who holds none.
            log.info("fixture fleet: %r holds no grant in the fixture directory", on_behalf_of)
            raise FleetUnavailable(
                "the signed-in identity holds no grant in this fixture estate. An unknown "
                "principal is refused rather than guessed at, same as PolicyEngine.binding_for "
                "does")
        with self.purpose_scope():
            return Principal(actor=self._decl.actor_id, on_behalf_of=on_behalf_of,
                             purpose=info.purpose, grant_id=info.grant_id)

    def status(self, principal_id: Optional[str] = None, **_: Any) -> dict:
        """The fixture fleet, as seen by the SIGNED-IN identity. `principals` lists that one
        identity (if this estate's directory knows it) — never the directory as a menu."""
        try:
            self.ensure_built()
            error = None
        except FleetUnavailable as exc:
            error = str(exc)
        sources = []
        for h in self._handles:
            d = h.description
            sources.append({
                "source_id": h.source_id,
                "kind": getattr(d, "kind", None),
                "pinning_tier": getattr(d, "pinning_tier", None),
                "egress_class": getattr(d, "egress_class", None),
                "capabilities": sorted(getattr(d, "capabilities", ())),
                "entities": [e.name for e in getattr(d, "entities", ())],
            })
        info = self._directory().get(principal_id) if principal_id else None
        me = ({"id": principal_id, "label": info.label, "purpose": info.purpose} if info else
              ({"id": principal_id, "label": f"{principal_id} (signed in)", "purpose": None}
               if principal_id else None))
        can_ask = error is None and info is not None
        reason = error or (None if info is not None else (
            "the signed-in identity holds no grant in this fixture estate's directory"
            if principal_id else "sign in first — a question is always asked AS someone"))
        return {
            "mode": self.mode,
            "available": error is None,
            "reason": error,
            "estate": self._decl.estate_label if self._decl else None,
            "sources": sources,
            "principal": me,
            "principals": {me["id"]: me["label"]} if me else {},
            "default_principal": me["id"] if me else None,
            "can_ask": can_ask,
            "ask_disabled_reason": None if can_ask else reason,
            "suggested_questions": fixture_suggested_questions(self) if error is None else [],
            "llm_backend": self._llm.get("backend"),
        }


def fleet_for(app: Any):
    """The one fleet per app, of the kind `WALNUT_FLEET` selects. Built lazily.

    Default: :class:`ConnectionsFleet` over the registered connections and the shared store.
    `WALNUT_FLEET=<mode>` naming a fixture-fleet declaration: that estate's
    :class:`OrchestratorFleet`."""
    mode = fleet_mode()
    found = getattr(app.state, "orchestrator_fleet", None)
    if mode == CONNECTIONS_MODE:
        if not isinstance(found, ConnectionsFleet):
            def store():
                s = getattr(app.state, "graph_store", None)
                if s is None:
                    s = getattr(getattr(app.state, "brain", None), "store", None)
                return s
            found = ConnectionsFleet(store_getter=store,
                                     passage_search=getattr(app.state, "passage_search", None))
            app.state.orchestrator_fleet = found
        return found
    if not isinstance(found, OrchestratorFleet) or found.mode != mode:
        found = OrchestratorFleet(fixture_fleet_decl(mode, SURFACE))
        app.state.orchestrator_fleet = found
    return found


def dispose_orchestrator_fleet(app: Any) -> None:
    """Drop the working copy. Called on shutdown; safe to call twice."""
    import shutil

    fleet = getattr(app.state, "orchestrator_fleet", None)
    if not isinstance(fleet, OrchestratorFleet):
        return
    try:
        if fleet._engine is not None:                             # noqa: SLF001 - same module
            fleet._engine.dispose()                                # noqa: SLF001
    except Exception:                                             # noqa: BLE001
        log.warning("orchestrator fleet engine dispose failed", exc_info=True)
    if fleet._workdir is not None:                                 # noqa: SLF001
        shutil.rmtree(fleet._workdir, ignore_errors=True)          # noqa: SLF001


# =============================================================================================
# the DEFAULT fleet — the sources this deployment actually connected (batch 2b, workstream R)
# =============================================================================================
#: Env switch between the fleets. Unset (the default) → the registered MCP connections, the shared
#: graph store and the passage index. A value naming a fixture-fleet declaration's `mode` → that
#: estate. Any other value is the default — an unknown name never selects a fixture.
FLEET_ENV = "WALNUT_FLEET"
CONNECTIONS_MODE = "connections"
#: The purpose a signed-in person's question is recorded under on the connections fleet. It is
#: the deployment's declared agent purpose; unset, the trace records the purpose as missing.
PURPOSE_ENV = "WALNUT_AGENT_PURPOSE"

#: What bounds what a signed-in asker sees on the connections fleet, stated in every answer
#: (R-019 finding 7, round 2 finding 10).
ACCESS_MODEL = ("per-principal connection grant: an identity reads only the connections the "
                "operator granted it (WALNUT_FEDERATED_GRANTS — '*' or a list of connection "
                "ids; no grant, nothing is read). Within a granted connection, what is in the "
                "graph and the passage index is bounded by that connection's own source "
                "credentials and declared scope at ingestion")

#: Cells shown per source, and characters per passage cell. A demo-sized answer, stated.
MAX_CELLS_PER_SOURCE = 12
PASSAGE_CELL_CHARS = 280

GRAPH_CHANNEL = "the knowledge graph"
PASSAGE_CHANNEL = "the passage index"


def fleet_mode() -> str:
    raw = os.environ.get(FLEET_ENV, "").strip()
    if raw and raw != CONNECTIONS_MODE and fixture_fleet_decl(raw, SURFACE) is not None:
        return raw
    return CONNECTIONS_MODE


def _pointer_dict(p: Any) -> dict:
    return p.model_dump(mode="json", exclude_none=True)


def _trust_of(p: Any) -> Optional[str]:
    t = getattr(p, "trust", None)
    return getattr(t, "value", t)


def _cell(column: str, value: Any, pointer: Any, *, row_id: Optional[str] = None,
          channel: str = GRAPH_CHANNEL) -> dict:
    return {"column": column, "state": "grounded", "row_id": row_id, "value": value,
            "trust": _trust_of(pointer), "pointer": _pointer_dict(pointer),
            "reason_code": None, "policy_id": None, "appealable": None, "channel": channel}


def _admits(grant: Any, source_id: Optional[str]) -> bool:
    if grant is NOT_GRANTED:
        return False
    return grant is None or (source_id is not None and source_id in grant)


def _pack_questions() -> list[str]:
    """Suggested questions a bound pack declares for this surface (`ui.yaml`: `trials.orchestrate`
    and `demo_question`). Read from the packs, never written here."""
    try:
        import yaml

        from databridge_graph.packs import active_vocabulary, packs_root
    except Exception:                                            # noqa: BLE001
        return []
    out: list[str] = []
    for name in active_vocabulary().packs:
        path = packs_root() / name / "ui.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:                                        # noqa: BLE001
            continue
        trials = (data.get("trials") or {}) if isinstance(data, dict) else {}
        for q in list(trials.get("orchestrate") or []) + [data.get("demo_question")]:
            text = q.get("question") if isinstance(q, dict) else q
            if isinstance(text, str) and text.strip() and text.strip() not in out:
                out.append(text.strip())
    return out[:6]


class ConnectionsFleet:
    """Ask a question of the sources this deployment connected — cited, or honestly not.

    Built from three things that already exist and nothing hard-coded: the registered MCP
    connections, the shared graph store (every value with its own pointer) and the passage index
    (hybrid passages, each a `doc_span` pointer into the capture store).

    **Who may read what** (R-019 round 2, finding 10): the asker is the SIGNED-IN identity, and an
    authenticated identity alone reads nothing. The route resolves the operator's connection grant
    for it (`verify_fleet.connection_grant` — the same directory `/federated` enforces) and passes
    it here; every graph cell and every passage is admitted only when its pointer's source is
    inside that grant, and retrieval is scoped to it before it runs. Within a granted connection,
    what ingestion could read was bounded by that source's own credentials (P12).

    Reads here are from what ingestion already captured, not live calls: a question never makes a
    tool call on somebody's server.
    """

    kind = CONNECTIONS_MODE

    def __init__(self, *, store_getter=None, sources_root: Optional[Path] = None,
                 passage_search=None, ingest_root: Optional[Path] = None) -> None:
        self._store_getter = store_getter or (lambda: None)
        self._sources_root = sources_root
        self._ingest_root = ingest_root
        self._passage_search = passage_search
        self._llm = detect_llm()

    # -- the sources -----------------------------------------------------------------------
    def connections(self) -> tuple[list[Any], list[dict]]:
        """`(connections, problems)` — a declaration that does not parse is a problem, named."""
        try:
            from databridge_mcp_ingest import paths as ingest_paths
            from databridge_mcp_ingest.connections import load_connection
        except Exception as exc:                                 # noqa: BLE001
            ref = error_ref(log, "databridge_mcp_ingest is not importable", exc)
            return [], [{"source_id": None, "problem": "CONNECTIONS_UNAVAILABLE", "ref": ref}]
        base = self._sources_root or ingest_paths.sources_dir()
        if not base.is_dir():
            return [], []
        conns, problems = [], []
        for p in sorted(base.glob("*.yaml")):
            if p.stem.count(".") > 0:
                continue
            try:
                conns.append(load_connection(p.stem, base))
            except Exception as exc:                             # noqa: BLE001
                ref = error_ref(log, "connection declaration did not load", exc, file=p.name)
                problems.append({"source_id": p.stem, "problem": "DECLARATION_INVALID",
                                 "ref": ref})
        return conns, problems

    def _runs(self, source_id: str) -> list:
        from .source_capture import list_runs

        try:
            return list_runs(self._ingest_root, source=source_id)
        except Exception:                                        # noqa: BLE001
            return []

    def _store(self):
        try:
            return self._store_getter()
        except Exception:                                        # noqa: BLE001
            return None

    # -- the signed-in identity -------------------------------------------------------------
    @staticmethod
    def purpose() -> Optional[str]:
        return os.environ.get(PURPOSE_ENV, "").strip() or None

    def principal_view(self, principal_id: str) -> dict:
        return {"id": principal_id, "label": f"{principal_id} (signed in)",
                "purpose": self.purpose()}

    # -- status ------------------------------------------------------------------------------
    def status(self, principal_id: Optional[str] = None, *, grant: Any = NOT_GRANTED) -> dict:
        """`grant` is the caller's connection grant (the route resolves it); the default is NO
        grant, so a caller that forgets to pass one sees nothing (fail closed)."""
        conns, problems = self.connections()
        granted = [c for c in conns if _admits(grant, c.id)]
        sources = []
        for c in granted:
            runs = self._runs(c.id)
            sources.append({
                "source_id": c.id, "title": c.title or c.id,
                "kind": getattr(c.transport, "type", None), "via": c.via, "trust": c.trust,
                "pinning_tier": None, "egress_class": None,
                "capabilities": ["graph.search", "retrieve.search"],
                "entities": [], "ingested": bool(runs),
                "runs": len(runs), "latest_run": runs[-1].run_id if runs else None,
            })
        store = self._store()
        counts = None
        if store is not None:
            try:
                counts = store.counts()
            except Exception:                                    # noqa: BLE001
                counts = None
        graph_has_nodes = bool(counts and counts.get("nodes"))
        has_grant = grant is not NOT_GRANTED
        can_ask = bool(principal_id) and has_grant and (bool(granted) or graph_has_nodes)
        reason = None
        if not principal_id:
            reason = "sign in first — a question is always asked AS someone"
        elif not has_grant:
            reason = ("the signed-in identity holds no connection grant; authentication alone "
                      "does not authorise reading connected sources")
        elif not granted and not graph_has_nodes:
            reason = ("no granted source is connected and the knowledge graph is empty — "
                      "connect a source first, then ask")
        me = self.principal_view(principal_id) if principal_id else None
        return {
            "mode": CONNECTIONS_MODE,
            "available": True,
            "reason": None,
            "estate": "the sources this deployment connected (var/sources) + the shared graph",
            "sources": sources,
            "problems": [p for p in problems if _admits(grant, p.get("source_id"))],
            "graph": counts if has_grant else None,
            "principal": me,
            "principals": {me["id"]: me["label"]} if me else {},
            "default_principal": me["id"] if me else None,
            "can_ask": can_ask,
            "ask_disabled_reason": None if can_ask else reason,
            "suggested_questions": _pack_questions(),
            "llm_backend": self._llm.get("backend"),
        }

    # -- ask ---------------------------------------------------------------------------------
    def ask(self, question: str, *, principal_id: str, trace: Any, max_sources: int = 8,
            grant: Any = NOT_GRANTED) -> dict:
        """Answer from the GRANTED sources only. `grant` defaults to no grant (fail closed); the
        route passes the caller's (`None` = every connection, a set = those connections)."""
        from brain_server import search_passages
        from databridge_graph.query import link_entities

        purpose = self.purpose()
        trace.principal(actor="orchestrator@walnut", on_behalf_of=principal_id, purpose=purpose)
        conns, problems = self.connections()
        registered = {c.id: c for c in conns if _admits(grant, c.id)}
        by_source: dict[str, list[dict]] = {}
        entity_note: dict[str, int] = {}
        withheld = 0

        # 1. the shared graph — every value with its own pointer (R-30), grouped by the source
        #    that pointer names; a value from a source outside the grant is never emitted.
        store = self._store() if grant is not NOT_GRANTED else None
        graph_error = None
        if store is not None:
            trace.capability(source_id="graph", capability="graph.search")
            try:
                from databridge_graph.packs import active_vocabulary

                vocab = active_vocabulary()
                for node in link_entities(store, question, top_k=8):
                    subject = vocab.is_subject(str(node.kind))
                    hidden = set(vocab.identifiers_for(str(node.kind))) if subject else set()
                    if node.sources:
                        ptr = node.sources[0]
                        if _admits(grant, ptr.source_id):
                            by_source.setdefault(ptr.source_id, []).append(
                                _cell(f"{node.kind}", node.label, ptr, row_id=node.id))
                            if subject:
                                entity_note[ptr.source_id] = entity_note.get(ptr.source_id,
                                                                             0) + 1
                        else:
                            withheld += 1
                    for attr in sorted(node.attr_sources):
                        if attr in hidden or node.attrs.get(attr) is None:
                            continue
                        ptr = node.attr_sources[attr]
                        if not _admits(grant, ptr.source_id):
                            withheld += 1
                            continue
                        by_source.setdefault(ptr.source_id, []).append(
                            _cell(f"{node.label} · {attr}", node.attrs[attr], ptr, row_id=node.id))
            except Exception as exc:                             # noqa: BLE001
                ref = error_ref(log, "graph search failed", exc)
                graph_error = {"code": "GRAPH_SEARCH_FAILED", "ref": ref}
                trace.unrecordable(what="graph search", because=f"GRAPH_SEARCH_FAILED ({ref})")
                by_source.clear()
                entity_note.clear()

        graph_searched = store is not None and graph_error is None

        # 2. the passage index — hybrid retrieval, scoped to the GRANTED connections before it
        #    runs (never filtered afterwards only).
        passages, retrieval = [], {"available": False, "reason": "no connection grant"}
        if grant is not NOT_GRANTED:
            trace.capability(source_id="retrieval", capability="retrieve.search")
            scope = (sorted(registered) if registered
                     else (None if grant is None else sorted(grant)))
            passages, retrieval = search_passages(question, search=self._passage_search, k=8,
                                                  sources=scope)
        for p in passages:
            if not _admits(grant, p.get("source_id")):
                withheld += 1
                continue
            ptr_json = p["pointer"]
            label = p.get("locator") or p.get("doc_id") or "passage"
            if p.get("approx_locator"):
                label = f"approx. {label}"
            text = p["text"][:PASSAGE_CELL_CHARS] + ("…" if p.get("truncated") else "")
            by_source.setdefault(p["source_id"], []).append({
                "column": label, "state": "grounded", "row_id": p.get("chunk_id"), "value": text,
                "trust": ptr_json.get("trust"), "pointer": ptr_json, "reason_code": None,
                "policy_id": None, "appealable": None, "channel": PASSAGE_CHANNEL})
        skipped = {}
        for s in ((retrieval.get("coverage") or {}).get("skipped") or []):
            if s.get("source_id"):
                skipped.setdefault(s["source_id"], s.get("reason") or "skipped")
        passages_searched = bool(retrieval.get("available"))
        channels_run = [name for name, ok in ((GRAPH_CHANNEL, graph_searched),
                                              (PASSAGE_CHANNEL, passages_searched)) if ok]
        channels_down = [name for name in (GRAPH_CHANNEL, PASSAGE_CHANNEL)
                         if name not in channels_run]

        # 3. one outcome per source: every granted connection (answered, or why not), then any
        #    other granted source the evidence came from (an upload, a seed).
        outcomes: list[dict] = []
        partial: list[str] = []
        order = list(registered) + sorted(s for s in by_source if s not in registered)
        for source_id in order[:max(1, max_sources)]:
            cells = by_source.get(source_id, [])[:MAX_CELLS_PER_SOURCE]
            conn = registered.get(source_id)
            ingested = bool(self._runs(source_id)) if conn is not None else True
            # R-019 round 2, finding 9: what did NOT cover this source, stated per source.
            missing = list(channels_down)
            if source_id in skipped and PASSAGE_CHANNEL not in missing:
                missing.append(PASSAGE_CHANNEL)
            if cells:
                contributed = [ch for ch in (GRAPH_CHANNEL, PASSAGE_CHANNEL)
                               if any(c.get("channel") == ch for c in cells)]
                status, message = "answered", (
                    f"{len(cells)} cited item(s) from {' and '.join(contributed)}")
                if entity_note.get(source_id):
                    message += (f"; {entity_note[source_id]} of them name a person or other "
                                f"subject — shown as candidates, never as an identification")
                if missing:
                    why = (f" ({skipped[source_id]})" if source_id in skipped
                           and PASSAGE_CHANNEL in missing else "")
                    message += (f"; {' and '.join(missing)} did not search this source{why}, so "
                                f"this is a partial answer (reduced coverage)")
                    partial.append(source_id)
            elif conn is not None and not ingested:
                status, message = "escalated", (
                    "registered but never ingested — nothing from this source is in the graph or "
                    "the passage index yet, so this answer may be incomplete (reduced coverage)")
            elif source_id in skipped:
                status, message = "escalated", (
                    f"not searched: {skipped[source_id]} — this answer may be incomplete "
                    f"(reduced coverage)")
            elif not channels_run:
                status, message = "escalated", (
                    "not searched: neither the knowledge graph nor the passage index could be "
                    "searched just now — this answer may be incomplete (reduced coverage)")
            elif channels_down:
                status, message = "answered", (
                    f"nothing in this source matched — but {' and '.join(channels_down)} could "
                    f"not be searched, so this is reduced coverage, not a clean miss")
                partial.append(source_id)
            else:
                status, message = "answered", "searched; nothing in this source matched"
            for c in cells:
                # Every EMITTED cell's pointer is recorded, so the trace can reconstruct each one.
                cited = c.get("pointer") or {}
                trace.pointer(origin="graph", source_id=cited.get("source_id"),
                              kind=cited.get("kind"), trust=cited.get("trust"))
                trace.cell(state="grounded", field_path=f"{source_id}.{c['column']}",
                           trust=c.get("trust"), source_id=source_id)
            caps = [cap for ch, cap in ((GRAPH_CHANNEL, "graph.search"),
                                        (PASSAGE_CHANNEL, "retrieve.search"))
                    if any(c.get("channel") == ch for c in cells)]
            outcomes.append({"source_id": source_id, "status": status, "message": message,
                             "capability": " + ".join(caps) or None,
                             "entity": conn.title if conn is not None and conn.title else None,
                             "fields": [], "cells": cells, "denial": None})
        left_out = order[len(outcomes):]
        if left_out:
            trace.note(key="sources_not_shown", text=", ".join(left_out))

        answered = any(o["cells"] for o in outcomes)
        not_searched = [o["source_id"] for o in outcomes if o["status"] == "escalated"]
        if channels_down:
            trace.note(key="channels_not_searched", text=", ".join(channels_down))
        # R-019 round 2, finding 9: the note claims only the channels that actually ran.
        searched = " and ".join(channels_run) if channels_run else "nothing (no channel ran)"
        note = (f"Asked as {principal_id}: searched {searched} over {len(registered)} granted "
                f"connected source(s)")
        if channels_down:
            note += f"; not searched: {' and '.join(channels_down)} (reduced coverage)"
        if not_searched:
            note += f"; reduced coverage — not searched: {', '.join(not_searched)}"
        if partial:
            note += f"; partial: {', '.join(partial)}"
        if retrieval.get("offline_embedding"):
            note += f"; passages ranked with {retrieval.get('embedding_label') or 'the offline embedding'}"
        return {
            "question": question,
            "principal": {"actor": "orchestrator@walnut", "on_behalf_of": principal_id,
                          "purpose": purpose},
            "sources_considered": order,
            "selection_note": note,
            "answered": answered,
            "outcome": "answered" if answered else ("escalated" if not_searched else "answered"),
            "sources": outcomes,
            "coverage": {"not_searched": not_searched, "not_shown": left_out,
                         "partial": partial,
                         "retrieval": retrieval, "graph_error": graph_error,
                         "graph_searched": graph_searched,
                         "passages_searched": passages_searched,
                         "channels_not_searched": channels_down,
                         "declaration_problems": [p for p in problems
                                                  if _admits(grant, p.get("source_id"))],
                         # Items that matched but come from a source outside the caller's
                         # grant: a count, never their values or their sources' names.
                         "withheld_outside_grant": withheld,
                         "access": ACCESS_MODEL},
            "mode": CONNECTIONS_MODE,
        }


def fixture_suggested_questions(fleet: "OrchestratorFleet") -> list[str]:
    """Questions DERIVED from what the fixture adapters describe (entity + two non-key fields),
    so the panel never carries a hard-coded list that drifts from the estate."""
    out: list[str] = []
    for h in getattr(fleet, "_handles", ()):
        for e in getattr(h.description, "entities", ())[:1]:
            key = set(e.key or ())
            fields = [f.name.replace("_", " ") for f in e.fields if f.name not in key][:2]
            if fields:
                out.append(f"what is the {e.name.replace('_', ' ')} {' and '.join(fields)}")
    return out
