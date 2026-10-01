"""`/adapter-agents` — the managers of sources (BUILD_PLAN §5.6 AdapterAgentSpec, §6 M/U).

    GET    /adapter-agents                              list (``?include_deleted=true``)
    POST   /adapter-agents                              create from a spec (YAML text or object)
    GET    /adapter-agents/proposals                    ontology proposals the agents filed
    POST   /adapter-agents/proposals/{pid}/forward      → POST /ontology/proposals, origin=adapter_agent
    GET    /adapter-agents/{id}                         spec + history + health
    PATCH  /adapter-agents/{id}                         update fields (immutable ones refused)
    POST   /adapter-agents/{id}/pause | /resume
    DELETE /adapter-agents/{id}                         a STATUS: spec and history are kept
    POST   /adapter-agents/{id}/sync                    background sync → {job_id}
    GET    /adapter-agents/{id}/jobs/{job_id}           that sync's live progress + summary
    GET    /adapter-agents/{id}/health
    GET    /adapter-agents/{id}/runs                    §5.7 run records
    POST   /adapter-agents/{id}/refresh-plans           mirror on-disk plan status into the spec
    GET    /adapter-agents/{id}/adaptations             held drift adaptations (``?status=pending``)
    POST   /adapter-agents/{id}/adaptations/{aid}/approve | /reject   owner only, as the principal

Every route requires `auth.require_principal`; every write records the principal as ``by``.

**Forwarding never auto-applies.** A forwarded proposal goes through the SAME handler as a
person's `POST /ontology/proposals` (looked up on the app, not re-implemented), with
``origin="adapter_agent"`` — which `/ontology` refuses to auto-approve whatever the toggle says
(§12 change log, founder's rule). ``proposed_by`` stays the filing agent
(``adapter_agent:<id>``); the principal who forwarded it is recorded beside it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import JobBusy, now_iso, platform_of, reload_graph

log = logging.getLogger("databridge.gateway.adapter_agents")


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _spec_json(spec: Any) -> Any:
    return spec.model_dump(mode="json") if hasattr(spec, "model_dump") else spec


class CreateAdapterAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: an `adapter_agent/v1` spec — YAML text or the object
    spec: Union[str, dict]


class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = ""


class DecisionBody(BaseModel):
    """Approve / reject a held adaptation. Deliberately has NO ``by``: the decider is the
    authenticated principal; any extra field (``by`` included) is ignored."""
    model_config = ConfigDict(extra="ignore")
    reason: Optional[str] = None
    #: optional (R-025): the ``changes_digest`` of the held change the decider was shown. An
    #: approval naming a different set of changes is refused (409), so one decision covers
    #: exactly the change a person saw.
    changes_digest: Optional[str] = None


def agent_owner(manager: Any, agent_id: str) -> Optional[str]:
    """The adapter agent's owner: the ``by`` of its ``create`` history entry — the same rule
    `activity_routes.ActivityView.adapter_agent_owner` applies. ``None`` if none is recorded,
    and then NOBODY decides from the app (the refusal says so)."""
    try:
        history = manager.registry.history(agent_id)
    except Exception:                                                 # noqa: BLE001
        return None
    for rec in history:
        if rec.get("action") == "create" and isinstance(rec.get("by"), str) and rec["by"].strip():
            return rec["by"].strip()
    return None


def adaptation_view(rec: dict) -> dict:
    """What a person needs to decide a held adaptation, read from the engine's record: the kind,
    what changed, why it was held, and when. Nothing is restated or inferred."""
    drifts = [d for d in (rec.get("drifts") or []) if isinstance(d, dict)]
    kinds = sorted({k for d in drifts for k in ([d.get("kind")] + (
        ["subject_touching"] if d.get("subject_touching") else [])) if k})
    classification = rec.get("classification") or {}
    held_because = [v.get("why") for v in classification.values()
                    if isinstance(v, dict) and v.get("asked") and v.get("why")]
    hold = rec.get("hold") or {}
    from databridge_adapter_agents.adapt import changes_digest_of  # noqa: PLC0415

    drift_ids = sorted(str(i) for i in rec.get("drift_ids") or [])
    return {"adaptation_id": rec.get("adaptation_id"), "status": rec.get("status"),
            # R-025 (additive): exactly the changes this decision covers, and their digest
            "drift_ids": drift_ids,
            "changes_digest": rec.get("changes_digest") or changes_digest_of(drift_ids),
            "connection": rec.get("connection"), "unit": rec.get("unit"), "kinds": kinds,
            "changed": [d.get("detail") for d in drifts if d.get("detail")],
            "held_because": held_because, "problems": list(rec.get("problems") or []),
            "hold": ({"level": hold.get("level"), "why": hold.get("why")} if hold else None),
            "action": rec.get("action"),
            "proposal_artifact": (rec.get("proposal") or {}).get("artifact"),
            "created_at": rec.get("created_at")}


def _adaptation_refusal(text: str) -> JSONResponse:
    """`AdaptationRefused` -> an honest status. The engine's messages are the contract here."""
    if text.startswith("no adaptation"):
        return _refusal("UNKNOWN_ADAPTATION", text, status=404)
    if "only a pending" in text:
        return _refusal("ADAPTATION_ALREADY_DECIDED", text, status=409)
    if text.startswith("REJECTED_CHANGE_BUNDLED"):
        return _refusal("REJECTED_CHANGE_BUNDLED", text, status=409)
    if text.startswith("CHANGES_DIGEST_MISMATCH"):
        return _refusal("CHANGES_DIGEST_MISMATCH", text, status=409)
    if "needs a reason" in text:
        return _refusal("REASON_REQUIRED", text)
    if "REFUSED" in text:
        return _refusal("PRINCIPAL_REFUSED", text, status=403)
    return _refusal("ADAPTATION_REFUSED", text)


