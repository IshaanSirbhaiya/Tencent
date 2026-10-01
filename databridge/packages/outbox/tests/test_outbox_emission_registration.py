"""Emission registration — the outbox's leg of the reverse pointer index's third table.

`record_draft` now registers a filed draft's citations — the recipient's provenance AND every
`because` pointer, i.e. exactly what `OutboxStore.pointers_of` already considers "every citation
behind a row" — with an `EmissionSink` if one was supplied. See `store.py`'s `EmissionSink`
Protocol and the comment in `record_draft` for what "inside the same transaction" means here.

Outbox drafts are deliberately NOT ledger-bound: unlike a committed evidence row, a draft the
cascade finds resting on a drifted pointer should be markable stale (a stale draft should not be
approved and sent), so `ledger_bound=False` is asserted here as its own property, not assumed.

Real `GraphStore`, imported here — never from `databridge_outbox` or `databridge_intake`, neither
of which depends on `databridge_graph`.
"""
from __future__ import annotations

from databridge_core import SourcePointer
from databridge_graph import Answer, GraphStore

from databridge_outbox.drafting import build_outbound_draft
from databridge_outbox.recipients import recipient_from_committed_ledger
from databridge_outbox.store import OutboxStore


# --- discoverability ----------------------------------------------------------------------


def test_a_filed_draft_is_discoverable_through_impact_of(table, coverage_observation, intake,
                                                          ledger):
    store = GraphStore()
    outbox = OutboxStore(emission_sink=store.reverse_index)
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    draft = build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                 recipient=recipient)
    outbox.record_draft(draft)

    report = store.reverse_index.impact_of(pointer=draft.recipient.pointer)
    assert report.answer is Answer.KNOWN
    assert len(report.emissions) == 1
    assert report.emissions[0].kind == "outbox_draft"
    assert report.emissions[0].ref == draft.draft_id
    assert report.ledger_bound == (), "an outbox draft is not a ledger-bound record"


# --- the demo moment: fan-out to emitted records, not only graph versions -----------------


def test_the_fan_out_from_a_drifted_because_pointer_reaches_the_draft(table, coverage_observation,
                                                                       intake, ledger):
    """`because` is the load-bearing field (`models.py`): it is why the draft exists, and every
    pointer in it must be reachable, not only the recipient's."""
    store = GraphStore()
    outbox = OutboxStore(emission_sink=store.reverse_index)
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    draft = build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                 recipient=recipient)
    outbox.record_draft(draft)
    assert len(draft.because) >= 2, "need more than the recipient pointer for a fan-out to prove"

    for pointer in draft.because:
        report = store.reverse_index.impact_of(pointer=pointer)
        assert report.answer is Answer.KNOWN
        assert [e.ref for e in report.emissions] == [draft.draft_id], (
            f"a `because` pointer ({pointer.kind}, {pointer.source_id}) did not reach the draft "
            f"it justified")


# --- ledger_bound is a real, structural distinction, not a default -------------------------


def test_an_outbox_draft_can_be_marked_stale_because_it_is_not_ledger_bound(table,
                                                                            coverage_observation,
                                                                            intake, ledger):
    store = GraphStore()
    outbox = OutboxStore(emission_sink=store.reverse_index)
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    draft = build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                 recipient=recipient)
    outbox.record_draft(draft)

    report = store.reverse_index.cascade("drift_material", pointer=draft.recipient.pointer)
    assert report.emissions_marked_stale == 1
    assert report.impact.emissions[0].stale_since is not None
    assert report.ledger_entries_untouched == 0


# --- honesty survives registration ---------------------------------------------------------


def test_a_bypassed_write_still_reports_unknown_with_the_exact_count(table, coverage_observation,
                                                                      intake, ledger):
    store = GraphStore()
    outbox = OutboxStore(emission_sink=store.reverse_index)
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    draft = build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                 recipient=recipient)
    outbox.record_draft(draft)

    bypassed = SourcePointer(source_id="bypassed_source", kind="doc_span",
                             document_id="bypassed_source", char_start=0, char_end=5)
    store.conn.execute(
        "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) VALUES (?,?,?,?)",
        ("some:entity", None, None, bypassed.model_dump_json()))
    store.conn.commit()

    answer, reason, missing = store.reverse_index.coverage()
    assert answer is Answer.UNKNOWN
    assert missing == 1


# --- backward compatibility: no sink, no behaviour change -----------------------------------


def test_a_store_with_no_sink_files_exactly_as_before(table, coverage_observation, intake,
                                                       ledger):
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    draft = build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                 recipient=recipient)
    outbox = OutboxStore()          # every existing call site: no emission_sink argument
    entry = outbox.record_draft(draft)
    assert entry.draft_id == draft.draft_id
