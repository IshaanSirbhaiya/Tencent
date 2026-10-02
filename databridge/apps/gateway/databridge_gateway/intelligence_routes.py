"""The ``/intelligence/*`` routes — the Business Intelligence surface (PHASE_7 T-42/T-43),
and the agent that reads it (`REMEDIATION.md` §3.1).

Three endpoints. Two of them compute figures; the third lets a member of staff ask a question in
English and get those same figures back as a list of values, each beside the pointer it came from
and each linking to the traversal behind it.

The figure surface exposes two things and refuses to mix them:

* **``graph``** — `databridge_graph.analytics.compute`: counts of *structural facts* about the
  shared graph (kinds present, most-connected entities, documents per source, cross-source
  entities, orphans, and — when the bound packs declare a subject — the identity-work figures),
  each with the denominator it was counted over and the ids behind it. Computed over meta-kinds and
  the packs' `subject_kinds`; no kind name is written here.
* **``ingestion``** — coverage and exclusions per source, read from the run ledgers ingestion
  writes under `var/ingest` (`source_capture`). Empty, and said to be, when nothing has run.
* **``queue``** — how many intakes in front of the counter cannot commit yet, and what each is
  waiting on, read straight from `ConfirmationState.is_ready_to_commit()`.

**What is deliberately absent: minutes, hours, and money.** `GET /impact` owns the only
time-saving claim this system is entitled to make (Phase 5's per-intake, stage-completed model),
and it is not restated, echoed or re-derived here. A structural count rendered beside a performance
claim reads as one, and that is how a dashboard stops being believable.

**Hard constraint 1, applied to a dashboard.** `blocked` reports *that* an intake needs a human and
*how many* registry candidates it named. It never names a candidate, never orders them, and carries
no field a client could read as a selection — a screen that ranked candidates would be doing
identity verification with extra steps. `analytics.compute` is held to the same rule on its side.

**Fail open, always.** Every aggregate is wrapped: a traversal that raises degrades this page to
"unavailable", exactly as the `ClinicHistory` enrichment in `intake_routes._history()` degrades an
intake. A dashboard must not 500 because an aggregate failed, and a *silent* zero would be worse
than an outage — so the failure is reported as a reason string, never as `0`.

## `POST /intelligence/ask` — and the four things it decides

**Fail open is for the figures. The agent fails CLOSED.** The two rules are opposite on purpose: a
missing aggregate leaves the rest of a page standing, but an answer assembled from a graph that
would not load, or from a model that is not configured, is a sentence a member of staff would act
on. So every degraded path here is a refusal with a code (`_refusal`), never a thinner answer.

1. **Structural reads only.** `agent_tools.build_structural_registry` hands the agent three
   `ClinicStructuralRead` tools and nothing else. No `CandidateRead`, no `PatientScopedRead` — the
   page's contract is structural counts, and a `PatientReadGrant` is an assertion this process is
   not entitled to make (there is no authenticated staff session behind `staff_id`).
2. **`AgentResponse`, never `AgentAnswer`.** `AgentAnswer` serialises the model's raw arguments,
   exception text and unredacted rows while omitting the rendered answer entirely — a route
   returning it would publish everything `render.present` exists to withhold. `answer.public()`
   is the only supported projection, and `test_intelligence_ask.py` pins the exact key set.
3. **Every cited value links to its own subgraph — and that subgraph is held to the same
   contract as the page.** Each pointer the tools emit names an entity, so a citation resolves
   through `GET /intelligence/subgraph/{id}`. That drill-down runs `intelligence_surface`'s own
   structural traversal rather than the generic `brain_neighbors` the `/brain` canvas uses: the
   generic one walked a cited document's `for_patient` edges and returned registry patients with
   their attributes and the edge's `match_score`, which is a ranked list of named candidates one
   click from a page whose contract forbids one.
4. **Nothing a document wrote is rendered here.** A document is an opaque handle plus its
   `document_type`; an organisation prints its name only when that name is a cell in
   `coverage_rules.yaml`. A filename can be `Alice_Tan_referral.pdf` — an ordinary label no content
   screen can see — so the rule is about provenance, not phrasing (`intelligence_surface`).
5. **The question is screened FIRST**, by the agent's own deterministic gate
   (`databridge_agent.screen_question`) — before the `as_of` parse, before the graph, before the
   model route. An identity question is refused with `model_calls == 0` and the refusal is a 200
   carrying the fixed staff-facing text. Screening after the configuration checks meant an unloaded
   graph answered "the clinic graph is not loaded" to "is this the same person?".
6. **Counts are never truncated.** Every figure is exact at any graph size (C-4,
   `intelligence_counts`): past the detail envelope only the member LISTS are left out, and each
   figure says so. The agent and the drill-down still refuse past their bounds rather than walk part
   of a graph (`intelligence_surface.check_graph_bounds`).

**R-015.** All four routes below used to be reachable by anyone, unauthenticated
(`phase5-gw-intelligence.md` #1) — this surface's own comments even documented "there is no
authenticated staff session behind `staff_id`" as if that were only a scoping limit on what a tool
may do, when it was also true of the HTTP boundary itself. Every route now takes
`auth.require_principal`; `_run_agent` records the authenticated `principal_id` on the trace as
`on_behalf_of` instead of the fixed `None` it used to record, which was an honest defect
(`"there is no human identity to record"`) that the auth gate now actually fixes rather than
merely narrating.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Literal, Mapping, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# Imported at module scope, not lazily, because both are `response_model` types: pydantic needs
# the real class when the model below is defined, and a string annotation resolved later would
# make the published schema depend on import order.
from databridge_agent.response import AgentResponse

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .outbox_routes import ActionCard, BlockedProposal, propose_actions

log = logging.getLogger("databridge.gateway.intelligence")

#: What a malformed `?as_of=` is answered with. The offending value is deliberately NOT quoted
#: back: it is caller-supplied text on a page that renders no uncited free text anywhere else, and
#: an error message is the classic place that rule gets forgotten. It goes to the log instead.
BAD_AS_OF_MESSAGE = ("the `as of` value is not an ISO-8601 datetime (for example 2026-08-01). "
                     "Refusing to guess which instant was meant — a rewound figure that quietly "
                     "stopped being rewound is a wrong number presented as a right one.")

#: What `/intelligence/ask` says when the structural reader found nothing (E2E run 2, N-15).
#: Pack-neutral: this route serves every binding, so it names no pack's graph or vocabulary.
NOTHING_ANSWERED_TEXT = ("Nothing in the knowledge graph answered that. I would rather say so "
                         "than fill the gap.")
NOTHING_ANSWERED_ALTERNATIVE = ("This reader answers structural questions (counts, what is "
                                "waiting, what changed). For what a record or document says, ask "
                                "on /brain/query, which answers with a citation per value.")

#: How this surface names itself on the audit surface (`GET /trace`). Three constants rather than
#: literals at the call site, because a trace's `agent`/`version` are what an auditor quotes back,
#: and a name that drifts between two call sites makes two agents out of one.
AGENT_NAME = "intelligence.reader"
AGENT_VERSION = "v1"
AGENT_ACTOR = "gateway.intelligence"

#: A front-desk question, not a document. Anything longer is a paste, and a paste is an attempt to
#: get document text into the one message that is NOT framed as untrusted data (`frame.py` frames
#: it anyway — this keeps the prompt bounded as well).
MAX_QUESTION_CHARS = 500

#: What the figure payload says about itself. A module constant because two routes serve it now —
#: `GET /intelligence` and the console, when a request asked for figures — and a caveat that
#: existed on only one of them would be a caveat that stopped applying the moment the page moved.
FIGURES_NOTE = (
    "Counts of structural facts, each with its denominator. Time saved is measured separately at "
    "GET /impact and is never derived from these. These figures and the grounded answer beside "
    "them are two reads of the graph: within one answer every tool shares one snapshot, but a "
    "document arriving between the two can leave a card and an answer a moment apart. Both "
    "report the `as_of` window they were read in; neither claims to have been read at the same "
    "instant as the other.")


class RefusalBody(BaseModel):
    """The refusal shape every route on this surface returns. A MODEL, not a dict literal.

    `_refusal` used to build the body by hand and hand it to `JSONResponse`, which bypasses
    FastAPI's `response_model` entirely — so the one path most likely to be wrong (the error path)
    was the one path with no contract on it, and the route's declared type was a claim about the
    happy case only. Every refusal below is now validated against this type before it is
    serialised, and `responses=` publishes it in the schema so the claim is checkable from
    outside.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    nearest_alternative: Optional[str] = None


