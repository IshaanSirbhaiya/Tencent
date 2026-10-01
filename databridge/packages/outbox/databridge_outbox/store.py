"""The outbox — append-only, exactly like the evidence ledger, and for the same reason.

``GRAPH_AGENT_AND_MCP.md`` §8 asked where the outbox should live and leaned "a separate table, do
not weaken the ledger's append-only guarantee". That is what this is: a separate database with the
ledger's *shape* rather than a new column on the ledger's table.

The shape is the argument. ``ledger.py``'s docstring makes it in full and it transfers without
amendment:

**Append-only.** There is no ``UPDATE`` and no ``DELETE`` in this module, and SQLite triggers
enforce that at the database level so a future code path — or a hand-run statement at 2am — cannot
quietly rewrite what happened. A state change is a NEW row whose ``supersedes`` names the row it
replaces. "Who approved this, and when, and what did the text say at the moment they approved it?"
stays answerable after the fact, which is the only version of an approval that is worth anything.

That last clause is why the payload columns are copied onto every transition row rather than
normalised away. An approval row carries the subject and body **as approved**. If the text lived in
one row and the approval in another, "a human approved this message" would mean "a human approved
whatever that row says now", and the draft row is the one nobody can change — but the join is the
one nobody would notice going wrong.

**Two structural guarantees the triggers do not give:**

* ``ix_outbox_sent_once`` — a UNIQUE index on ``draft_id`` where ``status = 'sent'``. A second sent
  row for one draft is rejected by the database. The dispatcher checks before sending; this is what
  holds when two dispatchers run at once.
* ``ix_outbox_supersedes_once`` — a UNIQUE index on ``supersedes``. Two rows superseding the same
  row would fork a draft's history into two live states, and ``current()`` would resolve the fork
  by insertion order — the machine picking between "approved" and "rejected" by accident.

**What this store will not do.** It has no ``send``. It imports no transport (there is no
``smtplib`` here and ``tests/test_outbox_cannot_send.py`` asserts it against the source). Moving a
row to ``sent`` requires it to be ``approved`` *with a named ``approved_by``*, so the recording of a
send is itself gated on the human-confirmation constraint (CLAUDE.md §Hard constraints 4) rather
than merely following it.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from typing import Iterable, Optional, Protocol, runtime_checkable

from databridge_core import SourcePointer

from .models import (
    APPROVED,
    DRAFTED,
    REJECTED,
    SENT,
    DraftStatus,
    NotApproved,
    OutboundDraft,
    OutboxEntry,
    OutboxError,
    refuse_machine_actor,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    rowid_pk            INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            TEXT NOT NULL,
    status              TEXT NOT NULL
                        CHECK (status IN ('drafted', 'approved', 'rejected', 'sent')),
    channel             TEXT NOT NULL,
    -- WHO this goes to, and which of the two permitted doors the address came through. Stored
    -- with its pointer so a reviewer reading a row six months later can see that a named human
    -- selected this patient in person, or typed this address, without trusting the row's word.
    recipient_address   TEXT NOT NULL,
    recipient_origin    TEXT NOT NULL
                        CHECK (recipient_origin IN ('committed_ledger', 'typed_by_human')),
    recipient_chosen_by TEXT NOT NULL,
    recipient_pointer_json TEXT NOT NULL,
    -- The text AS APPROVED, carried on every transition row. See the module docstring.
    subject             TEXT NOT NULL,
    body                TEXT NOT NULL,
    intake_id           TEXT,
    playbook_id         TEXT NOT NULL,
    because_json        TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    recorded_at         TEXT NOT NULL,
    recorded_by         TEXT NOT NULL,
    approved_by         TEXT,
    approved_at         TEXT,
    rejected_by         TEXT,
    rejected_at         TEXT,
    reason              TEXT,
    sent_at             TEXT,
    message_id          TEXT,
    supersedes          INTEGER REFERENCES outbox(rowid_pk)
);
CREATE INDEX IF NOT EXISTS ix_outbox_draft ON outbox(draft_id);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON outbox(status);

-- At most one `sent` row per draft, enforced by the database. The dispatcher checks the status
-- before it sends; this is what holds when two dispatchers are running.
CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_sent_once
    ON outbox(draft_id) WHERE status = 'sent';

-- At most one row may supersede any given row. Two would fork a draft's history into two live
-- states, and `current()` would then resolve "approved or rejected?" by insertion order.
CREATE UNIQUE INDEX IF NOT EXISTS ix_outbox_supersedes_once
    ON outbox(supersedes) WHERE supersedes IS NOT NULL;

-- The append-only guarantee, enforced by the database rather than by everyone remembering.
CREATE TRIGGER IF NOT EXISTS outbox_no_update
BEFORE UPDATE ON outbox
BEGIN
    SELECT RAISE(ABORT, 'outbox is append-only: record a state change as a new row');
END;

CREATE TRIGGER IF NOT EXISTS outbox_no_delete
BEFORE DELETE ON outbox
BEGIN
    SELECT RAISE(ABORT, 'outbox is append-only: rows are never deleted');
END;
"""

