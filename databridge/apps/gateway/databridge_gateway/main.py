"""FastAPI gateway — the frontend's single backend (FRONTEND_PLAN_v2 §6).

One brain, two mouths: wraps the same package functions the CLI imports. Owns all logic +
the refusal / airgap enforcement. The DataBridge-era fakes (/ingest, /schema, /annotate*, /ws,
/ask, /convert/sample) and the canned boot state behind them were removed on 2026-10-01.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import demo_state as ds
from .auth import (AuthenticatedPrincipal, install_auth_handler, log_auth_state_at_boot,
                   require_principal)
from .builder_routes import register_builder_routes
from .connector_routes import register_connector_routes
from .graph_routes import register_graph_routes
from .orchestrator_routes import register_orchestrator_routes
from .evidence_routes import register_evidence_routes
from .export_routes import register_export_routes
from .outbox_routes import register_outbox_routes
from .mcp_routes import register_mcp_routes
from .intake_routes import _register_intake_routes
from .intelligence_routes import _register_intelligence_routes
from .materialise_routes import register_materialise_routes
from .ontology_routes import register_ontology_routes
from .pack_routes import register_pack_routes
from .trace_routes import register_trace_routes
from .sources_routes import register_sources_routes
from .agents_routes import register_agents_routes
from .adapter_agents_routes import register_adapter_agents_routes
from .automation_routes import register_automation_routes
from .verify_fleet import dispose_fleet
from .orchestrator_fleet import dispose_orchestrator_fleet
from .verify_routes import register_verify_routes
from .models import (
    ArtifactListResponse,
    ArtifactReplayResponse,
    ArtifactSummary,
    BrainIngestRequest,
    BrainPathRequest,
    BrainQueryRequest,
    ConvertRequest,
    ConvertResponse,
)

log = logging.getLogger("databridge.gateway")
# Local-only log sink (no remote shipping — keeps the airgap claim airtight, REVIEW_2 L4 nit).
logging.basicConfig(level=logging.INFO)
# (auto-LIMIT lives in the SQL safety wall — databridge_convert.sqlsub.safety.DEFAULT_LIMIT — not here.)


class _UngroundedNarration(Exception):
    """The LLM's answer named a value its evidence does not contain, or asserted an identity."""


#: What the LLM branch of `/brain/query` checks before it serves a narration (R-019 round 2).
NARRATION_CLAIM_CHECK = ("identity_screen+value_tokens_in_evidence+check_claims"
                         "+named_terms_in_evidence")
#: A citation tag the grounded narrator appends after a bound value (`llm.GroundedNarrator`).
_CITATION_TAG = re.compile(r"\s*\[C\d+\]")


def candidate_refusal_code(store, candidates) -> str:
    """``SUBJECT_NEEDS_SELECTION`` when any candidate is a declared SUBJECT kind (a name landed
    on a person-like record: a human selects it, even when there is only one), else
    ``AMBIGUOUS_ENTITY`` (a shared name that selects no subject). Decided from the candidates'
    kinds in the store and the bound vocabulary — never from the refusal's wording."""
    try:
        from databridge_graph.packs import active_vocabulary  # noqa: PLC0415
        vocab = active_vocabulary()
        for cid in candidates or []:
            row = store.open_node_row(cid)
            if row is not None and vocab.is_subject(str(row["kind"])):
                return "SUBJECT_NEEDS_SELECTION"
    except Exception:  # noqa: BLE001
        log.exception("could not classify refusal candidates; reporting AMBIGUOUS_ENTITY")
    return "AMBIGUOUS_ENTITY"


def candidate_details(store, candidates) -> list[dict]:
    """Each candidate id as something a person can choose from: its label, kind, the source it
    came from and its own pointers. A SUBJECT candidate also carries the subject annotation
    (``requires_human_selection: True``) — listing it is presenting a candidate, never choosing.
    An id with no open node is listed bare rather than dropped."""
    out: list[dict] = []
    try:
        from brain_server import subject_annotation                    # noqa: PLC0415
        from databridge_graph.packs import active_vocabulary            # noqa: PLC0415
        from databridge_graph.store import row_to_node                  # noqa: PLC0415
        vocab = active_vocabulary()
    except Exception:  # noqa: BLE001
        log.exception("could not describe refusal candidates")
        return [{"id": str(c)} for c in candidates or []]
    for cid in candidates or []:
        row = store.open_node_row(cid)
        if row is None:
            out.append({"id": cid})
            continue
        node = row_to_node(store, row)
        kind = str(getattr(node.kind, "value", node.kind))
        entry = {"id": cid, "label": node.label, "kind": kind,
                 "source_ids": sorted({p.source_id for p in node.sources}),
                 "pointers": [p.model_dump(mode="json", exclude_none=True)
                              for p in node.sources[:3]],
                 "subject": vocab.is_subject(kind)}
        if entry["subject"]:
            entry.update(subject_annotation(node.kind))
        out.append(entry)
    return out


def candidate_refusal(store, candidates, message: str) -> JSONResponse:
    """The 422 for a reference this system will not resolve on its own, with every candidate
    described (N-14). Two cases, decided from the candidates' kinds, never the wording:

    * ``SUBJECT_NEEDS_SELECTION`` — a name landed on a person-like record: the message stays the
      identity refusal, and a human selects (hard constraint 1);
    * ``AMBIGUOUS_ENTITY`` — separate NON-subject records share the name. That is not an identity
      determination, so the message says what it is: separate, unmerged records, listed so the
      caller can ask about one of them by id.
    """
    candidates = list(candidates or [])
    code = candidate_refusal_code(store, candidates)
    details = candidate_details(store, candidates)
    if code == "AMBIGUOUS_ENTITY":
        labels = " / ".join(sorted({repr(d["label"]) for d in details if d.get("label")}))
        sources = ", ".join(sorted({s for d in details for s in d.get("source_ids", [])}))
        message = (f"{len(candidates)} separate records carry the name {labels or 'asked about'}"
                   f" (from {sources or 'unknown sources'}). They are not merged, and this "
                   f"answer path does not decide whether they are one entity. Ask again naming "
                   f"one of the ids in `candidates`.")
    return JSONResponse(status_code=422, content={"refusal": {
        "code": code, "message": message, "candidates": candidates,
        "candidate_details": details,
        "nearest_alternative": "Ask again naming one of the ids in `candidates`."}})


