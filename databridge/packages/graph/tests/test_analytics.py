"""`analytics.compute` — the graph-derived operational aggregates (T-43).

Until this file existed the module had **no tests at all**, which is how its `as_of` comparison
shipped raising `TypeError` on a naive datetime: nothing called it, so nothing found out.

What these tests are actually guarding:

1. every figure carries its denominator and the ids behind it (a count nobody can walk to is
   indistinguishable from a literal);
2. `as_of` is a real bi-temporal rewind, not decoration — and it never returns an edge whose
   endpoints do not exist in that rewound view (the T-30 invariant, read from the other side);
3. nothing here narrows a `FOR_PATIENT` claim to one candidate (CLAUDE.md §Hard constraints 1).

`analytics.compute` itself names no kind: it reasons over meta-kinds and the bound packs'
`subject_kinds`. This file is its HEALTHCARE-BOUND fixture — the clinic seed under the healthcare
vocabulary — so the expectations below are written in that pack's kinds and derived from the store,
proving the generic figures still say exactly what the clinical ones said.
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_graph import EdgeKind, NodeKind, analytics
from databridge_graph.clinic_seed import fresh_clinic_store

UTC = dt.timezone.utc


@pytest.fixture(scope="module")
def store():
    return fresh_clinic_store()


@pytest.fixture(scope="module")
def full(store):
    return analytics.compute(store)


# --- the defect this file was written for -----------------------------------------
def test_as_of_accepts_a_naive_datetime(store):
    """`compute(store, as_of=datetime(2026, 8, 1))` is the obvious thing to type, and it used to
    raise `TypeError: can't compare offset-naive and offset-aware datetimes`."""
    naive = analytics.compute(store, as_of=dt.datetime(2026, 8, 1))
    aware = analytics.compute(store, as_of=dt.datetime(2026, 8, 1, tzinfo=UTC))
    assert naive.to_dict() == aware.to_dict()


def test_as_of_is_stamped_on_the_result(store):
    assert analytics.compute(store).as_of is None
    out = analytics.compute(store, as_of=dt.datetime(2026, 8, 1))
    assert out.as_of is not None and out.as_of.startswith("2026-08-01")


# --- every figure carries its denominator and its members --------------------------
def test_every_count_carries_a_denominator_and_its_members(full):
    for count in (full.ambiguity, full.unlinked_documents, full.conflicts):
        assert count is not None
        assert count.label, "a bare number with no label is not a figure"
        assert count.of >= count.n >= 0
        assert len(count.members) == count.n, (
            f"{count.label}: {count.n} counted but {len(count.members)} ids given — a figure "
            "nobody can walk to is indistinguishable from a literal")


def test_pct_is_zero_rather_than_a_zero_division_on_an_empty_population():
    empty = analytics.Count("nothing yet", 0, 0)
    assert empty.pct == 0.0


def test_every_member_id_resolves_to_a_labelled_entity(full):
    ids = {*full.ambiguity.members, *full.unlinked_documents.members, *full.conflicts.members}
    for issuer in full.issuer_mix:
        ids.add(issuer.issuer_id)
        ids.update(issuer.documents)
    ids.update(full.uncovered_references.members)
    assert ids, "the seed should produce at least one traceable member"
    for entity_id in ids:
        entity = full.entities.get(entity_id)
        assert entity is not None, f"{entity_id} is named by a figure but carries no label"
        assert entity.label and entity.kind


def test_entities_carries_only_what_the_figures_name(full):
    """The payload grows with the figures, not with the graph."""
    assert len(full.entities) < full.nodes


# --- the figures themselves, against the live seed ---------------------------------
def test_counts_agree_with_the_store(store, full):
    counts = store.counts()
    assert full.nodes == counts["nodes"]
    assert full.edges == counts["edges"]
    assert full.documents == len(store.open_nodes_of_kind(NodeKind.DOCUMENT))
    by_kind = {k.kind: k.n for k in full.kinds}
    for kind in (NodeKind.PACKAGE, NodeKind.TEST, NodeKind.PATIENT):
        assert by_kind[kind.value] == len(store.open_nodes_of_kind(kind))
    assert sum(by_kind.values()) == full.nodes
    # The subject kind is flagged as one, from the pack's declaration.
    assert {k.kind for k in full.kinds if k.subject} == {NodeKind.PATIENT.value}


def test_patients_is_seen_here_out_of_on_file_and_the_two_are_not_the_same_number(store, full):
    """`patients` is a `Count`, not an `int`, and this is why.

    ``of`` is every PATIENT node — rows in the clinic's file. ``n`` is the rows some document
    claims — an upper bound on people who came through the door. As a bare int the key meant
    whichever the seed happened to produce, and the seed changed. Asserting them SEPARATELY, and
    asserting they DIFFER, is what stops the figure quietly becoming the other one again.
    """
    on_file = len(store.open_nodes_of_kind(NodeKind.PATIENT))
    claimed = {r["dst_id"] for r in store.open_edges()
               if r["kind"] == EdgeKind.FOR_PATIENT.value}
    assert full.subjects.of == on_file
    assert full.subjects.n == len(claimed)
    assert 0 < full.subjects.n < full.subjects.of, (
        "seen-here and on-file must be different numbers, or this Count is decoration")
    # the members are the ids counted, so a reader can walk to them rather than trust the number
    assert set(full.subjects.members) == claimed
    assert "registry rows" in full.subjects.label


def test_issuer_mix_documents_sum_to_the_issued_edges(store, full):
    issued = [r for r in store.open_edges() if r["kind"] == EdgeKind.ISSUED.value]
    assert sum(i.n for i in full.issuer_mix) == len(issued)
    assert full.issuer_mix == sorted(full.issuer_mix, key=lambda i: (-i.n, i.issuer))
    for issuer in full.issuer_mix:
        assert full.entities[issuer.issuer_id].kind == NodeKind.ORG.value


def test_ambiguity_members_are_documents_with_more_than_one_for_patient_edge(store, full):
    per_doc: dict[str, set[str]] = {}
    for r in store.open_edges():
        if r["kind"] == EdgeKind.FOR_PATIENT.value:
            per_doc.setdefault(r["src_id"], set()).add(r["dst_id"])
    expected = {d for d, pats in per_doc.items() if len(pats) > 1}
    assert set(full.ambiguity.members) == expected
    assert full.ambiguity.of == len(per_doc)
    for doc_id in full.ambiguity.members:
        assert full.entities[doc_id].kind == NodeKind.DOCUMENT.value


def test_ambiguity_reports_the_count_and_never_a_chosen_candidate(full):
    """Hard constraint 1, read from this module's side: the payload has no field that could be
    mistaken for 'and the answer is this one'."""
    payload = full.to_dict()
    flat = str(payload).lower()
    for forbidden in ("selected", "best_match", "resolved_patient", "top_candidate", "score"):
        assert forbidden not in flat
    # The ambiguity figure names DOCUMENTS (which need a human) — never one patient per document.
    for doc_id in payload["ambiguity"]["members"]:
        assert payload["entities"][doc_id]["kind"] == NodeKind.DOCUMENT.value


def test_unlinked_documents_are_documents_with_no_for_patient_edge(store, full):
    linked = {r["src_id"] for r in store.open_edges() if r["kind"] == EdgeKind.FOR_PATIENT.value}
    docs = {r["id"] for r in store.open_nodes_of_kind(NodeKind.DOCUMENT)}
    assert set(full.unlinked_documents.members) == docs - linked
    assert full.unlinked_documents.of == len(docs)


def test_conflicts_are_registry_rows_claimed_by_more_than_one_issuer(store, full):
    issuer_of = {r["dst_id"]: r["src_id"] for r in store.open_edges()
                 if r["kind"] == EdgeKind.ISSUED.value}
    docs_by_patient: dict[str, set[str]] = {}
    for r in store.open_edges():
        if r["kind"] == EdgeKind.FOR_PATIENT.value:
            docs_by_patient.setdefault(r["dst_id"], set()).add(r["src_id"])
    expected = {p for p, docs in docs_by_patient.items()
                if len({issuer_of[d] for d in docs if d in issuer_of}) > 1}
    assert set(full.conflicts.members) == expected
    assert full.conflicts.of == len(docs_by_patient)


def test_uncovered_references_are_the_tests_requested_but_never_included(store, full):
    """Derived generically (REFERENCES from a document, minus PART_OF from an agreement), it must
    land exactly on the clinical reading: tests a document requests that no package includes."""
    included = {r["dst_id"] for r in store.open_edges()
                if r["kind"] == EdgeKind.INCLUDES_TEST.value}
    requested = {r["dst_id"] for r in store.open_edges()
                 if r["kind"] == EdgeKind.REQUESTS_TEST.value}
    expected = requested - included
    assert expected, "the seed has a coverage gap, or this asserts nothing"
    assert set(full.uncovered_references.members) == expected
    assert full.uncovered_references.of == len(store.open_nodes_of_kind(NodeKind.TEST))
    assert all(full.entities[t].kind == NodeKind.TEST.value
               for t in full.uncovered_references.members)


def test_rankings_never_rank_a_subject(full):
    """Most-connected and recently-ingested are rankings; ranking registry rows would be ranking
    people. They are derived from `subject_kinds`, so the clinic's patients are never in them."""
    ranked = [*full.most_connected, *full.recently_ingested]
    assert ranked, "nothing ranked, so nothing checked"
    assert all(full.entities[r.id].kind != NodeKind.PATIENT.value for r in ranked)
    assert full.most_connected == sorted(full.most_connected, key=lambda r: (-r.value, r.id))


