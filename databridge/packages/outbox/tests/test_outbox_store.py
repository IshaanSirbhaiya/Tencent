"""The outbox is the human-confirmation gate, applied outward — and append-only like the ledger.

Two properties, tested the way `packages/intake/tests` tests them for the evidence ledger:

* **nothing sends without a named approver** — asserted directly, and asserted again against a
  row hand-forged into the database rather than built through the API, because the API is not the
  only thing that will ever write here;
* **history is not rewritten** — the triggers are exercised, not assumed.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from databridge_outbox.drafting import build_outbound_draft
from databridge_outbox.models import NotApproved
from databridge_outbox.recipients import recipient_from_committed_ledger
from databridge_outbox.store import (
    DuplicateDraft,
    IllegalTransition,
    OutboxStore,
    UnknownDraft,
)

NURSE = "Nurse Lim"


@pytest.fixture()
def draft(table, coverage_observation, intake, ledger):
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    return build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                recipient=recipient,
                                created_at=dt.datetime(2026, 8, 12, tzinfo=dt.timezone.utc))


@pytest.fixture()
def store(draft) -> OutboxStore:
    out = OutboxStore()
    out.record_draft(draft)
    return out


# --- nothing sends without a named approver ------------------------------------------


def test_a_drafted_row_cannot_be_recorded_as_sent(store, draft):
    """THE test. A draft nobody approved cannot become a send, whoever asks."""
    with pytest.raises(NotApproved, match="named human"):
        store.record_sent(draft.draft_id, message_id="<x@clinic>")
    assert store.current(draft.draft_id).status == "drafted"


def test_a_rejected_row_cannot_be_recorded_as_sent(store, draft):
    store.reject(draft.draft_id, rejected_by=NURSE, reason="the patient asked us to phone instead")
    with pytest.raises(NotApproved):
        store.record_sent(draft.draft_id, message_id="<x@clinic>")


@pytest.mark.parametrize("who", ["agent", "system", "walnut", "", "   ", "dispatcher", "AI"])
def test_a_machine_cannot_approve(store, draft, who):
    with pytest.raises(NotApproved, match="named person"):
        store.approve(draft.draft_id, approved_by=who)
    assert store.current(draft.draft_id).status == "drafted"


def test_a_hand_forged_approved_row_with_no_approver_is_not_dispatchable(store, draft):
    """The API cannot produce this row. Something else might.

    `approved_by` NULL on an `approved` row is a row that says a human agreed and cannot say which
    one — and a row that cannot name its approver has not been approved. Both `is_dispatchable`
    and `record_sent` have to see that, or "approved" becomes a string in a column.
    """
    live = store.current(draft.draft_id)
    store.conn.execute(
        "INSERT INTO outbox (draft_id, status, channel, recipient_address, recipient_origin, "
        "recipient_chosen_by, recipient_pointer_json, subject, body, intake_id, playbook_id, "
        "because_json, created_at, recorded_at, recorded_by, supersedes) "
        "VALUES (?, 'approved', 'email', ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'forged', ?)",
        (live.draft_id, live.recipient_address, live.recipient_origin, live.recipient_chosen_by,
         live.recipient_pointer.model_dump_json(exclude_none=True), live.subject, live.body,
         live.intake_id, live.playbook_id, live.created_at, live.recorded_at, live.rowid))
    store.conn.commit()

    forged = store.current(draft.draft_id)
    assert forged.status == "approved" and forged.approved_by is None
    assert forged.is_dispatchable is False
    assert store.awaiting_dispatch() == []
    with pytest.raises(NotApproved):
        store.record_sent(draft.draft_id, message_id="<x@clinic>")


def test_an_approved_row_is_dispatchable_and_records_its_send(store, draft):
    approved = store.approve(draft.draft_id, approved_by=NURSE)
    assert approved.is_dispatchable
    assert [e.draft_id for e in store.awaiting_dispatch()] == [draft.draft_id]

    sent = store.record_sent(draft.draft_id, message_id="<abc@walnut.clinic.invalid>")
    assert sent.status == "sent"
    assert sent.message_id == "<abc@walnut.clinic.invalid>"
    # The approval is carried onto the sent row: "who let this out" must be answerable from the
    # row that says it left.
    assert sent.approved_by == NURSE and sent.approved_at
    assert store.awaiting_dispatch() == []


def test_a_send_with_no_message_id_is_refused(store, draft):
    store.approve(draft.draft_id, approved_by=NURSE)
    with pytest.raises(Exception, match="message id"):
        store.record_sent(draft.draft_id, message_id="  ")


# --- append-only ---------------------------------------------------------------------


def test_the_database_rejects_update(store):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE outbox SET status = 'approved'")


def test_the_database_rejects_delete(store):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("DELETE FROM outbox")


def test_a_state_change_is_a_new_row_and_the_old_one_stays(store, draft):
    store.approve(draft.draft_id, approved_by=NURSE)
    store.record_sent(draft.draft_id, message_id="<abc@clinic>")
    history = store.history(draft.draft_id)
    assert [h.status for h in history] == ["drafted", "approved", "sent"]
    assert [h.supersedes for h in history] == [None, history[0].rowid, history[1].rowid]
    # The text as approved is on the approval row, not fetched through a join that could move.
    assert history[1].body == history[0].body == draft.body.value


def test_the_database_refuses_a_second_sent_row_for_one_draft(store, draft):
    """`ix_outbox_sent_once`. The dispatcher checks before sending; this is what holds when two
    dispatchers run at once."""
    store.approve(draft.draft_id, approved_by=NURSE)
    live = store.record_sent(draft.draft_id, message_id="<abc@clinic>")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO outbox (draft_id, status, channel, recipient_address, recipient_origin, "
            "recipient_chosen_by, recipient_pointer_json, subject, body, playbook_id, "
            "because_json, created_at, recorded_at, recorded_by) "
            "VALUES (?, 'sent', 'email', ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'forged')",
            (live.draft_id, live.recipient_address, live.recipient_origin,
             live.recipient_chosen_by,
             live.recipient_pointer.model_dump_json(exclude_none=True), live.subject, live.body,
             live.playbook_id, live.created_at, live.recorded_at))


def test_the_database_refuses_two_rows_superseding_the_same_row(store, draft):
    """A fork would give one draft two live states, and `current()` would resolve
    "approved or rejected?" by insertion order."""
    live = store.current(draft.draft_id)
    store.approve(draft.draft_id, approved_by=NURSE)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO outbox (draft_id, status, channel, recipient_address, recipient_origin, "
            "recipient_chosen_by, recipient_pointer_json, subject, body, playbook_id, "
            "because_json, created_at, recorded_at, recorded_by, supersedes) "
            "VALUES (?, 'rejected', 'email', ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'forged', ?)",
            (live.draft_id, live.recipient_address, live.recipient_origin,
             live.recipient_chosen_by,
             live.recipient_pointer.model_dump_json(exclude_none=True), live.subject, live.body,
             live.playbook_id, live.created_at, live.recorded_at, live.rowid))


# --- the state machine ----------------------------------------------------------------


def test_an_approved_draft_cannot_be_approved_again(store, draft):
    store.approve(draft.draft_id, approved_by=NURSE)
    with pytest.raises(IllegalTransition):
        store.approve(draft.draft_id, approved_by=NURSE)


def test_a_sent_draft_is_terminal(store, draft):
    store.approve(draft.draft_id, approved_by=NURSE)
    store.record_sent(draft.draft_id, message_id="<abc@clinic>")
    with pytest.raises(IllegalTransition, match="terminal"):
        store.reject(draft.draft_id, rejected_by=NURSE, reason="too late")


def test_a_rejected_draft_is_terminal(store, draft):
    store.reject(draft.draft_id, rejected_by=NURSE, reason="wrong patient — I will phone them")
    with pytest.raises(IllegalTransition, match="terminal"):
        store.approve(draft.draft_id, approved_by=NURSE)


def test_a_rejection_needs_a_reason(store, draft):
    with pytest.raises(Exception, match="reason"):
        store.reject(draft.draft_id, rejected_by=NURSE, reason="  ")


def test_an_unknown_draft_is_raised_not_ignored(store):
    with pytest.raises(UnknownDraft):
        store.approve("draft_nope", approved_by=NURSE)
    with pytest.raises(UnknownDraft):
        store.record_sent("draft_nope", message_id="<x@clinic>")


# --- idempotence ----------------------------------------------------------------------


def test_the_same_monitor_firing_twice_produces_the_same_draft_id(table, coverage_observation,
                                                                  intake, ledger):
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    monitor = table.by_id("coverage_conflict_on_file")
    first = build_outbound_draft(monitor, coverage_observation, recipient=recipient)
    second = build_outbound_draft(monitor, coverage_observation, recipient=recipient)
    assert first.draft_id == second.draft_id, (
        "a monitor polling every five minutes must not give the front desk one row per poll")


def test_filing_the_same_draft_twice_is_refused_rather_than_silently_merged(store, draft):
    with pytest.raises(DuplicateDraft):
        store.record_draft(draft)


def test_a_file_backed_outbox_survives_the_process_that_wrote_it(tmp_path, draft):
    """The dispatcher is a DIFFERENT PROCESS, so the store has to be a file. Proven, not assumed."""
    path = str(tmp_path / "outbox.sqlite")
    writer = OutboxStore(path)
    writer.record_draft(draft)
    writer.approve(draft.draft_id, approved_by=NURSE)

    reader = OutboxStore(path)
    assert [e.draft_id for e in reader.awaiting_dispatch()] == [draft.draft_id]
