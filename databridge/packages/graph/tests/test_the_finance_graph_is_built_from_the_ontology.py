"""The finance graph, built deterministically from the PREDEFINED ontology — mirrors
`test_the_government_graph_is_built_from_the_ontology.py` (read it first; it is the pattern this
file follows, including which fixture is edited and how).

Two things this file pins that the government test does not need to:

* **`sanctions_watchlist` is bound, but never read unless named.** It is the finance estate's
  ninth source, added to `packs/finance/seed_map.yaml` as its own dataset (`wl_watchlist`) so it
  CAN be loaded — but `build_finance_graph()`'s default fixture below always passes
  `only=non_watchlist_dataset_ids()`, exactly the eight non-held-out sources, so the graph this
  file's other tests examine never touched it, precisely as when it was absent from the map
  entirely. `test_the_ninth_source_is_never_read_unless_named` is the assertion for that half;
  `packages/graph/tests/test_the_incremental_sanctions_join.py` is the assertion for the other
  half — that naming it explicitly loads it into an EXISTING store with no rebuild, and that
  `finance_seed.join_watchlist_dataset` joins it into the graph correctly.
* **The map binds five of the estate's eight non-held-out sources, not eight**, because
  `estate_seed.py` reads exactly four shapes (`sql_insert`, `csv`, `json_pages`, `fixed_width`) and
  three sources — `filings`, `credit_memos`, `payments` — fit none of them (see the seed map's own
  header for the full reasoning). `test_sources_the_readers_cannot_honestly_bind_are_absent` pins
  that this was a *decision*, not an oversight the fixture happens to hide.

The build is bounded with `max_rows_per_dataset` throughout, exactly as the government test bounds
it, for the same reason: the same bound gives the same graph anywhere, and the whole path is
exercised in seconds. The unbounded build is reported as untimed (see the task notes) rather than
run here, since a merge-performance fix was landing concurrently in `merge.py`/`store.py`.
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

from databridge_core import Trust
from databridge_graph.estate_seed import SeedMapError, build_pack_graph, load_seed_map
from databridge_graph.finance_seed import (
    PACK,
    WATCHLIST_DATASET,
    build_finance_graph,
    load_finance_seed,
    non_watchlist_dataset_ids,
)
from databridge_graph.meta import ATTESTED_META_KINDS
from databridge_graph.packs import active_vocabulary, deployment
from databridge_graph.store import GraphStore

SAMPLE = 40

#: Sources this map cannot bind at all, and are asserted absent below — `estate_seed.py`'s four
#: readers have no honest way to parse them (see the seed map's own header). `sanctions_watchlist`
#: is NOT in this list any more: it IS bound (`wl_watchlist`), just never read by the `only=`
#: filter this file's `built` fixture passes.
UNBOUND_SOURCES = ("filings", "credit_memos", "payments")


def _packs_copy(tmp_path, *, edit=None):
    """A writable copy of `packs/`, with `fixtures_root` repointed at the REAL fixtures.

    Identical in shape to the government test's helper of the same name — see it for why the
    repoint is needed.
    """
    packs_dir = tmp_path / "packs"
    shutil.copytree("packs", packs_dir)
    seed_map = packs_dir / PACK / "seed_map.yaml"
    text = seed_map.read_text(encoding="utf-8").replace(
        'fixtures_root: "../../fixtures/packs/finance"',
        f'fixtures_root: "{pathlib.Path("fixtures/packs/finance").resolve().as_posix()}"')
    if edit is not None:
        text = edit(text)
    seed_map.write_text(text, encoding="utf-8")
    return packs_dir


@pytest.fixture(scope="module")
def built():
    with deployment(PACK):
        # `only=` names the eight non-held-out sources explicitly — never the ninth. This is the
        # same selection `sanctions_watchlist`'s absence from the map used to guarantee by
        # construction; now that it IS in the map, the guarantee moves to the caller naming what
        # it wants, which is exactly what the incremental-join test exercises on purpose.
        yield build_finance_graph(max_rows_per_dataset=SAMPLE, only=non_watchlist_dataset_ids())


@pytest.fixture(scope="module")
def nodes(built):
    return built[0]


@pytest.fixture(scope="module")
def edges(built):
    return built[1]


@pytest.fixture(scope="module")
def report(built):
    return built[2]


# --- the deliverable ---------------------------------------------------------------------
def test_the_graph_builds_and_is_substantial(nodes, edges, report):
    assert len(nodes) > 500
    assert len(edges) > 250
    assert report.node_count == len(nodes)
    assert len(report.rows_read) >= 20, (
        "the map binds twenty-plus datasets across five sources; a build that reads only a "
        "handful of them is not the deliverable")


def test_every_kind_emitted_is_one_the_pack_declares(nodes, edges):
    """The whole point: built from the PREDEFINED library. A kind the library lacks goes through
    the growth protocol, where a human is asked — never through the builder."""
    with deployment(PACK):
        vocab = active_vocabulary()
        for node in nodes:
            assert str(node.kind) in vocab.node_kinds
        for edge in edges:
            assert str(edge.kind) in vocab.edge_kinds


def test_the_build_uses_a_meaningful_part_of_the_library(nodes, edges):
    """Calibrated to what this map actually binds (five of nine sources — see the module
    docstring), not copied from the government test's larger figure."""
    used_nodes = {str(n.kind) for n in nodes}
    used_edges = {str(e.kind) for e in edges}
    assert len(used_nodes) >= 18
    assert len(used_edges) >= 14


