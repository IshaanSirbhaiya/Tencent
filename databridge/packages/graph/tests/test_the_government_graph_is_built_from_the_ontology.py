"""The government graph, built deterministically from the PREDEFINED ontology — Phase 4.

The deliverable this pins is a comparison. The finance graph will be built *by the agents*, which
discover their kinds as they go; the government graph is built from a declared node/edge library
and from nothing else. Both halves of that sentence need to be true and testable:

* **from the library** — every kind emitted is one `packs/government/ontology.yaml` declares, and a
  map naming anything else fails at build time rather than coining it;
* **deterministically** — the graph is COMPUTED from the fixture rows. A lookup table mapping input
  to prepared output would be a violation; reading a row and constructing a grounded node from it
  is not. `test_the_graph_is_computed_from_the_data_not_recalled` is the assertion that separates
  those two, and it is the load-bearing test in this file.

The build is bounded with `max_rows_per_dataset` throughout. The bound is deterministic — the first
N rows in file order — so the same bound gives the same graph anywhere, and the whole path is
exercised in seconds against an estate of tens of thousands of rows. The unbounded numbers are
reported in `wiki/30-workstreams/PHASE_LOG.md`.
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

from databridge_core import Trust
from databridge_graph.estate_seed import SeedMapError, build_pack_graph, load_seed_map
from databridge_graph.government_seed import (
    PACK,
    build_government_graph,
    load_government_seed,
)
from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS, EdgeMetaKind
from databridge_graph.packs import active_vocabulary, deployment
from databridge_graph.store import GraphStore

SAMPLE = 40


def _packs_copy(tmp_path, *, edit=None):
    """A writable copy of `packs/`, with `fixtures_root` repointed at the REAL fixtures.

    The map's `fixtures_root` is relative to the pack directory, so copying `packs/` elsewhere
    would leave it pointing at a directory that does not exist. Repointing it absolutely keeps
    these tests about the thing they are testing — what the builder does with a map — rather than
    about where the copy landed.
    """
    packs_dir = tmp_path / "packs"
    shutil.copytree("packs", packs_dir)
    seed_map = packs_dir / PACK / "seed_map.yaml"
    text = seed_map.read_text(encoding="utf-8").replace(
        'fixtures_root: "../../fixtures/packs/government"',
        f'fixtures_root: "{pathlib.Path("fixtures/packs/government").resolve().as_posix()}"')
    if edit is not None:
        text = edit(text)
    seed_map.write_text(text, encoding="utf-8")
    return packs_dir


@pytest.fixture(scope="module")
def built():
    with deployment(PACK):
        yield build_government_graph(max_rows_per_dataset=SAMPLE)


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
    assert len(nodes) > 1000
    assert len(edges) > 500
    assert report.node_count == len(nodes)
    assert len(report.rows_read) >= 20, "the estate has eight sources; a build that reads two of "\
                                        "them is not the deliverable"


def test_every_kind_emitted_is_one_the_pack_declares(nodes, edges):
    """The whole point: built from the PREDEFINED library. A kind the library lacks goes through
    the growth protocol, where a human is asked — never through the builder."""
    with deployment(PACK):
        vocab = active_vocabulary()
        for node in nodes:
            assert str(node.kind) in vocab.node_kinds
        for edge in edges:
            assert str(edge.kind) in vocab.edge_kinds


def test_the_build_uses_a_large_part_of_the_library_rather_than_a_corner(nodes, edges):
    """A predefined ontology nobody uses is a document, not a schema."""
    used_nodes = {str(n.kind) for n in nodes}
    used_edges = {str(e.kind) for e in edges}
    assert len(used_nodes) >= 25
    assert len(used_edges) >= 25


def test_a_map_naming_a_kind_the_ontology_lacks_fails_by_name(tmp_path):
    """It must fail at BUILD time with the kind named, not skip the row and produce a smaller
    graph nobody notices."""
    packs_dir = _packs_copy(tmp_path, edit=lambda s: s.replace(
        "      - kind: permit_type\n", "      - kind: household\n", 1))
    with deployment(PACK):
        with pytest.raises(SeedMapError, match="household"):
            build_pack_graph(PACK, max_rows_per_dataset=5, packs_dir=packs_dir)


# --- the rule that separates a deterministic path from a hardcode ---------------------------
def test_the_graph_is_computed_from_the_data_not_recalled(tmp_path):
    """**The load-bearing test.** A lookup table mapping input to output is a violation; reading a
    row and constructing a grounded node from it is not.

    The difference is observable: change the DATA and the graph must change with it, in the exact
    place the change was made and nowhere else. A builder holding prepared answers would return the
    same graph for altered input — which is precisely what "hardcoded" means, however the code is
    spelled.

    A copy of one fixture file is edited, the map is pointed at the copy, and the value is followed
    through to the node's attribute and to the pointer that cites it.
    """
    packs_dir = _packs_copy(tmp_path)
    fixtures = tmp_path / "fixtures" / "packs" / PACK
    fixtures.mkdir(parents=True)
    source = "fixtures/packs/government/permits_registry/seed/010_permit_types.sql"
    target = fixtures / "permits_registry" / "seed" / "010_permit_types.sql"
    target.parent.mkdir(parents=True)
    original = open(source, encoding="utf-8").read()
    target.write_text(original.replace("'Residential Alteration Permit'",
                                       "'A Title That Appears In No Fixture'"), encoding="utf-8")

    # Point the map at the edited copy, and build that dataset alone.
    seed_map = packs_dir / PACK / "seed_map.yaml"
    lines = seed_map.read_text(encoding="utf-8").splitlines()
    lines = [f'fixtures_root: "{fixtures.as_posix()}"' if line.startswith("fixtures_root:")
             else line for line in lines]
    seed_map.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with deployment(PACK):
        nodes, _, _ = build_pack_graph(PACK, packs_dir=packs_dir, only=["permit_types"])
    labels = {n.label for n in nodes}
    assert "A Title That Appears In No Fixture" in labels
    assert "Residential Alteration Permit" not in labels

    # ...and the graph shrinks when the data does, in exactly the amount removed.
    trimmed = original.split("\n")
    body = [line for line in trimmed if line.strip().startswith("('")]
    target.write_text("\n".join(trimmed[:trimmed.index(body[0])] + [body[0].rstrip(",") + ";"]),
                      encoding="utf-8")
    with deployment(PACK):
        fewer, _, _ = build_pack_graph(PACK, packs_dir=packs_dir, only=["permit_types"])
    assert len([n for n in fewer if str(n.kind) == "permit_type"]) == 1


def test_the_same_bound_gives_the_same_graph_twice():
    """Determinism, asserted rather than assumed: ids, order and pointers all stable."""
    with deployment(PACK):
        first_nodes, first_edges, first_report = build_government_graph(max_rows_per_dataset=10)
        second_nodes, second_edges, second_report = build_government_graph(max_rows_per_dataset=10)
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
    """The absence is RECORDED. `unresolved` names the key that was asked and not answered, which
    is what stops a reader mistaking "we did not find it" for "it is not there"."""
    with_gaps = [n for n in nodes if n.unresolved]
    assert with_gaps, "an estate this messy with no unresolved keys would mean gaps were filled in"
    for node in with_gaps[:50]:
        for key in node.unresolved:
            assert node.attrs.get(key) is None


def test_untrusted_sources_are_labelled_untrusted(nodes):
    """Spine D3: correspondence headers, OCR text and legacy records may be stored, cited and
    displayed, and may never influence control flow. The label is what makes that enforceable."""
    by_kind = {}
    for node in nodes:
        by_kind.setdefault(str(node.kind), set()).update(p.trust for p in node.sources)
    for kind in ("message", "document", "photo", "legacy_record"):
        assert by_kind.get(kind) == {Trust.UNTRUSTED}, f"{kind} must be untrusted"
    for kind in ("application", "decision", "parcel", "person"):
        assert by_kind.get(kind) == {Trust.TRUSTED}, f"{kind} is a system of record"


# --- the identity constraint ------------------------------------------------------------------
def test_the_builder_writes_no_determination(edges):
    """CLAUDE.md constraint 1. Identity CLAIMS are `about`-shaped; a determination of who somebody
    is comes only from a named human's attestation, and no builder may write one."""
    with deployment(PACK):
        for edge in edges:
            assert edge.kind.meta not in ATTESTED_META_KINDS
            assert edge.attestation is None
        claims = [e for e in edges if e.kind.meta in CLAIM_META_KINDS]
        assert claims, "a graph of this estate with no identity CLAIMS would mean they were "\
                       "silently dropped rather than recorded as claims"


