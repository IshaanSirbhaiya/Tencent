"""query.py — as_of snapshots, neighbors, path, timeline, grounded nl_query, over the clinic seed.

`link_entities` and the `check_claims` anti-fabrication gate are unchanged by ADR-0009 — they are
vocabulary-independent and are what make an answer trustworthy. What changed is the intent
templates, so the tests below exercise them against real clinic questions.
"""
from __future__ import annotations

import datetime as dt
import re

from databridge_graph import (
    EdgeKind,
    GraphStore,
    NodeKind,
    as_of,
    neighbors,
    nl_query,
    node_id,
    path,
    row_to_edge,
    timeline,
)
from databridge_graph.clinic_seed import (
    SEED_SYSTEM_TIME,
    load_clinic_seed,
    org_node_id,
    package_node_id,
    catalogue_test_node_id,
)

_UTC = dt.timezone.utc

MERIDIAN = org_node_id("Meridian Life Assurance Pte Ltd")
WELL2 = package_node_id("WELL2", "BLPHS")


def _seeded():
    s = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_clinic_seed(s)
    return s


# --- bi-temporal as_of ------------------------------------------------------------
def test_as_of_system_time_before_learning_is_empty():
    # audit/replay: "what did the graph believe before we ingested it?" → nothing
    s = _seeded()
    before = as_of(s, dt.datetime(2026, 5, 1, tzinfo=_UTC),
                   dt.datetime(2026, 5, 1, tzinfo=_UTC))   # system_time < learn (2026-08-11)
    assert before.nodes == [] and before.edges == []


def test_as_of_valid_time_before_the_rules_took_effect_is_empty():
    s = _seeded()
    early = as_of(s, dt.datetime(2025, 12, 31, tzinfo=_UTC), SEED_SYSTEM_TIME)
    assert early.nodes == []


def test_as_of_at_the_seed_instant_sees_the_whole_graph():
    s = _seeded()
    now = as_of(s, SEED_SYSTEM_TIME, SEED_SYSTEM_TIME)
    assert len(now.nodes) == s.counts()["nodes"]
    assert any(n.kind == NodeKind.PATIENT for n in now.nodes)
    assert any(n.kind == NodeKind.COVERAGE_RULE for n in now.nodes)


# --- neighbors / path -------------------------------------------------------------
def test_neighbors_one_hop():
    s = _seeded()
    sub = neighbors(s, MERIDIAN, depth=1)
    assert any(n.kind == NodeKind.DOCUMENT for n in sub.nodes)      # ISSUED
    assert all(e.src_id == MERIDIAN or e.dst_id == MERIDIAN for e in sub.edges)


def test_neighbors_edge_kind_filter():
    s = _seeded()
    sub = neighbors(s, WELL2, edge_kind=EdgeKind.INCLUDES_TEST, depth=1)
    assert sub.edges
    assert all(e.kind == EdgeKind.INCLUDES_TEST for e in sub.edges)


def test_path_carries_provenance():
    """An insurer reaches a test it paid for through the document that ordered it — and every
    step of that path is a grounded edge, so the path IS the provenance."""
    s = _seeded()
    gp = path(s, MERIDIAN, catalogue_test_node_id("chest_xray"))
    assert gp.found and gp.steps
    assert gp.pointers()


def test_path_absent_returns_not_found():
    s = _seeded()
    gp = path(s, MERIDIAN, "patient:nobody")
    assert gp.found is False and gp.steps == []


# --- timeline ---------------------------------------------------------------------
def test_timeline_has_node_and_edges():
    s = _seeded()
    evs = timeline(s, MERIDIAN)
    kinds = {e.kind for e in evs}
    assert "node" in kinds and "edge" in kinds


# --- grounded nl_query ------------------------------------------------------------
def test_nl_query_package_lists_its_tests_with_citations():
    s = _seeded()
    a = nl_query(s, "What does the WELL2 package include?")
    assert a.grounded, a.refusal
    assert "WELL2" in a.answer
    assert "includes" in a.answer
    assert a.pointers()
    assert len(a.citations) > 1          # the package AND each test it cites


def test_nl_query_package_answer_carries_its_age_band():
    """The band is a quantity, so it only survives `check_claims` because it is an attribute of a
    cited node — which is exactly the guarantee under test."""
    s = _seeded()
    a = nl_query(s, "What does the WELL2 package include?")
    assert a.grounded and "40-59" in a.answer


