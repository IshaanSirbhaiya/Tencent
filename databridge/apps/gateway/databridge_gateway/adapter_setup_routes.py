"""`/adapter-setup` — set up a source through its adapter agent (FEAT8, founder design 2026-10-02).

    GET  /adapter-setup/sources                     every registered READ source, each with where
                                                    its setup stands ("ready to set up" when no
                                                    agent keeps it yet)
    GET  /adapter-setup/{source_id}                 the steps, the plan, the mapping, the agent's
                                                    explanation and the setup's Activity records
    POST /adapter-setup/{source_id}/start           the agent's draft is created (as the signed-in
                                                    person) and the agent proposes the crawl plan
    POST /adapter-setup/{source_id}/plan/approve    a PERSON approves the plan (+ vouches)
    POST /adapter-setup/{source_id}/discover        a CAPTURE-ONLY discovery: tables and documents
                                                    are captured, screened and the mapping is
                                                    proposed; nothing is built (ingest enforces it)
    POST /adapter-setup/{source_id}/explain         the agent explains the proposed mapping
                                                    (advisory, harness, pointers into the
                                                    discovery's captures)
    POST /adapter-setup/{source_id}/mapping/approve a PERSON approves tables, naming the
                                                    explanation they read
    POST /adapter-setup/{source_id}/handoff         the person activates the agent (sealed
                                                    delegation) and it starts the build run

**No parallel pipeline.** Plan proposal, plan approval and mapping approval CALL the Sources
routes' own handlers (looked up on the app, as the ontology forward does), with the authenticated
principal object; their checks, locks and refusals apply. Discovery runs the ingest package's own
run in its capture-only mode (`run_ingest(capture_only=True)`), admitted under the same source
lock and the same plan-staleness check as a Sources run. Links, holds and identity rules are
decided on their existing routes, unchanged.

**The hand-off gate (`setup.handoff_problems`, Codex R-027 AAX f1).** Mapping approval and the
hand-off need a successful discovery under the plan approved now (#8), a saved explanation bound
to that discovery's captures and to the current proposal (#4, #5), and a plan that is not stale
for the connection as it is now (#6). An agent that is already active is never re-activated here:
it hands off only with a sealed delegation in force (#7). The build is recorded `started`, then
its terminal outcome from the sync job (#9).

Every route requires `auth.require_principal`; a decision is always the signed-in principal's.
"""
from __future__ import annotations

import logging
import threading
import types
from typing import Any, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import JobBusy, platform_of
from .public_errors import (actor_of, error_ref, from_exception, is_machine, refusal,
                            refusal_body)

log = logging.getLogger("databridge.gateway.adapter_setup")

#: the sync summary statuses a build counts as having succeeded
_BUILD_OK = ("completed", "completed_with_warnings")


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    """The common refusal envelope (R-028 #11): ``message`` is route-authored, never an
    exception's text; a specific code with a standard equivalent answers as the standard code."""
    return refusal(code, message, status)


def _exc_refusal(exc: BaseException, default_code: str, status: int, context: str,
                 principal: Any = None, **detail: Any) -> JSONResponse:
    """R-028 #6: a caught exception answers a fixed sentence (or a package refusal's own safe
    sentence) and an ``error_ref``; its text is in the server log only."""
    code, message, reason, ref = from_exception(log, exc, default_code=default_code,
                                                context=context, principal=principal, **detail)
    return JSONResponse(status_code=status,
                        content=refusal_body(code, message, reason_code=reason, ref=ref))


def _unavailable(rt: Any) -> JSONResponse:
    """The packages the setup needs are not importable. Their import errors are in the server
    log under the reference, never in the answer (R-028 #6)."""
    ref = error_ref(log, "setup unavailable", None, problems=list(getattr(rt, "problems", [])))
    return refusal("SETUP_UNAVAILABLE", "the ingest package or the adapter-agent manager is not "
                   "available in this gateway", 503, ref=ref)


class HandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadence: str = "daily"
    #: the key of EVERY pending decision the person defers / keeps held (Codex R-027 AAX f2 #8);
    #: it must name exactly the pending set shown to them
    deferred: list[str] = []


class DocumentsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: the discovery and documents digest the person decided on (as shown)
    discovery_run_id: str
    documents_digest: str


class VouchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vouch_read_tools: list[str] = []
    #: the `plan_digest` of the plan the person reviewed (`GET /adapter-setup/{id}`), forwarded
    #: to the Sources approval route, which refuses an approval of a plan that changed since
    plan_digest: Optional[str] = None


class TablesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: Optional[list[str]] = None
    #: the `mapping_digest` of the explanation the person read (Codex R-027 AAX f1 #4)
    explanation_digest: Optional[str] = None


