"""The two shapes this build does not produce, refusing by name.

A refusal is a design artefact; an empty rectangle is a bug waiting to be mistaken for a dataset.
Each message below names the *specific* missing piece, not "not implemented", so that whoever
picks it up knows what has to exist first and why the shortcut is not available.
"""
from __future__ import annotations

from ..compiler import CompiledPlan
from ..errors import ShapeNotImplemented
from ..reader import EstateReader


def build_graph_tensor(compiled: CompiledPlan, reader: EstateReader):
    """Refuse — SYSTEM_DESIGN §6.6(b). What is missing is the negative sample set."""
    raise ShapeNotImplemented(
        "shape 'graph_tensor' is not produced by this build, and the missing piece is not the "
        "tensor. Three things have to exist first:\n"
        "  1. **A materialised, hashed NEGATIVE SAMPLE SET.** A temporal-graph dataset without "
        "pinned negatives is not reproducible however well the positives are versioned — two "
        "runs sample different non-edges and score differently — and no packaging format ships "
        "this today. There is no negative sampler in this repo.\n"
        "  2. **Per-edge emission of BOTH timestamps**, with a loader contract that uses them "
        "differently: `t_valid` orders the world and `t_system` decides membership. Using only "
        "`t_valid` leaks every retro-dated fact; using only `t_system` teaches the model clerical "
        "latency. That is a property of the CONSUMER, and this package cannot enforce it in a "
        "file.\n"
        "  3. **Heuristic baselines in the receipt.** On TGB leaderboards heuristics sit within "
        "~0.6–1.5 MRR points of the best models, so a graph dataset shipped without them invites "
        "a GNN that does not earn its complexity.\n"
        "Materialise the same cohort as 'tabular' until those land: it is a weaker model and an "
        "honest artefact, which is the better of the two trades.")


def build_conversational(compiled: CompiledPlan, reader: EstateReader):
    """Refuse — SYSTEM_DESIGN §6.6(d). What is missing is the generated-claim provenance path."""
    raise ShapeNotImplemented(
        "shape 'conversational' is not produced by this build. It needs four controls that do not "
        "exist here, and the last one is a hard constraint rather than a feature:\n"
        "  1. **A `generated_claim` provenance path** — every assistant turn carries a claim whose "
        "parents are the grounded pointers that licensed it, and an assertive span with no "
        "attributable parent rejects the row. This package emits no generated claims at all.\n"
        "  2. **Provenance depth <= 1** — a generated claim may not be rooted solely in another "
        "generated claim. That is the structural anti-collapse control, and it has to be checked "
        "on the graph of claims, not on the text.\n"
        "  3. **A `min_grounded_fraction` floor in the plan and the achieved fraction in the "
        "receipt**, plus the generator model, version and licence.\n"
        "  4. **Identity-verification turns excluded BY CONSTRUCTION.** No synthesised dialogue "
        "may depict the system asserting an identity match. The constraint that "
        "`requires_human_selection` is `Literal[True]` applies to training data exactly as it "
        "applies at runtime, and a shape that could emit such a turn — even filtered afterwards — "
        "is the wrong shape. This one is not a missing feature; it is the reason the shape is not "
        "half-built.")


__all__ = ["build_graph_tensor", "build_conversational"]
