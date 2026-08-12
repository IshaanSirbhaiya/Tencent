"""The ``/intelligence/*`` routes — the Business Intelligence surface (PHASE_7 T-42/T-43).

This is the read-only foundation Phase 8's agent will sit on. It exposes two things and refuses to
mix them:

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
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger("databridge.gateway.intelligence")


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
            blocked.append({
                "intake_id": intake_id,
                "document_id": draft.record.document.document_id,
                "document_type": draft.record.document_type.value,
                # HOW MANY candidates the registry returned — never which. Hard constraint 1.
                "candidate_count": len(draft.resolution.candidates),
                "coverage_status": draft.coverage.status.value,
                "eligibility_flags": list(draft.coverage.eligibility_flags),
                "blocked_because": because,
                "identity_verified": bool(state.identity_checked_in_person_by),
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