def test_documents_by_source_and_cross_source_are_counted_from_pointers(store, full):
    docs = {r["id"] for r in store.open_nodes_of_kind(NodeKind.DOCUMENT)}
    listed = {d for s in full.documents_by_source for d in s.documents}
    assert listed == docs, "every document cites at least one source"
    for member in full.cross_source.members:
        cited = {p.source_id for p in store.sources_for(member)}
        assert len(cited) >= 2
    for member in full.orphans.members:
        assert not any(member in (e["src_id"], e["dst_id"]) for e in store.open_edges())


# --- the bi-temporal rewind --------------------------------------------------------
def test_as_of_before_any_document_exists_shows_the_config_but_no_paperwork(store):
    early = analytics.compute(store, as_of=dt.datetime(2026, 1, 2, tzinfo=UTC))
    assert early.documents == 0
    assert early.issuer_mix == []
    assert early.ambiguity.n == 0 and early.ambiguity.of == 0
    # The rules and the registry predate the paperwork, so they are still there — on file. But
    # nobody has been SEEN yet, because no document exists to claim anyone. The two halves of the
    # Count move independently, which is the property a bare int could not express.
    assert early.subjects.of > 0
    assert {k.kind for k in early.kinds} >= {NodeKind.PACKAGE.value, NodeKind.PATIENT.value}
    assert early.subjects.n == 0
    assert early.subjects.members == ()


