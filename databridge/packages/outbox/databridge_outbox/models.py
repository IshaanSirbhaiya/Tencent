"""What an outbound message is, before anybody has agreed to send it.

The design this implements is `docs/hackathon/GRAPH_AGENT_AND_MCP.md` §4.3. Its one-line summary —
"``because`` is the load-bearing field" — is true, and there is a second field that is load-bearing
in a harder way, so it is stated here in full:

**THE RECIPIENT RULE.** A draft's recipient may come from exactly two places:

    (a) a COMMITTED evidence-ledger row, whose ``selected_patient_id`` a named human attested to
        in person, resolved to that patient's registered address; or
    (b) a human typing it.

There is no third. Deciding which person a message is about is an identity determination, and this
system does not make identity determinations (CLAUDE.md §Hard constraints 1). A model that reads an
attacker-controlled chit letter must not be able to influence *who* hears about it — not directly,
not by "picking between two candidates the graph already returned".

``Recipient`` is where that rule is a type rather than a review comment:

* it cannot be constructed except through ``recipients.py``'s two factories — every other path,
  including ``model_validate`` on a dict that arrived over HTTP or out of a model's JSON, is
  refused by a ``mode="before"`` validator that demands a module-private token;
* it carries a ``SourcePointer`` and an ``origin``, and the two must agree: a ``committed_ledger``
  recipient must cite the ledger, a ``typed_by_human`` one must cite the front-desk entry that
  recorded the typing;
* ``chosen_by`` must be a named person. ``"agent"``, ``"system"``, ``"walnut"`` and their friends
  are refused, the same device ``PatientReadGrant.attested_by`` uses.

**Honest scope, as everywhere else in this repo.** This is Python. In-process code can reach the
token, or call ``model_construct``. What the type buys is that the ordinary path — and every path a
*model* has, which is a JSON object parsed into a schema — cannot produce one. The structural
guarantee that matters is in ``drafting.build_outbound_draft``: it has **no parameter through which
an address can arrive at all**, which is the same shape as ``intake.claim.build_claim_draft`` and is
pinned by a signature test.

Everything else here is the ordinary discipline: ``subject`` and ``body`` are ``GroundedValue``s, so
a sentence with no pointer is unconstructible rather than merely discouraged, and every string that
becomes an SMTP header is screened for CR/LF before it can carry a second header with it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from databridge_core import GroundedValue, SourcePointer

# One channel today. A Literal rather than a str so adding Slack is a schema change somebody sees,
# not a string that appears in a config file one afternoon.
Channel = Literal["email"]

#: ``drafted`` -> ``approved``/``rejected`` -> ``sent``. The store enforces the arrows; this names
#: the states. There is no ``sending``: a status that exists only inside one process's memory is
#: not a state the record can tell you about afterwards.
DraftStatus = Literal["drafted", "approved", "rejected", "sent"]

DRAFTED: DraftStatus = "drafted"
APPROVED: DraftStatus = "approved"
REJECTED: DraftStatus = "rejected"
SENT: DraftStatus = "sent"

#: Which of the two permitted doors a recipient came through. See the module docstring.
RecipientOrigin = Literal["committed_ledger", "typed_by_human"]

#: The source-id prefixes the two doors stamp on their pointers, so ``origin`` and ``pointer``
#: cannot disagree about where an address came from.
LEDGER_SOURCE_PREFIX = "evidence_ledger:"
FRONT_DESK_SOURCE_PREFIX = "front_desk_entry:"

#: The table a human-typed address is recorded against. Self-referential to this store, exactly as
#: ``claim.py``'s ``selected_patient_id`` pointer is self-referential to the ledger: the record that
#: a named person typed this address at this time IS the provenance, and there is nowhere more
#: authoritative to point.
FRONT_DESK_TABLE = "outbox_recipient_entry"


class OutboxError(RuntimeError):
    """Something about an outbound message was refused. Subclasses map to stable causes."""


class RecipientRefused(OutboxError):
    """An address did not come from one of the two permitted places, or is not addressable."""


class DraftRefused(OutboxError):
    """A draft could not be built: an ungrounded value, or a template nothing could fill."""


class NotApproved(OutboxError):
    """Something tried to move a draft outward without a named human's approval behind it."""


# --- who may be named as having done something ------------------------------------
#
# Copied deliberately from ``databridge_agent.sealed._NOT_A_HUMAN`` rather than imported: this
# package must not depend on the agent (that dependency is the one the whole design is arranged to
# avoid), and a duplicated safety list is better than an import that undoes the separation it
# protects. ``tests/test_recipient_rule.py`` asserts the two still agree.
NOT_A_HUMAN = frozenset({
    "system", "agent", "the agent", "copilot", "model", "llm", "auto", "automatic", "machine",
    "walnut", "databridge", "none", "null", "unknown", "n/a", "bot", "assistant", "ai",
    "dispatcher", "monitor", "playbook", "",
})

# CR, LF and NUL. An address or subject carrying one of these can append a second SMTP header —
# ``Bcc:`` being the interesting one — so they are refused at the type, before any transport sees
# them. The transport screens again; two checks, because this one runs in every test and the other
# one only runs when something is actually being sent.
HEADER_INJECTION = re.compile(r"[\r\n\x00]")

