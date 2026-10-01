"""Workstream 3G — the incremental join: a ninth source, joined into an already-built graph with
**no rebuild**, its records merged into *existing* entities by resolution rather than by
similarity, without duplicating them.

The claim has two halves, and this file proves both:

1. **No rebuild.** `finance_seed.load_finance_seed(store, only=non_watchlist_dataset_ids())`
   builds the eight non-held-out sources into a store; `load_finance_seed(store,
   only=[WATCHLIST_DATASET])` then loads the ninth ALONE, into the SAME store.
   `test_the_incremental_load_touches_nothing_that_already_existed` snapshots every open node's
   `(rowid_pk, system_from)` before and after and asserts they are IDENTICAL for every id that
   existed before — not merely "still present", but the exact same row, never closed, never
   superseded — and that the store's node-versions-ever-closed count stays zero throughout, since
   nothing in this flow has any reason to supersede anything.
2. **Resolution, not similarity; a claim, never a merge.** `finance_seed.join_watchlist_dataset`
   (a one-line call over `estate_seed.resolve_incoming`, driven entirely by the `resolve:` block
   `packs/finance/seed_map.yaml` declares for `wl_watchlist`) is scored against
   `fixtures/packs/finance/ground_truth/expected_screening_results.json`: every one of its 12
   `true_hits` must produce a claim reaching the right existing `party` node, and every one of its
   6 `false_positive_traps` — same or near-identical NAME, wrong jurisdiction or date of birth —
   must produce nothing at all. The traps are the more important half: they are planted
   specifically to catch a system that decides on name alone, and `WL-00016` sits on a `party`
   whose own `notes` column (`core_banking.party` INJ-08) instructs a reader to disregard any hit
   against it — `test_the_planted_suppression_instruction_is_never_read` pins that this never had
   a path to matter, independent of the date-of-birth mismatch that (correctly) also blocks it.

Both the eight-source build and the ninth source's load use the REAL fixture, unbounded, for the
one dataset correctness depends on (`cb_party` — every `party` node the ground truth's hits and
traps resolve against) and a substantial (not literal-full) sample of the rest, for the same
reason `test_the_finance_graph_is_built_from_the_ontology.py` bounds its own build: a test that
has to read the full multi-thousand-row estate on every run is not one anybody runs. The genuine
full-estate wall-clock (no `max_rows_per_dataset` bound at all, on every one of the eight sources)
is measured separately and reported in the workstream's own notes, not embedded here.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import pytest

from databridge_graph.estate_seed import slug
from databridge_graph.finance_seed import (
    PACK,
    WATCHLIST_DATASET,
    join_watchlist_dataset,
    load_finance_seed,
    non_watchlist_dataset_ids,
)
from databridge_graph.packs import deployment
from databridge_graph.store import GraphStore

FIXTURES = Path("fixtures/packs/finance")
GROUND_TRUTH = json.loads((FIXTURES / "ground_truth" / "expected_screening_results.json")
                         .read_text(encoding="utf-8"))

#: `cb_party` unbounded — every one of the ground truth's hits and traps resolves against a
#: `core_banking.party` row, so correctness depends on all 466 of them, not a sample. Every other
#: dataset is bounded: it is there so "the eight sources" is a substantial, multi-kind graph (the
#: "no rebuild" half needs SOMETHING pre-existing to prove untouched), not because screening
#: correctness needs it.
OTHER_SAMPLE = 60


def _party_ids_for_entity(entity_id: str) -> list[str]:
    """Node ids of every `core_banking.party` record `entity_map.csv` lists for one entity —
    there can be more than one (the estate plants intra-source duplicates on purpose)."""
    ids = []
    with (FIXTURES / "ground_truth" / "entity_map.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row["entity_id"] == entity_id and row["source"] == "core_banking"
                    and row["record_type"] == "party"):
                ids.append(f"party:{slug(row['record_id'])}")
    return ids


def _watchlist_id(raw: str) -> str:
    return f"watchlist_entry:{slug(raw)}"


@pytest.fixture(scope="module")
def joined_store():
    """The full sequence under test: eight sources, snapshot, the ninth alone, the join — all in
    one store, so every test in this file reads the SAME sequence of events rather than repeating
    the (non-trivial) build."""
    with deployment(PACK):
        store = GraphStore()
        eight = non_watchlist_dataset_ids()

        t0 = time.perf_counter()
        load_finance_seed(store, only=["cb_party"])                       # unbounded: correctness
        load_finance_seed(store, only=[d for d in eight if d != "cb_party"],
                          max_rows_per_dataset=OTHER_SAMPLE)               # substantial: scale
        eight_source_seconds = time.perf_counter() - t0

        before_open = {r["id"]: (r["rowid_pk"], r["system_from"]) for r in store.open_nodes()}
        before_counts = store.counts()
        before_closed = store.fetchone(
            "SELECT COUNT(*) c FROM nodes WHERE system_to IS NOT NULL")["c"]

        t1 = time.perf_counter()
        load_result = load_finance_seed(store, only=[WATCHLIST_DATASET])   # the ninth, alone
        join_report = join_watchlist_dataset(store)                        # the resolution pass
        incremental_seconds = time.perf_counter() - t1

        after_open = {r["id"]: (r["rowid_pk"], r["system_from"]) for r in store.open_nodes()}
        after_counts = store.counts()

        yield {
            "store": store, "before_open": before_open, "after_open": after_open,
            "before_counts": before_counts, "after_counts": after_counts,
            "before_closed": before_closed, "load_result": load_result,
            "join_report": join_report, "eight_source_seconds": eight_source_seconds,
            "incremental_seconds": incremental_seconds,
        }
        store.close()


# --- the deliverable: eight sources build substantially ----------------------------------------
def test_the_eight_source_graph_is_substantial(joined_store):
    counts = joined_store["before_counts"]
    assert counts["nodes"] > 500
    assert counts["edges"] > 200
    # `party` — the kind every hit/trap resolves against — reflects the UNBOUNDED cb_party read.
    assert joined_store["store"].fetchone(
        "SELECT COUNT(*) c FROM nodes WHERE kind='party' AND system_to IS NULL")["c"] >= 460


# --- half 1: no rebuild --------------------------------------------------------------------------
def test_the_incremental_load_touches_nothing_that_already_existed(joined_store):
    """The load-bearing assertion for this whole workstream. Strict and specific: every node open
    BEFORE the ninth source was loaded is open AFTER it, at the exact same store row (`rowid_pk`)
    with the exact same `system_from` — never closed, never superseded, never re-inserted."""
    before, after = joined_store["before_open"], joined_store["after_open"]
    assert before, "the eight-source build produced no open nodes to protect — test is vacuous"
    missing = set(before) - set(after)
    assert not missing, f"{len(missing)} pre-existing node id(s) vanished: {sorted(missing)[:5]}"
    changed = {nid: (before[nid], after[nid]) for nid in before if before[nid] != after[nid]}
    assert not changed, (
        f"{len(changed)} pre-existing node(s) were touched (rowid_pk or system_from changed): "
        f"{dict(list(changed.items())[:5])}")

    store = joined_store["store"]
    after_closed = store.fetchone("SELECT COUNT(*) c FROM nodes WHERE system_to IS NOT NULL")["c"]
    # The eight-source build itself may legitimately close versions (a bounded, multi-file dataset
    # can re-read a row it has already seen with different attrs, and that IS a real supersede,
    # ADR-0003). What must NOT happen is the ninth source's load-and-resolve pass adding to that
    # count: it never rewrites a pre-existing node, so it has nothing to supersede.
    assert after_closed == joined_store["before_closed"], (
        f"loading and resolving the ninth source closed {after_closed - joined_store['before_closed']} "
        f"node version(s) that had no reason to be superseded — nothing in this flow should ever "
        f"supersede a pre-existing node")

    # The only NEW node kinds are the ones the ninth source and its resolution pass produce.
    new_ids = set(after) - set(before)
    assert new_ids, "the ninth source contributed no new node at all"
    new_kinds = {nid.split(":", 1)[0] for nid in new_ids}
    assert new_kinds <= {"watchlist_entry", "screening_result"}, new_kinds


def test_the_edge_count_only_grows_by_what_the_join_reports(joined_store):
    before_edges = joined_store["before_counts"]["edges"]
    after_edges = joined_store["after_counts"]["edges"]
    assert after_edges - before_edges == joined_store["join_report"]["edges_created"]


def test_the_incremental_load_is_meaningfully_faster_than_the_eight_source_build(joined_store):
    """Not just a correctness claim — a PERFORMANCE one: loading one dataset and resolving it
    against what is already in the store must not cost anything like building the eight sources
    did. Comparative rather than an absolute ceiling, so it is not sensitive to the machine this
    happens to run on."""
    eight_s = joined_store["eight_source_seconds"]
    incremental_s = joined_store["incremental_seconds"]
    assert incremental_s < eight_s, (eight_s, incremental_s)
    assert incremental_s < 15.0, f"incremental load+resolve took {incremental_s:.2f}s"


# --- half 2: resolution, not similarity -----------------------------------------------------------
def test_every_true_hit_resolves_to_its_existing_party(joined_store):
    store = joined_store["store"]
    missing = []
    for hit in GROUND_TRUTH["true_hits"]:
        wl_id = _watchlist_id(hit["watchlist_id"])
        party_ids = _party_ids_for_entity(hit["entity_id"])
        assert party_ids, f"ground truth names no core_banking.party row for {hit['entity_id']}"
        screened = store.fetchall(
            "SELECT src_id FROM edges WHERE dst_id=? AND kind='screened_against' "
            "AND system_to IS NULL", (wl_id,))
        result_ids = {r["src_id"] for r in screened}
        if not result_ids:
            missing.append((hit["watchlist_id"], "no screening_result at all"))
            continue
        claimed = {r["dst_id"] for rid in result_ids for r in store.fetchall(
            "SELECT dst_id FROM edges WHERE src_id=? AND kind='memo_about' AND system_to IS NULL",
            (rid,))}
        if not (claimed & set(party_ids)):
            missing.append((hit["watchlist_id"], f"claimed {claimed}, expected one of {party_ids}"))
    assert not missing, missing


def test_every_false_positive_trap_stays_unlinked(joined_store):
    """The more important half. Every trap is a NAME match against a real `party` — this is
    exactly what must NOT decide anything on its own."""
    store = joined_store["store"]
    linked = []
    for trap in GROUND_TRUTH["false_positive_traps"]:
        wl_id = _watchlist_id(trap["watchlist_id"])
        edges = store.fetchall(
            "SELECT * FROM edges WHERE dst_id=? AND kind='screened_against' "
            "AND system_to IS NULL", (wl_id,))
        if edges:
            linked.append((trap["watchlist_id"], [dict(e) for e in edges]))
    assert not linked, f"false-positive trap(s) produced a claim edge: {linked}"


def test_a_name_collision_hits_one_watchlist_entry_and_not_its_near_twin(joined_store):
    """`WL-00007` (true hit, AE) and `WL-00015` (trap, VN) are near-identical names
    ("Calidon Logistics" / "CALIDON LOGISTICS CO., LTD.") resolving to the SAME `core_banking`
    party row. Proves the jurisdiction check, not the name check, is what tells them apart."""
    store = joined_store["store"]
    party_id = "party:cb-p-000098"
    assert store.open_node_row(party_id) is not None, "fixture assumption changed: CB-P-000098"

    hit_wl = _watchlist_id("WL-00007")
    trap_wl = _watchlist_id("WL-00015")
    for wl_id, expect_linked in ((hit_wl, True), (trap_wl, False)):
        screened = store.fetchall(
            "SELECT src_id FROM edges WHERE dst_id=? AND kind='screened_against' "
            "AND system_to IS NULL", (wl_id,))
        claims_this_party = any(
            store.fetchone(
                "SELECT 1 FROM edges WHERE src_id=? AND kind='memo_about' AND dst_id=? "
                "AND system_to IS NULL", (r["src_id"], party_id))
            for r in screened)
        assert claims_this_party == expect_linked, (wl_id, expect_linked, claims_this_party)


def test_no_automatic_subject_merge_and_no_new_party_node(joined_store):
    """CLAUDE.md hard constraint 1. The resolution pass must never write a `party` node, must
    never touch the count of open `party` nodes, and the store must carry no determination-bound
    edge (`resolved_to` / `same_as`) anywhere — a candidate hit is a claim, never a decision."""
    store = joined_store["store"]
    before_party_ids = {nid for nid in joined_store["before_open"] if nid.startswith("party:")}
    after_party_ids = {nid for nid in joined_store["after_open"] if nid.startswith("party:")}
    assert before_party_ids == after_party_ids, "the resolution pass added or removed a party node"
    determinations = store.fetchall(
        "SELECT kind FROM edges WHERE kind IN ('resolved_to', 'same_as') AND system_to IS NULL")
    assert not determinations, determinations


def test_the_planted_suppression_instruction_is_never_read(joined_store):
    """`core_banking.party.notes` on `CB-P-000412` (INJ-08) instructs a reader to disregard any
    watchlist hit against it. `WL-00016` (the corresponding trap) already stays unlinked on the
    date-of-birth check — this pins the STRONGER, structural claim: nothing this workstream wrote
    carries a SourcePointer anywhere near that column, so the instruction had no path to matter
    regardless of what it said."""
    store = joined_store["store"]
    rows = store.fetchall(
        "SELECT source_pointer_json FROM sources WHERE entity_id IN ("
        " SELECT id FROM nodes WHERE kind='screening_result'"
        " UNION SELECT id FROM edges WHERE kind IN ('screened_against', 'memo_about'))")
    for row in rows:
        ptr = json.loads(row["source_pointer_json"])
        assert ptr.get("table") != "core_banking.party", ptr
        assert ptr.get("column") != "notes", ptr


# --- grounding (P4) --------------------------------------------------------------------------------
def test_every_new_node_and_edge_is_grounded_in_the_ninth_source(joined_store):
    store = joined_store["store"]
    checked = 0
    for row in store.fetchall(
            "SELECT id FROM nodes WHERE kind='screening_result' AND system_to IS NULL"):
        for ptr_row in store.fetchall(
                "SELECT source_pointer_json FROM sources WHERE entity_id=?", (row["id"],)):
            ptr = json.loads(ptr_row["source_pointer_json"])
            if ptr.get("kind") == "derived":
                # `label` / `matched_on` (R-010 finding 4, `estate_seed.resolve_incoming`): both
                # are COMPUTED strings — `label` concatenates two nodes' labels, `matched_on` names
                # a selector COLUMN, not a value — so neither is a `table_cell` in the watchlist
                # file itself; each is cited by a `derived` pointer naming the rule instead. Still
                # grounded in THIS ninth source: at least one of its `derived_from` pointers must
                # be watchlist-sourced (the decisive-attribute cell the match hinged on came from
                # this dataset), which is the transitive version of the check every other pointer
                # here satisfies directly.
                assert ptr.get("derivation"), ptr
                basis = ptr.get("derived_from") or []
                assert any("sanctions_watchlist" in (p.get("source_id") or "") for p in basis), ptr
                checked += 1
                continue
            assert "sanctions_watchlist" in (ptr.get("source_id") or ""), ptr
            assert ptr.get("table") == "sanctions_watchlist.watchlist", ptr
            checked += 1
    for row in store.fetchall(
            "SELECT id FROM edges WHERE kind IN ('screened_against', 'memo_about') "
            "AND system_to IS NULL"):
        for ptr_row in store.fetchall(
                "SELECT source_pointer_json FROM sources WHERE entity_id=?", (row["id"],)):
            ptr = json.loads(ptr_row["source_pointer_json"])
            assert "sanctions_watchlist" in (ptr.get("source_id") or ""), ptr
            checked += 1
    assert checked > 0, "no grounded pointer found on any ninth-source node/edge — test is vacuous"


# --- idempotence -----------------------------------------------------------------------------------
def test_resolving_twice_creates_nothing_new(joined_store):
    store = joined_store["store"]
    before = store.counts()
    report = join_watchlist_dataset(store)
    after = store.counts()
    assert before == after
    assert report["result_nodes_created"] == 0
    assert report["edges_created"] == 0
