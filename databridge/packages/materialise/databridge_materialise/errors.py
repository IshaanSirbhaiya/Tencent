"""Refusals. Every one names the invariant it enforced and why the invariant exists.

Two families, and the split matters:

* **`PlanRefused`** — the compiler would not accept the plan. Nothing was read, nothing left the
  boundary, and the remedy is to change the plan.
* **`MaterialisationRefused`** — the plan compiled and the *execution* produced something the
  receipt could not describe honestly. The remedy is usually a policy or a source question, not a
  plan edit.

Both carry `invariant`, a short stable token, so a caller can branch on which rule fired without
matching on prose. The prose is for the human who has to fix it.
"""
from __future__ import annotations

from typing import Optional


class MaterialisationError(Exception):
    """Base for everything this package refuses."""

    invariant: str = ""

    def __init__(self, message: str, *, invariant: Optional[str] = None) -> None:
        super().__init__(message)
        if invariant is not None:
            self.invariant = invariant


class PlanRefused(MaterialisationError):
    """The plan compiler refused. The dataset was never built."""


class FeatureAxisRefused(PlanRefused):
    invariant = "features_on_system_time"


class LabelAxisRefused(PlanRefused):
    invariant = "labels_on_valid_time"


class MaturityLagMissing(PlanRefused):
    invariant = "label_maturity_lag_declared"


class PolicyNotInHash(PlanRefused):
    invariant = "policy_inside_plan_hash"


class PointerRequirementDisabled(PlanRefused):
    invariant = "pointer_for_every_cell"


class AbstentionNotFeasible(PlanRefused):
    invariant = "abstention_feasibility_tested"


class NoSystemTimeAxis(PlanRefused):
    """A feature was declared over an entity that records no system time.

    A corollary of `features_on_system_time` rather than a separate idea: an axis a source does
    not have cannot be cut on, and the honest answer is that the column is not admissible as a
    feature — not that it may be admitted on the axis that happens to exist.
    """

    invariant = "features_on_system_time"


class ImputationRefused(PlanRefused):
    invariant = "no_imputation"


class DeterminismIncomplete(PlanRefused):
    invariant = "re_executable"


class AirGapUnpinned(PlanRefused):
    """`policy.egress: air_gapped` with sources this build cannot actually replay air-gapped.

    SYSTEM_DESIGN §6.3 rule 6 calls this a compile-time property. Before R-014 Finding 8 the check
    only appended a note and let the plan compile anyway — a runtime hope wearing a compile-time
    sentence. Raised, not noted: an air-gapped claim the compiler only remarks on is not one.
    """

    invariant = "air_gapped_sources_pinned"


class PlanNeedsInput(PlanRefused):
    """The request cannot be expressed as a plan that compiles, so the planner ASKS instead of
    guessing (E2E run 2, N-07). The message is the question: what the requester must name — an
    outcome field, a decision instant, a shape this build produces — and the catalogue's own
    candidates for each. Nothing was read."""

    invariant = "plan_needs_input"

    def __init__(self, message: str, *, questions: tuple[str, ...] = (),
                 invariant: Optional[str] = None) -> None:
        super().__init__(message, invariant=invariant)
        #: the planner's question(s), verbatim — each is answerable by id (E2E run 3, P-10;
        #: `questions.question_items`)
        self.questions = tuple(q for q in questions if str(q).strip())


class PlanDraftInvalid(PlanRefused):
    """A drafted plan names a value outside the plan vocabulary and no closed, recorded
    normalisation maps it onto one (E2E run 2, N-07). The message names the field, the value and
    the values that are accepted."""

    invariant = "plan_draft_in_vocabulary"


class ShapeNotImplemented(PlanRefused):
    """A shape this build does not produce. The message names what is missing, never 'TODO'."""

    invariant = "shape_supported"


class TransformedDisclosureUnrepresentable(PlanRefused):
    """Spine §9 O1b — refused rather than papered over.

    A masked, generalised or otherwise transformed cell is a *partial* disclosure. A Receipt over a
    dataset containing one would have to count it as fully disclosed or fully withheld, and both
    are false.

    Since 2026-08-16 the vocabulary has a word for it — `databridge_core.provenance.Transformed` —
    but the read path does not yet use it: the adapter's `Cell` union is a narrower three-state
    type that fails closed, and Policy still returns a masked column as an ordinary
    `GroundedValue`. So this package cannot tell the two apart where it would have to count them,
    and refuses. Raised at compile time from the policy's own compiled decision table, and again
    at materialisation if a transformed cell reaches the shaper.
    """

    invariant = "o1b_no_fourth_cell_state"


class MaterialisationRefused(MaterialisationError):
    """Execution produced something the receipt could not describe honestly."""


class MissingnessLeaks(MaterialisationRefused):
    invariant = "missingness_leak_check"


class UngroundedCell(MaterialisationRefused):
    invariant = "pointer_for_every_cell"


class ChunkResliceFailed(MaterialisationRefused):
    invariant = "chunk_reslice_check"


class AgentShapedPlanViolated(MaterialisationRefused):
    """The agent pathway produced a dataset that fails an invariant the compiler enforces.

    PHASE_PLAN Phase 6: on the agent path the invariants move from *compiled* to *checked after
    the fact*, and a dataset failing them is refused rather than shipped with a warning.
    """

    invariant = "agent_path_checked_after_the_fact"


class SourcePinViolated(MaterialisationRefused):
    """The stores this run read are not the stores its plan pins (R-021 Finding 3).

    A `plan_hash` identifies exactly one dataset only if the bytes it was computed against are the
    bytes read. A pinned source that is missing, carries a different content or schema hash, or
    changed while the run was in flight, refuses the run rather than emitting different bytes
    under the same identity.
    """

    invariant = "source_pins_enforced"


class SourceCannotReconstructInstant(MaterialisationRefused):
    """A whole-file store cannot answer "what was believed at S" for the instant a plan reads at
    (R-021 Finding 10): it was extracted after S, so it holds a later belief and no history."""

    invariant = "temporal_as_of_contract"


__all__ = [
    "SourcePinViolated", "SourceCannotReconstructInstant",
    "MaterialisationError", "PlanRefused", "FeatureAxisRefused", "LabelAxisRefused",
    "MaturityLagMissing", "PolicyNotInHash", "PointerRequirementDisabled",
    "AbstentionNotFeasible", "NoSystemTimeAxis", "ImputationRefused", "DeterminismIncomplete",
    "AirGapUnpinned", "ShapeNotImplemented", "TransformedDisclosureUnrepresentable",
    "MaterialisationRefused", "MissingnessLeaks", "UngroundedCell", "ChunkResliceFailed",
    "AgentShapedPlanViolated", "PlanNeedsInput", "PlanDraftInvalid",
]