# Deliberately strict, and deliberately SINGLE: no commas, no semicolons, no angle brackets, no
# display name. One draft addresses one person. A recipient list is a place to hide an extra
# addressee, and "who else was on that email" is not a question a clinic should have to ask.
ADDRESS = re.compile(
    "^[^@\\s,;:<>\"'\\\\]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\\.[A-Za-z]{2,}$")


def refuse_machine_actor(who: str, *, what: str) -> str:
    """Return ``who`` stripped, or raise if it is not the name of a person.

    Used for ``chosen_by``, ``approved_by`` and ``rejected_by``. A process name in any of those
    fields turns "a human approved this" into a sentence the audit trail cannot support.
    """
    name = (who or "").strip()
    if not name or name.lower() in NOT_A_HUMAN:
        raise NotApproved(
            f"{what} REFUSED: {who!r} is not a named person. Approving an outbound message is an "
            f"act by a member of staff; a process name here would make the audit trail claim "
            f"something that never happened (CLAUDE.md §Hard constraints 4).")
    if HEADER_INJECTION.search(name):
        raise NotApproved(f"{what} REFUSED: {who!r} contains a line break")
    return name


# --- the recipient ------------------------------------------------------------------

#: The private capability that makes ``recipients.py`` the only door. A module-level object rather
#: than a string or a flag: a flag is guessable from a schema, and a model's channel into this
#: process is a JSON object.
_RECIPIENT_TOKEN = object()


