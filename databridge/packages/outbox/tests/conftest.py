"""Fixtures shared by the outbox tests: a real intake, really committed, by a real attestation.

Nothing here fakes the ledger. The recipient rule is about *committed evidence*, so a test that
stubbed the ledger would be testing a different rule than the one that ships — the same reason
`test_capability_boundary.py` runs its tools against a real seeded graph rather than a mock.
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_core import GroundedValue, SourcePointer
from databridge_intake.ledger import EvidenceLedger, subject_fingerprint_for
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake
from databridge_outbox.monitors import Observation, load_monitors, monitors_path

STAFF = "staff:front_desk_01"
NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)
CHIT = "chit_07_mol_authorisation_pee226.txt"


@pytest.fixture(scope="session")
def chit_text() -> str:
    return (fixtures_dir() / "chits" / CHIT).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def intake(chit_text):
    return run_intake(chit_text, document_id=CHIT)


@pytest.fixture()
def ledger(intake) -> EvidenceLedger:
    """A ledger with this intake committed behind a named human's in-person attestation."""
    patient_id = intake.resolution.candidates[0].patient_id
    fingerprint = subject_fingerprint_for(intake.record, intake.resolution, patient_id)
    confirmation = (ConfirmationState(intake_id=intake.intake_id)
                    .attest_identity_checked(STAFF, NOW)
                    .select_patient(patient_id, subject_fingerprint=fingerprint))
    store = EvidenceLedger()
    store.commit(intake.record, confirmation, subject_fingerprint=fingerprint, at=NOW)
    return store


@pytest.fixture(scope="session")
def table():
    return load_monitors(monitors_path())


def doc_pointer(value: str, text: str) -> SourcePointer:
    """A ``doc_span`` that really does slice to ``value``, so a grounding proof means something."""
    start = text.index(value)
    return SourcePointer(source_id=CHIT, kind="doc_span", document_id=CHIT,
                         char_start=start, char_end=start + len(value))


@pytest.fixture()
def coverage_observation(intake, chit_text) -> Observation:
    """Facts for ``coverage_conflict_on_file``, each grounded in a real span of the real chit."""
    return Observation(intake_id=intake.intake_id, facts={
        "unauthorised_test": GroundedValue(
            value="Electrocardiogram (ECG)",
            pointer=doc_pointer("Electrocardiogram (ECG)", chit_text)),
        "package_code": GroundedValue(value="PEE226",
                                      pointer=doc_pointer("PEE226", chit_text)),
    })
