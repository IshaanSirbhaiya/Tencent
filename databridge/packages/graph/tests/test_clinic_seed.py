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
    REGISTRY_ALL,
    REGISTRY_REFERENCED,
    SEED_SYSTEM_TIME,
    _document_date,
    _document_two_digit_year,
    build_seed,
    catalogue_test_node_id,
    claimed_patient_node_ids,
    document_node_id,
    fresh_clinic_store,
    load_clinic_seed,
    org_canonical,
    org_node_id,
    orphan_node_ids,
    package_node_id,
    patient_census,
    patient_node_id,
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


def _unexpected_orphans(nodes, edges) -> list[str]:
    """Orphans that are NOT a patient the clinic merely has on file.

    PLAN.md §5 Phase 2's "no orphans" is still the property being defended; what changed is that
    one class of orphan is now deliberate. Seeding the whole registry puts 289 patients in the
    graph that no document names, and **an incoming ``FOR_PATIENT`` edge is exactly what tells a
    patient the clinic has SEEN from one it merely has ON FILE**. Those are unconnected because
    there is honestly nothing to connect them to — not because a builder dropped an edge.

    Everything else is still a bug, so the exclusion is written as narrowly as it can be: the node
    must be a PATIENT *and* carry no identity claim. A stranded ORG, a package whose test edges
    failed to build, or a patient a document names that somehow lost its edge, all still fail.
    """
    by_id = {n.id: n for n in nodes}
    on_file_only = ({n.id for n in nodes if n.kind is NodeKind.PATIENT}
                    - set(claimed_patient_node_ids(nodes, edges)))
    return [nid for nid in orphan_node_ids(nodes, edges)
            if not (by_id[nid].kind is NodeKind.PATIENT and nid in on_file_only)]


def test_the_only_orphan_is_a_patient_the_clinic_has_never_seen(nodes, edges):
    """PLAN.md §5 Phase 2 "Done when", as it now reads. An orphan answers no question — except
    "does the clinic have anyone by this name on file?", which is the first question anyone asks a
    clinic system and the one the 289 unreferenced registry rows exist to answer."""
    assert _unexpected_orphans(nodes, edges) == []
    # …and the deliberate ones are exactly the census's `on_file_only`, so this cannot quietly
    # become "any orphan is fine": the number is pinned to a figure computed a different way.
    census = patient_census(nodes, edges)
    assert census["on_file_only"] > 0
    assert len(orphan_node_ids(nodes, edges)) == census["on_file_only"]


def test_seeding_only_referenced_patients_leaves_no_orphan_at_all(nodes, edges):
    """The older shape is still reachable and still perfectly connected — which is the proof that
    the orphans above come from the option and not from a broken builder."""
    only_referenced = build_seed(registry_patients=REGISTRY_REFERENCED)
    assert orphan_node_ids(only_referenced[0], only_referenced[1]) == []
    assert len(only_referenced[0]) < len(nodes)


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
    """Unreferenced catalogue entries are omitted on purpose — but silently omitting them would
    read as "this is everything".

    ``registry_patients_not_referenced`` is 0 under the DEFAULT and that is the assertion, not an
    absence of one: the whole registry is seeded, so nothing is missing, and a non-zero value here
    would mean rows were lost. The number that used to be interesting moved to ``patients``, which
    is a fact about the clinic rather than about this function's arguments.
    """
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    result = load_clinic_seed(store)
    assert result["skipped"]["registry_patients_not_referenced"] == 0
    assert result["skipped"]["catalogue_tests_not_referenced"] > 0
    assert result["nodes_created"] > 0 and result["edges_created"] > 0


