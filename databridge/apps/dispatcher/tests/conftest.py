"""A real committed intake, a real grounded draft, and a transport that only counts.

The recording transport is the point of most of these tests: "unapproved cannot send" is only
proved by something that would have noticed if it had.
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_core import GroundedValue, SourcePointer
from databridge_intake.ledger import EvidenceLedger, subject_fingerprint_for
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake
from databridge_outbox.drafting import build_outbound_draft
from databridge_outbox.monitors import Observation, load_monitors, monitors_path
from databridge_outbox.recipients import recipient_from_committed_ledger
from databridge_outbox.store import OutboxStore

STAFF = "staff:front_desk_01"
NURSE = "Nurse Lim"
NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)
CHIT = "chit_07_mol_authorisation_pee226.txt"


class RecordingTransport:
    """A transport that records what it was asked to send and never opens a socket.

    ``name`` and ``send`` are the whole ``SendTransport`` protocol — which is itself the assertion
    that a transport gets three strings and no ability to decide anything.
    """

    name = "recording"

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    def send(self, *, to: str, subject: str, body: str) -> str:
        if self.fail:
            raise RuntimeError("the mail server is down")
        self.sent.append({"to": to, "subject": subject, "body": body})
        return f"<{len(self.sent)}@recording.invalid>"


@pytest.fixture()
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture()
def failing_transport() -> RecordingTransport:
    """A mail server that is down. The row must survive it, still approved and still unsent."""
    return RecordingTransport(fail=True)


@pytest.fixture(scope="session")
def chit_text() -> str:
    return (fixtures_dir() / "chits" / CHIT).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def intake(chit_text):
    return run_intake(chit_text, document_id=CHIT)


@pytest.fixture()
def ledger(intake) -> EvidenceLedger:
    patient_id = intake.resolution.candidates[0].patient_id
    fingerprint = subject_fingerprint_for(intake.record, intake.resolution, patient_id)
    confirmation = (ConfirmationState(intake_id=intake.intake_id)
                    .attest_identity_checked(STAFF, NOW)
                    .select_patient(patient_id, subject_fingerprint=fingerprint))
    store = EvidenceLedger()
    store.commit(intake.record, confirmation, subject_fingerprint=fingerprint, at=NOW)
    return store


@pytest.fixture()
def draft(intake, ledger, chit_text):
    table = load_monitors(monitors_path())

    def span(value: str) -> SourcePointer:
        start = chit_text.index(value)
        return SourcePointer(source_id=CHIT, kind="doc_span", document_id=CHIT,
                             char_start=start, char_end=start + len(value))

    observation = Observation(intake_id=intake.intake_id, facts={
        "unauthorised_test": GroundedValue(value="Electrocardiogram (ECG)",
                                           pointer=span("Electrocardiogram (ECG)")),
        "package_code": GroundedValue(value="PEE226", pointer=span("PEE226"))})
    return build_outbound_draft(table.by_id("coverage_conflict_on_file"), observation,
                                recipient=recipient_from_committed_ledger(intake.intake_id,
                                                                          ledger))


@pytest.fixture()
def store(draft) -> OutboxStore:
    out = OutboxStore()
    out.record_draft(draft)
    return out
