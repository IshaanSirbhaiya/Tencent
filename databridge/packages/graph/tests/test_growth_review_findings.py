"""Review R-012 (2026-08-16) — dispositions in `wiki/40-reviews/R-012-growth-dispositions.md`.

One test per finding this pass closed, each written so it fails if the fix is reverted. Two
Criticals were the mandate:

  * `enforce_attestation`, `attestation.py` — omitting `src_id` used to default to
    `checked.candidate_id`, so `check_binds` compared the attestation's candidate against ITSELF.
    A self-comparison always succeeds; the binding check was silently a no-op for any caller that
    did not supply `src_id`.
  * `evaluate_proposal`, `growth.py` — `EVIDENCE_UNGROUNDED` and `EVIDENCE_UNTRUSTED` were
    waivable by `auto_approve`, and `EVIDENCE_UNTRUSTED` could additionally be switched off
    outright by `GrowthPolicy.require_trusted_evidence=False`. Both contradict `GrowthProposal`'s
    own promise that an ungrounded proposal "is never auto-approved", and this module's own
    citation of spine D3 ("untrusted content may never influence control flow").

Three more findings from the same review were fixed opportunistically because they sit in the
same two files and the fix was small: the YAML-injection hole in `ontology_fragment` (finding 1),
the attestor-name check accepting `"AI"` / `"system"` (finding 3), the attestation's own grounding
not requiring TRUSTED sources (finding 4), the wall never returning what it actually checked
(finding 8, partial — see the dispositions file), and the content address omitting
`attestor_role`/`statement` (finding 9). Findings 6 and 7 are recorded as acknowledged-not-closed
in the dispositions file rather than tested here — see that file for why.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_core import SourcePointer, Trust
from databridge_graph.attestation import Attestation, AttestationError, enforce_attestation
from databridge_graph.growth import (
    STRUCTURAL_ESCALATIONS,
    GrowthError,
    GrowthPolicy,
    GrowthProposal,
    Outcome,
    ProposalAxis,
    Trigger,
    evaluate_proposal,
    ontology_fragment,
)
from databridge_graph.kinds import EdgeKind, NodeKind
from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS
from databridge_graph.packs import active_vocabulary, deployment
from databridge_graph.schema import node_id

PACK = "finance"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pointer(source: str = "s", trust: Trust = Trust.TRUSTED) -> SourcePointer:
    return SourcePointer(source_id=source, kind="table_cell", table="t", column="c", row_id="1",
                         trust=trust)


def _a_subject_kind() -> str:
    return sorted(active_vocabulary().subject_kinds)[0]


def _a_subject_id(slug: str = "one") -> str:
    return node_id(NodeKind(_a_subject_kind()), slug)


def _a_determination_kind() -> EdgeKind:
    vocab = active_vocabulary()
    kinds = [k for m in ATTESTED_META_KINDS for k in vocab.edges_binding_to(m)]
    assert kinds, "this pack declares no determination edge kind"
    return EdgeKind(kinds[0])


def _a_claim_kind() -> EdgeKind:
    vocab = active_vocabulary()
    kinds = [k for m in CLAIM_META_KINDS for k in vocab.edges_binding_to(m)]
    assert kinds, "this pack declares no claim edge kind"
    return EdgeKind(kinds[0])


def _valid_attestation(**overrides) -> Attestation:
    vocab = active_vocabulary()
    kind = _a_subject_kind()
    fields = dict(
        attestor="Ada Verifier",
        mode=vocab.attestation_for(kind),
        attested_at=_now(),
        subject_kind=kind,
        identifier_checked=vocab.identifiers_for(kind)[0],
        grant_type=vocab.identity_grant_for(kind),
        sources=(_pointer("attestation-capture"),),
        candidate_id="doc:1",
        subject_id=_a_subject_id(),
        edge_kind=str(_a_determination_kind()))
    fields.update(overrides)
    return Attestation(**fields)


# ==============================================================================================
# Critical 1 (the task's numbering) — enforce_attestation, attestation.py:462-466
# ==============================================================================================

def test_omitting_src_id_refuses_rather_than_comparing_candidate_to_itself():
    """The finding, verbatim: `src_id=checked.candidate_id` made the binding check a tautology.

    Before the fix, an attestation minted for `doc:1 -> alice` satisfied `check_binds` for ANY
    edge ending at alice regardless of the edge's real `src_id`, because the caller's silence
    about `src_id` was read as "compare candidate_id to candidate_id" — always true. The wall must
    refuse outright when it cannot check the binding, not perform a check that cannot fail.
    """
    with deployment(PACK):
        attestation = _valid_attestation(candidate_id="doc:1", subject_id=_a_subject_id(),
                                         edge_kind=str(_a_determination_kind()))
        with pytest.raises(AttestationError, match="src_id"):
            enforce_attestation(_a_determination_kind(), _a_subject_id(), attestation,
                                edge_id="e:no-src-id")


def test_omitting_src_id_does_not_let_a_wrong_source_edge_through():
    """The concrete attack the finding describes: reuse an attestation minted for `doc:1` on an
    edge whose real source is `doc:2`, by simply not telling the wall what the source is.
    """
    with deployment(PACK):
        subject = _a_subject_id()
        for_doc1 = _valid_attestation(candidate_id="doc:1", subject_id=subject,
                                      edge_kind=str(_a_determination_kind()))
        # Honestly told the real source, the wall correctly refuses the mismatch.
        with pytest.raises(AttestationError, match="not made for this edge"):
            enforce_attestation(_a_determination_kind(), subject, for_doc1,
                                src_id="doc:2", edge_id="e:honest")
        # Omitting src_id must not be a quieter way past the same guard.
        with pytest.raises(AttestationError):
            enforce_attestation(_a_determination_kind(), subject, for_doc1, edge_id="e:omitted")


def test_the_store_and_the_model_both_always_supply_src_id():
    """The production call sites were never actually vulnerable — both `GraphEdge`'s
    after-validator and `GraphStore.add_edge` (via `_screen_determination`) pass `src_id=`
    explicitly. This pins that fact so it stays true: `enforce_attestation` is a public,
    `__all__`-exported function, and the hole mattered because a FUTURE or DIRECT caller could
    omit `src_id` — not because either of today's two callers do.
    """
    import inspect

    from databridge_graph import schema, store as store_mod

    schema_src = inspect.getsource(schema.GraphEdge._attrs_must_be_grounded)
    assert "src_id=self.src_id" in schema_src
    store_src = inspect.getsource(store_mod.GraphStore._screen_determination)
    assert "src_id=edge.src_id" in store_src


def test_enforce_attestation_returns_the_revalidated_attestation():
    """Finding 8 (High), fixed opportunistically alongside finding 2: the wall used to return
    `None` unconditionally, so a caller that persisted `attestation` (the ORIGINAL, un-rebuilt
    object it was handed) rather than the rebuild could persist bytes that were never the bytes
    actually checked. The wall now hands back what it checked, for callers that use it.

    (`GraphStore._screen_determination` does not yet use the return value — see the R-012
    dispositions file for why that half of finding 8 is not closed in this pass: the fix touches
    `store.py`, which is outside this pass's file ownership.)
    """
    with deployment(PACK):
        subject = _a_subject_id()
        attestation = _valid_attestation(attestor="  Ada Verifier  ", candidate_id="doc:1",
                                         subject_id=subject, edge_kind=str(_a_determination_kind()))
        result = enforce_attestation(_a_determination_kind(), subject, attestation,
                                     src_id="doc:1", edge_id="e:returns")
        assert result is not None
        assert result.attestor == "Ada Verifier"          # revalidated: whitespace stripped

        claim_kind = _a_claim_kind()
        assert enforce_attestation(claim_kind, subject, None, src_id="doc:1",
                                   edge_id="e:claim") is None


# ==============================================================================================
# Critical 2 (the task's numbering) — evaluate_proposal, growth.py:617-636 and 653-658
# ==============================================================================================

def _grounded_proposal(**overrides) -> GrowthProposal:
    fields = dict(
        axis=ProposalAxis.NODE, name="household_unit", binds_to="entity",
        description="A unit no existing kind expresses.",
        pack="government", proposed_by="postgres-adapter/permits",
        evidence=(_pointer("registry/seed.sql"),))
    fields.update(overrides)
    return GrowthProposal(**fields)


def test_an_ungrounded_proposal_cannot_be_auto_approved():
    """The finding, first half: `GrowthProposal` has always said an ungrounded proposal "is never
    auto-approved" (its own docstring). Before this fix, `GrowthPolicy(auto_approve=True)` did
    exactly that.
    """
    with deployment("government"):
        decision = evaluate_proposal(_grounded_proposal(evidence=()),
                                     policy=GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.EVIDENCE_UNGROUNDED in {f.code for f in decision.structural_findings}
        assert "NO policy setting can waive" in decision.reasoning


def test_untrusted_evidence_cannot_be_auto_approved():
    """The finding, second half: untrusted evidence backing a proposal used to become approved
    under `auto_approve=True` — the trigger was waivable. Spine D3 forbids untrusted content
    deciding control flow, and widening the schema is control flow.
    """
    with deployment("government"):
        untrusted = _pointer("registry/seed.sql", trust=Trust.UNTRUSTED)
        decision = evaluate_proposal(_grounded_proposal(evidence=(untrusted,)),
                                     policy=GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.EVIDENCE_UNTRUSTED in {f.code for f in decision.structural_findings}


def test_the_trust_check_has_no_toggle_that_disables_it():
    """The finding's sharper half: `require_trusted_evidence=False` used to disable the check
    OUTRIGHT — not merely make it waivable, but stop it from firing at all. "Structurally
    unwaivable" has to mean no parameter can reach it; a config field beside a trigger this
    module calls structural is exactly the shape the review named. The field no longer exists,
    and no combination of the fields that remain can waive the trigger.
    """
    fields = {f.name for f in dataclasses.fields(GrowthPolicy)}
    assert "require_trusted_evidence" not in fields

    with deployment("government"):
        untrusted = _pointer("registry/seed.sql", trust=Trust.UNTRUSTED)
        wide_open = GrowthPolicy(auto_approve=True, node_kind_budget=None, edge_kind_budget=None,
                                 near_duplicate_threshold=1.0, declared_proposers=frozenset())
        decision = evaluate_proposal(_grounded_proposal(evidence=(untrusted,)), policy=wide_open)
        assert decision.outcome is Outcome.ESCALATED
        assert Trigger.EVIDENCE_UNTRUSTED in {f.code for f in decision.structural_findings}


def test_evidence_triggers_are_members_of_structural_escalations():
    assert Trigger.EVIDENCE_UNGROUNDED in STRUCTURAL_ESCALATIONS
    assert Trigger.EVIDENCE_UNTRUSTED in STRUCTURAL_ESCALATIONS


def test_a_near_duplicate_remains_legitimately_waivable():
    """The negative control: not every trigger became structural. `NEAR_DUPLICATE` was never one
    of the identity triggers and is not one of the evidence triggers either — pinning this proves
    the fix narrowed to exactly the two evidence triggers rather than over-correcting into making
    `auto_approve` meaningless.
    """
    with deployment("government"):
        decision = evaluate_proposal(_grounded_proposal(name="case_files"),
                                     policy=GrowthPolicy(auto_approve=True))
        assert decision.outcome is Outcome.APPROVED
        assert Trigger.NEAR_DUPLICATE in {f.code for f in decision.waived_findings}


# ==============================================================================================
# Opportunistic fixes from the same review pass (findings 1, 3, 4, 9) — same two files, small
# diffs, folded in rather than deferred to a later pass.
# ==============================================================================================

def test_ontology_fragment_cannot_be_used_to_inject_a_second_declaration():
    """Finding 1 (Critical): `proposed_by` used to be interpolated into the YAML fragment's
    trailing comment with no escaping. A `proposed_by` containing a literal newline followed by a
    second `- name:` block rendered as a SECOND, unevaluated ontology entry — one that
    `evaluate_proposal` never saw and a human pasting the fragment in would add without
    recognising it as a separate proposal.
    """
    import yaml

    with deployment("government"):
        payload = ("adapter\n  - name: illicit_resolution\n    binds_to: resolved_to\n"
                  "    src: [document]\n    dst: [person]")
        proposal = _grounded_proposal(proposed_by=payload)
        fragment = ontology_fragment(proposal)
        parsed = yaml.safe_load("node_kinds:\n" + fragment)
        entries = parsed["node_kinds"]
        assert len(entries) == 1, fragment
        assert entries[0]["name"] == proposal.name
        assert not any(e.get("name") == "illicit_resolution" for e in entries)


def test_endpoint_names_that_could_break_out_of_the_yaml_list_are_refused_at_construction():
    """The same finding's second injection point: `src`/`dst` were rendered raw into
    `src: [...]` / `dst: [...]`. Endpoints are supposed to be kind identifiers anyway, so they are
    now checked against the same grammar `name` was always checked against — refused where they
    are written, not where they are rendered.
    """
    with pytest.raises(GrowthError):
        GrowthProposal(axis=ProposalAxis.EDGE, name="objected_to", binds_to="references",
                       description="x", pack="government", proposed_by="adapter",
                       src=("case_event, illicit: true",), dst=("application",))


def test_an_attestor_that_is_a_bare_service_word_is_refused():
    """Finding 3 (Critical): the length check alone let `attestor="AI"` or `attestor="system"`
    through — a floor below the module's own promise that an attestor is "not a role, not a
    service account, not 'system'".
    """
    with deployment(PACK):
        for bad in ("AI", "system", "Bot", "automated"):
            with pytest.raises(ValidationError):
                _valid_attestation(attestor=bad)


def test_an_attestations_own_grounding_must_be_trusted():
    """Finding 4 (Critical): `_must_be_grounded` reduced to `bool(sources)` — an explicitly
    UNTRUSTED capture pointer satisfied it just as well as a trusted one. An attestation
    authorises an identity-bearing edge; spine D3 forbids untrusted content deciding that.
    """
    with deployment(PACK):
        with pytest.raises(ValidationError):
            _valid_attestation(sources=(_pointer("terminal-log", trust=Trust.UNTRUSTED),))


def test_attestation_id_distinguishes_records_that_differ_only_in_role_or_statement():
    """Finding 9 (Medium): the content address omitted `attestor_role` and `statement`, so two
    attestations that disagreed only there shared one id, letting audit records be conflated or
    incorrectly treated as replays of each other.
    """
    with deployment(PACK):
        one = _valid_attestation(statement="Matched against NRIC.")
        two = _valid_attestation(statement="Matched against passport — NRIC not presented.")
        assert one.attestation_id != two.attestation_id

        three = _valid_attestation(attestor_role="front desk")
        four = _valid_attestation(attestor_role="duty manager")
        assert three.attestation_id != four.attestation_id
