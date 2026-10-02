"""The dispatch lease: selection, claiming and sending are one database-backed decision (R-028 OUTX).

Codex R-028 #3 (CRITICAL): the gateway's ownership check and the dispatcher's whole-queue sweep
were guarded by an in-process ``RLock``. The SQLite queue is shared by several processes, so a
second gateway worker could approve another principal's draft between "the queue is all yours" and
the sweep, and that draft left with yours.

These tests use SEPARATE ``OutboxStore`` connections on one file, which is what two processes are
to SQLite: nothing in-process (no shared lock, no shared connection) can make them pass.
"""
from __future__ import annotations

import datetime as dt
import threading

import pytest

from databridge_core import GroundedValue
from databridge_outbox.drafting import build_outbound_draft
from databridge_outbox.monitors import Observation
from databridge_outbox.recipients import recipient_from_committed_ledger
from databridge_outbox.store import ClaimLost, OutboxStore

from conftest import doc_pointer

NURSE, OTHER_NURSE = "Nurse Lim", "Nurse Tan"


@pytest.fixture()
def drafts(table, coverage_observation, intake, ledger, chit_text):
    """Two DIFFERENT drafts (two monitors), both addressed through the committed-ledger door."""
    recipient = recipient_from_committed_ledger(intake.intake_id, ledger)
    first = build_outbound_draft(table.by_id("coverage_conflict_on_file"), coverage_observation,
                                 recipient=recipient,
                                 created_at=dt.datetime(2026, 8, 12, tzinfo=dt.timezone.utc))
    second = build_outbound_draft(
        table.by_id("questionnaire_outstanding"),
        Observation(intake_id=intake.intake_id, facts={
            "questionnaire_name": GroundedValue(value="PEE226",
                                                pointer=doc_pointer("PEE226", chit_text))}),
        recipient=recipient)
    assert first.draft_id != second.draft_id
    return first, second


@pytest.fixture()
def path(tmp_path, drafts) -> str:
    p = str(tmp_path / "outbox.sqlite")
    writer = OutboxStore(p)
    for d in drafts:
        writer.record_draft(d)
    return p


def test_a_claim_names_only_the_rows_it_selected(path, drafts):
    """The finding's race: A claims Alice's draft; THEN Bob's is approved in another process; a
    sweep under A's claim sends only what A claimed."""
    alice, bob = drafts
    a, b = OutboxStore(path), OutboxStore(path)                  # two "processes"
    a.approve(alice.draft_id, approved_by=NURSE)
    claim = a.claim_for_dispatch(claimed_by=NURSE, draft_ids=[alice.draft_id])
    assert claim.draft_ids == (alice.draft_id,)
    b.approve(bob.draft_id, approved_by=OTHER_NURSE)             # slips in after the claim
    assert [e.draft_id for e in b.claimed(claim.claim_id)] == [alice.draft_id]


def test_the_predicate_is_evaluated_inside_the_claim(path, drafts):
    alice, bob = drafts
    s = OutboxStore(path)
    s.approve(alice.draft_id, approved_by=NURSE)
    s.approve(bob.draft_id, approved_by=OTHER_NURSE)
    claim = s.claim_for_dispatch(claimed_by=NURSE,
                                 may_claim=lambda e: e.approved_by == NURSE)
    assert claim.draft_ids == (alice.draft_id,)
    # the predicate's rejects are NOT leased: anyone else may still claim them
    assert OutboxStore(path).claim_for_dispatch(claimed_by="x").draft_ids == (bob.draft_id,)


def test_an_unapproved_row_is_never_claimed(path, drafts):
    alice, _ = drafts
    s = OutboxStore(path)
    assert s.claim_for_dispatch(claimed_by=NURSE, draft_ids=[alice.draft_id]).draft_ids == ()
    assert s.claim_for_dispatch(claimed_by=NURSE).draft_ids == ()


def test_a_held_lease_excludes_every_other_connection(path, drafts):
    alice, _ = drafts
    a, b = OutboxStore(path), OutboxStore(path)
    a.approve(alice.draft_id, approved_by=NURSE)
    first = a.claim_for_dispatch(claimed_by=NURSE, draft_ids=[alice.draft_id])
    assert first.draft_ids == (alice.draft_id,)
    assert b.claim_for_dispatch(claimed_by="other").draft_ids == ()
    assert b.holds_claim(alice.draft_id, first.claim_id)
    # and the other connection cannot record a send for it
    with pytest.raises(ClaimLost):
        b.record_sent(alice.draft_id, message_id="<x@t>", claim_id="claim-not-mine")
    with pytest.raises(ClaimLost):
        b.record_sent(alice.draft_id, message_id="<x@t>")      # no claim at all, lease held
    a.release_claim(first.claim_id)
    assert b.claim_for_dispatch(claimed_by="other").draft_ids == (alice.draft_id,)