class Recipient(BaseModel):
    """Who a draft is addressed to, and which of the two permitted doors it came through.

    Not constructible directly — see the module docstring and ``recipients.py``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str
    origin: RecipientOrigin
    #: The named human whose act put this address here: the staff member who attested to the
    #: candidate selection (``committed_ledger``), or the one who typed it (``typed_by_human``).
    chosen_by: str
    #: Where the address is written down. For ``committed_ledger`` a ``derived`` pointer citing
    #: BOTH the ledger row that records the human's selection AND the registry cell the address was
    #: read from — because the human attested to *who this is*, the registry is what says *how to
    #: reach them*, and a pointer that conflated the two would imply an identity check the system
    #: never performed.
    pointer: SourcePointer

    @model_validator(mode="before")
    @classmethod
    def _only_the_two_doors(cls, data: Any) -> Any:
        """Refuse construction unless ``recipients.py`` is doing it.

        ``mode="before"`` on purpose: it runs for ``Recipient(...)``, for
        ``Recipient.model_validate(...)`` and for a nested field parsed out of JSON, which is every
        path a value arriving from outside this process can take. A model's output is such a path.
        So is a request body.
        """
        if not isinstance(data, dict):
            raise RecipientRefused(
                "Recipient REFUSED: it is built by databridge_outbox.recipients, from a committed "
                "ledger row or from a human typing an address — never parsed from arbitrary data")
        if data.pop("via_", None) is not _RECIPIENT_TOKEN:
            raise RecipientRefused(
                "Recipient REFUSED: an address may come from exactly two places — a committed "
                "evidence-ledger row a named human attested to, or a human typing it "
                "(recipients.recipient_from_committed_ledger / recipient_typed_by_human). "
                "Choosing who a message is about is an identity determination, and this system "
                "does not make one (CLAUDE.md §Hard constraints 1).")
        return data

    @model_validator(mode="after")
    def _addressable_and_attributable(self) -> "Recipient":
        address = self.address.strip()
        if HEADER_INJECTION.search(self.address):
            raise RecipientRefused(
                f"Recipient REFUSED: {self.address!r} contains a line break, which would let it "
                f"carry a second SMTP header (a Bcc, typically) into the send")
        if not ADDRESS.match(address):
            raise RecipientRefused(
                f"Recipient REFUSED: {self.address!r} is not a single addressable mailbox. One "
                f"draft addresses one person; a list is a place for an addressee nobody reviewed")
        refuse_machine_actor(self.chosen_by, what="Recipient.chosen_by")
        expected = (LEDGER_SOURCE_PREFIX if self.origin == "committed_ledger"
                    else FRONT_DESK_SOURCE_PREFIX)
        if not self.pointer.source_id.startswith(expected):
            raise RecipientRefused(
                f"Recipient REFUSED: origin={self.origin!r} but its pointer cites "
                f"{self.pointer.source_id!r}. The origin and the provenance must be the same "
                f"claim, or one of them is decoration")
        return self


# --- the draft --------------------------------------------------------------------


class OutboundDraft(BaseModel):
    """A message the clinic might send. Frozen, grounded, and inert until a human approves it.

    ``requires_human_approval`` is ``Literal[True]``: there is no value of this field that means
    "send this one without asking", which is the same device as
    ``ResolutionResult.requires_human_selection`` and ``Playbook.requires_human_approval``
    (CLAUDE.md §Hard constraints 4).
    """

    model_config = ConfigDict(frozen=True)

    draft_id: str
    channel: Channel = "email"
    recipient: Recipient
    #: Every sentence that leaves the building carries a pointer. ``GroundedValue`` makes that a
    #: construction requirement rather than a habit — the grounding wall's argument, applied to
    #: outbound text (CLAUDE.md §Hard constraints 2).
    subject: GroundedValue
    body: GroundedValue
    #: The intake this concerns, when it concerns one.
    intake_id: Optional[str] = None
    #: The monitor or playbook that produced it. A draft nobody can attribute to a rule is a draft
    #: nobody can argue with.
    playbook_id: str
    #: WHY this draft exists — the design's load-bearing field. The template row, the recipient's
    #: provenance, the condition that fired, and every committed value interpolated into the text.
    because: tuple[SourcePointer, ...]
    created_at: dt.datetime
    requires_human_approval: Literal[True] = True

    @model_validator(mode="after")
    def _nothing_ungrounded_and_nothing_smuggled(self) -> "OutboundDraft":
        for field in ("subject", "body"):
            grounded: GroundedValue = getattr(self, field)
            if not isinstance(grounded.value, str) or not grounded.value.strip():
                raise DraftRefused(
                    f"OutboundDraft REFUSED: {field} is empty. A blank message is a message whose "
                    f"content nobody reviewed")
        if HEADER_INJECTION.search(str(self.subject.value)):
            raise DraftRefused(
                "OutboundDraft REFUSED: the subject contains a line break, which would let it "
                "append an SMTP header of its own")
        if not self.because:
            raise DraftRefused(
                "OutboundDraft REFUSED: no `because`. An action with no traceable justification is "
                "refused construction, exactly as an ungrounded field is "
                "(GRAPH_AGENT_AND_MCP.md §4.3)")
        cited = {p.model_dump_json(exclude_none=True) for p in self.because}
        for pointer, what in ((self.recipient.pointer, "the recipient's provenance"),
                              (self.subject.pointer, "the subject's provenance"),
                              (self.body.pointer, "the body's provenance")):
            if pointer.model_dump_json(exclude_none=True) not in cited:
                raise DraftRefused(
                    f"OutboundDraft REFUSED: {what} is not in `because`. A reviewer reads "
                    f"`because`; provenance it does not list is provenance nobody will see")
        return self

    def to_payload(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "channel": self.channel,
            "to": self.recipient.address,
            "recipient_origin": self.recipient.origin,
            "recipient_chosen_by": self.recipient.chosen_by,
            "recipient_pointer": self.recipient.pointer.model_dump(mode="json", exclude_none=True),
            "subject": self.subject.value,
            "subject_pointer": self.subject.pointer.model_dump(mode="json", exclude_none=True),
            "body": self.body.value,
            "body_pointer": self.body.pointer.model_dump(mode="json", exclude_none=True),
            "intake_id": self.intake_id,
            "playbook_id": self.playbook_id,
            "because": [p.model_dump(mode="json", exclude_none=True) for p in self.because],
            "created_at": self.created_at.isoformat(),
            "requires_human_approval": True,
        }


def draft_id_for(*, playbook_id: str, intake_id: Optional[str], address: str, body: str) -> str:
    """A deterministic id for "this monitor, about this intake, to this person, saying this".

    Deterministic so a monitor that runs every five minutes produces the SAME draft id every time
    and the store's uniqueness check turns re-evaluation into a no-op. A random id would give the
    front desk one row per poll to reject by hand, which is how a review queue stops being read.
    """
    digest = hashlib.sha256("\x00".join(
        [playbook_id, intake_id or "", address, body]).encode("utf-8")).hexdigest()
    return f"draft_{digest[:24]}"


class OutboxEntry(BaseModel):
    """One row of the outbox, as read back: a state, and how it got there.

    A read model, not a draft. The store is append-only, so the "current" state of a draft is the
    latest row about it and every earlier row is still there to be read.
    """

    model_config = ConfigDict(frozen=True)

    rowid: int
    draft_id: str
    status: DraftStatus
    channel: Channel
    recipient_address: str
    recipient_origin: RecipientOrigin
    recipient_chosen_by: str
    recipient_pointer: SourcePointer
    subject: str
    body: str
    intake_id: Optional[str]
    playbook_id: str
    because: tuple[SourcePointer, ...]
    created_at: str
    recorded_at: str
    recorded_by: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[str] = None
    reason: Optional[str] = None
    sent_at: Optional[str] = None
    message_id: Optional[str] = None
    supersedes: Optional[int] = None

    @property
    def is_dispatchable(self) -> bool:
        """The ONE predicate the dispatcher is allowed to act on.

        Both halves matter and neither is redundant: ``status == approved`` without an
        ``approved_by`` is a row that says a human agreed and cannot say which one, and a row that
        cannot name its approver has not been approved (CLAUDE.md §Hard constraints 4).
        """
        return self.status == APPROVED and bool((self.approved_by or "").strip())

    def to_payload(self) -> dict:
        data = self.model_dump(mode="json", exclude={"recipient_pointer", "because"})
        data["recipient_pointer"] = self.recipient_pointer.model_dump(mode="json",
                                                                     exclude_none=True)
        data["because"] = [p.model_dump(mode="json", exclude_none=True) for p in self.because]
        return data