def _endpoint(app: FastAPI, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in (getattr(route, "methods", None)
                                                               or set()):
            return route.endpoint
    return None


def _dump(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    return obj


def _vocab():
    from databridge_graph.packs import active_vocabulary  # noqa: PLC0415

    return active_vocabulary()


def gateway_plan_stale(service: Any, connection: str) -> Optional[str]:
    """Codex R-027 AAX f1 #6: the Sources routes' SHARED staleness check
    (`sources_routes.plan_stale_reason`, for a run), applied by the adapter-agent manager at
    activate, resume and every sync. Reads the plan and the connection through the agent's own
    ingest service. A service that cannot read declarations binds nothing here (its plan status
    gate still applies)."""
    from .sources_routes import connection_revision, plan_stale_reason  # noqa: PLC0415

    read = getattr(service, "read_config", None)
    if not callable(read):
        return None
    plan, conn = read(connection, "plan"), read(connection, "connection")
    if plan is None:
        return None                       # no plan: the manager's plan-status gate refuses it
    if conn is None:
        return "its connection declaration could not be read, so its plan binds to nothing"
    if isinstance(plan, dict):
        # the real service returns a stored CrawlPlan as JSON (a corrupt one RAISES above, so it
        # fails closed); a declaration that is not a crawl plan at all comes from a service that
        # keeps no crawl plans, and it binds nothing
        from databridge_mcp_ingest.plans import CrawlPlan  # noqa: PLC0415

        try:
            CrawlPlan.model_validate(plan)
        except Exception:                                             # noqa: BLE001
            return None
    view = types.SimpleNamespace(**plan) if isinstance(plan, dict) else plan
    return plan_stale_reason(view, connection_revision(conn), for_run=True)


def _keeper(manager: Any, source_id: str) -> Optional[Any]:
    """The agent that keeps `source_id`: the one Set up created, else any live agent that manages
    it (an agent described in English earlier is not duplicated)."""
    from databridge_adapter_agents.setup import keeper_id  # noqa: PLC0415

    specs = [s for s in manager.registry.list() if source_id in s.connections]
    own = next((s for s in specs if s.id == keeper_id(source_id)), None)
    return own or (specs[0] if specs else None)


def _step(sid: str, label: str, status: str, detail: str = "") -> dict:
    return {"id": sid, "label": label, "status": status, "detail": detail}


def _gate(rt: Any, conn: Any) -> dict:
    """Everything the gate reads for one source, from disk, once (`setup.load_gate` - the SAME
    function the manager verifies an activation with): the plan (status, staleness for the
    connection as it is now, digest), the latest discovery, the mapping, the explanation, the
    documents decision, the hand-off problems and the pending decisions. Read-only."""
    from databridge_adapter_agents.setup import load_gate  # noqa: PLC0415

    from .sources_routes import connection_revision, plan_stale_reason  # noqa: PLC0415

    try:
        plan = rt.ingest.load_plan(conn.id)
    except Exception:                                                 # noqa: BLE001
        plan = None
    stale = plan_stale_reason(plan, connection_revision(conn), for_run=True) if plan else None
    return load_gate(conn.id, rt.manager.registry.root, plan_stale=stale)


def discovered_ok(discovery: Optional[dict], g: dict) -> bool:
    from databridge_adapter_agents.setup import discovery_problems  # noqa: PLC0415

    return not discovery_problems(discovery, plan_digest=g["plan_digest"])


def _explained_by(produced_by: dict) -> str:
    """W-06: who explained it - the model that RAN and its task class, or the mapper's rules."""
    if produced_by.get("mode") == "model":
        return (f"by {produced_by.get('model') or 'a model the harness did not name'} "
                f"(task class {produced_by.get('task_class')})")
    return "from the mapper's rules (no model)"


def setup_state(app: FastAPI, conn: Any) -> dict:
    """Where one source's setup stands, computed from what is on disk (the plan, the discovery,
    the mapping, the explanation, the agent). Read-only."""
    from databridge_adapter_agents.setup import (approved_by_people,  # noqa: PLC0415
                                                 discovery_problems, document_items,
                                                 documents_digest, documents_problems,
                                                 explanation_problems, setup_records)

    rt = platform_of(app)
    sid = conn.id
    g = _gate(rt, conn)
    plan, mapping, discovery, explanation = g["plan"], g["mapping"], g["discovery"], \
        g["explanation"]
    try:
        runs = list(rt.ingest.list_runs(sid))
    except Exception:                                                 # noqa: BLE001
        runs = []
    running = rt.jobs.busy("ingest", sid)
    keeper = _keeper(rt.manager, sid) if rt.manager is not None else None
    plan_status, stale = g["plan_status"], g["stale"]
    tables = dict(getattr(mapping, "tables", None) or {})
    counts = {s: sum(1 for t in tables.values() if t.status == s)
              for s in ("proposed", "approved", "unmapped")}
    disc_problems = discovery_problems(discovery, plan_digest=g["plan_digest"])
    discovered = not disc_problems
    for_this_plan = bool(discovery) and discovery.get("plan_digest") == g["plan_digest"]
    ex_problems = explanation_problems(explanation, mapping, discovery) if tables else []
    fresh = bool(explanation) and bool(tables) and not ex_problems
    problems = g["problems"]
    people, people_problems = (approved_by_people(sid, mapping, g["vocab"]) if tables
                               else ([], []))
    kept = keeper is not None and keeper.status == "active"
    disc_tables = bool((discovery or {}).get("tables"))
    doc_items = [d for d in ((discovery or {}).get("documents") or {}).get("items") or []]
    # W-05: documents the screen allowed now (built at the hand-off) and documents an earlier
    # sync already built (kept by the keeper, not rebuilt) - both need a person's decision
    doc_allowed, doc_kept = document_items(discovery)
    docs_digest = documents_digest(discovery) if discovered_ok(discovery, g) else None
    docs_problems = documents_problems(g["documents_decision"], discovery)
    docs_only = discovered_ok(discovery, g) and not disc_tables and bool(doc_allowed or doc_kept)
    if running:
        discover_status = "doing"
    elif discovered:
        discover_status = "done"
    elif for_this_plan:
        discover_status = "blocked"          # it ran under this plan and did not succeed
    else:
        discover_status = "todo"
    steps = [
        _step("plan", "Propose the crawl plan",
              "done" if plan is not None else "todo",
              f"{len(getattr(plan, 'steps', []) or [])} step(s)" if plan is not None else ""),
        _step("approve_plan", "A person approves the plan",
              "blocked" if stale else ("done" if plan_status == "approved" else
                                        "waiting_for_person" if plan is not None else "todo"),
              stale or (f"approved by {plan.approved_by}" if plan_status == "approved" else "")),
        _step("discover", "Discover: capture and screen, build nothing", discover_status,
              (f"discovery {discovery.get('run_id')} {discovery.get('status')}"
               if discovery and discover_status != "todo" else "")
              + ("" if discovered or discover_status == "todo"
                 else "; " + "; ".join(disc_problems[:1]))),
        _step("propose_mapping", "Propose the mapping",
              "done" if (tables and discovered) or docs_only else "todo",
              "no tables: documents only" if docs_only else
              f"{counts['proposed']} proposed, {counts['approved']} approved, "
              f"{counts['unmapped']} unmapped" if tables else ""),
        _step("explain", "The agent explains the mapping",
              "done" if fresh or docs_only else ("todo" if discovered else "blocked"),
              "no tables: documents only" if docs_only else
              (_explained_by(explanation["produced_by"]) if fresh else
               "; ".join(ex_problems[:1]) if explanation else "")),
        _step("approve_mapping", "A person approves the mapping",
              "done" if (people and not people_problems and fresh) or docs_only else
              ("waiting_for_person" if tables and fresh else "todo"),
              "no tables: documents only" if docs_only else
              "; ".join(people_problems[:2]) or
              f"{len(people)} table(s) approved by a person, {counts['proposed']} still proposed"),
        _step("approve_documents", "A person decides on the documents",
              ("done" if not docs_problems else "waiting_for_person") if docs_digest else
              ("done" if discovered else "todo"),
              (f"{len(doc_allowed)} document(s) to build, {len(doc_kept)} already built to keep"
               if docs_digest else "no documents to build" if discovered else "")),
        _step("handoff", "Hand off: build, then keep the source",
              "done" if kept else ("todo" if not problems else "blocked"),
              f"kept by {keeper.id}" if kept else "; ".join(problems[:3])),
    ]
    phase = "kept" if kept else ("ready" if keeper is None and plan is None else "in_setup")
    finished = [r for r in runs if r.get("status") not in ("running", None)]
    return {
        "source_id": sid, "purpose": getattr(conn, "purpose", "ingest"),
        "transport": getattr(getattr(conn, "transport", None), "type", None),
        "phase": phase, "steps": steps, "running": running,
        "plan_status": plan_status, "plan_stale": stale,
        "runs": len(runs), "last_run": _public_run(finished[-1]) if finished else None,
        "discovery": ({k: discovery.get(k) for k in ("run_id", "status", "message",
                                                      "finished_at")}
                      if discovery else None),
        "mapping_counts": counts, "explanation_fresh": fresh,
        "explanation_digest": (explanation or {}).get("mapping_digest") if fresh else None,
        "handoff_problems": problems,
        "pending_decisions": g["pending"],
        "documents": {"captured": len(doc_items), "allowed": len(doc_allowed),
                      "kept": len(doc_kept),
                      "held": sum(1 for d in doc_items if d.get("decision") == "review"
                                  or d.get("held")),
                      "digest": docs_digest, "decided": bool(docs_digest) and not docs_problems,
                      "discovery_run_id": (discovery or {}).get("run_id")},
        "keeper": ({"id": keeper.id, "status": keeper.status, "title": keeper.title}
                   if keeper is not None else None),
        "activity": [{"run_id": r["run_id"], "at": r.get("started"), "status": r.get("status"),
                      "reason": r.get("reason"), "by": (r.get("trigger") or {}).get("by"),
                      "step": ((r.get("steps") or [{}])[0].get("tool"))}
                     for r in (setup_records(rt.manager.runs_root, keeper.id)
                               if keeper is not None else [])],
    }


#: W-06: what a run summary may show a person - never its server directory or free-text notes
_PUBLIC_RUN_KEYS = ("source_id", "run_id", "status", "message", "counts", "started_at",
                    "finished_at", "durations_s", "plan")


def _public_run(run: Optional[dict]) -> Optional[dict]:
    """A run summary for the setup page: its own fields, without the server-side run directory
    and the notes (which name screen-rule files by their server path)."""
    if not isinstance(run, dict):
        return None
    return {k: run[k] for k in _PUBLIC_RUN_KEYS if k in run}


def _record(rt, spec, **kw):
    """One setup Activity record. A PERSON's step passes ``who`` — the common actor object of the
    signed-in principal (R-028 #7) — so the record names who decided, never a bare string."""
    from databridge_adapter_agents.setup import record_step  # noqa: PLC0415

    try:
        return record_step(rt.manager.runs_root, spec, **kw)
    except Exception as exc:                                          # noqa: BLE001
        log.warning("setup: activity record for %s not written: %s", spec.id, exc)
        return None


def _discover_job(app: FastAPI, source_id: str, run_id: str, job_id: str, revision: str,
                  spec: Any, by: str, requester: Optional[dict] = None) -> None:
    """The capture-only discovery, on its own worker. Re-checks the admission when it starts (the
    Sources run's own check), then runs ingest in its capture-only mode and records the TERMINAL
    outcome."""
    from databridge_mcp_ingest.runner import DISCOVERED  # noqa: PLC0415

    from .sources_routes import admission_refusal_at_start  # noqa: PLC0415

    rt = platform_of(app)
    try:
        refused = admission_refusal_at_start(rt, source_id, revision)
        if refused:
            rt.jobs.finish(job_id, status="refused", error=refused)
            _record(rt, spec, step="discover", status="failed", by=by, requested_by=requester,
                    reason=f"the discovery {run_id} of {source_id} was refused when it started: "
                           f"{refused}")
            return
        import databridge_mcp_ingest as mi  # noqa: PLC0415

        report = mi.run_ingest(source_id, store=None, harness=rt.harness,
                               progress_cb=lambda ev: rt.jobs.progress(job_id, ev),
                               run_id=run_id, capture_only=True)
        data = _dump(report) or {}
        status = data.get("status")
        ok = status == DISCOVERED
        rt.jobs.finish(job_id, status="completed" if ok else "failed", result=data,
                       run_status=status)
        _record(rt, spec, step="discover", status="completed" if ok else "failed", by=by,
                requested_by=requester,
                reason=(f"discovered {source_id} ({run_id}): captured and screened, mapping "
                        f"proposed, nothing built" if ok else
                        f"the discovery {run_id} of {source_id} ended {status}: "
                        f"{str(data.get('message') or '')[:300]}"),
                counts={k: (data.get("counts") or {}).get(k)
                        for k in ("inventory", "captured", "built")})
    except Exception as exc:                                          # noqa: BLE001
        # R-028 #6: the visible job and Activity records carry the reference, not the text
        ref = error_ref(log, "discovery failed", exc, source_id=source_id, run_id=run_id)
        rt.jobs.finish(job_id, status="failed",
                       error=f"the discovery failed (reference {ref})", error_ref=ref)
        _record(rt, spec, step="discover", status="failed", by=by, requested_by=requester,
                reason=f"the discovery {run_id} of {source_id} failed (reference {ref})")


def _build_job(app: FastAPI, agent_id: str, job_id: str, by: str, source_id: str,
               requester: Optional[dict] = None) -> None:
    """The hand-off's build: the agent's own sync job, then the TERMINAL Activity record from
    its outcome (Codex R-027 AAX f1 #9) - never "completed" at queue time."""
    from .adapter_agents_routes import _sync_job  # noqa: PLC0415

    rt = platform_of(app)
    data = _sync_job(app, agent_id, job_id, by, actor=requester) or {}
    status = data.get("status")
    try:
        spec = rt.manager.registry.get(agent_id)
    except Exception:                                                 # noqa: BLE001
        log.warning("setup: %s vanished before its build was recorded", agent_id)
        return
    ok = status in _BUILD_OK
    _record(rt, spec, step="build", status="completed" if ok else "failed", by=by,
            requested_by=requester,
            reason=(f"built {source_id} from the approved mapping (sync {data.get('run_id')} "
                    f"{status}); it keeps the source from now on" if ok else
                    f"the build run of {source_id} ended {status}"
                    + (f" (reference {data['error_ref']})" if data.get("error_ref") else "")),
            counts={k: v for k, v in (data.get("totals") or {}).items()})


def register_adapter_setup_routes(app: FastAPI) -> None:
    install_auth_handler(app)

    def _need(source_id: str):
        rt = platform_of(app)
        if rt.ingest is None or rt.manager is None:
            return None, None, _unavailable(rt)
        try:
            conn = rt.ingest.load_connection(source_id)
        except Exception as exc:                                      # noqa: BLE001
            # the package's text names the declaration's path (R-028 #6)
            log.info("setup: source %r could not be loaded: %s", source_id, exc)
            return None, None, _refusal("UNKNOWN_SOURCE", "no registered source with that id",
                                        status=404)
        if getattr(conn, "purpose", "ingest") != "ingest":
            return None, None, _refusal("NOT_A_READ_SOURCE",
                                        f"{source_id!r} is a write connection; only a read "
                                        f"source is set up and crawled", status=409)
        return rt, conn, None

    def _call(path: str, method: str, **kw):
        fn = _endpoint(app, path, method)
        if fn is None:
            return _refusal("SOURCES_ROUTES_UNAVAILABLE",
                            f"{method} {path} is not mounted on this gateway", status=503)
        return fn(**kw)

    def _refused(result: Any) -> Optional[JSONResponse]:
        return result if isinstance(result, JSONResponse) else None

    @app.get("/adapter-setup/sources")
    def setup_sources(_p: AuthenticatedPrincipal = Depends(require_principal)):
        rt = platform_of(app)
        if rt.ingest is None or rt.manager is None:
            return _unavailable(rt)
        out, problems = [], []
        try:
            conns = rt.ingest.list_connections()
        except Exception as exc:                                      # noqa: BLE001
            return _exc_refusal(exc, "SOURCES_UNREADABLE", 500, "setup: sources not listed")
        for c in conns:
            if getattr(c, "purpose", "ingest") != "ingest":
                continue                                  # a write connection is never crawled
            try:
                out.append(setup_state(app, c))
            except Exception as exc:                                  # noqa: BLE001
                ref = error_ref(log, "setup state failed", exc, source_id=getattr(c, "id", "?"))
                problems.append(f"{getattr(c, 'id', '?')}: its setup state could not be read "
                                f"(reference {ref})")
        return {"sources": out, "problems": problems}

    @app.get("/adapter-setup/{source_id}")
    def setup_get(source_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_adapter_agents.setup import load_explanation  # noqa: PLC0415

        rt, conn, err = _need(source_id)
        if err:
            return err
        try:
            plan = rt.ingest.load_plan(source_id)
        except Exception:                                             # noqa: BLE001
            plan = None
        try:
            mapping = rt.ingest.load_mapping(source_id)
        except Exception:                                             # noqa: BLE001
            mapping = None
        from .sources_routes import plan_digest  # noqa: PLC0415

        return {"state": setup_state(app, conn), "connection": _dump(conn),
                "plan": _dump(plan), "plan_digest": plan_digest(plan) if plan is not None else None,
                "mapping": _dump(mapping),
                "explanation": load_explanation(rt.manager.registry.root, source_id)}

    @app.post("/adapter-setup/{source_id}/start")
    def setup_start(source_id: str,
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_adapter_agents.setup import draft_spec  # noqa: PLC0415

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            vocab = _vocab()
            if not vocab.packs:
                return _refusal("NO_PACK", "no pack is enabled; the agent has no ontology to map "
                                           "to", status=409)
            try:
                # the person who clicked Set up creates the agent (and so owns it, in Activity)
                spec = rt.manager.create(draft_spec(source_id, vocab.packs[0]), by=principal)
            except Exception as exc:                                  # noqa: BLE001
                return _exc_refusal(exc, "AGENT_NOT_CREATED", 409, "setup: agent not created",
                                    principal=principal, source_id=source_id)
            _record(rt, spec, step="created", status="completed", by=principal.principal_id,
                    requested_by=actor_of(principal),
                    who=actor_of(principal),
                    actor="person", reason=f"created the draft adapter agent {spec.id} to set up "
                                           f"{source_id}")
            rt.reload_engine()
        if spec.status == "draft":
            # Codex R-027 AAX f2 #1: from now on this agent is activated only through the
            # hand-off (the manager enforces it, on every route)
            from databridge_adapter_agents.setup import mark_setup_agent  # noqa: PLC0415

            mark_setup_agent(rt.manager.registry.root, spec.id, source_id,
                             by=principal.principal_id)
        result = _call(f"/sources/{{source_id}}/plan", "POST", source_id=source_id,
                       principal=principal)
        if _refused(result):
            _record(rt, spec, step="propose_plan", status="failed", by=principal.principal_id,
                    requested_by=actor_of(principal),
                    reason="the crawl plan could not be proposed")
            return result
        plan = result.get("plan") or {}
        _record(rt, spec, step="propose_plan", status="completed", by=principal.principal_id,
                requested_by=actor_of(principal),
                reason=f"proposed a crawl plan with {len(plan.get('steps') or [])} step(s) for "
                       f"{source_id}; a person approves it",
                counts={"steps": len(plan.get("steps") or []),
                        "problems": len(plan.get("problems") or [])})
        return {"agent": spec.id, "plan": plan, "state": setup_state(app, conn)}

    @app.post("/adapter-setup/{source_id}/plan/approve")
    def setup_plan_approve(source_id: str, body: Optional[VouchBody] = Body(None),
                           principal: AuthenticatedPrincipal = Depends(require_principal)):
        from .sources_routes import ApprovePlan  # noqa: PLC0415

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            return _refusal("NOT_STARTED", "press Set up first", status=409)
        vouch = list(body.vouch_read_tools) if body else []
        result = _call(f"/sources/{{source_id}}/plan/approve", "POST", source_id=source_id,
                       body=ApprovePlan(vouch_read_tools=vouch,
                                        plan_digest=body.plan_digest if body else None),
                       principal=principal)
        if _refused(result):
            return result
        try:
            rt.manager.refresh_plans(spec.id, by=principal.principal_id)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("setup: refresh_plans %s: %s", spec.id, exc)
        _record(rt, spec, step="approve_plan", status="completed", by=principal.principal_id,
                requested_by=actor_of(principal),
                who=actor_of(principal),
                actor="person", reason=(f"approved the crawl plan for {source_id}"
                                        + (f", vouching that {', '.join(vouch)} only read"
                                           if vouch else "")))
        return {"plan": result.get("plan"), "state": setup_state(app, conn)}

    @app.post("/adapter-setup/{source_id}/discover", status_code=202)
    def setup_discover(source_id: str, background: BackgroundTasks,
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Codex R-027 AAX f1 #1: a CAPTURE-ONLY discovery (never the Sources run, which builds
        documents). Admitted as a Sources run is: under the source lock, the plan approved and
        not stale for the connection as it is now, one ingest at a time per source."""
        from .sources_routes import connection_revision, plan_stale_reason  # noqa: PLC0415

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            return _refusal("NOT_STARTED", "press Set up first", status=409)
        with rt.source_lock(source_id):
            try:
                conn = rt.ingest.load_connection(source_id)
                plan = rt.ingest.load_plan(source_id)
            except Exception:                                         # noqa: BLE001
                plan = None
            if plan is None or getattr(plan, "status", None) != "approved":
                return _refusal("PLAN_NOT_APPROVED",
                                f"the crawl plan for {source_id!r} is "
                                f"{getattr(plan, 'status', 'missing')!r}; a person approves it "
                                f"before the source is read", status=409)
            rev = connection_revision(conn)
            stale = plan_stale_reason(plan, rev, for_run=True)
            if stale:
                return _refusal("PLAN_STALE", stale, status=409)
            run_id = rt.ingest.new_run_id()
            try:
                job = rt.jobs.start("ingest", source_id, by=principal.principal_id,
                                    run_id=run_id, source_id=source_id,
                                    connection_revision=rev, mode="discover")
            except JobBusy:
                return _refusal("RUN_IN_PROGRESS", f"an ingest of {source_id!r} is already "
                                                   f"running", status=409)
        _record(rt, spec, step="discover", status="started", by=principal.principal_id,
                requested_by=actor_of(principal),
                reason=f"started a capture-only discovery of {source_id} ({run_id}): it reads "
                       f"and screens the source and proposes a mapping; nothing is built until "
                       f"a person approves it and hands the source off")
        worker = threading.Thread(target=_discover_job,
                                  args=(app, source_id, run_id, job["job_id"], rev, spec,
                                        principal.principal_id, actor_of(principal)),
                                  name=f"discover-{source_id}-{run_id}", daemon=True)
        worker.start()
        background.add_task(worker.join)
        return {"run_id": run_id, "job_id": job["job_id"], "status": "running",
                "mode": "discover", "started_by": principal.principal_id}

    @app.post("/adapter-setup/{source_id}/explain")
    def setup_explain(source_id: str,
                      principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_adapter_agents.explain import explain_mapping  # noqa: PLC0415
        from databridge_adapter_agents.setup import (discovery_problems,  # noqa: PLC0415
                                                     save_explanation)

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            return _refusal("NOT_STARTED", "press Set up first", status=409)
        g = _gate(rt, conn)
        why = discovery_problems(g["discovery"], plan_digest=g["plan_digest"])
        if why:
            # Codex R-027 AAX f1 #5/#8: evidence comes only from a successful discovery's captures
            return _refusal("DISCOVERY_REQUIRED", "; ".join(why), status=409)
        mapping = g["mapping"]
        if not (g["discovery"] or {}).get("tables"):
            # Codex R-027 AAX f2 #7: a document-only source has no table mapping to explain; a
            # person decides on its documents instead
            return _refusal("NO_TABLES", "the discovery captured no table: this source is "
                                         "documents only - decide on its documents", status=409)
        if mapping is None or not (mapping.tables or {}):
            return _refusal("NO_MAPPING", "the discovery proposed no tables", status=409)
        try:
            explanation = explain_mapping(source_id, mapping, g["vocab"], harness=rt.harness,
                                          agent_id=spec.id, discovery=g["discovery"])
        except Exception as exc:                                      # noqa: BLE001
            return _exc_refusal(exc, "EXPLAIN_FAILED", 500, "setup: explanation failed",
                                source_id=source_id)
        save_explanation(rt.manager.registry.root, source_id, explanation)
        tables = explanation["tables"]
        by_status = {s: sum(1 for t in tables if t["status"] == s)
                     for s in ("proposed", "approved", "unmapped")}
        _record(rt, spec, step="explain", status="completed", by=principal.principal_id,
                requested_by=actor_of(principal),
                reason=(f"proposed the mapping of {source_id} ({by_status['proposed']} table(s) "
                        f"proposed, {by_status['unmapped']} unmapped) and explained it "
                        f"({explanation['produced_by']['mode']}); a person approves it"),
                counts={**by_status,
                        "evidence": sum(len(t["evidence"]) for t in tables)},
                model=explanation["produced_by"].get("model"))
        return {"explanation": explanation, "state": setup_state(app, conn)}

    @app.post("/adapter-setup/{source_id}/mapping/approve")
    def setup_mapping_approve(source_id: str, body: Optional[TablesBody] = Body(None),
                              principal: AuthenticatedPrincipal = Depends(require_principal)):
        """A person approves tables, naming the explanation they read (Codex R-027 AAX f1 #4):
        it must be saved, bound to the latest successful discovery and to the current proposal,
        and its digest must be the one in the request."""
        from databridge_adapter_agents.setup import (discovery_problems,  # noqa: PLC0415
                                                     explanation_problems)

        from .sources_routes import ApproveMapping  # noqa: PLC0415

        from databridge_mcp_ingest.runner import RunInProgress, source_run_lock  # noqa: PLC0415

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            return _refusal("NOT_STARTED", "press Set up first", status=409)
        # Codex R-027 AAX f2 #6: validation and approval happen under the source lock (link
        # decisions, plan approval and run admission take it) AND the ingest run lock (every
        # run and discovery holds it while it proposes mappings): nothing can change the mapping
        # between the check and the approval
        with rt.source_lock(source_id):
            try:
                run_lock = source_run_lock(source_id)
                run_lock.__enter__()
            except RunInProgress as busy:
                log.info("setup: mapping approval of %s refused, run in progress: %s",
                         source_id, busy)
                return _refusal("RUN_IN_PROGRESS", f"a run or discovery of {source_id!r} is in "
                                                   f"progress; approve when it ends", status=409)
            try:
                g = _gate(rt, conn)
                why = (discovery_problems(g["discovery"], plan_digest=g["plan_digest"])
                       + explanation_problems(g["explanation"], g["mapping"], g["discovery"]))
                if why:
                    return _refusal("EXPLANATION_REQUIRED", "; ".join(why), status=409)
                want = (g["explanation"] or {}).get("mapping_digest")
                if not body or not body.explanation_digest or body.explanation_digest != want:
                    return _refusal("EXPLANATION_NOT_READ",
                                    "the approval must name the explanation it was made against "
                                    "(explanation_digest), and it is not the current one; reload "
                                    "and review the explanation", status=409)
                explained = {t["table"]: t["mapping_digest"]
                             for t in (g["explanation"] or {}).get("tables") or []}
                tables = body.tables
                result = _call(f"/sources/{{source_id}}/mapping/approve", "POST",
                               source_id=source_id, body=ApproveMapping(tables=tables),
                               principal=principal)
            finally:
                run_lock.__exit__(None, None, None)
        if _refused(result):
            return result
        # belt and braces: every approval this call recorded is of the digest that was explained
        for t, tm in ((result.get("mapping") or {}).get("tables") or {}).items():
            a = tm.get("approval") or {}
            if a.get("principal_id") == principal.principal_id and \
                    (tables is None or t in tables) and a.get("digest") != explained.get(t):
                log.error("setup: %s approved at %s, explained at %s", t, a.get("digest"),
                          explained.get(t))
                return _refusal("APPROVED_UNEXPLAINED_MAPPING",
                                f"{t} was approved at a mapping that was not the one explained; "
                                f"explain it again and approve again", status=409)
        approved = sorted(t for t, tm in ((result.get("mapping") or {}).get("tables") or {})
                          .items() if (tm.get("approval") or {}).get("principal_id")
                          == principal.principal_id and tm.get("status") == "approved")
        _record(rt, spec, step="approve_mapping", status="completed", by=principal.principal_id,
                requested_by=actor_of(principal),
                who=actor_of(principal),
                actor="person", reason=f"approved the mapping of {', '.join(approved) or 'no '}"
                                       f" table(s) of {source_id}",
                counts={"tables": len(approved)})
        return {"mapping": result.get("mapping"), "state": setup_state(app, conn)}

    @app.post("/adapter-setup/{source_id}/documents/approve")
    def setup_documents_approve(source_id: str, body: DocumentsBody = Body(...),
                                principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Codex R-027 AAX f2 #7: a PERSON decides to build the documents the latest discovery
        captured - the discovery run and the documents digest they were shown. W-05: documents
        an earlier sync already built are in the same decision, as documents the keeper KEEPS
        (not rebuilt); a source that is entirely built is adopted this way."""
        from databridge_adapter_agents.setup import (discovery_problems,  # noqa: PLC0415
                                                     documents_digest, record_documents_decision)
        from databridge_mcp_ingest.plans import (ApprovalRefused,  # noqa: PLC0415
                                                 require_authenticated_approver)

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            return _refusal("NOT_STARTED", "press Set up first", status=409)
        try:
            who = require_authenticated_approver(principal, what="the documents decision")
        except ApprovalRefused as exc:
            # R-028 #11: the standard approver code (MACHINE_ACTOR for a machine principal),
            # the former NOT_A_PERSON kept as reason_code; the gate's text is logged (#6)
            return _exc_refusal(exc, "NOT_A_PERSON", 409, "setup: documents approver refused",
                                principal=principal, source_id=source_id)
        with rt.source_lock(source_id):
            g = _gate(rt, conn)
            why = discovery_problems(g["discovery"], plan_digest=g["plan_digest"])
            if why:
                return _refusal("DISCOVERY_REQUIRED", "; ".join(why), status=409)
            digest = documents_digest(g["discovery"])
            if digest is None:
                return _refusal("NO_DOCUMENTS", "the discovery captured no document the screen "
                                                "allowed and none an earlier run built",
                                status=409)
            if body.discovery_run_id != g["discovery"].get("run_id") or \
                    body.documents_digest != digest:
                return _refusal("DOCUMENTS_CHANGED", "the documents shown are not the latest "
                                                     "discovery's; reload", status=409)
            rec = record_documents_decision(rt.manager.registry.root, source_id, who=who,
                                            discovery=g["discovery"])
        _record(rt, spec, step="approve_documents", status="completed",
                by=principal.principal_id, who=who, actor="person",
                requested_by=actor_of(principal),
                reason=(f"decided to build {rec.get('to_build', 0)} and keep "
                        f"{rec.get('to_keep', 0)} already-built document(s) discovery "
                        f"{rec['discovery_run_id']} captured from {source_id}"),
                counts={"to_build": rec.get("to_build", 0), "to_keep": rec.get("to_keep", 0)})
        return {"decision": rec, "state": setup_state(app, conn)}

    @app.post("/adapter-setup/{source_id}/handoff", status_code=202)
    def setup_handoff(source_id: str, background: BackgroundTasks,
                      body: Optional[HandoffBody] = Body(None),
                      principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_adapter_agents.setup import CADENCES  # noqa: PLC0415

        rt, conn, err = _need(source_id)
        if err:
            return err
        spec = _keeper(rt.manager, source_id)
        if spec is None:
            return _refusal("NOT_STARTED", "press Set up first", status=409)
        from databridge_adapter_agents.adapt import (_MACHINE,  # noqa: PLC0415
                                                     DELEGATED_POLICY, Policy)
        from databridge_adapter_agents.setup import (gate_binding,  # noqa: PLC0415
                                                     mark_setup_agent, setup_source_of)

        # Codex R-027 AAX f2 #2: a machine never hands off (and is never recorded as a person)
        if is_machine(principal) or principal.principal_id.strip().lower().startswith(_MACHINE):
            return _refusal("MACHINE_ACTOR", f"{principal.principal_id!r} is a machine actor; "
                                             f"only a signed-in person hands a source off",
                            status=409)
        cadence = (body.cadence if body else "daily")
        if cadence not in CADENCES:
            return _refusal("BAD_CADENCE", f"cadence is one of {sorted(CADENCES)}")
        g = _gate(rt, conn)
        if g["problems"]:
            return _refusal("HANDOFF_NOT_READY", "; ".join(g["problems"]), status=409)
        # Codex R-027 AAX f2 #8: every pending decision is deferred BY NAME, or decided first
        pending = sorted({d["key"] for d in g["pending"]})
        deferred = sorted(set(body.deferred if body else []))
        if deferred != pending:
            missing = [k for k in pending if k not in deferred]
            extra = [k for k in deferred if k not in pending]
            return _refusal("DECISIONS_PENDING",
                            "decide or defer every pending decision by name: "
                            + ("; ".join(d["what"] for d in g["pending"] if d["key"] in missing)
                               or "")
                            + (f" (not pending: {extra})" if extra else ""), status=409)
        if spec.status == "paused":
            return _refusal("ADAPTER_AGENT_PAUSED", f"{spec.id} is paused; resume it on its "
                                                    f"page", status=409)
        if spec.status == "active":
            # Codex R-027 AAX f1 #7: an agent already active was never activated HERE; it builds
            # only under a sealed delegation in force and its own gate (plan approved, not stale)
            seal, why = rt.manager.delegation_status(spec)
            blocked = rt.manager.agent_gate(spec)
            if seal is None or blocked:
                return _refusal("KEEPER_NOT_SEALED",
                                f"{spec.id} is already active, but "
                                + ("; ".join(blocked) if blocked else
                                   f"no sealed delegation is in force ({why})")
                                + "; pause and resume it on its page so a person re-activates "
                                  "it, then hand off", status=409)
        try:
            if spec.status == "draft":
                if Policy.of(spec).policy != DELEGATED_POLICY:
                    return _refusal("NO_SEAL_POSSIBLE",
                                    f"{spec.id} delegates nothing (adapt.policy "
                                    f"{Policy.of(spec).policy!r}); a keeper builds only under a "
                                    f"sealed delegation", status=409)
                if setup_source_of(rt.manager.registry.root, spec.id) is None:
                    mark_setup_agent(rt.manager.registry.root, spec.id, source_id,
                                     by=principal.principal_id)
                if spec.id.startswith("keeper-") and dict(spec.sync.model_dump(mode="json")) \
                        != CADENCES[cadence]:
                    spec = rt.manager.update(spec.id, {"sync": CADENCES[cadence]}, by=principal)
                rt.manager.refresh_plans(spec.id, by=principal.principal_id)
                g = _gate(rt, conn)
                if g["problems"]:
                    return _refusal("HANDOFF_NOT_READY", "; ".join(g["problems"]), status=409)
                # Codex R-027 AAX f2 #1: the PERSON's authorisation, bound to the setup as it is
                rt.manager.authorize_setup_handoff(spec.id, by=principal, binding=gate_binding(
                    plan_digest=g["plan_digest"], discovery=g["discovery"],
                    mapping=g["mapping"], explanation=g["explanation"],
                    documents=g["documents_decision"], deferred=deferred))
                # the PERSON activates it: the manager verifies that authorisation against the
                # setup as it is now, and mints the sealed delegation for this principal (AA3)
                spec = rt.manager.activate(spec.id, by=principal)
                seal, why = rt.manager.delegation_status(spec)
                if seal is None or seal.get("principal_id") != principal.principal_id:
                    # f2 #2: no build without a seal in force - and the agent does not stay active
                    rt.manager.pause(spec.id, by=principal)
                    return _refusal("NO_SEAL", f"{spec.id} was not sealed to "
                                               f"{principal.principal_id} ({why}); it is paused "
                                               f"and nothing was built", status=409)
                _record(rt, spec, step="activate", status="completed",
                        by=principal.principal_id, who=actor_of(principal), actor="person",
                        requested_by=actor_of(principal),
                        reason=f"activated {spec.id} as the keeper of {source_id} "
                               f"(sync: {cadence}; deferred: {len(deferred)})")
        except Exception as exc:                                      # noqa: BLE001
            return _exc_refusal(exc, "ADAPTER_AGENT_NOT_ACTIVATABLE", 409,
                                "setup: hand-off refused", principal=principal,
                                source_id=source_id)
        rt.reload_engine()
        try:
            job = rt.jobs.start("adapter_sync", spec.id, by=principal.principal_id)
        except JobBusy:
            return _refusal("SYNC_IN_PROGRESS", f"{spec.id} is already syncing", status=409)
        _record(rt, spec, step="build", status="started", by=principal.principal_id,
                requested_by=actor_of(principal),
                reason=f"started the build run of {source_id} from the approved mapping "
                       f"(job {job['job_id']}); its outcome is recorded when it ends")
        background.add_task(_build_job, app, spec.id, job["job_id"], principal.principal_id,
                            source_id, actor_of(principal))
        return {"agent": spec.id, "job_id": job["job_id"], "status": "running",
                "started_by": principal.principal_id}


__all__ = ["register_adapter_setup_routes", "setup_state", "gateway_plan_stale"]
