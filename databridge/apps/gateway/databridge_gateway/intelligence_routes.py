"""The ``/intelligence/*`` routes — the Business Intelligence surface (PHASE_7 T-42/T-43),
and the agent that reads it (`REMEDIATION.md` §3.1).

Three endpoints. Two of them compute figures; the third lets a member of staff ask a question in
English and get those same figures back as a list of values, each beside the pointer it came from
and each linking to the traversal behind it.

The figure surface exposes two things and refuses to mix them:

* **``graph``** — `databridge_graph.analytics.compute`: counts of *structural facts* about the
  clinic graph, each with the denominator it was counted over and the ids behind it. Until now
  `analytics.py` was 151 lines nothing called.
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
6. **Bounds refuse rather than truncate.** Every figure here is a count, so a count over part of a
   graph is a wrong number, not a short one (`intelligence_surface.check_graph_bounds`).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# Imported at module scope, not lazily, because both are `response_model` types: pydantic needs
# the real class when the model below is defined, and a string annotation resolved later would
# make the published schema depend on import order.
from databridge_agent.response import AgentResponse

from .outbox_routes import ActionCard, BlockedProposal, propose_actions

log = logging.getLogger("databridge.gateway.intelligence")

#: What a malformed `?as_of=` is answered with. The offending value is deliberately NOT quoted
#: back: it is caller-supplied text on a page that renders no uncited free text anywhere else, and
#: an error message is the classic place that rule gets forgotten. It goes to the log instead.
BAD_AS_OF_MESSAGE = ("the `as of` value is not an ISO-8601 datetime (for example 2026-08-01). "
                     "Refusing to guess which instant was meant — a rewound figure that quietly "
                     "stopped being rewound is a wrong number presented as a right one.")

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
REFUSAL_RESPONSES = {status: {"model": SurfaceRefusal} for status in (404, 422, 503)}


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


def _graph_analytics(app: FastAPI, as_of: Optional[dt.datetime]) -> tuple[Optional[dict],
                                                                         Optional[str]]:
    """(payload, unavailable_reason). Never raises — see the module docstring."""
    brain = getattr(app.state, "brain", None)
    if brain is None:
        return None, "the clinic graph is not loaded"
    try:
        from databridge_graph.analytics import compute

        from .intelligence_surface import (
            SurfaceNames,
            SurfaceTooLarge,
            check_graph_bounds,
            sanitise_analytics,
        )

        try:
            # Bounded BEFORE the traversal, not after. `compute` materialises the whole open node
            # and edge set; on a graph past this surface's envelope the honest answer is "these
            # figures are withheld", never a number somebody had to cut down to fit.
            check_graph_bounds(brain.store)
        except SurfaceTooLarge as exc:
            return None, exc.message
        names = SurfaceNames(brain.store)
        # Sanitised on the way out, always. `conflicts` counts REGISTRY ROWS, so its members are
        # patient node ids and `entities` carried their names — and the page rendered both, one
        # `labelOf()` away from a patient's name on a business-intelligence screen.
        return sanitise_analytics(compute(brain.store, as_of=as_of).to_dict(), names), None
    except Exception:                                                # noqa: BLE001
        log.exception("graph analytics unavailable; serving the page without them")
        return None, "the graph traversal failed; see server logs"


def _queue_summary(app: FastAPI) -> tuple[Optional[dict], Optional[str]]:
    """What the counter is waiting on, counted over the intakes actually held.

    Reads the same `app.state` the `/intake` queue reads, and the same
    `ConfirmationState.is_ready_to_commit()` the confirm gate reads, rather than re-deriving
    "blocked" from a copy of those rules — a second definition of blocked would drift from the
    gate, and the gate is the one that matters.
    """
    intakes = getattr(app.state, "intakes", None)
    if intakes is None:
        return None, "the intake queue is not loaded"
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

        blocked, committed = [], 0
        for intake_id, entry in held:
            draft = entry["draft"]
            state = states.get(intake_id) or ConfirmationState(intake_id=intake_id)
            if state.committed:
                committed += 1
                continue
            _, because = state.is_ready_to_commit()
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
            blocked.append({
                "intake_id": intake_id,
                "document_id": draft.record.document.document_id,
                "document_type": draft.record.document_type.value,
                # HOW MANY candidates the registry returned — never which. Hard constraint 1.
                "candidate_count": len(draft.resolution.candidates),
                "coverage_status": draft.coverage.status.value,
                "eligibility_flags": list(draft.coverage.eligibility_flags),
                "blocked_because": because,
                "identity_verified": verified,
            })
        blocked.sort(key=lambda b: b["intake_id"])
        return {
            "intakes": len(held),
            "committed": committed,
            "blocked": blocked,
            "blocked_count": len(blocked),
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

    ``typed_address``/``typed_by`` are door (b) of the recipient rule, passed straight through to
    ``recipients.recipient_typed_by_human``. They are read only by a clinic rule that declares that
    door; a rule declaring ``committed_ledger`` refuses a typed address outright
    (``build_outbound_draft``), so supplying one cannot redirect a message.
    """

    request: str = Field(default="")
    as_of: Optional[str] = None
    intake_id: Optional[str] = None
    typed_address: Optional[str] = None
    typed_by: Optional[str] = None


