"""The abstention feasibility test (Kotte, arXiv:2606.29054; spine D9, AGENT_PLANE §3).

The result being enforced: for a distribution-free selective predictor targeting risk α on a task
whose base risk is μ, **any** method must abstain on at least

    (μ − α) / (1 − α)

of examples when μ > α. Below that fraction the target is not merely hard, it is unattainable — no
choice of model, threshold or calibration set reaches it.

Why this belongs in a plan compiler rather than in a model-training script: a model asked to
propose a risk budget will accept whatever α it is handed, and the number then travels into a
receipt, a data card and eventually a regulator's file as if it had been established. Kotte found
α=0.10 *uncertifiable* for NER-class tasks across six models and eight datasets — so the honest
default is that a budget is refused until it is tested, not accepted until it is challenged.

The test needs a measured μ. A plan that asserts α with no `base_risk` is refused for that reason
alone: an untested budget and a failed test are different facts, and only one of them can be fixed
by lowering α.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Feasibility:
    """The answer, with the numbers that produced it."""

    alpha: float
    base_risk: float
    min_abstention_rate: float
    max_abstention_rate: Optional[float]
    feasible: bool
    reason: str


def kotte_min_abstention(base_risk: float, alpha: float) -> float:
    """The floor on abstention. ``0.0`` when the base risk already meets the target."""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie strictly between 0 and 1, got {alpha!r}")
    if not 0.0 <= base_risk <= 1.0:
        raise ValueError(f"base_risk must lie in [0, 1], got {base_risk!r}")
    if base_risk <= alpha:
        return 0.0
    return (base_risk - alpha) / (1.0 - alpha)


def assess(base_risk: float, alpha: float,
           max_abstention_rate: Optional[float] = None) -> Feasibility:
    """Is this budget certifiable, and at what cost in coverage?"""
    floor = kotte_min_abstention(base_risk, alpha)
    if max_abstention_rate is None:
        # No ceiling declared: the budget is feasible in principle, and the cost is reported so
        # nobody discovers it after the model is deployed. Abstaining on 40% of a queue is a
        # feasible budget and an unusable product, and those are different conversations.
        return Feasibility(alpha=alpha, base_risk=base_risk, min_abstention_rate=floor,
                           max_abstention_rate=None, feasible=True,
                           reason=(f"attainable: base risk {base_risk:.3f} against target "
                                   f"{alpha:.3f} forces abstention on at least {floor:.1%} of "
                                   f"examples. No ceiling was declared, so nothing bounds that"))
    feasible = floor <= max_abstention_rate
    if feasible:
        reason = (f"attainable within the declared ceiling: floor {floor:.1%} <= "
                  f"{max_abstention_rate:.1%}")
    else:
        reason = (f"UNCERTIFIABLE: base risk {base_risk:.3f} against target {alpha:.3f} forces "
                  f"abstention on at least {floor:.1%} of examples, and the plan declares a "
                  f"ceiling of {max_abstention_rate:.1%}. No model, threshold or calibration set "
                  f"reaches this budget — the remedy is a higher alpha or a higher ceiling, not a "
                  f"better model")
    return Feasibility(alpha=alpha, base_risk=base_risk, min_abstention_rate=floor,
                       max_abstention_rate=max_abstention_rate, feasible=feasible, reason=reason)


__all__ = ["Feasibility", "kotte_min_abstention", "assess"]