def _find_ontology_proposal_endpoint(app: FastAPI):
    for route in app.routes:
        if getattr(route, "path", None) == "/ontology/proposals" and \
                "POST" in (getattr(route, "methods", None) or set()):
            return route.endpoint
    return None


def _proposals(manager: Any) -> list[dict]:
    """The agents' proposal log, folded: one row per proposal with its forwarding state."""
    rows: dict[str, dict] = {}
    forwards: dict[str, dict] = {}
    for rec in manager.proposal_log.all():
        pid = rec.get("proposal_id")
        if not pid:
            continue
        if rec.get("event") == "forwarded":
            forwards[pid] = rec
        elif pid not in rows:
            rows[pid] = dict(rec)
    for pid, fw in forwards.items():
        if pid in rows:
            rows[pid]["forwarded"] = {k: fw.get(k) for k in
                                      ("at", "by", "ontology_proposal_id", "ontology_status")}
    return list(rows.values())


def _sync_job(app: FastAPI, agent_id: str, job_id: str, by: str) -> None:
    rt = platform_of(app)
    try:
        summary = rt.manager.sync(agent_id, trigger={"type": "manual", "by": by})
        data = summary.as_dict()
        rt.jobs.finish(job_id, status=("failed" if data.get("status") in ("failed", "refused")
                                       else "completed"), result=data)
    except Exception as exc:                                          # noqa: BLE001
        log.exception("adapter agent %s sync failed", agent_id)
        rt.jobs.finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        rt.jobs.update(job_id, graph_counts=reload_graph(app))


def filing_agent_of(row: dict) -> Optional[str]:
    """``adapter_agent:<id>`` from the log's OWN ``adapter_agent`` field — the one the manager
    writes beside the record — never from the ``proposed_by`` inside the stored request, which is
    the part of the record a malformed or compromised filing could set to anything."""
    agent = row.get("adapter_agent")
    return f"adapter_agent:{agent}" if isinstance(agent, str) and agent.strip() else None


def forward_marker(proposal_id: str) -> str:
    """The idempotency key a forwarded ontology proposal carries in its description."""
    return f"[forwarded-from adapter-agent proposal {proposal_id}]"


def _claim_path(manager: Any, proposal_id: str) -> Path:
    safe = hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()[:32]
    return Path(manager.proposal_log.path).parent / "forward_claims" / f"{safe}.claim"


def _claimed_by(claim: Path) -> Optional[str]:
    try:
        return json.loads(claim.read_text(encoding="utf-8")).get("by")
    except (OSError, ValueError):
        return None


