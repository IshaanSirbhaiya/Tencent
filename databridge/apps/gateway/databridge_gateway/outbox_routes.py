"""`/outbox/*` — the HTTP surface over the send gate, and the one place execution is triggered.

``packages/outbox`` is the send gate: an append-only store of drafts, a recipient rule with no
third door, and a state machine in which nothing becomes ``sent`` without a named human's
``approved_by``. It had no HTTP surface, so nothing a person could click ever reached it. This is
that surface, and it adds no capability the package does not already have — every refusal below is
the package's own, surfaced with a code.

## Where execution runs, and what this route costs

``apps/dispatcher`` is a **separate, model-free process** on purpose. Its docstring and
``tests/test_dispatcher_is_model_free.py`` make the claim structural: no model is importable from
it, transitively, and the SMTP credentials live only there. If this module imported ``Dispatcher``
and ``SmtpTransport`` and called them in-process, the gateway — which holds a writable ledger, an
LLM harness and the agent's tool registry — would also hold mail egress, and the separation would
survive only as a directory layout.

So ``POST /outbox/{id}/execute`` **spawns the dispatcher as a subprocess** and reads the result
back out of the shared, file-backed outbox. Concretely:

    python apps/dispatcher/dispatcher.py --once --db <DATABRIDGE_OUTBOX_DB>

Nothing in this module imports a transport (``tests/test_outbox_routes.py`` asserts that against
this file's source, the same device ``test_outbox_cannot_send.py`` uses on the package), and the
gateway process never opens a socket to a mail server.

**What this still costs, stated plainly.** In the real deployment the dispatcher is a long-running
service that polls ``awaiting_dispatch()``; approval alone is the trigger and no HTTP route
"executes" anything. Here a route causes a process to start, which means:

* the gateway can start a process, and the argv it starts is code in this file — a smaller
  privilege than holding SMTP credentials, but not zero;
* a sweep sends **every** approved row, not only the one named in the URL, because a dispatcher is
  a queue worker and there is no per-row flag to give it. The route reports the state of the row
  you asked about and says so in ``note``;
* ``send`` then ``record_sent`` is still not atomic (``dispatcher.py`` says so at length); a crash
  between them leaves a delivered message on an ``approved`` row.

The honest description is that this button *stands in for* the dispatcher's own poll timer during a
demo. It does not merge the two processes, and the moment `DATABRIDGE_OUTBOX_DB` points at a store
a real dispatcher is already polling, this route becomes unnecessary rather than wrong.

## The outbox database is a FILE

``OutboxStore``'s default is ``:memory:`` and that default is correct for the package: an in-memory
store belongs to the process that created it, which is the process split telling you the truth. It
is exactly wrong here, because the reader is a different process. ``DATABRIDGE_OUTBOX_DB`` names
the file; :func:`outbox_db_path` defaults it under ``databridge/var/`` so a developer who sets
nothing still gets a store the dispatcher can see.

## What this surface may publish

`/intelligence`'s contract applies here unchanged (``intelligence_surface``): no patient names, no
scores, documents as handles. A draft is the one object in this system that carries a **patient's
mailbox**, which is a stronger identifier than a name, so:

* the address is **masked** (``a…@example.org``) and carries a stable ``handle`` so two drafts to
  the same person are visibly the same person without naming them. The full address is visible
  where the patient is at the counter — and in the mail log, which is where a send is checked;
* **how** the recipient was established is published in full, from a closed table of code-authored
  sentences keyed on ``recipient_origin``. That is the part an approver actually needs: not "who is
  alice@…" but "the address the clinic already held for the patient a named human selected in
  person";
* pointers are **projected onto a closed shape** (:func:`project_pointer`) rather than redacted.
  A ``derived`` pointer's ``derivation`` is free text this module did not author — the
  ledger-backed one names a patient id — so it is never echoed; what prints is the fact that a
  derivation exists and the citations under it.

The cost is the same trade ``intelligence_surface`` documents: an approver on this page cannot read
the mailbox. That is deliberate, and it is why ``/frontdesk`` exists.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import subprocess
import sys
from typing import Any, Literal, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("databridge.gateway.outbox")

#: Same name the dispatcher reads, deliberately: the two processes share one file or they share
#: nothing, and a second env var would let them disagree about which store is the store.
OUTBOX_DB_ENV = "DATABRIDGE_OUTBOX_DB"
DISPATCH_TIMEOUT_ENV = "DATABRIDGE_DISPATCH_TIMEOUT_SECONDS"
DEFAULT_DISPATCH_TIMEOUT = 30.0

#: Where the dispatcher subprocess needs to import from. Mirrors the root `pyproject`'s
#: `pythonpath` for exactly the three trees `dispatcher.py` touches — the store, the provenance
#: primitives it deserialises, and the transport beside it. Deliberately NOT the whole list: the
#: dispatcher must not become importable-adjacent to the agent, and a short path is a checkable
#: statement about what that process may reach.
DISPATCH_PYTHONPATH = ("packages/core", "packages/outbox", "apps/dispatcher")

#: How much of a draft's body this surface prints. Long enough that an approver reads the whole
#: message rather than approving an ellipsis; short enough that a pasted document is not rendered.
MAX_BODY_CHARS = 2_000
MAX_SUBJECT_CHARS = 240

#: Config files this repo authored and committed. A pointer into one of these resolves to a file a
#: human can open, so its identifiers print. Everything else is digested to a handle.
REPO_CONFIG_SOURCES = frozenset({"clinic_monitors.yaml", "coverage_rules.yaml"})

#: Ledger pointers name the intake, which this surface already publishes, and nothing else.
LEDGER_SOURCE_PREFIX = "evidence_ledger:"
FRONT_DESK_SOURCE_PREFIX = "front_desk_entry:"

#: WHY this address is allowed to be written to, in sentences this module authored. Keyed on the
#: row's structured ``recipient_origin``, never assembled from a pointer's free text.
RECIPIENT_ORIGIN_TEXT: dict[str, str] = {
    "committed_ledger": (
        "The address the clinic already held for the patient a named member of staff selected in "
        "person, on a committed evidence-ledger row. No part of that identity check was "
        "automatic, nothing chose between candidates, and this system never read an address off a "
        "document."),
    "typed_by_human": (
        "A named member of staff typed this address. The system did not take it from a document: "
        "an address printed on a referral letter is a claim about identity made by whoever wrote "
        "the letter, and acting on it would be an identity determination."),
}

#: The staff-facing sentence for each refusal this surface can give. Code-authored, so a refusal
#: cannot quote back an exception carrying a patient id or an address.
REFUSALS: dict[str, str] = {
    "UNKNOWN_DRAFT": (
        "no draft with that id is in the outbox. Nothing is created by asking about it."),
    "NOT_APPROVED": (
        "this draft has not been approved by a named member of staff, so it cannot be executed. "
        "Approving is the gate: the outbox refuses the state change, the dispatcher refuses the "
        "send, and this route refuses to start the dispatcher. Three layers, none of them "
        "removable from here."),
    "APPROVER_REQUIRED": (
        "an approval names a person. A process name, a blank, or 'system' would make the audit "
        "trail claim something that never happened — the record has to be able to say who let "
        "this message out."),
    "REASON_REQUIRED": (
        "a rejection says why. A refusal with no reason teaches the next draft nothing."),
    "ILLEGAL_TRANSITION": (
        "this draft is not in a state that permits that. The outbox is append-only and its state "
        "machine is one-way: a rejected draft is not un-rejected, and a sent message is not "
        "unsent by anything a database can do."),
    "DISPATCH_UNAVAILABLE": (
        "the dispatcher process could not be started, so nothing was attempted. The draft is "
        "untouched and still approved; see server logs."),
    "DISPATCH_FAILED": (
        "the dispatcher ran and this draft did not leave. It stays approved and will be retried — "
        "a mail server that was briefly down and one that is permanently misconfigured look "
        "identical from here, and neither is a reason to mark a message sent. See server logs."),
    "OUTBOX_UNAVAILABLE": (
        "the outbox store could not be opened, so no draft can be listed, approved or executed. "
        "See server logs."),
}


# --- refusals ---------------------------------------------------------------------------
class RefusalBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    nearest_alternative: Optional[str] = None


class SurfaceRefusal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    refusal: RefusalBody


REFUSAL_RESPONSES = {status: {"model": SurfaceRefusal} for status in (404, 422, 503)}


def _refusal(code: str, status: int = 422, *, message: Optional[str] = None) -> JSONResponse:
    """A refusal whose text comes from :data:`REFUSALS`, never from an exception.

    ``message`` is an override for the two callers that have a code-authored sentence of their own
    (the proposal path). An exception's ``str`` is logged and never serialised: ``recipients.py``
    writes excellent, detailed messages, and several of them name a patient id.
    """
    body = SurfaceRefusal(refusal=RefusalBody(
        code=code, message=message or REFUSALS.get(code, REFUSALS["OUTBOX_UNAVAILABLE"])))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


# --- where the store lives ----------------------------------------------------------------
def repo_root() -> pathlib.Path:
    """``databridge/`` — resolved from ``__file__``, never the cwd.

    uvicorn, pytest and the dispatcher subprocess all start in different directories; a relative
    default would give each of them a different outbox, which is the one failure mode that looks
    exactly like "the approval did not stick".
    """
    return pathlib.Path(__file__).resolve().parents[3]


def default_outbox_db() -> pathlib.Path:
    return repo_root() / "var" / "outbox.sqlite"


def outbox_db_path(app: Optional[FastAPI] = None) -> str:
    """The outbox file. **Never** ``:memory:``.

    Order: an explicit ``app.state.outbox_db_path`` (tests, and a future multi-tenant wiring),
    then ``$DATABRIDGE_OUTBOX_DB``, then the default under ``databridge/var/``. An in-memory store
    would be invisible to the dispatcher, which is a different process — that is the capability
    boundary, and defaulting around it would quietly delete it.
    """
    if app is not None:
        override = getattr(app.state, "outbox_db_path", None)
        if override:
            return str(override)
    from_env = (os.environ.get(OUTBOX_DB_ENV) or "").strip()
    # Pure: reads the configuration and creates nothing. `get_store` makes the directory when it
    # is about to open the file — a getter with a filesystem side effect means every test that
    # merely asks where the outbox *would* live leaves a directory behind in the repo.
    return str(pathlib.Path(from_env) if from_env else default_outbox_db())


def get_store(app: FastAPI):
    """The one ``OutboxStore`` this process holds, opened on first use and cached.

    Cached on ``app.state`` rather than at module scope so two apps in one test session (which is
    the normal shape of this suite) do not share a handle to whichever file the first one opened.
    """
    store = getattr(app.state, "outbox", None)
    if store is not None:
        return store
    from databridge_outbox.store import OutboxStore

    path = pathlib.Path(outbox_db_path(app))
    path.parent.mkdir(parents=True, exist_ok=True)
    store = OutboxStore(str(path))
    app.state.outbox = store
    return store


# --- what this surface may print ----------------------------------------------------------
def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:10]


def address_handle(address: str) -> str:
    """A stable, opaque handle for one mailbox. Two drafts to the same person share it."""
    return f"box-{_digest(address.strip().lower())}"


def mask_address(address: str) -> str:
    """``alice.tan@example.org`` → ``a…@example.org``.

    The local part is the identifying half and is reduced to its first character; the domain stays
    because "which organisation" is the part an approver reads (a patient's own provider, an
    insurer's claims inbox), and a fully opaque row would make approval meaningless rather than
    careful. A single-character or malformed local part masks to ``…`` rather than to itself.
    """
    address = (address or "").strip()
    local, sep, domain = address.partition("@")
    if not sep or not domain:
        return "…"
    head = local[:1] if len(local) > 1 else ""
    return f"{head}…@{domain}"


class Citation(BaseModel):
    """One pointer behind a draft field, projected onto a closed shape.

    A *projection*, never a redaction pass — the same argument ``intelligence_surface.project``
    makes: a field added to ``SourcePointer`` later cannot travel through here unnoticed, because
    nothing here copies a pointer wholesale.

    ``detail`` is filled only from identifiers this repo authored (a monitor id, a column name, an
    intake id). A ``derived`` pointer's ``derivation`` is free text assembled elsewhere — the
    recipient's names a patient id — so it is reported as *existing*, with its own children
    projected under it, and never quoted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    source: str
    #: True when ``source`` is a file in this repo that a human can open.
    repo_config: bool = False
    table: Optional[str] = None
    column: Optional[str] = None
    row: Optional[str] = None
    #: For ``doc_span``: the document HANDLE and the character range, never the filename.
    document: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    derived_from: tuple["Citation", ...] = ()


def _public_source(source_id: str) -> tuple[str, bool]:
    if source_id in REPO_CONFIG_SOURCES:
        return source_id, True
    for prefix in (LEDGER_SOURCE_PREFIX, FRONT_DESK_SOURCE_PREFIX):
        if source_id.startswith(prefix):
            # The intake id is already published by `/intelligence`'s queue block; the front-desk
            # prefix carries a STAFF name, which is a person this system is allowed to name.
            return source_id, False
    return f"src-{_digest(source_id)}", False


def project_pointer(pointer: Any) -> Citation:
    """One ``SourcePointer`` → the closed shape above. Default closed for unknown kinds."""
    source, repo_config = _public_source(pointer.source_id)
    if pointer.kind == "doc_span":
        from .intelligence_surface import document_handle

        return Citation(
            kind="doc_span", source=document_handle(pointer.source_id),
            document=document_handle(pointer.document_id or pointer.source_id),
            char_start=pointer.char_start, char_end=pointer.char_end)
    if pointer.kind == "table_cell":
        return Citation(
            kind="table_cell", source=source, repo_config=repo_config,
            table=pointer.table, column=pointer.column,
            # A row id in a repo config file is a monitor id or a rule id and prints; anywhere
            # else it addresses a row in a data source, and a registry row is a person.
            row=str(pointer.row_id) if repo_config else _digest(str(pointer.row_id)))
    if pointer.kind == "derived":
        return Citation(
            kind="derived", source=source, repo_config=repo_config,
            derived_from=tuple(project_pointer(p) for p in (pointer.derived_from or [])))
    return Citation(kind=pointer.kind, source=source, repo_config=repo_config)


def _present(value: str, limit: int) -> tuple[str, bool]:
    """Render draft text for staff. ``(text, was_redacted)``.

    Draft bodies are rendered from repo templates interpolated with values read out of a document,
    so the interpolated half is attacker-controlled text about to be shown in the clinic's own UI.
    It goes through the same two screens the agent's answers do: an instruction-shaped value is
    replaced by the fixed redaction line, and everything else is neutralised.
    """
    from databridge_agent.frame import untrusted
    from databridge_agent.guardrails import looks_like_injection
    from databridge_agent.render import REDACTED_VALUE

    if looks_like_injection(value or ""):
        return REDACTED_VALUE, True
    text = untrusted(value or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " […]"
    return text or "(empty)", False


class RecipientView(BaseModel):
    """Who a draft is addressed to, as much as this surface may say — and how that was decided."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin: str
    #: The named human whose act put this address here. A member of staff, never a patient.
    chosen_by: str
    masked: str
    handle: str
    #: A code-authored sentence from :data:`RECIPIENT_ORIGIN_TEXT`.
    established: str
    pointer: Citation


class ActionCard(BaseModel):
    """One proposed action: what it would do, to whom, why, and where it stands.

    The key set is pinned by ``test_outbox_routes.py``. A field added here is a field published to
    a browser, and that test fails until somebody writes down that it is safe to publish.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str
    status: str
    channel: str
    #: The monitor that produced it, and the row's own prose from ``clinic_monitors.yaml`` — repo
    #: config, so it prints. A draft nobody can attribute to a rule is a draft nobody can argue
    #: with.
    playbook_id: str
    title: Optional[str] = None
    when: Optional[str] = None
    escalate_to: Optional[str] = None
    intake_id: Optional[str] = None
    recipient: RecipientView
    subject: str
    body: str
    #: True when the subject or body was instruction-shaped and has been replaced. A card in this
    #: state is still approvable — that is the reviewer's call, and hiding the button would hide
    #: the finding — but it renders as withheld rather than as text somebody read.
    redacted: bool = False
    because: tuple[Citation, ...] = ()
    requires_human_approval: Literal[True] = True
    created_at: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[str] = None
    reason: Optional[str] = None
    sent_at: Optional[str] = None
    message_id: Optional[str] = None


class ExecutionResult(BaseModel):
    """What actually happened. A demo whose send is invisible proves nothing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str
    status: str
    message_id: Optional[str] = None
    sent_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    recipient_masked: str
    #: WHICH PROCESS did it. Named in the payload rather than only in a docstring, so a reader of
    #: the API can see that the gateway did not send this.
    executed_by: str
    note: str


class OutboxListing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    drafts: tuple[ActionCard, ...] = ()
    counts: dict[str, int] = Field(default_factory=dict)
    #: The store file, so "the dispatcher cannot see my approval" is answerable by looking.
    database: str
    note: str


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: str = ""


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rejected_by: str = ""
    reason: str = ""


LISTING_NOTE = (
    "Drafts, not messages. Nothing here has left the clinic and nothing here can send itself: a "
    "row becomes `sent` only after a named member of staff approves it and a separate, model-free "
    "process performs it. Addresses are masked on this screen — the mailbox is visible at the "
    "counter, where the patient is.")

EXECUTION_NOTE = (
    "Performed by apps/dispatcher, in its own process, with no model in it and no way back into "
    "this one. A sweep sends every row that is approved, not only this one, because a dispatcher "
    "is a queue worker; the state reported here is this row's.")


def _monitor_row(playbook_id: str):
    """The monitor this draft came from, or ``None`` if the config no longer has that row."""
    try:
        from databridge_outbox import clinic_monitors

        return clinic_monitors().by_id(playbook_id)
    except Exception:                                                # noqa: BLE001
        # A draft outlives an edit to the config table, and an old draft is still a real record of
        # something a human may have approved. Losing the prose is survivable; refusing to render
        # the row is not.
        log.info("no monitor row for playbook %r; rendering the draft without its prose",
                 playbook_id)
        return None


def action_card(entry: Any) -> ActionCard:
    """One ``OutboxEntry`` → the card a browser gets."""
    monitor = _monitor_row(entry.playbook_id)
    subject, subject_redacted = _present(entry.subject, MAX_SUBJECT_CHARS)
    body, body_redacted = _present(entry.body, MAX_BODY_CHARS)
    return ActionCard(
        draft_id=entry.draft_id,
        status=entry.status,
        channel=entry.channel,
        playbook_id=entry.playbook_id,
        title=getattr(monitor, "title", None),
        when=getattr(monitor, "when", None),
        escalate_to=getattr(monitor, "escalate_to", None),
        intake_id=entry.intake_id,
        recipient=RecipientView(
            origin=entry.recipient_origin,
            chosen_by=entry.recipient_chosen_by,
            masked=mask_address(entry.recipient_address),
            handle=address_handle(entry.recipient_address),
            established=RECIPIENT_ORIGIN_TEXT.get(
                entry.recipient_origin,
                "This address came through a door this surface does not recognise, so nothing is "
                "claimed about how it was established."),
            pointer=project_pointer(entry.recipient_pointer)),
        subject=subject,
        body=body,
        redacted=subject_redacted or body_redacted,
        because=tuple(project_pointer(p) for p in entry.because),
        created_at=entry.created_at,
        approved_by=entry.approved_by,
        approved_at=entry.approved_at,
        rejected_by=entry.rejected_by,
        rejected_at=entry.rejected_at,
        reason=entry.reason,
        sent_at=entry.sent_at,
        message_id=entry.message_id)


# --- execution: a different process, started from here ------------------------------------
def dispatcher_script() -> pathlib.Path:
    return repo_root() / "apps" / "dispatcher" / "dispatcher.py"


def dispatch_argv(db_path: str) -> list[str]:
    """The exact command this route runs. A function so a test can assert it without running it."""
    return [sys.executable, str(dispatcher_script()), "--once", "--db", db_path]


def run_dispatcher_once(db_path: str, *, timeout: Optional[float] = None) -> int:
    """Start ``apps/dispatcher`` for one sweep and wait for it. Returns its exit code.

    **Nothing about the send happens in this process.** No transport is imported here, no SMTP
    credential is read here, and the only thing crossing back is an exit code — the receipts the
    dispatcher prints go to this process's log and never to a client, because
    ``SendReceipt.describe()`` contains the recipient address.
    """
    if timeout is None:
        timeout = float(os.environ.get(DISPATCH_TIMEOUT_ENV) or DEFAULT_DISPATCH_TIMEOUT)
    env = os.environ.copy()
    paths = [str(repo_root() / p) for p in DISPATCH_PYTHONPATH]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env[OUTBOX_DB_ENV] = db_path
    proc = subprocess.run(dispatch_argv(db_path), capture_output=True, text=True,
                          timeout=timeout, env=env, cwd=str(repo_root()), check=False)
    # Logged, never returned. A receipt names the mailbox it went to.
    log.info("dispatcher exited %s\n%s\n%s", proc.returncode,
             (proc.stdout or "").strip(), (proc.stderr or "").strip())
    return int(proc.returncode)


def _executor(app: FastAPI):
    """The callable that performs a sweep. Overridable on ``app.state`` for tests only.

    A test that really shelled out would need a mail server, and a test that mocked ``smtplib``
    would be asserting against a mock of the one thing that matters. The override takes the same
    argument the real one does — a database path — so a fake runs the *actual* ``Dispatcher`` over
    the *actual* file with a recording transport, which is the closest a test gets to the truth.
    """
    return getattr(app.state, "outbox_executor", None) or run_dispatcher_once


# --- proposing: the composition root between an intake and a monitor ----------------------
#
# `Observation` says it in its own docstring: *assembled by the composition root from committed
# rows, the intake queue and the config table — never by a model.* This is that composition root.
# Every fact below is a `GroundedValue` that already existed on the intake draft, or a `derived`
# pointer citing a row in `coverage_rules.yaml`. Nothing is computed into existence to make a
# monitor fire, which is the temptation here and the one that would matter.

#: Why a monitor that is due produced no draft. Code-authored, keyed, and never an exception's
#: text: ``recipients.py``'s messages are excellent and several of them name a patient id.
PROPOSAL_BLOCKED: dict[str, str] = {
    "UNKNOWN_INTAKE": (
        "no intake with that id is in front of the counter. A proposal is about a registration a "
        "member of staff named; nothing here searches for one."),
    "NOTHING_DUE": (
        "no clinic rule applies to this registration right now. The rules live in "
        "clinic_monitors.yaml and each one names the condition it fires on — a rule that does not "
        "hold is not a message worth sending."),
    "NO_COMMITTED_SELECTION": (
        "nothing has been committed for this registration, so there is nobody this message is "
        "established to be about. A named member of staff checks the identification document "
        "against the person, in person, and confirms; until then there is no address, because "
        "there is no established recipient. Nothing is drafted to nobody."),
    "CONFLICTING_SELECTIONS": (
        "the committed evidence for this registration names more than one selected patient. No "
        "message is addressed until a human establishes which record this encounter belongs to; "
        "the system does not choose between them, and picking the most recent one would be "
        "choosing who somebody is."),
    "NO_ADDRESS_ON_FILE": (
        "the patient a member of staff selected has no address on file. Somebody at the counter "
        "can ask for one and type it, and the record will then say who entered it — nothing here "
        "guesses one from a name or reads one off a document."),
    "ADDRESS_MUST_BE_TYPED": (
        "this clinic rule addresses its message to an address a member of staff types, because "
        "the situation it fires on is usually one where identity is not yet established. Type an "
        "address and name yourself; until then there is no draft."),
    "NOT_GROUNDED": (
        "the message could not be built from grounded values alone. A sentence a patient reads is "
        "held to the same rule as an extracted field: if it is not in a source, it is not "
        "emitted."),
    "RECIPIENT_REFUSED": (
        "the address for this rule could not be established through either of the two permitted "
        "doors. Nothing is drafted; see server logs."),
}

#: The two config sources a proposal's facts may cite, as pointers. Same file id ``claim.py`` and
#: ``clinic_seed`` use, so a fact's provenance and the graph's agree about what to call it.
RULES_SOURCE_ID = "coverage_rules.yaml"
RULES_TABLE = "rules"


class BlockedProposal(BaseModel):
    """A rule that applies but produced no draft, and the code-authored reason.

    Surfaced rather than swallowed. "The system decided not to tell you why it did nothing" is the
    failure mode a review queue dies of, and every one of these reasons is a thing a member of
    staff can act on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    playbook_id: str
    title: Optional[str] = None
    code: str
    message: str


def _blocked(playbook_id: str, code: str, monitor: Any = None) -> BlockedProposal:
    return BlockedProposal(playbook_id=playbook_id, title=getattr(monitor, "title", None),
                           code=code, message=PROPOSAL_BLOCKED[code])


def observe_intake(draft: Any) -> Any:
    """What is grounded-true about one intake, as an ``Observation`` the monitors read.

    **Only facts that already carry a pointer.** Three of them today:

    * ``unauthorised_test`` — a test the document orders that the confirmed package does not
      include, carrying the ``doc_span`` the extractor recorded for the printed line;
    * ``package_code`` — the ``GroundedValue`` the extractor read off the authorisation;
    * ``questionnaire_name`` / ``questionnaire_outstanding`` — the questionnaire the matched rule
      in ``coverage_rules.yaml`` requires, and whether the prefill leaves fields only the patient
      can answer. The pointer cites the rule row; the derivation says what "outstanding" means
      here, because this clinic has no questionnaire-return channel and the fact must not imply
      one.

    **What is deliberately absent: ``minutes_blocked``.** ``intake_blocked_too_long`` needs it and
    this function does not produce it, so that monitor never fires from this surface. Two reasons,
    and both are the kind that should be written down rather than worked around: nothing in the
    intake carries a *grounded* elapsed time (``received_at`` is a field on a ``DocumentRef``, not
    a ``GroundedValue``, and the clock is not a source), and this page's contract is structural
    counts with no minutes in them. Inventing the fact would put a fabricated number into a
    sentence a patient reads.
    """
    from databridge_core import GroundedValue, SourcePointer
    from databridge_intake.models import PrefillStatus, QuestionnaireKind
    from databridge_outbox.monitors import Observation

    facts: dict[str, Any] = {}

    for test in draft.coverage.authorised_tests:
        # `in_package is False` is the coverage engine's own determination, and `pointer` is the
        # line of the letter that ordered it. A test with no pointer came from a package
        # definition rather than from the document, so there is no line to quote at a patient.
        if not test.in_package and test.pointer is not None and test.as_printed:
            facts["unauthorised_test"] = GroundedValue(value=test.as_printed,
                                                       pointer=test.pointer)
            break

    package_code = draft.record.authorisation.package_code
    if package_code is not None and str(package_code.value or "").strip():
        facts["package_code"] = package_code

    kind = draft.coverage.questionnaire
    rule_id = draft.coverage.matched_rule_id
    if kind is not QuestionnaireKind.NONE and rule_id:
        rule_pointer = SourcePointer(source_id=RULES_SOURCE_ID, kind="table_cell",
                                     table=RULES_TABLE, column="questionnaire", row_id=rule_id)
        facts["questionnaire_name"] = GroundedValue(
            value=str(kind.value).replace("_", " "), pointer=rule_pointer)
        outstanding = any(f.status is PrefillStatus.PATIENT_MUST_ANSWER
                          for f in draft.questionnaire.fields)
        if outstanding:
            facts["questionnaire_outstanding"] = GroundedValue(
                value=True,
                pointer=SourcePointer(
                    source_id=RULES_SOURCE_ID, kind="derived",
                    derivation=(
                        "the questionnaire this rule requires still has fields only the patient "
                        "can answer, and this clinic has no channel through which a completed "
                        "questionnaire arrives — so 'outstanding' means 'not yet answered here', "
                        "not 'we asked and were ignored'"),
                    derived_from=[rule_pointer]))

    return Observation(intake_id=draft.intake_id, facts=facts)


def _recipient_for(monitor: Any, intake_id: str, app: FastAPI, *,
                   typed_address: Optional[str], typed_by: Optional[str]) -> tuple[Any, str]:
    """``(recipient, "")`` or ``(None, code)``. The monitor's config decides which door."""
    from databridge_outbox import recipients as doors
    from databridge_outbox.models import RecipientRefused

    if monitor.recipient_from == "typed_by_human":
        if not (typed_address or "").strip() or not (typed_by or "").strip():
            return None, "ADDRESS_MUST_BE_TYPED"
        try:
            return doors.recipient_typed_by_human(typed_address.strip(),
                                                  typed_by=typed_by.strip()), ""
        except RecipientRefused:
            log.info("a typed address was refused for intake %r", intake_id)
            return None, "RECIPIENT_REFUSED"

    ledger = getattr(app.state, "ledger", None)
    if ledger is None:
        return None, "NO_COMMITTED_SELECTION"
    try:
        return doors.recipient_from_committed_ledger(intake_id, ledger), ""
    except doors.ConflictingSelections:
        return None, "CONFLICTING_SELECTIONS"
    except doors.NoAddressOnFile:
        return None, "NO_ADDRESS_ON_FILE"
    except doors.NoCommittedSelection:
        return None, "NO_COMMITTED_SELECTION"
    except RecipientRefused:
        log.exception("a recipient was refused for intake %r", intake_id)
        return None, "RECIPIENT_REFUSED"


def propose_actions(app: FastAPI, intake_id: str, *, typed_address: Optional[str] = None,
                    typed_by: Optional[str] = None
                    ) -> tuple[list[ActionCard], list[BlockedProposal]]:
    """Every draft the clinic's rules call for on one registration. **Drafts only.**

    ``intake_id`` is named by a member of staff; nothing here searches for an intake, ranks them,
    or picks one. The monitors decide *what* is worth saying, ``recipients.py`` decides *who* it
    may be said to, and this function is only the wiring between them — which is why it has no
    parameter through which a recipient could be chosen except the typed-address door, whose whole
    point is that a human is the one choosing.
    """
    from databridge_outbox import clinic_monitors
    from databridge_outbox.drafting import build_outbound_draft
    from databridge_outbox.models import DraftRefused
    from databridge_outbox.store import DuplicateDraft

    held = (getattr(app.state, "intakes", None) or {}).get(intake_id)
    if held is None:
        return [], [BlockedProposal(playbook_id="", title=None, code="UNKNOWN_INTAKE",
                                    message=PROPOSAL_BLOCKED["UNKNOWN_INTAKE"])]

    observation = observe_intake(held["draft"])
    due = clinic_monitors().due(observation)
    if not due:
        return [], [BlockedProposal(playbook_id="", title=None, code="NOTHING_DUE",
                                    message=PROPOSAL_BLOCKED["NOTHING_DUE"])]

    store = get_store(app)
    cards: list[ActionCard] = []
    blocked: list[BlockedProposal] = []
    for monitor in due:
        recipient, code = _recipient_for(monitor, intake_id, app,
                                         typed_address=typed_address, typed_by=typed_by)
        if recipient is None:
            blocked.append(_blocked(monitor.id, code, monitor))
            continue
        try:
            draft = build_outbound_draft(monitor, observation, recipient=recipient)
        except DraftRefused:
            log.info("monitor %r could not be drafted for intake %r", monitor.id, intake_id)
            blocked.append(_blocked(monitor.id, "NOT_GROUNDED", monitor))
            continue
        try:
            entry = store.record_draft(draft)
        except DuplicateDraft:
            # The id is a digest of the monitor, the intake, the address and the text, so an
            # identical draft IS the same draft. Re-asking shows the row that already exists —
            # including one a human already approved — rather than filing a second one to reject.
            entry = store.current(draft.draft_id)
            if entry is None:                                        # pragma: no cover - tripwire
                blocked.append(_blocked(monitor.id, "NOT_GROUNDED", monitor))
                continue
        cards.append(action_card(entry))
    return cards, blocked


class ProposeRequest(BaseModel):
    """What a member of staff sends to ask for drafts.

    ``intake_id`` is required and is the whole of the addressing decision this request can make:
    it names a registration, and the clinic's own rules decide the rest. There is deliberately no
    ``to``, no ``patient_id``, and no ``monitor_id`` — a caller choosing which rule fires is a
    caller choosing which patient hears about what.
    """

    model_config = ConfigDict(extra="forbid")

    intake_id: str = ""
    #: Door (b). Only read by a rule that declares ``recipient_from: typed_by_human``.
    typed_address: Optional[str] = None
    typed_by: Optional[str] = None


class ProposalResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intake_id: str
    proposed: tuple[ActionCard, ...] = ()
    blocked: tuple[BlockedProposal, ...] = ()
    note: str = LISTING_NOTE


# --- the routes ---------------------------------------------------------------------------
def register_outbox_routes(app: FastAPI) -> None:
    """Attach `/outbox/*`. Adds no capability the outbox package does not already have."""

    def _store():
        return get_store(app)

    @app.get("/outbox", response_model=OutboxListing, response_model_exclude_none=False)
    def outbox_list():
        """Every draft the clinic holds, newest state first, with the evidence behind each field."""
        try:
            entries = [e for e in (_store().current(d) for d in
                                   sorted({r.draft_id for r in _store().entries()}))
                       if e is not None]
        except Exception:                                            # noqa: BLE001
            log.exception("could not read the outbox")
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        cards = [action_card(e) for e in entries]
        counts: dict[str, int] = {}
        for card in cards:
            counts[card.status] = counts.get(card.status, 0) + 1
        return OutboxListing(drafts=tuple(cards), counts=counts,
                             database=outbox_db_path(app), note=LISTING_NOTE)

    @app.post("/outbox/propose", response_model=ProposalResponse, responses=REFUSAL_RESPONSES)
    def outbox_propose(payload: ProposeRequest):
        """Ask the clinic's own rules what is worth saying about one registration. **Drafts only.**

        This route cannot send and has no path to anything that can: it writes rows into the
        outbox, and a row is inert until a named human approves it and a separate process performs
        it. That is the whole safety argument — an instruction injected into a chit letter can at
        worst cause a draft somebody reads.
        """
        intake_id = (payload.intake_id or "").strip()
        if not intake_id:
            return _refusal("UNKNOWN_INTAKE", status=404,
                            message=PROPOSAL_BLOCKED["UNKNOWN_INTAKE"])
        try:
            cards, blocked = propose_actions(app, intake_id,
                                             typed_address=payload.typed_address,
                                             typed_by=payload.typed_by)
        except Exception:                                            # noqa: BLE001
            log.exception("could not propose actions for %r", intake_id)
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        return ProposalResponse(intake_id=intake_id, proposed=tuple(cards),
                                blocked=tuple(blocked))

    @app.get("/outbox/{draft_id}", response_model=ActionCard, responses=REFUSAL_RESPONSES)
    def outbox_draft(draft_id: str):
        """One draft: what it would do, to whom, why, and what stands behind each field."""
        try:
            entry = _store().current(draft_id)
        except Exception:                                            # noqa: BLE001
            log.exception("could not read draft %r", draft_id)
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        if entry is None:
            return _refusal("UNKNOWN_DRAFT", status=404)
        return action_card(entry)

    @app.post("/outbox/{draft_id}/approve", response_model=ActionCard,
              responses=REFUSAL_RESPONSES)
    def outbox_approve(draft_id: str, payload: ApproveRequest):
        """A named member of staff agrees this may go out. **The gate.**

        The name is checked by ``OutboxStore.approve`` → ``refuse_machine_actor``, not here: one
        definition of "is that a person", in the module that writes the row. This route only maps
        its refusal onto a code.

        There is no authentication behind the name — the sign-in screen says so and so does the
        UI copy on this page. An approval records *who says they did this*; it does not prove one.
        """
        from databridge_outbox.models import NotApproved
        from databridge_outbox.store import IllegalTransition, UnknownDraft

        try:
            entry = _store().approve(draft_id, approved_by=payload.approved_by)
        except NotApproved:
            return _refusal("APPROVER_REQUIRED")
        except UnknownDraft:
            return _refusal("UNKNOWN_DRAFT", status=404)
        except IllegalTransition:
            return _refusal("ILLEGAL_TRANSITION")
        except Exception:                                            # noqa: BLE001
            log.exception("approve failed for %r", draft_id)
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        return action_card(entry)

    @app.post("/outbox/{draft_id}/reject", response_model=ActionCard, responses=REFUSAL_RESPONSES)
    def outbox_reject(draft_id: str, payload: RejectRequest):
        """A named member of staff says no, and says why. The draft stays in the record."""
        from databridge_outbox.models import NotApproved, OutboxError
        from databridge_outbox.store import IllegalTransition, UnknownDraft

        if not (payload.reason or "").strip():
            return _refusal("REASON_REQUIRED")
        try:
            entry = _store().reject(draft_id, rejected_by=payload.rejected_by,
                                    reason=payload.reason)
        except NotApproved:
            return _refusal("APPROVER_REQUIRED")
        except UnknownDraft:
            return _refusal("UNKNOWN_DRAFT", status=404)
        except IllegalTransition:
            return _refusal("ILLEGAL_TRANSITION")
        except OutboxError:
            return _refusal("REASON_REQUIRED")
        except Exception:                                            # noqa: BLE001
            log.exception("reject failed for %r", draft_id)
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        return action_card(entry)

    @app.post("/outbox/{draft_id}/execute", responses=REFUSAL_RESPONSES)
    def outbox_execute(draft_id: str):
        """Trigger the dispatcher, then report what actually happened.

        Refuses an unapproved row **before** starting anything, so the failure is a clean 422
        rather than a process that starts and finds nothing to do. That check is not the guarantee
        — ``Dispatcher.refuse_unless_approved`` and ``OutboxStore.record_sent`` both make it
        again, in the process that would do the damage and in the store that would record it — it
        is the one that makes the refusal legible here.
        """
        try:
            entry = _store().current(draft_id)
        except Exception:                                            # noqa: BLE001
            log.exception("could not read draft %r", draft_id)
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        if entry is None:
            return _refusal("UNKNOWN_DRAFT", status=404)
        if entry.status == "sent":
            # Idempotent. A second click is not a second send: the store's `ix_outbox_sent_once`
            # would refuse the row anyway, and reporting the existing result is the honest answer.
            return {"draft": action_card(entry).model_dump(mode="json"),
                    "result": _result(entry).model_dump(mode="json")}
        if not entry.is_dispatchable:
            return _refusal("NOT_APPROVED")

        db_path = outbox_db_path(app)
        try:
            code = _executor(app)(db_path)
        except Exception:                                            # noqa: BLE001
            log.exception("could not run the dispatcher for %r", draft_id)
            return _refusal("DISPATCH_UNAVAILABLE", status=503)

        try:
            after = _store().current(draft_id)
        except Exception:                                            # noqa: BLE001
            log.exception("could not re-read draft %r after dispatch", draft_id)
            return _refusal("OUTBOX_UNAVAILABLE", status=503)
        if after is None or after.status != "sent":
            log.error("dispatcher exited %s and %r is still %s", code, draft_id,
                      None if after is None else after.status)
            return _refusal("DISPATCH_FAILED", status=503)
        return {"draft": action_card(after).model_dump(mode="json"),
                "result": _result(after).model_dump(mode="json")}

    def _result(entry: Any) -> ExecutionResult:
        return ExecutionResult(
            draft_id=entry.draft_id, status=entry.status, message_id=entry.message_id,
            sent_at=entry.sent_at, approved_by=entry.approved_by, approved_at=entry.approved_at,
            recipient_masked=mask_address(entry.recipient_address),
            executed_by=entry.recorded_by, note=EXECUTION_NOTE)


__all__ = [
    "ActionCard",
    "ApproveRequest",
    "BlockedProposal",
    "Citation",
    "PROPOSAL_BLOCKED",
    "ProposalResponse",
    "ProposeRequest",
    "observe_intake",
    "propose_actions",
    "DISPATCH_PYTHONPATH",
    "EXECUTION_NOTE",
    "ExecutionResult",
    "LISTING_NOTE",
    "OUTBOX_DB_ENV",
    "OutboxListing",
    "RECIPIENT_ORIGIN_TEXT",
    "REFUSALS",
    "RecipientView",
    "RejectRequest",
    "action_card",
    "address_handle",
    "default_outbox_db",
    "dispatch_argv",
    "dispatcher_script",
    "get_store",
    "mask_address",
    "outbox_db_path",
    "project_pointer",
    "register_outbox_routes",
    "repo_root",
    "run_dispatcher_once",
]