def test_a_map_naming_a_kind_the_ontology_lacks_fails_by_name(tmp_path):
    """It must fail at BUILD time with the kind named, not skip the row and produce a smaller
    graph nobody notices."""
    packs_dir = _packs_copy(tmp_path, edit=lambda s: s.replace(
        "      - kind: party\n        key: \"{party_id}\"\n",
        "      - kind: household\n        key: \"{party_id}\"\n", 1))
    with deployment(PACK):
        with pytest.raises(SeedMapError, match="household"):
            build_pack_graph(PACK, max_rows_per_dataset=5, packs_dir=packs_dir)


# --- the rule that separates a deterministic path from a hardcode ---------------------------
def test_the_graph_is_computed_from_the_data_not_recalled(tmp_path):
    """**The load-bearing test.** A lookup table mapping input to output is a violation; reading a
    row and constructing a grounded node from it is not.

    Mirrors the government test exactly: a copy of one fixture file is edited, the map is pointed
    at the copy, and the change is followed through to the node's attribute and to the pointer
    that cites it — then the fixture shrinks and the graph shrinks with it, by exactly the amount
    removed.
    """
    packs_dir = _packs_copy(tmp_path)
    fixtures = tmp_path / "fixtures" / "packs" / PACK
    fixtures.mkdir(parents=True)
    source = "fixtures/packs/finance/core_banking/seed/01_party_01.sql"
    target = fixtures / "core_banking" / "seed" / "01_party_01.sql"
    target.parent.mkdir(parents=True)
    original = open(source, encoding="utf-8").read()
    target.write_text(original.replace("'Fairmont Reach Property LLC'",
                                       "'A Legal Name That Appears In No Fixture'"),
                      encoding="utf-8")

    seed_map = packs_dir / PACK / "seed_map.yaml"
    lines = seed_map.read_text(encoding="utf-8").splitlines()
    lines = [f'fixtures_root: "{fixtures.as_posix()}"' if line.startswith("fixtures_root:")
             else line for line in lines]
    seed_map.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with deployment(PACK):
        nodes, _, _ = build_pack_graph(PACK, packs_dir=packs_dir, only=["cb_party"])
    labels = {n.label for n in nodes}
    assert "A Legal Name That Appears In No Fixture" in labels
    assert "Fairmont Reach Property LLC" not in labels

    trimmed = original.split("\n")
    body = [line for line in trimmed if line.strip().startswith("('")]
    target.write_text("\n".join(trimmed[:trimmed.index(body[0])] + [body[0].rstrip(",") + ";"]),
                      encoding="utf-8")
    with deployment(PACK):
        fewer, _, _ = build_pack_graph(PACK, packs_dir=packs_dir, only=["cb_party"])
    assert len([n for n in fewer if str(n.kind) == "party"]) == 1