def _release_claim(claim: Path) -> None:
    """Remove a claim THIS request created and whose side effect did not happen (a lock file,
    not a record: the proposal log is the record, and it is append-only)."""
    try:
        claim.unlink()
    except OSError:
        log.warning("could not release forward claim %s", claim)


def _ontology_proposal_with(marker: str, proposer: str) -> Optional[dict]:
    try:
        from .ontology_routes import load_state                       # noqa: PLC0415
        proposals = load_state().get("proposals") or []
    except Exception:                                                 # noqa: BLE001
        log.exception("ontology state unreadable while reconciling a forward")
        return None
    return next((p for p in proposals if marker in str(p.get("description") or "")
                 and p.get("proposed_by") == proposer), None)


def _log_forwarded(rt: Any, proposal_id: str, by: Optional[str], result: dict, *,
                   reconciled: bool = False) -> None:
    rec = {"event": "forwarded", "proposal_id": proposal_id, "at": now_iso(), "by": by,
           "ontology_proposal_id": result.get("id"), "ontology_status": result.get("status"),
           "origin": "adapter_agent"}
    if reconciled:
        rec["reconciled"] = True
    rt.manager.proposal_log.append(rec)


def register_adapter_agents_routes(app: FastAPI) -> None:
    install_auth_handler(app)
    # One forward at a time per app: the check "not yet forwarded" and the append "forwarded" must
    # not interleave, or two concurrent forwards both file an ontology proposal (R-019 #6).
    forward_lock = threading.Lock()

    def _mgr():
        rt = platform_of(app)
        if rt.manager is None:
            return None, _refusal("ADAPTER_AGENTS_UNAVAILABLE",
                                  "the adapter-agent manager did not build: "
                                  + "; ".join(rt.problems), status=503)
        return rt, None

    def _registry_error(exc: Exception) -> JSONResponse:
        text = str(exc)
        if text.startswith("no adapter agent"):
            return _refusal("UNKNOWN_ADAPTER_AGENT", text, status=404)
        if "already exists" in text:
            return _refusal("ADAPTER_AGENT_EXISTS", text, status=409)
        if "was deleted" in text:
            return _refusal("ADAPTER_AGENT_DELETED", text, status=410)
        return _refusal("BAD_ADAPTER_AGENT", text)

    @app.get("/adapter-agents")
    def aa_list(include_deleted: bool = Query(False),
                _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        specs = rt.manager.registry.list(include_deleted=include_deleted)
        out = []
        for s in specs:
            row = {"spec": _spec_json(s), "deleted": rt.manager.registry.is_deleted(s.id)}
            try:
                h = rt.manager.health(s.id)
                row["overall"] = h["overall"]
                row["last_sync"] = h["last_sync"]
                row["issues"] = h["issues"]
            except Exception as exc:                                  # noqa: BLE001
                row["overall"] = "unknown"
                row["issues"] = [f"health unreadable: {exc}"]
            out.append(row)
        return {"adapter_agents": out, "problems": dict(rt.manager.registry.problems)}

    @app.post("/adapter-agents")
    def aa_create(body: CreateAdapterAgent = Body(...),
                  principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        from databridge_workflow_spec import AdapterAgentSpec, SpecError, load_spec  # noqa

        try:
            spec = load_spec(body.spec)
        except (SpecError, ValueError) as exc:
            return _refusal("BAD_ADAPTER_AGENT", str(exc))
        if not isinstance(spec, AdapterAgentSpec):
            return _refusal("BAD_ADAPTER_AGENT",
                            f"expected an adapter_agent/v1 spec, got {type(spec).__name__}")
        try:
            # INT5 (AA3 cross-track 1): the principal OBJECT, which build_platform registers as
            # trusted, so an active auto_additive agent gets its sealed delegation. A bare id
            # string is recorded but seals nothing (additive changes would all be held).
            created = rt.manager.create(spec, by=principal)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(created), "by": principal.principal_id}

    # -- proposals (declared BEFORE /{agent_id} so the literal path wins) ------------------------
    @app.get("/adapter-agents/proposals")
    def aa_proposals(_p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        return {"proposals": _proposals(rt.manager)}

    @app.post("/adapter-agents/proposals/{proposal_id}/forward")
    def aa_forward(proposal_id: str,
                   principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        with forward_lock:
            return _forward_locked(rt, proposal_id, principal)

    def _forward_locked(rt, proposal_id: str, principal: AuthenticatedPrincipal):
        rows = {r["proposal_id"]: r for r in _proposals(rt.manager)}
        row = rows.get(proposal_id)
        if row is None:
            return _refusal("UNKNOWN_PROPOSAL", f"no adapter-agent proposal {proposal_id!r}",
                            status=404)
        if row.get("forwarded"):
            return _refusal("ALREADY_FORWARDED",
                            f"{proposal_id} was forwarded as "
                            f"{row['forwarded'].get('ontology_proposal_id')} by "
                            f"{row['forwarded'].get('by')}", status=409)
        endpoint = _find_ontology_proposal_endpoint(app)
        if endpoint is None:
            return _refusal("ONTOLOGY_UNAVAILABLE",
                            "POST /ontology/proposals is not mounted on this gateway", status=503)
        from .ontology_routes import ProposalRequest                   # noqa: PLC0415

        request = dict(row.get("request") or {})
        # The origin is set HERE, not taken from the stored record: whatever the log says, a
        # kind an adapter agent suggested is forwarded as one — and never auto-applies.
        request["origin"] = "adapter_agent"
        # ...and so is the proposer: OVERWRITTEN from the log's `adapter_agent`, not kept from the
        # request (`setdefault` let a stored request claim a person, or another agent, filed it).
        proposer = filing_agent_of(row)
        if proposer is None:
            return _refusal("BAD_PROPOSAL", f"{proposal_id} names no filing adapter agent; it "
                                            f"cannot be forwarded with a trustworthy proposer")
        request["proposed_by"] = proposer
        # The idempotency key rides on the ontology proposal itself (R-019 round 2, platform #7):
        # `forward_marker` in its description names the adapter proposal it came from, so a
        # retry after a crash between "ontology proposal created" and "forwarded logged" finds
        # the one that exists instead of filing a second.
        marker = forward_marker(proposal_id)
        request["description"] = f"{request.get('description') or ''} {marker}".strip()
        try:
            payload = ProposalRequest.model_validate(request)
        except Exception as exc:                                      # noqa: BLE001
            return _refusal("BAD_PROPOSAL", f"the filed proposal is not a valid ontology "
                                            f"proposal: {exc}")
        # A DURABLE, cross-process claim before the side effect: exclusive file creation is
        # atomic across workers, so exactly one forward proceeds for this proposal id.
        claim = _claim_path(rt.manager, proposal_id)
        try:
            claim.parent.mkdir(parents=True, exist_ok=True)
            with open(claim, "x", encoding="utf-8") as fh:
                fh.write(json.dumps({"proposal_id": proposal_id, "at": now_iso(),
                                     "by": principal.principal_id}))
        except FileExistsError:
            return _reconcile_claim(rt, proposal_id, proposer, marker, claim)
        try:
            result = endpoint(payload=payload, principal=principal)
        except Exception:
            _release_claim(claim)                         # nothing was filed: allow a retry
            raise
        if isinstance(result, JSONResponse):
            _release_claim(claim)                         # /ontology refused: nothing filed
            return result                                             # /ontology's own refusal
        _log_forwarded(rt, proposal_id, principal.principal_id, result)
        return {"proposal_id": proposal_id, "forwarded_by": principal.principal_id,
                "ontology_proposal": result}

    def _reconcile_claim(rt, proposal_id: str, proposer: str, marker: str, claim):
        """A claim exists without a `forwarded` record: another worker is forwarding right now,
        or an earlier forward stopped after its claim. If /ontology already holds the proposal
        (found by its marker), record it — the retry completes the log, it files nothing."""
        existing = _ontology_proposal_with(marker, proposer)
        if existing is not None:
            _log_forwarded(rt, proposal_id, _claimed_by(claim), existing, reconciled=True)
            return _refusal("ALREADY_FORWARDED",
                            f"{proposal_id} was forwarded as {existing.get('id')} (the record of "
                            f"it was completed now)", status=409)
        return _refusal("FORWARD_IN_PROGRESS",
                        f"{proposal_id} is being forwarded (claim {claim.name}); no ontology "
                        f"proposal carries it yet. Retry shortly; if it never appears, an "
                        f"operator inspects {claim}", status=409)

    # -- one agent -------------------------------------------------------------------------------
    @app.get("/adapter-agents/{agent_id}")
    def aa_get(agent_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.registry.get(agent_id, include_deleted=True)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        try:
            health = rt.manager.health(agent_id)
        except Exception as exc:                                      # noqa: BLE001
            health = {"overall": "unknown", "issues": [f"health unreadable: {exc}"]}
        return {"spec": _spec_json(spec), "deleted": rt.manager.registry.is_deleted(agent_id),
                "state": rt.manager.registry.state(agent_id),
                "history": rt.manager.registry.history(agent_id), "health": health,
                "next_fire": _next_fire(rt, agent_id)}

    @app.patch("/adapter-agents/{agent_id}")
    def aa_patch(agent_id: str, changes: dict = Body(...),
                 principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.update(agent_id, dict(changes), by=principal)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(spec), "by": principal.principal_id}

    @app.post("/adapter-agents/{agent_id}/pause")
    def aa_pause(agent_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.pause(agent_id, by=principal.principal_id)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(spec)}

    @app.post("/adapter-agents/{agent_id}/activate")
    def aa_activate(agent_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        """DRAFT -> active, as the authenticated principal (never a body name). The manager runs
        the gate — every crawl plan approved (spec record AND plan on disk) and a known adapt
        policy — and refuses with its reasons otherwise; a refused draft stays a draft."""
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.activate(agent_id, by=principal)
        except Exception as exc:                                      # noqa: BLE001
            text = str(exc)
            if "cannot be activated" in text or "only a draft is activated" in text:
                return _refusal("ADAPTER_AGENT_NOT_ACTIVATABLE", text, status=409)
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(spec), "by": principal.principal_id}

    @app.post("/adapter-agents/{agent_id}/resume")
    def aa_resume(agent_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.resume(agent_id, by=principal)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(spec)}

    @app.delete("/adapter-agents/{agent_id}")
    def aa_delete(agent_id: str, body: Optional[DeleteBody] = Body(None),
                  principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.delete(agent_id, by=principal.principal_id,
                                     reason=(body.reason if body else ""))
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(spec), "deleted": True,
                "note": "deletion is a status: the spec file and its history are kept"}

    @app.post("/adapter-agents/{agent_id}/sync", status_code=202)
    def aa_sync(agent_id: str, background: BackgroundTasks,
                principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.registry.get(agent_id)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        # Said up front rather than as a 202 that later "fails": the manager refuses a paused
        # agent at execution time anyway (it re-reads the registry there), this only answers
        # the person honestly now.
        status = getattr(spec, "status", None)
        if status == "paused":
            return _refusal("ADAPTER_AGENT_PAUSED", f"adapter agent {agent_id!r} is paused; "
                                                    f"resume it to sync", status=409)
        if status != "active":
            # A draft is never runnable by any route (R-019 round 2, platform #4): it is turned
            # live by POST /adapter-agents/{id}/activate, which runs the manager's checks. The
            # manager refuses a non-active agent at execution time too (`manager.sync`).
            return _refusal("ADAPTER_AGENT_NOT_ACTIVE",
                            f"adapter agent {agent_id!r} is {status!r}; only an active agent "
                            f"syncs — activate it first (POST /adapter-agents/{agent_id}/activate)",
                            status=409)
        try:
            job = rt.jobs.start("adapter_sync", agent_id, by=principal.principal_id)
        except JobBusy as busy:
            return _refusal("SYNC_IN_PROGRESS", str(busy), status=409)
        background.add_task(_sync_job, app, agent_id, job["job_id"], principal.principal_id)
        return {"job_id": job["job_id"], "status": "running", "started_by": principal.principal_id}

    @app.get("/adapter-agents/{agent_id}/jobs/{job_id}")
    def aa_job(agent_id: str, job_id: str,
               _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt = platform_of(app)
        job = rt.jobs.get(job_id)
        if job is None or job["kind"] != "adapter_sync" or job["key"] != agent_id:
            return _refusal("UNKNOWN_JOB", f"no sync job {job_id!r} for {agent_id!r}", status=404)
        return job

    @app.get("/adapter-agents/{agent_id}/health")
    def aa_health(agent_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            return rt.manager.health(agent_id)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)

    @app.get("/adapter-agents/{agent_id}/runs")
    def aa_runs(agent_id: str, _p: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        return {"agent_id": agent_id, "runs": rt.manager.run_records(agent_id),
                "in_flight": [j for j in rt.jobs.list("adapter_sync", agent_id)
                              if j["status"] == "running"]}

    # -- held drift adaptations (INT4): a person decides them from the app ---------------------
    @app.get("/adapter-agents/{agent_id}/adaptations")
    def aa_adaptations(agent_id: str, status: str = Query("pending"),
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            records = rt.manager.adaptations(agent_id)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        want = (status or "pending").strip().lower()
        owner = agent_owner(rt.manager, agent_id)
        return {"agent_id": agent_id, "status": want, "owner": owner,
                "may_decide": owner is not None and owner == principal.principal_id,
                "adaptations": [adaptation_view(r) for r in records
                                if want == "all" or r.get("status") == want]}

    def _decide(agent_id: str, adaptation_id: str, principal: AuthenticatedPrincipal,
                decide) -> Any:
        rt, err = _mgr()
        if err:
            return err
        try:
            rt.manager.registry.get(agent_id)                     # unknown 404 / deleted 410
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        owner = agent_owner(rt.manager, agent_id)
        if owner is None or owner != principal.principal_id:
            return _refusal("NOT_ADAPTER_AGENT_OWNER",
                            f"only the owner of adapter agent {agent_id!r} "
                            f"({owner or 'no owner is recorded'}) decides its held changes; "
                            f"you are {principal.principal_id!r}", status=403)
        from databridge_adapter_agents.adapt import AdaptationRefused  # noqa: PLC0415
        try:
            # the principal OBJECT the auth layer minted; never a body name (hard constraint 4)
            rec = decide(rt.manager, principal)
        except AdaptationRefused as exc:
            return _adaptation_refusal(str(exc))
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adaptation": adaptation_view(rec) | {
                    "decided_by": rec.get("decided_by"), "decided_at": rec.get("decided_at"),
                    "note": rec.get("note"), "version": rec.get("version")},
                "by": principal.principal_id}

    @app.post("/adapter-agents/{agent_id}/adaptations/{adaptation_id}/approve")
    def aa_approve_adaptation(agent_id: str, adaptation_id: str,
                              body: Optional[DecisionBody] = Body(None),
                              principal: AuthenticatedPrincipal = Depends(require_principal)):
        note = ((body.reason if body else None) or "").strip() or None
        digest = ((body.changes_digest if body else None) or "").strip() or None
        return _decide(agent_id, adaptation_id, principal,
                       lambda m, p: m.approve_adaptation(agent_id, adaptation_id, by=p,
                                                         note=note, changes_digest=digest))

    @app.post("/adapter-agents/{agent_id}/adaptations/{adaptation_id}/reject")
    def aa_reject_adaptation(agent_id: str, adaptation_id: str,
                             body: Optional[DecisionBody] = Body(None),
                             principal: AuthenticatedPrincipal = Depends(require_principal)):
        reason = ((body.reason if body else None) or "").strip()
        return _decide(agent_id, adaptation_id, principal,
                       lambda m, p: m.reject_adaptation(agent_id, adaptation_id, by=p,
                                                        reason=reason))

    @app.post("/adapter-agents/{agent_id}/refresh-plans")
    def aa_refresh(agent_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        rt, err = _mgr()
        if err:
            return err
        try:
            spec = rt.manager.refresh_plans(agent_id, by=principal.principal_id)
        except Exception as exc:                                      # noqa: BLE001
            return _registry_error(exc)
        rt.reload_engine()
        return {"adapter_agent": _spec_json(spec)}


def _next_fire(rt: Any, sid: str) -> Optional[str]:
    if rt.engine is None:
        return None
    try:
        n = rt.engine.next_fire(sid)
    except Exception:                                                 # noqa: BLE001
        return None
    return n.isoformat() if n else None


__all__ = ["register_adapter_agents_routes"]
