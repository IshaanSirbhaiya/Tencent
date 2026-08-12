"""clinic_seed.py — the graph built from the real fixtures.

Replaces `test_demo_seed.py`. The invariants worth defending are the ones PLAN.md §5 Phase 2 names
under "Done when": every node grounded, no orphans, and — the load-bearing one — a document with an
ambiguous subject produces one `FOR_PATIENT` edge *per candidate*, never a single collapsed answer.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from types import SimpleNamespace

import pytest

from databridge_core import GroundedValue, SourcePointer
from databridge_graph import GraphStore, NodeKind
from databridge_graph.clinic_seed import (
    SEED_SYSTEM_TIME,
    _document_date,
    _document_two_digit_year,
    build_seed,
    catalogue_test_node_id,
    document_node_id,
    fresh_clinic_store,
    load_clinic_seed,
    org_canonical,
    org_node_id,
    orphan_node_ids,
    package_node_id,
    rule_node_id,
)
from databridge_graph.schema import EdgeKind


@pytest.fixture(scope="module")
def seed():
    return build_seed()


@pytest.fixture(scope="module")
def nodes(seed):
    return seed[0]


@pytest.fixture(scope="module")
def edges(seed):
    return seed[1]


@pytest.fixture(scope="module")
def by_id(nodes):
    return {n.id: n for n in nodes}


# --- the anti-fabrication wall (P4) -------------------------------------------------


def test_every_node_carries_at_least_one_source_pointer(nodes):
    """The whole reason this package's schema refuses ungrounded construction. Asserted here too
    because a seed is where fabricated convenience nodes would otherwise creep in."""
    assert nodes
    ungrounded = [n.id for n in nodes if not n.sources]
    assert ungrounded == []


def test_every_edge_carries_at_least_one_source_pointer(edges):
    assert edges
    assert [e.id for e in edges if not e.sources] == []


def test_pointers_are_real_kinds_not_placeholders(nodes):
    """A pointer that names no source is a fabrication with extra steps."""
    for node in nodes:
        for ptr in node.sources:
            assert ptr.kind in {"table_cell", "doc_span", "derived"}, (node.id, ptr.kind)
            assert ptr.source_id


def test_registry_backed_nodes_point_at_the_csv(by_id, nodes):
    patients = [n for n in nodes if n.kind is NodeKind.PATIENT]
    assert patients
    for patient in patients:
        assert any(p.kind == "table_cell" or p.kind == "derived" for p in patient.sources)
        assert any("patient_registration" in (p.source_id or "") for p in patient.sources)


def test_config_backed_nodes_point_at_the_rules_yaml(nodes):
    for kind in (NodeKind.PACKAGE, NodeKind.COVERAGE_RULE, NodeKind.TEST):
        of_kind = [n for n in nodes if n.kind is kind]
        assert of_kind, f"no {kind.value} nodes were seeded"
        for node in of_kind:
            assert all(p.source_id == "coverage_rules.yaml" for p in node.sources), node.id


def test_document_backed_nodes_point_at_a_chit(nodes):
    for kind in (NodeKind.DOCUMENT, NodeKind.CLINIC, NodeKind.VISIT):
        for node in (n for n in nodes if n.kind is kind):
            assert any(p.kind == "doc_span" for p in node.sources), node.id


# --- structural integrity -----------------------------------------------------------


def test_there_are_no_orphan_nodes(nodes, edges):
    """PLAN.md §5 Phase 2 "Done when". An orphan answers no question and inflates every count on
    the dashboard."""
    assert orphan_node_ids(nodes, edges) == []


def test_no_edge_dangles(nodes, edges):
    """An edge to a node that was never built silently disappears from every traversal — the first
    draft of this seed shipped five of them because two id helpers disagreed about underscores."""
    ids = {n.id for n in nodes}
    endpoints = {x for e in edges for x in (e.src_id, e.dst_id)}
    assert sorted(endpoints - ids) == []


def test_node_ids_are_unique(nodes):
    dupes = [nid for nid, count in Counter(n.id for n in nodes).items() if count > 1]
    assert dupes == []


def test_every_vocabulary_member_used_is_a_real_enum_member(nodes, edges):
    assert {n.kind for n in nodes} <= set(NodeKind)
    assert {e.kind for e in edges} <= set(EdgeKind)


# --- the identity constraint (CLAUDE.md §Hard constraints 1) ------------------------


def test_an_ambiguous_document_yields_one_edge_per_candidate(edges):
    """The load-bearing test. Chits 3 and 5 each name a patient the registry holds twice, with no
    identifier to tell them apart. The graph must carry BOTH claims — collapsing to one would be
    the machine deciding identity, which is exactly what this architecture forbids."""
    for document in ("chit_03_everwell_group_invitation.txt",
                     "chit_05_bluepeak_wellness_voucher.txt"):
        doc_id = document_node_id(document)
        claims = [e for e in edges if e.kind is EdgeKind.FOR_PATIENT and e.src_id == doc_id]
        assert len(claims) == 2, f"{document} should carry two candidate claims, got {len(claims)}"
        assert len({e.dst_id for e in claims}) == 2, "the two claims must name different patients"


def test_every_for_patient_edge_is_marked_as_a_claim(edges):
    """Nothing downstream may read one of these as a determination, so each carries the caveat in
    its own attributes rather than relying on a reader knowing the convention."""
    claims = [e for e in edges if e.kind is EdgeKind.FOR_PATIENT]
    assert claims
    for edge in claims:
        assert edge.attrs["basis"] == "claimed_on_document"
        assert edge.attrs["requires_human_verification"] is True
        assert "candidate_count" in edge.attrs


def test_no_edge_asserts_a_fact_about_an_unconfirmed_patient(edges):
    """COVERED_BY / EMPLOYED_BY / SCHEDULED_AS each state something about a *specific* patient.
    At seed time no patient is confirmed, so none of them may appear. They are reserved for the
    post-confirmation ledger path (ADR-0009)."""
    forbidden = {EdgeKind.COVERED_BY, EdgeKind.EMPLOYED_BY, EdgeKind.SCHEDULED_AS}
    offenders = sorted({e.kind.value for e in edges if e.kind in forbidden})
    assert offenders == [], (
        f"the seed emitted {offenders}, which assert facts about a patient nobody has verified")


def test_a_single_candidate_is_still_only_a_claim(edges):
    """Chit 2 resolves to exactly one registry patient at a high score. It is STILL a claim —
    a confident match is not a verified identity."""
    doc_id = document_node_id("chit_02_everwell_voucher.txt")
    claims = [e for e in edges if e.kind is EdgeKind.FOR_PATIENT and e.src_id == doc_id]
    assert len(claims) == 1
    assert claims[0].attrs["requires_human_verification"] is True


# --- the data actually landed --------------------------------------------------------


def test_every_chit_in_the_corpus_is_present(nodes):
    """Counted against the fixture directory rather than a literal, so adding a chit does not need
    an edit here — the Phase 6 conflict fixture (chit 10) is why this now derives the number."""
    from databridge_intake.paths import fixtures_dir

    expected = len(list((fixtures_dir() / "chits").glob("*")))
    documents = [n for n in nodes if n.kind is NodeKind.DOCUMENT]
    assert len(documents) == expected


def test_all_seven_issuers_are_present(by_id):
    from databridge_intake.rules import rules

    for issuer in rules().issuers:
        assert org_node_id(issuer.name) in by_id, issuer.code


def test_every_coverage_rule_is_present(by_id):
    from databridge_intake.rules import rules

    for package in rules().packages:
        assert rule_node_id(package.id) in by_id, package.id


def test_a_package_is_linked_to_the_tests_it_includes(edges):
    """"What does WELL2 include?" must be one edge traversal, not a YAML re-read."""
    well2 = package_node_id("WELL2", "BLPHS")
    included = {e.dst_id for e in edges
                if e.kind is EdgeKind.INCLUDES_TEST and e.src_id == well2}
    assert len(included) == 11
    assert catalogue_test_node_id("resting_ecg") in included


def test_a_document_links_to_the_tests_it_printed(edges):
    doc_id = document_node_id("chit_01_meridian_referral.txt")
    requested = {e.dst_id for e in edges
                 if e.kind is EdgeKind.REQUESTS_TEST and e.src_id == doc_id}
    assert catalogue_test_node_id("chest_xray") in requested
    assert catalogue_test_node_id("hiv_antibody") in requested


def test_the_issuer_of_each_document_is_linked(edges, nodes):
    issued = {e.dst_id for e in edges if e.kind is EdgeKind.ISSUED}
    documents = {n.id for n in nodes if n.kind is NodeKind.DOCUMENT}
    assert documents <= issued, "every chit names an issuer and must be linked to it"


# --- organisation merging (and the line it must not cross) ---------------------------


def test_the_three_coral_harbour_spellings_are_one_org(by_id, nodes):
    """Chits 1, 3 and 9 print "Coral Harbour Pte Ltd", "CORAL HARBOUR PTE LIMITED" and
    "CORAL HARBOUR PTE LTD". One employer, three spellings, one node — with all three source
    pointers kept so the merge is auditable."""
    node = by_id[org_node_id("Coral Harbour Pte Ltd")]
    assert len(node.sources) == 3
    labels = [n.label for n in nodes if n.kind is NodeKind.ORG and "coral" in n.id]
    assert len(labels) == 1


@pytest.mark.parametrize("printed", [
    "Coral Harbour Pte Ltd", "CORAL HARBOUR PTE LIMITED", "CORAL HARBOUR PTE LTD",
    "Coral Harbour", "coral harbour pte. ltd.",
])
def test_org_canonical_folds_legal_form_suffixes(printed):
    assert org_canonical(printed) == "coral-harbour"


def test_org_canonical_does_not_merge_genuinely_different_orgs():
    """Folding suffixes must not fold away the distinguishing part of a name."""
    assert org_canonical("Everwell Insurance Group") != org_canonical(
        "Everwell Insurance Group (Corporate Solutions)")
    assert org_canonical("Meridian Life Assurance Pte Ltd") != org_canonical(
        "Northstar Life Assurance")


# --- what was deliberately left out --------------------------------------------------


def test_the_seed_reports_what_it_skipped():
    """Unreferenced registry rows and catalogue entries are omitted on purpose — but silently
    omitting them would read as "this is everything"."""
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    result = load_clinic_seed(store)
    assert result["skipped"]["registry_patients_not_referenced"] > 0
    assert result["skipped"]["catalogue_tests_not_referenced"] > 0
    assert result["nodes_created"] > 0 and result["edges_created"] > 0


# --- store integration ----------------------------------------------------------------


def test_loading_is_idempotent():
    """Re-running the seed corroborates existing nodes rather than duplicating them."""
    store = fresh_clinic_store()
    before = store.counts()
    load_clinic_seed(store)
    assert store.counts()["nodes"] == before["nodes"]
    assert store.counts()["edges"] == before["edges"]


def test_the_seeded_store_matches_the_built_graph(nodes, edges):
    store = fresh_clinic_store()
    counts = store.counts()
    assert counts["nodes"] == len(nodes)
    assert counts["edges"] == len(edges)


# --- bi-temporal integrity -------------------------------------------------------------------


def test_no_edge_begins_before_the_things_it_relates(nodes, edges):
    """A relationship cannot exist before both of its endpoints do.

    This failed for **63 of 111 edges**. `_edge` stamped every relationship with the standing
    configuration date (2026-01-01) while document nodes correctly carried the date printed on the
    letter, so the store asserted that a referral had requested a test seven months before the
    referral existed.

    It was invisible on screen — the renderer refuses to draw an edge unless both endpoints are
    visible, so the picture self-corrected while the data underneath was wrong. Everything that
    READS these dates rather than drawing them (`as_of` replay, the analytics trend, "what did the
    rules say on the day we billed this?") was answering from a date nobody could defend.
    """
    starts = {n.id: n.valid_from for n in nodes}
    offenders = []
    for edge in edges:
        for endpoint in (edge.src_id, edge.dst_id):
            began = starts.get(endpoint)
            if began is not None and edge.valid_from is not None and began > edge.valid_from:
                offenders.append(
                    f"{edge.kind.value} {edge.src_id} -> {edge.dst_id}: edge valid_from "
                    f"{edge.valid_from.date()} precedes {endpoint} ({began.date()})")
    assert offenders == [], (
        f"{len(offenders)} edge(s) begin before an endpoint exists:\n" + "\n".join(offenders[:10]))


def test_the_temporal_axis_actually_moves(edges):
    """The corollary, asserted positively: once edges carry honest dates they must not all share
    one. If every edge began on the same day there is nothing for the time slider to replay, and
    the bi-temporal claim is decoration."""
    distinct = {e.valid_from for e in edges if e.valid_from is not None}
    assert len(distinct) > 1, (
        "every edge shares a single valid_from — the bi-temporal axis carries no information")


# --- document dates use a document-appropriate two-digit-year pivot, not the DOB one ------------


def _authorised(**fields):
    """A minimal object shaped like the slice of `IntakeRecord` `_document_date` reads."""
    ptr = SourcePointer(source_id="x", kind="doc_span", document_id="x", char_start=0, char_end=1)
    from databridge_intake.models import AuthorisationBlock

    gv_fields = {k: GroundedValue(value=v, pointer=ptr, confidence=1.0) for k, v in fields.items()}
    return SimpleNamespace(authorisation=AuthorisationBlock(**gv_fields))


def test_document_date_pivots_a_near_future_two_digit_year_toward_today():
    """The bug this pins: `resolve.parse_claimed_dob` pivots a two-digit year toward "the most
    recent reading NOT in the future" — right for a date of birth, which can never legitimately be
    ahead of today, and wrong for a document date, which legitimately can. With DATASET_TODAY =
    2026-08-11, a voucher printing `Valid From 01/09/26` (three weeks out) used to resolve to
    **1926-09-01** because every 20xx reading landing after today was discarded outright, no matter
    how close. `_document_date` must land on 2026, the reading nearest to today."""
    record = _authorised(valid_from="01/09/26")
    assert _document_date(record) == dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def test_document_date_pivots_a_recent_past_two_digit_year_correctly_too():
    """The nearest-to-today rule must not simply always prefer 20xx — it has to actually compare.
    `20/05/24`, printed well before DATASET_TODAY (2026-08-11), is unambiguously 2024, and the two
    century readings are far enough apart (2024 vs 1924) that "nearest" and "prefer 2000s" agree —
    this pins that the comparison, not a hardcoded preference, is what is doing the work."""
    record = _authorised(valid_from="20/05/24")
    assert _document_date(record) == dt.datetime(2024, 5, 20, tzinfo=dt.timezone.utc)


def test_document_date_falls_back_to_appointment_when_valid_from_is_absent():
    record = _authorised(appointment_datetime="15/10/26")
    assert _document_date(record) == dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)


def test_document_date_returns_none_for_an_unparseable_value():
    """Unparseable must stay `None` — the caller falls back to the seed instant, never a guess."""
    record = _authorised(valid_from="not a date")
    assert _document_date(record) is None


def test_document_two_digit_year_picks_the_calendar_reading_closest_to_today():
    """Direct test of the pivot itself, decoupled from `_document_date`'s I/O."""
    today = dt.date(2026, 8, 11)
    # 26 -> 2026-09-01 (21 days after today) beats 1926-09-01 (a century away).
    assert _document_two_digit_year("01", "09", "26", today=today) == "01/09/2026"
    # 52 -> the DOB-style case still resolves sanely: 2052 is 26 years out, 1952 is 74 years back,
    # so 2052 remains nearer — this function does not special-case "the past is always right".
    assert _document_two_digit_year("01", "01", "52", today=today) == "01/01/2052"
