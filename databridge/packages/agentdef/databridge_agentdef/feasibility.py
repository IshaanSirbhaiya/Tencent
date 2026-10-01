"""The **abstention feasibility test** (AGENT_PLANE §3, spine D9).

An operator writes ``abstain_when: certificate.risk > 0.08`` and means "I want this agent to be
wrong at most 8% of the time". That is a wish, not a budget, and the difference is arithmetic.

Kotte ([arXiv:2606.29054](https://arxiv.org/abs/2606.29054)) proves the constraint: for a base
risk mu and a target risk alpha, **any distribution-free selective predictor must abstain on at
least**

    q_min = (mu - alpha) / (1 - alpha)      when mu > alpha,     and 0 otherwise

of examples. The bound is on the *method*, not on the model - no amount of prompting moves it.
Kotte also reports that alpha = 0.10 was **uncertifiable** for NER-class tasks across six models
and eight datasets: the abstention rate required to certify it exceeded what any usable system
could carry. So a declaration asking for alpha at or below the uncertifiable level *for a task of
that class* is refused, with the citation, rather than accepted because the operator wishes it
were true.

## Why the base risk is declared rather than computed here

mu is a measurement over a golden set with a model in the loop, and there is no model in this
compiler - for the same reason there is none in `growth.py::evaluate_proposal`: a compiler that
calls a model gives a different answer on Tuesday, and a grant that is not reproducible is not a
grant. So the declaration must carry the measurement and say what it was measured on, and the
compiler checks the two things it *can* check without a model:

1. that a measurement exists at all (an untested budget is the failure this module is named for);
2. that it was measured on **this** golden set - the declared digest must equal the digest of the
   file the eval binding points at. A mu measured on some other set is a number with a provenance
   gap, and a provenance gap in a risk budget is exactly the shape of the problem.

## What this module deliberately does not do

It does not *choose* alpha, does not smooth mu, and does not offer a "close enough" band. A
feasibility test whose answer can be argued with is a formality.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional

#: The paper the bound comes from. Recorded on every report so a reader can check the arithmetic
#: against the source rather than against this docstring.
KOTTE_CITATION = "arXiv:2606.29054"

#: Target risk levels found **uncertifiable** for a task class, per Kotte. A declaration naming
#: one of these task classes and asking for alpha at or below the level is refused.
#:
#: Keyed by task class rather than by domain on purpose: the finding is about the shape of the
#: prediction problem (short spans, many decisions per document, no calibration signal per span),
#: not about lending or permitting. A per-domain table here would be exactly the hardcoded
#: per-issuer branching the repo forbids elsewhere.
UNCERTIFIABLE_ALPHA: Mapping[str, float] = MappingProxyType({
    "ner": 0.10,
    "span_extraction": 0.10,
})

#: Task classes this build knows how to reason about. Closed, for the same reason the purpose
#: vocabulary is: an unknown task class silently skipping the uncertifiability table would make
#: the table opt-in, and a safety table nobody opts into is decoration.
KNOWN_TASK_CLASSES: frozenset[str] = frozenset({
    "ner", "span_extraction", "document_qa", "classification", "summarisation",
})


def minimum_abstention(base_risk: float, target_alpha: float) -> float:
    """Kotte's lower bound on the abstention rate, in [0, 1].

    ``0.0`` when the base risk already sits at or below the target: nothing must be abstained on,
    which is the case an operator hopes for and rarely has.
    """
    if not isinstance(base_risk, (int, float)) or isinstance(base_risk, bool):
        raise ValueError(f"base_risk {base_risk!r} must be a number")
    if not isinstance(target_alpha, (int, float)) or isinstance(target_alpha, bool):
        raise ValueError(f"target_alpha {target_alpha!r} must be a number")
    if not math.isfinite(base_risk) or not (0.0 <= base_risk <= 1.0):
        raise ValueError(f"base_risk {base_risk!r} must be a probability in [0, 1]")
    if not math.isfinite(target_alpha) or not (0.0 <= target_alpha < 1.0):
        raise ValueError(
            f"target_alpha {target_alpha!r} must be in [0, 1). alpha = 1 is 'be wrong always', "
            f"which is not a risk target, and the bound divides by (1 - alpha)")
    if base_risk <= target_alpha:
        return 0.0
    return (base_risk - target_alpha) / (1.0 - target_alpha)


@dataclass(frozen=True)
class FeasibilityReport:
    """The arithmetic, kept beside its answer so a reviewer can redo it.

    `certifiable` and `feasible` are separate fields because they fail for different reasons and
    have different fixes. Not certifiable means *no distribution-free method can certify this
    alpha for this class of task* - the fix is a different alpha, or a different framing of the
    task. Not feasible means the bound is computable and this operator declared they could not
    carry it - the fix is a wider `max_abstention_rate`, or a better model, and both are decisions
    a person makes.
    """

    task_class: str
    target_alpha: float
    base_risk: float
    minimum_abstention: float
    declared_max_abstention: float
    certifiable: bool
    feasible: bool
    reasoning: str
    citation: str = KOTTE_CITATION
    measured_on: Optional[str] = None
    golden_set_digest: Optional[str] = None

    def as_record(self) -> dict:
        return {
            "task_class": self.task_class,
            "target_alpha": self.target_alpha,
            "base_risk": self.base_risk,
            "minimum_abstention": round(self.minimum_abstention, 6),
            "declared_max_abstention": self.declared_max_abstention,
            "certifiable": self.certifiable,
            "feasible": self.feasible,
            "citation": self.citation,
            "measured_on": self.measured_on,
            "golden_set_digest": self.golden_set_digest,
            "reasoning": self.reasoning,
        }


def assess(*, task_class: str, target_alpha: float, base_risk: float,
           max_abstention_rate: float, measured_on: Optional[str] = None,
           golden_set_digest: Optional[str] = None) -> FeasibilityReport:
    """Run the test. Pure, deterministic, no model, no network.

    The order is the order a reviewer would use: first ask whether the target is certifiable *at
    all* for this class of task, because if it is not then the rest of the arithmetic is a
    calculation about an impossible thing.
    """
    if task_class not in KNOWN_TASK_CLASSES:
        raise ValueError(
            f"task_class {task_class!r} is not one this build knows: "
            f"{sorted(KNOWN_TASK_CLASSES)}. The uncertifiability table is keyed by task class, so "
            f"an unrecognised class would skip it silently - and a safety table that can be "
            f"skipped by misspelling a key is not a safety table")
    if not math.isfinite(max_abstention_rate) or not (0.0 <= max_abstention_rate <= 1.0):
        raise ValueError(
            f"max_abstention_rate {max_abstention_rate!r} must be a rate in [0, 1]")

    floor = UNCERTIFIABLE_ALPHA.get(task_class)
    q_min = minimum_abstention(base_risk, target_alpha)
    certifiable = floor is None or target_alpha > floor

    if not certifiable:
        return FeasibilityReport(
            task_class=task_class, target_alpha=target_alpha, base_risk=base_risk,
            minimum_abstention=q_min, declared_max_abstention=max_abstention_rate,
            certifiable=False, feasible=False, measured_on=measured_on,
            golden_set_digest=golden_set_digest,
            reasoning=(
                f"NOT CERTIFIABLE: target risk alpha={target_alpha:.4g} for a {task_class!r} task "
                f"is at or below {floor:.4g}, which Kotte ({KOTTE_CITATION}) found uncertifiable "
                f"for this class of task across six models and eight datasets. The obstruction is "
                f"in the METHOD, not in the model: no distribution-free selective predictor "
                f"certifies it, so no amount of prompting, fine-tuning or retrieval reaches it. "
                f"Declare an alpha above {floor:.4g}, or reframe the task so it is not "
                f"{task_class!r}-shaped."))

    feasible = q_min <= max_abstention_rate
    if feasible:
        detail = (f"base risk mu={base_risk:.4g} is already at or below alpha={target_alpha:.4g}, "
                  f"so the bound forces no abstention at all"
                  if base_risk <= target_alpha else
                  f"mu={base_risk:.4g} exceeds alpha={target_alpha:.4g}, so at least "
                  f"(mu-alpha)/(1-alpha) = {q_min:.4g} of examples must be abstained on")
        return FeasibilityReport(
            task_class=task_class, target_alpha=target_alpha, base_risk=base_risk,
            minimum_abstention=q_min, declared_max_abstention=max_abstention_rate,
            certifiable=True, feasible=True, measured_on=measured_on,
            golden_set_digest=golden_set_digest,
            reasoning=(
                f"FEASIBLE: {detail}; the declaration says this deployment can carry up to "
                f"{max_abstention_rate:.4g}. Measured on {measured_on or 'an unnamed run'}. "
                f"Kotte {KOTTE_CITATION}."))

    return FeasibilityReport(
        task_class=task_class, target_alpha=target_alpha, base_risk=base_risk,
        minimum_abstention=q_min, declared_max_abstention=max_abstention_rate,
        certifiable=True, feasible=False, measured_on=measured_on,
        golden_set_digest=golden_set_digest,
        reasoning=(
            f"NOT FEASIBLE: measured base risk mu={base_risk:.4g} against target "
            f"alpha={target_alpha:.4g} forces a minimum abstention rate of "
            f"(mu-alpha)/(1-alpha) = {q_min:.4g}, and the declaration says this deployment can "
            f"carry at most {max_abstention_rate:.4g}. An agent that abstains more often than the "
            f"operator can absorb is an agent nobody will keep switched on, and one that abstains "
            f"less does not hold its risk target. Raise max_abstention_rate to at least "
            f"{q_min:.4g}, raise alpha, or lower mu with a better extractor - the number is not "
            f"negotiable. Kotte {KOTTE_CITATION}."))