def test_a_map_asking_for_a_determination_is_refused_structurally(tmp_path):
    """Not a matter of how the map is written: the builder refuses the kind before constructing
    the edge, so no map, however authored, can make it decide who somebody is."""
    packs_dir = _packs_copy(tmp_path, edit=lambda s: s.replace(
        '      - kind: about_subject\n        src: "application:{application_id}"',
        '      - kind: resolved_to\n        src: "application:{application_id}"', 1))
    with deployment(PACK):
        with pytest.raises(SeedMapError, match="DETERMINATION"):
            build_pack_graph(PACK, max_rows_per_dataset=5, packs_dir=packs_dir)


def test_two_registers_holding_the_same_human_stay_two_nodes(nodes):
    """The honest state. Deciding that a row in one case system and a row in another denote one
    person is an identity determination, and only a named human may make it — so until then the
    graph holds both, and nothing merges them by similarity."""
    people = [n for n in nodes if str(n.kind) == "person"]
    assert len({n.id for n in people}) == len(people)
    labels = [n.label for n in people]
    assert len(set(labels)) < len(labels), (
        "this estate contains people recorded in more than one register; if every label were "
        "unique here, something had already merged them")


# --- the report is the honest half ------------------------------------------------------------
def test_dangling_edges_are_dropped_and_counted_rather_than_kept(nodes, edges, report):
    """A reference that resolves to nothing is a FINDING in this estate — land references from a
    retired numbering scheme, case references off untrusted headers. It belongs in the report where
    somebody can read it, not in the graph where it looks like a relationship."""
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
        first = load_government_seed(store, max_rows_per_dataset=8)
        assert first["nodes_created"] > 0 and first["edges_created"] > 0
        assert first["attribute_sources_attached"] > first["nodes_created"], (
            "per-attribute provenance is the point; one pointer per node would mean the "
            "attributes are grounded by the entity's citation, which is the failure the "
            "per-attribute wall exists to prevent")
        second = load_government_seed(store, max_rows_per_dataset=8)
        assert second["nodes_created"] == 0
        assert second["edges_created"] == 0