class SurfaceRefusal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    refusal: RefusalBody


#: What every route here declares for its refusal statuses, so the contract covers both paths.
REFUSAL_RESPONSES = {status: {"model": SurfaceRefusal} for status in (401, 403, 404, 422, 503)}


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    body = SurfaceRefusal(refusal=RefusalBody(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _parse_as_of(raw: Optional[str]) -> Optional[dt.datetime]:
    """`?as_of=` → datetime, or `ValueError`.

    A malformed value is a refusal, never a silent fall-back to "now": a rewound figure that
    quietly stopped being rewound is a wrong number presented as a right one.
    """
    if raw is None or raw == "":
        return None
    value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # `analytics.compute` normalises a naive value to UTC itself (that was one of T-43's two
    # defects); this is only so the echoed `as_of` says what was actually used.
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


#: How many steps the agent may take. Enough for the figure reads a question is most likely to
#: need plus an entity read; the rest are recorded as `not_read`, never silently dropped.
AGENT_MAX_STEPS = 6


def _store(app: FastAPI):
    """The shared graph store — ``app.state.graph_store``, else ``app.state.brain.store``."""
    from .evidence_routes import graph_store_of

    return graph_store_of(app)


def _graph_analytics(app: FastAPI, as_of: Optional[dt.datetime]) -> tuple[Optional[dict],
                                                                         Optional[str]]:
    """(payload, unavailable_reason). Never raises — see the module docstring.

    C-4 (finance dry run): every figure is EXACT at any graph size. The page used to withhold all
    of them past 5,000 nodes / 20,000 relationships. Now:

    * within `MAX_GRAPH_NODES` / `MAX_GRAPH_EDGES` (the DETAIL envelope, measured on the requested
      snapshot) the figures come from `analytics.compute`, with the member ids each one counted;
    * past it, the OPEN graph is counted in the store (`intelligence_counts.store_figures`, SQL
      aggregates) and a rewound one by `compute`; either way the numbers are exact, and only the
      member LISTS are left out, each figure saying so (`MEMBERS_NOT_LISTED`).

    "Unavailable" is now reserved for a real failure: no graph, a history the reader cannot
    rewind, or a read that raised."""
    store = _store(app)
    if store is None:
        return None, "the graph is not loaded"
    try:
        from databridge_graph.analytics import SnapshotIncomplete, compute

        from .intelligence_counts import store_figures, supports_sql, unlist_members
        from .intelligence_surface import (
            COUNT_FIGURES,
            SurfaceNames,
            sanitise_analytics,
            snapshot_view,
            within_detail_envelope,
        )

        try:
            # ONE snapshot of the requested instant, read once (R-019 t08b #5/#9): the envelope,
            # the naming policy and every in-memory figure are computed over the SAME rows.
            view = snapshot_view(store, as_of)
        except SnapshotIncomplete as exc:
            return None, exc.message
        names = SurfaceNames(view)
        if within_detail_envelope(view):
            raw = compute(store, snapshot=view.snapshot).to_dict()
            listed = True
        elif as_of is None and supports_sql(store):
            raw = store_figures(store)
            listed = False
        else:
            # A rewound snapshot past the envelope: the store's SQL counts the OPEN graph only,
            # so the rewind is counted in memory — exactly — and its member lists dropped below.
            raw = compute(store, snapshot=view.snapshot).to_dict()
            listed = False
        # Sanitised on the way out, always: a figure counting SUBJECT rows has subject ids as its
        # members, and the entity table would otherwise carry their names.
        payload = sanitise_analytics(raw, names)
        if not listed:
            payload = unlist_members(payload, COUNT_FIGURES)
        else:
            payload["members_listed"] = True
        # Which kinds are withheld and why — kind names only. The claim EDGE names are left out:
        # this page never walks a claim, so it has no reason to name one.
        payload["withholding"] = names.policy.describe(include_edges=False)
        return payload, None
    except Exception:                                                # noqa: BLE001
        log.exception("graph analytics unavailable; serving the page without them")
        return None, "the graph traversal failed; see server logs"


def _ingestion_summary() -> tuple[Optional[dict], Optional[str]]:
    """Coverage and exclusions per ingestion source (P3/P7). Never raises; empty is said out loud.

    Not rewound by `as_of`: the run ledgers are files, not bi-temporal facts.
    """
    try:
        from .source_capture import source_summaries

        sources = source_summaries()
    except Exception:                                                # noqa: BLE001
        log.exception("ingestion ledgers unreadable; serving the page without them")
        return None, "the ingestion run ledgers could not be read; see server logs"
    return {
        "sources": sources,
        "excluded": sum(s["latest"]["excluded"] for s in sources),
        "note": None if sources else (
            "no ingestion run has written its ledgers yet — this is an empty register, not a "
            "clean one"),
    }, None


def _queue_summary(app: FastAPI) -> tuple[Optional[dict], Optional[str]]:
    """What the counter is waiting on, counted over the intakes actually held.

    Reads the same `app.state` the `/intake` queue reads, and the same
    `ConfirmationState.is_ready_to_commit()` the confirm gate reads, rather than re-deriving
    "blocked" from a copy of those rules — a second definition of blocked would drift from the
    gate, and the gate is the one that matters.
    """
    intakes = getattr(app.state, "intakes", None)
    if intakes is None:
        # C-5: a deployment that mounts no intake queue has nothing to summarise. That is not a
        # failure, so it is not reported as one (no `queue_unavailable` reason for the page to
        # print under a heading about a queue that does not exist); `queue_mounted: false` says
        # so instead, and the page leaves the card out.
        return None, None
    try:
        from databridge_intake.ledger import attestation_subject_matches
        from databridge_intake.models import ConfirmationState

        lock = getattr(app.state, "intake_lock", None)
        if lock is not None:
            # Snapshot under the lock: `/impact` learned the hard way that iterating this dict
            # while an upload inserts into it is a live-demo 500, not a theoretical one.
            with lock:
                held = list(intakes.items())
                states = dict(getattr(app.state, "confirmations", {}))
        else:
            held = list(intakes.items())
            states = dict(getattr(app.state, "confirmations", {}))

        blocked, ready_to_commit, committed = [], [], 0
        for intake_id, entry in held:
            draft = entry["draft"]
            state = states.get(intake_id) or ConfirmationState(intake_id=intake_id)
            if state.committed:
                committed += 1
                continue
            ready, because = state.is_ready_to_commit()
            # R-22. The confirmation was joined to the current draft by `intake_id` alone — and
            # `intake_id` is derived from the document, so it is identical before and after a
            # re-upload re-extracts it. A replaced draft therefore reported `identity_verified:
            # true` off an attestation made about the reading it replaced. Ask the same question
            # the confirm gate asks: is this attestation still ABOUT this draft?
            verified, mismatch = attestation_subject_matches(draft.record, draft.resolution, state)
            if not verified and not because:
                # `is_ready_to_commit()` sees a complete state and reports nothing to wait on,
                # which would put a blocked intake on the board with an empty reason. What it is
                # waiting on is a fresh in-person check.
                because = mismatch
            entry = {
                "intake_id": intake_id,
                "document_id": draft.record.document.document_id,
                "document_type": draft.record.document_type.value,
                # HOW MANY candidates the registry returned — never which. Hard constraint 1.
                "candidate_count": len(draft.resolution.candidates),
                "coverage_status": draft.coverage.status.value,
                "eligibility_flags": list(draft.coverage.eligibility_flags),
                "blocked_because": because,
                "identity_verified": verified,
            }
            if ready and verified:
                # R-017 t08 #7. The gate WOULD open: nothing is blocking this intake. It waits on
                # the staff member's confirm, and is listed as such — counting it as blocked with
                # an empty reason inflated `blocked_count` and put a reasonless row on the board.
                entry["blocked_because"] = None
                entry["waiting_on"] = "the staff member's confirmation (the gate is ready to open)"
                ready_to_commit.append(entry)
            else:
                blocked.append(entry)
        blocked.sort(key=lambda b: b["intake_id"])
        ready_to_commit.sort(key=lambda b: b["intake_id"])
        return {
            "intakes": len(held),
            "committed": committed,
            "blocked": blocked,
            "blocked_count": len(blocked),
            "ready_to_commit": ready_to_commit,
            "ready_count": len(ready_to_commit),
        }, None
    except Exception:                                                # noqa: BLE001
        log.exception("intake queue summary unavailable; serving the page without it")
        return None, "the intake queue could not be summarised; see server logs"


class AskRequest(BaseModel):
    """What a member of staff sends. Two fields, and neither can widen what the agent may read.

    There is deliberately **no** `staff_id`, no grant, no patient and no tool list. A route that
    accepted any of those would be letting the caller — which, once a document is in the loop, may
    as well be the document — choose the agent's capabilities (`sealed.PatientReadGrant`: *never
    deserialize a grant from a request*).
    """

    question: str = Field(default="")
    #: The page's own `as of` control, applied server-side to the traversal. Staff-supplied, never
    #: a tool argument — the model has no way to move the window it reads over.
    as_of: Optional[str] = None


class ConsoleRequest(BaseModel):
    """What a member of staff types into the console, plus the two things only a human may supply.

    ``intake_id`` names a registration. It is here because the agent **may not choose who a
    message is about** (`AGENT_CAPABILITIES.md` §The two rules): a request that implies an act is
    acted on for the registration a person named, and there is no path by which the model, or a
    document the model read, selects one.

    ``typed_address`` is door (b) of the recipient rule, passed to
    ``recipients.recipient_typed_by_human`` with the SIGNED-IN principal as who typed it;
    ``typed_by`` in the body is ignored (R-027 ONE). It is read only by a clinic rule that declares that
    door; a rule declaring ``committed_ledger`` refuses a typed address outright
    (``build_outbound_draft``), so supplying one cannot redirect a message.
    """

    request: str = Field(default="")
    as_of: Optional[str] = None
    intake_id: Optional[str] = None
    typed_address: Optional[str] = None
    typed_by: Optional[str] = None


#: The console's own sentences, addressed by key — the same device ``databridge_agent.render.NOTES``
#: uses, and for the same reason: a sentence a model could phrase is a sentence a model could
#: rephrase. The KEYS are code; the TEXT is the bound pack's (`packs/<pack>/agent_wording.yaml`,
#: `console_notes`). HC1 (2026-10-01, SWEEP hardcode audit #5): these used to be literals here
#: speaking of "the clinic's own rules" and "which patient a message is about" under every pack.
def console_notes() -> Mapping[str, str]:
    from databridge_agent.wording import wording

    return wording().console_notes


def echo_request(request: str) -> str:
    """The staff member's own words, neutralised, for printing under the box they typed them in.

    **Neutralised, not redacted, and the difference is deliberate.**
    ``databridge_agent.render.present`` replaces an instruction-shaped value with a fixed
    redaction line, and that rule is exactly right for a value read out of a document: a clinical
    document talking to an automated reader must not have its payload reproduced in the clinic's
    own UI.

    It is the wrong rule for this one string. A request that implies an act — "email the patient
    about the test that is not covered" — is instruction-shaped *by construction*; it is the
    primary thing this box exists to accept, and it was typed one second ago by the person now
    reading the response. Echoing it back as "[withheld — this value contains text addressed to an
    automated reader]" tells that person nothing and makes the propose path unreadable.

    So this applies ``frame.untrusted`` — the same neutraliser used on the way *in* to the model —
    which strips frame markers, control, zero-width and bidi characters, role headers and
    chat-template tokens, and caps the length. Nothing here is dropped silently: every replacement
    leaves a visible marker.

    ``present`` is untouched and still governs every value that came out of a *tool*, and the
    agent's own ``AgentResponse.question`` on the nested answer is still rendered by it — so both
    renderings are on the page and the difference between them is visible rather than assumed.
    """
    from databridge_agent.frame import untrusted

    text = untrusted(request or "").strip()
    return text[:MAX_QUESTION_CHARS] if len(text) > MAX_QUESTION_CHARS else text


class ConsoleNote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    text: str


def console_note(key: str) -> ConsoleNote:
    return ConsoleNote(key=key, text=console_notes()[key])


#: Requests that are asking for figures. A closed, code-authored set of shapes — never a model
#: deciding what kind of request this was. The page's whole redesign rests on this: analytics are
#: one possible ANSWER, so something has to decide when they were asked for, and that something
#: must be as inspectable as the rest of the surface.
_FIGURE_REQUESTS = re.compile(
    r"\b(?:figures?|analytics|dashboard|breakdown|statistics|stats|how many|count|counts|"
    r"totals?|overview|mix|what(?:'s| is) (?:still )?(?:blocked|outstanding|waiting))\b", re.I)


def asks_for_figures(request: str) -> bool:
    return bool(_FIGURE_REQUESTS.search(request or ""))


def monitors_unavailable(app: FastAPI) -> Optional[str]:
    """Why no drafting rule applies here, or ``None`` when the monitors may be evaluated.

    The monitors (``databridge_outbox`` config) observe an INTAKE and address its SUBJECT through
    the committed-ledger door. Both halves have to exist in this deployment for a monitor to mean
    anything: an intake queue mounted, and a bound pack declaring a subject kind for the door to
    resolve. Anything else would evaluate one product's rules against a graph they were not
    written for. Derived from the deployment, never from a pack's name.
    """
    if getattr(app.state, "intakes", None) is None:
        return ("no intake queue is mounted in this deployment, so no drafting rule applies — "
                "the monitors observe intakes and nothing else")
    from databridge_graph.packs import active_vocabulary

    if not active_vocabulary().subject_kinds:
        return ("no bound pack declares a subject kind, so the committed-ledger door the "
                "monitors address a message through has nothing to resolve")
    return None


class ConsoleResponse(BaseModel):
    """What the console returns. The key set is pinned by ``test_console_routes.py``.

    ``AgentResponse`` for the read half, unchanged and nested rather than flattened — flattening
    would put this type's fields and that one's in one namespace, and the next field added to
    either would silently widen the other. ``proposed`` carries ``ActionCard``s, which are held to
    the same withholding rules as everything else on this page (``outbox_routes``).

    Note what is absent, and deliberately: any field carrying a recipient's address, a patient
    name, a candidate, a score, a minute or a dollar.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The staff member's own words, neutralised by :func:`echo_request`. Not redacted — see that
    #: function for why a request box is the one string on this page where the renderer's
    #: instruction-shaped rule would be a category error.
    request: str
    #: Which half of the design ran. Decided by ``databridge_agent.screen_question`` — the
    #: deterministic gate that already refuses send requests — never by a model.
    intent: Literal["answer", "propose"]
    answer: Optional[AgentResponse] = None
    proposed: tuple[ActionCard, ...] = ()
    blocked: tuple[BlockedProposal, ...] = ()
    #: The `/intelligence` figure payload, present only when the request asked for figures.
    analytics: Optional[dict] = None
    notes: tuple[ConsoleNote, ...] = ()
    #: Why the grounded answer is absent, when it is. The console answers with a 200 and this
    #: field rather than with a 4xx, because one request produces one description of everything
    #: that happened: the read half can fail while the figures half stands, and an HTTP error
    #: would throw away the half that worked. The hard failures — an empty request, a malformed
    #: `as of` — are still statuses, because there is nothing to describe.
    refusal: Optional[RefusalBody] = None


def _reasoning_harness(app: FastAPI):
    """``(harness, planner)``: a real model when one is configured and reachable, the deterministic
    planner when NO model is configured, and a raise when one is configured but unreachable.

    `build_extraction_harness` is reused rather than reimplemented: `agent_reasoning` routes to the
    `sonnet` alias, and that function's whole job is to remap that alias onto whichever configured
    provider actually holds a key. Its name says "extraction" because that was its first caller;
    what it does is "a Harness for the alias the pipeline uses", which is this one too. Writing a
    second copy here would mean two places to update when the provider story changes.
    """
    from .agent_gold import agent_gold_harness
    from .llm import build_extraction_harness, detect_llm

    detected = getattr(app.state, "llm_meta", None) or detect_llm()
    harness = build_extraction_harness(detected)
    if harness is not None:
        return harness, "live"
    # HC1 (2026-10-01): the deterministic planner only when NO model is configured — the same rule
    # `/intake` follows. A key that is set but routes nowhere used to fall through to the planner
    # silently; it is now a refusal, and an offline answer says it was planned offline.
    import os

    if os.getenv("DATABRIDGE_FORCE_GOLD") != "1":
        from . import llm as gateway_llm

        keyed = [env for env in gateway_llm._PROVIDER_KEY_ENV.values() if os.getenv(env)]
        if keyed:
            raise _ModelConfiguredButUnreachable(
                f"{', '.join(sorted(keyed))} is set but no reasoning model is reachable")
    return agent_gold_harness(), "offline_gold"


class _ModelConfiguredButUnreachable(RuntimeError):
    """A model is configured for the agent and cannot be used. Refused, never planned offline."""


def _run_agent(app: FastAPI, question: str, parsed: Optional[dt.datetime], *,
               principal_id: Optional[str] = None
               ) -> tuple[Optional[AgentResponse], Optional[RefusalBody], int]:
    """The read half, shared by `/intelligence/ask` and `/intelligence/console`.

    Returns ``(response, None, 200)`` or ``(None, refusal, status)``. A tuple rather than a
    ``JSONResponse``, because the two callers need the failure in different shapes: `/ask` returns
    it as a status, and the console carries it as a field beside the figures the same request may
    also have asked for. Written once so the two cannot drift into refusing different things.

    **Fails CLOSED throughout.** An answer is a sentence a member of staff acts on; a thinner one
    assembled from a graph that would not load is worse than none.

    The question is assumed to have been screened already: both callers run
    ``databridge_agent.screen_question`` first, before the `as_of` parse and before any of the
    configuration checks below, so that an identity question is refused with ``model_calls == 0``
    whatever state the rest of the system is in.
    """
    from .trace_recorder import recorder_for

    # The trace is opened BEFORE the first check, so a request refused for a configuration reason
    # still appears on the audit surface. A surface that only recorded the requests that succeeded
    # would be a record of the happy path wearing the name of an audit trail.
    started = time.perf_counter()
    tr = recorder_for(app).begin(surface="intelligence.ask", agent=AGENT_NAME,
                                 version=AGENT_VERSION)
    if principal_id is not None:
        # R-015: the route this call site serves now sits behind `auth.require_principal`, so
        # `on_behalf_of` is a real, authenticated identity — recorded here rather than left `None`.
        tr.principal(actor=AGENT_ACTOR, on_behalf_of=principal_id, purpose=None)
    else:                                              # pragma: no cover - defensive; see below
        tr.principal(actor=AGENT_ACTOR, on_behalf_of=None, purpose=None)
        tr.unrecordable(
            what="on_behalf_of",
            because="this call was not made through an authenticated route, so there is no human "
                    "identity to record.")

    store = _store(app)
    if store is None:
        tr.close("refused")
        return None, RefusalBody(
            code="GRAPH_UNAVAILABLE",
            message="the graph is not loaded, so there is nothing to read and nothing to "
                    "cite"), 503

    from databridge_agent import AGENT_TASK_CLASS
    from databridge_core import resolve_model

    try:
        resolve_model(AGENT_TASK_CLASS)
    except Exception:                                                # noqa: BLE001
        log.exception("no model route for %r", AGENT_TASK_CLASS)
        tr.close("refused")
        return None, RefusalBody(
            code="AGENT_MODEL_NOT_CONFIGURED",
            message=f"no model is routed for '{AGENT_TASK_CLASS}' in config/models.yaml, so this "
                    f"question cannot be planned. Routing is configuration; adding the line is "
                    f"the fix."), 503

    from databridge_graph.analytics import SnapshotIncomplete

    from .agent_tools import _ReadOnlyGraphView
    from .intelligence_surface import SurfaceTooLarge, check_graph_bounds, snapshot_view

    try:
        # The snapshot the whole answer reads, taken once and bounded as itself (R-019 t08b #5/#9).
        view = snapshot_view(_ReadOnlyGraphView(store), parsed)
        check_graph_bounds(view)
    except SnapshotIncomplete as exc:
        tr.close("refused")
        return None, RefusalBody(code=exc.code, message=exc.message), 503
    except SurfaceTooLarge as exc:
        # Loud, not truncated. Every value this agent reports is a count or a member of one, and a
        # count computed over part of a graph is a wrong number, not a partial one.
        tr.close("refused")
        return None, RefusalBody(code=exc.code, message=exc.message), 503

    try:
        from databridge_agent import ClinicAgent

        from .agent_tools import build_structural_registry

        registry = build_structural_registry(store, as_of=parsed, snapshot=view)
        harness, planner = _reasoning_harness(app)
        answer = ClinicAgent(registry, harness, max_steps=AGENT_MAX_STEPS).ask(question)
    except _ModelConfiguredButUnreachable as exc:
        tr.close("refused")
        return None, RefusalBody(code="LIVE_MODEL_UNAVAILABLE",
                                 message=f"{exc}. Nothing was answered; see /health."), 503
    except Exception:                                                # noqa: BLE001
        log.exception("the agent failed on %r", question[:120])
        tr.unrecordable(what="everything the agent did before it raised",
                        because="the agent raised, so no read record survived to be recorded")
        tr.close("failed")
        return None, RefusalBody(
            code="AGENT_UNAVAILABLE",
            message="the agent could not complete this read; see server logs. No partial answer "
                    "is returned, because a partial answer is the one thing worse than none "
                    "here"), 503

    response = answer.public().model_copy(update={"planner": planner})
    _record_agent_read(tr, response, parsed, wall_ms=int((time.perf_counter() - started) * 1000))
    if not response.refused and not response.values:
        # Refuse rather than render an empty answer. The wording is the GATEWAY's, pack-neutral
        # (E2E run 2, N-15): the agent package's note named one pack's graph ("the clinic
        # graph") under every binding. Code-authored either way; no model phrases it.
        tr.close("refused")
        return None, RefusalBody(code="NO_GROUNDED_ANSWER",
                                 message=NOTHING_ANSWERED_TEXT,
                                 nearest_alternative=NOTHING_ANSWERED_ALTERNATIVE), 422
    tr.close("refused" if response.refused else "answered")
    return response, None, 200


def _pointer_id(pointer) -> Optional[str]:
    """A SourcePointer's stable id (`ptr:sha256:<pointer_digest>`), or None if it cannot be
    digested — the release then shows `RELEASE_POINTER_NOT_RECORDED`, never a made-up id."""
    try:
        from databridge_adapter import pointer_digest           # noqa: PLC0415
        return f"ptr:sha256:{pointer_digest(pointer)}"
    except Exception:                                            # noqa: BLE001
        return None


def _record_agent_read(tr, response, as_of, *, wall_ms: int) -> None:
    """Record what the read actually did, onto the trace opened by :func:`_run_agent`.

    Everything here is read off the response the agent returned; nothing is inferred and nothing
    is filled in. Three things are worth naming:

    * **Every tool call is a capability step**, including the ones that failed — a read that was
      attempted and refused is part of the authorisation path, and dropping it would make the
      trace a record of what worked.
    * **`origin="graph"` is a fact, not a default.** These tools read the bi-temporal store and
      nothing else; no live source is contacted on this path. That is precisely the claim
      "answered from history" is making, so it is recorded per pointer rather than asserted once.
    * **The ledger is not appended on this path, and the trace says so.** `released()` looks for
      an earlier `ledger_append` covering the field and records a `LEDGER_APPEND_NOT_RECORDED`
      defect when there is none. There is none here: this surface discloses values from a
      read-only traversal without writing a read entry first, which `AGENT_PLANE.md` §7 property 3
      requires. Recording the gap is the whole point of building the recorder before the ledger
      write exists — a recorder that only ever agreed with the code would prove nothing.
    """
    when = as_of.isoformat() if as_of else None
    for read in getattr(response, "reads", ()) or ():
        tr.capability(source_id="graph", capability=getattr(read, "tool", None),
                      as_of=when)
        status = getattr(read, "status", "")
        if status and status != "ok":
            tr.note(key="read_not_ok", text=f"{getattr(read, 'tool', '')}: {status}")

    for value in getattr(response, "values", ()) or ():
        pointer = getattr(value, "source", None)
        trust = getattr(getattr(pointer, "trust", None), "value", None)
        path = f"{getattr(value, 'tool', '')}.{getattr(value, 'field', '')}"
        pid = None
        if pointer is not None:
            # INT5 (ACT3: `released()` requires a pointer id an earlier `pointer` step recorded
            # for the SAME source). The value was read from the graph, so the step is recorded
            # under source "graph" — the source this trace's capability and cell steps name —
            # with the pointer's stable id (`ptr:sha256:<adapter pointer_digest>`).
            pid = _pointer_id(pointer)
            tr.pointer(origin="graph", pointer_id=pid, source_id="graph",
                       kind=str(getattr(pointer, "kind", "") or "") or None, trust=trust,
                       as_of=when)
        if getattr(value, "withheld", False):
            # A withheld value (the standard envelope in `text`'s place, R-028 TIDY #7) is
            # WITHHELD, not grounded: the renderer refused to print it and said why. Counting it
            # as a citation would put a value the reader never saw into the citation count.
            tr.cell(state="withheld", field_path=path, trust=trust,
                    reason_code="instruction_shaped_value", policy_id="databridge_agent.render",
                    source_id="graph")
        else:
            tr.cell(state="grounded", field_path=path, trust=trust, source_id="graph")
            tr.released(field_path=path, source_id="graph", pointer_id=pid)

    for path in getattr(response, "unresolved", ()) or ():
        # Absent, not withheld. Nothing denied these — the source had no value — and the two are
        # separate rows on the surface for exactly that reason.
        tr.cell(state="absent", field_path=str(path), source_id="graph")

    if getattr(response, "requires_human_selection", False):
        tr.escalation(
            to="a named human",
            reason="the read returned candidates; selecting one is a determination reserved for "
                   "a human and is never made by this system")
    if getattr(response, "refused", False):
        tr.note(key="agent_refusal", text=str(getattr(response, "refusal_code", "") or "refused"))

    tr.cost(wall_ms=wall_ms, model_calls=int(getattr(response, "model_calls", 0) or 0))
    tr.unrecordable(
        what="llm token usage",
        because="the harness does not return a token count to this call site, so budget_used is "
                "null rather than zero")


def _register_intelligence_routes(app: FastAPI) -> None:
    """Attach the BI surface. The figures and ``/ask`` are read-only: nothing they do writes to
    the graph or the ledger. The page's one-time tasks (FEAT8, `one_time_routes`) are mounted
    here too: they run through the agent runtime, and any write they propose waits for a person."""
    install_auth_handler(app)
    from .one_time_routes import register_one_time_routes             # noqa: PLC0415
    register_one_time_routes(app)

    @app.get("/intelligence")
    def intelligence(as_of: Optional[str] = None,
                     _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Structural counts over the shared graph, plus — when this deployment mounts an intake
        queue — what that queue is waiting on.

        `?as_of=2026-08-01` rewinds the graph figures to what was true on that date. The queue is
        the live queue and is not rewound — `app.state.intakes` is not bi-temporal, and pretending
        otherwise would put a real number next to a fabricated one.
        """
        try:
            parsed = _parse_as_of(as_of)
        except ValueError:
            log.info("rejected a malformed as_of on /intelligence")
            return _refusal("BAD_AS_OF", BAD_AS_OF_MESSAGE)
        graph, graph_unavailable = _graph_analytics(app, parsed)
        queue, queue_unavailable = _queue_summary(app)
        queue_mounted = getattr(app.state, "intakes", None) is not None
        ingestion, ingestion_unavailable = _ingestion_summary()
        return {
            "as_of": parsed.isoformat() if parsed else None,
            "graph": graph,
            "graph_unavailable": graph_unavailable,
            "queue": queue,
            "queue_unavailable": queue_unavailable,
            # Whether this deployment mounts an intake queue at all. False is not a failure: the
            # page shows no queue card, rather than a card about a queue the pack never declared.
            "queue_mounted": queue_mounted,
            "ingestion": ingestion,
            "ingestion_unavailable": ingestion_unavailable,
            # Said in the payload, not only in the UI copy, so an agent reading this over MCP in
            # Phase 8 inherits the distinction rather than having to be told it.
            "note": FIGURES_NOTE,
        }

    @app.get("/intelligence/subgraph/{entity_id:path}", responses=REFUSAL_RESPONSES)
    def intelligence_subgraph(entity_id: str, depth: int = 1, as_of: Optional[str] = None,
                              _principal: AuthenticatedPrincipal = Depends(require_principal)):
        """The traversal behind one figure's member — how a number on the page is checked.

        **Its own traversal, not `brain_neighbors`.** The generic one serves `/brain`, which has a
        different contract; running it here walked the cited document's `for_patient` edges and
        returned registry patient nodes — name, date of birth, nationality — together with the
        edge's `match_score` and `matched_on`. That is a ranked list of named candidates, one
        click from a page whose whole contract is that it never produces one.

        Filtering that result for this consumer would have been a subtractive control: it works by
        remembering to subtract, so a node kind added later is exposed until somebody notices.
        `structural_neighbors` is additive — an allow-list of edge kinds between an allow-list of
        node kinds — so a new kind is invisible here until a human classifies it. The cost is that
        this page cannot show a relationship nobody has classified; that is the right way to fail.

        `as_of` is threaded through so a drill-down reads the same window the figure and the
        answer were computed in. Without it a rewound answer opened into the present graph and
        showed evidence that had changed, or none at all.
        """
        store = _store(app)
        if store is None:
            return _refusal("GRAPH_UNAVAILABLE", "the graph is not loaded", status=503)
        try:
            parsed = _parse_as_of(as_of)
        except ValueError:
            log.info("rejected a malformed as_of on /intelligence/subgraph")
            return _refusal("BAD_AS_OF", BAD_AS_OF_MESSAGE)
        depth = max(1, min(depth, 3))                # a BI drill-down, not a graph dump

        from databridge_graph.analytics import SnapshotIncomplete

        from .intelligence_surface import (
            SurfaceNames,
            SurfaceTooLarge,
            check_graph_bounds,
            snapshot_view,
            structural_neighbors,
            subgraph_edge_sources,
            subgraph_sources,
        )

        try:
            # The requested snapshot FIRST (R-019 t08b #3/#9): the token is resolved, the bound is
            # checked, names are classified and pointers are read against the rows that stood at
            # `as_of` — an entity closed since resolves in its own window, and a label is the one
            # its version carried, checked against that version's pointers.
            view = snapshot_view(store, parsed)
            check_graph_bounds(view)
            names = SurfaceNames(view)
            resolved = names.resolve(entity_id)
            if resolved is None:
                return _refusal("UNKNOWN_ENTITY",
                                "no entity on this surface matched that reference", status=404)
            out = structural_neighbors(view, names, resolved, depth=depth, as_of=parsed)
        except SnapshotIncomplete as exc:
            return _refusal(exc.code, exc.message, status=503)
        except SurfaceTooLarge as exc:
            # R-015: one existence-oracle policy, applied the same way `/evidence/trace/{id}` uses
            # it — 403 means "this exists and is not releasable here", 404 means "no such entity",
            # and the two are never conflated. `NOT_ON_THIS_SURFACE` used to be 422 ("bad request")
            # here while the evidence surface used 403 for the identical shape of refusal
            # (`phase5-gw-intelligence.md` #9); one deployment answering the same question two
            # different ways is itself a distinguishing signal. `GRAPH_TOO_LARGE`/`SUBGRAPH_TOO_LARGE`
            # stay 503 — a capacity refusal, not an authorisation one.
            return _refusal(exc.code, exc.message,
                            status=403 if exc.code == "NOT_ON_THIS_SURFACE" else 503)
        except Exception:                                            # noqa: BLE001
            log.exception("subgraph lookup failed")
            return _refusal("SUBGRAPH_FAILED", "the traversal failed; see server logs",
                            status=503)
        # R-21. This used to swallow a pointer-lookup failure, set `sources = {}` and return the
        # nodes anyway — the drill-down that exists to let a figure be CHECKED, rendering values
        # with the checking apparatus quietly removed. An empty `sources` map is indistinguishable
        # on the page from "this entity genuinely has no citations", so the failure looked like a
        # fact about the graph. If the evidence cannot be loaded there is nothing to show.
        try:
            sources = subgraph_sources(view, names, out)
            edge_sources = subgraph_edge_sources(view, names, out)
        except Exception:                                            # noqa: BLE001
            log.exception("pointer lookup failed for a subgraph")
            return _refusal(
                "EVIDENCE_UNAVAILABLE",
                "the traversal succeeded but its citations could not be loaded, so the result is "
                "not checkable and is withheld", status=503)
        # Nodes AND relationships (R-019 t08b #2): an edge is a fact, and a fact without its own
        # pointer withholds the whole traversal rather than rendering beside a `n_sources: 0`.
        uncited = sorted([n["id"] for n in out.get("nodes", []) if not sources.get(n["id"])]
                         + [e["id"] for e in out.get("edges", [])
                            if not edge_sources.get(e["id"])])
        if uncited:
            # `GraphNode` refuses construction without a pointer, so this should be unreachable —
            # which is exactly why listing the ids and returning the values anyway was wrong. If
            # it ever does happen, the invariant this whole page rests on has broken, and the
            # honest response is to say so rather than to render values under a caveat field.
            log.error("subgraph returned %d entities with no citations", len(uncited))
            return _refusal(
                "EVIDENCE_UNAVAILABLE",
                f"{len(uncited)} entity/relationship fact(s) in this traversal have no "
                f"SourcePointer, so the result is not checkable and is withheld", status=503)
        return {**out, "depth": depth, "as_of": parsed.isoformat() if parsed else None,
                "sources": sources, "edge_sources": edge_sources}

    # -- the agent ------------------------------------------------------------------
    from databridge_agent import AgentResponse

    @app.post("/intelligence/ask", response_model=AgentResponse,
              response_model_exclude_none=True, responses=REFUSAL_RESPONSES)
    def intelligence_ask(payload: AskRequest,
                         principal: AuthenticatedPrincipal = Depends(require_principal)):
        """A question in, a grounded list of values out — each with the pointer it came from.

        Returns `AgentResponse` and nothing else. The agent's OWN refusals (an identity question,
        a request to send something) come back as a 200 carrying `refused: true` and the fixed
        staff-facing text: they are the honest answer to that question, not a server error. The
        `_refusal` bodies below are the other kind — the surface could not run at all.
        """
        question = (payload.question or "").strip()
        if not question:
            return _refusal("QUESTION_REQUIRED", "ask a question; there is nothing to answer")
        if len(question) > MAX_QUESTION_CHARS:
            return _refusal(
                "QUESTION_TOO_LONG",
                f"a question is {MAX_QUESTION_CHARS} characters or fewer. This box is for a "
                f"question, not for a document — drop documents on /intake, where every value "
                f"extracted from them is grounded and reviewed by a person.")
        # THE SCREEN COMES FIRST — before the as_of parse, before the graph, before the model
        # route. The endpoint promises that an identity question is refused with
        # `model_calls == 0`; screening after the configuration checks meant a clinic with an
        # unloaded graph answered "the clinic graph is not loaded" to "is this the same person?".
        # That is a configuration report where a refusal was promised, and the refusal is the part
        # that matters — it must not depend on the rest of the system being healthy.
        from databridge_agent import screen_question

        gated = screen_question(question)
        if gated is not None:
            return gated

        try:
            parsed = _parse_as_of(payload.as_of)
        except ValueError:
            log.info("rejected a malformed as_of on /intelligence/ask")
            return _refusal("BAD_AS_OF", BAD_AS_OF_MESSAGE)

        answered, refused, status = _run_agent(app, question, parsed,
                                               principal_id=principal.principal_id)
        if refused is not None:
            return JSONResponse(status_code=status,
                                content=SurfaceRefusal(refusal=refused).model_dump(mode="json"))
        return answered

    # -- the console ----------------------------------------------------------------
    @app.post("/intelligence/console", response_model=ConsoleResponse,
              responses=REFUSAL_RESPONSES)
    def intelligence_console(payload: ConsoleRequest,
                             principal: AuthenticatedPrincipal = Depends(require_principal)):
        """Ask for something in plain language. One response describing everything that happened.

        `AGENT_CAPABILITIES.md` gives this surface one shape — *a request box; analytics are one
        possible answer, not the content of the page* — and one line that decides the design: the
        agent may **propose** anything and **perform** nothing. This route is where both live.

        **What routes a request, and why it is not a model.** ``databridge_agent.screen_question``
        already refuses a request to send with ``AGENT_HAS_NO_SEND_CAPABILITY`` — a deterministic
        pattern match that runs before any model is reached. That refusal is *also the classifier*:
        a request the screen calls send-shaped is exactly a request that implies an act, so it goes
        to the propose path instead of dead-ending. Nothing about which half runs is decided by a
        model, or by anything a document could influence. An identity question still refuses, and
        refuses first — ``screen_incoming`` checks identity before it checks sending, so a request
        that is both is refused rather than proposed.

        **The propose path cannot choose a recipient.** It needs ``intake_id``, which a member of
        staff supplies by naming a registration on this page; with none, it returns zero proposals
        and the note saying why. From there it is the clinic's own config: the monitors in
        ``clinic_monitors.yaml`` decide what is worth saying, ``recipients.py``'s two doors decide
        who it may be said to, and ``build_outbound_draft`` has no parameter through which an
        address can arrive. What comes back is a DRAFT in the outbox with
        ``requires_human_approval`` — never a send, and there is no code path here that could
        become one.

        **Figures only when asked for.** :func:`asks_for_figures` is a closed set of request
        shapes, code-authored, and it is the whole of the page's redesign: the same counts the
        cards used to render permanently are now one possible answer among three.

        **Everything new here inherits the page's contract.** A proposed action is rendered by
        ``outbox_routes.action_card``, which masks the mailbox, publishes the recipient's
        provenance as a code-authored sentence rather than a derivation string, projects every
        pointer onto a closed shape and screens the draft's own text for instructions. A card is
        not a hole in the withholding rules the traversal closed.
        """
        request = (payload.request or "").strip()
        if not request:
            return _refusal("REQUEST_REQUIRED", "ask for something; there is nothing to do")
        if len(request) > MAX_QUESTION_CHARS:
            return _refusal(
                "REQUEST_TOO_LONG",
                f"a request is {MAX_QUESTION_CHARS} characters or fewer. This box is for a "
                f"request, not for a document — drop documents on /intake, where every value "
                f"extracted from them is grounded and reviewed by a person.")

        # THE SCREEN COMES FIRST, before the `as_of` parse and before the graph — the same order
        # `/intelligence/ask` uses and for the same reason: the refusal must not depend on the
        # rest of the system being healthy.
        from databridge_agent import screen_question
        from databridge_agent.guardrails import NO_SEND_CODE

        gated = screen_question(request)
        echoed = echo_request(request)

        if gated is not None and gated.refusal_code != NO_SEND_CODE:
            # An identity question. It is answered, not routed onward: this system does not decide
            # who somebody is, and a request to do so is not a request to draft something either.
            return ConsoleResponse(request=echoed, intent="answer", answer=gated)

        try:
            parsed = _parse_as_of(payload.as_of)
        except ValueError:
            log.info("rejected a malformed as_of on /intelligence/console")
            return _refusal("BAD_AS_OF", BAD_AS_OF_MESSAGE)

        analytics = None
        if asks_for_figures(request):
            graph, graph_unavailable = _graph_analytics(app, parsed)
            queue, queue_unavailable = _queue_summary(app)
            ingestion, ingestion_unavailable = _ingestion_summary()
            analytics = {"as_of": parsed.isoformat() if parsed else None,
                         "graph": graph, "graph_unavailable": graph_unavailable,
                         "queue": queue, "queue_unavailable": queue_unavailable,
                         "ingestion": ingestion, "ingestion_unavailable": ingestion_unavailable,
                         "note": FIGURES_NOTE}

        notes: list[ConsoleNote] = []
        if analytics is not None:
            notes.append(console_note("figures_on_request"))

        if gated is not None:
            # PROPOSE. The screen called this send-shaped, so it is a request to act.
            notes.extend([console_note("proposal_only"), console_note("no_authentication")])
            unavailable = monitors_unavailable(app)
            if unavailable is not None:
                # No drafting rules apply to this deployment. Said, not silently empty: a propose
                # request that returned nothing would read as "nothing is due".
                return ConsoleResponse(
                    request=echoed, intent="propose", analytics=analytics,
                    blocked=(BlockedProposal(playbook_id="", title=None,
                                             code="NO_MONITORS_FOR_THIS_DEPLOYMENT",
                                             message=unavailable),),
                    notes=tuple(notes))
            intake_id = (payload.intake_id or "").strip()
            if not intake_id:
                notes.insert(0, console_note("needs_an_intake"))
                return ConsoleResponse(request=echoed, intent="propose", analytics=analytics,
                                       notes=tuple(notes))
            try:
                from .outbox_routes import actor_id

                proposed, blocked = propose_actions(
                    app, intake_id, principal=principal,
                    typed_address=payload.typed_address,
                    typed_by=actor_id(principal) if payload.typed_address else None)
            except Exception:                                        # noqa: BLE001
                log.exception("could not propose actions for %r", intake_id)
                return _refusal("PROPOSAL_UNAVAILABLE",
                                "the deployment's rules could not be evaluated for that "
                                "registration; see server logs. Nothing was drafted.",
                                status=503)
            return ConsoleResponse(request=echoed, intent="propose", proposed=tuple(proposed),
                                   blocked=tuple(blocked), analytics=analytics,
                                   notes=tuple(notes))

        # ANSWER.
        answered, refused, _status = _run_agent(app, request, parsed,
                                                principal_id=principal.principal_id)
        if answered is not None:
            notes.append(console_note("answer_is_a_list"))
        return ConsoleResponse(request=echoed, intent="answer", answer=answered,
                               analytics=analytics, notes=tuple(notes), refusal=refused)