def test_the_skipped_registry_count_goes_non_zero_when_the_registry_is_not_seeded():
    """The same field, under the option that makes it mean something. Reported in one place
    regardless of which option produced it — otherwise "how much of the registry is missing?" has
    two answers and neither is checked."""
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    result = load_clinic_seed(store, registry_patients=REGISTRY_REFERENCED)
    assert result["skipped"]["registry_patients_not_referenced"] > 0
    # …and under this option every patient in the graph IS one a document names, so the census's
    # other number goes to zero. The two numbers move in opposite directions and are computed by
    # different code, which is what stops either being read as the other.
    assert result["patients"]["on_file_only"] == 0
    assert result["patients"]["claimed_by_a_document"] == result["patients"]["on_file"] > 0


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
    assert _document_date(record)[0] == dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def test_document_date_pivots_a_recent_past_two_digit_year_correctly_too():
    """The nearest-to-today rule must not simply always prefer 20xx — it has to actually compare.
    `20/05/24`, printed well before DATASET_TODAY (2026-08-11), is unambiguously 2024, and the two
    century readings are far enough apart (2024 vs 1924) that "nearest" and "prefer 2000s" agree —
    this pins that the comparison, not a hardcoded preference, is what is doing the work."""
    record = _authorised(valid_from="20/05/24")
    assert _document_date(record)[0] == dt.datetime(2024, 5, 20, tzinfo=dt.timezone.utc)


def test_document_date_falls_back_to_appointment_when_valid_from_is_absent():
    record = _authorised(appointment_datetime="15/10/26")
    assert _document_date(record)[0] == dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)


def test_document_date_returns_the_pointer_the_date_was_read_from():
    """The date is also written to the document node as an attribute, and an attribute travels
    with its own citation. Returning the datetime alone forced the caller to either invent a
    pointer or print the value uncited — the second is what it did."""
    record = _authorised(valid_from="01/09/26")
    _, pointer, field = _document_date(record)
    assert pointer is record.authorisation.valid_from.pointer
    assert field == "valid_from"


def test_a_date_read_from_the_appointment_field_is_not_reported_as_the_issue_date():
    """The two printed dates do not mean the same thing, and neither means "issued". Recording an
    appointment date under `issued_at` put a pointer beside a claim it does not support."""
    record = _authorised(appointment_datetime="15/10/26")
    _, _, field = _document_date(record)
    assert field == "appointment_datetime"


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


# --- R-02: an identity claim is evidenced by the subject span, or it is not made ---------------
#
# `_doc_ptr` mints a `0:1` span when a document's length is unknown, and the FOR_PATIENT edge used
# to be built with `[subject_span or doc_ptr]`. A document naming nobody therefore produced a
# patient-identity claim whose evidence was one character of letterhead — a citation that looks
# authoritative and supports nothing, breaking constraints 1 and 2 in the same line.
#
# The fix has no fallback: with no exact subject pointer, NO edge is created. The tests below pin
# both halves — the drop itself, and that nothing in the seed is evidenced by a placeholder span.


def _blanked_subject_corpus():
    """The real corpus with ONE document's subject removed, and its candidates left in place.

    The point is to reproduce exactly the state the old fallback was written for: a resolver that
    returned candidates while the document carries no span naming a subject. Blanking the subject
    and re-resolving would return no candidates and the test would pass vacuously, so the original
    resolution is kept — the seed then has candidates it cannot cite, which is the decision under
    test.
    """
    from databridge_intake.registry import registry
    from databridge_intake.resolve import resolve
    from databridge_intake.rules import rules

    from databridge_graph.clinic_seed import _fixture_intakes

    intakes, texts = _fixture_intakes()
    target = "chit_01_meridian_referral.txt"
    resolutions = {name: resolve(record.subject) for name, record in intakes.items()}
    record = intakes[target]
    intakes[target] = record.model_copy(update={
        "subject": record.subject.model_copy(
            update={"claimed_name": None, "claimed_id_number": None})})
    return target, intakes, resolutions, rules(), {r.patient_id: r for r in registry()}, texts


