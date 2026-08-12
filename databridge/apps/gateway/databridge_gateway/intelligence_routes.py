"""The ``/intelligence/*`` routes — the Business Intelligence surface (PHASE_7 T-42/T-43),
and the agent that reads it (`REMEDIATION.md` §3.1).

Three endpoints. Two of them compute figures; the third lets a member of staff ask a question in
English and get those same figures back as a list of values, each beside the pointer it came from
and each linking to the traversal behind it.

The figure surface exposes two things and refuses to mix them:

* **``graph``** — `databridge_graph.analytics.compute`: counts of *structural facts* about the
  clinic graph, each with the denominator it was counted over and the ids behind it. Until now
  `analytics.py` was 151 lines nothing called.
* **``queue``** — how many intakes in front of the counter cannot commit yet, and what each is
  waiting on, read straight from `ConfirmationState.is_ready_to_commit()`.

**What is deliberately absent: minutes, hours, and money.** `GET /impact` owns the only
time-saving claim this system is entitled to make (Phase 5's per-intake, stage-completed model),
and it is not restated, echoed or re-derived here. A structural count rendered beside a performance
claim reads as one, and that is how a dashboard stops being believable.

**Hard constraint 1, applied to a dashboard.** `blocked` reports *that* an intake needs a human and
*how many* registry candidates it named. It never names a candidate, never orders them, and carries
no field a client could read as a selection — a screen that ranked candidates would be doing
identity verification with extra steps. `analytics.compute` is held to the same rule on its side.

**Fail open, always.** Every aggregate is wrapped: a traversal that raises degrades this page to
"unavailable", exactly as the `ClinicHistory` enrichment in `intake_routes._history()` degrades an
intake. A dashboard must not 500 because an aggregate failed, and a *silent* zero would be worse
than an outage — so the failure is reported as a reason string, never as `0`.

## `POST /intelligence/ask` — and the four things it decides

**Fail open is for the figures. The agent fails CLOSED.** The two rules are opposite on purpose: a
missing aggregate leaves the rest of a page standing, but an answer assembled from a graph that
would not load, or from a model that is not configured, is a sentence a member of staff would act
on. So every degraded path here is a refusal with a code (`_refusal`), never a thinner answer.

1. **Structural reads only.** `agent_tools.build_structural_registry` hands the agent three
   `ClinicStructuralRead` tools and nothing else. No `CandidateRead`, no `PatientScopedRead` — the
   page's contract is structural counts, and a `PatientReadGrant` is an assertion this process is
   not entitled to make (there is no authenticated staff session behind `staff_id`).
2. **`AgentResponse`, never `AgentAnswer`.** `AgentAnswer` serialises the model's raw arguments,
   exception text and unredacted rows while omitting the rendered answer entirely — a route
   returning it would publish everything `render.present` exists to withhold. `answer.public()`
   is the only supported projection, and `test_intelligence_ask.py` pins the exact key set.
3. **Every cited value links to its own subgraph.** Each pointer the tools emit carries a graph
   entity id in `source_id`, so a citation resolves through `GET /intelligence/subgraph/{id}` —
   the drill-down that refuses when it cannot show the evidence.
4. **The question is screened before a model is reached**, by the agent's own deterministic gate
   (`databridge_agent.screen_incoming`), so an identity question is refused with `model_calls == 0`
   and the refusal is a 200 carrying the fixed staff-facing text — it is an answer, not an error.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

log = logging.getLogger("databridge.gateway.intelligence")

#: A front-desk question, not a document. Anything longer is a paste, and a paste is an attempt to
#: get document text into the one message that is NOT framed as untrusted data (`frame.py` frames
#: it anyway — this keeps the prompt bounded as well).
MAX_QUESTION_CHARS = 500


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _parse_as_of(raw: Optional[str]) -> Optional[dt.datetime]:
    """`?as_of=` → datetime, or `ValueError`.

    A malformed value is a refusal, never a silent fall-back to "now": a rewound figure that
    quietly stopped being rewound is a wrong number presented as a right one.
    """
    if raw is None or raw == "":
        return None
    value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # `analytics.compute` normalises a naive value to UTC itself (that was one of T-43's two
    # defects); this is only so the echoed `as_of` says what was actually used.
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _graph_analytics(app: FastAPI, as_of: Optional[dt.datetime]) -> tuple[Optional[dict],
                                                                         Optional[str]]:
    """(payload, unavailable_reason). Never raises — see the module docstring."""
    brain = getattr(app.state, "brain", None)
    if brain is None:
        return None, "the clinic graph is not loaded"
    try:
        from databridge_graph.analytics import compute

        return compute(brain.store, as_of=as_of).to_dict(), None
    except Exception:                                                # noqa: BLE001
        log.exception("graph analytics unavailable; serving the page without them")
        return None, "the graph traversal failed; see server logs"


def _queue_summary(app: FastAPI) -> tuple[Optional[dict], Optional[str]]:
    """What the counter is waiting on, counted over the intakes actually held.

    Reads the same `app.state` the `/intake` queue reads, and the same
    `ConfirmationState.is_ready_to_commit()` the confirm gate reads, rather than re-deriving
    "blocked" from a copy of those rules — a second definition of blocked would drift from the
    gate, and the gate is the one that matters.
    """
    intakes = getattr(app.state, "intakes", None)
    if intakes is None:
        return None, "the intake queue is not loaded"
    try:
        from databridge_intake.ledger import attestation_subject_matches
        from databridge_intake.models import ConfirmationState

        lock = getattr(app.state, "intake_lock", None)
        if lock is not None:
            # Snapshot under the lock: `/impact` learned the hard way that iterating this dict
            # while an upload inserts into it is a live-demo 500, not a theoretical one.
            with lock:
                held = list(intakes.items())
                states = dict(getattr(app.state, "confirmations", {}))
        else:
            held = list(intakes.items())
            states = dict(getattr(app.state, "confirmations", {}))

        blocked, committed = [], 0
        for intake_id, entry in held:
            draft = entry["draft"]
            state = states.get(intake_id) or ConfirmationState(intake_id=intake_id)
            if state.committed:
                committed += 1
                continue
            _, because = state.is_ready_to_commit()
            # R-22. The confirmation was joined to the current draft by `intake_id` alone — and
            # `intake_id` is derived from the document, so it is identical before and after a
            # re-upload re-extracts it. A replaced draft therefore reported `identity_verified:
            # true` off an attestation made about the reading it replaced. Ask the same question
            # the confirm gate asks: is this attestation still ABOUT this draft?
            verified, mismatch = attestation_subject_matches(draft.record, draft.resolution, state)
            if not verified and not because:
                # `is_ready_to_commit()` sees a complete state and reports nothing to wait on,
                # which would put a blocked intake on the board with an empty reason. What it is
                # waiting on is a fresh in-person check.
                because = mismatch
            blocked.append({
                "intake_id": intake_id,
                "document_id": draft.record.document.document_id,
                "document_type": draft.record.document_type.value,
                # HOW MANY candidates the registry returned — never which. Hard constraint 1.
                "candidate_count": len(draft.resolution.candidates),
                "coverage_status": draft.coverage.status.value,
                "eligibility_flags": list(draft.coverage.eligibility_flags),
                "blocked_because": because,
                "identity_verified": verified,
            })
        blocked.sort(key=lambda b: b["intake_id"])
        return {
            "intakes": len(held),
            "committed": committed,
            "blocked": blocked,
            "blocked_count": len(blocked),
        }, None
    except Exception:                                                # noqa: BLE001
        log.exception("intake queue summary unavailable; serving the page without it")
        return None, "the intake queue could not be summarised; see server logs"


class AskRequest(BaseModel):
    """What a member of staff sends. Two fields, and neither can widen what the agent may read.

    There is deliberately **no** `staff_id`, no grant, no patient and no tool list. A route that
    accepted any of those would be letting the caller — which, once a document is in the loop, may
    as well be the document — choose the agent's capabilities (`sealed.PatientReadGrant`: *never
    deserialize a grant from a request*).
    """

    question: str = Field(default="")
    #: The page's own `as of` control, applied server-side to the traversal. Staff-supplied, never
    #: a tool argument — the model has no way to move the window it reads over.
    as_of: Optional[str] = None


def _reasoning_harness(app: FastAPI):
    """A real model when a key is present, the deterministic planner otherwise. Never `None`.

    `build_extraction_harness` is reused rather than reimplemented: `agent_reasoning` routes to the
    `sonnet` alias, and that function's whole job is to remap that alias onto whichever configured
    provider actually holds a key. Its name says "extraction" because that was its first caller;
    what it does is "a Harness for the alias the pipeline uses", which is this one too. Writing a
    second copy here would mean two places to update when the provider story changes.
    """
    from .agent_gold import agent_gold_harness
    from .llm import build_extraction_harness, detect_llm

    detected = getattr(app.state, "llm_meta", None) or detect_llm()
    return build_extraction_harness(detected) or agent_gold_harness()


def _register_intelligence_routes(app: FastAPI) -> None:
    """Attach the BI surface. Read-only: nothing here writes to the graph or the ledger."""

    @app.get("/intelligence")
    def intelligence(as_of: Optional[str] = None):
        """Structural counts over the clinic graph, plus what the counter is waiting on.

        `?as_of=2026-08-01` rewinds the graph figures to what was true on that date. The queue is
        the live queue and is not rewound — `app.state.intakes` is not bi-temporal, and pretending
        otherwise would put a real number next to a fabricated one.
        """
        try:
            parsed = _parse_as_of(as_of)
        except ValueError:
            return _refusal("BAD_AS_OF",
                            f"'{as_of}' is not an ISO-8601 datetime; refusing to guess what "
                            f"instant was meant")
        graph, graph_unavailable = _graph_analytics(app, parsed)
        queue, queue_unavailable = _queue_summary(app)
        return {
            "as_of": parsed.isoformat() if parsed else None,
            "graph": graph,
            "graph_unavailable": graph_unavailable,
            "queue": queue,
            "queue_unavailable": queue_unavailable,
            # Said in the payload, not only in the UI copy, so an agent reading this over MCP in
            # Phase 8 inherits the distinction rather than having to be told it.
            "note": "Counts of structural facts, each with its denominator. Time saved is "
                    "measured separately at GET /impact and is never derived from these.",
        }

    @app.get("/intelligence/subgraph/{entity_id:path}")
    def intelligence_subgraph(entity_id: str, depth: int = 1):
        """The traversal behind one figure's member — how a number on the page is checked.

        Delegates to the same `brain_neighbors` the `/brain` canvas uses; this endpoint exists so
        the BI page can show a subgraph without owning a second copy of the traversal.
        """
        brain = getattr(app.state, "brain", None)
        if brain is None:
            return _refusal("GRAPH_UNAVAILABLE", "the clinic graph is not loaded", status=503)
        depth = max(1, min(depth, 3))                # a BI drill-down, not a graph dump
        try:
            out = brain.brain_neighbors(entity_id, depth)
        except Exception:                                            # noqa: BLE001
            log.exception("subgraph lookup failed for %r", entity_id)
            return _refusal("SUBGRAPH_FAILED", "the traversal failed; see server logs",
                            status=503)
        if out.get("error"):
            return _refusal("UNKNOWN_ENTITY", out["error"], status=404)
        # R-21. This used to swallow a pointer-lookup failure, set `sources = {}` and return the
        # nodes anyway — the drill-down that exists to let a figure be CHECKED, rendering values
        # with the checking apparatus quietly removed. An empty `sources` map is indistinguishable
        # on the page from "this entity genuinely has no citations", so the failure looked like a
        # fact about the graph. If the evidence cannot be loaded there is nothing to show.
        try:
            from brain_server import pointer_to_dict           # apps/mcp_server (on pythonpath)

            sources = {}
            for node in out.get("nodes", []):
                sources[node["id"]] = [pointer_to_dict(p)
                                       for p in brain.store.sources_for(node["id"])]
        except Exception:                                            # noqa: BLE001
            log.exception("pointer lookup failed for %r", entity_id)
            return _refusal(
                "EVIDENCE_UNAVAILABLE",
                "the traversal succeeded but its citations could not be loaded, so the result is "
                "not checkable and is withheld", status=503)
        uncited = sorted(n["id"] for n in out.get("nodes", []) if not sources.get(n["id"]))
        if uncited:
            # `GraphNode` refuses construction without a pointer, so this should be unreachable —
            # which is exactly why listing the ids and returning the values anyway was wrong. If
            # it ever does happen, the invariant this whole page rests on has broken, and the
            # honest response is to say so rather than to render values under a caveat field.
            log.error("subgraph %r returned entities with no citations: %s", entity_id, uncited)
            return _refusal(
                "EVIDENCE_UNAVAILABLE",
                f"{len(uncited)} entity/entities in this traversal have no SourcePointer, so the "
                f"result is not checkable and is withheld", status=503)
        return {**out, "depth": depth, "sources": sources}

    # -- the agent ------------------------------------------------------------------
    from databridge_agent import AgentResponse

    @app.post("/intelligence/ask", response_model=AgentResponse,
              response_model_exclude_none=True)
    def intelligence_ask(payload: AskRequest):
        """A question in, a grounded list of values out — each with the pointer it came from.

        Returns `AgentResponse` and nothing else. The agent's OWN refusals (an identity question,
        a request to send something) come back as a 200 carrying `refused: true` and the fixed
        staff-facing text: they are the honest answer to that question, not a server error. The
        `_refusal` bodies below are the other kind — the surface could not run at all.
        """
        question = (payload.question or "").strip()
        if not question:
            return _refusal("QUESTION_REQUIRED", "ask a question; there is nothing to answer")
        if len(question) > MAX_QUESTION_CHARS:
            return _refusal(
                "QUESTION_TOO_LONG",
                f"a question is {MAX_QUESTION_CHARS} characters or fewer. This box is for a "
                f"question, not for a document — drop documents on /intake, where every value "
                f"extracted from them is grounded and reviewed by a person.")
        try:
            parsed = _parse_as_of(payload.as_of)
        except ValueError:
            return _refusal("BAD_AS_OF",
                            f"'{payload.as_of}' is not an ISO-8601 datetime; refusing to guess "
                            f"what instant was meant")

        brain = getattr(app.state, "brain", None)
        if brain is None:
            # Fails CLOSED, unlike the figures above. An answer is a sentence staff act on, and
            # "the graph was not loaded" is not something a short answer lets them infer.
            return _refusal("GRAPH_UNAVAILABLE",
                            "the clinic graph is not loaded, so there is nothing to read and "
                            "nothing to cite", status=503)

        from databridge_agent import AGENT_TASK_CLASS
        from databridge_core import resolve_model

        try:
            resolve_model(AGENT_TASK_CLASS)
        except Exception:                                            # noqa: BLE001
            log.exception("no model route for %r", AGENT_TASK_CLASS)
            return _refusal(
                "AGENT_MODEL_NOT_CONFIGURED",
                f"no model is routed for '{AGENT_TASK_CLASS}' in config/models.yaml, so this "
                f"question cannot be planned. Routing is configuration; adding the line is the "
                f"fix.", status=503)

        try:
            from databridge_agent import ClinicAgent

            from .agent_tools import build_structural_registry

            registry = build_structural_registry(brain.store, as_of=parsed)
            answer = ClinicAgent(registry, _reasoning_harness(app)).ask(question)
        except Exception:                                            # noqa: BLE001
            log.exception("the clinic agent failed on %r", question[:120])
            return _refusal("AGENT_UNAVAILABLE",
                            "the agent could not complete this read; see server logs. No partial "
                            "answer is returned, because a partial answer is the one thing worse "
                            "than none here", status=503)

        response = answer.public()
        if not response.refused and not response.values:
            # Refuse rather than render an empty answer. The wording is the agent's own
            # code-authored note, so the same sentence appears whichever path produced it.
            from databridge_agent.render import NOTHING_ANSWERED, note_text

            return _refusal("NO_GROUNDED_ANSWER", note_text(NOTHING_ANSWERED))
        return response