def test_nl_query_org_resolves_and_names_its_documents():
    s = _seeded()
    a = nl_query(s, "Who is Meridian Life Assurance?")
    assert a.grounded, a.refusal
    assert a.answer.startswith("Meridian Life Assurance")
    assert "insurer" in a.answer


def test_nl_query_test_resolves_to_the_packages_containing_it():
    s = _seeded()
    a = nl_query(s, "Which package includes the Chest X-Ray?")
    assert a.grounded, a.refusal
    assert "Chest X-Ray" in a.answer


def _patients(store):
    """(a patient some document claims, a patient nobody has ever referred), both uniquely named.

    Both are needed and neither may be hardcoded: the point of the pair is that the SAME caveat
    reaches both, and a literal id would stop being either the moment the registry changed.
    Uniquely named because an ambiguous name is refused before any answer is built — that is the
    other half of this file's identity story and it has its own tests below.
    """
    from databridge_graph import NodeKind, row_to_node
    from databridge_graph.clinic_seed import claimed_patient_node_ids
    from databridge_graph.identity import same_label_node_ids

    nodes = [row_to_node(store, r) for r in store.open_nodes()]
    edges = [row_to_edge(store, r) for r in store.open_edges()]
    claimed = set(claimed_patient_node_ids(nodes, edges))
    unique = [n for n in nodes if n.kind is NodeKind.PATIENT
              and len(same_label_node_ids(store, n.id)) == 1]
    named = next(n for n in unique if n.id in claimed)
    on_file_only = next(n for n in unique if n.id not in claimed)
    return named, on_file_only


def test_a_patient_answer_never_claims_identity_is_verified():
    """The load-bearing one. A document names a patient; the graph must present that as a CLAIM
    and say so in the answer text, because the answer is what staff read
    (CLAUDE.md §Hard constraints 1)."""
    s = _seeded()
    named, _ = _patients(s)
    a = nl_query(s, f"Who is {named.label}?")
    assert a.grounded, a.refusal
    assert "not a verified identity" in a.answer
    assert "in person" in a.answer
    for forbidden in ("verified identity is", "identity confirmed", "is confirmed to be"):
        assert forbidden not in a.answer.lower()


def test_a_patient_no_document_names_still_gets_the_identity_caveat():
    """The regression this test exists for, and the reason the caveat moved out of the loop.

    `clinic_seed` seeds all 300 registry rows; 289 of them have no `FOR_PATIENT` edge. The caveat
    used to be appended INSIDE the loop over those edges, so those 289 answered with a bare name
    and a date of birth and nothing else — a registry row presented as an identification, which is
    constraint 1 broken by omission rather than by assertion. **Registry presence is not
    identification**, so the caveat belongs to the KIND.
    """
    s = _seeded()
    _, on_file_only = _patients(s)
    a = nl_query(s, f"Who is {on_file_only.label}?")
    assert a.grounded, a.refusal
    assert on_file_only.label in a.answer
    assert "not a verified identity" in a.answer
    assert "in person" in a.answer
    # and it says WHY it has nothing more to show, rather than looking like a thin answer
    assert "No document on file names this patient" in a.answer


def test_every_patient_in_the_graph_gets_the_caveat_not_just_the_ones_on_screen():
    """Swept over the whole registry rather than a sample, because the failure mode was a whole
    CLASS of patient — the ones no demo ever opens — losing the caveat silently."""
    from databridge_graph import NodeKind, row_to_node
    from databridge_graph.identity import same_label_node_ids

    s = _seeded()
    checked = 0
    for row in s.open_nodes():
        if row["kind"] != NodeKind.PATIENT.value:
            continue
        if len(same_label_node_ids(s, row["id"])) > 1:
            continue                     # refused outright; covered by the ambiguity tests below
        a = nl_query(s, row["id"])
        assert a.grounded, (row["id"], a.refusal)
        assert "not a verified identity" in a.answer, row["id"]
        assert "in person" in a.answer, row["id"]
        checked += 1
    assert checked > 100, f"only {checked} patients were reachable — the sweep proved little"


# --- a name is not an identifier (CLAUDE.md §Hard constraints 1) ----------------------