def _with_route(resp, route_info: dict):
    """Attach the routing marker to a graph-path answer or refusal (``routed_to: "graph"``)."""
    if isinstance(resp, dict):
        return {**resp, **route_info}
    if isinstance(resp, JSONResponse):
        import json                                                    # noqa: PLC0415
        try:
            body = json.loads(bytes(resp.body).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return resp
        if isinstance(body, dict):
            return JSONResponse(status_code=resp.status_code, content={**body, **route_info})
    return resp


NARRATION_CLAIM_SCOPE = ("values, quantities and named terms are checked against the evidence "
                         "(never the question); identity assertions are refused; relations "
                         "stated in prose are not machine-verified")


#: A "value token": a run of word characters and joiners. Only those containing a digit (dates,
#: amounts, ids, counts, codes) are checked — the claims a narration can invent that a reader
#: cannot tell from real ones by reading the prose.
_VALUE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9./:_-]*")


def _norm_value(tok: str) -> str:
    return tok.strip(".,;:!?)(\"'-/").lower()


def unsupported_value_tokens(answer: str, evidence: str) -> list[str]:
    """Every digit-bearing token of ``answer`` not found in ``evidence`` (case-insensitive,
    surrounding punctuation ignored). Computed, never looked up: the check is the membership of
    the answer's own tokens in the evidence the model was given.

    The QUESTION is not evidence (R-019 round 2, main #1): "is the balance 9000?" does not make
    9000 a supported value. A narration that repeats a value only the question holds is dropped
    and the claim-checked deterministic answer is served instead."""
    haystack = (evidence or "").lower()
    known = {_norm_value(t) for t in _VALUE_TOKEN.findall(haystack)}
    out: list[str] = []
    for tok in _VALUE_TOKEN.findall(answer or ""):
        norm = _norm_value(tok)
        if not any(ch.isdigit() for ch in norm):
            continue
        if norm in known or norm in haystack:
            continue
        out.append(tok.strip(".,;:!?)(\"'-/"))
    return out


#: A digit-bearing value, a date, or a percent — the forms `check_claims` may find in an answer.
_EVIDENCE_FORM = re.compile(r"\d{4}-\d{2}-\d{2}|-?\d[\d,]*(?:\.\d+)?%?")


def narration_claim_violations(answer: str, evidence: str) -> list[str]:
    """`check_claims` over the model's narration, the gate `nl_query` applies to its own answer.

    The allowed forms are computed from the EVIDENCE the model was given — every value-shaped
    form in it, never listed, and never the question (main #1). What this adds to
    `unsupported_value_tokens` is what that check cannot see because it carries no digit — a
    spelled-out magnitude ("a hundred"), a unit-worded amount, a hedged quantity."""
    from databridge_convert.generate.claim_check import check_claims  # noqa: PLC0415

    allowed = set(_EVIDENCE_FORM.findall(evidence or ""))
    return [v.token for v in check_claims(answer or "", facts=[], allow=allowed).violations]


#: A capitalised term: the names, organisations, codes and titles a narration can state as fact.
#: The first word of a sentence, line or list item is skipped (it is capitalised by grammar).
_NAMED_TERM = re.compile(r"\b[A-Z][A-Za-z0-9'&-]*[A-Za-z0-9]\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\n\s*(?:[-*•]\s*|\d+[.)]\s*)?)$")


def narration_unsupported_terms(answer: str, evidence: str) -> list[str]:
    """Every NAMED term of ``answer`` (a capitalised word that does not open a sentence) that
    the evidence does not contain (case-insensitive). A narration may only name what its
    evidence names — an invented person, organisation, code or document is caught here even
    though it carries no digit (R-019 round 2, main #2). Computed from the answer's own words."""
    text = answer or ""
    haystack = (evidence or "").lower()
    out: list[str] = []
    for m in _NAMED_TERM.finditer(text):
        term = m.group(0)
        if _SENTENCE_START.search(text[: m.start()]):
            continue
        if term.lower() in haystack or term in out:
            continue
        out.append(term)
    return out


#: Identity ASSERTIONS in generated prose (hard constraint 1). The question screen
#: (`guardrails.check_question`) stops the question being asked; these stop an answer to an
#: innocent question ("summarise these candidates") from stating a match. Generic phrasing of
#: "X is/was identified as / is the same person as / is a match for" — no domain branching.
_IDENTITY_ASSERTIONS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(?:is|are|was|were|has been|have been)\s+(?:\w+\s+){0,2}(?:identified|"
               r"verified|confirmed|authenticated)\s+as\b", re.I),
    re.compile(r"\bidentit(?:y|ies)\b.{0,40}?\b(?:is|are|was|were|has been|have been)\s+"
               r"(?:\w+\s+){0,2}(?:confirmed|verified|matched|established|validated|"
               r"authenticated)\b", re.I),
    re.compile(r"\b(?:confirmed|verified|matched|established|validated)\s+(?:\w+\s+){0,2}"
               r"identit(?:y|ies)\b", re.I),
    re.compile(r"\b(?:same|identical)\s+(?:person|individual|subject|people)\b", re.I),
    re.compile(r"\b(?:is|are|was|were)\s+(?:a|an|the)\s+(?:definite\s+|confirmed\s+|exact\s+|"
               r"positive\s+)?match\s+(?:for|to|with)\b", re.I),
)


#: Generic words for "a subject" in prose; the enabled packs' own subject kinds are added at
#: call time (`_subject_nouns`), so no domain's word is written here.
_GENERIC_SUBJECT_NOUNS = ("person", "individual", "subject", "holder", "customer", "client",
                          "member")


