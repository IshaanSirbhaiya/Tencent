"""Emission registration — the materialiser's leg of the reverse pointer index's third table.

`materialise()` now registers the built dataset under its `plan_hash` — the dataset's identity —
citing every pointer it emitted, with an `EmissionSink` if one was supplied. See `executor.py`'s
`EmissionSink` Protocol and the comment beside the registration call for why this one, unlike
`ledger.commit` and `outbox.record_draft`, cannot be "inside a transaction": materialisation writes
files, not database rows, so there is no `conn` whose rollback the call could ride.

Real `GraphStore`, imported here. `packages/materialise` has no dependency on `databridge_graph`
in its own source — this package's `conftest.py` puts `packages/graph` on `sys.path` for exactly
this kind of test.
"""
from __future__ import annotations

from databridge_core import SourcePointer
from databridge_graph import Answer, GraphStore
from databridge_graph.ptr_index import pointer_identity

from databridge_materialise import materialise

from plans import covenant_plan


# --- discoverability ----------------------------------------------------------------------


def test_a_materialised_dataset_is_discoverable_through_impact_of(reader):
    store = GraphStore()
    plan = covenant_plan()
    built = materialise(plan, reader, emission_sink=store.reverse_index)
    assert built.dataset.pointers(), "need a build with grounded cells to prove anything"

    sample = built.dataset.pointers()[0]
    report = store.reverse_index.impact_of(pointer=sample)
    assert report.answer is Answer.KNOWN
    assert len(report.emissions) == 1
    emission = report.emissions[0]
    assert emission.kind == "materialised_dataset"
    assert emission.ref == built.compiled.plan_hash
    assert emission.plan_hash == built.compiled.plan_hash
    assert report.ledger_bound == (), "a materialised dataset is not a ledger-bound record"


# --- the demo moment: fan-out to emitted records, not only graph versions -----------------


def test_the_fan_out_from_a_drifted_source_reaches_the_dataset_with_every_pointer_it_cites(
        reader):
    """A materialised dataset cites many pointers; ALL of them belong in the index or the
    fan-out is wrong (the task's own words). This measures the number: how many DISTINCT
    pointers a drifted source now reaches through one emitted dataset."""
    store = GraphStore()
    plan = covenant_plan()
    built = materialise(plan, reader, emission_sink=store.reverse_index)

    distinct = {pointer_identity(p) for p in built.dataset.pointers()}
    assert len(distinct) > 1, "need more than one distinct pointer for a fan-out to be worth "\
        "measuring"

    report = store.reverse_index.impact_of(source_id="core_banking")
    assert report.answer is Answer.KNOWN
    assert len(report.emissions) == 1
    reached = set(report.emissions[0].pointer_ids)
    # Every distinct pointer this dataset cites was registered, and the source's own pointer set
    # is a superset (the source may hold pointers that never made it into THIS dataset's columns).
    assert len(reached) == len(distinct), (
        f"registered {len(reached)} pointer(s) against a dataset that cites {len(distinct)} "
        f"distinct pointer(s) — the fan-out is short of the truth")


# --- honesty survives registration ---------------------------------------------------------


def test_a_bypassed_write_still_reports_unknown_with_the_exact_count(reader):
    store = GraphStore()
    built = materialise(covenant_plan(), reader, emission_sink=store.reverse_index)
    assert built.dataset.pointers()

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


def test_a_build_with_no_sink_materialises_exactly_as_before(reader):
    built = materialise(covenant_plan(), reader)          # every existing call site
    assert built.receipt.plan_hash == covenant_plan().plan_hash
