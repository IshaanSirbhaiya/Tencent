"""The graph-growth protocol — PHASE_PLAN Phase 4.

A closed vocabulary is only useful if there is an honest way to widen it. An adapter agent working
a real estate meets things the library has no kind for, and its two options are to drop them or to
**ask**. This is the asking, and these tests pin the three properties that make it a control rather
than a formality:

1. the decision is **computed**, not recalled — same proposal, same vocabulary, same envelope, same
   answer, with no model in the loop;
2. some escalations are **structural** — an organisation may switch off the human gate for ordinary
   additions and can never switch it off for a determination kind or anything touching a subject;
3. approval produces an **edit for review**, not a mutation: nothing here widens a pack.

The candidate kinds used below are the ones the government estate's `ONTOLOGY_NOTES.md` names as
deliberately left out of the library "so the protocol has something real to chew on".
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from databridge_core import SourcePointer, Trust
from databridge_graph.growth import (
    NEAR_DUPLICATE_THRESHOLD,
    STRUCTURAL_ESCALATIONS,
    GrowthError,
    GrowthLedger,
    GrowthPolicy,
    GrowthProposal,
    Outcome,
    ProposalAxis,
    Trigger,
    evaluate_proposal,
    ontology_fragment,
    similarity_report,
)
from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS
from databridge_graph.packs import active_vocabulary, deployment

PACK = "government"
PROPOSER = "postgres-adapter/permits"
FIXED_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _clock():
    return FIXED_NOW


def _evidence(n: int = 2, trust: Trust = Trust.TRUSTED) -> tuple[SourcePointer, ...]:
    return tuple(
        SourcePointer(source_id="permits_registry/seed/030.sql", kind="table_cell",
                      table="a_table", column="a_column", row_id=str(i), trust=trust)
        for i in range(n))


def _node_proposal(**overrides) -> GrowthProposal:
    fields = dict(
        axis=ProposalAxis.NODE, name="household", binds_to="entity",
        description="The unit an assessment is made against.",
        pack=PACK, proposed_by=PROPOSER, evidence=_evidence(),
        motivation="A column counts members of a unit that is never enumerated as a row.")
    fields.update(overrides)
    return GrowthProposal(**fields)


def _edge_proposal(**overrides) -> GrowthProposal:
    fields = dict(
        axis=ProposalAxis.EDGE, name="objected_to", binds_to="references",
        description="A challenge raised before a decision, visible only as an event type.",
        pack=PACK, proposed_by=PROPOSER, evidence=_evidence(),
        src=("case_event",), dst=("application",))
    fields.update(overrides)
    return GrowthProposal(**fields)


def _decide(proposal: GrowthProposal, policy: GrowthPolicy | None = None):
    return evaluate_proposal(proposal, policy=policy or GrowthPolicy(), now=_clock)


# --- the happy path: an ordinary addition inside the envelope -------------------------------
def test_a_clean_proposal_inside_the_envelope_is_approved():
    with deployment(PACK):
        decision = _decide(_node_proposal())
        assert decision.outcome is Outcome.APPROVED
        assert decision.findings == ()
        assert "No trigger fired" in decision.reasoning


def test_the_same_proposal_decides_the_same_way_every_time():
    """Deterministic, and the id is content-derived so the ledger cannot drift from what was
    approved. Two separately-constructed but identical proposals are ONE proposal."""
    with deployment(PACK):
        a, b = _node_proposal(), _node_proposal()
        assert a.proposal_id == b.proposal_id
        first, second = _decide(a), _decide(b)
        assert first.as_record() == second.as_record()
        # ...and a different name is a different proposal.
        assert _node_proposal(name="objection_record").proposal_id != a.proposal_id


def test_the_decision_carries_the_comparison_that_produced_it():
    """A reviewer must be able to redo the arithmetic rather than trust the verdict."""
    with deployment(PACK):
        decision = _decide(_node_proposal())
        assert decision.similarity.nearest, "an approval must still show what it compared against"
        assert decision.similarity.threshold == NEAR_DUPLICATE_THRESHOLD
        assert decision.policy_digest
        assert decision.vocabulary_packs == (PACK,)
        assert decision.decided_at == FIXED_NOW


# --- THE structural exception ----------------------------------------------------------------
def test_a_determination_kind_escalates_even_with_auto_approve_on():
    """The headline requirement, and the one that must never be configurable.

    A kind bound to an attested meta-kind records a DETERMINATION of which subject an artefact
    concerns. That is the one decision this system refuses to automate (CLAUDE.md §Hard constraints
    1), so coining a new way to make one is a decision about the identity guarantee itself. An
    organisation that has switched off the human gate for ordinary additions is still asked.
    """
    with deployment(PACK):
        for meta in sorted(m.value for m in ATTESTED_META_KINDS):
            proposal = _edge_proposal(name="identified_as", binds_to=meta,
                                      src=("document",), dst=("case_file",))
            wide_open = GrowthPolicy(auto_approve=True, node_kind_budget=None,
                                     edge_kind_budget=None)
            decision = _decide(proposal, wide_open)
            assert decision.outcome is Outcome.ESCALATED, meta
            codes = {f.code for f in decision.structural_findings}
            assert Trigger.BINDS_TO_ATTESTED_META_KIND in codes
            assert "NO policy setting can waive" in decision.reasoning


def test_a_claim_kind_escalates_even_with_auto_approve_on():
    """The shadow of the same rule. An `about`-shaped kind is a CLAIM about which subject an
    artefact concerns; every surface rendering one must carry the caveat, so adding a new way to
    make one is an identity decision too."""
    with deployment(PACK):
        for meta in sorted(m.value for m in CLAIM_META_KINDS):
            decision = _decide(
                _edge_proposal(name="appears_to_concern", binds_to=meta,
                               src=("document",), dst=("case_file",)),
                GrowthPolicy(auto_approve=True))
            assert decision.outcome is Outcome.ESCALATED
            assert Trigger.BINDS_TO_CLAIM_META_KIND in {f.code for f in decision.findings}


def test_an_edge_touching_a_subject_escalates_even_with_auto_approve_on():
    with deployment(PACK):
        subject = sorted(active_vocabulary().subject_kinds)[0]
        decision = _decide(
            _edge_proposal(name="linked_to_subject", dst=(subject,)),
            GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.ESCALATED
        finding = next(f for f in decision.findings if f.code is Trigger.TOUCHES_SUBJECT_KIND)
        assert subject in finding.detail


def test_a_wildcard_endpoint_counts_as_touching_every_subject():
    """A wildcard reaches every declared kind, so it reaches every subject — and its reach is
    exactly the thing nobody wrote down."""
    with deployment(PACK):
        decision = _decide(_edge_proposal(name="relates_to_anything", dst=("*",)),
                           GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.TOUCHES_SUBJECT_KIND in {f.code for f in decision.findings}


def test_proposing_a_new_subject_escalates_even_with_auto_approve_on():
    with deployment(PACK):
        decision = _decide(_node_proposal(proposes_subject=True), GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.PROPOSES_A_SUBJECT in {f.code for f in decision.structural_findings}


def test_the_structural_exception_is_not_configurable():
    """Asserted over EVERY field of the policy, not the two we happened to think of.

    The claim is that `auto_approve` is computed against a set built by SUBTRACTING the structural
    triggers, so no setting can reach one. A test naming two settings would pass a version of the
    code where a third setting could. This one enumerates the policy's own fields, sets each to its
    most permissive value, and asserts the answer does not move.
    """
    permissive = {
        "auto_approve": True,
        "node_kind_budget": None,
        "edge_kind_budget": None,
        "near_duplicate_threshold": 1.0,
        "declared_proposers": frozenset(),
    }
    fields = {f.name for f in dataclasses.fields(GrowthPolicy)}
    assert fields == set(permissive), (
        "GrowthPolicy grew a field this test does not know about. Add it to `permissive` with its "
        "most permissive value and confirm the structural escalations still hold — a new setting "
        "is exactly how a structural rule quietly becomes a configurable one. (`require_trusted_"
        "evidence` was removed in review R-012, 2026-08-16, Critical: it was exactly such a "
        "setting, letting a deployment disable the check behind `EVIDENCE_UNTRUSTED` outright.)")

    with deployment(PACK):
        subject = sorted(active_vocabulary().subject_kinds)[0]
        structural_cases = [
            _edge_proposal(name="identified_as",
                           binds_to=sorted(m.value for m in ATTESTED_META_KINDS)[0],
                           src=("document",), dst=("case_file",)),
            _edge_proposal(name="appears_to_concern",
                           binds_to=sorted(m.value for m in CLAIM_META_KINDS)[0],
                           src=("document",), dst=("case_file",)),
            _edge_proposal(name="linked_to_subject", dst=(subject,)),
            _node_proposal(proposes_subject=True),
            _node_proposal(name="ungrounded_kind", evidence=()),
            _node_proposal(name="untrusted_kind", evidence=(
                SourcePointer(source_id="correspondence/INDEX.csv", kind="table_cell",
                              table="t", column="c", row_id="1", trust=Trust.UNTRUSTED),)),
        ]
        for proposal in structural_cases:
            decision = _decide(proposal, GrowthPolicy(**permissive))
            assert decision.outcome is Outcome.ESCALATED, proposal.name
            assert decision.structural_findings, proposal.name
            assert all(f.code in STRUCTURAL_ESCALATIONS for f in decision.structural_findings)


def test_no_policy_field_names_a_trigger():
    """A structural rule stops being structural the moment a field can name it. Nothing in the
    envelope may hold a trigger code, under any name."""
    codes = {t.value for t in Trigger}
    for field in dataclasses.fields(GrowthPolicy):
        assert field.name not in codes
        default = field.default
        if isinstance(default, (frozenset, set, tuple, list)):
            assert not (set(default) & codes)


# --- the waivable triggers ---------------------------------------------------------------
def test_a_near_duplicate_escalates_and_names_both_kinds():
    """Two kinds meaning nearly the same thing split every later query in half, and the split is
    invisible from either side."""
    with deployment(PACK):
        decision = _decide(_node_proposal(name="case_files"))
        assert decision.outcome is Outcome.ESCALATED
        finding = next(f for f in decision.findings if f.code is Trigger.NEAR_DUPLICATE)
        assert "case_file" in finding.detail
        assert decision.similarity.near_duplicate_of == "case_file"


def test_a_near_duplicate_can_be_waived_by_the_envelope_and_the_waiver_is_recorded():
    """`auto_approve` waives it — and the approval must still SAY what it waived, or the record
    reads as clean."""
    with deployment(PACK):
        decision = _decide(_node_proposal(name="case_files"), GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.APPROVED
        assert Trigger.NEAR_DUPLICATE in {f.code for f in decision.waived_findings}
        assert "waived by the deployment's auto_approve envelope" in decision.reasoning


def test_a_budget_is_the_point_at_which_growth_is_looked_at():
    with deployment(PACK):
        declared = sum(1 for d in active_vocabulary().node_kinds.values() if d.pack == PACK)
        decision = _decide(_node_proposal(), GrowthPolicy(node_kind_budget=declared))
        assert decision.outcome is Outcome.ESCALATED
        finding = next(f for f in decision.findings if f.code is Trigger.BUDGET_EXCEEDED)
        assert str(declared) in finding.detail
        # One more of headroom and it sails through.
        assert _decide(_node_proposal(),
                       GrowthPolicy(node_kind_budget=declared + 1)).outcome is Outcome.APPROVED


def test_an_absent_budget_is_not_an_unlimited_one_it_is_an_absent_declaration():
    """`None` means the trigger cannot fire, not that growth is unbounded. A default number here
    would be a ceiling nobody chose."""
    with deployment(PACK):
        decision = _decide(_node_proposal(), GrowthPolicy(node_kind_budget=None))
        assert Trigger.BUDGET_EXCEEDED not in {f.code for f in decision.findings}


def test_ungrounded_evidence_escalates():
    """The pointers into the data that motivated a kind are what make the addition checkable."""
    with deployment(PACK):
        decision = _decide(_node_proposal(evidence=()))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.EVIDENCE_UNGROUNDED in {f.code for f in decision.findings}


@pytest.mark.parametrize("trust", [Trust.UNTRUSTED, None])
def test_untrusted_or_unlabelled_evidence_escalates(trust):
    """Widening the schema IS control flow (spine D3). An attacker who can write into a source
    could otherwise author a kind."""
    with deployment(PACK):
        pointer = SourcePointer(source_id="correspondence/INDEX.csv", kind="table_cell",
                                table="t", column="c", row_id="1", trust=trust)
        decision = _decide(_node_proposal(evidence=(pointer,)))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.EVIDENCE_UNTRUSTED in {f.code for f in decision.findings}


def test_an_undeclared_proposer_escalates_only_where_proposers_were_declared():
    with deployment(PACK):
        assert Trigger.PROPOSER_NOT_DECLARED not in {
            f.code for f in _decide(_node_proposal()).findings}
        decision = _decide(_node_proposal(),
                           GrowthPolicy(declared_proposers=frozenset({"another-adapter"})))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.PROPOSER_NOT_DECLARED in {f.code for f in decision.findings}


def test_an_endpoint_no_pack_declares_escalates_rather_than_being_refused():
    """Not a refusal: the other half may be a second proposal in the same batch."""
    with deployment(PACK):
        decision = _decide(_edge_proposal(dst=("a_kind_no_pack_declares",)))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.ENDPOINT_NOT_DECLARED in {f.code for f in decision.findings}


def test_every_trigger_that_fires_is_reported_not_just_the_first():
    """A proposal that is both a near-duplicate AND touches a subject must show both, or fixing the
    first makes the second disappear from view."""
    with deployment(PACK):
        subject = sorted(active_vocabulary().subject_kinds)[0]
        decision = _decide(_edge_proposal(name="about_subjects", dst=(subject,), evidence=()))
        codes = {f.code for f in decision.findings}
        assert {Trigger.NEAR_DUPLICATE, Trigger.TOUCHES_SUBJECT_KIND,
                Trigger.EVIDENCE_UNGROUNDED} <= codes


# --- refusals: things that are not a human's decision ---------------------------------------
def test_a_kind_the_library_already_holds_is_refused_rather_than_escalated():
    """Escalating an exact duplicate asks a human to rule on a lookup."""
    with deployment(PACK):
        decision = _decide(_node_proposal(name="parcel", binds_to="location"))
        assert decision.outcome is Outcome.REFUSED
        assert "already declared" in decision.reasoning
        assert decision.findings == ()


def test_rebinding_one_name_to_a_second_shape_is_refused_as_ambiguous():
    """Node ids are `kind:slug`, so one name meaning two shapes makes the id space ambiguous and a
    time-travel answer wrong."""
    with deployment(PACK):
        decision = _decide(_node_proposal(name="parcel", binds_to="entity"))
        assert decision.outcome is Outcome.REFUSED
        assert "ambiguous" in decision.reasoning


def test_a_meta_kind_that_does_not_exist_is_refused():
    with deployment(PACK):
        decision = _decide(_node_proposal(binds_to="a_meta_kind_nobody_declared"))
        assert decision.outcome is Outcome.REFUSED
        assert "changes only by ADR" in decision.reasoning


def test_an_axis_mismatch_is_refused():
    """A node kind bound to an EDGE meta-kind could never load as a pack declaration."""
    with deployment(PACK):
        assert _decide(_node_proposal(binds_to="references")).outcome is Outcome.REFUSED
        assert _decide(_edge_proposal(binds_to="entity")).outcome is Outcome.REFUSED


@pytest.mark.parametrize("name", ["Household", "house hold", "1household", "house-hold", ""])
def test_a_name_that_could_never_be_a_kind_is_refused_where_it_is_written(name):
    with pytest.raises(GrowthError):
        _node_proposal(name=name)


def test_a_proposal_must_name_its_proposer():
    """An anonymous schema change is one nobody can follow up."""
    with pytest.raises(GrowthError, match="proposer"):
        _node_proposal(proposed_by="  ")


def test_only_a_node_kind_can_ask_to_be_a_subject():
    with pytest.raises(GrowthError, match="no identity to attest to"):
        _edge_proposal(proposes_subject=True)


# --- similarity is over names, deterministically ----------------------------------------------
def test_similarity_is_size_aware_so_a_shorter_name_is_not_swallowed():
    """`case` and `case_event` are genuinely different kinds; a lenient subset match would call
    them duplicates and block the honest one."""
    report = similarity_report(ProposalAxis.NODE, "case", ["case_event", "case_file"])
    assert report.near_duplicate_of is None
    assert report.top_score < NEAR_DUPLICATE_THRESHOLD


def test_the_similarity_report_is_byte_stable():
    """A decision that reads differently on two machines is not evidence."""
    kinds = ["parcel", "person", "payment", "permit", "photo", "review"]
    first = similarity_report(ProposalAxis.NODE, "parcels", kinds).as_record()
    second = similarity_report(ProposalAxis.NODE, "parcels", list(reversed(kinds))).as_record()
    assert first == second


# --- the record ------------------------------------------------------------------------------
def test_the_ledger_records_every_proposal_decision_and_escalation():
    with deployment(PACK):
        ledger = GrowthLedger()
        ledger.evaluate_and_record(_node_proposal(), now=_clock)
        ledger.evaluate_and_record(
            _edge_proposal(name="identified_as",
                           binds_to=sorted(m.value for m in ATTESTED_META_KINDS)[0],
                           src=("document",), dst=("case_file",)),
            policy=GrowthPolicy(auto_approve=True), now=_clock)
        ledger.evaluate_and_record(_node_proposal(name="parcel", binds_to="location"), now=_clock)

        summary = ledger.summary()
        assert summary == {
            "proposals": 3, "approved": 1, "escalated": 1, "refused": 1,
            "structural_escalations": 1, "by_proposer": {PROPOSER: 3}}
        records = ledger.to_records()
        assert [r["sequence"] for r in records] == [0, 1, 2]
        assert all(r["decision"]["reasoning"] for r in records)
        assert records[1]["decision"]["structural"] == [
            Trigger.BINDS_TO_ATTESTED_META_KIND.value]


def test_the_ledger_is_append_only():
    ledger = GrowthLedger()
    for name in ("remove", "pop", "clear", "update", "delete"):
        assert not hasattr(ledger, name), (
            f"GrowthLedger exposes {name!r} — the record of what the system asked about is exactly "
            f"the part that must survive somebody being satisfied with the answer")


def test_a_decision_cannot_be_recorded_against_the_wrong_proposal():
    with deployment(PACK):
        ledger = GrowthLedger()
        proposal = _node_proposal()
        other = _node_proposal(name="objection_record")
        with pytest.raises(GrowthError, match="does not belong"):
            ledger.record(proposal, evaluate_proposal(other, now=_clock))


# --- approval produces an edit for review, never a mutation ----------------------------------
def test_approval_produces_a_yaml_fragment_and_widens_nothing():
    with deployment(PACK):
        before = set(active_vocabulary().node_kinds)
        proposal = _node_proposal()
        assert _decide(proposal).outcome is Outcome.APPROVED
        assert set(active_vocabulary().node_kinds) == before, (
            "an approval must not widen the vocabulary: the ontology is a reviewed, versioned "
            "file, and a kind that appears without a diff is a kind nobody approved")

        fragment = ontology_fragment(proposal)
        assert "- name: household" in fragment
        assert "binds_to: entity" in fragment
        assert proposal.proposal_id in fragment


def test_the_fragment_for_an_edge_carries_its_endpoints():
    with deployment(PACK):
        fragment = ontology_fragment(_edge_proposal())
        assert "src: [case_event]" in fragment
        assert "dst: [application]" in fragment


def test_the_fragment_parses_as_the_pack_schema_expects():
    """A fragment a human pastes in must actually load, or the protocol ends at a suggestion."""
    import yaml

    with deployment(PACK):
        parsed = yaml.safe_load("node_kinds:\n" + ontology_fragment(_node_proposal()))
        entry = parsed["node_kinds"][0]
        assert entry["name"] == "household"
        assert entry["binds_to"] == "entity"
        assert entry["description"].strip()
