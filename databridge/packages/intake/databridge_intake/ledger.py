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

**An attestation is bound to its subject, not to an intake id.** ``intake_id`` is derived from the
document and survives every re-extraction, so a check made about one reading of a document and one
candidate list could authorise a record resolved against a different one. ``subject_fingerprint_for``
digests the document revision, the candidate set and the selected registry row; ``commit`` demands
that digest as a required keyword argument and refuses on any difference (R-17).

Every row carries the ``SourcePointer`` its value came from. A committed fact that cannot say where
it came from is not evidence, so ``EvidenceEntry`` requires one and this module never invents one.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from typing import Iterable, Optional

from databridge_core import SourcePointer

from .models import ConfirmationState, EvidenceEntry, IntakeRecord, ResolutionResult

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


class SubjectChanged(IdentityNotVerified):
    """The attestation was made about a different reading of this document, or a different
    candidate list, than the one being committed.

    A subclass of ``IdentityNotVerified`` on purpose: every existing caller that already fails
    closed on "identity is not verified" keeps failing closed on "identity was verified, but not
    about THIS record" without being taught a new exception type first.
    """


# --- binding an attestation to the thing it was made about --------------------------


#: Bumped if the composition below ever changes. A stored digest from an older scheme then stops
#: matching, which closes the gate rather than silently comparing two different things.
_FINGERPRINT_SCHEME = 1


def subject_fingerprint_for(record: IntakeRecord,
                            resolution: ResolutionResult,
                            selected_patient_id: str) -> str:
    """Digest of *what the human was actually offered, and what they picked out of it*.

    ``sha256(document_id | extracted_at | selected_patient_id | sorted(candidate patient ids))``.

    Each part is there because changing it changes the thing the attestation was about:

    * **document_id + extracted_at** — the document REVISION. Re-uploading a file re-extracts it
      under a fresh ``extracted_at``, so an attestation made about the previous reading no longer
      applies to the new one.
    * **the candidate set** — the list the human chose from. "I checked this person against these
      three rows and it is the second" is not the same act as "…against these three OTHER rows".
    * **selected_patient_id** — which row they picked.

    ``intake_id`` is deliberately NOT in here: it is derived from the document alone and is stable
    across every one of the changes above, which is exactly why binding an attestation to it was
    the bug (R-17, CLAUDE.md §Hard constraints 1).

    Candidate ids are sorted so an unrelated re-ranking does not invalidate an attestation; the
    membership of the set is what the human was offered, not the order it was printed in.
    """
    if not selected_patient_id:
        # No selection means there is no subject to fingerprint. Returning a digest of "nothing"
        # would produce a value that compares equal to another absent selection — a stable
        # fingerprint for "no attestation at all".
        raise ValueError(
            "subject_fingerprint_for requires the patient id a human selected; there is no "
            "subject to bind an attestation to without one")
    payload = json.dumps({
        "scheme": _FINGERPRINT_SCHEME,
        "document_id": record.document.document_id,
        "extracted_at": record.extracted_at.isoformat(),
        "selected_patient_id": selected_patient_id,
        "candidates": sorted(c.patient_id for c in resolution.candidates),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def attestation_subject_matches(record: IntakeRecord,
                                resolution: ResolutionResult,
                                confirmation: ConfirmationState) -> tuple[bool, str]:
    """Whether ``confirmation``'s attestation is still about THIS record and THIS candidate set.

    ``(True, "")`` or ``(False, why)``. Read by every surface that reports an intake as
    identity-verified — the confirm gate, the front-desk queue and the ``/intelligence`` queue
    (R-22) — so that "verified" cannot mean "verified about something else". A single definition,
    because a second one drifts from the gate and the gate is the one that matters.
    """
    ok, why = confirmation.is_ready_to_commit()
    if not ok:
        return False, why
    current = subject_fingerprint_for(record, resolution, confirmation.selected_patient_id)
    if confirmation.subject_fingerprint != current:
        return False, ("this document was re-read, or its candidate list changed, since the "
                       "in-person identity check — the check must be made again against what "
                       "the counter is looking at now")
    return True, ""


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
               subject_fingerprint: Optional[str],
               at: Optional[dt.datetime] = None,
               field_paths: Optional[Iterable[str]] = None) -> list[EvidenceEntry]:
        """Write the record's grounded fields to the ledger. **The gate.**

        Refuses unless a named human has verified identity in person AND selected a candidate AND
        that attestation was made about THIS record. Nothing partial is written on refusal: every
        check happens before the first insert.

        ``subject_fingerprint`` is the digest of the resolution the caller is about to commit
        against — ``subject_fingerprint_for(record, resolution, selected_patient_id)``. It is
        passed in rather than recomputed here because ``commit`` never sees a ``ResolutionResult``:
        it is handed a record and a confirmation, and the candidate list lives on neither.

        It has **no default**, and that is load-bearing. A defaulted parameter would let every
        existing and future call site skip the check by saying nothing, so the guard would protect
        exactly the callers that already knew about it — a guard that fails open. Passing ``None``
        explicitly is allowed and still fails closed: `None` cannot equal a stored fingerprint, and
        a state with no fingerprint has no selection and is refused by the readiness check above.
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
            # KEPT, and it is not the check below in another form. This one catches a confirmation
            # for a DIFFERENT document being handed to this record. It cannot catch a stale
            # attestation for the SAME document, because `intake_id` is derived from the document
            # (`extract_intake`: sha256(document_id + text sha)) and is therefore identical on both
            # sides of a re-extraction — the record is re-read, the confirmation is deliberately
            # retained, and both ids compare equal while the candidate set underneath them has
            # changed. That is R-17: a guard that looks like it is watching this door and is
            # structurally incapable of seeing anything move through it.
            raise LedgerError(
                f"commit REFUSED: confirmation is for intake {confirmation.intake_id!r} but the "
                f"record is {record.intake_id!r}")
        if confirmation.subject_fingerprint != subject_fingerprint:
            # THE check `intake_id` cannot make. The attestation carries a digest of the document
            # revision, the candidate list and the row the human picked; the caller passes the same
            # digest recomputed over what is actually being committed. Any difference means the
            # in-person check was made about a different reading of this document or a different
            # set of candidates, so it does not authorise this commit (CLAUDE.md §Hard constraints
            # 1). The digests are not quoted into the message: they are compared, not read.
            raise SubjectChanged(
                "commit REFUSED: the in-person identity check was made against a different "
                "reading of this document, or a different candidate list, than the one being "
                "committed. Staff must check identity again against what is on the counter now; "
                "the system never carries an attestation across a re-read "
                "(CLAUDE.md §Hard constraints 1).")

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
