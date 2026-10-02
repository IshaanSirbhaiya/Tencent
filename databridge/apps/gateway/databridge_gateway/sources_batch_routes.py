"""Every source at once (FEAT8, 2026-10-02): propose all, approve all after ONE review, run all.

    GET  /source-batch/plans             the review: every pending plan with, per source, its tool
                                         calls and each tool's class, the tools that need a vouch
                                         (unclassified) with the reason, and what keeps the plan
                                         out of a batch — plus the sources that are not pending
    POST /source-batch/plans/propose     propose a plan for every ingest source that has no
                                         approved one (a write connection is never crawled)
    POST /source-batch/plans/approve     approve the plans the person REVIEWED: body
                                         ``{"items": [{source_id, plan_digest, vouch_read_tools}]}``
    POST /source-batch/runs              start a run on every ingest source whose plan is approved

The batch is a loop over the per-source path, never a second one: each item is approved through
`sources_routes.approve_plan_for` — the function `POST /sources/{id}/plan/approve` uses — as the
authenticated principal (the dev token is marked by the package, as on a single approval), and so
lands in the same hash-chained approval log.

What a batch will NOT approve (the source is listed ``needs_individual_review`` with the reason):
a plan that calls a write-classified (or denied, or write-named) tool; a plan with an unclassified
tool the person did not tick; a vouch for a tool that is not an unclassified tool of THAT plan; a
plan with problems or no steps. A vouch is per source and per tool: ticking ``execute_sql`` on one
source vouches for nothing on another. A plan that changed after it was reviewed (its digest
differs) is refused. A source the body does not name is not touched. Every named source gets a
result row: approved, or not, with its reason.

The routes live under ``/source-batch`` — not ``/sources/<word>`` — because every
``/sources/<segment>`` is a source id, and a source may be named anything the id rule allows.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal, require_principal
from .platform_runtime import platform_of
from .public_errors import actor_of, error_ref
from .sources_routes import (CRAWLED_PURPOSE, SourceRefused, _dump, _ingest_unavailable,
                             _plan_or_none, approve_plan_for, connection_revision, plan_digest,
                             plan_stale_reason, propose_plan_for, start_run_for)

log = logging.getLogger("databridge.gateway.sources.batch")

#: The only purpose that is crawled. The listings below use it only to REPORT a row; the gate is
#: `sources_routes.refuse_unless_crawled`, inside each shared helper, under the source lock.
_CRAWLED = CRAWLED_PURPOSE
NEEDS_REVIEW = "NEEDS_INDIVIDUAL_REVIEW"


class BatchApproveItem(BaseModel):
    """One reviewed plan. Who approves is ALWAYS the principal; a body name is ignored."""
    model_config = ConfigDict(extra="ignore")
    source_id: str = Field(min_length=1)
    #: `plan_digest` from the review the person saw: a plan that changed since is not approved.
    plan_digest: str = Field(min_length=1)
    #: The unclassified tools of THIS plan the person ticked, one by one.
    vouch_read_tools: list[str] = Field(default_factory=list)


class BatchApprove(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[BatchApproveItem] = Field(min_length=1)


# --- the review ------------------------------------------------------------------------------
def _purpose(conn: Any) -> Optional[str]:
    return getattr(conn, "purpose", None)


def _step_calls(step: Any, classes: dict) -> list[dict]:
    """The tool calls ONE step makes, each with its role, the plan's class for the tool and the
    COMPLETE fixed arguments the plan passes (R-027 r2 #1: a mode argument such as
    ``action: delete`` is on screen, not hidden behind the tool name). A SQL call also carries its
    whole SQL configuration (statement argument, dialect, parse, extra arguments)."""
    out: list[dict] = []
    for role, spec, attr, args_attr in (
            ("roots", getattr(step, "roots", None), "tool", "args"),
            ("list", getattr(step, "list", None), "tool", "args"),
            ("read text", getattr(step, "read", None), "text_tool", "text_args"),
            ("read binary", getattr(step, "read", None), "binary_tool", "binary_args"),
            ("sql", getattr(step, "sql", None), "tool", "extra_args")):
        tool = getattr(spec, attr, None) if spec is not None else None
        if tool:
            call = {"role": role, "tool": tool, "class": classes.get(tool),
                    "args": dict(getattr(spec, args_attr, None) or {})}
            if role == "sql":
                call["sql"] = _dump(spec)
            out.append(call)
    return out


def assess_plan(plan: Any) -> dict:
    """The batch view of ONE plan, computed by the package's own tool rule
    (`plans.plan_tool_refusals`), never a second copy of it:

    * ``vouchable`` — each step tool the plan records as ``unclassified:unknown`` whose refusal
      a vouch by name removes, with the refusal text as its reason;
    * ``blocked`` — what refuses the plan even when every vouchable tool is vouched (a write,
      a denied tool, an executor outside a SQL step, a tool with no class), plus its problems.
    """
    from databridge_mcp_ingest.plans import plan_tool_refusals         # noqa: PLC0415

    classes = dict(getattr(plan, "tool_classes", None) or {})
    steps = list(getattr(plan, "steps", None) or [])
    calls = [{"step": getattr(s, "id", "?"), "strategy": getattr(s, "strategy", None),
              "calls": _step_calls(s, classes)} for s in steps]
    used = sorted({c["tool"] for s in calls for c in s["calls"]})
    base = plan_tool_refusals(plan, set())
    vouchable: list[dict] = []
    for tool in used:
        if not str(classes.get(tool) or "").startswith("unclassified:unknown"):
            continue
        after = plan_tool_refusals(plan, {tool})
        gone = [r for r in base if r not in after]
        if gone:
            vouchable.append({"tool": tool, "class": classes.get(tool), "reason": "; ".join(gone)})
    blocked = list(plan_tool_refusals(plan, {v["tool"] for v in vouchable}))
    blocked = [f"plan problem: {p}" for p in (getattr(plan, "problems", None) or [])] + blocked
    if not steps:
        blocked.append("the plan has no steps")
    return {"steps": calls,
            "tools": [{"tool": t, "class": classes.get(t)} for t in used],
            "vouchable": vouchable, "blocked": blocked}


def _review_row(conn: Any, plan: Any) -> dict:
    row = {"source_id": conn.id, "title": getattr(conn, "title", "") or conn.id,
           "plan_status": getattr(plan, "status", None), "plan_digest": plan_digest(plan),
           "origin": getattr(plan, "origin", None)}
    try:
        row.update(assess_plan(plan))
    except Exception as exc:                                          # noqa: BLE001
        # R-028 #6: the exception's text goes to the server log, never to the review
        ref = error_ref(log, "batch review: the plan could not be assessed", exc,
                        source_id=conn.id)
        row.update({"steps": [], "tools": [], "vouchable": [], "error_ref": ref,
                    "blocked": [f"the plan could not be assessed (reference {ref}); review it "
                                f"on its own page"]})
    stale = plan_stale_reason(plan, connection_revision(conn), for_run=False)
    if stale:
        row["blocked"].append(stale)
    row["batch_approvable"] = not row["blocked"]
    return row


def batch_precheck(item: BatchApproveItem):
    """The batch's own gate, run under the source lock right before the per-plan approval — after
    the shared checks every approval makes (purpose, plan exists, not already approved for a
    batch, the reviewed digest), so it only ever assesses the plan the person saw."""
    vouched = {t for t in item.vouch_read_tools if t}

    def check(conn: Any, plan: Any) -> None:
        try:
            view = assess_plan(plan)
        except Exception as exc:                                      # noqa: BLE001
            ref = error_ref(log, "batch approval: the plan could not be assessed", exc,
                            source_id=getattr(conn, "id", None))
            raise SourceRefused(NEEDS_REVIEW, f"the plan could not be assessed for a batch "
                                f"(reference {ref}); review it on its own page", status=409,
                                error_ref=ref) from None
        reasons = list(view["blocked"])
        offered = {v["tool"] for v in view["vouchable"]}
        stray = sorted(vouched - offered)
        if stray:
            reasons.append(f"a vouch was sent for {', '.join(stray)}, which "
                           f"{'is' if len(stray) == 1 else 'are'} not an unclassified tool of "
                           f"this plan; nothing but an unclassified tool can be vouched for")
        reasons.extend(f"{v['tool']} is unclassified and was not ticked: {v['reason']}"
                       for v in view["vouchable"] if v["tool"] not in vouched)
        if reasons:
            raise SourceRefused(NEEDS_REVIEW, "; ".join(reasons), status=409)
    return check


# --- routes ----------------------------------------------------------------------------------
def register_sources_batch_routes(app: FastAPI) -> None:
    def _rt():
        rt = platform_of(app)
        if rt.ingest is None:
            return None, _ingest_unavailable(rt)
        return rt, None

    def _connections(rt) -> list:
        try:
            return list(rt.ingest.list_connections())
        except Exception as exc:                                      # noqa: BLE001
            ref = error_ref(log, "batch: the registered sources could not be listed", exc)
            raise SourceRefused("SOURCES_UNREADABLE", "the registered sources could not be "
                                "read", status=500, error_ref=ref) from None

    @app.get("/source-batch/plans")
    def batch_review(_p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _rt()
        if err:
            return err
        try:
            conns = _connections(rt)
        except SourceRefused as refused:
            return refused.response()
        pending, approved, no_plan, not_crawled = [], [], [], []
        for conn in conns:
            if _purpose(conn) != _CRAWLED:
                not_crawled.append({"source_id": conn.id, "purpose": _purpose(conn),
                                    "reason": "a write connection is never crawled"})
                continue
            plan = _plan_or_none(rt.ingest, conn.id)
            if plan is None:
                no_plan.append({"source_id": conn.id})
            elif getattr(plan, "status", None) == "approved":
                approved.append({"source_id": conn.id,
                                 "approved_by": getattr(plan, "approved_by", None)})
            else:
                pending.append(_review_row(conn, plan))
        return {"pending": pending, "approved": approved, "no_plan": no_plan,
                "not_crawled": not_crawled}

    @app.post("/source-batch/plans/propose")
    def batch_propose(principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _rt()
        if err:
            return err
        try:
            conns = _connections(rt)
        except SourceRefused as refused:
            return refused.response()
        results = []
        for conn in conns:
            sid = conn.id
            if _purpose(conn) != _CRAWLED:
                results.append({"source_id": sid, "outcome": "skipped", "code": "NOT_CRAWLED",
                                "reason": "a write connection is never crawled"})
                continue
            plan = _plan_or_none(rt.ingest, sid)
            if getattr(plan, "status", None) == "approved" and not plan_stale_reason(
                    plan, connection_revision(conn), for_run=True):
                results.append({"source_id": sid, "outcome": "skipped",
                                "code": "ALREADY_APPROVED",
                                "reason": "its plan is approved; proposing again would "
                                          "replace an approved plan"})
                continue
            try:
                plan = propose_plan_for(rt, sid)
            except SourceRefused as refused:
                results.append({"source_id": sid, "outcome": "failed", **refused.row()})
                continue
            results.append({"source_id": sid, "outcome": "proposed",
                            "plan_status": getattr(plan, "status", None)})
        log.info("sources batch: %s proposed plans: %s", principal.principal_id,
                 {r["source_id"]: r["outcome"] for r in results})
        return {"results": results, "proposed_by": principal.principal_id}

    @app.post("/source-batch/plans/approve")
    def batch_approve(body: BatchApprove = Body(...),
                      principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _rt()
        if err:
            return err
        results, seen = [], set()
        for item in body.items:
            sid = item.source_id
            if sid in seen:
                results.append({"source_id": sid, "outcome": "refused", "code": "DUPLICATE",
                                "reason": "this source was named twice; it was decided once"})
                continue
            seen.add(sid)
            try:
                plan = approve_plan_for(rt, sid, principal, sorted(set(item.vouch_read_tools)),
                                        item.plan_digest, refuse_approved=True,
                                        precheck=batch_precheck(item))
            except SourceRefused as refused:
                results.append({"source_id": sid,
                                "outcome": ("needs_individual_review" if refused.code == NEEDS_REVIEW
                                            else "refused"), **refused.row()})
                continue
            data = _dump(plan) or {}
            results.append({"source_id": sid, "outcome": "approved",
                            "approved_by": data.get("approved_by"),
                            "approval": data.get("approval") or {},
                            "tool_classes": data.get("tool_classes") or {}})
        counts: dict[str, int] = {}
        for r in results:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        log.info("sources batch: %s approved plans: %s", principal.principal_id,
                 {r["source_id"]: r["outcome"] for r in results})
        # R-028 #7: who decided, as the common actor object (never a decorated id)
        return {"results": results, "counts": counts, "by": principal.principal_id,
                "dev_only": principal.dev_only, "actor": actor_of(principal)}

    @app.post("/source-batch/runs", status_code=202)
    def batch_runs(background: BackgroundTasks,
                   principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _rt()
        if err:
            return err
        try:
            conns = _connections(rt)
        except SourceRefused as refused:
            return refused.response()
        results = []
        for conn in conns:
            sid = conn.id
            if _purpose(conn) != _CRAWLED:
                results.append({"source_id": sid, "outcome": "skipped", "code": "NOT_CRAWLED",
                                "reason": "a write connection is never run"})
                continue
            plan = _plan_or_none(rt.ingest, sid)
            if getattr(plan, "status", None) != "approved":
                results.append({"source_id": sid, "outcome": "skipped",
                                "code": "PLAN_NOT_APPROVED",
                                "reason": f"its plan is {getattr(plan, 'status', None) or 'missing'}"
                                          f"; approve it first"})
                continue
            try:
                started = start_run_for(app, rt, sid, principal, background)
            except SourceRefused as refused:
                results.append({"source_id": sid, "outcome": "refused", **refused.row()})
                continue
            # `started` only once the run's own worker is running (`dispatch_run`).
            results.append({"source_id": sid, "outcome": "started", **started})
        log.info("sources batch: %s started runs: %s", principal.principal_id,
                 {r["source_id"]: r["outcome"] for r in results})
        return {"results": results, "started_by": principal.principal_id}


__all__ = ["register_sources_batch_routes", "assess_plan", "plan_digest", "batch_precheck",
           "BatchApprove", "BatchApproveItem", "NEEDS_REVIEW"]