def _subject_nouns() -> list[str]:
    nouns = set(_GENERIC_SUBJECT_NOUNS)
    try:
        from databridge_graph.packs import active_vocabulary  # noqa: PLC0415
        for sk in active_vocabulary().subject_kinds.values():
            nouns.add(str(sk.kind).replace("_", " ").lower())
    except Exception:  # noqa: BLE001  (a vocabulary that cannot load still screens the generic)
        log.exception("identity screen: pack subject kinds unavailable; generic nouns only")
    return sorted(nouns, key=len, reverse=True)


def _subject_patterns() -> list[re.Pattern]:
    nouns = "|".join(re.escape(n) for n in _subject_nouns())
    return [
        # "X is the same / right / verified <subject>"
        re.compile(r"\b(?:is|are|was|were)\s+(?:\w+\s+){0,2}(?:the\s+)?(?:same|right|correct|"
                   r"real|actual|matching|verified|confirmed)\s+(?:" + nouns + r")\b", re.I),
        # "Alice is the <subject>" — but not "is the <subject> record / named in ..."
        re.compile(r"\b(?:is|are|was|were)\s+the\s+(?:" + nouns + r")\b(?!\s+(?:record|"
                   r"records|field|fields|file|details|entry|entries|named|listed|described|"
                   r"referred|mentioned))", re.I),
    ]


def narration_identity_claims(answer: str) -> list[str]:
    """The identity-match assertions in generated prose (the answer-side screen). Also runs the
    question screen over the answer, so a narration phrased like a forbidden question is caught
    by the same rules. Any hit drops the narration — the system presents candidates, a human
    decides who someone is (CLAUDE.md hard constraint 1)."""
    from databridge_intake.guardrails import check_question  # noqa: PLC0415

    text = re.sub(r"\s+", " ", answer or "")
    hits = [m.group(0) for pat in (*_subject_patterns(), *_IDENTITY_ASSERTIONS)
            for m in pat.finditer(text)]
    screened = check_question(text)
    if screened.refused:
        hits.append(f"identity screen: {screened.reason}")
    return hits


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    # Stable `code` is safe to show; raw exceptions are logged server-side only (REVIEW_2 L6).
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("gateway boot")
    # No canned state is built here any more (2026-10-01, ROADMAP §3 item 17). The fintech
    # annotation draft, the /ask replay dictionary and the pre-registered D3 artifact that used
    # to be built at boot fed routes that answered from them regardless of the request; those
    # routes are removed and their fixtures are no longer wired in (they stay on disk).
    # `artifacts` holds only what a real `/convert` call registers.
    app.state.artifacts = {}
    # Every read and write of the registry holds this lock (R-019 round 2, main #8): the sync
    # handlers run on a thread pool, and iterating while another thread inserts raises.
    app.state.artifacts_lock = threading.Lock()
    # ONE shared graph store (KNOWLEDGE_SYSTEM_PRINCIPLES §6 Batch 1, contract C1). Opened once,
    # file-backed (`WALNUT_GRAPH_DB_PATH`, default `<databridge>/var/graph/workspace.db`), on the
    # real clock. `/graph`, `/evidence`, `/intelligence` and `/brain` all read THIS object —
    # `app.state.brain.store is app.state.graph_store` — and the ingest CLI / MCP writer write to
    # the same file from their own processes. GraphStore caches nothing, so their commits are
    # visible on the next read; `app.state.reload_graph()` re-opens the file for the one case a
    # live connection cannot follow (the file replaced by an atomic publish).
    from brain_server import (                           # apps/mcp_server (on pythonpath)
        BrainTools, demo_seed_enabled, demo_seed_sources, graph_db_path, open_shared_store)
    from databridge_evals.e_intake import gold_harness

    app.state.graph_db_path = graph_db_path()
    app.state.graph_store = open_shared_store(app.state.graph_db_path)
    try:
        # Real-LLM detection (T08 16.5d): a key in env → /brain/query is LLM-narrated and
        # /brain/ingest extracts with the real model (contract C5); no key, or
        # DATABRIDGE_FORCE_GOLD=1, → the deterministic gold backends.
        from .llm import build_extraction_harness, build_query_harness, detect_llm
        app.state.llm_meta = detect_llm()
        app.state.brain_llm = build_query_harness(app.state.llm_meta)
        ingest_harness = build_extraction_harness(app.state.llm_meta)
        app.state.brain_ingest_backend = "real" if ingest_harness is not None else "gold"
        # The enabled packs' demo seed is OPT-IN (contract C2): the shared store holds the user's
        # own sources, and a demo appearing in it uninvited is data nobody put there. WHICH seed
        # and which demo documents is the packs' declaration (`demo_seed.yaml`, Codex f2 #6) —
        # the gateway names no pack and imports no fixture.
        seeded = demo_seed_enabled()
        sources: dict = {}
        if seeded:
            sources.update(demo_seed_sources())
        # `sources` is an ALLOW-LIST: brain_ingest never opens an arbitrary path, so an agent with MCP
        # access cannot turn it into a file-read primitive.
        # Without a model the harness is the GOLD one, and a gold harness is an answer written
        # down for ONE fixture: it is only ever applied to a registered source (a pack-declared
        # demo document). `/brain/ingest` refuses INLINE text when the
        # backend is gold (R-019 main #1), so the gold draft can never be passed off as an
        # extraction of a document a person dropped in.
        # FXB2 (R-020 f2 #7): `/brain/ingest` reports the extraction route it used, which is
        # decided independently of the brain (query) route.
        app.state.brain = BrainTools(app.state.graph_store,
                                     harness=ingest_harness or gold_harness(), sources=sources,
                                     extraction_route={
                                         "backend": app.state.brain_ingest_backend,
                                         "routing": app.state.llm_meta.get("routing", {})})
        if seeded:
            app.state.brain.load_seed()
        app.state.reload_graph = app.state.graph_store.refresh
        log.info("graph store: %s (demo seed %s) — %s", app.state.graph_db_path,
                 "loaded" if seeded else "off", app.state.graph_store.counts())
        log.info("LLM backend: %s (profile: cloud); /brain/ingest extracts with the %s harness",
                 app.state.llm_meta["backend"], app.state.brain_ingest_backend)
        # Knowledge platform (BUILD_PLAN §6 U): the shared harness, MCP ingest facade, source gateway
        # factory, retriever, adapter-agent manager (over THIS store) and the trigger engine — built
        # once, on `app.state.platform`. The engine's loop starts here and stops below.
        from .platform_runtime import start_platform
        await start_platform(app)
        app.state.ready = True
        log.info("gateway ready")
        yield
    finally:
        await _shutdown(app)


