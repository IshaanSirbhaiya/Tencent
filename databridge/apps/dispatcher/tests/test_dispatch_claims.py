"""The dispatcher sends only what its lease names (R-028 OUTX, Codex #3 CRITICAL).

A sweep started for one claim sends exactly the rows that claim selected, even when another
process approves a different row while the sweep runs; and no two dispatchers send one row.
"""
from __future__ import annotations

import pytest

from databridge_outbox.store import ClaimLost, OutboxStore

from dispatcher import Dispatcher, main

NURSE, OTHER_NURSE = "Nurse Lim", "Nurse Tan"


@pytest.fixture()
def second(intake, ledger, chit_text):
    from databridge_core import GroundedValue, SourcePointer
    from databridge_outbox.drafting import build_outbound_draft
    from databridge_outbox.monitors import Observation, load_monitors, monitors_path
    from databridge_outbox.recipients import recipient_from_committed_ledger

    start = chit_text.index("PEE226")
    pointer = SourcePointer(source_id="chit_07_mol_authorisation_pee226.txt", kind="doc_span",
                            document_id="chit_07_mol_authorisation_pee226.txt",
                            char_start=start, char_end=start + len("PEE226"))
    return build_outbound_draft(
        load_monitors(monitors_path()).by_id("questionnaire_outstanding"),
        Observation(intake_id=intake.intake_id, facts={
            "questionnaire_name": GroundedValue(value="PEE226", pointer=pointer)}),
        recipient=recipient_from_committed_ledger(intake.intake_id, ledger))


@pytest.fixture()
def path(tmp_path, draft, second) -> str:
    p = str(tmp_path / "outbox.sqlite")
    s = OutboxStore(p)
    s.record_draft(draft)
    s.record_draft(second)
    return p


def test_a_claimed_sweep_sends_only_its_claim(path, draft, second, transport):
    """The finding's interleaving, across connections: Alice's draft is claimed; Bob's is
    approved by another process afterwards; the sweep for Alice's claim sends Alice's only."""
    gateway, other_worker = OutboxStore(path), OutboxStore(path)
    gateway.approve(draft.draft_id, approved_by=NURSE)
    claim = gateway.claim_for_dispatch(claimed_by=NURSE, draft_ids=[draft.draft_id])
    other_worker.approve(second.draft_id, approved_by=OTHER_NURSE)

    receipts = Dispatcher(OutboxStore(path), transport).run_once(claim_id=claim.claim_id)
    assert [r.draft_id for r in receipts] == [draft.draft_id]
    assert len(transport.sent) == 1
    after = OutboxStore(path)
    assert after.current(second.draft_id).status == "approved"
    assert after.claimed(claim.claim_id) == []                    # released when it ended


def test_a_row_leased_to_another_dispatch_is_not_sent(path, draft, transport):
    s = OutboxStore(path)
    s.approve(draft.draft_id, approved_by=NURSE)
    held = s.claim_for_dispatch(claimed_by="another", draft_ids=[draft.draft_id])
    d = Dispatcher(OutboxStore(path), transport)
    assert d.run_once() == []                                     # the poll skips leased rows
    with pytest.raises(ClaimLost):
        d.dispatch(draft.draft_id)
    with pytest.raises(ClaimLost):
        d.dispatch(draft.draft_id, claim_id="claim-forged")
    assert transport.sent == []
    s.release_claim(held.claim_id)
    assert [r.draft_id for r in d.run_once()] == [draft.draft_id]


def test_a_failed_send_releases_its_lease_for_the_next_run(path, draft, failing_transport,
                                                             transport):
    s = OutboxStore(path)
    s.approve(draft.draft_id, approved_by=NURSE)
    claim = s.claim_for_dispatch(claimed_by=NURSE, draft_ids=[draft.draft_id])
    with pytest.raises(RuntimeError):
        Dispatcher(OutboxStore(path), failing_transport).run_once(claim_id=claim.claim_id)
    assert s.current(draft.draft_id).status == "approved"
    assert s.claimed(claim.claim_id) == []
    assert [r.draft_id for r in Dispatcher(s, transport).run_once()] == [draft.draft_id]


def test_the_command_line_takes_a_claim(path, draft, second, transport, monkeypatch):
    """``--claim`` is the single-draft mode the gateway starts: one claim, its rows only."""
    import dispatcher as mod

    s = OutboxStore(path)
    s.approve(draft.draft_id, approved_by=NURSE)
    s.approve(second.draft_id, approved_by=OTHER_NURSE)
    claim = s.claim_for_dispatch(claimed_by=NURSE, draft_ids=[draft.draft_id])
    monkeypatch.setattr(mod, "build_dispatcher",
                        lambda db_path=None, transport=None, _t=transport:
                        Dispatcher(OutboxStore(db_path), _t))
    assert main(["--once", "--db", path, "--claim", claim.claim_id]) == 0
    assert [m["subject"] for m in transport.sent] == [draft.subject.value]
    assert s.current(second.draft_id).status == "approved"