def test_a_name_shared_by_several_patients_is_refused_not_answered_about_one_of_them():
    """The registry holds three "Loh Wei Ming", four "Rahman Zhi Hao" and four "Tan Mei Ling".
    `nl_query` used to return whichever of them `link_entities` scored first among equals — the
    machine choosing between people who share a name, silently, with nothing in the answer saying
    a choice had been made."""
    s = _seeded()
    a = nl_query(s, "Who is Loh Wei Ming?")
    assert a.grounded is False
    assert a.answer == ""
    assert len(a.candidates) == 3
    assert "will not choose" in a.refusal
    for candidate in a.candidates:
        assert s.open_node_row(candidate) is not None


def test_the_ambiguity_refusal_is_a_fork_in_the_road_not_a_dead_end():
    """A refusal with no way forward would just be a wall. Asking again with one of the ids the
    refusal handed back resolves cleanly — an id is an identifier, and a caller who has one has
    already made the choice this module will not make for them."""
    s = _seeded()
    refused = nl_query(s, "Who is Loh Wei Ming?")
    assert refused.candidates

    for candidate in refused.candidates:
        a = nl_query(s, candidate)
        assert a.grounded, (candidate, a.refusal)
        assert "Loh Wei Ming" in a.answer
        assert "not a verified identity" in a.answer
    # …and they are genuinely different people, so the refusal was not pedantry
    dobs = {nl_query(s, c).answer for c in refused.candidates}
    assert len(dobs) == len(refused.candidates)


def test_a_visit_claim_is_not_narrated_as_a_document_naming_the_patient():
    """`FOR_PATIENT` also runs visit → patient. Unfiltered, the answer said "<visit> names this
    patient" and then chose the caveat that assumes a document exists. A non-document claim leaves
    the registry-only wording standing, which is the cautious direction to fail in."""
    import datetime as dt

    from databridge_core import SourcePointer
    from databridge_graph import GraphEdge, GraphNode

    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    when = dt.datetime(2026, 1, 1, tzinfo=_UTC)
    ptr = SourcePointer(source_id="d", kind="doc_span", document_id="d",
                        char_start=0, char_end=1)
    store.add_node(GraphNode(id="patient:p", kind=NodeKind.PATIENT, label="Zephyrine Quillon",
                             sources=[ptr], valid_from=when, system_from=when))
    store.add_node(GraphNode(id="visit:v", kind=NodeKind.VISIT, label="Visit scheduled by v",
                             sources=[ptr], valid_from=when, system_from=when))
    store.add_edge(GraphEdge(id="e", src_id="visit:v", dst_id="patient:p",
                             kind=EdgeKind.FOR_PATIENT, sources=[ptr],
                             valid_from=when, system_from=when))

    a = nl_query(store, "Who is Zephyrine Quillon?")
    assert a.grounded, a.refusal
    assert "Visit scheduled by v" not in a.answer, "a visit was narrated as naming the patient"
    assert "No document on file names this patient" in a.answer
    assert "not a verified identity" in a.answer


def test_a_uniquely_named_entity_is_not_refused_by_the_ambiguity_check():
    """The check must cost nothing where a name really does identify one thing, or every answer
    on the page disappears."""
    s = _seeded()
    a = nl_query(s, "Who is Meridian Life Assurance?")
    assert a.grounded, a.refusal
    assert a.candidates == []


def test_nl_query_no_match_refuses():
    s = _seeded()
    a = nl_query(s, "What is the airspeed velocity of an unladen swallow?")
    assert a.grounded is False and a.refusal


def test_every_clinic_answer_is_claim_checked_clean():
    # the anti-fabrication guarantee: every grounded answer's quantities trace to a cited node
    s = _seeded()
    for q in ["What does the WELL2 package include?",
              "What does the PEE226 package include?",
              "Who is Meridian Life Assurance?",
              "Which package includes the Chest X-Ray?"]:
        a = nl_query(s, q)
        assert a.grounded, f"{q!r} should be grounded, got refusal: {a.refusal}"
        assert a.pointers(), f"{q!r} produced no citations"


def test_documents_are_provenance_never_the_answer_entity():
    """A DOCUMENT node is what an answer CITES, not what it is about — otherwise asking about a
    patient returns "chit_01_meridian_referral.txt." and calls it an answer."""
    s = _seeded()
    a = nl_query(s, "chit_01_meridian_referral.txt")
    assert not a.answer.startswith("chit_01")


