"""``/materialise`` — plans in, receipts out (ROADMAP Phase F; PHASE_PLAN Phase 6).

    GET  /materialise/plans                       shapes (built / not built) + receipts on disk
    GET  /materialise/catalogue                   what the data agent can plan against, live
    POST /materialise                             {"request": "<English>", "answers"?: {id: text}}
                                                  or {"plan": {...}}
                                                  → 202 {job_id}; the run is a background job.
                                                  A planner question comes back in the job's
                                                  refusal as ``question_items[{id, text}]``;
                                                  answer it by posting the SAME request with
                                                  ``answers`` (E2E run 3, P-10)
    GET  /materialise/jobs                        materialisation jobs, newest last
    GET  /materialise/jobs/{job_id}               status, progress events, result or refusal
    GET  /materialise/receipts                    every run on disk (summary)
    GET  /materialise/receipts/{plan_hash}        the full receipt.json
    GET  /materialise/receipts/{plan_hash}/files/{name}   download one output file

Every route requires a principal (`auth.require_principal`), like every other platform route: a
receipt names what was read, and a dataset IS what was read. The run executes as the signed-in
principal — its identity is stamped on every withholding record and a submitted plan must name
that same principal — never as a name in the request body.

**The materialiser is attached at boot** by `platform_runtime.start_platform` via
:func:`set_materialiser`. Until something is attached, ``POST /materialise`` still answers ``501``
naming what is missing, and ``GET /materialise/plans`` offers no shapes: a shape in a picker is a
shape the system claims it can produce. Once attached, the shapes offered are exactly the ones the
executor has a builder for; the rest are listed with ``built: false`` and the reason.

A materialiser that exposes only the two-method protocol (``plans``/``run``) is still served
synchronously — the job path is used when it can report progress (``PlatformMaterialiser``). Either
way ``run`` is called with ``by=<signed-in principal>``; one whose ``run`` cannot accept it is
refused (``MATERIALISER_NOT_PRINCIPAL_AWARE``), never run under its own default identity (R-021
Finding 12).

**A run is visible only to the principal who made it** (R-021 Finding 1). Job and receipt listings
are filtered to the caller; a job, receipt or file of another principal's run answers the same 404
a run that does not exist answers — its existence is not disclosed either. There is no
administrative override: none is defined, so none exists.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from fastapi import BackgroundTasks, Body, Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import JobBusy, JobTracker

log = logging.getLogger("databridge.gateway.materialise")

#: What has to exist before a receipt can be a measurement rather than a sentence. Reported only
#: while no materialiser is attached.
MISSING_COMPONENTS = (
    "a Materialisation Plan type with a canonical plan_hash (PHASE_PLAN Phase 6; the hash is the "
    "dataset's identity, so it must exist before any dataset ships)",
    "an adapter fleet that can resolve a plan's pointers (PHASE_PLAN Phase 3) — the gateway has "
    "the adapter CONTRACT and no adapter behind it",
    "a receipt writer that counts rows, pointers, withheld cells and verify() verdicts from the "
    "run itself",
    "a writer for the output shapes (parquet + receipt.json + croissant.json)",
)

NOT_IMPLEMENTED_MESSAGE = (
    "the materialiser is not attached. A receipt is a set of measurements — rows resolved, "
    "pointers followed, cells withheld, verdicts returned — and this endpoint will not invent them "
    "so the page renders. It answers with what is missing instead."
)

JOB_KIND = "materialise"


@runtime_checkable
class Materialiser(Protocol):
    """The two methods every materialiser answers, both returning measurements."""

    def plans(self) -> list[dict[str, Any]]:
        ...

    def run(self, plan: dict[str, Any], *, by: str) -> dict[str, Any]:
        """Runs as ``by`` — the signed-in principal — always (R-021 Finding 12)."""
        ...


class PlanRequest(BaseModel):
    """``{"request": "..."}`` or ``{"plan": {...}}``. Permissive at the edge: the materialiser
    validates the plan against the real `MaterialisationPlan` type, so a second schema here would
    be a second definition of the plan to drift from."""

    model_config = ConfigDict(extra="allow")


def set_materialiser(app: FastAPI, materialiser: Optional[Materialiser]) -> None:
    """Attach the materialiser (done at boot by `platform_runtime.start_platform`)."""
    app.state.materialiser = materialiser


def _materialiser(app: FastAPI) -> Optional[Materialiser]:
    found = getattr(app.state, "materialiser", None)
    return found if isinstance(found, Materialiser) else None


def _accepts_principal(impl: Any) -> bool:
    """Whether ``impl.run`` can be told who it runs as (a ``by`` keyword)."""
    try:
        params = inspect.signature(impl.run).parameters
    except (TypeError, ValueError):
        return False
    return "by" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD
                                 for p in params.values())


def _owned(doc_owner: Any, principal: AuthenticatedPrincipal) -> bool:
    return doc_owner is not None and doc_owner == principal.principal_id


def _run_owner(impl: Any, plan_hash: str) -> Any:
    """Who a run on disk executed as, from the materialiser's own record."""
    if callable(getattr(impl, "run_by", None)):
        return impl.run_by(plan_hash)
    doc = impl.receipt(plan_hash) if callable(getattr(impl, "receipt", None)) else None
    return ((doc or {}).get("platform") or {}).get("run_by")


