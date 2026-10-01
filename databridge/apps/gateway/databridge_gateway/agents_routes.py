"""`/agents` — user-defined agents from plain English, run on demand, with an approval inbox.

    POST /agents/compile                              the English box → DraftResult + draft_id
    POST /agents                                      save a draft (by draft_id), optionally activate
    GET  /agents                                      every agent (workflow + declaration pair)
    GET  /agents/approvals                            actions awaiting a person
    POST /agents/approvals/{run_id}/{action_id}/approve | /reject | /reverse
    GET  /agents/{id}                                 spec, declaration, recent runs, next fire
    POST /agents/{id}/run  {question, wait}           run once (background unless wait=true)
    GET  /agents/{id}/jobs/{job_id}                   a background run's state → its run_id
    GET  /agents/{id}/runs                            §5.7 run records
    GET  /agents/{id}/runs/{run_id}                   one record + its action history

``POST /agents/compile`` and ``POST /agents`` cover BOTH kinds the compiler recognises: an agent
draft is written with `save_draft` (workflow + agentdef declaration, §5.6); an adapter-agent draft
goes through the adapter-agent registry so its history records who created it.

**Who acts is the signed-in principal — always.** Approve / reject / reverse pass an
`databridge_agent_runtime.Approver` built from the `require_principal` result as ``by`` — the
runtime refuses a bare name (R-017 t02 finding 3); a ``by`` (or ``approved_by``) in the request
body is ignored. The run's ``trigger.by`` is the principal too.

**Only the agent's owner, or an operator, decides its actions** (E2E run 2, N-18;
`agent_ownership`). The owner is the principal who saved the agent, recorded at save time; an
agent saved before that has no owner and is operator-only. ``GET /agents/approvals`` marks each
pending action ``may_decide`` for the caller.

**What was previewed is what is saved.** ``/agents/compile`` keeps the `DraftResult` under a
``draft_id``; ``POST /agents`` saves THAT object. It does not re-compile the text, so a model that
answers differently the second time cannot change what a person confirmed.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent_ownership import (decision_refusal, is_operator, record_owner, recorded_owner,
                              save_refusal)
from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import (AgentNotAdmitted, JobBusy, approver_for, platform_of,
                               run_agent_now)

log = logging.getLogger("databridge.gateway.agents")


#: ids name files (`<agents_dir>/<id>.workflow.yaml`, `<runs>/agent/<id>/<run_id>.json`): no
#: separators, no leading dot, so a path parameter can never climb out of its directory.
_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    answers: dict[str, str] = Field(default_factory=dict)


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    activate: bool = False


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: Optional[str] = None
    wait: bool = False


class DecisionBody(BaseModel):
    #: ``extra="ignore"``: a client that sends ``by`` is not refused, it is simply not believed.
    model_config = ConfigDict(extra="ignore")
    reason: str = ""
    note: str = ""


def existing_ids() -> list[str]:
    from databridge_automation import load_specs_from_dirs            # noqa: PLC0415
    from databridge_workflow_spec import WorkflowSpec                 # noqa: PLC0415

    specs, _ = load_specs_from_dirs()
    return sorted(s.agent if isinstance(s, WorkflowSpec) else s.id for s in specs)


def current_catalogue():
    from databridge_automation import load_catalogue                  # noqa: PLC0415
    return load_catalogue(existing_ids=existing_ids())


def _agent_rows() -> tuple[list[dict], list[str]]:
    from databridge_agent_runtime.paths import agents_dir             # noqa: PLC0415
    from databridge_workflow_spec import WorkflowSpec, load_spec      # noqa: PLC0415

    base = agents_dir()
    rows, problems = [], []
    if not base.is_dir():
        return rows, problems
    for p in sorted(base.glob("*.workflow.yaml")):
        try:
            spec = load_spec(p)
        except Exception as exc:                                      # noqa: BLE001
            problems.append(f"{p.name}: {exc}")
            continue
        if not isinstance(spec, WorkflowSpec):
            problems.append(f"{p.name}: not a workflow/v1 spec")
            continue
        rows.append({"agent": spec.agent, "title": spec.title, "mode": spec.mode,
                     "status": spec.status, "trigger": spec.trigger.type,
                     "has_declaration": (base / f"{spec.agent}.agent.yaml").is_file()})
    return rows, problems


def may_view_agent_runs(principal: Any, agent_id: str) -> bool:
    """Whether ``principal`` may see ``agent_id``'s runs and their proposed actions (E2E run 3,
    P-09) — the rule `/activity` applies, and it FAILS CLOSED: an operator sees every agent; anyone
    else only an agent whose RECORDED owner they are. An agent with no recorded owner (or an
    unreadable record) is operator-only. A proposal carries its full arguments (an e-mail body, a
    path, SQL), so listing it is disclosing it."""
    if is_operator(principal):
        return True
    owner = recorded_owner(agent_id)
    return owner is not None and owner == principal.principal_id


def _run_files(agent_id: str) -> list:
    from databridge_agent_runtime import list_runs                    # noqa: PLC0415
    return list_runs(agent=agent_id)


#: RunRecord statuses (`completed | stopped | failed | refused`) that are a run which DID its
#: work: `stopped` is a run that hit a declared limit and recorded what it had. An allow-list, so a
#: failed or refused run is never reported as a completed job (R-019 platform #7).
_RUN_DONE = {"completed", "stopped"}


def job_status_for_record(status: Optional[str]) -> str:
    return "completed" if status in _RUN_DONE else "failed"


def _run_job(app: FastAPI, agent_id: str, job_id: str, trigger: dict) -> None:
    rt = platform_of(app)
    try:
        record = run_agent_now(app, agent_id, trigger=trigger)
        rt.jobs.finish(job_id, status=job_status_for_record(record.status), run_id=record.run_id,
                       run_status=record.status,
                       result={"run_id": record.run_id, "status": record.status})
    except Exception as exc:                                          # noqa: BLE001
        log.exception("agent %s run failed", agent_id)
        rt.jobs.finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")


def register_agents_routes(app: FastAPI) -> None:
    install_auth_handler(app)

    # -- the English box ------------------------------------------------------------------------
    @app.post("/agents/compile")
    def agents_compile(body: CompileRequest = Body(...),
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_automation import compile_english              # noqa: PLC0415

        rt = platform_of(app)
        if not body.text.strip():
            return _refusal("EMPTY_TEXT", "say what the agent should do")
        try:
            result = compile_english(body.text, current_catalogue(), harness=rt.harness,
                                     answers=dict(body.answers) or None)
        except ValueError as exc:
            return _refusal("CANNOT_COMPILE", str(exc))
        draft_id = rt.keep_draft(result, by=principal.principal_id)
        return {"draft_id": draft_id, **result.as_record()}

    @app.post("/agents")
    def agents_save(body: SaveRequest = Body(...),
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_automation import ActivationRefused, save_draft  # noqa: PLC0415
        from databridge_workflow_spec import AdapterAgentSpec          # noqa: PLC0415

        rt = platform_of(app)
        held = rt.drafts.get(body.draft_id)
        if held is None:
            return _refusal("UNKNOWN_DRAFT", f"no draft {body.draft_id!r}; compile the text "
                                             f"again (drafts are kept in memory only)", status=404)
        # A draft is saved only by the principal who previewed it (R-019 platform #5): "what was
        # previewed is what is saved" means nothing if someone who never saw the preview can
        # save — and activate — another person's draft by learning its id.
        if held.get("by") != principal.principal_id:
            log.warning("agents: %s tried to save %s's draft %s", principal.principal_id,
                        held.get("by"), body.draft_id)
            return _refusal("NOT_YOUR_DRAFT", "this draft was compiled by another principal; "
                            "compile the text yourself to review and save it", status=403)
        result = held["result"]
        if result.spec is None:
            return _refusal("NOTHING_TO_SAVE", "this draft has open questions and no spec yet: "
                            + "; ".join(q.text for q in result.open_questions), status=409)
        if body.activate and not result.activatable:
            why = [q.text for q in result.open_questions]
            if result.compilation is not None and not result.compilation.compiled:
                why += [r.get("detail", "") for r in result.compilation.refusals] or \
                       [result.compilation.reason]
            return _refusal("NOT_ACTIVATABLE", "this draft cannot be activated: "
                            + "; ".join(w for w in why if w), status=409)
        if isinstance(result.spec, AdapterAgentSpec):
            if rt.manager is None:
                return _refusal("ADAPTER_AGENTS_UNAVAILABLE", "; ".join(rt.problems), status=503)
            spec = result.spec.model_copy(update={"status": "active" if body.activate
                                                  else "draft"})
            try:
                saved = rt.manager.create(spec, by=principal)   # the object: seals (AA3)
            except Exception as exc:                                  # noqa: BLE001
                return _refusal("ADAPTER_AGENT_EXISTS" if "already exists" in str(exc)
                                else "BAD_ADAPTER_AGENT", str(exc),
                                status=409 if "already exists" in str(exc) else 422)
            rt.drafts.pop(body.draft_id, None)
            rt.reload_engine()
            return {"kind": "adapter_agent", "id": saved.id, "status": saved.status,
                    "saved_by": principal.principal_id}
        # N-18: an existing agent is replaced only by its owner or an operator, and the saver of
        # a new one is recorded as its owner — from the authenticated principal, never the body.
        refused = save_refusal(principal, result.spec_id)
        if refused:
            return _refusal(refused[0], refused[1], status=403)
        try:
            written = save_draft(result, activate=body.activate)
        except ActivationRefused as exc:
            return _refusal("NOT_ACTIVATABLE", str(exc), status=409)
        owner = record_owner(result.spec_id, principal.principal_id)
        rt.drafts.pop(body.draft_id, None)
        rt.reload_engine()
        log.info("agents: %s saved %s (activate=%s)", principal.principal_id, result.spec_id,
                 body.activate)
        return {"kind": "agent", "id": result.spec_id,
                "status": "active" if body.activate else "draft",
                "files": [str(p) for p in written], "saved_by": principal.principal_id,
                "owner": owner}

    @app.get("/agents")
    def agents_list(_p: AuthenticatedPrincipal = Depends(require_principal)):
        rows, problems = _agent_rows()
        return {"agents": rows, "problems": problems}

    # -- approvals (declared BEFORE /agents/{agent_id}) -----------------------------------------
    @app.get("/agents/approvals")
    def agents_approvals(_p: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_agent_runtime import pending_actions           # noqa: PLC0415
        # E2E run 3, P-09: the inbox lists ONLY what this caller may decide — the agent's recorded
        # owner, or an operator (`decision_refusal`, the same fail-closed rule as `/activity`).
        # Another owner's proposal is not listed at all: not its arguments, not that it exists,
        # and no count of what was left out. An unreadable or unknown run is left out too.
        visible = []
        for item in pending_actions():
            try:
                if decision_refusal(_p, item["run_id"]) is not None:
                    continue
            except (FileNotFoundError, ValueError):
                continue
            item["may_decide"] = True
            visible.append(item)
        return {"pending": visible}

    def _decide(action: str, run_id: str, action_id: str, body: Optional[DecisionBody],
                principal: AuthenticatedPrincipal):
        import databridge_agent_runtime as ar                          # noqa: PLC0415

        rt = platform_of(app)
        who = principal.principal_id                                   # never the body
        # N-18: only the agent's owner or an operator decides its actions. Checked BEFORE the
        # runtime is reached, so a refused decision leaves no event on the action log.
        try:
            refused = decision_refusal(principal, run_id)
        except FileNotFoundError as exc:
            return _refusal("UNKNOWN_ACTION", str(exc), status=404)
        if refused:
            log.warning("agents: %s may not %s %s/%s: %s", who, action, run_id, action_id,
                        refused[0])
            return _refusal(refused[0], refused[1], status=403)
        # The runtime takes the AUTHENTICATED principal, never a name (R-017 t02 finding 3).
        # Minted by the gateway's registered authenticator (RTF): a caller-built Approver is refused.
        approver = approver_for(principal)
        gw = None
        try:
            if action == "approve":
                gw = rt.new_gateway()
                record = ar.approve(run_id, action_id, approver, gateway=gw)
            elif action == "reject":
                record = ar.reject(run_id, action_id, approver,
                                   reason=(body.reason if body else ""))
            else:
                record = ar.reverse(run_id, action_id, approver,
                                    note=(body.note if body else ""))
        except ar.ActionRefused as exc:
            return _refusal("ACTION_REFUSED", str(exc), status=409)
        except (FileNotFoundError, KeyError, IndexError) as exc:
            return _refusal("UNKNOWN_ACTION", str(exc), status=404)
        except ValueError as exc:
            return _refusal("ACTION_REFUSED", str(exc), status=409)
        finally:
            if gw is not None:
                from .platform_runtime import close_gateway            # noqa: PLC0415
                close_gateway(gw)
        return {"run_id": run_id, "action_id": action_id, "decided_by": who,
                "record": record.to_dict()}

    @app.post("/agents/approvals/{run_id}/{action_id}/approve")
    def agents_approve(run_id: str, action_id: str, body: Optional[DecisionBody] = Body(None),
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        return _decide("approve", run_id, action_id, body, principal)

    @app.post("/agents/approvals/{run_id}/{action_id}/reject")
    def agents_reject(run_id: str, action_id: str, body: Optional[DecisionBody] = Body(None),
                      principal: AuthenticatedPrincipal = Depends(require_principal)):
        return _decide("reject", run_id, action_id, body, principal)

    @app.post("/agents/approvals/{run_id}/{action_id}/reverse")
    def agents_reverse(run_id: str, action_id: str, body: Optional[DecisionBody] = Body(None),
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        return _decide("reverse", run_id, action_id, body, principal)

    # -- one agent ------------------------------------------------------------------------------
    def _load(agent_id: str):
        from databridge_agent_runtime import MappingError, load_agent  # noqa: PLC0415
        if not _ID_RE.match(agent_id or ""):
            return None, _refusal("BAD_AGENT_ID", f"{agent_id!r} is not an agent id", status=400)
        try:
            return load_agent(agent_id), None
        except MappingError as exc:
            return None, _refusal("UNKNOWN_AGENT", str(exc), status=404)
        except Exception as exc:                                      # noqa: BLE001
            return None, _refusal("AGENT_UNREADABLE", str(exc), status=422)

    @app.get("/agents/{agent_id}")
    def agents_get(agent_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_agent_runtime.paths import agents_dir         # noqa: PLC0415

        pair, err = _load(agent_id)
        if err:
            return err
        spec, _decl = pair
        decl_path = agents_dir() / f"{agent_id}.agent.yaml"
        rt = platform_of(app)
        nxt = None
        if rt.engine is not None:
            try:
                n = rt.engine.next_fire(agent_id)
                nxt = n.isoformat() if n else None
            except Exception:                                         # noqa: BLE001
                nxt = None
        return {"spec": spec.model_dump(mode="json"),
                "declaration_yaml": decl_path.read_text(encoding="utf-8"),
                "runs": ([p.stem for p in _run_files(agent_id)][-20:]
                         if may_view_agent_runs(_p, agent_id) else []),          # P-09
                "next_fire": nxt}

    @app.post("/agents/{agent_id}/run", status_code=202)
    def agents_run(agent_id: str, background: BackgroundTasks,
                   body: Optional[RunRequest] = Body(None),
                   principal: AuthenticatedPrincipal = Depends(require_principal)):
        pair, err = _load(agent_id)
        if err:
            return err
        # The one admission gate every trigger path goes through (F2): a DRAFT or PAUSED agent
        # does not run from "run now" either.
        from databridge_automation import admission_refusal           # noqa: PLC0415
        refused = admission_refusal(pair[0])
        if refused:
            return _refusal("AGENT_NOT_RUNNABLE", f"agent {agent_id!r} cannot run: {refused}",
                            status=409)
        body = body or RunRequest()
        trigger = {"type": "manual", "by": principal.principal_id, "question": body.question}
        rt = platform_of(app)
        if body.wait:
            try:
                record = run_agent_now(app, agent_id, trigger=trigger)
            except AgentNotAdmitted as exc:
                return _refusal("AGENT_NOT_RUNNABLE", str(exc), status=409)
            except Exception as exc:                                  # noqa: BLE001
                return _refusal("RUN_FAILED", f"{type(exc).__name__}: {exc}", status=500)
            return JSONResponse(status_code=200, content={"run_id": record.run_id,
                                                          "record": record.to_dict()})
        try:
            job = rt.jobs.start("agent_run", agent_id, by=principal.principal_id)
        except JobBusy as busy:
            return _refusal("RUN_IN_PROGRESS", str(busy), status=409)
        background.add_task(_run_job, app, agent_id, job["job_id"], trigger)
        return {"job_id": job["job_id"], "status": "running", "started_by": principal.principal_id}

    @app.get("/agents/{agent_id}/jobs/{job_id}")
    def agents_job(agent_id: str, job_id: str,
                   _p: AuthenticatedPrincipal = Depends(require_principal)):
        job = platform_of(app).jobs.get(job_id)
        if job is None or job["kind"] != "agent_run" or job["key"] != agent_id:
            return _refusal("UNKNOWN_JOB", f"no run job {job_id!r} for {agent_id!r}", status=404)
        return job

    @app.get("/agents/{agent_id}/runs")
    def agents_runs(agent_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_agent_runtime import read_run                  # noqa: PLC0415

        if not _ID_RE.match(agent_id or ""):
            return _refusal("BAD_AGENT_ID", f"{agent_id!r} is not an agent id", status=400)
        if not may_view_agent_runs(_p, agent_id):
            # P-09: the same body an agent with no runs gives — nothing says the runs exist.
            return {"agent": agent_id, "runs": []}
        out = []
        for p in _run_files(agent_id):
            try:
                r = read_run(p)
            except Exception as exc:                                  # noqa: BLE001
                out.append({"run_id": p.stem, "status": "unreadable", "error": str(exc)})
                continue
            out.append({"run_id": r.run_id, "status": r.status, "started": r.started,
                        "ended": r.ended, "trigger": r.trigger,
                        "actions": [a.model_dump(mode="json") for a in r.actions]})
        out.sort(key=lambda r: r.get("started") or "")
        return {"agent": agent_id, "runs": out}

    @app.get("/agents/{agent_id}/runs/{run_id}")
    def agents_run_get(agent_id: str, run_id: str,
                       _p: AuthenticatedPrincipal = Depends(require_principal)):
        from databridge_agent_runtime import ActionLog, read_run, run_path  # noqa: PLC0415
        from databridge_agent_runtime.runs import actions_path         # noqa: PLC0415

        if not _ID_RE.match(run_id or "") or not _ID_RE.match(agent_id or ""):
            return _refusal("BAD_RUN_ID", f"{agent_id!r}/{run_id!r} is not a run id", status=400)
        path = run_path(run_id, agent=agent_id)
        # P-09: a run the caller may not see gets the SAME 404 as a missing one.
        if not path.is_file() or not may_view_agent_runs(_p, agent_id):
            return _refusal("UNKNOWN_RUN", f"no run {run_id!r} for agent {agent_id!r}",
                            status=404)
        record = read_run(path)
        return {"agent": agent_id, "record": record.to_dict(),
                "action_history": ActionLog(actions_path(path)).events()}


__all__ = ["register_agents_routes", "current_catalogue", "existing_ids",
           "job_status_for_record"]