def test_a_document_naming_no_subject_produces_no_for_patient_edge():
    from databridge_graph.clinic_seed import _build

    target, intakes, resolutions, table, registry_by_id, texts = _blanked_subject_corpus()
    assert resolutions[target].candidates, "the fixture must still offer candidates to drop"

    nodes, edges, _ = _build(intakes, resolutions, table, registry_by_id, texts)

    doc_id = document_node_id(target)
    claims = [e for e in edges if e.kind is EdgeKind.FOR_PATIENT and e.src_id == doc_id]
    assert claims == [], (
        "a document with no subject span must link to no candidate — we cannot say who it is "
        "about, and an edge here would assert that we can")
    # Dropping the claim must leave the graph consistent, not merely smaller: the candidate's
    # PATIENT node is built inside the same loop, so a half-applied fix would leave it stranded
    # or leave some other edge pointing at a node that was never created. The candidate patients
    # themselves become on-file-only rows — that is the honest outcome of a letter naming nobody,
    # and `_unexpected_orphans` is what tells it apart from a builder that lost an edge.
    assert _unexpected_orphans(nodes, edges) == []
    ids = {n.id for n in nodes}
    assert sorted({x for e in edges for x in (e.src_id, e.dst_id)} - ids) == []


# --- the registry option: what seeding 300 rows does, and what it must NOT do -----------
#
# `clinic_seed`'s module docstring makes four claims about `registry_patients="all"`, and every
# one of them is a claim about something NOT changing. They are tested here because a docstring
# that asserts an invariant nothing checks is how the invariant stops being true.


def test_the_identity_claims_are_identical_whichever_registry_option_is_used():
    """The docstring's strongest claim: *seeding the registry adds no relationships at all.*

    Not "roughly the same", not "the same count" — the same `FOR_PATIENT` edges, with the same
    endpoints, the same attributes and the same evidence. If seeding the other 289 rows could add
    or move even one identity claim, then who a document is about would depend on how much of the
    CSV happened to be loaded, which is a determination made by a configuration flag.
    """
    all_nodes, all_edges, _ = build_seed(registry_patients=REGISTRY_ALL)
    ref_nodes, ref_edges, _ = build_seed(registry_patients=REGISTRY_REFERENCED)

    def claims(edges):
        return sorted(
            (e.id, e.src_id, e.dst_id, e.valid_from.isoformat(),
             tuple(sorted((k, str(v)) for k, v in e.attrs.items())),
             tuple(p.model_dump_json() for p in e.sources))
            for e in edges if e.kind is EdgeKind.FOR_PATIENT)

    assert claims(all_edges) == claims(ref_edges)
    assert claims(all_edges), "the corpus must actually produce claims for this to mean anything"
    # …and NO other relationship appeared either. The registry pass writes nodes only.
    assert sorted(e.id for e in all_edges) == sorted(e.id for e in ref_edges)


def test_a_patient_no_document_names_is_built_exactly_like_one_a_document_names():
    """`_patient_node` is deliberately shared between the two passes. This proves the sharing, on
    a patient NO document names — the ~289 nobody would think to open.

    Constraint 2, at the level of the individual value: every attribute on that node carries its
    OWN pointer into the registry CSV, or it is `None` and its name is in `unresolved`. A thinner,
    less-cited shape for the rows nobody looks at is exactly the drift a second builder would
    produce, and exactly what no reviewer would catch.
    """
    nodes, edges, _ = build_seed()
    claimed = set(claimed_patient_node_ids(nodes, edges))
    on_file_only = [n for n in nodes if n.kind is NodeKind.PATIENT and n.id not in claimed]
    claimed_nodes = [n for n in nodes if n.id in claimed]
    assert on_file_only and claimed_nodes

    def shape(node):
        return (sorted(node.attrs), sorted(node.attr_sources),
                sorted(p.kind for p in node.sources))

    reference = shape(claimed_nodes[0])
    for node in on_file_only:
        assert shape(node) == reference, node.id
        assert node.unresolved == [], node.id
        for name, value in node.attrs.items():
            pointer = node.attr_sources.get(name)
            if value is None:
                assert name in node.unresolved, (node.id, name)
                continue
            assert pointer is not None, f"{node.id}.{name} is a value nobody can check"
            assert pointer.kind in {"table_cell", "derived"}, (node.id, name, pointer.kind)
            assert "patient_registration" in (pointer.source_id or ""), (node.id, name)