async def _shutdown(app: FastAPI) -> None:
    """Release everything the lifespan acquired, each step on its own (R-019 main #8).

    Runs from a ``finally``, so a boot that failed half-way (``start_platform`` raising after the
    store was opened) still closes the store; and each step is isolated, so one failing cleanup
    cannot skip the rest — a trigger engine left running or a SQLite handle left pinning the file
    on Windows is exactly the leak this exists to prevent.

    Round 2 (main #5): the store close sits in an OUTER ``finally``, so neither a failing import
    of ``platform_runtime`` nor a ``CancelledError`` (a ``BaseException``, which ``except
    Exception`` does not catch) during ``stop_platform`` can skip it. A cancellation is held,
    the remaining steps and the close still run, and it is re-raised at the end."""
    cancelled: Optional[BaseException] = None
    try:
        async def _stop() -> None:
            # Stop the trigger engine first: a firing still running must finish (or be drained)
            # while the store it writes to is still open. Imported HERE, inside its own step,
            # so an import failure is one failed step, not an escape past the close.
            from .platform_runtime import stop_platform
            await stop_platform(app)

        steps = [
            ("stop_platform", _stop),
            # The evidence fleet / orchestrator build private working copies of a synthetic
            # estate on first use; a temp directory per server run that nothing removes is a leak.
            ("dispose_fleet", lambda: dispose_fleet(app)),
            ("dispose_orchestrator_fleet", lambda: dispose_orchestrator_fleet(app)),
        ]
        for name, fn in steps:
            try:
                out = fn()
                if hasattr(out, "__await__"):
                    await out
            except asyncio.CancelledError as exc:
                log.warning("shutdown step %s was cancelled; finishing cleanup first", name)
                cancelled = exc
            except Exception:  # noqa: BLE001
                log.exception("shutdown step %s failed; continuing with the rest", name)
    finally:
        # Release the shared store's file handle LAST (everything above may still write to it).
        store = getattr(app.state, "graph_store", None)
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                log.exception("closing the shared graph store failed")
    if cancelled is not None:
        raise cancelled