def test_the_same_bound_gives_the_same_graph_twice():
    """Determinism, asserted rather than assumed: ids, order and pointers all stable."""
    with deployment(PACK):
        first_nodes, first_edges, first_report = build_finance_graph(max_rows_per_dataset=10)
        second_nodes, second_edges, second_report = build_finance_graph(max_rows_per_dataset=10)
    assert [n.id for n in first_nodes] == [n.id for n in second_nodes]
    assert [e.id for e in first_edges] == [e.id for e in second_edges]
    assert first_report.as_record() == second_report.as_record()
    assert [n.model_dump_json() for n in first_nodes[:50]] == \
           [n.model_dump_json() for n in second_nodes[:50]]


# --- grounding -------------------------------------------------------------------------------
def test_every_node_and_edge_carries_a_real_source_pointer(nodes, edges):
    """P4. `GraphNode`/`GraphEdge` refuse construction without one, so this asserts the pointers
    are USABLE rather than merely present: each names a file that exists and a cell within it."""
    fixtures = load_seed_map(PACK).root
    for entity in list(nodes)[:400] + list(edges)[:400]:
        assert entity.sources
        for pointer in entity.sources:
            assert pointer.kind == "table_cell"
            assert pointer.table and pointer.column and pointer.row_id
            assert (fixtures / pointer.source_id).is_file(), pointer.source_id


def test_every_written_attribute_carries_its_own_pointer(nodes):
    """`sources` grounds the ENTITY — that this thing exists. It says nothing about the individual
    values, so a value with no pointer of its own is not written as a value at all."""
    for node in list(nodes)[:400]:
        for name, value in node.attrs.items():
            if value is None:
                assert name not in node.attr_sources
            else:
                assert name in node.attr_sources, f"{node.id}.{name} has a value and no pointer"


def test_a_null_cell_becomes_an_unanswered_question_not_a_guess(nodes):
    """The absence is RECORDED. `unresolved` names the key that was asked and not answered."""
    with_gaps = [n for n in nodes if n.unresolved]
    assert with_gaps, "an estate this messy with no unresolved keys would mean gaps were filled in"
    for node in with_gaps[:50]:
        for key in node.unresolved:
            assert node.attrs.get(key) is None


def test_untrusted_sources_are_labelled_untrusted(nodes):
    """Spine D3: the mailbox is untrusted in full (sources.yaml), and the systems of record this
    map binds (core_banking, crm) are trusted."""
    by_kind = {}
    for node in nodes:
        by_kind.setdefault(str(node.kind), set()).update(p.trust for p in node.sources)
    assert by_kind.get("message") == {Trust.UNTRUSTED}, "mailbox-sourced messages must be untrusted"
    for kind in ("account", "facility", "transaction", "relationship_manager"):
        assert by_kind.get(kind) == {Trust.TRUSTED}, f"{kind} is a system of record"


def test_no_untrusted_field_is_emitted_as_a_trusted_attribute(nodes):
    """Hard requirement 6: trust is per FIELD in `sources.yaml`, but `estate_seed.py` grounds a
    pointer per DATASET. The map's answer is to leave the untrusted columns out of `attrs`
    entirely for an otherwise-trusted dataset, rather than emit them under a pointer that
    misrepresents their trust. This asserts the columns named untrusted in `sources.yaml` never
    surface as a grounded, `trusted` attribute anywhere in the build."""
    banned = {
        "party": {"notes"}, "facility": {"notes"}, "collateral": {"notes", "description"},
        "transaction": {"narrative", "counterparty_name"},
        "interaction": {"subject", "summary"}, "opportunity": {"lost_reason"},
        "instrument": {"issuer_name_as_reported"},
        "sector": {"note"}, "covenant_test": {"borrower"},
    }
    for node in nodes:
        forbidden = banned.get(str(node.kind))
        if not forbidden:
            continue
        for key in forbidden:
            assert key not in node.attrs, f"{node.id} carries {key!r} as a trusted attribute"


