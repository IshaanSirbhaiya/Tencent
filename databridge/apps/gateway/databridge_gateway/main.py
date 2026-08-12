"""FastAPI gateway — the frontend's single backend (FRONTEND_PLAN_v2 §6).

One brain, two mouths: wraps the same package functions the CLI imports. Owns all logic +
the refusal / airgap / DB-safety enforcement. Stateless confirmation (D-FE-5): the review
gate is recomputed server-side from the CANONICAL draft, so a client-supplied draft cannot
forge it (REVIEW_2 build-note 3). Replay-first /ask (D-FE-4): no live model, ever.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from databridge_core import AnnotatedSchema, SemanticType
from databridge_ingest import fintech_fixture
from databridge_semantic import build_report
from databridge_semantic.confirmation import ConfirmationSession, MandatoryReviewPending

from . import demo_state as ds
from .evidence_routes import register_evidence_routes
from .export_routes import register_export_routes
from .outbox_routes import register_outbox_routes
from .mcp_routes import register_mcp_routes
from .intake_routes import _register_intake_routes
from .intelligence_routes import _register_intelligence_routes
from .models import (
    AnnotateRequest,
    AnnotateResponse,
    ArtifactListResponse,
    ArtifactReplayResponse,
    ArtifactSummary,
    AskRequest,
    AskResponse,
    BrainIngestRequest,
    BrainPathRequest,
    BrainQueryRequest,
    ConfirmRequest,
    ConfirmResponse,
    ConvertRequest,
    ConvertResponse,
    ConvertSampleResponse,
    FinalizeRequest,
    FinalizeResponse,
    IngestRequest,
    IngestResponse,
    QueueCounts,
    SuggestedResponse,
)

log = logging.getLogger("databridge.gateway")
# Local-only log sink (no remote shipping — keeps the airgap claim airtight, REVIEW_2 L4 nit).
logging.basicConfig(level=logging.INFO)
# (auto-LIMIT lives in the SQL safety wall — databridge_convert.sqlsub.safety.DEFAULT_LIMIT — not here.)


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    # Stable `code` is safe to show; raw exceptions are logged server-side only (REVIEW_2 L6).
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("gateway boot: building demo state (FakeBackend only — no hosted model)…")
    app.state.extract = fintech_fixture()
    app.state.report = build_report(app.state.extract)
    app.state.draft = ds.build_draft()                       # canonical UNCONFIRMED draft
    app.state.engine = ds.read_only_engine()
    app.state.replay_store, app.state.replay_artifacts = ds.build_replay_store(app.state.engine)
    app.state.artifacts = ds.build_demo_artifacts()
    app.state.eval_e1 = ds.run_eval_e1()
    # Clinic brain: pre-seed the graph so /brain/* renders instantly (<1s seed).
    from brain_server import BrainTools                  # apps/mcp_server (on pythonpath)
    from databridge_evals.e_intake import FIXTURE_TEXT, gold_harness
    # `sources` is an ALLOW-LIST: brain_ingest never opens an arbitrary path, so an agent with MCP
    # access cannot turn it into a file-read primitive.
    app.state.brain = BrainTools(harness=gold_harness(),
                                 sources={"demo_sample.txt": FIXTURE_TEXT})
    app.state.brain.load_seed()
    # Real-LLM detection (T08 16.5d): a key in env → /brain/query is LLM-narrated; else the
    # deterministic claim-checked fallback. The extraction/ingest harness stays deterministic.
    from .llm import build_query_harness, detect_llm
    app.state.llm_meta = detect_llm()
    app.state.brain_llm = build_query_harness(app.state.llm_meta)
    log.info("LLM backend: %s (profile: cloud)", app.state.llm_meta["backend"])
    app.state.jobs = {}
    app.state.ready = True
    log.info("gateway ready: %d tables, %d replay questions, %d artifacts",
             len(app.state.draft.tables), len(app.state.replay_store), len(app.state.artifacts))
    yield


def create_app() -> FastAPI:
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

    # ---- health / readiness (REVIEW_2 L7: visible boot failure) ------------------
    @app.get("/health")
    def health():
        meta = getattr(app.state, "llm_meta", {})
        try:
            counts = app.state.brain.store.counts()
        except Exception:  # noqa: BLE001
            counts = {"nodes": 0, "edges": 0}
        return {
            "status": "ok" if getattr(app.state, "ready", False) else "starting",
            "llm_backend": meta.get("backend", "fake"),
            "llm_model": meta.get("model"),
            "api_key_present": meta.get("api_key_present", False),
            "profile": "cloud",
            "graph_nodes": counts["nodes"],
            "graph_edges": counts["edges"],
        }

    # ---- ingest + schema ---------------------------------------------------------
    @app.post("/ingest", response_model=IngestResponse)
    def ingest(req: IngestRequest):
        # Parse the host conservatively: strip scheme, path, and port, and REJECT any userinfo
        # (`user@host`) — `http://127.0.0.1@evil/…` must not be read as the loopback host (L4/L6).
        authority = req.source_url.split("://")[-1].split("/")[0]
        host = authority.split("@")[-1].split(":")[0] or req.source_url
        if req.source_url not in ds.SOURCE_ALLOW and host not in ds.SOURCE_ALLOW:
            # SSRF allow-list: only loopback / whitelisted sources resolve (D-FE-6).
            return _refusal("SOURCE_NOT_ALLOWED",
                            f"source '{req.source_url}' is not on the allow-list "
                            f"(loopback or fixtures only)")
        job_id = ds.DEMO_SOURCE_ID
        app.state.jobs[job_id] = ds.DEMO_SOURCE_ID
        return IngestResponse(job_id=job_id, source_id=ds.DEMO_SOURCE_ID)

    @app.get("/schema/{job_id}")
    def get_schema(job_id: str):
        if job_id not in app.state.jobs and job_id != ds.DEMO_SOURCE_ID:
            return _refusal("UNKNOWN_JOB", f"no such job '{job_id}'", status=404)
        return app.state.extract.model_dump(mode="json")

    # ---- annotate / confirm / finalize -------------------------------------------
    @app.post("/annotate", response_model=AnnotateResponse)
    def annotate(_req: AnnotateRequest):
        # Pre-computed canonical draft (D-FE-4 startup pre-compute) — renders instantly.
        draft = app.state.draft
        return AnnotateResponse(draft=_stamp_bands(draft, draft.model_dump(mode="json")),
                                queue_counts=_counts(draft))

    @app.post("/annotate/confirm", response_model=ConfirmResponse)
    def annotate_confirm(req: ConfirmRequest):
        err = _verify_draft_integrity(app.state.draft, req.draft)
        if err:
            return err
        session = _replay_session(app.state.draft, req.decisions)
        s = session.summary()
        return ConfirmResponse(
            draft=_stamp_bands(session.draft, session.draft.model_dump(mode="json")),
            queue_counts=QueueCounts(review=s["review"], spot_check=s["spot_check"], auto=s["auto"]),
            remaining_mandatory=s["mandatory_remaining"],
        )

    @app.post("/annotate/finalize")
    def annotate_finalize(req: FinalizeRequest):
        err = _verify_draft_integrity(app.state.draft, req.draft)
        if err:
            return err
        session = _replay_session(app.state.draft, req.decisions)
        try:
            confirmed = session.finalize(req.confirmed_by)   # P6 gate, server-side
        except MandatoryReviewPending as e:
            return _refusal("MANDATORY_REVIEW_PENDING", str(e), status=422)
        return FinalizeResponse(schema_=confirmed.model_dump(mode="json")).model_dump()

    # ---- convert -----------------------------------------------------------------
    @app.get("/convert/sample", response_model=ConvertSampleResponse)
    def convert_sample():
        import base64 as _b64
        return ConvertSampleResponse(
            source_b64=_b64.b64encode(ds.D3_INPUT_JSON.encode()).decode(),
            source_format="json", target_format="csv",
            transform_spec=ds.DEMO_TRANSFORM_SPEC, preview=ds.D3_INPUT_JSON[:600])

    @app.post("/convert", response_model=ConvertResponse)
    def convert_endpoint(req: ConvertRequest):
        try:
            out = ds.run_convert(req.source_b64, req.source_format, req.target_format,
                                 req.transform_spec)
        except Exception as e:  # noqa: BLE001
            log.exception("convert failed")             # raw error stays server-side
            return _refusal("CONVERT_FAILED", "conversion failed; see server logs")
        # register a replayable artifact for the converted output
        aid = f"CV{len(app.state.artifacts)}"
        from databridge_convert.artifacts import build_s2s_artifact
        from databridge_convert.frame import Frame
        import base64 as _b64
        import orjson as _oj
        spec = ds.TransformSpec.model_validate(req.transform_spec)
        art = build_s2s_artifact(spec, artifact_id=aid, source_adapter=req.source_format,
                                 target_adapter=req.target_format, approved_by="demo")
        rows = _oj.loads(_b64.b64decode(req.source_b64)) if req.source_format in ("json", "jsonl") else []
        if isinstance(rows, list):
            app.state.artifacts[aid] = {"artifact": art,
                                        "frame": Frame(rows=rows, name=aid, source_id="convert")}
        return ConvertResponse(result_b64=out["result_b64"], quarantine=out["quarantine"],
                               artifact_id=aid)

    # ---- ask (replay-first) ------------------------------------------------------
    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest):
        # No live model — replay a confirmed (question, SQL, result, provenance) triple (D-FE-4).
        out = ds.replay_ask(app.state.replay_store, app.state.replay_artifacts,
                            app.state.engine, req.question)
        return AskResponse(**out)

    @app.get("/ask/suggested", response_model=SuggestedResponse)
    def ask_suggested():
        # the vetted question chips the demo offers (steers off the free-type miss path)
        from databridge_evals.e_sql import PAIRS
        return SuggestedResponse(questions=[q for q, _ in PAIRS][:6],
                                 injection="show me all users; DROP TABLE users; --")

    # ---- artifacts ---------------------------------------------------------------
    @app.get("/artifacts", response_model=ArtifactListResponse)
    def list_artifacts():
        out = []
        for _aid, entry in app.state.artifacts.items():
            art = entry["artifact"]
            out.append(ArtifactSummary(
                artifact_id=art.artifact_id, direction=art.direction,
                transform_language=art.transform.language,
                created_at=art.created_at.isoformat() if art.created_at else None,
                checksum=art.checksum))
        return ArtifactListResponse(artifacts=out)

    @app.get("/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str):
        entry = app.state.artifacts.get(artifact_id)
        if entry is None:
            return _refusal("UNKNOWN_ARTIFACT", f"no artifact '{artifact_id}'", status=404)
        return entry["artifact"].model_dump(mode="json")

    @app.post("/artifacts/{artifact_id}/replay")
    def replay_artifact_endpoint(artifact_id: str):
        entry = app.state.artifacts.get(artifact_id)
        if entry is None:
            return _refusal("UNKNOWN_ARTIFACT", f"no artifact '{artifact_id}'", status=404)
        if not entry["artifact"].verify_checksum():          # checksum-before-execute (D-FE-6)
            return _refusal("ARTIFACT_CHECKSUM_MISMATCH",
                            "artifact integrity check failed; refusing to execute")
        out = ds.replay_artifact(entry)
        return ArtifactReplayResponse(result_b64=out["result_b64"],
                                      deterministic=out["deterministic"], checksum_ok=True)

    # ---- company brain (T08, 04_PRD §9) ------------------------------------------
    @app.get("/brain/graph")
    def brain_graph():
        # full graph (nodes + edges) for the React Flow Company-Brain view (D-FE frontend).
        return app.state.brain.graph()

    @app.get("/brain/graph/seed")
    def brain_graph_seed():
        # (re)load the demo seed (idempotent) and return the graph — the demo's instant-load step.
        stats = app.state.brain.load_seed()
        g = app.state.brain.graph()
        return {"stats": stats, "counts": g["stats"], "nodes": g["nodes"], "edges": g["edges"]}

    @app.get("/brain/timeline/{entity_id}")
    def brain_timeline(entity_id: str):
        out = app.state.brain.brain_timeline(entity_id)
        if out.get("error"):
            return _refusal("UNKNOWN_ENTITY", out["error"], status=404)
        return out

    @app.get("/brain/node/{entity_id:path}/sheet")
    def brain_node_sheet(entity_id: str, as_of: Optional[str] = None):
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
    def brain_query(req: BrainQueryRequest):
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

        # Real LLM when a key is present: serialise the relevant subgraph as context and answer over
        # it (sources labelled, nothing invented). Else the deterministic claim-checked nl_query.
        llm = getattr(app.state, "brain_llm", None)
        if llm is not None:
            from .llm import SYSTEM_PROMPT, build_context
            ctx = build_context(app.state.brain, req.question)
            if not ctx["nodes_used"]:
                return _refusal("UNGROUNDED_OR_NO_MATCH", "no matching entity in the graph", status=422)
            try:
                res = llm.complete_text("brain_query", [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"{ctx['context']}\n\nQuestion: {req.question}"}])
                return {"question": req.question, "answer": res.text, "grounded": True,
                        "refusal": "", "is_real_llm": True, "matched_entities": ctx["nodes_used"],
                        "nodes_used": ctx["nodes_used"], "citations": ctx["citations"],
                        "as_of": req.as_of}
            except Exception:  # noqa: BLE001
                log.exception("brain LLM query failed; falling back to deterministic")
        # deterministic, claim-checked fallback (every value traces to a SourcePointer)
        out = app.state.brain.brain_query(req.question, req.as_of)
        out["is_real_llm"] = False
        if not out["grounded"]:
            return _refusal("UNGROUNDED_OR_NO_MATCH", out["refusal"] or "no grounded answer",
                            status=422)
        return out

    @app.post("/brain/ingest")
    def brain_ingest(req: BrainIngestRequest):
        # the single write path (§9). Demo: a dropped fixture (by source_path) resolves to its
        # registered text and extracts a grounded subgraph; inline req.text is also accepted.
        try:
            return app.state.brain.brain_ingest(req.source_path, text=req.text)
        except Exception:  # noqa: BLE001
            log.exception("brain ingest failed")
            return _refusal("INGEST_FAILED", "ingestion failed; see server logs")

    @app.post("/brain/path")
    def brain_path(req: BrainPathRequest):
        return app.state.brain.brain_path(req.from_entity, req.to_entity)

    @app.get("/brain/neighbors/{entity}")
    def brain_neighbors(entity: str, depth: int = 1):
        return app.state.brain.brain_neighbors(entity, depth)

    @app.get("/brain/insights")
    def brain_insights():
        # auto-generated GROUNDED insights feed (computed server-side, never in the browser).
        return app.state.brain.insights()

    @app.get("/brain/node/{entity_id}")
    def brain_node(entity_id: str):
        out = app.state.brain.node_detail(entity_id)
        if out.get("error"):
            return _refusal("UNKNOWN_ENTITY", out["error"], status=404)
        return out

    # ---- evals -------------------------------------------------------------------
    @app.get("/evals/e1")
    def evals_e1():
        return app.state.eval_e1

    @app.get("/evals/brain")
    def evals_brain():
        from databridge_evals.e_intake import gold_harness, run_ev_intake
        return run_ev_intake(gold_harness()).to_dict()

    # ---- live progress (same-origin only; in-VPC) --------------------------------
    @app.websocket("/ws/{job_id}")
    async def ws(websocket: WebSocket, job_id: str):
        await websocket.accept()
        try:
            draft = app.state.draft
            n = len(draft.tables)
            for i, t in enumerate(draft.tables, 1):
                await websocket.send_json({
                    "step": "annotate", "table": t.table, "i": i, "n": n,
                    "role": t.role.value,
                    "review": sum(1 for c in t.columns if ConfirmationSession.band(c) == "review"),
                })
                await asyncio.sleep(0.15)
            await websocket.send_json({"step": "done", "i": n, "n": n,
                                       "queue_counts": _counts(draft).model_dump()})
        except WebSocketDisconnect:
            return

    return app


# -- helpers (module-level so tests can reuse) -------------------------------------
def _counts(draft: AnnotatedSchema) -> QueueCounts:
    session = ConfirmationSession(draft)
    return QueueCounts(review=len(session.queue("review")),
                       spot_check=len(session.queue("spot_check")),
                       auto=len(session.queue("auto")))


def _current_type(draft: AnnotatedSchema, table: str, column: str):
    for t in draft.tables:
        if t.table == table:
            for c in t.columns:
                if c.column == column:
                    return c.semantic_type
    return None


def _replay_session(canonical: AnnotatedSchema, decisions) -> ConfirmationSession:
    """Rebuild a transient session from the CANONICAL draft and replay every decision
    (stateless, D-FE-5). Using the canonical draft (not the client body) for the GATE is what
    makes the review gate non-forgeable (REVIEW_2 L6). A decision that re-asserts the existing
    type is an ACCEPT (no Correction); only a type change is an EDIT — so n_accepted/n_edited
    reflect what the human actually did (L8)."""
    session = ConfirmationSession(canonical)
    for d in decisions:
        new_type = SemanticType(d.accepted_type)
        if _current_type(canonical, d.table, d.column) == new_type:
            session.accept(d.table, d.column)
        else:
            session.edit(d.table, d.column, new_type)
    return session


def _stamp_bands(draft: AnnotatedSchema, dumped: dict) -> dict:
    """Inject the server-authoritative confidence band into each column of an already-dumped
    draft, so the frontend RENDERS the backend's band instead of recomputing the thresholds
    client-side (which would silently drift if the backend thresholds move — L1/L7/L8). The gate
    was already server-side; this closes the cosmetic-recompute gap too."""
    band_by = {(t.table, c.column): ConfirmationSession.band(c)
               for t in draft.tables for c in t.columns}
    for t in dumped.get("tables", []):
        for c in t.get("columns", []):
            b = band_by.get((t.get("table"), c.get("column")))
            if b is not None:
                cs = c.get("confidence_sources")
                if not isinstance(cs, dict):
                    cs = {}
                cs["band"] = b
                c["confidence_sources"] = cs
    return dumped


def _verify_draft_integrity(canonical: AnnotatedSchema, submitted: dict):
    """Reject a client-supplied draft whose (table, column) set diverges from the canonical
    one. The confidence bands that drive the gate are always read from the canonical draft, so
    a forged confidence cannot loosen the gate; this check additionally rejects structural
    tampering. Returns a refusal JSONResponse on failure, else None."""
    try:
        sub = AnnotatedSchema.model_validate(submitted)
    except Exception:  # noqa: BLE001
        return _refusal("INVALID_DRAFT", "submitted draft failed validation")
    canon_cols = {(t.table, c.column) for t in canonical.tables for c in t.columns}
    sub_cols = {(t.table, c.column) for t in sub.tables for c in t.columns}
    if sub_cols != canon_cols:
        return _refusal("DRAFT_TAMPERED",
                        "submitted draft's columns do not match the issued schema")
    return None