#: The console's own code-authored sentences, addressed by key — the same device
#: ``databridge_agent.render.NOTES`` uses, and for the same reason: a sentence a model could
#: phrase is a sentence a model could rephrase.
CONSOLE_NOTES: dict[str, str] = {
    "proposal_only": (
        "This is a draft. Nothing has been sent, and nothing here can send: the agent writes into "
        "an append-only outbox, a named member of staff approves, and a separate process with no "
        "model in it performs the send. An instruction hidden in a document can at worst cause a "
        "draft somebody reads."),
    "needs_an_intake": (
        "That reads as a request to do something rather than to look something up. Name the "
        "registration it concerns and the clinic's own rules will say what is worth sending about "
        "it — the system does not choose which patient a message is about, because choosing that "
        "is an identity determination."),
    "no_authentication": (
        "An approval records who says they approved it. There is no authentication behind that "
        "name yet: this is a POC and the sign-in screen says the same thing. Building it is the "
        "next thing that has to happen before this touches a real clinic."),
    "figures_on_request": (
        "Figures are shown because this request asked for them. They are counts of structural "
        "facts, each with the population it was counted over; none of them is a claim about time "
        "or money."),
    "answer_is_a_list": (
        "The answer is a list of values, not a paragraph. The model chooses which values appear "
        "and writes none of them: each line is printed by code beside the pointer that value "
        "itself carries."),
}


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
    return ConsoleNote(key=key, text=CONSOLE_NOTES[key])


#: Requests that are asking for figures. A closed, code-authored set of shapes — never a model
#: deciding what kind of request this was. The page's whole redesign rests on this: analytics are
#: one possible ANSWER, so something has to decide when they were asked for, and that something
#: must be as inspectable as the rest of the surface.
_FIGURE_REQUESTS = re.compile(
    r"\b(?:figures?|analytics|dashboard|breakdown|statistics|stats|how many|count|counts|"
    r"totals?|overview|mix|what(?:'s| is) (?:still )?(?:blocked|outstanding|waiting))\b", re.I)


def asks_for_figures(request: str) -> bool:
    return bool(_FIGURE_REQUESTS.search(request or ""))


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
    """A real model when a key is present, the deterministic planner otherwise. Never `None`.

    `build_extraction_harness` is reused rather than reimplemented: `agent_reasoning` routes to the
    `sonnet` alias, and that function's whole job is to remap that alias onto whichever configured
    provider actually holds a key. Its name says "extraction" because that was its first caller;
    what it does is "a Harness for the alias the pipeline uses", which is this one too. Writing a
    second copy here would mean two places to update when the provider story changes.
    """
    from .agent_gold import agent_gold_harness
    from .llm import build_extraction_harness, detect_llm

    detected = getattr(app.state, "llm_meta", None) or detect_llm()
    return build_extraction_harness(detected) or agent_gold_harness()