# --- R-30: every value the answer prints carries the citation for THAT value -------------------
#
# `nl_query` printed `attrs['date_of_birth']`, `attrs['kind']`, `attrs['billing_party']` and their
# siblings into an answer whose `cite()` attached only the node's ENTITY pointers — the same Shape A
# defect already fixed in `node_sheet`, `/evidence`, billing, the MCP payload and `clinic_history`.
# This one feeds `/brain`'s answer box, so a judge who clicks a citation finds it does not support
# the value printed beside it.
#
# The tests below walk the answer's values generically rather than checking three known fields, so
# a new intent template cannot reintroduce the defect without failing here.

# One question per intent template in `nl_query`, so the walk below covers every branch that
# prints an attribute rather than only the branch someone remembered to test.
_EVERY_TEMPLATE = [
    "What does the WELL2 package include?",          # PACKAGE   — name, eligibility
    "What does the PEE226 package include?",         # PACKAGE
    "Who is Meridian Life Assurance?",               # ORG       — kind, billing_party
    "Who is Northstar Life Assurance?",              # ORG
    "Which package includes the Chest X-Ray?",       # TEST      — category
    "What is the Full Blood Count?",                 # TEST
    "Who is Loh Wei Ming?",                          # PATIENT   — date_of_birth
    "What is the Meridian medical referral rule?",   # COVERAGE_RULE — issuer_code, billed_to,
    "What is the Everwell Health Check-up Voucher rule?",             # ...and eligibility
    "Tell me about the Visit scheduled by chit_02_everwell_voucher.txt",   # VISIT — appointment
]


def _grounded_answers():
    store = _seeded()
    answers = [(q, nl_query(store, q)) for q in _EVERY_TEMPLATE]
    grounded = [(q, a) for q, a in answers if a.grounded]
    assert grounded, "no question produced a grounded answer — the walk below would be vacuous"
    return store, grounded


def _uncited_text(answer: str, printed: set) -> str:
    r"""The answer with every ALREADY-CITED value blanked out — what is left to account for.

    A plain substring scan for "is this stored value in the answer?" is useless, and the first
    draft of this test proved it three ways: a `list_price_sgd` of `25` "appears" inside the
    package name *Pre-Employment Examination (ages 25 to 49)*, a `40` inside the band `40-59`, and
    a `refused_count` of `0` inside `chit_01_...`. None of those is a value the answer printed, and
    a test that reports them is noise that gets deleted rather than fixed.

    Masking the cited values first is the right way round: whatever is left is text the answer
    produced from something OTHER than a value it cited, which is exactly the defect being hunted.
    Longest first, so a short value cannot eat a prefix of a longer one.
    """
    for text in sorted(printed, key=len, reverse=True):
        answer = answer.replace(text, " ")
    return answer


def _appears_as_a_value(text: str, where: str) -> bool:
    r"""Whole-value containment. The bounds are ``[\w-]`` rather than ``\b`` so a hyphenated value
    such as ``40-59`` must match entire, not as the ``40`` inside it."""
    return re.search(rf"(?<![\w-]){re.escape(text)}(?![\w-])", where) is not None


def _node(store, entity_id):
    from databridge_graph import row_to_node

    row = store.open_node_row(entity_id)
    assert row is not None, entity_id
    return row_to_node(store, row)


def test_every_value_an_answer_prints_carries_the_pointer_for_that_value():
    """The positive form. For each value the answer printed: it is that node's stored value, the
    pointer attached is that attribute's OWN pointer, and it is not merely one of the node's
    entity citations — which is exactly the substitution the defect made."""
    store, grounded = _grounded_answers()
    checked, narrower_than_the_entity = 0, 0
    for question, answer in grounded:
        for value in answer.values:
            node = _node(store, value.entity_id)
            where = f"{question!r}: {value.entity_id}.{value.attr}"
            assert value.value == str(node.attrs[value.attr]), where
            assert value.pointer == node.attr_sources[value.attr], (
                f"{where} is cited by something other than its own attr_source")
            assert value.value in answer.answer, f"{where} is cited but never printed"
            checked += 1
            narrower_than_the_entity += value.pointer not in node.sources
    assert checked > 0, "no answer printed an attribute value; the assertions above ran on nothing"
    # Non-vacuity. An implementation that "fixed" this by copying `node.sources` onto every value
    # would satisfy everything above, so at least some values must be cited by a pointer the
    # entity citations do NOT contain. It is deliberately not asserted per value: a VISIT node is
    # grounded by the appointment span, which is also its `appointment` attribute's pointer, and
    # that coincidence is honest — one span really does establish both.
    assert narrower_than_the_entity > 0, (
        "every value's pointer is also one of its node's entity citations — the per-value channel "
        "is carrying nothing the entity channel did not already say")


