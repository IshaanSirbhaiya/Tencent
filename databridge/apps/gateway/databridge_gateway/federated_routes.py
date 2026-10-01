"""`/federated` — ask one question across every adapter agent's sources (FED).

    POST /federated/ask   {question, wait?, sources?, answers?}  → a job (202), or the answer
                                                       (wait=true); ``answers`` is
                                                       ``{"fed:<sha1>": text}`` (E2E N-03)
    GET  /federated/jobs/{job_id}                      → the job; ``result`` is the answer:
         {answer_markdown, table, citations, coverage, plan, timings, run_id, questions, …}

The main agent plans (rules first, else the harness), each adapter agent's delegate reads its own
sources in parallel, a deterministic engine combines, and the model only narrates the computed
table (`databridge_federated`). Every value in ``table`` carries its derived pointer.

**Who asks is the signed-in principal** (`auth.require_principal`); a ``by`` in the body is not
believed. **What they may read is the OPERATOR's grant, never the request's** (R-021 round 2,
finding 1). ``WALNUT_FEDERATED_GRANTS`` is a JSON object ``{"<principal_id>": ["<connection>",
…] | "*"}`` read per request: a principal reads only its granted connections (``"*"`` = every
registered, non-paused connection), and ``sources`` can only NARROW that grant, never widen it.
Fail closed: a principal with no grant record is refused (403 ``FEDERATED_NOT_GRANTED``) before
anything is read; with the variable unset, only the local-demo dev principal (``dev_mode``, which
`auth` grants solely under ``WALNUT_DEV_AUTH=1``) keeps the default every-connection scope — the
same rule `ontology_routes` applies to owners. Live reads are never admitted from this route. A
job's result is returned only to the principal who started it.

Mounted by ``register_federated_routes(app)`` (the coordinator adds that one call to ``main.py``).
The service is built per question from the live registry, so a new adapter agent is askable at
once; ``app.state.federated_factory`` (installed at boot by `install_federated`) may be replaced by
a test.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .platform_runtime import JobBusy, platform_of

log = logging.getLogger("databridge.gateway.federated")

JOB_KIND = "federated_ask"
MAX_QUESTION_CHARS = 2000
MAX_ANSWERS = 20
MAX_ANSWER_CHARS = 2000
GRANTS_ENV = "WALNUT_FEDERATED_GRANTS"
#: The sentinel for "no grant record": distinct from ``None`` (= every registered connection).
_NOT_GRANTED = object()


def _ensure_importable() -> None:
    """`packages/federated` is new (FED); until `serve.py::_PACKAGES` and the root pytest
    ``pythonpath`` list it, put it on the path here so the route works either way."""
    try:
        import databridge_federated  # noqa: F401,PLC0415
    except ImportError:
        pkg = Path(__file__).resolve().parents[3] / "packages" / "federated"
        if pkg.is_dir() and str(pkg) not in sys.path:
            sys.path.insert(0, str(pkg))


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


class AskRequest(BaseModel):
    #: ``extra="ignore"``: a client that sends ``by`` is not refused, it is simply not believed.
    model_config = ConfigDict(extra="ignore")
    question: str
    wait: bool = False
    sources: Optional[list[str]] = Field(default=None, description="narrow to these connections")
    #: answers to the planner's questions from an earlier answer (E2E N-03): ``{id: text}`` where
    #: ``id`` is that answer's ``question_items[].id`` (``fed:<sha1 of the question>``)
    answers: Optional[dict[str, str]] = Field(default=None)


def _bad_answers(answers: Optional[dict[str, str]]) -> Optional[str]:
    """Why ``answers`` is refused, or None. Ids are content hashes, so a typed id that is not
    one is a client error, never a silently ignored answer."""
    if not answers:
        return None
    _ensure_importable()
    from databridge_federated.planner import QUESTION_ID_RE           # noqa: PLC0415
    if len(answers) > MAX_ANSWERS:
        return f"at most {MAX_ANSWERS} answers per question"
    for k, v in answers.items():
        if not QUESTION_ID_RE.match(str(k)):
            return (f"answer id {str(k)[:80]!r} is not a question id; use an id from the "
                    f"earlier answer's question_items (fed:<sha1 of the question>)")
        if len(str(v)) > MAX_ANSWER_CHARS:
            return f"an answer is at most {MAX_ANSWER_CHARS} characters"
    return None


def default_factory(app: Any) -> Callable[[], Any]:
    """A fresh `FederatedService` over the app's shared store, harness and retriever."""

    def build() -> Any:
        _ensure_importable()
        from databridge_federated import build_service                # noqa: PLC0415

        rt = platform_of(app)
        return build_service(store=getattr(app.state, "graph_store", None), harness=rt.harness,
                             retriever=rt.new_retriever(), gateway_factory=rt.gateway_factory)
    return build


def install_federated(app: Any, rt: Any = None) -> None:          # noqa: ARG001 - boot hook
    """Called once from `platform_runtime.start_platform`: installs the service factory."""
    if getattr(app.state, "federated_factory", None) is None:
        app.state.federated_factory = default_factory(app)


def _factory(app: Any) -> Callable[[], Any]:
    f = getattr(app.state, "federated_factory", None)
    if f is None:
        f = default_factory(app)
        app.state.federated_factory = f
    return f


