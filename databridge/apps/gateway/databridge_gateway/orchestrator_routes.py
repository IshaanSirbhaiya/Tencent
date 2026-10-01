"""``/orchestrate`` — ask for something, and watch the fleet go and get it.

**The loop this closes.** Everything needed for it already existed and nothing connected it: the
adapter Cores read under policy and emit grounded cells; `AdapterShell` turns a task into one
validated `CapabilityRequest` or an escalation; `PolicyEngine` admits or refuses; `TraceRecorder`
records each step. What was missing was the **main agent** — the thing that takes a question,
chooses which sources can answer it, hands each one's Shell a brief, and assembles what comes back.
There was no `class Orchestrator` in this repository, and no live code path constructed a Shell.

So an operator could inspect evidence, and could browse the graph, and could not *ask for
anything*. This module is that gap, closed by three pieces that each already own their own file:

* `databridge_orchestrator.Orchestrator` (`packages/orchestrator`) — the loop itself: select
  sources from their own declared capability matrix, hand each a Shell brief, take the request or
  the escalation, let the adapter's own Policy-backed `resolve()` decide, assemble cells.
* `orchestrator_fleet.OrchestratorFleet` — the real adapters an estate's fixture-fleet
  declaration names, admitted by a real `PolicyEngine`, mirroring `verify_fleet.py`'s
  construction one level up (two Cores instead of one, so the fan-out is visibly multi-agent).
* This module — the two HTTP routes, and nothing else. It builds a `Principal` from a named,
  directory-known identity (never from a client-supplied purpose or grant — spine D5: a purpose is
  a fact about WHY the read happens, not a field an HTTP body gets to set), opens one `TraceHandle`
  per request, and hands both to the orchestrator.

**R-015.** The identity used to build that `Principal` used to be `AskBody.on_behalf_of` — a
caller-supplied field. `POST /orchestrate/ask {"on_behalf_of": "<another identity>"}` returned
that identity's disclosure to anyone who could reach this route, because nothing authenticated
the claim. It now comes from `databridge_gateway.auth.require_principal`, a FastAPI dependency
that resolves the caller's identity from a bearer token this deployment issued — never from the
request body. `AskBody` no longer accepts `on_behalf_of` at all; there is nothing left in the body
that could select a grant. See `auth.py` for the mechanism and its threat model, and
`wiki/40-reviews/R-015-gateway-auth-dispositions.md` for the disposition.

**What must stay true, because the value of the answer rests entirely on it.**

* **The Shell proposes; it never executes.** It holds no credential and no driver handle, and it
  is validated against the adapter's own advertised capability set exactly once. A rejected
  proposal raises rather than being fed back to the model — a screen whose rejection is a prompt is
  a screen that eventually passes anything. Enforced in `AdapterShell.propose`/`validate`
  (`packages/adapter`); this route calls `Orchestrator.ask` exactly once per request and does not
  wrap it in a retry.
* **Policy decides, per field, before a value moves.** A denial is `Withheld` with a reason code,
  never a quietly missing row — `SourceOutcome.denial` for a whole-source refusal, a `withheld`
  cell state for a per-column one. Neither is dropped on the way to JSON below.
* **The orchestrator never sees a value it did not receive as a grounded cell.** It assembles
  pointers and cells; it does not summarise rows into prose and pass the prose on. This route does
  not touch that shape either — `_serialize_outcome` is a 1:1 projection of `GroundedCell`, not a
  rendering decision.
* **Untrusted stays quotable, never actionable.** An untrusted cell's `trust: "untrusted"` label
  rides along in the JSON; nothing in this module or in `Orchestrator` used its VALUE to select a
  source, build a predicate, or decide access — source selection reads only `describe()`
  (`databridge_orchestrator.selection`).
* **Every step is recorded, including the ones that refused.** One `TraceHandle` per request, from
  `principal` through every `capability`/`policy`/`cell`/`escalation` step to `close()` — so
  `GET /trace` shows a real run, not an empty adapter branch.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .error_shaping import safe_detail
from .orchestrator_fleet import ConnectionsFleet, FleetUnavailable, fleet_for
from .verify_fleet import NOT_GRANTED, connection_grant
from .trace_recorder import recorder_for

log = logging.getLogger("databridge.gateway.orchestrator")


class AskBody(BaseModel):
    """The only thing a caller may say: the question. WHO asks it is never one of these fields.

    There used to be an `on_behalf_of` here — "WHICH KNOWN IDENTITY asks it" — and that field was
    exactly the vulnerability R-015 closes: a request body is not an authentication mechanism, and
    a caller who could set `on_behalf_of` to another identity received that identity's disclosure
    however the docstring described the field's intent. The identity now comes from
    `auth.require_principal` (a bearer token this deployment issued), which this module's `Depends`
    resolves before this body is even parsed. `max_sources` is capped, not merely defaulted, so a
    request cannot fan this out past what a demo-sized fleet's token budget was ever sized for
    (`shell.py` §9a). `question` is length-capped for the same reason `intelligence_routes.py`
    caps its own free-text fields — an unbounded string is unbounded model/token work per request.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(max_length=2000)
    max_sources: int = 4


def _refusal(code: str, message: str, status: int = 422, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "nearest_alternative": None}
    body.update(extra)
    return JSONResponse(status_code=status, content={"refusal": body})


# =============================================================================================
# serialisation — a 1:1 projection of the orchestrator's own types, never a rewrite of them
# =============================================================================================
def _serialize_cell(cell: Any) -> dict[str, Any]:
    return {
        "column": cell.column,
        "state": cell.state,
        "row_id": cell.row_id,
        "value": cell.value,
        "trust": cell.trust,
        "pointer": cell.pointer,
        "reason_code": cell.reason_code,
        "policy_id": cell.policy_id,
        "appealable": cell.appealable,
    }