def test_no_attribute_reaches_the_answer_text_without_its_own_citation():
    """The generic form, and the one that fails if a template goes back to interpolating
    `primary.attrs[...]` directly: any stored attribute value that turns up in the answer text must
    appear in `values` with a pointer. Walking the text rather than a field list is deliberate —
    a new template that prints a new attribute is caught without anyone updating this test."""
    store, grounded = _grounded_answers()
    offenders: list[str] = []
    for question, answer in grounded:
        printed = {v.value for v in answer.values}
        residue = _uncited_text(answer.answer, printed)
        for citation in answer.citations:
            node = _node(store, citation.entity_id)
            for attr, raw in node.attrs.items():
                if raw is None:
                    continue
                text = str(raw)
                if text and text not in printed and _appears_as_a_value(text, residue):
                    offenders.append(
                        f"{question!r}: {citation.entity_id}.{attr} = {text!r} is printed in the "
                        f"answer but carries no citation of its own")
    assert offenders == [], "\n".join(offenders)


def test_a_patient_answer_cites_the_registry_row_the_date_of_birth_came_from():
    """The worked example, because this is the one on screen: a registry DOB is a `table_cell` in
    `patient_registration_synthetic.csv`, not something the entity citations ever saw.

    Asked by NAME, of a patient whose name identifies exactly one row. "Loh Wei Ming" is three
    people and is now refused before any answer is assembled, which is a different test.
    """
    store = _seeded()
    named, _ = _patients(store)
    a = nl_query(store, f"Who is {named.label}?")
    assert a.grounded, a.refusal
    dobs = [v for v in a.values if v.attr == "date_of_birth"]
    assert dobs, "the patient answer printed no date of birth to check"
    for value in dobs:
        assert value.value in a.answer
        assert "patient_registration" in (value.pointer.source_id or "")


def test_an_ungroundable_value_is_not_printed_and_is_named_in_unresolved():
    """The negative form. A value the graph cannot cite individually is not a fact the answer may
    state; it belongs in `unresolved`, exactly as the node sheet moves it to its Unresolved
    section. `None` is the honest way an attribute says "asked, not found"."""
    from databridge_core import SourcePointer
    from databridge_graph import GraphEdge, GraphNode

    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    ptr = SourcePointer(source_id="coverage_rules.yaml", kind="table_cell",
                        table="issuers", column="name", row_id="0")
    store.add_node(GraphNode(
        id="org:zephyr", kind=NodeKind.ORG, label="Zephyr Mutual Assurance",
        attrs={"kind": None, "billing_party": None}, sources=[ptr],
        valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))
    store.add_node(GraphNode(
        id="document:d", kind=NodeKind.DOCUMENT, label="d.txt", sources=[ptr],
        valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))
    store.add_edge(GraphEdge(
        id="e", src_id="org:zephyr", dst_id="document:d", kind=EdgeKind.ISSUED, sources=[ptr],
        valid_from=SEED_SYSTEM_TIME, system_from=SEED_SYSTEM_TIME))

    a = nl_query(store, "Who is Zephyr Mutual Assurance?")
    assert a.grounded, a.refusal
    assert "is a None" not in a.answer and "billing the None" not in a.answer
    assert a.values == [], "nothing was citable, so nothing may be reported as cited"
    assert "org:zephyr.kind" in a.unresolved
    assert "org:zephyr.billing_party" in a.unresolved


def test_the_answers_pointers_include_the_per_value_citations():
    """`pointers()` is what a caller renders as "the evidence for this answer". Leaving the
    per-value pointers out of it would fix the data model and leave the surface unchanged."""
    store = _seeded()
    a = nl_query(store, "Who is Meridian Life Assurance?")
    assert a.grounded, a.refusal
    assert a.values
    for value in a.values:
        assert value.pointer in a.pointers()
