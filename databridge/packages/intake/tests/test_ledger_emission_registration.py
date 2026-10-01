"""Emission registration — the evidence ledger's leg of the reverse pointer index's third table.

Until a store called `record_emission`, the index's cascade named graph node/edge versions and
**zero emitted records** (STATUS.md, `packages/graph/databridge_graph/ptr_index.py`). `commit()`
now registers each appended row with an `EmissionSink` if one was supplied, flagged ledger-bound —
see `ledger.py`'s `EmissionSink` Protocol and the comment in `commit()` for exactly what "inside
the same transaction" can and cannot mean across two independent SQLite databases.

These tests exercise that against a REAL `GraphStore` and a REAL `ReverseIndex`, imported here —
never from `databridge_intake` itself, which must never import `databridge_graph`
(`test_history.py::test_packages_intake_never_imports_the_graph`, and the circular-dependency
reason behind it: `packages/graph` imports `packages/intake`).
"""
from __future__ import annotations

import datetime as dt
import json

from databridge_core import SourcePointer
from databridge_graph import Answer, GraphStore

from databridge_intake.ledger import EvidenceLedger, subject_fingerprint_for
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake

STAFF = "staff:front_desk_01"
NOW = dt.datetime(2026, 8, 16, 9, 0, tzinfo=dt.timezone.utc)


def _draft(name: str = "chit_02_everwell_voucher.txt"):
    path = fixtures_dir() / "chits" / name
    return run_intake(path.read_text(encoding="utf-8"), document_id=name)


def _attested(draft) -> ConfirmationState:
    patient_id = draft.resolution.candidates[0].patient_id
    return (ConfirmationState(intake_id=draft.intake_id)
            .attest_identity_checked(STAFF, NOW)
            .select_patient(patient_id, subject_fingerprint=subject_fingerprint_for(
                draft.record, draft.resolution, patient_id)))


def _subject(draft) -> str:
    return subject_fingerprint_for(draft.record, draft.resolution,
                                   draft.resolution.candidates[0].patient_id)


# --- discoverability ----------------------------------------------------------------------


def test_a_committed_evidence_row_is_discoverable_through_impact_of():
    store = GraphStore()
    ledger = EvidenceLedger(emission_sink=store.reverse_index)
    draft = _draft()

    entries = ledger.commit(draft.record, _attested(draft), subject_fingerprint=_subject(draft))
    assert entries

    report = store.reverse_index.impact_of(pointer=entries[0].pointer)
    assert report.answer is Answer.KNOWN
    assert len(report.ledger_bound) == 1
    assert report.ledger_bound[0].kind == "ledger_evidence"
    assert report.ledger_bound[0].ref == f"evidence:{_rowid_of(ledger, entries[0])}"
    # Ledger rows are ledger-bound records, never "live" emissions — the cascade must not be able
    # to confuse the two buckets and mark a historical fact stale.
    assert report.emissions == ()


def _rowid_of(ledger: EvidenceLedger, entry) -> int:
    row = next(r for r in ledger.entries(entry.intake_id) if r["field_path"] == entry.field_path)
    return int(row["rowid_pk"])


# --- the demo moment: fan-out to emitted records, not only graph versions -----------------


def test_the_fan_out_from_a_drifted_source_reaches_every_committed_row():
    """This is the number the demo shows: a source drifts, and the cascade names not only graph
    versions but every evidence row committed while believing it.

    A chit's fields do not all cite the SAME source: most are `doc_span`s into the chit itself,
    a few (`visit_type`, `issuer_kind`, `billing_party`) are `table_cell`s into
    `coverage_rules.yaml`. The fan-out claim is scoped to the document's own citations — the ones
    that would actually be affected if THIS document were re-read differently — and every one of
    them must be reachable."""
    store = GraphStore()
    ledger = EvidenceLedger(emission_sink=store.reverse_index)
    document_id = "chit_01_meridian_referral.txt"
    draft = _draft(document_id)

    entries = ledger.commit(draft.record, _attested(draft), subject_fingerprint=_subject(draft))
    doc_entries = [e for e in entries if e.pointer.source_id == document_id]
    assert len(doc_entries) >= 3, "need a multi-row commit for a fan-out to be worth measuring"

    report = store.reverse_index.impact_of(source_id=document_id)
    assert report.answer is Answer.KNOWN
    assert len(report.ledger_bound) == len(doc_entries), (
        f"expected all {len(doc_entries)} committed rows citing {document_id!r} reachable from "
        f"the drifted source, got {len(report.ledger_bound)} — the fan-out is supposed to reach "
        f"EMITTED RECORDS, not stop at graph versions")


# --- ledger-bound is structural, and the cascade must honour it ---------------------------


def test_a_ledger_bound_emission_is_returned_by_the_cascade_and_never_mutated():
    store = GraphStore()
    ledger = EvidenceLedger(emission_sink=store.reverse_index)
    draft = _draft()

    entries = ledger.commit(draft.record, _attested(draft), subject_fingerprint=_subject(draft))
    entry = entries[0]

    report = store.reverse_index.cascade("drift_material", pointer=entry.pointer)
    assert len(report.impact.ledger_bound) == 1
    assert report.impact.ledger_bound[0].stale_since is None, (
        "a ledger-bound emission must never be marked stale — it is historical fact, and a "
        "correction is a new ledger entry, not an edit to this one")
    assert report.ledger_entries_untouched == 1
    assert report.emissions_marked_stale == 0

    # The ledger's own append-only guarantee holds independently of the cascade — a second commit
    # attempt against this intake still refuses, and the row the cascade returned is still the one
    # `commit()` wrote (nothing in `cascade()` reaches back into the ledger's own database).
    row = next(r for r in ledger.entries(draft.intake_id) if r["field_path"] == entry.field_path)
    assert row["value_json"] == json.dumps(entry.value, default=str)


# --- honesty survives registration ---------------------------------------------------------


def test_a_bypassed_write_still_reports_unknown_with_the_exact_count():
    """Registering the ledger's emissions must not create a false 'nothing affected' for a pointer
    some OTHER, still-unregistered write path wrote. The anti-join the whole honesty property
    rests on must still see it."""
    store = GraphStore()
    ledger = EvidenceLedger(emission_sink=store.reverse_index)
    draft = _draft()
    ledger.commit(draft.record, _attested(draft), subject_fingerprint=_subject(draft))

    # A write that bypassed indexing entirely — straight into `sources`, no binding — exactly the
    # way `test_ptr_index.py` simulates "a path that bypassed the hook".
    bypassed = SourcePointer(source_id="bypassed_source", kind="doc_span",
                             document_id="bypassed_source", char_start=0, char_end=5)
    store.conn.execute(
        "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) VALUES (?,?,?,?)",
        ("some:entity", None, None, bypassed.model_dump_json()))
    store.conn.commit()

    answer, reason, missing = store.reverse_index.coverage()
    assert answer is Answer.UNKNOWN
    assert missing == 1
    assert "never indexed" in reason


# --- backward compatibility: no sink, no behaviour change -----------------------------------


def test_a_ledger_with_no_sink_commits_exactly_as_before():
    draft = _draft()
    ledger = EvidenceLedger()          # every existing call site: no emission_sink argument
    entries = ledger.commit(draft.record, _attested(draft), subject_fingerprint=_subject(draft))
    assert entries
    assert ledger.count(draft.intake_id) == len(entries)