#: The state machine, written once. ``rejected`` and ``sent`` are terminal: a rejected draft is not
#: un-rejected, it is superseded by a NEW draft somebody looked at, and a sent message is not
#: unsent by anything a database can do.
ALLOWED_NEXT: dict[str, frozenset[str]] = {
    DRAFTED: frozenset({APPROVED, REJECTED}),
    APPROVED: frozenset({SENT}),
    REJECTED: frozenset(),
    SENT: frozenset(),
}

#: Columns copied verbatim onto every transition row — the message as it stood when the transition
#: was made.
_PAYLOAD_COLUMNS = (
    "draft_id", "channel", "recipient_address", "recipient_origin", "recipient_chosen_by",
    "recipient_pointer_json", "subject", "body", "intake_id", "playbook_id", "because_json",
    "created_at",
)

_ALL_COLUMNS = (*_PAYLOAD_COLUMNS, "status", "recorded_at", "recorded_by", "approved_by",
                "approved_at", "rejected_by", "rejected_at", "reason", "sent_at", "message_id",
                "supersedes")


class UnknownDraft(OutboxError):
    """No such draft. Raised, never treated as "nothing to do"."""


class DuplicateDraft(OutboxError):
    """This draft id is already filed. Re-running a monitor is a no-op, not a second row."""


class IllegalTransition(OutboxError):
    """A state change the outbox's state machine does not permit."""


def _now(at: Optional[dt.datetime] = None) -> str:
    return (at or dt.datetime.now(dt.timezone.utc)).isoformat()


@runtime_checkable
class EmissionSink(Protocol):
    """Where a filed draft registers itself with the reverse pointer index.

    A `Protocol`, not an import of `databridge_graph` — the same seam `databridge_intake.ledger`
    uses, for the same reason: this store should not have to grow a dependency on the graph
    substrate just to log a citation. `ReverseIndex.record_emission` already has exactly this
    shape, so a real `GraphStore.reverse_index()` satisfies it with no import in either direction;
    composition happens where the store is constructed.
    """

    def record_emission(self, kind: str, ref: str, pointers: Iterable[SourcePointer], *,
                        plan_hash: Optional[str] = None, ledger_bound: bool = False,
                        emitted_at: Optional[dt.datetime] = None) -> int: ...