def _run_agent(app: FastAPI, question: str, parsed: Optional[dt.datetime]
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
    brain = getattr(app.state, "brain", None)
    if brain is None:
        return None, RefusalBody(
            code="GRAPH_UNAVAILABLE",
            message="the clinic graph is not loaded, so there is nothing to read and nothing to "
                    "cite"), 503

    from databridge_agent import AGENT_TASK_CLASS
    from databridge_core import resolve_model

    try:
        resolve_model(AGENT_TASK_CLASS)
    except Exception:                                                # noqa: BLE001
        log.exception("no model route for %r", AGENT_TASK_CLASS)
        return None, RefusalBody(
            code="AGENT_MODEL_NOT_CONFIGURED",
            message=f"no model is routed for '{AGENT_TASK_CLASS}' in config/models.yaml, so this "
                    f"question cannot be planned. Routing is configuration; adding the line is "
                    f"the fix."), 503

    from .intelligence_surface import SurfaceTooLarge, check_graph_bounds

    try:
        check_graph_bounds(brain.store)
    except SurfaceTooLarge as exc:
        # Loud, not truncated. Every value this agent reports is a count or a member of one, and a
        # count computed over part of a graph is a wrong number, not a partial one.
        return None, RefusalBody(code=exc.code, message=exc.message), 503

    try:
        from databridge_agent import ClinicAgent

        from .agent_tools import build_structural_registry

        registry = build_structural_registry(brain.store, as_of=parsed)
        answer = ClinicAgent(registry, _reasoning_harness(app)).ask(question)
    except Exception:                                                # noqa: BLE001
        log.exception("the clinic agent failed on %r", question[:120])
        return None, RefusalBody(
            code="AGENT_UNAVAILABLE",
            message="the agent could not complete this read; see server logs. No partial answer "
                    "is returned, because a partial answer is the one thing worse than none "
                    "here"), 503

    response = answer.public()
    if not response.refused and not response.values:
        # Refuse rather than render an empty answer. The wording is the agent's own code-authored
        # note, so the same sentence appears whichever path produced it.
        from databridge_agent.render import NOTHING_ANSWERED, note_text

        return None, RefusalBody(code="NO_GROUNDED_ANSWER",
                                 message=note_text(NOTHING_ANSWERED)), 422
    return response, None, 200


def _register_intelligence_routes(app: FastAPI) -> None:
    """Attach the BI surface. Read-only: nothing here writes to the graph or the ledger."""

    @app.get("/intelligence")
    def intelligence(as_of: Optional[str] = None):
        """Structural counts over the clinic graph, plus what the counter is waiting on.

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
        return {
            "as_of": parsed.isoformat() if parsed else None,
            "graph": graph,
            "graph_unavailable": graph_unavailable,
            "queue": queue,
            "queue_unavailable": queue_unavailable,
            # Said in the payload, not only in the UI copy, so an agent reading this over MCP in
            # Phase 8 inherits the distinction rather than having to be told it.
            "note": FIGURES_NOTE,
        }

    @app.get("/intelligence/subgraph/{entity_id:path}", responses=REFUSAL_RESPONSES)
    def intelligence_subgraph(entity_id: str, depth: int = 1, as_of: Optional[str] = None):
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
        brain = getattr(app.state, "brain", None)
        if brain is None:
            return _refusal("GRAPH_UNAVAILABLE", "the clinic graph is not loaded", status=503)
        try:
            parsed = _parse_as_of(as_of)
        except ValueError:
            log.info("rejected a malformed as_of on /intelligence/subgraph")
            return _refusal("BAD_AS_OF", BAD_AS_OF_MESSAGE)
        depth = max(1, min(depth, 3))                # a BI drill-down, not a graph dump

        from .intelligence_surface import (
            SurfaceNames,
            SurfaceTooLarge,
            check_graph_bounds,
            structural_neighbors,
            subgraph_sources,
        )

        try:
            check_graph_bounds(brain.store)
            names = SurfaceNames(brain.store)
            resolved = names.resolve(entity_id)
            if resolved is None:
                return _refusal("UNKNOWN_ENTITY",
                                "no entity on this surface matched that reference", status=404)
            out = structural_neighbors(brain.store, names, resolved, depth=depth, as_of=parsed)
        except SurfaceTooLarge as exc:
            return _refusal(exc.code, exc.message,
                            status=422 if exc.code == "NOT_ON_THIS_SURFACE" else 503)
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
            sources = subgraph_sources(brain.store, names, out)
        except Exception:                                            # noqa: BLE001
            log.exception("pointer lookup failed for a subgraph")
            return _refusal(
                "EVIDENCE_UNAVAILABLE",
                "the traversal succeeded but its citations could not be loaded, so the result is "
                "not checkable and is withheld", status=503)
        uncited = sorted(n["id"] for n in out.get("nodes", []) if not sources.get(n["id"]))
        if uncited:
            # `GraphNode` refuses construction without a pointer, so this should be unreachable —
            # which is exactly why listing the ids and returning the values anyway was wrong. If
            # it ever does happen, the invariant this whole page rests on has broken, and the
            # honest response is to say so rather than to render values under a caveat field.
            log.error("subgraph returned %d entities with no citations", len(uncited))
            return _refusal(
                "EVIDENCE_UNAVAILABLE",
                f"{len(uncited)} entity/entities in this traversal have no SourcePointer, so the "
                f"result is not checkable and is withheld", status=503)
        return {**out, "depth": depth, "as_of": parsed.isoformat() if parsed else None,
                "sources": sources}

    # -- the agent ------------------------------------------------------------------
    from databridge_agent import AgentResponse

    @app.post("/intelligence/ask", response_model=AgentResponse,
              response_model_exclude_none=True, responses=REFUSAL_RESPONSES)
    def intelligence_ask(payload: AskRequest):
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

        answered, refused, status = _run_agent(app, question, parsed)
        if refused is not None:
            return JSONResponse(status_code=status,
                                content=SurfaceRefusal(refusal=refused).model_dump(mode="json"))
        return answered

    # -- the console ----------------------------------------------------------------
    @app.post("/intelligence/console", response_model=ConsoleResponse,
              responses=REFUSAL_RESPONSES)
    def intelligence_console(payload: ConsoleRequest):
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
            analytics = {"as_of": parsed.isoformat() if parsed else None,
                         "graph": graph, "graph_unavailable": graph_unavailable,
                         "queue": queue, "queue_unavailable": queue_unavailable,
                         "note": FIGURES_NOTE}

        notes: list[ConsoleNote] = []
        if analytics is not None:
            notes.append(console_note("figures_on_request"))

        if gated is not None:
            # PROPOSE. The screen called this send-shaped, so it is a request to act.
            notes.extend([console_note("proposal_only"), console_note("no_authentication")])
            intake_id = (payload.intake_id or "").strip()
            if not intake_id:
                notes.insert(0, console_note("needs_an_intake"))
                return ConsoleResponse(request=echoed, intent="propose", analytics=analytics,
                                       notes=tuple(notes))
            try:
                proposed, blocked = propose_actions(app, intake_id,
                                                    typed_address=payload.typed_address,
                                                    typed_by=payload.typed_by)
            except Exception:                                        # noqa: BLE001
                log.exception("could not propose actions for %r", intake_id)
                return _refusal("PROPOSAL_UNAVAILABLE",
                                "the clinic's rules could not be evaluated for that "
                                "registration; see server logs. Nothing was drafted.",
                                status=503)
            return ConsoleResponse(request=echoed, intent="propose", proposed=tuple(proposed),
                                   blocked=tuple(blocked), analytics=analytics,
                                   notes=tuple(notes))

        # ANSWER.
        answered, refused, _status = _run_agent(app, request, parsed)
        if answered is not None:
            notes.append(console_note("answer_is_a_list"))
        return ConsoleResponse(request=echoed, intent="answer", answer=answered,
                               analytics=analytics, notes=tuple(notes), refusal=refused)