def _runs_as_job(impl: Any) -> bool:
    return callable(getattr(impl, "receipts", None)) and callable(getattr(impl, "run", None))


def _jobs(app: FastAPI) -> JobTracker:
    """The platform's job tracker when the runtime is up (one place for every background job);
    otherwise one owned by this app, so a bare test app does not boot the whole platform."""
    rt = getattr(app.state, "platform", None)
    jobs = getattr(rt, "jobs", None)
    if isinstance(jobs, JobTracker):
        return jobs
    own = getattr(app.state, "materialise_jobs", None)
    if not isinstance(own, JobTracker):
        own = JobTracker()
        app.state.materialise_jobs = own
    return own


def _refusal(code: str, message: str, status: int = 422, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None, **extra}})


def _run_job(app: FastAPI, impl: Any, body: dict, by: str, job_id: str) -> None:
    jobs = _jobs(app)
    try:
        result = impl.run(body, by=by, progress=lambda ev: jobs.progress(job_id, ev))
    except Exception as exc:                                         # noqa: BLE001
        code = getattr(exc, "code", None)
        if code:
            # A refusal is an ANSWER (the compiler or the policy said no, by name), not a crash.
            # A planner QUESTION carries its id, so it can be answered back (E2E run 3, P-10).
            refusal = {"code": code, "message": getattr(exc, "message", str(exc))}
            if callable(getattr(exc, "refusal", None)):
                refusal = {**refusal, **exc.refusal()}
            jobs.finish(job_id, status="refused", error=getattr(exc, "message", str(exc)),
                        refusal=refusal)
        else:
            log.exception("materialisation failed")
            jobs.finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        return
    jobs.finish(job_id, status="succeeded", result=result,
                plan_hash=(result or {}).get("plan_hash"))


