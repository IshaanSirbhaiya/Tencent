"""The **graph-growth protocol** — how a closed vocabulary is allowed to widen, and who decides.

`packs.py` closes the vocabulary: a kind no enabled pack declares is skipped, never coined. That
is the right default and it is not the whole story. An adapter agent working a real estate meets
things the library has no kind for, and it has exactly two honest options: drop them, or **ask**.
This module is the asking.

The shape, in one line:

    an adapter agent PROPOSES a kind → this module DECIDES, deterministically → the decision is
    recorded with its reasoning → inside a declared envelope it is auto-approved, and otherwise a
    human is asked.

## The three properties that make this a control rather than a formality

**1. The decision is computed, never recalled.** `evaluate_proposal` is a pure function of the
proposal, the enabled vocabulary and a declared policy envelope. There is no model call in it, no
learned threshold, no table of "proposals we said yes to before". Every escalation carries the
comparison that produced it — the two names and the score, the budget and the count, the endpoint
and the subject list — so a reviewer can check the arithmetic rather than trust the verdict. A
system that cannot explain why it widened its own schema has not widened it under control.

**2. Some escalations cannot be switched off, and that is structural rather than configured.**
An organisation may set `auto_approve=True` and stop being asked about ordinary additions. It may
not, by any setting, stop being asked about a kind that would record a **determination**, or one
that touches a **subject** — the entity whose identity the system may never auto-confirm
(CLAUDE.md §Hard constraints 1, spine D4b) — nor about a proposal that is **ungrounded or backed
by untrusted evidence** (CLAUDE.md §Hard constraints 2, spine D3: untrusted content may never
influence control flow, and widening the schema is control flow). Those triggers are members of
`STRUCTURAL_ESCALATIONS`, a module constant that no field of `GrowthPolicy` can reach, and the
waiver in `evaluate_proposal` is computed as *the triggers that are not structural*. There is no
branch in which `auto_approve` is consulted about one, because the set it can act on is built by
subtracting them first. A flag that can be flipped to disable the identity gate — or the
provenance gate — is how a governed system becomes ungoverned, so the flag does not exist rather
than being defaulted off. (Review 2026-08-16, `R-012`, Critical: the evidence triggers were
defined as waivable and a `require_trusted_evidence` policy field could disable the trust check
outright, contradicting `GrowthProposal`'s own promise that an ungrounded proposal "is never
auto-approved" and this module's own citation of spine D3. Both are now structural and the field
that could turn the trust check off has been removed — a "structurally unwaivable" trigger with a
toggle beside it was never structural.)

**3. Approval is a decision, not a write.** An approved proposal does not mutate a pack. It
produces a YAML fragment (`ontology_fragment`) for a human or a UI to add to `ontology.yaml`,
where it is reviewed, versioned and grounded like every other pack artefact. The vocabulary stays
something a person can read end to end. Approval says "this may be added"; adding it is still an
edit to a file under review.

## What is deliberately NOT here

*No natural-language similarity over descriptions.* Near-duplicate detection compares **kind
names** and nothing else. Comparing prose would make the threshold a matter of writing style, and a
duplicate-detection rule that a well-written description can slip past is worse than none.

The measure is `kind_similarity`, and it is deliberately NOT `merge.token_set_ratio` alone. That
function returns exactly `0.0` when two token sets are disjoint, which is correct for entity
resolution — "Coral Harbour" and "Vintners Foundry" are not the same company — and wrong here,
because the commonest duplicate kind name in practice is a plural or a suffixed variant of an
existing one, and a plural shares no token with its singular. So the two measures are combined and
the
higher wins: the token ratio catches reordered multi-word names, and a character ratio over the raw
names catches the plural. A false positive costs one human glance at a report that shows both names
and the score; a false negative costs a permanently split vocabulary.

*No "approve with modification".* A proposal is a fixed artefact with an id derived from its
content; changing it produces a different proposal with a different id. Otherwise the record of
what was approved and the thing that was added drift apart, and the ledger stops being evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, replace
from difflib import SequenceMatcher
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable, Iterator, Mapping, Optional, Sequence

from databridge_core import SourcePointer, Trust, trust_lub

from .merge import token_set_ratio
from .meta import (
    ATTESTED_META_KINDS,
    CLAIM_META_KINDS,
    EdgeMetaKind,
    NodeMetaKind,
)
from .packs import ANY_KIND, Vocabulary, active_vocabulary

# A kind name becomes the node-id prefix, so it must be unambiguous — the same rule `packs.py`
# applies at load time, asserted here so a proposal that could never load is refused at proposal
# time rather than at the moment someone tries to use it.
_KIND_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: Default near-duplicate threshold. Chosen BELOW the entity resolver's 0.92: merging two records
#: wrongly is recoverable, whereas a near-duplicate kind is permanent — every row written under
#: either name afterwards has to be reconciled by hand — so the schema question is asked earlier
#: than the record question. It is a policy field, not a constant, so a deployment can raise it.
NEAR_DUPLICATE_THRESHOLD = 0.72

#: How many nearest existing kinds a similarity report carries. Enough for a reviewer to see the
#: neighbourhood, few enough to render.
SIMILARITY_REPORT_SIZE = 5


class ProposalAxis(str, Enum):
    """Which half of the vocabulary a proposal would widen."""

    NODE = "node"
    EDGE = "edge"


class Outcome(str, Enum):
    """What `evaluate_proposal` decided.

    `REFUSED` is not a harsher `ESCALATED`. It is the answer when there is nothing for a human to
    decide: the proposal could not load as a pack declaration at all, or the library already holds
    exactly this kind. Sending either to a person is asking them to rule on a typo.
    """

    APPROVED = "approved"
    ESCALATED = "escalated"
    REFUSED = "refused"


class Trigger(str, Enum):
    """Why a proposal did not sail through. Each one names a comparison a reviewer can redo."""

    # --- structural: never auto-approvable, whatever the policy says ---
    BINDS_TO_ATTESTED_META_KIND = "binds_to_attested_meta_kind"
    BINDS_TO_CLAIM_META_KIND = "binds_to_claim_meta_kind"
    TOUCHES_SUBJECT_KIND = "touches_subject_kind"
    PROPOSES_A_SUBJECT = "proposes_a_subject"
    EVIDENCE_UNGROUNDED = "evidence_ungrounded"
    EVIDENCE_UNTRUSTED = "evidence_untrusted"
    # --- waivable inside a declared envelope ---
    NEAR_DUPLICATE = "near_duplicate"
    BUDGET_EXCEEDED = "budget_exceeded"
    PROPOSER_NOT_DECLARED = "proposer_not_declared"
    ENDPOINT_NOT_DECLARED = "endpoint_not_declared"


#: The triggers `auto_approve` cannot reach.
#:
#: Structural, not configured: `evaluate_proposal` computes the waivable set by SUBTRACTING this
#: constant, so there is no code path on which `auto_approve` is consulted about one of them. No
#: field of `GrowthPolicy` names a trigger, and `test_the_structural_exception_is_not_configurable`
#: asserts that over every field of the policy rather than over the two we happened to think of.
#:
#: Why these six, in two families.
#:
#: **Identity (CLAUDE.md §Hard constraints 1, spine D4b).** The first two would let a kind be
#: coined that records, or claims, WHICH SUBJECT an artefact concerns — the determination this
#: system exists to refuse to automate. The third would let an edge kind reach a subject without
#: anyone noticing which subject. The fourth would let a pack acquire a new protected entity, or
#: (far worse in practice) let a near-identical unprotected twin of an existing subject be created
#: beside it, at which point `never_fuzzy_merge` protects the old kind and nothing protects the
#: new one.
#:
#: **Provenance (CLAUDE.md §Hard constraints 2, spine D3).** `EVIDENCE_UNGROUNDED` and
#: `EVIDENCE_UNTRUSTED` joined this set in review R-012 (2026-08-16, Critical): both were
#: previously waivable, and `EVIDENCE_UNTRUSTED` could additionally be switched off outright by
#: `GrowthPolicy.require_trusted_evidence=False` — a toggle beside a trigger this module's own
#: docstrings called structural. `GrowthProposal` has always said an ungrounded proposal "is never
#: auto-approved"; `require_trusted_evidence`'s own docstring has always said untrusted evidence
#: "may never influence control flow (spine D3) — and widening the schema is control flow". Both
#: were absolute claims sitting beside code that could be configured to contradict them. An
#: ungrounded or untrusted proposal widening the schema is untrusted content deciding control
#: flow, which D3 forbids categorically — not "unless a deployment prefers otherwise" — so there
#: is no `require_trusted_evidence` field any more: the trust check is unconditional, exactly as
#: the identity checks above always were.
STRUCTURAL_ESCALATIONS = frozenset({
    Trigger.BINDS_TO_ATTESTED_META_KIND,
    Trigger.BINDS_TO_CLAIM_META_KIND,
    Trigger.TOUCHES_SUBJECT_KIND,
    Trigger.PROPOSES_A_SUBJECT,
    Trigger.EVIDENCE_UNGROUNDED,
    Trigger.EVIDENCE_UNTRUSTED,
})


class GrowthError(ValueError):
    """A proposal is malformed in a way that no decision could repair."""


# --------------------------------------------------------------------------------------
# The proposal
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TriggerFinding:
    """One reason, with the comparison that produced it.

    `detail` is written to be read by the human being escalated to. It names the values that were
    compared, not just the rule — "'household' scores 0.78 against declared 'house_hold'" rather
    than "near duplicate" — because the reviewer's job is to judge the comparison.
    """

    code: Trigger
    detail: str

    @property
    def structural(self) -> bool:
        """Whether `auto_approve` is powerless against this one."""
        return self.code in STRUCTURAL_ESCALATIONS

    def as_record(self) -> dict:
        return {"code": self.code.value, "detail": self.detail, "structural": self.structural}


@dataclass(frozen=True)
class SimilarityReport:
    """How close the proposed name is to what the library already holds.

    Always attached to a decision, including an approval: "we checked, and the nearest existing
    kind was this far away" is the part a reviewer needs in order to trust an auto-approval, and
    it is exactly the part a bare verdict throws away.
    """

    axis: ProposalAxis
    threshold: float
    nearest: tuple[tuple[str, float], ...] = ()

    @property
    def near_duplicate_of(self) -> Optional[str]:
        """The closest existing kind at or above the threshold, or ``None``."""
        for name, score in self.nearest:
            if score >= self.threshold:
                return name
        return None

    @property
    def top_score(self) -> float:
        return self.nearest[0][1] if self.nearest else 0.0

    def as_record(self) -> dict:
        return {"axis": self.axis.value, "threshold": self.threshold,
                "near_duplicate_of": self.near_duplicate_of,
                "nearest": [{"kind": n, "score": round(s, 4)} for n, s in self.nearest]}


@dataclass(frozen=True)
class GrowthProposal:
    """An adapter agent's request that the vocabulary widen by one kind.

    Frozen, and its `proposal_id` is derived from its content (`_content`), so "what was
    approved" and "what was added" cannot drift apart: an edited proposal is a different proposal.

    `evidence` is what makes this a proposal rather than an opinion. It is the pointers into the
    data that motivated the kind — the rows or spans the agent could not express — and a proposal
    carrying none is not refused (a human may still want to see it) but it is never auto-approved,
    because an ungrounded schema change is a schema change nobody can check.
    """

    axis: ProposalAxis
    name: str
    binds_to: str
    """The meta-kind this kind would bind to — a `NodeMetaKind` or `EdgeMetaKind` value, matching
    the axis. This is the whole reason the meta-model is closed and the packs are not: a new kind
    is new vocabulary, never new engine behaviour."""

    description: str
    pack: str
    proposed_by: str
    """The adapter (or agent) that raised it. Recorded on the decision, so "which connector keeps
    asking for kinds?" is answerable — a connector that proposes constantly is usually pointed at
    a source the ontology genuinely does not cover, and that is worth knowing."""

    evidence: tuple[SourcePointer, ...] = ()
    src: tuple[str, ...] = ()
    dst: tuple[str, ...] = ()
    proposes_subject: bool = False
    """Whether the proposal asks for the new node kind to be a **subject** (D4b). Always escalates;
    see `STRUCTURAL_ESCALATIONS`."""

    motivation: str = ""
    """Free text from the proposer: what it met that it could not express. Displayed, never parsed
    and never used in the decision — an untrusted string may not influence control flow (D3)."""

    proposed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not _KIND_NAME.match(str(self.name or "")):
            raise GrowthError(
                f"proposed {self.axis.value} kind {self.name!r} is not a lowercase snake_case "
                f"identifier. Kind names become the node-id prefix, so a name a pack could never "
                f"load is refused where it is written rather than where it is used")
        if not str(self.pack or "").strip():
            raise GrowthError("a proposal must name the pack it would widen")
        if not str(self.proposed_by or "").strip():
            raise GrowthError(
                "a proposal must name its proposer: an anonymous schema change is one nobody can "
                "follow up, and 'which adapter keeps asking?' is the question the record exists "
                "to answer")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "src", tuple(self.src))
        object.__setattr__(self, "dst", tuple(self.dst))
        if self.axis is ProposalAxis.NODE and (self.src or self.dst):
            raise GrowthError("a node-kind proposal has no src/dst endpoints")
        # Review R-012 (2026-08-16), Critical, finding 1: `src`/`dst` are untrusted adapter input
        # that `ontology_fragment` later interpolates verbatim into a YAML list (`src: [...]`,
        # `dst: [...]`). An endpoint carrying `, name: illicit_kind` or a bracket/newline is YAML
        # CONTROL CHARACTERS reaching a file a human is expected to read as a flat, harmless list —
        # untrusted content becoming actionable is exactly what spine D3 forbids. Endpoints are
        # supposed to be kind names anyway (an existing declared kind, or another proposal's kind
        # in the same batch), so the SAME identifier grammar `name` already enforces is enforced
        # here too, at construction — refused where it is written, not where it is rendered.
        bad = sorted(e for e in (*self.src, *self.dst)
                    if e != ANY_KIND and not _KIND_NAME.match(str(e)))
        if bad:
            raise GrowthError(
                f"endpoint(s) {bad} are not lowercase snake_case kind identifiers (or the {ANY_KIND!r} "
                f"wildcard). Endpoints become YAML list entries in `ontology_fragment`; a value that "
                f"is not a plain identifier could break out of that list and inject an unreviewed "
                f"declaration into the fragment a human is trusting as flat text")
        if self.axis is ProposalAxis.EDGE and self.proposes_subject:
            raise GrowthError(
                "only a node kind can be a subject — a subject is an entity whose identity is "
                "attested, and a relationship has no identity to attest to")

    # --- identity ---
    def _content(self) -> dict:
        """Everything that makes this proposal THIS proposal. Deliberately excludes `proposed_at`
        and `motivation`: the same request raised twice a day apart, or reworded, is the same
        request, and giving it two ids would make the ledger read as two proposals."""
        return {
            "axis": self.axis.value, "name": self.name, "binds_to": self.binds_to,
            "pack": self.pack, "proposed_by": self.proposed_by,
            "description": self.description,
            "src": list(self.src), "dst": list(self.dst),
            "proposes_subject": self.proposes_subject,
            "evidence": [p.model_dump(mode="json", exclude_none=True) for p in self.evidence],
        }

    @property
    def proposal_id(self) -> str:
        digest = hashlib.sha256(
            json.dumps(self._content(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"gp_{digest[:16]}"

    @property
    def endpoints(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.src, *self.dst)))

    def as_record(self) -> dict:
        return {
            "proposal_id": self.proposal_id, "axis": self.axis.value, "name": self.name,
            "binds_to": self.binds_to, "pack": self.pack, "proposed_by": self.proposed_by,
            "description": self.description, "motivation": self.motivation,
            "src": list(self.src), "dst": list(self.dst),
            "proposes_subject": self.proposes_subject,
            "evidence_count": len(self.evidence),
            "evidence_trust": (trust_lub(self.evidence).value if self.evidence else None),
            "proposed_at": self.proposed_at.isoformat() if self.proposed_at else None,
        }


# --------------------------------------------------------------------------------------
# The envelope
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GrowthPolicy:
    """The declared envelope inside which a proposal may be auto-approved.

    Every field here widens or narrows what an organisation is *asked about*. **No field here can
    reach a structural escalation** — see `STRUCTURAL_ESCALATIONS` and
    `test_the_structural_exception_is_not_configurable`, which asserts that over the whole field
    list rather than over the fields we thought of.
    """

    auto_approve: bool = False
    """The human gate. `False` (the default) escalates every triggered proposal — an organisation
    that has not thought about this gets the cautious behaviour. `True` waives the WAIVABLE
    triggers only.

    Note what it does not mean: with `auto_approve=True` an untriggered proposal was already
    approved, so the switch changes nothing for the clean case. Its entire effect is on proposals
    that fired a waivable trigger — which is precisely the population an organisation should be
    deciding about deliberately."""

    node_kind_budget: Optional[int] = None
    """Ceiling on the pack's node kinds, counting the proposed one. `None` means no declared
    ceiling, which is NOT the same as unlimited: it means the budget trigger cannot fire, and an
    organisation that wants growth bounded has to say by how much. A default number here would be
    a ceiling nobody chose."""

    edge_kind_budget: Optional[int] = None

    near_duplicate_threshold: float = NEAR_DUPLICATE_THRESHOLD

    declared_proposers: frozenset[str] = frozenset()
    """Adapters permitted to propose. Empty means the check does not run — again, an absent
    declaration is not an open door, it is an absent declaration, and the trigger simply cannot
    fire. A non-empty set makes an undeclared proposer an escalation."""

    # `require_trusted_evidence` was removed in review R-012 (2026-08-16, Critical). It let a
    # deployment switch off the check that untrusted evidence must not decide a schema change —
    # exactly the D3 violation `EVIDENCE_UNTRUSTED` exists to catch, and now that the trigger is
    # structural (`STRUCTURAL_ESCALATIONS`) a field that could disable it outright would still be
    # the toggle "structurally unwaivable" promises does not exist. The trust check now always
    # runs; see `evaluate_proposal`.

    def digest(self) -> str:
        """A stable fingerprint of the envelope that decided, recorded alongside the decision.

        Without it, a decision read back later says what was decided but not what the rules were
        at the time, and "why was this auto-approved in March?" becomes unanswerable the first
        time the envelope changes.
        """
        payload = {
            "auto_approve": self.auto_approve,
            "node_kind_budget": self.node_kind_budget,
            "edge_kind_budget": self.edge_kind_budget,
            "near_duplicate_threshold": self.near_duplicate_threshold,
            "declared_proposers": sorted(self.declared_proposers),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]


# --------------------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GrowthDecision:
    """What was decided, why, and under which envelope. The unit the UI renders."""

    proposal_id: str
    outcome: Outcome
    findings: tuple[TriggerFinding, ...]
    similarity: SimilarityReport
    policy_digest: str
    decided_at: datetime
    reasoning: str
    vocabulary_packs: tuple[str, ...] = ()

    @property
    def structural_findings(self) -> tuple[TriggerFinding, ...]:
        """The findings no setting could have waived. Rendered separately, because "we asked you
        because of a configurable rule" and "we asked you because this touches identity" are
        different messages and the second one must not be lost in a list."""
        return tuple(f for f in self.findings if f.structural)

    @property
    def waived_findings(self) -> tuple[TriggerFinding, ...]:
        """Findings that fired and were waived by `auto_approve`. Empty unless the outcome is
        APPROVED — an approval that waived something must say so, or the record reads as clean."""
        if self.outcome is not Outcome.APPROVED:
            return ()
        return tuple(f for f in self.findings if not f.structural)

    @property
    def escalated(self) -> bool:
        return self.outcome is Outcome.ESCALATED

    def as_record(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "outcome": self.outcome.value,
            "findings": [f.as_record() for f in self.findings],
            "structural": [f.code.value for f in self.structural_findings],
            "waived": [f.code.value for f in self.waived_findings],
            "similarity": self.similarity.as_record(),
            "policy_digest": self.policy_digest,
            "decided_at": self.decided_at.isoformat(),
            "reasoning": self.reasoning,
            "vocabulary_packs": list(self.vocabulary_packs),
        }


# --------------------------------------------------------------------------------------
# The evaluation — deterministic, explainable, no model call
# --------------------------------------------------------------------------------------
def _name_tokens(name: str) -> str:
    """A kind name as words, so `token_set_ratio` compares meaning rather than punctuation."""
    return name.replace("_", " ")


def kind_similarity(a: str, b: str) -> float:
    """How close two KIND NAMES are, in [0, 1]. Deterministic and symmetric.

    Two measures, higher wins, for a reason worth stating rather than tuning around:

    * `merge.token_set_ratio` is the entity resolver's function. It is size-aware — a short name
      swallowed by a longer one scores LOW, so `case` and `case_event` are correctly NOT duplicates
      — and it handles multi-word names whose words are in a different order.
    * It also returns exactly `0.0` for disjoint token sets, which is right for entity resolution
      and wrong for kind names: a plural and its singular share no token at all and are
      obviously the same kind. A character ratio over the raw names catches that, and it is the
      commonest real duplicate.

    Neither alone is enough, so the pair is combined rather than one of them being adjusted — the
    resolver's behaviour is correct for the resolver, and changing it to suit this caller would be
    fixing the wrong function.
    """
    return max(token_set_ratio(_name_tokens(a), _name_tokens(b)),
               SequenceMatcher(None, a, b).ratio())


def similarity_report(axis: ProposalAxis, name: str, existing: Iterable[str], *,
                      threshold: float = NEAR_DUPLICATE_THRESHOLD,
                      size: int = SIMILARITY_REPORT_SIZE) -> SimilarityReport:
    """Score a proposed name against every declared kind on the same axis (`kind_similarity`).

    Ties are broken by name so the report is byte-stable across runs; a decision that reads
    differently on two machines is not evidence.
    """
    scored = sorted(
        ((other, kind_similarity(name, other)) for other in existing),
        key=lambda pair: (-pair[1], pair[0]))
    return SimilarityReport(axis=axis, threshold=threshold, nearest=tuple(scored[:size]))


def _meta_for(axis: ProposalAxis, binds_to: str):
    """The meta-kind a proposal binds to, or ``None`` if the axis and the value disagree."""
    try:
        return NodeMetaKind(binds_to) if axis is ProposalAxis.NODE else EdgeMetaKind(binds_to)
    except ValueError:
        return None


def _declared_kinds(vocab: Vocabulary, axis: ProposalAxis) -> Mapping[str, object]:
    return vocab.node_kinds if axis is ProposalAxis.NODE else vocab.edge_kinds


def _refusal(proposal: GrowthProposal, report: SimilarityReport, policy: GrowthPolicy,
             at: datetime, reason: str, packs: tuple[str, ...]) -> GrowthDecision:
    return GrowthDecision(
        proposal_id=proposal.proposal_id, outcome=Outcome.REFUSED, findings=(),
        similarity=report, policy_digest=policy.digest(), decided_at=at,
        reasoning=reason, vocabulary_packs=packs)


def evaluate_proposal(proposal: GrowthProposal, *,
                      policy: Optional[GrowthPolicy] = None,
                      vocab: Optional[Vocabulary] = None,
                      now: Optional[Callable[[], datetime]] = None) -> GrowthDecision:
    """Decide a proposal. **Deterministic**: same proposal, same vocabulary, same envelope, same
    VERDICT — outcome, findings and reasoning — on any machine, with no model in the loop.

    `decided_at` is the one field this does not cover: it is a wall-clock audit timestamp, not
    part of the verdict, and defaults to `datetime.now(timezone.utc)` when the caller does not
    supply `now`. Nothing derives an id or a hash from it — `proposal_id` and `policy_digest`
    both exclude it by construction — so two calls a second apart still agree on everything a
    reviewer or a replay would check; only the metadata timestamp differs, exactly as it should.
    A caller that needs `decided_at` itself to be reproducible (a test, a replay) passes `now`.

    The order below is the order a reviewer would use, and it is load-bearing:

    1. **Refuse what is not a question.** A name that could not load, a meta-kind that does not
       exist or does not match the axis, a kind the library already holds under the same shape —
       none of these is a judgement call, and escalating them trains a reviewer to click through.
    2. **Collect every trigger.** All of them, not the first: a proposal that is both a
       near-duplicate AND touches a subject must show both, or fixing the first makes the second
       disappear from view.
    3. **Subtract the structural ones.** What remains is what `auto_approve` may waive. This
       subtraction is why the exception is structural — the flag is never consulted about a
       trigger that is not in the waivable set, because that set is built by removing them.
    """
    policy = policy or GrowthPolicy()
    vocab = vocab if vocab is not None else active_vocabulary()
    at = (now or (lambda: datetime.now(timezone.utc)))()
    declared = _declared_kinds(vocab, proposal.axis)
    report = similarity_report(proposal.axis, proposal.name, declared.keys(),
                               threshold=policy.near_duplicate_threshold)
    packs = tuple(vocab.packs)

    # --- 1. refusals: nothing here is a human's decision -------------------------------
    meta = _meta_for(proposal.axis, proposal.binds_to)
    if meta is None:
        valid = [m.value for m in
                 (NodeMetaKind if proposal.axis is ProposalAxis.NODE else EdgeMetaKind)]
        return _refusal(proposal, report, policy, at,
                        f"REFUSED: {proposal.axis.value} kind {proposal.name!r} binds to "
                        f"{proposal.binds_to!r}, which is not a {proposal.axis.value} meta-kind. "
                        f"The meta-model is closed and changes only by ADR; valid values are "
                        f"{valid}. A pack could not load this declaration, so there is nothing "
                        f"for a reviewer to weigh.", packs)

    existing = declared.get(proposal.name)
    if existing is not None:
        if getattr(existing, "binds_to", None) is meta:
            return _refusal(proposal, report, policy, at,
                            f"REFUSED as redundant: {proposal.axis.value} kind "
                            f"{proposal.name!r} is already declared by pack "
                            f"{getattr(existing, 'pack', '?')!r} and already binds to "
                            f"{meta.value!r}. Use it. Escalating an exact duplicate would ask a "
                            f"human to rule on a lookup.", packs)
        return _refusal(proposal, report, policy, at,
                        f"REFUSED as ambiguous: {proposal.name!r} is already declared by pack "
                        f"{getattr(existing, 'pack', '?')!r} bound to "
                        f"{getattr(existing, 'binds_to').value!r}, and this proposal binds it to "
                        f"{meta.value!r}. Node ids are f'{{kind}}:{{slug}}', so one name meaning "
                        f"two shapes makes the id space ambiguous and a time-travel answer wrong. "
                        f"Rename.", packs)

    # --- 2. every trigger, computed ------------------------------------------------------
    findings: list[TriggerFinding] = []

    # (a) the determination gate. An edge bound to an attested meta-kind IS an identity
    #     determination; one bound to a claim meta-kind is the claim that shadows it. Neither may
    #     be coined without a human, at any setting.
    if meta in ATTESTED_META_KINDS:
        findings.append(TriggerFinding(
            Trigger.BINDS_TO_ATTESTED_META_KIND,
            f"binds to {meta.value!r}, a meta-kind that records a DETERMINATION of which subject "
            f"an artefact concerns. Such an edge is writable only behind a named human's "
            f"attestation, so coining one is a decision about the identity guarantee itself. "
            f"Never auto-approved."))
    if meta in CLAIM_META_KINDS:
        findings.append(TriggerFinding(
            Trigger.BINDS_TO_CLAIM_META_KIND,
            f"binds to {meta.value!r}, a meta-kind that records a CLAIM about which subject an "
            f"artefact concerns. Every surface rendering one must carry the caveat that it is a "
            f"claim; adding a new way to make one is an identity decision. Never auto-approved."))

    # (b) subject reach. An endpoint list naming a subject — or the wildcard, which names every
    #     subject at once — puts the new kind next to the protected entity.
    subjects = set(vocab.subject_kinds)
    if proposal.proposes_subject:
        findings.append(TriggerFinding(
            Trigger.PROPOSES_A_SUBJECT,
            f"asks that {proposal.name!r} be declared a SUBJECT (D4b). A subject is the entity "
            f"whose identity the system may never auto-confirm; declaring one commits the "
            f"deployment to presenting candidates and requiring a named human's attestation. "
            f"Never auto-approved."))
    touched = sorted(e for e in proposal.endpoints if e in subjects)
    if touched:
        findings.append(TriggerFinding(
            Trigger.TOUCHES_SUBJECT_KIND,
            f"names subject kind(s) {touched} among its endpoints. Declared subjects in this "
            f"binding: {sorted(subjects)}. Never auto-approved."))
    elif ANY_KIND in proposal.endpoints and subjects:
        findings.append(TriggerFinding(
            Trigger.TOUCHES_SUBJECT_KIND,
            f"uses the {ANY_KIND!r} endpoint wildcard, which reaches every declared kind — "
            f"including the subject kind(s) {sorted(subjects)}. A wildcard that touches a subject "
            f"is a subject-touching edge whose reach nobody wrote down. Never auto-approved."))

    # (c) endpoints that no enabled pack declares. Not a refusal: the proposer may be describing
    #     an estate whose other half is a second proposal in the same batch.
    unknown_ends = sorted(e for e in proposal.endpoints
                          if e != ANY_KIND and e not in vocab.node_kinds)
    if unknown_ends:
        findings.append(TriggerFinding(
            Trigger.ENDPOINT_NOT_DECLARED,
            f"names endpoint kind(s) {unknown_ends} that no enabled pack declares. The edge would "
            f"load with endpoints that resolve to nothing, so either a node-kind proposal is "
            f"missing from this batch or the name is a typo."))

    # (d) near-duplicate. The comparison, with both names and the score.
    dup = report.near_duplicate_of
    if dup is not None:
        score = dict(report.nearest)[dup]
        findings.append(TriggerFinding(
            Trigger.NEAR_DUPLICATE,
            f"{proposal.name!r} scores {score:.2f} against the declared {proposal.axis.value} "
            f"kind {dup!r}, at or above the {policy.near_duplicate_threshold:.2f} threshold. "
            f"Two kinds meaning nearly the same thing split every later query in half, and the "
            f"split is not visible from either side."))

    # (e) budget. The declared ceiling on how far the vocabulary may widen without being asked.
    budget = (policy.node_kind_budget if proposal.axis is ProposalAxis.NODE
              else policy.edge_kind_budget)
    if budget is not None:
        in_pack = sum(1 for d in declared.values() if getattr(d, "pack", None) == proposal.pack)
        if in_pack + 1 > budget:
            findings.append(TriggerFinding(
                Trigger.BUDGET_EXCEEDED,
                f"pack {proposal.pack!r} declares {in_pack} {proposal.axis.value} kind(s); this "
                f"would make {in_pack + 1}, past the declared budget of {budget}. The budget is "
                f"the point at which an organisation wants to look at its own vocabulary rather "
                f"than keep adding to it."))

    # (f) evidence. Ungrounded and untrusted are different failures and are reported separately.
    # Both are STRUCTURAL (`STRUCTURAL_ESCALATIONS`, review R-012): unconditional, with no policy
    # field able to disable either check — an ungrounded or untrusted proposal widening the schema
    # is untrusted content deciding control flow, which spine D3 forbids categorically, not "unless
    # a deployment prefers otherwise".
    if not proposal.evidence:
        findings.append(TriggerFinding(
            Trigger.EVIDENCE_UNGROUNDED,
            "carries no evidence pointers. The pointers into the data that motivated a kind are "
            "what make the addition checkable; without them the reviewer is being asked to agree "
            "with an assertion. Never auto-approved."))
    else:
        lub = trust_lub(proposal.evidence)
        if lub is not Trust.TRUSTED:
            unlabelled = sum(1 for p in proposal.evidence if p.trust is None)
            detail = (f"{unlabelled} of {len(proposal.evidence)} evidence pointer(s) carry no "
                      f"trust label, and an unlabelled pointer joins as UNTRUSTED"
                      if unlabelled else
                      f"at least one of {len(proposal.evidence)} evidence pointer(s) is UNTRUSTED")
            findings.append(TriggerFinding(
                Trigger.EVIDENCE_UNTRUSTED,
                f"{detail}. Untrusted values may be stored, cited and displayed, and may never "
                f"influence control flow (spine D3) — and widening the schema is control flow: an "
                f"attacker who can write into a source could otherwise author a kind."))

    # (g) an undeclared proposer, where the deployment declared any at all.
    if policy.declared_proposers and proposal.proposed_by not in policy.declared_proposers:
        findings.append(TriggerFinding(
            Trigger.PROPOSER_NOT_DECLARED,
            f"proposed by {proposal.proposed_by!r}, which is not among the declared proposers "
            f"{sorted(policy.declared_proposers)}."))

    # --- 3. the waiver, computed by SUBTRACTION -------------------------------------------
    # `auto_approve` acts on `waivable` and on nothing else. The structural findings are removed
    # from consideration BEFORE the flag is read, which is what makes the exception structural
    # rather than a rule someone remembered to write.
    structural = tuple(f for f in findings if f.structural)
    waivable = tuple(f for f in findings if not f.structural)

    if structural:
        outcome = Outcome.ESCALATED
    elif not waivable:
        outcome = Outcome.APPROVED
    elif policy.auto_approve:
        outcome = Outcome.APPROVED
    else:
        outcome = Outcome.ESCALATED

    return GrowthDecision(
        proposal_id=proposal.proposal_id, outcome=outcome, findings=tuple(findings),
        similarity=report, policy_digest=policy.digest(), decided_at=at,
        reasoning=_reasoning(proposal, outcome, structural, waivable, report, policy),
        vocabulary_packs=packs)


def _reasoning(proposal: GrowthProposal, outcome: Outcome,
               structural: Sequence[TriggerFinding], waivable: Sequence[TriggerFinding],
               report: SimilarityReport, policy: GrowthPolicy) -> str:
    """The explanation, assembled from the same values the decision was computed from.

    Written as one paragraph per reason rather than a code list because it is read by whoever is
    being asked to decide, and "escalated: TOUCHES_SUBJECT_KIND" tells them nothing they can act
    on.
    """
    head = (f"{proposal.axis.value} kind {proposal.name!r} → {proposal.binds_to!r} "
            f"(pack {proposal.pack!r}, proposed by {proposal.proposed_by!r})")
    lines = [f"{outcome.value.upper()}: {head}."]

    if outcome is Outcome.APPROVED and not waivable and not structural:
        near = (f"nearest declared kind {report.nearest[0][0]!r} at {report.top_score:.2f}, below "
                f"the {report.threshold:.2f} threshold" if report.nearest else "no kinds to "
                "compare against")
        lines.append(f"No trigger fired: {near}; the meta-kind it binds to carries no identity "
                     f"rule; its endpoints touch no declared subject; its evidence is grounded "
                     f"and trusted.")
        return "\n".join(lines)

    if structural:
        lines.append("Escalated to a human for reason(s) that NO policy setting can waive — "
                     "`auto_approve` is not consulted about these:")
        lines += [f"  · {f.code.value}: {f.detail}" for f in structural]
    if waivable:
        verb = ("waived by the deployment's auto_approve envelope"
                if outcome is Outcome.APPROVED else "escalated to a human")
        lines.append(f"Reason(s) {verb} (auto_approve={policy.auto_approve}, "
                     f"envelope {policy.digest()}):")
        lines += [f"  · {f.code.value}: {f.detail}" for f in waivable]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GrowthEntry:
    """One proposal and its decision, as recorded. The row a UI renders."""

    proposal: GrowthProposal
    decision: GrowthDecision
    sequence: int

    def as_record(self) -> dict:
        return {"sequence": self.sequence,
                "proposal": self.proposal.as_record(),
                "decision": self.decision.as_record()}


class GrowthLedger:
    """Append-only record of every proposal, decision and escalation.

    Append-only in the same sense as the evidence ledger: there is no `remove`, no `update` and no
    `clear`. A proposal that was escalated and then resolved is TWO entries — the escalation and
    whatever followed it — because the record of what the system asked about is exactly the part
    that must survive somebody being satisfied with the answer.

    In-memory here. Persistence belongs with the ledger that stores it, and it is deliberately not
    invented in the module that computes the decision — a decision engine that also owns a
    database is a decision engine nobody can test without one.
    """

    def __init__(self) -> None:
        self._entries: list[GrowthEntry] = []

    def record(self, proposal: GrowthProposal, decision: GrowthDecision) -> GrowthEntry:
        if decision.proposal_id != proposal.proposal_id:
            raise GrowthError(
                f"decision {decision.proposal_id!r} does not belong to proposal "
                f"{proposal.proposal_id!r} — recording a decision against the wrong proposal is "
                f"how a ledger stops being evidence")
        entry = GrowthEntry(proposal=proposal, decision=decision, sequence=len(self._entries))
        self._entries.append(entry)
        return entry

    def evaluate_and_record(self, proposal: GrowthProposal, **kwargs) -> GrowthEntry:
        """The ordinary front door: decide, then record, so an evaluation cannot be forgotten."""
        return self.record(proposal, evaluate_proposal(proposal, **kwargs))

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[GrowthEntry]:
        return iter(tuple(self._entries))

    def entries(self) -> tuple[GrowthEntry, ...]:
        return tuple(self._entries)

    def with_outcome(self, outcome: Outcome) -> tuple[GrowthEntry, ...]:
        return tuple(e for e in self._entries if e.decision.outcome is outcome)

    def escalations(self) -> tuple[GrowthEntry, ...]:
        """What a human is waiting on. The queue the UI shows first."""
        return self.with_outcome(Outcome.ESCALATED)

    def approvals(self) -> tuple[GrowthEntry, ...]:
        return self.with_outcome(Outcome.APPROVED)

    def refusals(self) -> tuple[GrowthEntry, ...]:
        return self.with_outcome(Outcome.REFUSED)

    def to_records(self) -> list[dict]:
        """JSON-ready rows for the UI, oldest first."""
        return [e.as_record() for e in self._entries]

    def summary(self) -> dict:
        """Counts by outcome plus the structural-escalation count, which is the number that
        matters: it is how often the identity guarantee was the reason a human was asked."""
        structural = sum(1 for e in self._entries if e.decision.structural_findings)
        return {"proposals": len(self._entries),
                "approved": len(self.approvals()),
                "escalated": len(self.escalations()),
                "refused": len(self.refusals()),
                "structural_escalations": structural,
                "by_proposer": {p: sum(1 for e in self._entries if e.proposal.proposed_by == p)
                                for p in sorted({e.proposal.proposed_by for e in self._entries})}}


# --------------------------------------------------------------------------------------
# Approval produces an edit for review, never a mutation
# --------------------------------------------------------------------------------------
def ontology_fragment(proposal: GrowthProposal) -> str:
    """The YAML block a human or a UI adds to the pack's ``ontology.yaml``.

    Approval does not widen anything by itself. This is the artefact it produces: text, for a file
    that is reviewed, versioned and diffable like every other pack artefact. The alternative — a
    function that edits the ontology in place — would make the vocabulary something that changes
    without a diff, and the first question anyone asks of a closed vocabulary is "who added that?".
    """
    ind = "    "
    lines = [f"  - name: {proposal.name}", f"{ind}binds_to: {proposal.binds_to}"]
    if proposal.axis is ProposalAxis.EDGE:
        if proposal.src:
            lines.append(f"{ind}src: [{', '.join(proposal.src)}]")
        if proposal.dst:
            lines.append(f"{ind}dst: [{', '.join(proposal.dst)}]")
    desc = " ".join(str(proposal.description or "").split())
    if desc:
        lines.append(f"{ind}description: >")
        line = ""
        for word in desc.split(" "):
            if len(line) + len(word) + 1 > 92:
                lines.append(f"{ind}  {line}")
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            lines.append(f"{ind}  {line}")
    # `proposed_by` is untrusted adapter input rendered into a YAML COMMENT. `name`, `binds_to`,
    # `src` and `dst` are all constrained to safe identifier grammars at construction (`__post_init__`
    # / `_KIND_NAME`), but `proposed_by` legitimately holds free-ish text (an adapter's own name),
    # so it is collapsed to one line here rather than validated away — a literal newline is the only
    # character that could turn this trailing comment into a second, uncommented YAML line (review
    # R-012, Critical, finding 1).
    proposed_by = " ".join(str(proposal.proposed_by or "").split())
    lines.append(f"{ind}# added by the growth protocol: proposal {proposal.proposal_id}, "
                 f"proposed by {proposed_by}")
    return "\n".join(lines) + "\n"


__all__ = [
    "NEAR_DUPLICATE_THRESHOLD", "SIMILARITY_REPORT_SIZE", "STRUCTURAL_ESCALATIONS",
    "GrowthDecision", "GrowthEntry", "GrowthError", "GrowthLedger", "GrowthPolicy",
    "GrowthProposal", "Outcome", "ProposalAxis", "SimilarityReport", "Trigger",
    "TriggerFinding", "evaluate_proposal", "kind_similarity", "ontology_fragment",
    "similarity_report",
]
