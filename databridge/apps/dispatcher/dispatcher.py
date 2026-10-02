"""The dispatcher — the dumbest process in the system, on purpose.

`docs/hackathon/GRAPH_AGENT_AND_MCP.md` §3 rule 1:

    The agent never holds send credentials. It writes to an outbox. A separate, dumb dispatcher —
    no model in the loop — sends approved rows. An injected prompt can therefore cause a *draft*,
    which a human reads, and never a send.

This is that process. Its entire decision procedure is:

    for row in outbox.awaiting_dispatch():      # approved, WITH a named approver
        message_id = transport.send(...)
        outbox.record_sent(row.draft_id, message_id=message_id)

There is no model here, no agent, and no path to either. ``tests/test_dispatcher_is_model_free.py``
asserts that by scanning every module in this directory — a scan that fails for a module nobody has
written yet, not a hardcoded list of today's names — and again by checking ``sys.modules`` after an
import, so a *transitive* model dependency also fails it. That second check is why
``databridge_outbox`` imports its ledger-facing module lazily.

**The only predicate it acts on** is ``OutboxEntry.is_dispatchable``: status ``approved`` AND a
non-empty ``approved_by``. Both halves are checked here, and again inside
``OutboxStore.record_sent``, and neither is redundant — this one stops the send, that one stops the
record, and a system where the send happened and the record did not is worse than one where neither
did.

**The gap this process cannot close, stated plainly.** ``send`` then ``record_sent`` is not atomic.
A crash between them leaves a message delivered and an outbox row still saying ``approved``, and the
next run would send it again. The outbox's ``ix_outbox_sent_once`` index makes a *duplicate record*
impossible, which is not the same as making a duplicate *delivery* impossible. Closing that
properly needs an idempotency key the transport honours; SMTP has none. For a clinic POC against
Mailpit the honest thing is to say so here rather than to imply an at-most-once guarantee that does
not exist. Nothing about it can cause an *unapproved* message to go out, which is the property that
actually matters.

**Every send is under a lease** (R-028 OUTX, Codex #3). A row is sent only by the process holding
its live dispatch lease (``OutboxStore.claim_for_dispatch``), claimed inside one cross-process
SQLite transaction; ``record_sent`` refuses any other. ``run_once()`` claims what is unleased and
sends that; ``run_once(claim_id=...)`` / ``--claim`` sends ONLY the rows a caller already claimed
(the gateway's single-draft mode), so a row approved by another process meanwhile is not swept
along with it.

Run it against a file-backed outbox — the default in-memory store belongs to whichever process
created it, and that is the process split telling you the truth:

    DATABRIDGE_OUTBOX_DB=./outbox.sqlite python apps/dispatcher/dispatcher.py --once
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, runtime_checkable

from databridge_outbox.models import NotApproved, OutboxEntry
from databridge_outbox.store import ClaimLost, OutboxStore

OUTBOX_DB_ENV = "DATABRIDGE_OUTBOX_DB"
POLL_SECONDS_ENV = "DATABRIDGE_DISPATCH_POLL_SECONDS"
DEFAULT_POLL_SECONDS = 15.0


class DispatchFailed(RuntimeError):
    """Every approved draft failed to send.

    Its own type rather than a re-raise of whatever the transport threw: reconstructing an
    arbitrary exception class with a new message assumes a ``str`` constructor, which is a guess
    about somebody else's library. A misconfiguration must be loud, and loud is all this needs
    to be — the cause is chained.
    """


@runtime_checkable
class SendTransport(Protocol):
    """Anything that can put one message outside the building.

    Three strings in, a message id out. Deliberately not "anything that can act": a transport that
    took a draft, or an outbox handle, could decide something, and deciding is the half of this
    system that has a human in it.
    """

    name: str

    def send(self, *, to: str, subject: str, body: str) -> str:
        ...


@dataclass(frozen=True)
class SendReceipt:
    """What left, when, and under which transport id."""

    draft_id: str
    to: str
    message_id: str
    transport: str
    sent_at: dt.datetime
    approved_by: str

    def describe(self) -> str:
        return (f"sent {self.draft_id} to {self.to} via {self.transport} "
                f"as {self.message_id} (approved by {self.approved_by})")


class Dispatcher:
    """Reads approved rows; sends them; records the send. Nothing else."""

    def __init__(self, store: OutboxStore, transport: SendTransport) -> None:
        self.store = store
        self.transport = transport

    # -- the gate ------------------------------------------------------------------
    @staticmethod
    def refuse_unless_approved(entry: OutboxEntry) -> None:
        """Raise unless a named human approved this row. The one check this process makes.

        Separate from ``dispatch`` so a test can call it directly, and so the refusal message is
        written once. ``is_dispatchable`` is the definition; this is where it stops something.
        """
        if not entry.is_dispatchable:
            raise NotApproved(
                f"dispatch REFUSED: draft {entry.draft_id!r} is {entry.status!r} with "
                f"approved_by={entry.approved_by!r}. Nothing leaves this clinic without a named "
                f"human approving it — the outbox IS the human-confirmation gate applied to "
                f"outbound messages (CLAUDE.md §Hard constraints 4).")

    # -- the work ------------------------------------------------------------------
    @property
    def actor(self) -> str:
        return f"dispatcher:{self.transport.name}"

    def dispatch(self, draft_id: str, *, claim_id: Optional[str] = None) -> SendReceipt:
        """Send one draft, if and only if a named human approved it AND this process holds its
        lease. With no ``claim_id`` the row is claimed here first (and released after)."""
        entry = self.store.current(draft_id)
        if entry is None:
            raise NotApproved(f"dispatch REFUSED: no draft {draft_id!r} in the outbox")
        self.refuse_unless_approved(entry)
        own = claim_id is None
        if own:
            claim = self.store.claim_for_dispatch(claimed_by=self.actor, draft_ids=[draft_id])
            if draft_id not in claim.draft_ids:
                raise ClaimLost(f"dispatch REFUSED: draft {draft_id!r} is leased to another "
                                f"dispatch")
            claim_id = claim.claim_id
        try:
            if not self.store.holds_claim(draft_id, claim_id):
                raise ClaimLost(f"dispatch REFUSED: this dispatch does not hold the lease on "
                                f"draft {draft_id!r}")
            entry = self.store.current(draft_id)
            if entry is None:
                raise NotApproved(f"dispatch REFUSED: no draft {draft_id!r} in the outbox")
            self.refuse_unless_approved(entry)
            message_id = self.transport.send(to=entry.recipient_address,
                                             subject=entry.subject,
                                             body=entry.body)
            sent = self.store.record_sent(draft_id, message_id=message_id, by=self.actor,
                                          claim_id=claim_id)
        finally:
            if own:
                self.store.release_claim(claim_id, [draft_id])
        return SendReceipt(draft_id=draft_id, to=sent.recipient_address, message_id=message_id,
                           transport=self.transport.name,
                           sent_at=dt.datetime.fromisoformat(sent.sent_at or ""),
                           approved_by=sent.approved_by or "")

    def run_once(self, *, claim_id: Optional[str] = None) -> list[SendReceipt]:
        """Send everything currently approved and unleased (claimed here), or with ``claim_id``
        ONLY the rows that claim holds. Returns a receipt per message that left. The claim's
        remaining leases are released at the end, so a failed row is retried next time.

        A failure on one row does not stop the rest: the failed row stays ``approved`` and is
        retried next time, which is the correct behaviour for a mail server that was briefly down
        and the only behaviour available for one that is permanently misconfigured. The exception
        is re-raised only if EVERY row failed, so a misconfiguration is loud rather than a silent
        no-op.
        """
        receipts: list[SendReceipt] = []
        failures: list[tuple[str, Exception]] = []
        if claim_id is None:
            claim = self.store.claim_for_dispatch(claimed_by=self.actor)
            claim_id, draft_ids = claim.claim_id, list(claim.draft_ids)
        else:
            draft_ids = [e.draft_id for e in self.store.claimed(claim_id)]
        try:
            for draft_id in draft_ids:
                try:
                    receipts.append(self.dispatch(draft_id, claim_id=claim_id))
                except Exception as exc:                   # noqa: BLE001 — reported, not swallowed
                    failures.append((draft_id, exc))
        finally:
            self.store.release_claim(claim_id)
        if failures and not receipts:
            draft_id, exc = failures[0]
            raise DispatchFailed(
                f"every approved draft failed to send; first was {draft_id}: "
                f"{type(exc).__name__}: {exc}") from exc
        for draft_id, exc in failures:
            print(f"dispatch FAILED for {draft_id}: {type(exc).__name__}: {exc}")
        return receipts

    def run_forever(self, *, poll_seconds: Optional[float] = None) -> None:  # pragma: no cover
        interval = poll_seconds if poll_seconds is not None else float(
            os.environ.get(POLL_SECONDS_ENV) or DEFAULT_POLL_SECONDS)
        while True:
            for receipt in self.run_once():
                print(receipt.describe())
            time.sleep(interval)


def build_dispatcher(*, db_path: Optional[str] = None,
                     transport: Optional[SendTransport] = None) -> Dispatcher:
    """Wire a dispatcher from the environment.

    The SMTP transport is imported here rather than at module scope so that importing this module
    — which is what the model-free scan and every unit test do — does not import ``smtplib``. The
    transport is a dependency of RUNNING the dispatcher, not of reasoning about it.
    """
    if transport is None:
        from smtp_transport import SmtpTransport

        transport = SmtpTransport()
    path = db_path or os.environ.get(OUTBOX_DB_ENV)
    if not path:
        raise RuntimeError(
            f"set {OUTBOX_DB_ENV} to the outbox database file. The default in-memory store belongs "
            f"to the process that created it, and the dispatcher is a DIFFERENT PROCESS — that is "
            f"the capability boundary, not an inconvenience to route around.")
    return Dispatcher(OutboxStore(path), transport)


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover (entrypoint)
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true",
                        help="send what is approved now, then exit")
    parser.add_argument("--db", default=None, help=f"outbox database (or ${OUTBOX_DB_ENV})")
    parser.add_argument("--claim", default=None,
                        help="send ONLY the rows this dispatch claim holds (with --once)")
    args = parser.parse_args(argv)

    dispatcher = build_dispatcher(db_path=args.db)
    print(f"dispatcher: outbox={args.db or os.environ.get(OUTBOX_DB_ENV)} "
          f"transport={dispatcher.transport.name}")
    if args.once:
        for receipt in dispatcher.run_once(claim_id=args.claim):
            print(receipt.describe())
        return 0
    if args.claim:
        parser.error("--claim is a one-sweep mode; use it with --once")
    dispatcher.run_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
