"""The two doors an address may come through. There is no third, and that is the whole file.

``models.Recipient`` refuses construction without a token that lives here, so this module is
literally the only way a draft acquires an addressee. Everything below is therefore a statement
about who the clinic is allowed to write to:

**Door (a) — a committed evidence-ledger row.** ``recipient_from_committed_ledger`` reads
``EvidenceLedger.current(intake_id)``, finds the ``selected_patient_id`` a named human attested to
in person, and resolves it against the patient registry. It takes no address argument. The address
it produces is the one already on file for the person a human said this is.

**Door (b) — a human types it.** ``recipient_typed_by_human`` takes an address and the name of the
person typing it, and records that act. The address is arbitrary, which is exactly why the *actor*
is not: the provenance of the resulting draft says "Nurse Lim typed this at 09:14", which is a
sentence somebody can be held to.

**What is deliberately absent.** There is no ``recipient_from_document``, no
``recipient_from_extracted_field``, and no function taking a candidate list. A chit letter that
prints ``patient@example.com`` does not thereby become a licence to write to it: the document is
attacker-controlled text, and "the email address on the referral letter" is a claim about identity
made by whoever wrote the letter. If a clinic really must write to an address a document names, a
human reads it and types it — door (b) — and the record then says a human did that.

The conflict check in ``recipient_from_committed_ledger`` is the same one
``intake.claim._selected_patient`` makes, for the same reason and not by accident: the ledger is
append-only, so a correction is a new row, and nothing stops two live rows naming different
selected patients. Taking the newest and moving on would be the machine resolving a contradiction
about *who someone is*. It stops and asks instead — and here the stakes are one notch higher than
in ``claim.py``, because the output of this function is an address a message actually goes to.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Mapping, Optional

from databridge_core import SourcePointer
from databridge_intake.ledger import EvidenceLedger
from databridge_intake.models import PatientRecord
from databridge_intake.registry import by_patient_id

from .models import (
    FRONT_DESK_SOURCE_PREFIX,
    FRONT_DESK_TABLE,
    LEDGER_SOURCE_PREFIX,
    Recipient,
    RecipientRefused,
    _RECIPIENT_TOKEN,
    refuse_machine_actor,
)

#: The ledger table and column a selection is recorded in. Same names ``claim.py`` cites, so a
#: pointer produced here and a pointer produced there resolve to the same place.
LEDGER_TABLE = "evidence"
SELECTION_COLUMN = "selected_patient_id"


class NoCommittedSelection(RecipientRefused):
    """Nothing is committed for this intake, or no committed row names a selected patient."""


class ConflictingSelections(RecipientRefused):
    """The live rows name more than one selected patient. A human decides which; the system will
    not, and certainly not by picking the most recent one and mailing it."""


class NoAddressOnFile(RecipientRefused):
    """The person a human selected has no usable address in the registry."""


def _selection_row(intake_id: str, ledger: EvidenceLedger) -> dict:
    rows = ledger.current(intake_id)
    if not rows:
        raise NoCommittedSelection(
            f"no confirmed evidence for intake {intake_id!r}: a recipient comes from the ledger, "
            f"and nothing has been committed. Nothing is drafted to nobody.")
    selections = {r[SELECTION_COLUMN] for r in rows.values() if r[SELECTION_COLUMN]}
    if len(selections) > 1:
        raise ConflictingSelections(
            f"the committed evidence for intake {intake_id!r} names more than one selected "
            f"patient ({', '.join(sorted(selections))}). No message is addressed until a human "
            f"establishes which record this encounter belongs to; the system does not choose "
            f"between them (CLAUDE.md §Hard constraints 1).")
    row = max((r for r in rows.values() if r[SELECTION_COLUMN]),
              key=lambda r: r["rowid_pk"], default=None)
    if row is None:
        raise NoCommittedSelection(
            f"intake {intake_id!r} has committed evidence but no row records a patient a human "
            f"selected, so there is nobody this message is established to be about")
    return row


def recipient_from_committed_ledger(
        intake_id: str,
        ledger: EvidenceLedger,
        *,
        registry: Optional[Mapping[str, PatientRecord]] = None) -> Recipient:
    """Door (a). The address on file for the patient a named human attested to, in person.

    **Takes no address.** The parameter list is the assertion — there is no argument through which
    a caller (or a model whose output a caller passed along) could supply one, so this function can
    only ever produce an address the clinic already held for a person a human already selected.

    The pointer is ``derived`` over two citations, and both are needed to describe what happened:
    the ledger row (a named human checked an NRIC against a face and picked this record) and the
    registry cell (this is the address written against that record). Citing only the registry would
    imply the system knows whose address it is; citing only the ledger would imply the address came
    from the attestation. Neither is true on its own.
    """
    row = _selection_row(intake_id, ledger)
    patient_id = row[SELECTION_COLUMN]
    table = registry if registry is not None else by_patient_id()
    patient = table.get(patient_id)
    if patient is None:
        raise NoAddressOnFile(
            f"the committed selection for intake {intake_id!r} names patient {patient_id!r}, "
            f"which is not in the registry. The system does not search for a near match — that "
            f"would be choosing who this is (CLAUDE.md §Hard constraints 1).")
    address = str(patient.email.value or "").strip()
    if not address:
        raise NoAddressOnFile(
            f"patient {patient_id!r} has no email address on file, so there is no address to write "
            f"to. Somebody at the counter can ask for one and type it (door (b)); nothing here "
            f"guesses one from a name or a document.")

    ledger_pointer = SourcePointer(
        source_id=f"{LEDGER_SOURCE_PREFIX}{intake_id}", kind="table_cell",
        table=LEDGER_TABLE, column=SELECTION_COLUMN, row_id=str(row["rowid_pk"]))
    pointer = SourcePointer(
        source_id=f"{LEDGER_SOURCE_PREFIX}{intake_id}", kind="derived",
        derivation=(
            f"the address on file for patient {patient_id}, which {row['confirmed_by']} selected "
            f"after an in-person identity check on {row['confirmed_at']}. The clinic never "
            f"verified this identity; a named human did, and this cites the row that records it"),
        derived_from=[ledger_pointer, patient.email.pointer])
    return Recipient(via_=_RECIPIENT_TOKEN, address=address, origin="committed_ledger",
                     chosen_by=row["confirmed_by"], pointer=pointer)


def recipient_typed_by_human(address: str,
                             *,
                             typed_by: str,
                             typed_at: Optional[dt.datetime] = None) -> Recipient:
    """Door (b). A named member of staff typed this address, and the record says so.

    ``typed_by`` has no default and may not be a process name. That is the entire safety property:
    an arbitrary address is fine as long as a person owns having entered it, and this function
    cannot be called in a way that leaves nobody owning it.

    The pointer is a ``table_cell`` into this store's own front-desk entry table — self-referential,
    like ``claim.py``'s pointer at ``evidence.selected_patient_id``, because the record of the act
    IS the provenance and there is nowhere more authoritative to point.
    """
    who = refuse_machine_actor(typed_by, what="recipient_typed_by_human(typed_by=...)")
    when = typed_at or dt.datetime.now(dt.timezone.utc)
    entry_id = hashlib.sha256(
        "\x00".join([address, who, when.isoformat()]).encode("utf-8")).hexdigest()[:24]
    pointer = SourcePointer(
        source_id=f"{FRONT_DESK_SOURCE_PREFIX}{who}", kind="table_cell",
        table=FRONT_DESK_TABLE, column="address", row_id=entry_id)
    return Recipient(via_=_RECIPIENT_TOKEN, address=address, origin="typed_by_human",
                     chosen_by=who, pointer=pointer)


__all__ = [
    "ConflictingSelections",
    "NoAddressOnFile",
    "NoCommittedSelection",
    "recipient_from_committed_ledger",
    "recipient_typed_by_human",
]
