"""The evidence ledger — append-only, and the human-confirmation gate in front of it.

Two properties, both structural rather than conventional:

**Append-only.** There is no ``UPDATE`` and no ``DELETE`` in this module, and SQLite triggers
enforce that at the database level so a future code path (or a hand-run statement) cannot quietly
rewrite history. A correction is a NEW row whose ``supersedes`` names the row it replaces; both
remain readable, which is what makes "what did we believe, and when?" answerable after the fact.

**Nothing enters without a completed human attestation.** ``commit`` takes a ``ConfirmationState``
and refuses unless ``is_ready_to_commit()`` — an in-person identity check by a named staff member,
plus a candidate that same human selected. The state class already makes an unattested selection
unconstructible; this is the second lock on the same door (CLAUDE.md §Hard constraints 1 and 4).

Every row carries the ``SourcePointer`` its value came from. A committed fact that cannot say where
it came from is not evidence, so ``EvidenceEntry`` requires one and this module never invents one.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from typing import Iterable, Optional

from databridge_core import SourcePointer

from .models import ConfirmationState, EvidenceEntry, IntakeRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    rowid_pk          INTEGER PRIMARY KEY AUTOINCREMENT,
    intake_id         TEXT NOT NULL,
    field_path        TEXT NOT NULL,
    value_json        TEXT NOT NULL,
    source_pointer_json TEXT NOT NULL,
    extracted_at      TEXT NOT NULL,
    confirmed_by      TEXT NOT NULL,
    confirmed_at      TEXT NOT NULL,
    -- WHICH candidate the human selected. Without it the durable record cannot show the
    -- "attestation + human-selected candidate" pair it exists to prove: the selection would live
    -- only in process memory and vanish on restart.
    selected_patient_id TEXT,
    supersedes        INTEGER REFERENCES evidence(rowid_pk)
);
CREATE INDEX IF NOT EXISTS ix_evidence_intake ON evidence(intake_id);

-- The append-only guarantee, enforced by the database rather than by everyone remembering.
-- Without these, "append-only" is a code review convention that survives exactly as long as
-- nobody is in a hurry.
CREATE TRIGGER IF NOT EXISTS evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence ledger is append-only: record a correction as a new row');
END;

CREATE TRIGGER IF NOT EXISTS evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence ledger is append-only: entries are never deleted');
END;
"""


class LedgerError(RuntimeError):
    """A commit was refused, or the ledger is unusable."""


class IdentityNotVerified(LedgerError):
    """The confirmation gate refused: no completed in-person identity check.

    Carried as its own type so the gateway can map it to a stable ``IDENTITY_NOT_VERIFIED``
    refusal code rather than pattern-matching a message.
    """


class AlreadyCommitted(LedgerError):
    """This intake has already been committed. Corrections are new entries, not re-confirmations."""