def register_materialise_routes(app: FastAPI) -> None:
    """Attach the ``/materialise`` routes."""
    install_auth_handler(app)

    @app.get("/materialise/plans")
    def list_plans(principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        if impl is None:
            return {
                "available": False,
                "plans": [],
                # Empty, not the four names from the design doc. A shape offered by a picker is a
                # shape the system claims it can produce.
                "shapes": [],
                "missing": list(MISSING_COMPONENTS),
                "note": NOT_IMPLEMENTED_MESSAGE,
            }
        # Shapes are public; receipts are the caller's own (R-021 Finding 1).
        plans = [p for p in impl.plans()
                 if not p.get("receipt") or _owned(p.get("run_by"), principal)]
        shape_rows = [p for p in plans if not p.get("receipt")]
        return {"available": True,
                "plans": plans,
                "shapes": sorted({str(p.get("shape")) for p in shape_rows
                                  if p.get("shape") and p.get("built", True)}),
                "shapes_not_built": [{"shape": p.get("shape"), "reason": p.get("reason")}
                                     for p in shape_rows if p.get("built") is False],
                "missing": [], "note": None}

    @app.get("/materialise/catalogue")
    def catalogue(as_of: Optional[str] = None,
                  principal: AuthenticatedPrincipal = Depends(require_principal)):
        """``as_of`` (ISO-8601, timezone-aware): read the catalogue — and the source pins a plan
        must carry (R-021 round 2, Finding 3) — at that instant, i.e. a plan's build instant."""
        impl = _materialiser(app)
        if impl is None or not callable(getattr(impl, "catalogue", None)):
            return _refusal("MATERIALISER_NOT_IMPLEMENTED", NOT_IMPLEMENTED_MESSAGE, 501)
        if as_of is None:
            return impl.catalogue(principal=principal.principal_id)
        try:
            instant = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                raise ValueError("naive instant")
        except ValueError:
            return _refusal("BAD_REQUEST", f"as_of {as_of!r} is not a timezone-aware ISO-8601 "
                                           f"instant")
        if "as_of" not in inspect.signature(impl.catalogue).parameters:
            return _refusal("MATERIALISER_NOT_IMPLEMENTED",
                            "the attached materialiser cannot read its catalogue as of an "
                            "instant", 501)
        return impl.catalogue(principal=principal.principal_id, as_of=instant)

    @app.post("/materialise")
    def materialise(background: BackgroundTasks,
                    plan: PlanRequest = Body(default_factory=PlanRequest),
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        body = plan.model_dump(mode="json")
        if impl is None:
            # 501, not 503 and not 200-with-an-empty-receipt. 503 would say "try again later",
            # which implies the thing exists and is down.
            return JSONResponse(status_code=501, content={"refusal": {
                "code": "MATERIALISER_NOT_IMPLEMENTED",
                "message": NOT_IMPLEMENTED_MESSAGE,
                "missing": list(MISSING_COMPONENTS),
                "nearest_alternative": (
                    "GET /evidence/... re-executes a single pointer today; that is the same "
                    "verification a receipt aggregates, one citation at a time."),
                "plan_received": body,
            }})
        if not _accepts_principal(impl):
            return _refusal(
                "MATERIALISER_NOT_PRINCIPAL_AWARE",
                "the attached materialiser's run() cannot be told which principal it runs as, so "
                "it would run under its own default identity. Refused: every run executes as the "
                "signed-in principal", 501)
        if not _runs_as_job(impl):
            try:
                return impl.run(body, by=principal.principal_id)
            except Exception as exc:                                 # noqa: BLE001
                log.exception("materialisation failed")
                return JSONResponse(status_code=503, content={"refusal": {
                    "code": "MATERIALISATION_FAILED",
                    "message": f"the run failed and no receipt is issued: {exc}",
                    "nearest_alternative": None}})
        has_request = isinstance(body.get("request"), str) and body["request"].strip()
        has_plan = isinstance(body.get("plan"), dict)
        if bool(has_request) == bool(has_plan):
            return _refusal("BAD_REQUEST", "send exactly one of `request` (English: describe the "
                                           "dataset you need) or `plan` (a MaterialisationPlan)")
        if callable(getattr(impl, "request_problem", None)):
            problem = impl.request_problem(body)
            if problem:
                return _refusal("BAD_REQUEST", problem)
        # The de-duplication key is PER PRINCIPAL (R-021 round 2, Finding 6): an identical body
        # from someone else is a separate job, so a busy answer can only ever name the caller's
        # own running job — never disclose that another principal is running this request.
        key = hashlib.sha256(json.dumps({"by": principal.principal_id, "body": body},
                                        sort_keys=True, default=str).encode()).hexdigest()
        try:
            job = _jobs(app).start(JOB_KIND, key[:32], by=principal.principal_id,
                                   request=body.get("request"),
                                   shape=((body.get("plan") or {}).get("shape") or {}).get("kind")
                                   if has_plan else None)
        except JobBusy as busy:
            if not _owned((busy.job or {}).get("by"), principal):
                # Unreachable with a per-principal key; kept so a tracker change can never turn
                # this into an existence oracle for another principal's run.
                return _refusal("ALREADY_RUNNING", "an identical materialisation is already "
                                                   "running", 409)
            return _refusal("ALREADY_RUNNING", str(busy), 409, job_id=busy.job["job_id"])
        background.add_task(_run_job, app, impl, body, principal.principal_id, job["job_id"])
        return JSONResponse(status_code=202, content={
            "job_id": job["job_id"], "status": "running",
            "poll": f"/materialise/jobs/{job['job_id']}"})

    @app.get("/materialise/jobs")
    def list_jobs(principal: AuthenticatedPrincipal = Depends(require_principal)):
        return {"jobs": [j for j in _jobs(app).list(kind=JOB_KIND)
                         if _owned(j.get("by"), principal)]}

    @app.get("/materialise/jobs/{job_id}")
    def get_job(job_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        job = _jobs(app).get(job_id)
        if job is None or job.get("kind") != JOB_KIND or not _owned(job.get("by"), principal):
            return _refusal("JOB_NOT_FOUND", f"no materialisation job {job_id!r}", 404)
        return job

    @app.get("/materialise/receipts")
    def list_receipts(principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        if impl is None or not callable(getattr(impl, "receipts", None)):
            return {"receipts": []}
        rows = (impl.receipts(by=principal.principal_id)
                if "by" in inspect.signature(impl.receipts).parameters else impl.receipts())
        return {"receipts": [r for r in rows if _owned(r.get("run_by"), principal)]}

    @app.get("/materialise/receipts/{plan_hash}")
    def get_receipt(plan_hash: str,
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        doc = impl.receipt(plan_hash) if impl is not None and callable(
            getattr(impl, "receipt", None)) else None
        if doc is None or not _owned((doc.get("platform") or {}).get("run_by"), principal):
            return _refusal("RECEIPT_NOT_FOUND", f"no receipt for plan_hash {plan_hash!r}", 404)
        out = {**doc, "files": impl.files(plan_hash)}
        # Additive (R-021 Data-page #1/#5): the server's word on what each file is, and a
        # server-side recomputation of the plan file's canonical hash.
        if callable(getattr(impl, "file_roles", None)):
            out["file_roles"] = impl.file_roles(plan_hash)
        if callable(getattr(impl, "plan_verification", None)):
            out["plan_verification"] = impl.plan_verification(plan_hash)
        return out

    @app.get("/materialise/receipts/{plan_hash}/files/{name}")
    def download(plan_hash: str, name: str,
                 principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        not_found = _refusal("FILE_NOT_FOUND",
                             f"{name!r} is not an output of run {plan_hash!r}", 404)
        if impl is None or not _owned(_run_owner(impl, plan_hash), principal):
            return not_found
        withheld = (impl.file_withheld(plan_hash, name)
                    if callable(getattr(impl, "file_withheld", None)) else None)
        if withheld:
            return _refusal("DATASET_WITHHELD",
                            f"{name!r} is a data file of this run (its dataset or its per-cell "
                            f"pointers) and is not served: {withheld}. The "
                            f"receipt and plan remain readable", 409)
        path = impl.file_path(plan_hash, name) if callable(
            getattr(impl, "file_path", None)) else None
        if path is None:
            return _refusal("FILE_NOT_FOUND",
                            f"{name!r} is not an output of run {plan_hash!r}", 404)
        media = ("application/json" if name.endswith(".json") else
                 "application/x-ndjson" if name.endswith(".jsonl") else
                 "application/yaml" if name.endswith(".yaml") else "application/octet-stream")
        return FileResponse(path, media_type=media, filename=name)
