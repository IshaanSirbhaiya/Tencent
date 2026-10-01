"""Two shapes this build does not produce. A refusal that names the missing piece is a design
artefact; an empty rectangle is a bug waiting to be mistaken for a dataset.

The messages are asserted rather than the exception type alone, because the whole value of these
refusals is that the next person to pick the work up learns what has to exist first — and, for the
conversational shape, why the shortcut is not merely unbuilt but unavailable.
"""
from __future__ import annotations

import pytest

from databridge_materialise import ShapeNotImplemented, build_conversational, build_graph_tensor


def test_the_graph_tensor_shape_names_the_negative_sample_set():
    """SYSTEM_DESIGN §6.6(b). The missing piece is not the tensor: a temporal-graph dataset without
    pinned negatives is not reproducible however well the positives are versioned, and nobody
    packages this today."""
    with pytest.raises(ShapeNotImplemented) as err:
        build_graph_tensor(None, None)
    message = str(err.value)
    assert "NEGATIVE SAMPLE SET" in message
    assert "not reproducible" in message
    assert "t_valid" in message and "t_system" in message, (
        "the per-edge dual-timestamp contract is a property of the CONSUMER and has to be named")
    assert "heuristic" in message.lower(), (
        "on TGB leaderboards heuristics sit within ~0.6-1.5 MRR points of the best models; a "
        "graph dataset shipped without baselines invites a GNN that does not earn its complexity")
    assert "tabular" in message, "a refusal should say what to do instead"


def test_the_conversational_shape_refuses_on_the_identity_constraint():
    """SYSTEM_DESIGN §6.6(d), and `CLAUDE.md` constraint 1.

    Three of the four missing controls are features. The fourth is not: no synthesised dialogue may
    depict the system asserting an identity match, and that applies to training data exactly as it
    applies at runtime. A shape that could emit such a turn — even filtered afterwards — is the
    wrong shape, which is why this one is not half-built.
    """
    with pytest.raises(ShapeNotImplemented) as err:
        build_conversational(None, None)
    message = str(err.value)
    assert "generated_claim" in message
    assert "depth <= 1" in message, "the structural anti-collapse control has to be named"
    assert "min_grounded_fraction" in message
    assert "identity match" in message.lower()
    assert "not a missing feature" in message


def test_neither_refusal_says_not_implemented_and_stops_there():
    """A guard against the refusals decaying into stubs over time."""
    for builder in (build_graph_tensor, build_conversational):
        with pytest.raises(ShapeNotImplemented) as err:
            builder(None, None)
        assert len(str(err.value)) > 400
        assert "TODO" not in str(err.value)