class EvidenceLedger:
    """Append-only SQLite store of confirmed facts."""

    def __init__(self, path: str = ":memory:") -> None:
        # NOTE: the default is in-memory, so the ledger does not survive a restart and multiple
        # workers would each hold their own. That is a POC deployment property, not a design
        # choice — pass a file path for anything durable. Recorded in STATUS.md.
        # The lock serialises transactions over the shared connection: `check_same_thread=False`
        # permits sharing but nothing serialises `with self.conn:` blocks, so two requests'
        # transactions could otherwise interleave and one could commit the other's partial inserts.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- reads --------------------------------------------------------------------
    def entries(self, intake_id: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM evidence"
        args: tuple = ()
        if intake_id is not None:
            sql += " WHERE intake_id = ?"
            args = (intake_id,)
        sql += " ORDER BY rowid_pk"
        # Same lock `commit` takes for its transaction, and for the same reason: this is the same
        # shared `sqlite3.Connection` (`check_same_thread=False`), so an unlocked read issued from
        # another thread mid-`commit` does not get its own snapshot — it sees whatever prefix of
        # that batch's INSERTs have executed so far, i.e. a partially-inserted batch. GET
        # /intake/{id}/ledger runs concurrently with confirm() from FastAPI's threadpool, so this
        # is reachable, not theoretical (the same class of bug `commit`'s own docstring warns
        # about for two overlapping writers).
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    def count(self, intake_id: Optional[str] = None) -> int:
        return len(self.entries(intake_id))

    def current(self, intake_id: str) -> dict[str, dict]:
        """The live view: the latest row per field path, superseded rows excluded.

        History stays in the table; this is only how the UI reads "what do we believe now".
        """
        # `self._lock` is an RLock: `commit` already holds it when IT calls `current` to compute
        # `existing`, and a bare `Lock` would deadlock that reentrant call. Locked here too so a
        # caller reading `current` directly (the ledger route) gets one consistent snapshot across
        # both `entries()` calls below, rather than two reads straddling a concurrent commit.
        with self._lock:
            superseded = {r["supersedes"] for r in self.entries(intake_id) if r["supersedes"]}
            out: dict[str, dict] = {}
            for row in self.entries(intake_id):
                if row["rowid_pk"] in superseded:
                    continue
                out[row["field_path"]] = row
            return out

    # -- the gate + write ---------------------------------------------------------
    def commit(self,
               record: IntakeRecord,
               confirmation: ConfirmationState,
               *,
               at: Optional[dt.datetime] = None,
               field_paths: Optional[Iterable[str]] = None) -> list[EvidenceEntry]:
        """Write the record's grounded fields to the ledger. **The gate.**

        Refuses unless a named human has verified identity in person AND selected a candidate.
        Nothing partial is written on refusal: the check happens before the first insert.
        """
        ok, why = confirmation.is_ready_to_commit()
        if not ok:
            raise IdentityNotVerified(
                f"commit REFUSED: {why}. Identity is verified in person by clinic staff; the "
                f"system never confirms it (CLAUDE.md §Hard constraints 1).")
        if confirmation.committed:
            # `is_ready_to_commit()` stays True after a commit, so without this an already-committed
            # state re-commits: the rows land, and only THEN does `ConfirmationState.commit()`
            # raise. The caller returns a refusal while a full duplicate batch sits in the ledger,
            # superseding the originals — a refusal that wrote, and a corrupted audit trail.
            raise AlreadyCommitted(
                f"commit REFUSED: intake {record.intake_id!r} is already committed. The ledger is "
                f"append-only — record a correction as a new entry rather than re-confirming.")
        if confirmation.intake_id != record.intake_id:
            raise LedgerError(
                f"commit REFUSED: confirmation is for intake {confirmation.intake_id!r} but the "
                f"record is {record.intake_id!r}")

        at = at or dt.datetime.now(dt.timezone.utc)
        grounded = record.grounded_fields()
        wanted = list(field_paths) if field_paths is not None else list(grounded)
        unknown = [p for p in wanted if p not in grounded]
        if unknown:
            raise LedgerError(
                f"commit REFUSED: field path(s) {unknown} are not values this document carries")

        # The read of prior rows and the inserts must be ONE transaction. Read outside it and two
        # concurrent confirms both see the same `prior` rowid, each insert a row superseding it,
        # and one field path ends up with two live rows that `current()` then resolves by
        # insertion order. FastAPI runs these sync routes in a threadpool over one shared
        # connection, so this is reachable, not theoretical.
        with self._lock, self.conn:
            existing = self.current(record.intake_id)
            entries: list[EvidenceEntry] = []
            for path in wanted:
                value = grounded[path]
                prior = existing.get(path)
                entries.append(EvidenceEntry(
                    intake_id=record.intake_id,
                    field_path=path,
                    value=value.value,
                    pointer=value.pointer,
                    extracted_at=record.extracted_at,
                    confirmed_by=confirmation.identity_checked_in_person_by,
                    confirmed_at=at,
                    selected_patient_id=confirmation.selected_patient_id,
                    supersedes=prior["rowid_pk"] if prior else None))
            for entry in entries:
                self.conn.execute(
                    "INSERT INTO evidence (intake_id, field_path, value_json, "
                    "source_pointer_json, extracted_at, confirmed_by, confirmed_at, "
                    "selected_patient_id, supersedes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry.intake_id, entry.field_path,
                     json.dumps(entry.value, default=str),
                     entry.pointer.model_dump_json(exclude_none=True),
                     entry.extracted_at.isoformat(), entry.confirmed_by,
                     entry.confirmed_at.isoformat(), entry.selected_patient_id, entry.supersedes))
        return entries

    def pointer_of(self, row: dict) -> SourcePointer:
        """Rehydrate a row's provenance. Typed on the way out, as it was on the way in."""
        return SourcePointer.model_validate_json(row["source_pointer_json"])
