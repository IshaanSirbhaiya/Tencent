"""The dispatcher sends approved rows and provably cannot send anything else.

`GRAPH_AGENT_AND_MCP.md` build step E: *"Approved drafts send; unapproved cannot, asserted by
test."* These are those tests. The transport records every call, so a refusal that quietly sent
anyway would be visible rather than inferred from an exception type.
"""
from __future__ import annotations

import pytest

from databridge_outbox.models import NotApproved

from dispatcher import Dispatcher, SendReceipt, build_dispatcher

NURSE = "Nurse Lim"


# --- nothing sends without a named approver -------------------------------------------


def test_a_drafted_row_is_not_sent(store, draft, transport):
    """THE test for this app. The queue is empty, and asking directly is refused."""
    dispatcher = Dispatcher(store, transport)
    assert dispatcher.run_once() == []
    with pytest.raises(NotApproved, match="named human"):
        dispatcher.dispatch(draft.draft_id)
    assert transport.sent == [], "the transport was called for a draft nobody approved"
    assert store.current(draft.draft_id).status == "drafted"


def test_a_rejected_row_is_not_sent(store, draft, transport):
    store.reject(draft.draft_id, rejected_by=NURSE, reason="the patient asked us to phone")
    dispatcher = Dispatcher(store, transport)
    assert dispatcher.run_once() == []
    with pytest.raises(NotApproved):
        dispatcher.dispatch(draft.draft_id)
    assert transport.sent == []


def test_an_approved_row_with_no_named_approver_is_not_sent(store, draft, transport):
    """A row the API cannot produce, forged straight into the database.

    "Approved" has to mean "a named human approved it", or it is a string in a column. The
    dispatcher checks both halves and so does `OutboxStore.record_sent`; this proves the first.
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

    dispatcher = Dispatcher(store, transport)
    assert dispatcher.run_once() == []
    with pytest.raises(NotApproved):
        dispatcher.dispatch(draft.draft_id)
    assert transport.sent == []


def test_an_unknown_draft_is_refused_rather_than_treated_as_nothing_to_do(store, transport):
    with pytest.raises(NotApproved, match="no draft"):
        Dispatcher(store, transport).dispatch("draft_nope")


def test_the_gate_is_one_named_check_a_test_can_call_directly(store, draft, transport):
    entry = store.current(draft.draft_id)
    with pytest.raises(NotApproved):
        Dispatcher.refuse_unless_approved(entry)
    approved = store.approve(draft.draft_id, approved_by=NURSE)
    Dispatcher.refuse_unless_approved(approved)          # does not raise


# --- approved rows do send --------------------------------------------------------------


def test_an_approved_row_sends_and_is_recorded(store, draft, transport):
    store.approve(draft.draft_id, approved_by=NURSE)
    receipts = Dispatcher(store, transport).run_once()

    assert [r.draft_id for r in receipts] == [draft.draft_id]
    assert len(transport.sent) == 1
    sent = transport.sent[0]
    assert sent["to"] == draft.recipient.address
    assert sent["subject"] == draft.subject.value
    assert sent["body"] == draft.body.value

    row = store.current(draft.draft_id)
    assert row.status == "sent"
    assert row.message_id == receipts[0].message_id
    assert row.approved_by == NURSE, "the sent row must still name who let it out"
    assert row.recorded_by == "dispatcher:recording"


def test_a_receipt_says_what_left_and_who_approved_it(store, draft, transport):
    store.approve(draft.draft_id, approved_by=NURSE)
    receipt, = Dispatcher(store, transport).run_once()
    assert isinstance(receipt, SendReceipt)
    assert receipt.approved_by == NURSE
    assert receipt.to == draft.recipient.address
    assert receipt.transport == "recording"
    assert NURSE in receipt.describe() and draft.draft_id in receipt.describe()


def test_a_sent_row_is_not_sent_a_second_time(store, draft, transport):
    store.approve(draft.draft_id, approved_by=NURSE)
    dispatcher = Dispatcher(store, transport)
    dispatcher.run_once()
    assert dispatcher.run_once() == []
    assert len(transport.sent) == 1


def test_only_the_approved_rows_go(store, draft, transport, intake, ledger, chit_text):
    """A queue with one approved row and one not sends exactly one message."""
    from databridge_core import GroundedValue, SourcePointer
    from databridge_outbox.drafting import build_outbound_draft
    from databridge_outbox.monitors import Observation, load_monitors, monitors_path
    from databridge_outbox.recipients import recipient_from_committed_ledger

    start = chit_text.index("PEE226")
    pointer = SourcePointer(source_id="chit_07_mol_authorisation_pee226.txt", kind="doc_span",
                            document_id="chit_07_mol_authorisation_pee226.txt",
                            char_start=start, char_end=start + len("PEE226"))
    second = build_outbound_draft(
        load_monitors(monitors_path()).by_id("questionnaire_outstanding"),
        Observation(intake_id=intake.intake_id, facts={
            "questionnaire_name": GroundedValue(value="PEE226", pointer=pointer)}),
        recipient=recipient_from_committed_ledger(intake.intake_id, ledger))
    store.record_draft(second)
    store.approve(draft.draft_id, approved_by=NURSE)

    receipts = Dispatcher(store, transport).run_once()
    assert [r.draft_id for r in receipts] == [draft.draft_id]
    assert store.current(second.draft_id).status == "drafted"


# --- failure behaviour -------------------------------------------------------------------


def test_a_transport_failure_leaves_the_row_approved_for_the_next_run(
        store, draft, failing_transport, transport):
    """A mail server that was briefly down must not lose the message, and must not mark it sent."""
    store.approve(draft.draft_id, approved_by=NURSE)
    with pytest.raises(RuntimeError, match="every approved draft failed"):
        Dispatcher(store, failing_transport).run_once()
    assert store.current(draft.draft_id).status == "approved"

    receipts = Dispatcher(store, transport).run_once()
    assert [r.draft_id for r in receipts] == [draft.draft_id]


# --- wiring ---------------------------------------------------------------------------------


def test_the_dispatcher_refuses_an_in_memory_outbox(monkeypatch, transport):
    """The default in-memory store belongs to the process that made it, and the dispatcher is a
    different process. Saying so out loud beats a dispatcher that silently finds nothing to do."""
    monkeypatch.delenv("DATABRIDGE_OUTBOX_DB", raising=False)
    with pytest.raises(RuntimeError, match="DIFFERENT PROCESS"):
        build_dispatcher(transport=transport)


def test_the_dispatcher_reads_an_outbox_another_process_wrote(tmp_path, draft, transport):
    from databridge_outbox.store import OutboxStore

    path = str(tmp_path / "outbox.sqlite")
    writer = OutboxStore(path)
    writer.record_draft(draft)
    writer.approve(draft.draft_id, approved_by=NURSE)

    receipts = build_dispatcher(db_path=path, transport=transport).run_once()
    assert [r.draft_id for r in receipts] == [draft.draft_id]
    assert OutboxStore(path).current(draft.draft_id).status == "sent"