def test_documents_accumulate_monotonically_over_the_seed_window(store):
    dates = [dt.datetime(2026, m, d, tzinfo=UTC)
             for m, d in [(1, 2), (6, 15), (8, 1), (8, 5), (8, 31)]]
    seen = [analytics.compute(store, as_of=d).documents for d in dates]
    assert seen == sorted(seen), seen
    assert seen[0] == 0 and seen[-1] == analytics.compute(store).documents


def test_as_of_never_counts_an_edge_whose_endpoints_are_not_yet_valid(store):
    """T-30's invariant, read from the analytics side. Before it, seed edges predated their own
    endpoints and this view counted issuers for documents that did not exist yet."""
    for day in (2, 15, 28):
        as_of = dt.datetime(2026, 6, day, tzinfo=UTC)
        view = analytics.compute(store, as_of=as_of)
        # every issued document in the mix must itself be valid at `as_of`
        live_docs = {r["id"] for r in store.open_nodes()
                     if r["kind"] == NodeKind.DOCUMENT.value
                     and dt.datetime.fromisoformat(r["valid_from"]) <= as_of}
        for issuer in view.issuer_mix:
            assert set(issuer.documents) <= live_docs
        assert view.documents == len(live_docs)


# --- R-017 (Codex t08) ---------------------------------------------------------------------
def _tiny_store():
    """A fresh in-memory store with one issuer and documents, built here so the expectations
    below are read off what was written, not off the seed."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphEdge, GraphNode
    from databridge_graph.store import GraphStore

    jan = dt.datetime(2026, 1, 1, tzinfo=UTC)
    store = GraphStore(clock=lambda: dt.datetime(2026, 9, 1, tzinfo=UTC))
    ptr = SourcePointer(source_id="tiny", kind="table_cell", table="t", column="c", row_id="1")

    def node(nid, kind, *, valid_from):
        store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=nid, sources=[ptr],
                                 valid_from=valid_from, system_from=jan))

    def edge(src, kind, dst, *, valid_from):
        store.add_edge(GraphEdge(id=f"{src}|{kind}|{dst}", src_id=src, dst_id=dst,
                                 kind=EdgeKind(kind), sources=[ptr], valid_from=valid_from,
                                 system_from=jan))
    return store, node, edge, jan


def test_as_of_reads_a_fact_whose_validity_ended_after_it_was_superseded():
    """t08 #6. A document valid February–August, closed in September WITH that validity end
    recorded, was true in March. Filtering only today's open rows made it vanish from every
    historical view; a retraction (closed, no validity end, no successor) must still not return."""
    store, node, edge, jan = _tiny_store()
    feb, aug, sep = (dt.datetime(2026, m, 1, tzinfo=UTC) for m in (2, 8, 9))
    node("org:o1", "org", valid_from=jan)
    node("document:ended", "document", valid_from=feb)
    node("document:retracted", "document", valid_from=feb)
    edge("org:o1", "issued", "document:ended", valid_from=feb)
    store.close_node("document:ended", at=sep, valid_to=aug)
    store.close_edge("org:o1|issued|document:ended", at=sep, valid_to=aug)
    store.close_node("document:retracted", at=sep)            # withdrawn: validity end unknown

    march = analytics.compute(store, as_of=dt.datetime(2026, 3, 1, tzinfo=UTC))
    assert march.documents == 1
    assert [i.documents for i in march.issuer_mix] == [("document:ended",)]
    assert analytics.compute(store, as_of=dt.datetime(2026, 8, 15, tzinfo=UTC)).documents == 0
    assert analytics.compute(store).documents == 0


def test_as_of_reads_a_superseded_version_when_its_successor_starts_later():
    """A replaced node (validity end unknown, the default) is still what was believed about the
    instant before its successor's validity began."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode

    store, node, _edge, jan = _tiny_store()
    feb, jun, sep = (dt.datetime(2026, m, 1, tzinfo=UTC) for m in (2, 6, 9))
    node("document:d", "document", valid_from=feb)
    ptr = SourcePointer(source_id="tiny", kind="table_cell", table="t", column="c", row_id="2")
    store.replace_node(GraphNode(id="document:d", kind=NodeKind("document"), label="v2",
                                 sources=[ptr], valid_from=jun, system_from=sep), at=sep)
    march = analytics.compute(store, as_of=dt.datetime(2026, 3, 1, tzinfo=UTC))
    july = analytics.compute(store, as_of=dt.datetime(2026, 7, 1, tzinfo=UTC))
    assert march.documents == 1 and july.documents == 1
    assert analytics.compute(store, as_of=dt.datetime(2026, 1, 15, tzinfo=UTC)).documents == 0