def test_an_expired_lease_can_be_claimed_again(path, drafts):
    """A dispatcher that died mid-sweep does not hold its rows for ever."""
    alice, _ = drafts
    s = OutboxStore(path)
    s.approve(alice.draft_id, approved_by=NURSE)
    dead = s.claim_for_dispatch(claimed_by="dead", draft_ids=[alice.draft_id],
                                lease_seconds=10, now=1000.0)
    assert dead.draft_ids == (alice.draft_id,)
    assert s.claim_for_dispatch(claimed_by="next", now=1005.0).draft_ids == ()
    assert s.claim_for_dispatch(claimed_by="next", now=1011.0).draft_ids == (alice.draft_id,)
    assert not s.holds_claim(alice.draft_id, dead.claim_id)


def test_recording_the_send_under_the_claim_releases_it(path, drafts):
    alice, _ = drafts
    s = OutboxStore(path)
    s.approve(alice.draft_id, approved_by=NURSE)
    claim = s.claim_for_dispatch(claimed_by=NURSE, draft_ids=[alice.draft_id])
    s.record_sent(alice.draft_id, message_id="<1@t>", claim_id=claim.claim_id)
    assert s.current(alice.draft_id).status == "sent"
    assert not s.holds_claim(alice.draft_id, claim.claim_id)
    assert s.claimed(claim.claim_id) == []


def test_concurrent_claimers_on_separate_connections_get_a_row_once(path, drafts):
    """Eight connections race for the same approved row: exactly one wins it."""
    alice, _ = drafts
    OutboxStore(path).approve(alice.draft_id, approved_by=NURSE)
    stores = [OutboxStore(path) for _ in range(8)]
    won: list[tuple] = []
    gate = threading.Barrier(len(stores))

    def race(s: OutboxStore, i: int) -> None:
        gate.wait()
        won.append(s.claim_for_dispatch(claimed_by=f"w{i}").draft_ids)

    threads = [threading.Thread(target=race, args=(s, i)) for i, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(won, key=len) == [()] * (len(stores) - 1) + [(alice.draft_id,)]


def test_the_lease_table_is_not_the_audit_record(path):
    """The outbox stays append-only; only the lease table (not history) may change."""
    import sqlite3

    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM outbox")
    conn.execute("DELETE FROM dispatch_lease")                       # allowed: not history


# --- the actor object (R-028 #7) -----------------------------------------------------------
ACTOR = {"principal_id": NURSE, "authenticated_by": "gateway:dev-token",
         "principal_type": "AuthenticatedPrincipal", "dev_only": True}


def test_a_decision_records_the_structured_actor_and_an_undecorated_id(path, drafts):
    alice, bob = drafts
    s = OutboxStore(path)
    approved = s.approve(alice.draft_id, approved_by=NURSE, actor=ACTOR)
    assert approved.approved_by == NURSE and approved.approved_actor == ACTOR
    claim = s.claim_for_dispatch(claimed_by=NURSE, draft_ids=[alice.draft_id])
    sent = s.record_sent(alice.draft_id, message_id="<1@t>", claim_id=claim.claim_id)
    assert sent.approved_actor == ACTOR, "the approver's actor must survive onto the sent row"
    rejected = s.reject(bob.draft_id, rejected_by=NURSE, reason="phone instead", actor=ACTOR)
    assert rejected.rejected_actor == ACTOR
    # read back by a fresh connection, from the file
    assert OutboxStore(path).current(alice.draft_id).approved_actor == ACTOR


def test_an_actor_that_disagrees_with_the_named_approver_is_refused(path, drafts):
    from databridge_outbox.models import NotApproved

    alice, _ = drafts
    with pytest.raises(NotApproved):
        OutboxStore(path).approve(alice.draft_id, approved_by="Somebody Else", actor=ACTOR)


def test_an_outbox_written_before_actor_columns_still_opens(tmp_path, drafts):
    """The columns are added to an existing file, never by rewriting history."""
    import sqlite3

    from databridge_outbox.store import SCHEMA

    p = str(tmp_path / "old.sqlite")
    old = sqlite3.connect(p)
    old.executescript(SCHEMA)
    old.commit()
    old.close()
    s = OutboxStore(p)
    s.record_draft(drafts[0])
    assert s.approve(drafts[0].draft_id, approved_by=NURSE, actor=ACTOR).approved_actor == ACTOR


def test_a_decision_race_across_connections_is_a_clean_refusal(path, drafts):
    """Two processes decide one draft at once. The read-check-insert is ONE immediate transaction,
    so the loser reads the winner's row and gets IllegalTransition, never a database-lock error or
    a forked history. Both reads are held open on a barrier to force the interleaving."""
    from databridge_outbox.store import IllegalTransition

    alice, _ = drafts
    a, b = OutboxStore(path), OutboxStore(path)
    gate = threading.Barrier(2)
    for s in (a, b):
        real = s.current

        def held_open(draft_id, _real=real):
            out = _real(draft_id)
            try:
                gate.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass
            return out

        s.current = held_open
    results: list = []

    def decide(fn):
        try:
            results.append(fn())
        except Exception as exc:                                     # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=decide, args=(lambda: a.approve(
                   alice.draft_id, approved_by=NURSE),)),
               threading.Thread(target=decide, args=(lambda: b.reject(
                   alice.draft_id, rejected_by=OTHER_NURSE, reason="no"),))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(results) == 2 and len(errors) == 1, results
    assert isinstance(errors[0], IllegalTransition), repr(errors[0])
    assert len(OutboxStore(path).history(alice.draft_id)) == 2