class OutboxStore:
    """Append-only SQLite store of outbound drafts and what humans decided about them."""

    def __init__(self, path: str = ":memory:", *,
                emission_sink: Optional[EmissionSink] = None) -> None:
        # Default in-memory, as ``EvidenceLedger`` is, and with the same caveat: nothing survives a
        # restart and multiple workers would each hold their own. Pass a file path for anything
        # durable — and for the dispatcher, which is a DIFFERENT PROCESS and therefore cannot see
        # an in-memory store at all. That is not a limitation to work around; it is the deployment
        # telling you the truth about the process split.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        # `None` by default: every existing caller (the dispatcher, the gateway route) keeps
        # working unchanged. Wired in, every filed draft becomes discoverable through the reverse
        # index's `impact_of` — see `record_draft` below.
        self._emission_sink = emission_sink

    # -- reads --------------------------------------------------------------------
    def _rows(self, draft_id: Optional[str] = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM outbox"
        args: tuple = ()
        if draft_id is not None:
            sql += " WHERE draft_id = ?"
            args = (draft_id,)
        sql += " ORDER BY rowid_pk"
        # Same lock the writes take, and for the same reason ``EvidenceLedger.entries`` gives: one
        # shared connection with ``check_same_thread=False`` means an unlocked read issued from
        # another thread mid-transaction sees a partially-inserted batch.
        with self._lock:
            return list(self.conn.execute(sql, args))

    @staticmethod
    def _entry(row: sqlite3.Row) -> OutboxEntry:
        return OutboxEntry(
            rowid=row["rowid_pk"],
            draft_id=row["draft_id"],
            status=row["status"],
            channel=row["channel"],
            recipient_address=row["recipient_address"],
            recipient_origin=row["recipient_origin"],
            recipient_chosen_by=row["recipient_chosen_by"],
            recipient_pointer=SourcePointer.model_validate_json(row["recipient_pointer_json"]),
            subject=row["subject"],
            body=row["body"],
            intake_id=row["intake_id"],
            playbook_id=row["playbook_id"],
            because=tuple(SourcePointer.model_validate(p)
                          for p in json.loads(row["because_json"])),
            created_at=row["created_at"],
            recorded_at=row["recorded_at"],
            recorded_by=row["recorded_by"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            rejected_by=row["rejected_by"],
            rejected_at=row["rejected_at"],
            reason=row["reason"],
            sent_at=row["sent_at"],
            message_id=row["message_id"],
            supersedes=row["supersedes"])

    def history(self, draft_id: str) -> list[OutboxEntry]:
        """Every row about this draft, oldest first. Nothing is ever removed from it."""
        return [self._entry(r) for r in self._rows(draft_id)]

    def entries(self) -> list[OutboxEntry]:
        return [self._entry(r) for r in self._rows()]

    def current(self, draft_id: str) -> Optional[OutboxEntry]:
        """The live row for this draft: the one nothing supersedes."""
        with self._lock:
            rows = self._rows(draft_id)
            if not rows:
                return None
            superseded = {r["supersedes"] for r in rows if r["supersedes"] is not None}
            live = [r for r in rows if r["rowid_pk"] not in superseded]
            # ``ix_outbox_supersedes_once`` makes a fork unrepresentable, so this is a tripwire on
            # a database that has been edited by something other than this module.
            if len(live) != 1:
                raise OutboxError(
                    f"draft {draft_id!r} has {len(live)} live rows; its history has been forked "
                    f"outside this module and no state can be read from it")
            return self._entry(live[0])

    def by_status(self, status: DraftStatus) -> list[OutboxEntry]:
        with self._lock:
            ids = {r["draft_id"] for r in self._rows()}
            out = [self.current(d) for d in sorted(ids)]
        return [e for e in out if e is not None and e.status == status]

    def awaiting_dispatch(self) -> list[OutboxEntry]:
        """The ONLY queue the dispatcher reads: approved rows that can name their approver.

        Both halves are checked here as well as in ``OutboxEntry.is_dispatchable``, because this is
        the query — a dispatcher that asked for ``status = 'approved'`` directly would pick up a row
        whose ``approved_by`` was never written.
        """
        return [e for e in self.by_status(APPROVED) if e.is_dispatchable]

    # -- writes: every one of them an INSERT ---------------------------------------
    def _insert(self, columns: dict) -> int:
        names = [c for c in _ALL_COLUMNS if c in columns]
        placeholders = ", ".join("?" for _ in names)
        sql = f"INSERT INTO outbox ({', '.join(names)}) VALUES ({placeholders})"
        cursor = self.conn.execute(sql, tuple(columns[n] for n in names))
        return int(cursor.lastrowid)

    def record_draft(self, draft: OutboundDraft, *, at: Optional[dt.datetime] = None) -> OutboxEntry:
        """File a draft. Idempotent by ``draft_id``… deliberately not silently so.

        A monitor that runs every five minutes produces the same ``draft_id`` every time, so the
        second attempt raises ``DuplicateDraft`` and the caller decides whether that is news. A
        store that quietly returned the existing row would let a *changed* body land under an
        already-approved id.
        """
        with self._lock, self.conn:
            if self._rows(draft.draft_id):
                raise DuplicateDraft(
                    f"draft {draft.draft_id!r} is already filed. The id is a digest of the "
                    f"monitor, the intake, the address and the text, so an identical draft is "
                    f"the same draft — and a different one gets a different id.")
            rowid = self._insert({
                "draft_id": draft.draft_id,
                "status": DRAFTED,
                "channel": draft.channel,
                "recipient_address": draft.recipient.address,
                "recipient_origin": draft.recipient.origin,
                "recipient_chosen_by": draft.recipient.chosen_by,
                "recipient_pointer_json": draft.recipient.pointer.model_dump_json(
                    exclude_none=True),
                "subject": str(draft.subject.value),
                "body": str(draft.body.value),
                "intake_id": draft.intake_id,
                "playbook_id": draft.playbook_id,
                "because_json": json.dumps(
                    [p.model_dump(mode="json", exclude_none=True) for p in draft.because]),
                "created_at": draft.created_at.isoformat(),
                "recorded_at": _now(at),
                # A draft is a machine act and says so. Only ``approved_by`` and ``rejected_by``
                # name people, and those are the two fields ``refuse_machine_actor`` guards.
                "recorded_by": f"monitor:{draft.playbook_id}",
            })
            # Registered as the last step of the SAME `with self._lock, self.conn:` block the
            # insert above runs in, exactly as `EvidenceLedger.commit` does and for the same
            # reason: a sink failure raises before this block commits, which rolls the insert back
            # with it, so a draft can never exist un-registered. It is not a single transaction
            # spanning the outbox's database and the graph's — two independent SQLite connections
            # cannot share one — but it is the strongest ordering available short of that: the sink
            # is called synchronously, in-process, before the outbox's own commit, and nothing runs
            # after it that could still fail.
            #
            # Every citation `pointers_of()` would return for this draft once it is an
            # `OutboxEntry` — the recipient's provenance AND every `because` pointer — registers
            # here, not only the recipient: `because` is this draft's whole justification, and a
            # fan-out that dropped it would make the drift cascade blind to the draft citing a
            # value that has since changed for any reason other than who it was addressed to.
            if self._emission_sink is not None:
                self._emission_sink.record_emission(
                    kind="outbox_draft", ref=draft.draft_id,
                    pointers=(draft.recipient.pointer, *draft.because),
                    ledger_bound=False, emitted_at=draft.created_at)
        entry = self.current(draft.draft_id)
        assert entry is not None and entry.rowid == rowid
        return entry

    def _transition(self, draft_id: str, *, to: DraftStatus, by: str,
                    extra: Optional[dict] = None) -> OutboxEntry:
        """Append a row moving ``draft_id`` to ``to``. The read and the insert are one transaction.

        Read outside it and two concurrent approvals both see the same live row, both insert a row
        superseding it, and the draft has two live states — which is the fork ``current()`` refuses
        to guess its way out of. (``ix_outbox_supersedes_once`` also rejects the second insert; the
        transaction is what makes the rejection a clean failure rather than a race.)
        """
        with self._lock, self.conn:
            live = self.current(draft_id)
            if live is None:
                raise UnknownDraft(f"no draft {draft_id!r} in the outbox")
            if to not in ALLOWED_NEXT[live.status]:
                onward = sorted(ALLOWED_NEXT[live.status])
                raise IllegalTransition(
                    f"draft {draft_id!r} is {live.status!r}; it cannot become {to!r}. Permitted "
                    f"from here: {onward or 'nothing — this is a terminal state'}")
            row = next(r for r in self._rows(draft_id) if r["rowid_pk"] == live.rowid)
            columns = {name: row[name] for name in _PAYLOAD_COLUMNS}
            # Carried forward so an approval is still visible on the `sent` row: the audit question
            # is "who let this out", and the answer must be on the row that says it left.
            columns.update({
                "approved_by": row["approved_by"], "approved_at": row["approved_at"],
                "rejected_by": row["rejected_by"], "rejected_at": row["rejected_at"],
                "reason": row["reason"],
                "status": to, "recorded_at": _now(), "recorded_by": by,
                "supersedes": live.rowid,
            })
            columns.update(extra or {})
            self._insert(columns)
        entry = self.current(draft_id)
        assert entry is not None
        return entry

    def approve(self, draft_id: str, *, approved_by: str,
                at: Optional[dt.datetime] = None) -> OutboxEntry:
        """A named human agrees this may go out. **The gate.**

        ``approved_by`` has no default and may not be a process name. There is no code path that
        approves a draft without one, which is the outbound half of CLAUDE.md §Hard constraints 4.
        """
        who = refuse_machine_actor(approved_by, what="approve(approved_by=...)")
        when = _now(at)
        return self._transition(draft_id, to=APPROVED, by=who,
                                extra={"approved_by": who, "approved_at": when})

    def reject(self, draft_id: str, *, rejected_by: str, reason: str,
               at: Optional[dt.datetime] = None) -> OutboxEntry:
        """A named human says no, and says why. The draft stays in the record; nothing is deleted."""
        who = refuse_machine_actor(rejected_by, what="reject(rejected_by=...)")
        if not (reason or "").strip():
            raise OutboxError(
                "reject REFUSED: a rejection with no reason teaches the next draft nothing")
        return self._transition(draft_id, to=REJECTED, by=who,
                                extra={"rejected_by": who, "rejected_at": _now(at),
                                       "reason": reason.strip()})

    def record_sent(self, draft_id: str, *, message_id: str, by: str = "dispatcher",
                    at: Optional[dt.datetime] = None) -> OutboxEntry:
        """Record that this draft left the building, and under which transport message id.

        Refuses anything that is not ``approved`` **with a named approver**. The dispatcher checks
        the same thing before it calls a transport; this is the check that survives somebody
        writing a second dispatcher.
        """
        with self._lock:
            live = self.current(draft_id)
            if live is None:
                raise UnknownDraft(f"no draft {draft_id!r} in the outbox")
            if not live.is_dispatchable:
                raise NotApproved(
                    f"record_sent REFUSED: draft {draft_id!r} is {live.status!r} and "
                    f"approved_by={live.approved_by!r}. Nothing leaves this system without a named "
                    f"human approving it (CLAUDE.md §Hard constraints 4).")
            if not (message_id or "").strip():
                raise OutboxError(
                    "record_sent REFUSED: no transport message id. A send that cannot be looked "
                    "up in the mail log is a send nobody can prove or trace")
            return self._transition(draft_id, to=SENT, by=by,
                                    extra={"sent_at": _now(at),
                                           "message_id": message_id.strip()})

    # -- provenance ---------------------------------------------------------------
    def pointers_of(self, entry: OutboxEntry) -> Iterable[SourcePointer]:
        """Every citation behind a row: why it exists, and how its recipient was established."""
        return (entry.recipient_pointer, *entry.because)


__all__ = [
    "ALLOWED_NEXT",
    "DuplicateDraft",
    "IllegalTransition",
    "OutboxStore",
    "UnknownDraft",
]