# --- the identity constraint ------------------------------------------------------------------
def test_the_builder_writes_no_determination(edges):
    """CLAUDE.md constraint 1. No edge this map declares binds to an ATTESTED meta-kind; the
    weakest identity-adjacent kind it uses is `references` — a citation, never a determination —
    for the estate's own worked examples of a candidate-only bridge column (`crm.account_org.
    external_ref`, `core_banking.txn.counterparty_party_id`)."""
    with deployment(PACK):
        for edge in edges:
            assert edge.kind.meta not in ATTESTED_META_KINDS
            assert edge.attestation is None


def test_a_map_asking_for_a_determination_is_refused_structurally(tmp_path):
    """Not a matter of how the map is written: the builder refuses the kind before constructing
    the edge, so no map, however authored, can make it decide who somebody is."""
    packs_dir = _packs_copy(tmp_path, edit=lambda s: s.replace(
        '      - kind: references\n        src: "party:{crm_org_id}"\n'
        '        dst: "party:{external_ref}"\n',
        '      - kind: same_as\n        src: "party:{crm_org_id}"\n'
        '        dst: "party:{external_ref}"\n', 1))
    with deployment(PACK):
        with pytest.raises(SeedMapError, match="DETERMINATION"):
            build_pack_graph(PACK, max_rows_per_dataset=5, packs_dir=packs_dir)


def test_two_registers_holding_the_same_entity_stay_two_nodes(nodes):
    """The honest state, finance's version of the government test of the same name. `cb_party`
    (core_banking.party) and `crm_account_org` (crm.account_org) are two independent registers for
    frequently the same real-world company, keyed on disjoint id spaces (`party_id` vs.
    `crm_org_id`). Deciding they are the same subject is an identity determination only a named
    human may make, so until then the graph holds both."""
    parties = [n for n in nodes if str(n.kind) == "party"]
    assert len({n.id for n in parties}) == len(parties)
    labels = [n.label for n in parties]
    assert len(set(labels)) < len(labels), (
        "this estate's two registers name overlapping real-world companies; if every label were "
        "unique here, something had already merged them")


# --- the report is the honest half ------------------------------------------------------------
def test_dangling_edges_are_dropped_and_counted_rather_than_kept(nodes, edges, report):
    """A reference that resolves to nothing is a FINDING in this estate: `counterparty_party_id`
    is an undeclared FK with a declared ~11% orphan rate, `crm.account_org.external_ref` is
    correct only ~30% of the time (sources.yaml). It belongs in the report, not silently kept."""
    ids = {n.id for n in nodes}
    for edge in edges:
        assert edge.src_id in ids and edge.dst_id in ids
    assert report.dangling_edges, "a build of this estate with no dangling references would mean "\
                                  "they were quietly repaired"


def test_the_report_names_what_it_declined_to_build(report):
    record = report.as_record()
    for key in ("rows_read", "nodes_by_kind", "edges_by_kind", "skipped_nodes_missing_key",
                "skipped_edges_missing_endpoint", "dangling_edges"):
        assert key in record
    assert record["nodes"] == sum(record["nodes_by_kind"].values())


# --- an edge cannot begin before its endpoints -------------------------------------------------
def test_no_edge_starts_before_both_things_it_relates_exist(nodes, edges):
    starts = {n.id: n.valid_from for n in nodes}
    for edge in edges:
        assert edge.valid_from >= starts[edge.src_id]
        assert edge.valid_from >= starts[edge.dst_id]


