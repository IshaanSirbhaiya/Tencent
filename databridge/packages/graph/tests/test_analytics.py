"""`analytics.compute` — the graph-derived operational aggregates (T-43).

Until this file existed the module had **no tests at all**, which is how its `as_of` comparison
shipped raising `TypeError` on a naive datetime: nothing called it, so nothing found out.

What these tests are actually guarding:

1. every figure carries its denominator and the ids behind it (a count nobody can walk to is
   indistinguishable from a literal);
2. `as_of` is a real bi-temporal rewind, not decoration — and it never returns an edge whose
   endpoints do not exist in that rewound view (the T-30 invariant, read from the other side);
3. nothing here narrows a `FOR_PATIENT` claim to one candidate (CLAUDE.md §Hard constraints 1).
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
    ids.update(t.id for t in full.tests_outside_any_package)
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
    assert full.patients == len(store.open_nodes_of_kind(NodeKind.PATIENT))
    assert full.packages == len(store.open_nodes_of_kind(NodeKind.PACKAGE))
    assert full.tests_in_catalogue == len(store.open_nodes_of_kind(NodeKind.TEST))


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


def test_tests_outside_any_package_are_requested_but_never_included(store, full):
    included = {r["dst_id"] for r in store.open_edges()
                if r["kind"] == EdgeKind.INCLUDES_TEST.value}
    requested = {r["dst_id"] for r in store.open_edges()
                 if r["kind"] == EdgeKind.REQUESTS_TEST.value}
    assert {t.id for t in full.tests_outside_any_package} == requested - included
    assert all(t.kind == NodeKind.TEST.value for t in full.tests_outside_any_package)


# --- the bi-temporal rewind --------------------------------------------------------
def test_as_of_before_any_document_exists_shows_the_config_but_no_paperwork(store):
    early = analytics.compute(store, as_of=dt.datetime(2026, 1, 2, tzinfo=UTC))
    assert early.documents == 0
    assert early.issuer_mix == []
    assert early.ambiguity.n == 0 and early.ambiguity.of == 0
    # The rules and the registry predate the paperwork, so they are still there.
    assert early.patients > 0 and early.packages > 0


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