def test_a_visit_claim_is_not_counted_as_a_document_naming_the_patient():
    """`FOR_PATIENT` runs visit → patient as well as document → patient (schema.py). Guarding only
    the DESTINATION let a visit's claim count as "a document names this person" — which inflates
    "seen here" and, downstream, picks the LESS cautious identity caveat."""
    import datetime as dt

    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphEdge, GraphNode

    when = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d",
                        char_start=0, char_end=1)
    nodes = [
        GraphNode(id="patient:p", kind=NodeKind.PATIENT, label="P", sources=[ptr],
                  valid_from=when, system_from=when),
        GraphNode(id="visit:v", kind=NodeKind.VISIT, label="V", sources=[ptr],
                  valid_from=when, system_from=when),
    ]
    visit_claim = GraphEdge(id="e", src_id="visit:v", dst_id="patient:p",
                            kind=EdgeKind.FOR_PATIENT, sources=[ptr],
                            valid_from=when, system_from=when)
    assert claimed_patient_node_ids(nodes, [visit_claim]) == []
    assert patient_census(nodes, [visit_claim])["claimed_by_a_document"] == 0

    document = GraphNode(id="document:d", kind=NodeKind.DOCUMENT, label="d", sources=[ptr],
                         valid_from=when, system_from=when)
    doc_claim = GraphEdge(id="e2", src_id="document:d", dst_id="patient:p",
                          kind=EdgeKind.FOR_PATIENT, sources=[ptr],
                          valid_from=when, system_from=when)
    assert claimed_patient_node_ids([*nodes, document],
                                    [visit_claim, doc_claim]) == ["patient:p"]


def test_every_registry_row_is_on_file():
    from databridge_intake.registry import registry

    nodes, _, _ = build_seed()
    seeded = {n.id for n in nodes if n.kind is NodeKind.PATIENT}
    expected = {patient_node_id(r.patient_id) for r in registry()}
    assert seeded == expected
    assert len(expected) > 200, "the whole registry, not a sample"


def test_the_census_numbers_agree_with_each_other():
    """Three numbers that must add up, and one inequality that is the whole point of reporting
    them apart: the clinic has seen far fewer people than it holds on file."""
    nodes, edges, _ = build_seed()
    census = patient_census(nodes, edges)
    assert census["on_file"] == sum(1 for n in nodes if n.kind is NodeKind.PATIENT)
    assert census["claimed_by_a_document"] + census["on_file_only"] == census["on_file"]
    assert census["claimed_by_a_document"] < census["on_file"]
    assert census["claimed_by_a_document"] == len(census["claimed_node_ids"]) > 0
    # every claimed id is a patient node that really does carry an incoming claim
    by_id = {n.id: n for n in nodes}
    claimed_edges = {e.dst_id for e in edges if e.kind is EdgeKind.FOR_PATIENT}
    for nid in census["claimed_node_ids"]:
        assert by_id[nid].kind is NodeKind.PATIENT
        assert nid in claimed_edges


def test_an_unknown_registry_option_is_refused_rather_than_falling_through():
    """A typo that silently seeded 11 patients instead of 300 would look exactly like the feature
    working — which is why the check is at the top of `_build`, before any work."""
    with pytest.raises(ValueError) as excinfo:
        build_seed(registry_patients="everyone")
    assert "everyone" in str(excinfo.value)
    for option in (REGISTRY_ALL, REGISTRY_REFERENCED):
        assert option in str(excinfo.value)


def test_the_referenced_option_still_seeds_only_the_patients_a_document_names():
    nodes, edges, skipped = build_seed(registry_patients=REGISTRY_REFERENCED)
    patients = {n.id for n in nodes if n.kind is NodeKind.PATIENT}
    assert patients == set(claimed_patient_node_ids(nodes, edges))
    assert 0 < len(patients) < 50
    assert skipped["registry_patients_not_referenced"] > 200


def test_the_dropped_identity_claims_are_reported_not_silently_missing():
    """A seed that is quietly smaller than the corpus is indistinguishable from one losing rows to
    a bug. `load_clinic_seed` already reports skipped registry rows; dropped claims follow it."""
    from databridge_graph.clinic_seed import _build

    target, intakes, resolutions, table, registry_by_id, texts = _blanked_subject_corpus()
    expected = len(resolutions[target].candidates)

    _, _, skipped = _build(intakes, resolutions, table, registry_by_id, texts)

    assert skipped["for_patient_claims_dropped_no_subject_span"] == expected
    assert skipped["documents_with_no_subject_span"] == [target]