def test_a_document_with_two_issuers_contributes_both_to_conflicts():
    """t08 #8. `issuer_of[document]` kept only the LAST issued edge, so a document issued by two
    parties could drop the one that makes a subject's claims cross-issuer."""
    store, node, edge, jan = _tiny_store()
    for org in ("org:o1", "org:o2"):
        node(org, "org", valid_from=jan)
    node("patient:p", "patient", valid_from=jan)
    for doc in ("document:a", "document:b"):
        node(doc, "document", valid_from=jan)
        edge(doc, "for_patient", "patient:p", valid_from=jan)
    edge("org:o1", "issued", "document:a", valid_from=jan)
    # document:b is issued by BOTH; o2 is written first, so "last edge wins" keeps o1.
    edge("org:o2", "issued", "document:b", valid_from=jan)
    edge("org:o1", "issued", "document:b", valid_from=jan)
    out = analytics.compute(store)
    assert out.conflicts.members == ("patient:p",)


# --- R-019 (Codex batch 2, t08b) ----------------------------------------------------------------
class _NoVersionAddressing:
    """A reader that can list every version but cannot address a version's pointers — the shape
    under which `_row_sources` used to fall back, silently, to the CURRENT version's pointers."""

    def __init__(self, store):
        self._store = store
        self.open_nodes = store.open_nodes
        self.open_edges = store.open_edges
        self.fetchall = store.fetchall

    def sources_for(self, entity_id, **kwargs):
        if kwargs:
            raise TypeError("no version addressing")
        return self._store.sources_for(entity_id)