def create_app() -> FastAPI:
    # R-015: build (and loudly log) the auth mechanism's state now, at boot, rather than letting
    # it happen lazily on whichever request reaches a gated route first. See `auth.py`.
    log_auth_state_at_boot()
    app = FastAPI(title="DataBridge Gateway", version="0.1.0", lifespan=lifespan)
    # CORS closed to the local dev origin only (D-FE-6).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"], allow_headers=["*"],
    )

    # ---- intake: the front-desk pipeline ----------------------------------------
    # The whole product surface. `POST /intake/{id}/confirm` is the gate: nothing reaches the
    # evidence ledger without a named staff member attesting to an in-person identity check.
    _register_intake_routes(app)

    # ---- evidence: the provenance chain, made visible --------------------------
    # Read-only. Source -> pointer -> extracted value -> graph node -> committed ledger
    # row, with the raw SourcePointer shown rather than summarised. This is the
    # Governance & Safety criterion rendered instead of asserted.
    register_evidence_routes(app)
    register_mcp_routes(app)
    register_export_routes(app)
    # The send gate over HTTP: drafts, the two human decisions, and the trigger for the
    # model-free dispatcher. This process imports no transport — execution is a subprocess.
    register_outbox_routes(app)

    # ---- intelligence: the graph, interpreted ----------------------------------
    # Counts of STRUCTURAL FACTS, each with its denominator and each walkable to the
    # subgraph behind it. It deliberately claims no minutes and no money: `/impact`
    # owns the only saving this system asserts, and a structural count sitting beside
    # a performance number starts reading as one.
    _register_intelligence_routes(app)

    # ---- platform surfaces (PHASE_PLAN Phase 5) ---------------------------------
    # `/packs` serves the pack directory, so the frontend's bundled registry is a fallback rather
    # than the source of truth. `/trace` is the audit surface: it holds only what requests
    # recorded as they ran, and there is no route that can write one. `/ontology` is the pack's
    # node/edge library plus the growth proposals, and it refuses to auto-approve a determination
    # or subject-touching kind at the API boundary rather than trusting the UI to. `/materialise`
    # runs Layer 3 over the shared platform (MAT): the materialiser is attached at boot in
    # `platform_runtime.start_platform`; shapes without a builder are listed `built: false`.
    register_pack_routes(app)
    register_trace_routes(app)
    register_ontology_routes(app)
    register_materialise_routes(app)
    # ---- knowledge platform (BUILD_PLAN_KNOWLEDGE_PLATFORM §6 U) --------------------------
    # `/sources` connects MCP servers and runs approved crawl plans in the background;
    # `/adapter-agents` manages them; `/agents` compiles English into governed agents, runs them
    # and holds the approval inbox; `/automation` is the trigger engine the lifespan hosts;
    # `/platform/catalogue` is what the English compiler sees. Every route requires a principal,
    # and every approval is attributed to that principal, never to a name in a request body.
    register_sources_routes(app)
    register_adapter_agents_routes(app)
    register_agents_routes(app)
    register_automation_routes(app)
    # `/federated/ask`: the main agent delegates sub-questions to adapter agents in parallel and a
    # deterministic combine engine joins/aggregates with derived pointers (FED).
    from .federated_routes import register_federated_routes
    register_federated_routes(app)
    # `/activity/*`: read-only merge of agent runs, delegations, automation firings, approvals and
    # adapter-agent syncs for the Activity & trace page (ACT). Every route requires a principal.
    from .activity_routes import register_activity_routes
    register_activity_routes(app)
    # `/evidence/verify` re-executes ONE pointer against a real adapter and answers from the
    # verdict lattice; `/evidence/impact` asks the reverse pointer index what relies on it, and
    # keeps "unknown" apart from "nothing" because a drift alarm that conflates them is worse
    # than none. `/builder` compiles an ADAPTER declaration (`load_mcp_declaration`) and an AGENT
    # declaration (`databridge_agentdef.compile_agent`) for real; where the agent compiler is not
    # importable it answers 501 naming what has to land, never a fabricated refusal.
    register_verify_routes(app)
    register_builder_routes(app)
    register_graph_routes(app)
    register_orchestrator_routes(app)
    register_connector_routes(app)

    # ---- health / readiness (REVIEW_2 L7: visible boot failure) ------------------
    @app.get("/health")
    def health():
        meta = getattr(app.state, "llm_meta", {})
        graph_error = None
        try:
            counts = app.state.brain.store.counts()
        except Exception as exc:  # noqa: BLE001
            # A store that cannot be counted is NOT healthy (R-019 main #11): zero counts under
            # "ok" read as "an empty, working store" and kept traffic flowing to a broken one.
            log.exception("health: the shared graph store could not be read")
            counts = {"nodes": None, "edges": None}
            graph_error = type(exc).__name__
        ready = getattr(app.state, "ready", False)
        status = "degraded" if (ready and graph_error) else ("ok" if ready else "starting")
        body = {
            "status": status,
            "graph_error": graph_error,
            "llm_backend": meta.get("backend", "fake"),
            "llm_model": meta.get("model"),
            # per extraction alias: configured vs effective model, and whether it fell back (R-020)
            "llm_routing": meta.get("routing", {}),
            "api_key_present": meta.get("api_key_present", False),
            "profile": "cloud",
            "graph_nodes": counts["nodes"],
            "graph_edges": counts["edges"],
            "graph_db_path": getattr(app.state, "graph_db_path", None),
            "brain_ingest_backend": getattr(app.state, "brain_ingest_backend", None),
        }
        return body if status != "degraded" else JSONResponse(status_code=503, content=body)

    # ---- removed legacy fakes (2026-10-01, ROADMAP §3 item 17) --------------------
    # `/ingest` ignored its input and returned a fixed source id; `/schema/{job}` returned a boot
    # fixture; `/annotate` (+ `/confirm`, `/finalize`) returned a draft built once at boot;
    # `/ws/{job_id}` replayed that draft on a timer as "progress"; `/ask` looked questions up in a
    # boot-built dictionary; `/convert/sample` served hard-coded rows. The real capabilities:
    # connecting a source and approving its mapping is `/sources`; asking is `/brain/query` and
    # `/agents`. Their paths now 404 rather than answer from data written down in advance.

    # ---- convert -----------------------------------------------------------------
    @app.post("/convert", response_model=ConvertResponse)
    def convert_endpoint(req: ConvertRequest,
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        # Authenticated like every other write (R-019 main #3): it registers a replayable
        # artifact, and that artifact's `approved_by` is the signed-in principal who ran it —
        # never the literal "demo" it used to be stamped with.
        try:
            out = ds.run_convert(req.source_b64, req.source_format, req.target_format,
                                 req.transform_spec)
        except Exception as e:  # noqa: BLE001
            log.exception("convert failed")             # raw error stays server-side
            return _refusal("CONVERT_FAILED", "conversion failed; see server logs")
        # Register a replayable artifact over the SAME frame the conversion read — built by the
        # source format's registered adapter, so every format `/convert` accepts yields a real
        # artifact (the old path parsed JSON only: CSV got an id that 404'd and JSONL 500'd after
        # a successful conversion). The entry keeps the requested TARGET format and the digest of
        # the bytes returned, so a replay is serialised the same way and compared with the
        # original (round 2, main #4).
        import uuid as _uuid

        from databridge_convert.artifacts import build_s2s_artifact
        spec = ds.TransformSpec.model_validate(req.transform_spec)
        with _artifacts_lock():
            # The FULL uuid, reserved under the lock and re-drawn on the (astronomically
            # unlikely) collision, so an id is unique by construction — never a later conversion
            # silently answering with an earlier one's artifact (round 2, main #9).
            aid = f"CV-{_uuid.uuid4().hex}"
            while aid in app.state.artifacts:
                aid = f"CV-{_uuid.uuid4().hex}"
            app.state.artifacts[aid] = None                   # reserved; filled below
        try:
            art = build_s2s_artifact(spec, artifact_id=aid, source_adapter=req.source_format,
                                     target_adapter=req.target_format,
                                     approved_by=principal.principal_id)
            entry = ds.artifact_entry(art, source_b64=req.source_b64,
                                      source_format=req.source_format,
                                      target_format=req.target_format,
                                      result_b64=out["result_b64"], name=aid)
        except Exception:  # noqa: BLE001
            with _artifacts_lock():
                app.state.artifacts.pop(aid, None)            # release the unused reservation
            log.exception("convert: artifact frame could not be rebuilt")
            return _refusal("ARTIFACT_NOT_REGISTERED",
                            "the conversion ran but its source could not be re-read to register "
                            "a replayable artifact; see server logs", status=500)
        with _artifacts_lock():
            app.state.artifacts[aid] = entry
        return ConvertResponse(result_b64=out["result_b64"], quarantine=out["quarantine"],
                               artifact_id=aid)

    def _artifacts_lock() -> threading.Lock:
        lock = getattr(app.state, "artifacts_lock", None)
        if lock is None:                                      # an app mounted without lifespan
            lock = app.state.artifacts_lock = threading.Lock()
        return lock

    def _artifact(artifact_id: str) -> Optional[dict]:
        with _artifacts_lock():
            return app.state.artifacts.get(artifact_id)       # None while only reserved

    # ---- artifacts ---------------------------------------------------------------
    # Gated (R-019 / DP): an artifact holds the transform and its replay re-emits the converted
    # data, so listing, reading and replaying need the same principal /convert does.
    @app.get("/artifacts", response_model=ArtifactListResponse)
    def list_artifacts(_p: AuthenticatedPrincipal = Depends(require_principal)):
        out = []
        with _artifacts_lock():                               # a snapshot, never a live view
            entries = [e for e in app.state.artifacts.values() if e is not None]
        for entry in entries:
            art = entry["artifact"]
            out.append(ArtifactSummary(
                artifact_id=art.artifact_id, direction=art.direction,
                transform_language=art.transform.language,
                created_at=art.created_at.isoformat() if art.created_at else None,
                checksum=art.checksum))
        return ArtifactListResponse(artifacts=out)

    @app.get("/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        entry = _artifact(artifact_id)
        if entry is None:
            return _refusal("UNKNOWN_ARTIFACT", f"no artifact '{artifact_id}'", status=404)
        return entry["artifact"].model_dump(mode="json")

    @app.post("/artifacts/{artifact_id}/replay")
    def replay_artifact_endpoint(artifact_id: str,
                                 _p: AuthenticatedPrincipal = Depends(require_principal)):
        entry = _artifact(artifact_id)
        if entry is None:
            return _refusal("UNKNOWN_ARTIFACT", f"no artifact '{artifact_id}'", status=404)
        if not entry["artifact"].verify_checksum():          # checksum-before-execute (D-FE-6)
            return _refusal("ARTIFACT_CHECKSUM_MISMATCH",
                            "artifact integrity check failed; refusing to execute")
        out = ds.replay_artifact(entry)
        # `result_b64` is in the artifact's REQUESTED target format, and `deterministic` is true
        # only when execute == replay AND both equal the bytes the original conversion returned
        # (round 2, main #4); `matches_original` and `target_format` say so explicitly.
        body = ArtifactReplayResponse(result_b64=out["result_b64"],
                                      deterministic=out["deterministic"],
                                      checksum_ok=True).model_dump()
        return {**body, "target_format": out["target_format"],
                "matches_original": out["matches_original"]}

    # ---- company brain (T08, 04_PRD §9) ------------------------------------------
    # EVERY /brain route requires a principal (E2E F-25): they disclose the shared graph — the
    # user's own sources — and an ungated handler also ran far enough to 500 instead of 401.
    install_auth_handler(app)             # AuthRequired -> the shape-preserving 401/503 body

    @app.get("/brain/graph")
    def brain_graph(limit: Optional[int] = None, _p: AuthenticatedPrincipal = Depends(require_principal)):
        # A BOUNDED view (nodes + edges) for the React Flow Company-Brain view: at most `limit`
        # nodes (default BrainTools.GRAPH_NODE_LIMIT), most-connected first; `stats.truncated`
        # says whether this is the whole store. Empty store → empty lists, not an error.
        return app.state.brain.graph(limit=limit)

    @app.post("/brain/reload")
    def brain_reload(_p: AuthenticatedPrincipal = Depends(require_principal)):
        # Re-open the shared store file (see GraphStore.refresh) and report its counts. Not needed
        # to see another process's commits — only to follow a file REPLACED under the gateway.
        # Gated like every other write route: it re-opens the shared store under every reader and
        # discloses the store's path and counts, so an anonymous caller gets 401, not the file.
        return {"graph_db_path": app.state.graph_db_path, "counts": app.state.reload_graph()}

    @app.post("/brain/graph/seed")
    def brain_graph_seed(_p: AuthenticatedPrincipal = Depends(require_principal)):
        # (re)load the demo seed (idempotent) and return the graph — the demo's instant-load step.
        # Opt-in like the boot-time seed. It WRITES, so it needs a principal (R-019 main #6) and
        # it is POST only (round 2, main #7): a GET — a prefetch, a retry, a cache revalidation —
        # must never mutate the shared store. `brainApi.seed` already sends POST.
        from brain_server import DEMO_SEED_ENV, demo_seed_enabled

        if not demo_seed_enabled():
            return _refusal("DEMO_SEED_DISABLED",
                            f"the demo seed is opt-in: set {DEMO_SEED_ENV}=1 to load it",
                            status=409)
        stats = app.state.brain.load_seed()
        g = app.state.brain.graph()
        return {"stats": stats, "counts": g["stats"], "nodes": g["nodes"], "edges": g["edges"]}

    @app.get("/brain/timeline/{entity_id}")
    def brain_timeline(entity_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        out = app.state.brain.brain_timeline(entity_id)
        if out.get("error"):
            return _refusal("UNKNOWN_ENTITY", out["error"], status=404)
        return out

    @app.get("/brain/node/{entity_id:path}/sheet")
    def brain_node_sheet(entity_id: str, as_of: Optional[str] = None,
                         _p: AuthenticatedPrincipal = Depends(require_principal)):
        """The node's **evidence sheet** — what the graph knows about one entity, as markdown.

        Assembled entirely from stored values, pointers and edges: no model runs in this path, so
        the sheet cannot narrate something the graph does not hold. It is both what a person sees
        on clicking a node and the grounding unit the assistant reads, which is the point — an
        answer should be checkable against the same document the reader was shown.

        `{entity_id:path}` because node ids contain colons and slashes
        (`patient:pat:4e487c99807b`, `document:chit_01_meridian_referral.txt`).
        """
        from databridge_graph.node_sheet import node_markdown

        when: Optional[dt.datetime] = None
        if as_of:
            try:
                when = dt.datetime.fromisoformat(as_of)
            except ValueError:
                return _refusal("BAD_AS_OF", f"'{as_of}' is not an ISO-8601 timestamp")
            if when.tzinfo is None:
                # The store holds nothing but UTC-aware instants, and comparing a naive one
                # against them raises TypeError deep inside the renderer — which surfaces as a
                # 500 rather than as the refusal this endpoint is careful to give everywhere
                # else. `?as_of=2026-05-01` is a perfectly reasonable thing for a person to
                # type, so it is normalised rather than rejected.
                when = when.replace(tzinfo=dt.timezone.utc)
        sheet = node_markdown(app.state.brain.store, entity_id, as_of=when)
        if sheet is None:
            return _refusal("UNKNOWN_ENTITY", f"no open node '{entity_id}'", status=404)
        return {"entity_id": entity_id, "as_of": as_of, "markdown": sheet}

    @app.post("/brain/query")
    def brain_query(req: BrainQueryRequest, _p: AuthenticatedPrincipal = Depends(require_principal)):
        # THE GUARDRAIL, FIRST. Identity-verification questions are refused deterministically,
        # before any model is reached — the system prompt forbids them too, but a prompt is a
        # request and this is the guarantee (CLAUDE.md §Hard constraints 1).
        from databridge_intake.guardrails import check_question

        screened = check_question(req.question)
        if screened.refused:
            return JSONResponse(status_code=422, content={"refusal": {
                "code": screened.code, "message": screened.answer,
                "nearest_alternative": "Ask what a document authorises, or which registry "
                                       "candidates it matches."}})

        # `as_of` is refused rather than echoed (R-019 main #7). Neither branch below can answer
        # "as of" an instant — `build_context` and `BrainTools.brain_query` both read the store's
        # CURRENT state — so returning the requested instant beside a present-day answer claimed a
        # temporal boundary that was never applied. The node sheet (`/brain/node/{id}/sheet
        # ?as_of=`) and `/brain/timeline/{id}` are the time-travel surfaces that do honour it.
        if req.as_of:
            return JSONResponse(status_code=422, content={"refusal": {
                "code": "AS_OF_UNSUPPORTED",
                "message": "/brain/query answers from the graph's current state only; it cannot "
                           "answer as of an earlier instant.",
                "nearest_alternative": "Ask without as_of, or read /brain/node/{id}/sheet?as_of= "
                                       "or /brain/timeline/{id} for history."}})

        # N-14 (E2E run 2): a question that needs several sources or arithmetic over rows is
        # DELEGATED — rules first, the harness router only for an undecided one — to the same
        # federated service `/federated/ask` uses, under the CALLER's operator grant. Everything
        # else stays on the graph + passage path below, and says which route it took.
        from .brain_delegation import ROUTE_GRAPH, delegate

        routed = delegate(app, req.question, _p, router=getattr(app.state, "brain_llm", None))
        if "payload" in routed:
            return routed["payload"]
        route_info = {"routed_to": ROUTE_GRAPH, "route": routed["route"]}
        if routed.get("attempt"):
            route_info["delegation_attempt"] = routed["attempt"]
        return _with_route(_brain_graph_answer(req), route_info)

    def _brain_graph_answer(req: BrainQueryRequest):
            # Real LLM when a key is present: serialise the relevant subgraph as context and answer over
            # it (sources labelled, nothing invented). Else the deterministic claim-checked nl_query.
            llm = getattr(app.state, "brain_llm", None)
            llm_dropped: Optional[dict] = None
            if llm is not None:
                from .llm import SYSTEM_PROMPT, build_context
                ctx = build_context(app.state.brain, req.question)
                # An ambiguous reference is refused HERE, before the model is reached — the same
                # place and for the same reason as the identity screen above. `build_context` returns
                # no context in that case, so falling through would send an empty prompt; and the
                # deterministic fallback below would answer the question the LLM branch just declined
                # to answer, which would make the refusal a formality. `candidates` travels with it so
                # the caller can ask again with an id (CLAUDE.md §Hard constraints 1).
                if ctx.get("refusal"):
                    return candidate_refusal(app.state.brain.store, ctx.get("candidates", []),
                                             ctx["refusal"])
                # Passage-only context (hybrid retrieval, no graph match) is still grounded evidence.
                # With NO context the model is not called. When `build_context` says why
                # (`deterministic_reason`: E2E run 3 — a WITHHELD item, P-06, or a vague question,
                # P-13) the deterministic branch below answers it; otherwise it is a no-match.
                if not ctx["nodes_used"] and not ctx["citations"]:
                    if not ctx.get("deterministic_reason"):
                        return _refusal("UNGROUNDED_OR_NO_MATCH", "no matching entity in the graph",
                                        status=422)
                    llm = None
            if llm is not None:
                try:
                    res = llm.complete_text("brain_query", [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"{ctx['context']}\n\nQuestion: {req.question}"}])
                    # THE IDENTITY SCREEN, ANSWER SIDE (R-019 round 2, main #2). The question screen
                    # above stops the forbidden question; this stops an innocent question's answer
                    # from asserting a match ("Alice is the <subject>"). Deterministic, before the
                    # value checks, and a hit is never served whatever else the answer holds.
                    # The narrator's own `[C<n>]` citation tags are recomputed binding markers, not
                    # claims: they are stripped before the checks below (a tag like "C1" carries a
                    # digit and a capital, and would otherwise read as an invented value or name).
                    checked = _CITATION_TAG.sub("", res.text or "")
                    identity = narration_identity_claims(checked)
                    if identity:
                        log.warning("brain LLM answer dropped: identity assertion: %s", identity[:5])
                        raise _UngroundedNarration(identity, "the answer asserted an identity match; "
                                                             "identity is decided by a named human")
                    # Checked against the EVIDENCE only — never the question (main #1): a value or a
                    # name the caller typed is not thereby supported.
                    unsupported = unsupported_value_tokens(checked, ctx["context"])
                    # ...the same deterministic claim check the deterministic answer passes
                    # (R-019-real-connections deferred #3): spelled or hedged quantities included...
                    unsupported += [t for t in narration_claim_violations(checked, ctx["context"])
                                    if t not in unsupported]
                    # ...and every NAMED term (a person, organisation, code, title) must be one the
                    # evidence names (main #2): invention without a digit is caught too.
                    unsupported += [t for t in narration_unsupported_terms(checked, ctx["context"])
                                    if t not in unsupported]
                    if unsupported:
                        # The narration states a value, a quantity or a name its evidence does not
                        # hold: the model's answer is dropped and the claim-checked deterministic
                        # answer below is served instead (R-019 main #2).
                        log.warning("brain LLM answer dropped: claims not in its evidence: %s",
                                    unsupported[:10])
                        raise _UngroundedNarration(unsupported)
                    return {"question": req.question, "answer": res.text, "grounded": True,
                            "refusal": "", "is_real_llm": True, "matched_entities": ctx["nodes_used"],
                            "nodes_used": ctx["nodes_used"], "citations": ctx["citations"],
                            "passages": ctx.get("passages", []), "as_of": req.as_of,
                            # What "grounded" means on THIS branch, stated rather than implied: the
                            # evidence is pointer-backed, and every value, quantity and named term of
                            # the narration occurs in it, and it asserts no identity match. The
                            # RELATIONS the prose draws between them are not machine-verified; the
                            # deterministic branch binds each claim to a SourcePointer.
                            "claim_check": NARRATION_CLAIM_CHECK,
                            "claim_check_scope": NARRATION_CLAIM_SCOPE,
                            # FXB (R-020-EF2): the narrator's value→[C<n>]→pointer bindings, as
                            # structured data beside the inline tags.
                            "values": list(getattr(res, "bindings", None) or []),
                            # FXB2 (R-020 f2 #2): sentences dropped for binding to no fact, and the
                            # subject records the served text names (each followed by the mark).
                            "dropped_sentences": list(getattr(res, "dropped_sentences", None)
                                                      or []),
                            "subject_mentions": list(getattr(res, "subject_mentions", None) or [])}
                except _UngroundedNarration as dropped:
                    reason = (dropped.args[1] if len(dropped.args) > 1
                              else "claims not supported by the evidence")
                    llm_dropped = {"reason": reason, "violations": list(dropped.args[0])}
                except Exception:  # noqa: BLE001
                    log.exception("brain LLM query failed; falling back to deterministic")
            # deterministic, claim-checked fallback (every value traces to a SourcePointer)
            out = app.state.brain.brain_query(req.question, req.as_of)
            out["is_real_llm"] = False
            if llm_dropped is not None:
                # flagged, not silently swapped: the caller sees that the model's answer was refused
                out["llm_answer_dropped"] = llm_dropped
            if not out["grounded"] and out.get("answer_kind") in ("withheld", "needs_clarification"):
                # Not a grounded answer and not a no-match: the item asked about was WITHHELD by the
                # ingestion screen (named, with its rule), or the question needs a question back
                # (`questions` / `question_items`, the `/federated/ask` needs-input shape).
                return out
            if not out["grounded"]:
                # `nl_query` returns the candidate ids with its ambiguity refusal, and this handler
                # was flattening every refusal into one code and dropping them — so the deterministic
                # branch refused correctly and then withheld the only thing that lets the caller get
                # an answer. "Ask again with one of these ids" is not advice if the ids never arrive.
                if out.get("candidates"):
                    return candidate_refusal(app.state.brain.store, out["candidates"],
                                             out["refusal"])
                return _refusal("UNGROUNDED_OR_NO_MATCH", out["refusal"] or "no grounded answer",
                                status=422)
            return out

    @app.post("/brain/ingest")
    def brain_ingest(req: BrainIngestRequest,
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        # the single write path (§9). Demo: a dropped fixture (by source_path) resolves to its
        # registered text and extracts a grounded subgraph; inline req.text is also accepted.
        # It writes the SHARED store every surface reads, so an anonymous caller gets 401
        # (R-019 main #5).
        if req.text is not None and getattr(app.state, "brain_ingest_backend", None) != "real":
            # No model: the harness is the gold one, a fixed answer for one registered fixture.
            # Applying it to a document a person dropped in would "extract" whatever the gold
            # draft names and the text happens to contain — a recalled answer, not a computed
            # one (R-019 main #1). Say so instead.
            return _refusal("NO_EXTRACTION_MODEL",
                            "extracting a dropped document needs a model, and none is configured "
                            "(no API key, or DATABRIDGE_FORCE_GOLD=1). Registered sources still "
                            "ingest; connect a source under /sources for model-free capture.",
                            status=503)
        log.info("brain ingest by %s: %s", principal.principal_id, req.source_path)
        try:
            return app.state.brain.brain_ingest(req.source_path, text=req.text)
        except Exception:  # noqa: BLE001
            log.exception("brain ingest failed")
            return _refusal("INGEST_FAILED", "ingestion failed; see server logs")

    @app.post("/brain/path")
    def brain_path(req: BrainPathRequest, _p: AuthenticatedPrincipal = Depends(require_principal)):
        return app.state.brain.brain_path(req.from_entity, req.to_entity)

    @app.get("/brain/neighbors/{entity}")
    def brain_neighbors(entity: str, depth: int = 1, _p: AuthenticatedPrincipal = Depends(require_principal)):
        return app.state.brain.brain_neighbors(entity, depth)

    @app.get("/brain/insights")
    def brain_insights(_p: AuthenticatedPrincipal = Depends(require_principal)):
        # auto-generated GROUNDED insights feed (computed server-side, never in the browser).
        return app.state.brain.insights()

    @app.get("/brain/node/{entity_id}")
    def brain_node(entity_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        out = app.state.brain.node_detail(entity_id)
        if out.get("error"):
            return _refusal("UNKNOWN_ENTITY", out["error"], status=404)
        return out

    # ---- evals: UNWIRED (R-019 round 2, main #3; founder: unwire, never delete) ---------
    # `/evals/e1` and `/evals/brain` ran a gold FakeBackend over a fixed fixture and returned its
    # predetermined score: the same answer whatever was asked, which is a canned result however
    # it is labelled. They are no longer routes (they 404). The code is kept, unrouted, as
    # `ds.run_eval_e1` and `evals_brain_report` below, for tests/CI.
    return app


def evals_brain_report() -> dict:
    """The brain-ingest plumbing score over the gold backend. NOT a route (R-019 round 2,
    main #3): a CI/test helper only, never served as a runtime answer."""
    from databridge_evals.e_intake import gold_harness, run_ev_intake
    return {**run_ev_intake(gold_harness()).to_dict(), **ds.E1_PROOF_LABEL,
            "note": ("Scores the brain-ingest extraction plumbing against a deterministic "
                     "gold backend over a fixed synthetic fixture. It is not a measurement "
                     "of any model.")}
