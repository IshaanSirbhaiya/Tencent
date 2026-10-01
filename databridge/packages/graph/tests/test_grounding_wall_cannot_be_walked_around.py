"""The anti-fabrication wall, tested against the two ways it was actually bypassed.

Found by review on 2026-08-16 and reproduced before being fixed. Both bypasses existed for the
entire life of the wall, which is worth stating plainly: the wall was documented, asserted in a
docstring, and relied upon by every claim the system makes about provenance — and it had a hole
you could walk through by *omitting an argument*.

1. `sources` carried `Field(default_factory=list)`, and **Pydantic v2 does not validate
   defaults**. Omitting `sources` entirely produced an ungrounded node; the field validator
   never ran.
2. `ConfigDict(frozen=True)` is **shallow**. `node.sources.clear()` emptied a validated node's
   grounding in place, leaving a nominally frozen model in a state its own validator forbids.

These tests exist so a future refactor that reintroduces a default, or swaps the tuple back to a
list for convenience, fails loudly here instead of silently everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_core import SourcePointer
from databridge_graph.kinds import EdgeKind, NodeKind
from databridge_graph.schema import GraphEdge, GraphNode


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pointer() -> SourcePointer:
    return SourcePointer(source_id="s", kind="table_cell", table="t", column="c", row_id="1")


def _a_node_kind() -> NodeKind:
    """Any kind the active deployment declares — the test must not name a domain concept."""
    return next(iter(NodeKind))


def _an_edge_kind() -> EdgeKind:
    return next(iter(EdgeKind))


# --- bypass 1: the unvalidated default ---------------------------------------------------

def test_a_node_cannot_be_built_by_omitting_sources():
    with pytest.raises(ValidationError):
        GraphNode(id="n:1", kind=_a_node_kind(), label="L",
                  valid_from=_now(), system_from=_now())


def test_an_edge_cannot_be_built_by_omitting_sources():
    with pytest.raises(ValidationError):
        GraphEdge(id="e:1", src_id="n:1", dst_id="n:2", kind=_an_edge_kind(),
                  valid_from=_now(), system_from=_now())


@pytest.mark.parametrize("empty", [[], (), None])
def test_an_explicitly_empty_source_list_is_still_refused(empty):
    with pytest.raises(ValidationError):
        GraphNode(id="n:2", kind=_a_node_kind(), label="L", sources=empty,
                  valid_from=_now(), system_from=_now())


# --- bypass 2: the shallow freeze --------------------------------------------------------

def test_grounding_cannot_be_removed_after_validation():
    node = GraphNode(id="n:3", kind=_a_node_kind(), label="L", sources=[_pointer()],
                     valid_from=_now(), system_from=_now())
    assert isinstance(node.sources, tuple), (
        "sources must be an immutable container: a list lets a validated node be emptied in "
        "place, which is how the wall was bypassed before 2026-08-16")
    with pytest.raises(AttributeError):
        node.sources.clear()          # type: ignore[attr-defined]


def test_the_field_is_still_forgiving_of_list_input():
    """Immutability must not cost the 69 call sites that pass a list."""
    node = GraphNode(id="n:4", kind=_a_node_kind(), label="L", sources=[_pointer()],
                     valid_from=_now(), system_from=_now())
    assert node.sources == (_pointer(),)


# --- the honest limit --------------------------------------------------------------------

def test_model_construct_still_bypasses_validation_and_that_is_documented():
    """`model_construct` skips validation by design — Pydantic offers no way to prevent it.

    This is asserted rather than fixed so the limit is visible: the construction-time wall is a
    guarantee about the NORMAL construction path, not a law of physics. The defence in depth is
    that the persistence boundary re-checks; this test records why that re-check exists.
    """
    ungrounded = GraphNode.model_construct(
        id="n:5", kind=_a_node_kind(), label="L", sources=(),
        valid_from=_now(), system_from=_now())
    assert ungrounded.sources == ()
