"""``/evidence/verify`` — a pointer goes in, a **verdict from the lattice** comes out.

The half of `/evidence` that `DEMO_AND_FRONTEND.md` §2.3 calls the demo's best ten seconds:

1. show a determination citing a value → `Verify` → **VERIFIED**;
2. the underlying row changes;
3. `Verify` again → **DRIFT_MATERIAL**, old beside new;
4. the reverse index names what is affected.

Every one of those four is answered by something that can be wrong. The verdict comes from a real
adapter's `verify()` (:mod:`verify_fleet`); the "new" value comes from re-running the same
policy-governed read; the impact answer comes from `databridge_graph`'s `ReverseIndex.impact_of`.

**Step 4's honesty rule, restated at the boundary because it is the one that matters.** The index
answers `KNOWN` or `UNKNOWN`, and an `UNKNOWN` answer with an empty list is *not* "nothing is
affected". This module keeps those apart in the payload — `impact.answer` is `"known"` /
`"unknown"` / `"unavailable"`, never a bare count — so a client cannot render a drift alarm as
reassurance by accident. `ptr_index.py` puts it best: *"conflating them is how a drift alarm
becomes falsely reassuring."*

Nothing here confirms anybody's identity, and nothing here writes to the evidence ledger. The one
write route (`POST /evidence/verify/mutate`) changes a cell in the fleet's private working copy of
a synthetic estate, and refuses any cell this gateway has not itself read and served.

**R-015.** Every route below is gated by `auth.require_principal` (INT1 gated the lattice too) —
an unauthenticated caller used to reach all of them, running as one hard-coded identity
regardless of who they were (`phase5-gw-evidence.md` #1).

**R-019 round 2.** Authentication says WHO asks; it authorises nothing by itself. A registered
connection's or a capture-bound pointer is verified only for an identity the operator granted that
source (`verify_fleet.connection_grant`), and it never reaches the fixture fleet. The fixture fleet
verifies only citations it served to the signed-in identity, and its mutation control needs an
explicit write entitlement. Errors reach the caller as a code and a reference, never as text.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .error_shaping import safe_detail
from .verify_fleet import (
    EvidenceFleet,
    FleetUnavailable,
    NoFixtureGrant,
    NoWriteGrant,
    error_ref,
    fleet_for,
)

log = logging.getLogger("databridge.gateway.verify")

#: The lattice, in operator language, keyed by the enum's own values. This is ENGINE vocabulary —
#: the eight states of `databridge_adapter.verdicts.Verdict` — and not a domain's, so it is the
#: same on every pack. The text restates `verdicts.py`; the tone is DERIVED from the enum's own
#: `must_escalate` / `is_positive` properties below rather than written down twice.
VERDICT_MEANING: dict[str, str] = {
    "verified": "re-read matches: the source still says what this citation vouches for.",
    "verified_as_of_capture": (
        "matched the capture this pointer cites. The DESIGNED outcome for a T1/T3 artefact, not a "
        "degraded one — the live source may have moved on and that was never the claim."),
    "drift_benign": (
        "something moved and it was not anything we cited. Only emitted by a source that "
        "advertises a change signal covering more than the read projection; nobody is paged."),
    "drift_material": (
        "a CITED value changed. A determination in the ledger now rests on something the source "
        "no longer says. This is the one verdict that must always escalate."),
    "expired": "the cursor window closed; the source can no longer be asked about this read.",
    "vanished": "the row, message, file or capture this pointer cites is gone.",
    "forbidden": "policy now denies what it once allowed. The citation is not wrong; it is unreadable.",
    "unverifiable": (
        "the tier, the source or this deployment cannot answer the question. An honest limit, "
        "not a failure — and deliberately not collapsed into a negative verdict."),
}

#: The three states that are neither positive nor escalating still need a distinct colour: they
#: schedule work rather than page someone. Everything else is derived from the enum.
_WORK_SCHEDULING = frozenset({"expired", "vanished", "forbidden"})


#: INT1 (2026-10-01): every route in this module requires an authenticated principal — the gateway
#: serves nothing anonymously except `/health` (`test_every_route_on_the_app_is_gated`).
_AUTH = [Depends(require_principal)]


def _tone(verdict) -> str:
    """`good` / `warn` / `bad` / `neutral`, computed from the lattice's own properties.

    `DRIFT_MATERIAL` must be alarming and `DRIFT_BENIGN` must not be (§1a): that split is the one
    that decides whether verification survives contact with production, so it is derived from
    `must_escalate` rather than from a table somebody can edit out of step with the enum.
    `UNVERIFIABLE` is `neutral` on purpose — it is an honest limit, and rendering it as a failure
    trains an operator to ignore it.
    """
    if verdict.must_escalate:
        return "bad"
    if verdict.is_positive:
        return "good"
    if verdict.value in _WORK_SCHEDULING:
        return "warn"
    return "neutral"


class PointerBody(BaseModel):
    """A `SourcePointer` as JSON, accepted as submitted and parsed by the core type.

    Permissive on purpose: `SourcePointer` is the schema, and a second one written here would be a
    second definition of pointer identity to drift away from the first.
    """

    model_config = ConfigDict(extra="allow")


class VerifyRequest(BaseModel):
    pointer: PointerBody
    #: Optional: the value the citation is offered for. Used ONLY to check that the cited cell or
    #: span holds it; it never becomes a "current value" and never changes what the source is
    #: asked. OMITTING it and sending `null` are different requests (`model_fields_set`): an
    #: explicit null is a value the citation is offered for.
    value: Any = Field(default=None)


class MutateRequest(BaseModel):
    """Demo step 2: change the underlying row, live."""

    pointer: PointerBody
    new_value: Any = Field(default=None)


def _refusal(code: str, message: str, status: int = 422, **extra) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "nearest_alternative": None}
    body.update(extra)
    return JSONResponse(status_code=status, content={"refusal": body})


def _no_grant() -> JSONResponse:
    """403 for a signed-in identity the fixture estate grants nothing. Names no other identity."""
    return _refusal("NO_GRANT_IN_FIXTURE_ESTATE", str(NoFixtureGrant()), status=403)


def _no_connection_grant() -> JSONResponse:
    """403 for a signed-in identity the operator has not granted this connection (R-019 round 2,
    finding 10). Authentication says WHO asks; only a grant says they may read."""
    return _refusal("NOT_GRANTED",
                    "the signed-in identity holds no read grant for this source. Authentication "
                    "alone does not authorise reading a connection's data", status=403)


def _failed(code: str, context: str, exc: Exception, status: int = 503) -> JSONResponse:
    """A stable code and a reference; the exception is logged under that reference only."""
    ref = error_ref(log, context, exc)
    return _refusal(code, f"{context} (reference {ref}; the detail is in the server log). No "
                          f"verdict is issued — an error is not a state of the lattice",
                    status=status, ref=ref)


def _parse_pointer(raw: dict):
    from databridge_core import SourcePointer

    return SourcePointer.model_validate(raw)


# =============================================================================================
# the reverse index — step 4
# =============================================================================================
def _impact(app, pointer) -> dict:
    """What relies on this pointer, or an honest statement that nobody can say.

    Three answers and they are kept apart:

    * ``unavailable`` — there is no reverse index to ask. *Not* "nothing is affected".
    * ``unknown``     — the index exists and has pointers it never saw, so any list it gives is a
      LOWER BOUND. Also not "nothing is affected".
    * ``known``       — the index is complete over this store; an empty list here really does mean
      nothing in this store cites the pointer, and it says so in those words.
    """
    # The ONE shared graph store (BUILD_PLAN §5.1) first; `brain.store` is the same object when
    # both are attached, and is the fallback for an app that only mounted the brain.
    store = getattr(app.state, "graph_store", None)
    brain = getattr(app.state, "brain", None)
    if store is None and brain is not None:
        store = getattr(brain, "store", None)
    if store is None:
        return {"answer": "unavailable",
                "reason": "no graph store is attached to this gateway, so no reverse pointer "
                          "index can be asked what cites this value",
                "records": [], "emissions": [], "coverage": None}
    try:
        index = store.reverse_index
    except Exception as exc:                                     # noqa: BLE001
        reason = safe_detail(log, context="the reverse pointer index is not available on this "
                             "store", exc=exc)
        return {"answer": "unavailable", "reason": reason,
                "records": [], "emissions": [], "coverage": None}
    try:
        report = index.impact_of(pointer=pointer)
        counts = index.counts()
    except Exception as exc:                                     # noqa: BLE001
        reason = safe_detail(log, context="the reverse pointer index could not answer", exc=exc)
        return {"answer": "unavailable", "reason": reason,
                "records": [], "emissions": [], "coverage": None}

    records = [{
        "entity_kind": e.entity_kind,
        "entity_id": e.entity_id,
        "kind": e.kind,
        "label": e.label,
        "field": e.field,
        "value": _plain(e.value),
        "is_open": bool(e.is_open),
    } for e in (tuple(report.nodes) + tuple(report.edges))]
    emissions = [{
        "kind": em.kind, "ref": em.ref, "plan_hash": em.plan_hash,
        "ledger_bound": bool(em.ledger_bound), "emitted_at": em.emitted_at,
        "stale_since": em.stale_since, "stale_cause": em.stale_cause,
    } for em in report.emissions]

    answer = "known" if report.is_complete else "unknown"
    return {
        "answer": answer,
        "reason": report.reason,
        "summary": report.summary(),
        "records": records,
        "emissions": emissions,
        "open_versions": sum(1 for r in records if r["is_open"]),
        "ledger_bound": len(report.ledger_bound),
        "unindexed_pointers": report.unindexed_pointers,
        "pointers_in_closure": len(report.pointer_ids),
        "coverage": counts,
    }


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# =============================================================================================
# routes
# =============================================================================================
def register_verify_routes(app: FastAPI) -> None:
    """Attach the verification surface. One write route, and it names what it will not write."""
    install_auth_handler(app)

    @app.get("/evidence/verify/lattice", dependencies=_AUTH)
    def lattice():
        """The eight verdicts, with tone derived from the enum rather than chosen by a client.

        Served so `/evidence` renders the lattice the ENGINE defines. A frontend holding its own
        copy would keep rendering eight states after the enum grew a ninth.
        """
        try:
            from databridge_adapter.verdicts import (
                ADVERTISED_ONLY_VERDICTS,
                UNIVERSAL_VERDICTS,
                Verdict,
            )
        except ImportError as exc:                               # pragma: no cover - path guard
            return _refusal(
                "ADAPTER_NOT_IMPORTABLE",
                f"the verdict lattice lives in databridge_adapter and this process cannot import "
                f"it ({exc}). The eight states are not restated here — a second copy of a closed "
                f"vocabulary is how the copy and the original stop agreeing",
                status=503)
        return {
            "verdicts": [{
                "verdict": v.value,
                "name": v.name,
                "meaning": VERDICT_MEANING.get(v.value, ""),
                "tone": _tone(v),
                "must_escalate": v.must_escalate,
                "is_positive": v.is_positive,
                "requires_advertised_capability": v in ADVERTISED_ONLY_VERDICTS,
            } for v in Verdict],
            "universal": sorted(v.value for v in UNIVERSAL_VERDICTS),
            "advertised_only": sorted(v.value for v in ADVERTISED_ONLY_VERDICTS),
            "note": ("`verify()` never returns a boolean (spine D6). Collapsing these eight to "
                     "true/false throws away the only information a human can act on."),
        }

    @app.get("/evidence/verify/fleet")
    def fleet_status(principal: AuthenticatedPrincipal = Depends(require_principal)):
        """What this deployment can verify against, and where it wrote its working copy."""
        fleet: EvidenceFleet = fleet_for(app)
        return fleet.status(principal.principal_id)

    @app.get("/evidence/verify/citations")
    def citations(refresh: bool = False,
                 principal: AuthenticatedPrincipal = Depends(require_principal)):
        """The cells a real read emitted, each with the pointer that grounds it.

        These are not examples. Every one was produced by `PostgresCore.resolve()` under the
        estate's own policy, and `value_at_read` is what the adapter returned at that moment —
        which is why old-vs-new after a mutation is a comparison and not a story.
        """
        fleet: EvidenceFleet = fleet_for(app)
        try:
            rows = fleet.citations(principal.principal_id, refresh=refresh)
        except NoFixtureGrant:
            return _no_grant()
        except FleetUnavailable as exc:
            return _refusal("EVIDENCE_FLEET_UNAVAILABLE", str(exc), status=503,
                            citations=[], fleet=fleet.status(principal.principal_id))
        return {
            "citations": [{
                "citation_id": c.citation_id,
                "source_id": c.source_id,
                "entity": c.entity,
                "column": c.column,
                "row_id": c.row_id,
                "value_at_read": c.value_at_read,
                "read_at": c.read_at,
                "trust": c.trust,
                "pointer": c.pointer,
            } for c in rows],
            "fleet": fleet.status(principal.principal_id),
        }

    @app.post("/evidence/verify")
    def verify(req: VerifyRequest,
              principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Re-execute one pointer. Returns the lattice, never a boolean.

        Any pointer, in this order — the first verifier that can speak for it answers:

        1. a `table_cell` from a REGISTERED CONNECTION → re-read live through that connection's
           MCP session (VERIFIED / DRIFT_MATERIAL with old beside new / VANISHED);
        2. a capture-bound pointer → the capture store (re-hash + span/cell read-back);
        3. otherwise — and never for a registered connection — the adapter fleet's own `verify()`
           as the SIGNED-IN identity (the fixture estate; UNVERIFIABLE for a source no adapter
           here serves; 403 for an identity the fixture estate grants nothing).
        """
        fleet: EvidenceFleet = fleet_for(app)
        raw = req.pointer.model_dump()
        try:
            pointer = _parse_pointer(raw)
        except Exception as exc:                                 # noqa: BLE001
            return _refusal("BAD_POINTER",
                            "that is not a SourcePointer. A pointer is the unit of verification; "
                            "there is nothing to re-execute without one")
        from .verify_fleet import (
            OMITTED,
            _same_value,
            capture_present,
            connection_grant,
            grant_admits,
            registered_connection_ids,
        )

        offered = req.value if "value" in req.model_fields_set else OMITTED
        registered = pointer.source_id in registered_connection_ids()
        generic = None
        if registered or capture_present(pointer):
            # A registered connection, or a pointer into the capture store: these are a
            # CONNECTION's data, and reading them needs the operator's grant for that source —
            # checked before anything is read. Such a pointer never reaches the fixture fleet.
            if not grant_admits(connection_grant(principal), pointer.source_id):
                return _no_connection_grant()
            try:
                generic = _verify_generic(app, pointer, offered, registered)
            except Exception as exc:                             # noqa: BLE001
                return _failed("VERIFY_FAILED", "the source could not complete verification",
                               exc)
        if generic is not None:
            v = generic.verdict
            return {
                "verdict": v.value, "name": v.name, "tone": _tone(v),
                "must_escalate": v.must_escalate, "is_positive": v.is_positive,
                "meaning": VERDICT_MEANING.get(v.value, ""), "note": generic.note,
                "method": generic.method, "verified_by": generic.verified_by,
                "verified_at": _now(),
                "pointer": pointer.model_dump(mode="json", exclude_none=True),
                "cited_value": generic.cited_value,
                "cited_value_known": generic.cited_value_known,
                "current_value": generic.current_value if generic.current_value_known else None,
                "current_value_known": generic.current_value_known,
                "capture_check": generic.capture_check, "live_check": generic.live_check,
                "impact": _impact(app, pointer),
            }
        try:
            verdict, note, source_id = fleet.verify(pointer, principal.principal_id)
        except NoFixtureGrant:
            return _no_grant()
        except FleetUnavailable as exc:
            return _refusal("EVIDENCE_FLEET_UNAVAILABLE", str(exc), status=503)
        except Exception as exc:                                 # noqa: BLE001
            # A failed verification is not a verdict. Saying VERIFIED or DRIFT here would be
            # inventing one, so the refusal is the answer.
            return _failed("VERIFY_FAILED", "the adapter could not complete verification", exc)

        # Principal-scoped (R-019 round 2, finding 5): only a citation served to THIS identity
        # contributes a cited value; another identity's register is never read back.
        recorded = fleet.recorded(pointer, principal.principal_id)
        cited_value = recorded.value_at_read if recorded is not None else None
        if (offered is not OMITTED and recorded is not None and verdict.is_positive
                and not _same_value(offered, recorded.value_at_read)):
            # R-019 round 2, finding 7 (fixture half): a positive verdict vouches for the value
            # the cell was READ with. A citation offered for another value is not vouched for.
            from databridge_adapter.verdicts import Verdict

            verdict, note = Verdict.DRIFT_MATERIAL, (
                "the cited cell was read with a different value from the one this citation is "
                "offered for; the source's own verdict on the pointer was positive, but it vouches "
                "for the value that was read, not the one offered")
        current_found, current_value = (False, None)
        if verdict.must_escalate and recorded is not None:
            # Only for a material drift, and only for a citation this gateway served: "what does
            # it say now?" is a second read, and a second read is only worth taking for the one
            # verdict that means a human has to act.
            try:
                current_found, current_value = fleet.current_value(pointer,
                                                                   principal.principal_id)
            except Exception:                                    # noqa: BLE001
                log.exception("re-read for old-vs-new failed")
                current_found, current_value = (False, None)

        return {
            "verdict": verdict.value,
            "name": verdict.name,
            "tone": _tone(verdict),
            "must_escalate": verdict.must_escalate,
            "is_positive": verdict.is_positive,
            "meaning": VERDICT_MEANING.get(verdict.value, ""),
            "note": note,
            "method": "adapter",
            "verified_by": source_id,
            "verified_at": _now(),
            "pointer": pointer.model_dump(mode="json", exclude_none=True),
            "cited_value": cited_value,
            "cited_value_known": recorded is not None,
            "current_value": current_value if current_found else None,
            "current_value_known": current_found,
            "impact": _impact(app, pointer),
        }

    @app.post("/evidence/impact")
    def impact(req: VerifyRequest,
              _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Step 4 on its own: what relies on this pointer, or why nobody can say."""
        try:
            pointer = _parse_pointer(req.pointer.model_dump())
        except Exception:                                         # noqa: BLE001
            return _refusal("BAD_POINTER", "that is not a SourcePointer")
        return {"pointer": pointer.model_dump(mode="json", exclude_none=True),
                "impact": _impact(app, pointer)}

    @app.post("/evidence/verify/mutate")
    def mutate(req: MutateRequest,
              principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Change the cell a citation cites — demo step 2, in the fleet's private working copy.

        Refuses anything this gateway did not itself read and serve. It writes no ledger row and
        makes no claim about anybody's identity; it exists so that step 3's `DRIFT_MATERIAL` is a
        detection rather than a slide. Write routes are held to the SAME auth gate as reads on this
        boundary — a caller who could not read a citation must not be able to change one either.
        """
        fleet: EvidenceFleet = fleet_for(app)
        try:
            pointer = _parse_pointer(req.pointer.model_dump())
        except Exception:                                         # noqa: BLE001
            return _refusal("BAD_POINTER", "that is not a SourcePointer")
        if req.new_value is None:
            return _refusal("NO_NEW_VALUE",
                            "a mutation needs the value to write. Writing NULL is a different "
                            "operation with a different meaning and is not what this control does")
        try:
            record = fleet.mutate(pointer, req.new_value, principal.principal_id)
        except NoFixtureGrant:
            return _no_grant()
        except NoWriteGrant as exc:
            return _refusal("NO_WRITE_GRANT", str(exc), status=403)
        except FleetUnavailable as exc:
            return _refusal("MUTATION_REFUSED", str(exc))
        except Exception as exc:                                 # noqa: BLE001
            return _failed("MUTATION_FAILED", "the write did not complete", exc)
        return {"changed": record,
                "note": ("the source now says something different. Verify the same pointer again "
                         "— the verdict is computed from the source, not from this response")}


def _verify_generic(app, pointer, value: Any, registered: bool):
    """Steps 1–2 of `/evidence/verify`'s order, for a pointer the caller is GRANTED to read.

    Never returns ``None``: a registered connection's or a capture-bound pointer is never handed
    to the fixture fleet (R-019 finding 1 / round 2 finding 2). The source gateway is
    `app.state.source_gateway` when one is attached, else a fresh `McpSourceGateway` for this one
    read, closed afterwards — a verify must never leave an MCP server process behind."""
    from .verify_fleet import (
        GenericVerification,
        default_source_gateway,
        verify_by_capture,
        verify_table_cell_live,
    )

    if registered and str(pointer.kind) == "table_cell" and pointer.capture_digest:
        attached = getattr(app.state, "source_gateway", None)
        gateway = attached if attached is not None else default_source_gateway()
        if gateway is not None:
            try:
                return verify_table_cell_live(pointer, gateway=gateway, value=value)
            finally:
                if attached is None and hasattr(gateway, "close"):
                    gateway.close()
    found = verify_by_capture(pointer, value=value, source_registered=registered)
    if found is None:
        return GenericVerification(
            _unverifiable(),
            f"{pointer.source_id!r} is a registered connection (or a capture-bound source), and "
            f"this pointer carries no capture binding this gateway can check or re-read live "
            f"from; no other verifier speaks for it — the fixture estate never does",
            "capture", None)
    return found


def _unverifiable():
    from databridge_adapter.verdicts import Verdict

    return Verdict.UNVERIFIABLE


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
