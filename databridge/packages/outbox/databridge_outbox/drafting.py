"""Building a draft — and the structural reason a model cannot decide who it goes to.

``build_outbound_draft`` is written to the same rule as ``intake.claim.build_claim_draft``, and for
the same reason that one was rewritten: *the fix for this class of bug is a signature, not a
convention.* ``build_claim_draft`` takes an intake id, a ledger and the config table, so there is no
parameter through which an uncommitted value can arrive. This one takes a monitor, an observation
and a ``Recipient``, so:

* **there is no parameter through which an ADDRESS can arrive.** Not ``to``, not ``address``, not
  ``email``, not a ``dict`` that might contain one. The only recipient-shaped parameter is typed
  ``Recipient``, which ``models.py`` refuses to construct outside ``recipients.py``'s two doors. A
  model's output reaches this process as JSON parsed into a schema, and there is no schema here it
  can be parsed into;
* **there is no parameter through which UNGROUNDED TEXT can arrive.** The subject and body are
  rendered from the monitor's templates — config, with a pointer into the file — interpolated with
  ``GroundedValue`` facts, each of which contributes its own pointer. A placeholder with no fact
  behind it does not render as a blank or as itself: the draft is refused.

``tests/test_recipient_rule.py`` pins the parameter list, exactly as
``test_build_claim_draft_cannot_be_handed_the_working_draft`` pins that one. If a ``to`` parameter
is ever added here, a model-supplied recipient can reach a mailbox again — so the parameter list
itself is the assertion.

Interpolation is a single ``re.sub`` pass with a callback, never a second pass over the result, so a
fact whose value happens to contain ``{package_code}`` inserts that text and does not become a
placeholder. Template injection through patient data is a real shape, and one pass is what closes
it.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from databridge_core import GroundedValue, SourcePointer

from .models import (
    DraftRefused,
    OutboundDraft,
    Recipient,
    draft_id_for,
)
from .monitors import MONITORS_SOURCE_ID, PLACEHOLDER, Monitor, Observation


def _render(monitor: Monitor, column: str, observation: Observation
            ) -> tuple[str, SourcePointer, list[SourcePointer]]:
    """One template, filled from grounded facts, with the provenance that produced it.

    Returns the text, a ``derived`` pointer citing the template row and every fact used, and the
    fact pointers themselves so the caller can put them in ``because``.
    """
    template = getattr(monitor, column)
    template_pointer = monitor.pointer(column)
    used: list[SourcePointer] = []
    missing: list[str] = []

    for name in monitor.placeholders(column):
        fact = observation.fact(name)
        if fact is None or fact.value is None or not str(fact.value).strip():
            missing.append(name)
    if missing:
        raise DraftRefused(
            f"monitor {monitor.id!r} REFUSED: {column} needs {missing}, and the observation for "
            f"intake {observation.intake_id!r} has no grounded value for them. A sentence a "
            f"patient reads is held to the same rule as an extracted field: if it is not in a "
            f"source, it is not emitted (CLAUDE.md §Hard constraints 2).")

    def substitute(match) -> str:
        fact = observation.fact(match.group(1))
        assert fact is not None                     # checked above; kept as a tripwire
        if fact.pointer not in used:
            used.append(fact.pointer)
        return str(fact.value)

    # ONE pass. A second pass over the result would let a fact whose value contains braces become a
    # placeholder — template injection through patient data, which is text an attacker can write.
    text = PLACEHOLDER.sub(substitute, template).strip()
    pointer = SourcePointer(
        source_id=MONITORS_SOURCE_ID, kind="derived",
        derivation=(f"clinic_monitors.yaml monitors[{monitor.id}].{column}, interpolated with "
                    f"grounded values from intake {observation.intake_id}"),
        derived_from=[template_pointer, *used])
    return text, pointer, [template_pointer, *used]


def build_outbound_draft(monitor: Monitor,
                         observation: Observation,
                         *,
                         recipient: Recipient,
                         created_at: Optional[dt.datetime] = None) -> OutboundDraft:
    """Draft the message ``monitor`` calls for about ``observation``, to ``recipient``.

    Refuses when the recipient came through a different door than the monitor declares: a monitor
    saying ``committed_ledger`` may not be satisfied with an address somebody typed, and vice
    versa. The config decides the door; the caller cannot substitute a more convenient one.

    Nothing here sends, queues a send, or acquires the means to. The draft returned is inert until
    ``OutboxStore.record_draft`` files it and a named human approves it.
    """
    if not isinstance(recipient, Recipient):
        # Typed, and checked anyway: a `str` slipping through here is precisely the failure this
        # module exists to make impossible, so it fails loudly rather than being coerced.
        raise DraftRefused(
            f"build_outbound_draft REFUSED: recipient is a {type(recipient).__name__}, not a "
            f"Recipient. An address may come only from a committed ledger row a human attested "
            f"to, or from a human typing one (recipients.py) — never from a caller's string, and "
            f"never from a model's output.")
    if recipient.origin != monitor.recipient_from:
        raise DraftRefused(
            f"build_outbound_draft REFUSED: monitor {monitor.id!r} declares "
            f"recipient_from={monitor.recipient_from!r} but this recipient came through "
            f"{recipient.origin!r}. Which door an address comes through is a clinic policy stated "
            f"in the config table, not something a call site chooses.")

    subject_text, subject_pointer, subject_cited = _render(monitor, "subject_template", observation)
    body_text, body_pointer, body_cited = _render(monitor, "body_template", observation)

    because: list[SourcePointer] = []
    for pointer in (monitor.pointer("condition"), recipient.pointer,
                    subject_pointer, body_pointer, *subject_cited, *body_cited):
        if pointer not in because:
            because.append(pointer)

    return OutboundDraft(
        draft_id=draft_id_for(playbook_id=monitor.id, intake_id=observation.intake_id,
                              address=recipient.address, body=body_text),
        channel=monitor.channel,
        recipient=recipient,
        subject=GroundedValue(value=subject_text, pointer=subject_pointer),
        body=GroundedValue(value=body_text, pointer=body_pointer),
        intake_id=observation.intake_id,
        playbook_id=monitor.id,
        because=tuple(because),
        created_at=created_at or dt.datetime.now(dt.timezone.utc))


__all__ = ["build_outbound_draft"]