def test_the_unmade_claim_is_recorded_on_the_document_as_unresolved():
    """Dropping the edge is only half of it. The question "who is this letter about?" was asked and
    not answered, and `unresolved` is where an unanswered question lives (CLAUDE.md constraint 2) —
    an attribute that simply vanished would read as "not applicable"."""
    from databridge_graph.clinic_seed import _build

    target, intakes, resolutions, table, registry_by_id, texts = _blanked_subject_corpus()
    nodes, _, _ = _build(intakes, resolutions, table, registry_by_id, texts)

    document = {n.id: n for n in nodes}[document_node_id(target)]
    assert document.attrs["claimed_subject_field"] is None
    assert "claimed_subject_field" in document.unresolved
    assert "claimed_subject_field" not in document.attr_sources, (
        "a pointer beside an absent value is a citation for nothing")


def test_a_document_that_does_name_a_subject_records_which_field_named_it(nodes):
    """The positive control for the above: every real chit names a subject, so every document node
    records WHICH printed field it was read from — the field name, never the value, because the
    value is a patient's name or NRIC and the graph has no business holding either as an attr."""
    from databridge_graph.clinic_seed import SUBJECT_FIELDS

    documents = [n for n in nodes if n.kind is NodeKind.DOCUMENT]
    assert documents
    named = [n for n in documents if n.attrs["claimed_subject_field"] is not None]
    assert named, "no document in the corpus recorded a subject field"
    for document in named:
        assert document.attrs["claimed_subject_field"] in SUBJECT_FIELDS
        assert document.attr_sources["claimed_subject_field"].kind == "doc_span"


def test_no_for_patient_edge_in_the_seed_is_evidenced_by_a_placeholder_span(edges, by_id):
    """Asserted over the WHOLE seed, not one document, so a chit nobody has written yet cannot
    reintroduce the fallback quietly.

    Two ways the placeholder gets in, and both are checked: as the edge's own evidence, and inside
    a `derived` attribute pointer's `derived_from` (which is where `claim_evidence` put it).
    """
    claims = [e for e in edges if e.kind is EdgeKind.FOR_PATIENT]
    assert claims, "no FOR_PATIENT edge was exercised"
    offenders: list[str] = []
    for edge in claims:
        document = by_id[edge.src_id]
        whole_document = list(document.sources)      # `_doc_ptr`, the self-citation
        for pointer in [*edge.sources,
                        *(p for ptr in edge.attr_sources.values()
                          for p in (ptr.derived_from or []))]:
            where = f"{edge.src_id} --for_patient--> {edge.dst_id}"
            if pointer.kind != "doc_span":
                offenders.append(f"{where}: cited a {pointer.kind}, not a span on the document")
                continue
            if pointer.char_start == 0 and (pointer.char_end or 0) <= 1:
                offenders.append(
                    f"{where}: evidenced by chars {pointer.char_start}-{pointer.char_end} — "
                    f"one character of letterhead")
            if (pointer.char_end or 0) <= (pointer.char_start or 0):
                offenders.append(f"{where}: empty span {pointer.char_start}-{pointer.char_end}")
            if pointer in whole_document:
                offenders.append(
                    f"{where}: evidenced by the document's own self-citation, which localises "
                    f"nothing and names no subject")
    assert offenders == [], "\n".join(offenders)


def test_every_for_patient_edge_cites_the_span_its_document_named_a_subject_from(edges, by_id):
    """The positive half: the evidence is not merely *some* span, it is the subject span recorded
    on the document node — the same pointer, so the two cannot drift apart."""
    for edge in (e for e in edges if e.kind is EdgeKind.FOR_PATIENT):
        document = by_id[edge.src_id]
        subject_pointer = document.attr_sources.get("claimed_subject_field")
        assert subject_pointer is not None, f"{document.id} claims a patient but named no subject"
        # `sources` is a tuple since 2026-08-16 (immutable, so a validated edge's
        # grounding cannot be emptied in place). Compare against a tuple.
        assert edge.sources == (subject_pointer,), edge.id