def federated_grants() -> Optional[dict[str, Any]]:
    """The operator's grant directory, or ``None`` when unconfigured. Read per call. A value that
    does not parse, or has the wrong shape, grants NOBODY anything (fail closed, logged)."""
    raw = os.environ.get(GRANTS_ENV, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.error("%s is not valid JSON; no principal is granted any connection", GRANTS_ENV)
        return {}
    if not isinstance(parsed, dict):
        log.error("%s must be a JSON object; no principal is granted any connection", GRANTS_ENV)
        return {}
    out: dict[str, Any] = {}
    for who, grant in parsed.items():
        if grant == "*":
            out[str(who)] = "*"
        elif isinstance(grant, list) and all(isinstance(c, str) for c in grant):
            out[str(who)] = frozenset(grant)
        else:
            log.error("%s[%r] must be \"*\" or a list of connection ids; ignored", GRANTS_ENV,
                      who)
    return out


def granted_connections(principal: AuthenticatedPrincipal) -> Any:
    """``None`` (every registered connection), a frozenset of connection ids, or
    ``_NOT_GRANTED``. Decided from operator configuration only — never from the request."""
    grants = federated_grants()
    if grants is None:
        return None if principal.dev_mode else _NOT_GRANTED
    grant = grants.get(principal.principal_id)
    if grant is None:
        return _NOT_GRANTED
    return None if grant == "*" else grant


def admitted_scope(granted: Any, sources: Optional[list[str]]) -> Optional[set[str]]:
    """The connections this question may read: the grant, narrowed by ``sources`` (never widened).
    ``None`` means every registered connection (a ``"*"`` grant with no ``sources``)."""
    if sources is None:
        return None if granted is None else set(granted)
    asked = set(sources)                   # an explicit empty list grants NOTHING
    return asked if granted is None else asked & set(granted)


def _ask(app: Any, question: str, principal_id: str, scope: Optional[set[str]],
         answers: Optional[dict[str, str]] = None) -> dict:
    _ensure_importable()
    from databridge_federated import CatalogueAdmission               # noqa: PLC0415

    from .trace_recorder import recorder_for                          # noqa: PLC0415

    svc = _factory(app)()
    # `scope` is the OPERATOR grant narrowed by the request's `sources` (see `admitted_scope`)
    admission = CatalogueAdmission(principal_id, set(scope) if scope is not None else None)
    trace = recorder_for(app).begin(surface="federated", agent="federated")
    ans = svc.answer(question, admission=admission, trace=trace, answers=answers or None)
    return ans.model_dump(mode="json")


def _run_job(app: Any, job_id: str, question: str, principal_id: str,
             scope: Optional[set[str]], answers: Optional[dict[str, str]] = None) -> None:
    rt = platform_of(app)
    try:
        result = _ask(app, question, principal_id, scope, answers)
        rt.jobs.finish(job_id, status="completed", result=result, run_id=result.get("run_id"))
    except Exception as exc:                                          # noqa: BLE001
        log.exception("federated question failed")
        rt.jobs.finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")


def register_federated_routes(app: FastAPI) -> None:
    install_auth_handler(app)

    @app.post("/federated/ask", status_code=202)
    def federated_ask(background: BackgroundTasks, body: AskRequest = Body(...),
                      principal: AuthenticatedPrincipal = Depends(require_principal)):
        q = (body.question or "").strip()
        if not q:
            return _refusal("EMPTY_QUESTION", "ask a question")
        if len(q) > MAX_QUESTION_CHARS:
            return _refusal("QUESTION_TOO_LONG", f"a question is at most {MAX_QUESTION_CHARS} "
                                                 f"characters")
        granted = granted_connections(principal)
        if granted is _NOT_GRANTED:
            # names nobody else's grant, and reads nothing
            return _refusal("FEDERATED_NOT_GRANTED",
                            f"principal {principal.principal_id!r} is authenticated but holds no "
                            f"federated read grant. Grants are configured by the operator in "
                            f"{GRANTS_ENV}, never by a request.", status=403)
        bad = _bad_answers(body.answers)
        if bad:
            return _refusal("BAD_ANSWERS", bad)
        scope = admitted_scope(granted, body.sources)
        if body.wait:
            try:
                return JSONResponse(status_code=200,
                                    content=_ask(app, q, principal.principal_id, scope,
                                                 body.answers))
            except Exception as exc:                                  # noqa: BLE001
                log.exception("federated question failed")
                return _refusal("ASK_FAILED", f"{type(exc).__name__}: {exc}", status=500)
        rt = platform_of(app)
        try:
            job = rt.jobs.start(JOB_KIND, f"q-{uuid.uuid4().hex[:12]}",
                                by=principal.principal_id, question=q)
        except JobBusy as busy:                                       # pragma: no cover - unique
            return _refusal("ASK_IN_PROGRESS", str(busy), status=409)
        background.add_task(_run_job, app, job["job_id"], q, principal.principal_id, scope,
                            body.answers)
        return {"job_id": job["job_id"], "status": "running",
                "started_by": principal.principal_id}

    @app.get("/federated/jobs/{job_id}")
    def federated_job(job_id: str, principal: AuthenticatedPrincipal = Depends(require_principal)):
        job = platform_of(app).jobs.get(job_id)
        # another principal's answer is not disclosed — not even that the job exists
        if job is None or job["kind"] != JOB_KIND or job.get("by") != principal.principal_id:
            return _refusal("UNKNOWN_JOB", f"no federated job {job_id!r}", status=404)
        return job


__all__ = ["register_federated_routes", "install_federated", "default_factory", "JOB_KIND",
           "GRANTS_ENV", "federated_grants", "granted_connections", "admitted_scope"]