# --- it loads -----------------------------------------------------------------------------------
def test_the_graph_loads_into_a_store_and_is_idempotent():
    with deployment(PACK):
        store = GraphStore()
        first = load_finance_seed(store, max_rows_per_dataset=8)
        assert first["nodes_created"] > 0 and first["edges_created"] > 0
        assert first["attribute_sources_attached"] > first["nodes_created"], (
            "per-attribute provenance is the point; one pointer per node would mean the "
            "attributes are grounded by the entity's citation, which is the failure the "
            "per-attribute wall exists to prevent")
        second = load_finance_seed(store, max_rows_per_dataset=8)
        assert second["nodes_created"] == 0
        assert second["edges_created"] == 0


# --- the ninth source: bound, but never read unless named, and the three the readers cannot bind
def test_the_ninth_source_is_never_read_unless_named(nodes, edges, report):
    """This file's `built` fixture names the eight other sources explicitly and never
    `wl_watchlist`, so its graph must never have read `sanctions_watchlist/`, must never have
    contributed a `watchlist_entry` node or a `screened_against`/`memo_about` edge, and must not
    appear in the report — a source that was skipped because it was not asked for and one that
    could not be asked for at all are different claims, and this pins the first now that the
    second is no longer true (`wl_watchlist` IS declared in the map; see
    `test_the_ninth_source_is_bound_and_readable`)."""
    assert WATCHLIST_DATASET not in report.rows_read
    assert "sanctions_watchlist" not in report.rows_read
    assert not any(str(n.kind) == "watchlist_entry" for n in nodes)
    assert not any(str(e.kind) in ("screened_against", "memo_about") for e in edges)


def test_the_ninth_source_is_bound_and_readable(tmp_path):
    """The other half of the claim: `wl_watchlist` IS a real dataset this map declares, reading
    the real `sanctions_watchlist/` transport `sources.yaml` names for it — bound, not merely
    mentioned in a comment. `test_the_incremental_sanctions_join.py` is where it is actually
    joined into a store; this just pins that `only=[WATCHLIST_DATASET]` reads it and nothing
    else, and that every node it produces is a `watchlist_entry` grounded in that file."""
    with deployment(PACK):
        seed = load_seed_map(PACK)
        ids = {d.id for d in seed.datasets}
        assert WATCHLIST_DATASET in ids
        nodes, edges, report = build_pack_graph(PACK, only=[WATCHLIST_DATASET])
    assert report.rows_read.get(WATCHLIST_DATASET, 0) > 0
    assert set(report.rows_read) == {WATCHLIST_DATASET}
    assert nodes and all(str(n.kind) == "watchlist_entry" for n in nodes)
    assert not edges
    for n in nodes:
        assert all(p.table == "sanctions_watchlist.watchlist" for p in n.sources)
        assert all("sanctions_watchlist" in p.source_id for p in n.sources)


def test_sources_the_readers_cannot_honestly_bind_are_absent():
    """`estate_seed.py` reads `sql_insert` / `csv` / `json_pages` / `fixed_width` and nothing
    else. `filings` and `credit_memos` are unversioned document drops with no structured per-row
    index anywhere in the fixture, and `payments` ships as JSONL (one JSON object per line), which
    none of the four readers parse. Binding any of them would mean this map inventing an index
    file the fixture does not provide — fabricating fixture data — so they are named absent here
    rather than forced. See the seed map's own header for the full reasoning."""
    with deployment(PACK):
        seed = load_seed_map(PACK)
    tables = {dataset.table for dataset in seed.datasets}
    for source in UNBOUND_SOURCES:
        assert not any(t == source or t.startswith(f"{source}.") for t in tables), source
    # And the readers actually used are exactly the four file-shape readers the executor declares,
    # plus `adapter` — never a fifth FILE reader this map would have had to invent to reach
    # filings/credit_memos/payments. `adapter` is a different kind of exception: it names no file
    # at all (`cb_facility_adapter` — see `adapter_ingest.py`), so it cannot be the same class of
    # fabrication this test guards against; it re-reads `core_banking.facility`, a table this map
    # already binds honestly via `sql_insert` (`cb_facility`), through a Core instead of a file.
    assert {dataset.reader for dataset in seed.datasets} <= {"sql_insert", "csv", "adapter"}