def _serialize_denial(denial: Any) -> Optional[dict[str, Any]]:
    if denial is None:
        return None
    rec = denial.record
    return {
        "field_path": rec.field_path,
        "policy_id": rec.policy_id,
        "reason_code": rec.reason_code,
        "principal": rec.principal,
        "purpose": rec.purpose,
        "appealable": rec.appealable,
    }


def _serialize_outcome(outcome: Any) -> dict[str, Any]:
    return {
        "source_id": outcome.source_id,
        "status": outcome.status,
        "message": outcome.message,
        "capability": outcome.capability,
        "entity": outcome.entity,
        "fields": list(outcome.fields),
        "cells": [_serialize_cell(c) for c in outcome.cells],
        "denial": _serialize_denial(outcome.denial),
    }


def _overall_outcome(result: Any) -> str:
    if result.answered:
        return "answered"
    statuses = {o.status for o in result.sources}
    if statuses == {"escalated"}:
        return "escalated"
    if statuses & {"refused"}:
        return "refused"
    return "answered"


def _serialize_result(result: Any, *, trace_id: Optional[str]) -> dict[str, Any]:
    return {
        "question": result.question,
        "principal": {
            "actor": result.principal_actor,
            "on_behalf_of": result.principal_on_behalf_of,
            "purpose": result.principal_purpose,
        },
        "sources_considered": list(result.sources_considered),
        "selection_note": result.selection_note,
        "answered": result.answered,
        "outcome": _overall_outcome(result),
        "trace_id": trace_id,
        "sources": [_serialize_outcome(o) for o in result.sources],
    }


# =============================================================================================
# routes
# =============================================================================================
def register_orchestrator_routes(app: FastAPI) -> None:
    """Mount the ask-the-fleet surface. Both routes disclose fleet/estate data and are gated by
    `auth.require_principal` — see the module docstring's R-015 note."""
    install_auth_handler(app)

    @app.get("/orchestrate/fleet")
    def orchestrate_fleet(principal: AuthenticatedPrincipal = Depends(require_principal)):
        # The payload names the SIGNED-IN identity and nobody else: there is no directory to
        # choose from, because a request cannot choose who it is (R-015).
        fleet = fleet_for(app)
        if isinstance(fleet, ConnectionsFleet):
            # R-019 round 2, finding 10: what a signed-in identity may read is the operator's
            # connection grant, resolved here from configuration — never from the request.
            return fleet.status(principal.principal_id, grant=connection_grant(principal))
        return fleet.status(principal.principal_id)

    @app.post("/orchestrate/ask")
    def orchestrate_ask(body: AskBody,
                        principal: AuthenticatedPrincipal = Depends(require_principal)):
        fleet = fleet_for(app)

        if not body.question.strip():
            return _refusal("EMPTY_QUESTION", "a question must have some text in it")

        if isinstance(fleet, ConnectionsFleet):
            # The default: the sources this deployment connected. The asker is the signed-in
            # identity, and authentication alone reads nothing: the operator's connection grant
            # decides which sources it may read (R-019 round 2, finding 10).
            grant = connection_grant(principal)
            if grant is NOT_GRANTED:
                return _refusal("NOT_GRANTED",
                                "the signed-in identity holds no connection grant; authentication "
                                "alone does not authorise reading connected sources", status=403)
            recorder = recorder_for(app)
            trace = recorder.begin(surface="orchestrate", agent="orchestrator", version="v2")
            try:
                result = fleet.ask(body.question, principal_id=principal.principal_id,
                                   trace=trace, max_sources=max(1, min(int(body.max_sources), 8)),
                                   grant=grant)
            except Exception as exc:                              # noqa: BLE001
                trace.unrecordable(what="the remainder of this request",
                                   because=f"{type(exc).__name__} was raised")
                trace.close("failed")
                message = safe_detail(log, context="the orchestrator run did not complete",
                                      exc=exc)
                return _refusal("ORCHESTRATION_FAILED", message, status=500)
            trace.close(result["outcome"])
            result["trace_id"] = trace.request_id
            return result

        try:
            # The GRANT-SELECTING identity is the one `require_principal` authenticated, never a
            # body field (R-015). `principal.principal_id` must still be one of THIS estate's known
            # identities (its fixture-fleet declaration's directory) — a caller authenticated
            # as, say, the generic `"staff"` identity used by the graph/trace surfaces holds no
            # grant here, and is refused the same way an unknown name always was.
            adapter_principal = fleet.principal_for(principal.principal_id)
        except FleetUnavailable as exc:
            return _refusal("UNKNOWN_PRINCIPAL", str(exc), status=422)

        recorder = recorder_for(app)
        trace = recorder.begin(surface="orchestrate", agent="orchestrator", version="v1")
        try:
            orchestrator = fleet.orchestrator
        except FleetUnavailable as exc:
            trace.unrecordable(what="the whole request",
                              because=f"the orchestrator fleet could not be built: {exc}")
            trace.close("failed")
            return _refusal("FLEET_UNAVAILABLE", str(exc), status=503)

        try:
            result = orchestrator.ask(
                body.question, principal=adapter_principal, trace=trace,
                max_sources=max(1, min(int(body.max_sources), 8)))
        except Exception as exc:                                  # noqa: BLE001
            trace.unrecordable(what="the remainder of this request",
                              because=f"{type(exc).__name__} was raised")
            trace.close("failed")
            message = safe_detail(log, context="the orchestrator run did not complete", exc=exc)
            return _refusal("ORCHESTRATION_FAILED", message, status=500)

        trace.close(_overall_outcome(result))
        return _serialize_result(result, trace_id=trace.request_id)