def test_a_historical_row_is_never_cited_with_the_current_versions_pointers():
    """t08b #1. A closed version whose own pointers cannot be read is reported as evidence
    unavailable — never filled from the pointers of the version open today."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode

    store, node, _edge, jan = _tiny_store()
    feb, jun, sep = (dt.datetime(2026, m, 1, tzinfo=UTC) for m in (2, 6, 9))
    node("document:d", "document", valid_from=feb)
    today = SourcePointer(source_id="present-day", kind="table_cell", table="t", column="c",
                          row_id="2")
    store.replace_node(GraphNode(id="document:d", kind=NodeKind("document"), label="v2",
                                 sources=[today], valid_from=jun, system_from=sep), at=sep)
    march = analytics.compute(_NoVersionAddressing(store),
                              as_of=dt.datetime(2026, 3, 1, tzinfo=UTC))
    assert march.documents == 1, "the historical version is in the snapshot"
    cited = {s.source for s in march.documents_by_source}
    assert "present-day" not in cited, "today's pointer was cited for March's version"
    assert march.evidence_unavailable.members == ("document:d",)
    # And the OPEN graph still reads the open version's pointers through the same reader.
    now = analytics.compute(_NoVersionAddressing(store))
    assert {s.source for s in now.documents_by_source} == {"present-day"}
    assert now.evidence_unavailable.n == 0


class _OpenOnly:
    def __init__(self, store):
        self.open_nodes = store.open_nodes
        self.open_edges = store.open_edges
        self.sources_for = store.sources_for


def test_a_historical_read_without_version_access_is_refused_not_undercounted():
    """t08b #6. A reader that sees only open rows cannot rewind; it used to return ordinary-looking
    figures that dropped every fact closed since."""
    store, node, _edge, jan = _tiny_store()
    node("document:gone", "document", valid_from=jan)
    store.close_node("document:gone", at=dt.datetime(2026, 9, 1, tzinfo=UTC),
                     valid_to=dt.datetime(2026, 8, 1, tzinfo=UTC))
    march = dt.datetime(2026, 3, 1, tzinfo=UTC)
    assert analytics.compute(store, as_of=march).documents == 1, "precondition: it existed"
    with pytest.raises(analytics.SnapshotIncomplete):
        analytics.compute(_OpenOnly(store), as_of=march)
    # The open graph needs no history, so the same reader still answers for "now".
    assert analytics.compute(_OpenOnly(store)).snapshot_basis == "open"


def test_uncovered_references_exist_before_any_agreement_includes_anything():
    """t08b #7. Which kinds an agreement can include is DECLARED; observing no PART_OF edge yet
    must not make the figure disappear exactly when every reference is uncovered."""
    from databridge_graph.packs import active_vocabulary
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind

    vocab = active_vocabulary()
    declared = {k for d in vocab.edge_kinds.values() if d.binds_to is EdgeMetaKind.PART_OF
                and any(vocab.node_kinds.get(s) is not None
                        and vocab.node_kinds[s].binds_to is NodeMetaKind.AGREEMENT
                        for s in d.src if s != "*")
                for k in d.dst if k != "*"}
    refs = [n for n, d in vocab.edge_kinds.items() if d.binds_to is EdgeMetaKind.REFERENCES
            and set(d.dst) & declared and "document" in d.src]
    assert declared and refs, "the bound vocabulary declares no includable kind; nothing to test"
    target_kind = sorted(set(vocab.edge_kinds[refs[0]].dst) & declared)[0]
    store, node, edge, jan = _tiny_store()
    node("document:r", "document", valid_from=jan)
    node(f"{target_kind}:t1", target_kind, valid_from=jan)
    edge("document:r", refs[0], f"{target_kind}:t1", valid_from=jan)
    out = analytics.compute(store)
    assert out.uncovered_references is not None
    assert out.uncovered_references.members == (f"{target_kind}:t1",)


def test_a_dangling_edge_is_skipped_and_counted_not_a_crash():
    """E2E F-03. An edge whose endpoint node does not exist (a foreign key to a row never
    ingested) raised KeyError in the includable-kinds pass and took `/intelligence/ask` down."""
    store, node, edge, jan = _tiny_store()
    node("org:o1", "org", valid_from=jan)
    node("document:d", "document", valid_from=jan)
    edge("org:o1", "issued", "document:d", valid_from=jan)
    edge("org:o1", "issued", "document:missing", valid_from=jan)
    edge("package:missing", "includes_test", "test:missing", valid_from=jan)
    dangling = [e for e in store.open_edges()
                if not (store.open_node_row(e["src_id"]) and store.open_node_row(e["dst_id"]))]
    assert dangling, "the fixture wrote no dangling edge; nothing to test"
    out = analytics.compute(store)
    assert out.dangling_edges == len(dangling)
    assert out.edges == len(store.open_edges()) - len(dangling)
    assert [i.documents for i in out.issuer_mix] == [("document:d",)]
