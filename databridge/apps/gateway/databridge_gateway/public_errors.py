"""What a route may say about a failure, and in which words (R-028 consistency, ROUTES track).

Two rules, shared by the Sources, source-batch, adapter-setup and materialise routes:

1. **No exception text in a public payload or a visible job record** (Codex R-028 #6). A failure
   answers a closed code and a FIXED sentence; the exception, its type and its text go to the
   server log under an opaque ``error_ref`` (``ERR-<hex>``) that the payload carries, so an operator
   can find it and nobody else learns a path, a query, a host or a credential-bearing message.

   The one exception is a package's own REFUSAL, whose sentence is the answer ("the plan calls
   ``execute_sql``, which is unclassified; vouch for it"). Its ``message`` is public only when ALL
   of these hold — anything else is replaced by the fixed sentence for its code:

   * it is an instance of a known refusal class (:func:`_refusal_classes`), not any exception;
   * every exception in its ``__cause__``/``__context__`` chain is also such a refusal. A refusal
     raised while any other exception was being handled may have interpolated that exception's
     text, so it is withheld; a refusal re-raised as another refusal keeps its words;
   * its sentence names no filesystem path and quotes no ``SomethingError:`` text;
   * it is not an approver-gate refusal (those always answer the fixed sentence of
     ``APPROVER_NOT_AUTHENTICATED`` / ``MACHINE_ACTOR``).

2. **One set of refusal codes for the same cause** (Codex R-028 #11), in the common envelope
   ``{"refusal": {"code", "message", "nearest_alternative", "reason_code"?, "error_ref"?}}``. The
   standard ``code`` is one of :data:`STANDARD_CODES` wherever the cause is one of theirs; the
   route's former, more specific code is kept as ``reason_code`` (tests and pages that named it
   still can), never dropped.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from databridge_core.decision_ledger import (APPROVER_NOT_AUTHENTICATED, DECISION_ALREADY_FINAL,
                                             DECISION_STALE, LEDGER_INTEGRITY_FAILED,
                                             LEGACY_CODES, MACHINE_ACTOR, REFUSAL_CODES,
                                             is_machine_principal)
from fastapi.responses import JSONResponse

#: The platform's standard refusal codes (CONS_BRIEF §3), from the ONE definition in
#: `databridge_core.decision_ledger` (LEDGER).
STANDARD_CODES = tuple(sorted(REFUSAL_CODES))

#: A route's or a package's specific code -> the standard code for the same cause: the core map
#: (`decision_ledger.LEGACY_CODES`) plus the route-level codes these routes used for the same
#: causes. The specific code survives as ``reason_code``.
STANDARD_FOR: dict[str, str] = {
    **LEGACY_CODES,
    # the thing decided on is no longer what the person reviewed
    "DOCUMENTS_CHANGED": DECISION_STALE,
    "EXPLANATION_NOT_READ": DECISION_STALE,
    # the decision was already made
    "ALREADY_APPROVED": DECISION_ALREADY_FINAL,
}

#: Fixed sentences: the public message for every standard code, and for the codes whose package
#: sentence may name a path or a key (never passed through).
FIXED_MESSAGES: dict[str, str] = {
    APPROVER_NOT_AUTHENTICATED: "this decision needs a signed-in person; the request's principal "
                                "is not an authenticated approver",
    MACHINE_ACTOR: "a machine actor cannot make this decision; only a signed-in person can",
    DECISION_STALE: "what was decided on changed after it was shown; reload it, review it again "
                    "and decide again",
    DECISION_ALREADY_FINAL: "this decision has already been made; nothing was changed",
    LEDGER_INTEGRITY_FAILED: "the decision log did not verify, so nothing was decided; an "
                             "operator must look at it (the server log names what failed)",
    "APPROVAL_KEY_UNAVAILABLE": "the deployment's approval key is not available, so nothing can "
                                "be approved; an operator must configure it",
}

#: Fixed sentences for the codes a route uses when something FAILED (not a refusal).
FAILURE_MESSAGES: dict[str, str] = {
    "SOURCES_UNREADABLE": "the registered sources could not be read",
    "UNKNOWN_SOURCE": "no registered source with that id",
    "PLAN_FAILED": "the crawl plan could not be proposed",
    "PLAN_NOT_APPROVABLE": "the crawl plan could not be approved",
    "RUN_NOT_STARTED": "the run could not be started",
    "BAD_CONNECTION": "the connection declaration was not accepted",
    "MAPPING_NOT_APPROVABLE": "the mapping could not be approved",
    "MAPPING_UNREADABLE": "the mapping review could not be read",
    "LINK_NOT_APPROVABLE": "the link could not be approved",
    "LINK_NOT_REJECTABLE": "the link could not be rejected",
    "HOLDS_UNREADABLE": "the held items could not be read",
    "OVERRIDE_REFUSED": "the screen override was not recorded",
    "VERIFY_FAILED": "the run could not be verified",
    "NEEDS_INDIVIDUAL_REVIEW": "the plan could not be assessed for a batch; review it on its own "
                               "page",
    "AGENT_NOT_CREATED": "the adapter agent could not be created",
    "EXPLAIN_FAILED": "the mapping could not be explained",
    "ADAPTER_AGENT_NOT_ACTIVATABLE": "the adapter agent could not be activated",
    "DESIGN_FAILED": "the dataset design operation failed",
    "MATERIALISATION_FAILED": "the run failed and no receipt is issued",
    "RUN_FAILED": "the run failed",
}

GENERIC_FAILURE = "the operation failed"
REF_NOTE = " (the server log has the details under the reference)"

_PATH = re.compile(r"[A-Za-z]:[\\/]|\\\\|(?:^|[\s'\"(=])/[\w.-]+/|[\w.-]+[\\/][\w.-]+[\\/]")
_EXC_TEXT = re.compile(r"\b[A-Z]\w*(?:Error|Exception|Exit|Interrupt)\b\s*[:(]")


def error_ref(logger: logging.Logger, context: str, exc: Optional[BaseException] = None,
              **detail: Any) -> str:
    """Log a failure in full under a fresh reference; return the reference (never the text)."""
    ref = f"ERR-{uuid.uuid4().hex[:10]}"
    if exc is not None:
        logger.error("[%s] %s %s", ref, context, detail or "", exc_info=exc)
    else:
        logger.error("[%s] %s %s", ref, context, detail or "")
    return ref


def _refusal_classes() -> tuple[type, ...]:
    """The package exception types whose sentence is written to be read by a person. Imported
    lazily and each optionally: a package that is absent contributes nothing (narrower, never
    wider)."""
    found: list[type] = []
    for mod, names in (
            ("databridge_mcp_ingest.plans", ("PlanError",)),
            ("databridge_mcp_ingest.mapping", ("MappingApprovalRefused",)),
            ("databridge_mcp_ingest.screen", ("OverrideRefused",)),
            ("databridge_mcp_ingest.connections", ("RegistrationRefused",
                                                   "RegistrationIncomplete")),
            ("databridge_materialise.delegated", ("DesignRefused",)),
            ("databridge_materialise.errors", ("MaterialisationError",)),
            ("databridge_materialise.platform", ("RunRefused",)),
            ("databridge_adapter_agents.adapt", ("AdaptationRefused",)),
            ("databridge_adapter_agents.delegations", ("DelegationRefused",)),
            ("databridge_core.decision_ledger", ("DecisionRefused",))):
        try:
            module = __import__(mod, fromlist=list(names))
        except Exception:                                             # noqa: BLE001
            continue
        found.extend(t for t in (getattr(module, n, None) for n in names)
                     if isinstance(t, type) and issubclass(t, BaseException))
    return tuple(found)


def _approver_refusal_classes() -> tuple[type, ...]:
    found: list[type] = []
    try:
        from databridge_mcp_ingest.plans import ApprovalRefused       # noqa: PLC0415
        found.append(ApprovalRefused)
    except Exception:                                                 # noqa: BLE001
        pass
    try:
        from databridge_adapter_agents.adapt import AdaptationRefused  # noqa: PLC0415
        found.append(AdaptationRefused)
    except Exception:                                                 # noqa: BLE001
        pass
    return tuple(found)


def is_machine(principal: Any) -> bool:
    """A machine principal (the core rule, `decision_ledger.is_machine_principal`, kept identical
    to adapter_agents `adapt._MACHINE`): refused wherever a person is required."""
    pid = str(getattr(principal, "principal_id", "") or "").strip()
    return not pid or is_machine_principal(pid)


def standard_code(code: Optional[str]) -> Optional[str]:
    if code in STANDARD_CODES:
        return code
    return STANDARD_FOR.get(code or "")


def _message_attr(exc: BaseException) -> Optional[str]:
    msg = getattr(exc, "message", None)
    if isinstance(msg, str):
        return msg
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return None


def public_sentence(exc: BaseException) -> Optional[str]:
    """The exception's own sentence when it is a person-readable refusal (see module doc), else
    None (the caller uses the fixed sentence for its code)."""
    classes = _refusal_classes()
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        # every link of the chain must itself be a person-readable refusal: a refusal re-raised
        # as another (a planner question carried by a run refusal) keeps its words, a refusal
        # raised while handling anything else (an OSError, a driver error) does not
        if not (classes and isinstance(cur, classes)):
            return None
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    msg = _message_attr(exc)
    if not msg or _PATH.search(msg) or _EXC_TEXT.search(msg):
        return None
    return msg


def caused_by_approver_gate(exc: BaseException) -> bool:
    """The approver gate refused (directly, or re-raised by a package as its own refusal — the
    original survives as ``__context__`` even under ``raise ... from None``)."""
    classes = _approver_refusal_classes()
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "code", None)
        std = standard_code(code) if isinstance(code, str) and code else None
        if std in (APPROVER_NOT_AUTHENTICATED, MACHINE_ACTOR):
            return True
        if std is not None:
            # another standard cause (a ledger that does not verify, a stale decision) — even
            # from a subclass of the gate's refusal, it is not the approver's fault
            return False
        if classes and isinstance(cur, classes):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def refusal_body(code: str, message: str, *, reason_code: Optional[str] = None,
                 ref: Optional[str] = None, **extra: Any) -> dict:
    body: dict[str, Any] = {"code": code, "message": message, "nearest_alternative": None}
    if reason_code and reason_code != code:
        body["reason_code"] = reason_code
    if ref:
        body["error_ref"] = ref
    body.update(extra)
    return {"refusal": body}


def refusal(code: str, message: str, status: int = 422, *, reason_code: Optional[str] = None,
            ref: Optional[str] = None, **extra: Any) -> JSONResponse:
    """A route refusal in the common envelope. ``message`` is route-authored text, never an
    exception's. A specific ``code`` with a standard equivalent is answered as the standard code,
    the specific one as ``reason_code``."""
    std = standard_code(code)
    if std is not None and std != code:
        # the route's own sentence (never exception text) stays: it says WHICH thing changed
        reason_code, code = reason_code or code, std
    return JSONResponse(status_code=status,
                        content=refusal_body(code, message, reason_code=reason_code, ref=ref,
                                             **extra))


def from_exception(logger: logging.Logger, exc: BaseException, *, default_code: str,
                   context: str, principal: Any = None, prefer_exc_code: bool = False,
                   **detail: Any) -> tuple[str, str, Optional[str], Optional[str]]:
    """Classify ``exc`` for a public answer: ``(code, message, reason_code, error_ref)``.

    * an approver-gate refusal -> ``MACHINE_ACTOR`` (a machine principal) or
      ``APPROVER_NOT_AUTHENTICATED``, with the fixed sentence;
    * a code the exception carries (``exc.code``) is used when it names a standard cause, or
      always when ``prefer_exc_code`` (a package whose codes ARE the API, such as the dataset
      designs); it is mapped to its standard code where one exists (the specific one kept as
      ``reason_code``); otherwise the route's ``default_code`` is the code;
    * the message is the refusal's own sentence only under :func:`public_sentence` (a ledger that
      does not verify says which record), else the fixed sentence for the code; whenever the
      exception's text is withheld it is logged under an ``error_ref`` the payload carries.
    """
    exc_code = getattr(exc, "code", None)
    exc_code = exc_code if isinstance(exc_code, str) and exc_code else None
    if exc_code is not None and not prefer_exc_code and standard_code(exc_code) is None:
        exc_code = None          # only a standard cause overrides the route's own code
    if caused_by_approver_gate(exc):
        code = MACHINE_ACTOR if (exc_code == MACHINE_ACTOR or (principal is not None
                                                               and is_machine(principal))) \
            else APPROVER_NOT_AUTHENTICATED
        ref = error_ref(logger, context, exc, **detail)
        specific = exc_code if exc_code and standard_code(exc_code) is None else default_code
        return code, FIXED_MESSAGES[code], specific, ref
    specific = exc_code or default_code
    std = standard_code(specific)
    code = std or specific
    reason = specific if std and std != specific else None
    sentence = public_sentence(exc)
    if sentence is not None:
        return code, sentence, reason, None
    ref = error_ref(logger, context, exc, **detail)
    fixed = FIXED_MESSAGES.get(code) or FIXED_MESSAGES.get(specific)
    if fixed is not None:
        return code, fixed, reason, ref
    return code, FAILURE_MESSAGES.get(specific, GENERIC_FAILURE) + REF_NOTE, reason, ref


def exception_refusal(logger: logging.Logger, exc: BaseException, *, default_code: str,
                      status: int, context: str, principal: Any = None,
                      prefer_exc_code: bool = False, **detail: Any) -> JSONResponse:
    code, message, reason, ref = from_exception(logger, exc, default_code=default_code,
                                                context=context, principal=principal,
                                                prefer_exc_code=prefer_exc_code, **detail)
    exc_status = getattr(exc, "status", None) if prefer_exc_code else None
    return JSONResponse(status_code=exc_status if isinstance(exc_status, int) else status,
                        content=refusal_body(code, message, reason_code=reason, ref=ref))


def failed_job_error(logger: logging.Logger, exc: BaseException, *, context: str,
                     code: str = "RUN_FAILED", **detail: Any) -> dict:
    """What a FAILED job records for anyone polling it: a fixed sentence and the reference."""
    ref = error_ref(logger, context, exc, **detail)
    return {"error": FAILURE_MESSAGES.get(code, GENERIC_FAILURE) + REF_NOTE, "error_ref": ref,
            "error_code": code}


def actor_of(principal: Any, *, delegation: Optional[dict] = None) -> dict:
    """The common actor object (CONS_BRIEF §1) for the signed-in principal: ``{principal_id,
    authenticated_by, principal_type, dev_only, delegation?}``, built by the core
    (`decision_ledger.actor_of`, which refuses a decorated id) from the same fields the approval
    gates record (`adapt.principal_of`: the principal's own ``authenticated_by``, its type)."""
    from databridge_core.decision_ledger import actor_of as _core_actor_of  # noqa: PLC0415

    who: dict[str, Any] = {
        "principal_id": str(getattr(principal, "principal_id", "") or ""),
        "authenticated_by": getattr(principal, "authenticated_by", None),
        "principal_type": type(principal).__name__,
        "dev_only": bool(getattr(principal, "dev_only", False)),
    }
    if delegation is not None:
        who["delegation"] = delegation
    return _core_actor_of(who)


__all__ = ["STANDARD_CODES", "STANDARD_FOR", "FIXED_MESSAGES", "FAILURE_MESSAGES",
           "error_ref", "refusal", "refusal_body", "exception_refusal", "from_exception",
           "failed_job_error", "public_sentence", "standard_code", "is_machine", "actor_of",
           "caused_by_approver_gate"]
