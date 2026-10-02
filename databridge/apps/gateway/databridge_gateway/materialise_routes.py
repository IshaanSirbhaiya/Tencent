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

DSD — RAW datasets built through delegation to the per-source adapter agents (founder,
2026-10-02). A separate mode beside the labelled one above, which is unchanged:

    POST /materialise/designs                     {"request": "<English>", "answers"?}
                                                  → 201 a PROPOSED table design (grain, columns
                                                  with meaning + source + when-known, label,
                                                  approved join links, questions) + its digest
    GET  /materialise/designs                     the caller's designs
    GET  /materialise/designs/{id}                one design (404 for anyone but its owner)
    PUT  /materialise/designs/{id}                an edit (columns, grain, label, filters);
                                                  re-validated; clears any approval
    POST /materialise/designs/{id}/approve        {"digest"} — the signed-in principal approves
                                                  EXACTLY that digest (authenticated only)
    POST /materialise/designs/{id}/build          {"digest"} → 202 job; builds only the APPROVED
                                                  digest; the result is a raw table + receipt +
                                                  pointer sidecar under /materialise/receipts

What a design may read is the OPERATOR's federated grant (`WALNUT_FEDERATED_GRANTS`, the same rule
as `/federated/ask`), never the request's; no grant record refuses with 403 before anything is
read.

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

**A run is visible only to the principal who made it, and to an operator** (R-021 Finding 1;
R-028 #10). Job, receipt and design listings are filtered to the caller; a job, receipt, file or
design of another principal answers the same 404 — byte for byte — that one that does not exist
answers, so its existence is not disclosed either. The one override is the platform's own rule,
the same one-time tasks, the outbox and Activity apply: ``agent_ownership.is_operator`` (the
operators named in ``WALNUT_OPERATORS``; when that is unset, only the dev principal). An operator
may READ every run, receipt, file, job and design; deciding on a design (edit, approve, build)
stays its owner's, because the design store records the owner as its decider.

**No exception text in a public answer or a visible job record** (R-028 #6): a failure answers a
fixed sentence and an ``error_ref``, and the exception is in the server log under that reference.
A package refusal's own sentence is shown only when `public_errors.public_sentence` allows it.
Refusal codes for the standard causes (stale, already final, approver, ledger integrity) are the
platform's standard codes; the former specific code is kept as ``reason_code`` (R-028 #11).
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

from . import agent_ownership
from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import JobBusy, JobTracker
from .public_errors import (FAILURE_MESSAGES, REF_NOTE, error_ref, from_exception, refusal,
                            refusal_body)

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
    """The principal's OWN record (deciding on it is the owner's alone)."""
    return doc_owner is not None and doc_owner == principal.principal_id


def _may_see(doc_owner: Any, principal: AuthenticatedPrincipal) -> bool:
    """Owner OR operator (R-028 #10): the platform rule one-time tasks, the outbox and Activity
    apply (`agent_ownership.is_operator`, fail-closed). A record with no owner is an operator's
    only."""
    return _owned(doc_owner, principal) or agent_ownership.is_operator(principal)


#: One body for a missing record and another principal's (nothing in it varies with the id).
_NOT_FOUND = {
    "job": ("JOB_NOT_FOUND", "no materialisation job with that id that you can see"),
    "design_job": ("JOB_NOT_FOUND", "no design build job with that id that you can see"),
    "receipt": ("RECEIPT_NOT_FOUND", "no receipt with that plan hash that you can see"),
    "file": ("FILE_NOT_FOUND", "no output file with that name in a run you can see"),
    "design": ("DESIGN_NOT_FOUND", "no design with that id that you can see"),
}


def _not_found(what: str) -> JSONResponse:
    code, message = _NOT_FOUND[what]
    return refusal(code, message, 404)


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
    """The common envelope; ``message`` is route-authored, never an exception's (R-028 #6)."""
    return refusal(code, message, status, **extra)


def _public_refusal(exc: BaseException, default_code: str, context: str,
                    principal: Any = None, **detail: Any) -> dict:
    """The refusal a job or a route shows for ``exc`` (R-028 #6/#11): the package's code (its
    codes are this API's), mapped to the standard code where the cause is a standard one; its
    sentence only when `public_errors.public_sentence` allows it, else a fixed sentence and an
    ``error_ref`` under which the text is logged."""
    code, message, reason, ref = from_exception(log, exc, default_code=default_code,
                                                context=context, principal=principal,
                                                prefer_exc_code=True, **detail)
    return refusal_body(code, message, reason_code=reason, ref=ref)["refusal"]


def _failed(jobs: JobTracker, job_id: str, exc: BaseException, context: str) -> None:
    """A job that CRASHED: a fixed sentence and the reference, never the text (R-028 #6)."""
    ref = error_ref(log, context, exc, job_id=job_id)
    jobs.finish(job_id, status="failed", error=FAILURE_MESSAGES["RUN_FAILED"] + REF_NOTE,
                error_ref=ref)


def _run_job(app: FastAPI, impl: Any, body: dict, by: str, job_id: str) -> None:
    jobs = _jobs(app)
    try:
        result = impl.run(body, by=by, progress=lambda ev: jobs.progress(job_id, ev))
    except Exception as exc:                                         # noqa: BLE001
        code = getattr(exc, "code", None)
        if code:
            # A refusal is an ANSWER (the compiler or the policy said no, by name), not a crash.
            # A planner QUESTION carries its id, so it can be answered back (E2E run 3, P-10).
            # The structured fields (`exc.refusal()`: question ids and texts the planner wrote)
            # are kept; the sentence is the safe one (R-028 #6).
            public = _public_refusal(exc, str(code), "materialisation refused")
            if callable(getattr(exc, "refusal", None)):
                extra = {k: v for k, v in dict(exc.refusal()).items()
                         if k not in ("code", "message")}
                public = {**public, **extra}
            jobs.finish(job_id, status="refused", error=public["message"], refusal=public,
                        **({"error_ref": public["error_ref"]} if public.get("error_ref") else {}))
        else:
            _failed(jobs, job_id, exc, "materialisation failed")
        return
    jobs.finish(job_id, status="succeeded", result=result,
                plan_hash=(result or {}).get("plan_hash"))


class DesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str
    answers: Optional[dict[str, str]] = None


class DesignEdit(BaseModel):
    """An edit: any of these replace the design's own; the result is re-validated."""
    model_config = ConfigDict(extra="forbid")
    grain: Optional[dict[str, Any]] = None
    grain_text: Optional[str] = None
    columns: Optional[list[dict[str, Any]]] = None
    links: Optional[list[dict[str, Any]]] = None
    filters: Optional[list[dict[str, Any]]] = None
    label: Optional[dict[str, Any]] = None
    #: answers to the design's open questions, {question id: text} (they stay open until answered)
    answers: Optional[dict[str, str]] = None
    #: the revision this edit was made on (compare-and-set: a superseded revision is refused)
    rev: int


class DigestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str
    rev: Optional[int] = None


DESIGN_JOB_KIND = "materialise_design_build"
MAX_DESIGN_REQUEST_CHARS = 2000


def _design_impl(app: FastAPI) -> Any:
    impl = _materialiser(app)
    return impl if impl is not None and callable(getattr(impl, "propose_design", None)) else None


def _design_refusal(exc: Exception, principal: Any = None) -> JSONResponse:
    status = int(getattr(exc, "status", None) or 422)
    body = _public_refusal(exc, "DESIGN_FAILED", "design operation refused", principal=principal)
    if status == 404:
        # one body for a missing design and another principal's (R-028 #10)
        return _not_found("design")
    return JSONResponse(status_code=status, content={"refusal": body})


def _design_failed(exc: Exception, context: str) -> JSONResponse:
    """A design operation that CRASHED (no code): fixed sentence + reference (R-028 #6)."""
    ref = error_ref(log, context, exc)
    return JSONResponse(status_code=500, content=refusal_body(
        "DESIGN_FAILED", FAILURE_MESSAGES["DESIGN_FAILED"] + REF_NOTE, ref=ref))


def _grant(principal: AuthenticatedPrincipal) -> tuple[Optional[set], Optional[JSONResponse]]:
    """The connections this principal may read for a design: the operator's federated grant."""
    from .federated_routes import _NOT_GRANTED, GRANTS_ENV, granted_connections  # noqa: PLC0415
    granted = granted_connections(principal)
    if granted is _NOT_GRANTED:
        return None, _refusal("DESIGN_NOT_GRANTED",
                              f"principal {principal.principal_id!r} holds no read grant; grants "
                              f"are configured by the operator in {GRANTS_ENV}", 403)
    return (None if granted is None else set(granted)), None


def _federation(app: FastAPI) -> Any:
    from .federated_routes import _factory                          # noqa: PLC0415
    return _factory(app)()


def _run_design_job(app: FastAPI, impl: Any, design_id: str, digest: str,
                    principal_id: str, allowed: Optional[set], job_id: str) -> None:
    jobs = _jobs(app)
    try:
        from databridge_federated import CatalogueAdmission          # noqa: PLC0415
        service = _federation(app)
        result = impl.build_design(design_id, digest=digest, by=principal_id, service=service,
                                   admission=CatalogueAdmission(principal_id, allowed),
                                   progress=lambda ev: jobs.progress(job_id, ev),
                                   allowed=allowed)
    except Exception as exc:                                         # noqa: BLE001
        code = getattr(exc, "code", None)
        if code:
            public = _public_refusal(exc, str(code), "design build refused")
            jobs.finish(job_id, status="refused", error=public["message"], refusal=public,
                        **({"error_ref": public["error_ref"]} if public.get("error_ref") else {}))
        else:
            _failed(jobs, job_id, exc, "design build failed")
        return
    jobs.finish(job_id, status="succeeded", result=result, plan_hash=result.get("plan_hash"))


def _register_design_routes(app: FastAPI) -> None:
    """DSD: table designs (propose → edit → approve) and raw builds by delegation."""

    @app.post("/materialise/designs", status_code=201)
    def design_propose(body: DesignRequest,
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _design_impl(app)
        if impl is None:
            return _refusal("MATERIALISER_NOT_IMPLEMENTED", NOT_IMPLEMENTED_MESSAGE, 501)
        text = (body.request or "").strip()
        if not text or len(text) > MAX_DESIGN_REQUEST_CHARS:
            return _refusal("BAD_REQUEST", f"describe the dataset in 1–{MAX_DESIGN_REQUEST_CHARS}"
                                           f" characters")
        allowed, err = _grant(principal)
        if err is not None:
            return err
        try:
            return impl.propose_design(text, by=principal.principal_id,
                                       service=_federation(app), answers=body.answers,
                                       allowed=allowed)
        except Exception as exc:                                     # noqa: BLE001
            if getattr(exc, "code", None):
                return _design_refusal(exc, principal)
            return _design_failed(exc, "design proposal failed")

    @app.get("/materialise/designs")
    def design_list(principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _design_impl(app)
        if impl is None:
            return {"designs": []}
        if agent_ownership.is_operator(principal):
            return {"designs": _all_designs(impl)}
        return {"designs": impl.designs(by=principal.principal_id)}

    @app.get("/materialise/designs/{design_id}")
    def design_get(design_id: str,
                   principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _design_impl(app)
        doc = _visible_design(impl, design_id, principal)
        if doc is None:
            return _not_found("design")
        return doc

    @app.put("/materialise/designs/{design_id}")
    def design_edit(design_id: str, body: DesignEdit,
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _design_impl(app)
        if impl is None:
            return _not_found("design")
        allowed, err = _grant(principal)
        if err is not None:
            return err
        edit = body.model_dump(exclude_none=True, exclude={"rev"})
        if "label" in body.model_fields_set and body.label is None:
            edit["label"] = None                   # an explicit "no label"
        try:
            return impl.edit_design(design_id, edit, by=principal.principal_id,
                                    service=_federation(app), rev=body.rev, allowed=allowed)
        except Exception as exc:                                     # noqa: BLE001
            if getattr(exc, "code", None):
                return _design_refusal(exc, principal)
            return _design_failed(exc, "design edit failed")

    @app.post("/materialise/designs/{design_id}/approve")
    def design_approve(design_id: str, body: DigestBody,
                       principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _design_impl(app)
        if impl is None:
            return _not_found("design")
        try:
            # the principal OBJECT the auth layer issued — never its name (hard constraint 4)
            return impl.approve_design(design_id, digest=body.digest, principal=principal,
                                       by=principal.principal_id, rev=body.rev)
        except Exception as exc:                                     # noqa: BLE001
            if getattr(exc, "code", None):
                return _design_refusal(exc, principal)
            return _design_failed(exc, "design approval failed")

    @app.post("/materialise/designs/{design_id}/build")
    def design_build(design_id: str, body: DigestBody, background: BackgroundTasks,
                     principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _design_impl(app)
        if impl is None or impl.design(design_id, by=principal.principal_id) is None:
            return _not_found("design")
        allowed, err = _grant(principal)
        if err is not None:
            return err
        try:
            # refused synchronously when the design is not approved at exactly this digest
            from databridge_materialise.delegated import approved_design  # noqa: PLC0415
            approved_design(impl.design_store(), design_id, digest=body.digest,
                            owner=principal.principal_id)
        except Exception as exc:                                     # noqa: BLE001
            if getattr(exc, "code", None):
                return _design_refusal(exc, principal)
            return _design_failed(exc, "design build admission failed")
        key = hashlib.sha256(json.dumps({"by": principal.principal_id, "design": design_id,
                                         "digest": body.digest}).encode()).hexdigest()[:32]
        try:
            job = _jobs(app).start(DESIGN_JOB_KIND, key, by=principal.principal_id,
                                   design_id=design_id)
        except JobBusy as busy:
            return _refusal("ALREADY_RUNNING", "this design is already being built at that "
                                               "digest", 409, job_id=busy.job["job_id"])
        background.add_task(_run_design_job, app, impl, design_id, body.digest,
                            principal.principal_id, allowed, job["job_id"])
        return JSONResponse(status_code=202, content={
            "job_id": job["job_id"], "status": "running",
            "poll": f"/materialise/designs/jobs/{job['job_id']}"})

    @app.get("/materialise/designs/jobs/{job_id}")
    def design_job(job_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        job = _jobs(app).get(job_id)
        if job is None or job.get("kind") != DESIGN_JOB_KIND or not _may_see(job.get("by"),
                                                                            principal):
            return _not_found("design_job")
        return job


def _all_designs(impl: Any) -> list[dict]:
    """Every design, for an operator (R-028 #10): read from the package's own store."""
    store = impl.design_store() if callable(getattr(impl, "design_store", None)) else None
    root = getattr(store, "dir", None)
    if store is None or root is None or not root.is_dir():
        return []
    out = [v for p in sorted(root.glob("dsg-*")) if p.is_dir() for v in [store.view(p.name)] if v]
    return sorted(out, key=lambda d: d.get("updated_at") or "", reverse=True)


def _visible_design(impl: Any, design_id: str, principal: AuthenticatedPrincipal):
    """The design if the caller owns it, or (R-028 #10) is an operator; else None."""
    if impl is None:
        return None
    doc = impl.design(design_id, by=principal.principal_id)
    if doc is not None or not agent_ownership.is_operator(principal):
        return doc
    store = impl.design_store() if callable(getattr(impl, "design_store", None)) else None
    return store.get(design_id) if store is not None else None


def register_materialise_routes(app: FastAPI) -> None:
    """Attach the ``/materialise`` routes."""
    install_auth_handler(app)
    # DSD first: `/materialise/designs/...` is declared before any wider pattern can claim it
    _register_design_routes(app)

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
                 if not p.get("receipt") or _may_see(p.get("run_by"), principal)]
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
                ref = error_ref(log, "synchronous materialisation failed", exc)
                return JSONResponse(status_code=503, content=refusal_body(
                    "MATERIALISATION_FAILED",
                    FAILURE_MESSAGES["MATERIALISATION_FAILED"] + REF_NOTE, ref=ref))
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
            return _refusal("ALREADY_RUNNING", "an identical materialisation of yours is "
                                               "already running", 409, job_id=busy.job["job_id"])
        background.add_task(_run_job, app, impl, body, principal.principal_id, job["job_id"])
        return JSONResponse(status_code=202, content={
            "job_id": job["job_id"], "status": "running",
            "poll": f"/materialise/jobs/{job['job_id']}"})

    @app.get("/materialise/jobs")
    def list_jobs(principal: AuthenticatedPrincipal = Depends(require_principal)):
        return {"jobs": [j for j in _jobs(app).list(kind=JOB_KIND)
                         if _may_see(j.get("by"), principal)]}

    @app.get("/materialise/jobs/{job_id}")
    def get_job(job_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        job = _jobs(app).get(job_id)
        if job is None or job.get("kind") != JOB_KIND or not _may_see(job.get("by"), principal):
            return _not_found("job")
        return job

    @app.get("/materialise/receipts")
    def list_receipts(principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        if impl is None or not callable(getattr(impl, "receipts", None)):
            return {"receipts": []}
        operator = agent_ownership.is_operator(principal)
        rows = (impl.receipts(by=None if operator else principal.principal_id)
                if "by" in inspect.signature(impl.receipts).parameters else impl.receipts())
        return {"receipts": [r for r in rows if _may_see(r.get("run_by"), principal)]}

    @app.get("/materialise/receipts/{plan_hash}")
    def get_receipt(plan_hash: str,
                    principal: AuthenticatedPrincipal = Depends(require_principal)):
        impl = _materialiser(app)
        doc = impl.receipt(plan_hash) if impl is not None and callable(
            getattr(impl, "receipt", None)) else None
        if doc is None or not _may_see((doc.get("platform") or {}).get("run_by"), principal):
            return _not_found("receipt")
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
        if impl is None or not _may_see(_run_owner(impl, plan_hash), principal):
            return _not_found("file")
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
            return _not_found("file")
        media = ("application/json" if name.endswith(".json") else
                 "application/x-ndjson" if name.endswith(".jsonl") else
                 "application/yaml" if name.endswith(".yaml") else "application/octet-stream")
        return FileResponse(path, media_type=media, filename=name)
