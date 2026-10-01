"""`canonical_id` is the exact-merge key, and it used to need no citation of its own (R-001/A4).

`GraphNode.attrs` are grounded one pointer per attribute: the entity-level `sources` ground the
claim that this thing exists and was read from somewhere, and they deliberately do NOT vouch for
individual values. `canonical_id` and `label` are **top-level fields**, so they fell outside that
wall entirely — an invented `canonical_id` needed no pointer at all, while the entity's `sources`
sat beside it appearing to vouch for it.

That matters because `canonical_id` is what `merge._resolve_existing` merges on. Merging two
records of a **subject** is the machine deciding whose record this is — the determination reserved
for a named human (CLAUDE.md §Hard constraints 1) — and an ungrounded key was enough to drive it.

Two changes, and the tests below separate them because they are different strengths:

1. identity-bearing fields can now be grounded per-attribute at all (`attr_sources` accepts them,
   and rules 2 and 3 of the wall apply to them unchanged);
2. an **ungrounded identifier on a subject cannot drive an exact merge** — no match, a fresh node
   is inserted, and the duplicate stays visible. Two rows staff can tell apart beats one row that
   silently fused two people.

The honest limit is recorded at the bottom: construction of an ungrounded `canonical_id` is still
*permitted*, because callers outside this package still build such nodes. The harm is closed at
the merge boundary rather than at the constructor, and the test that says so names what has to
change before the constructor can be tightened too.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_core import SourcePointer
from databridge_graph.kinds import NodeKind
from databridge_graph.merge import upsert_node
from databridge_graph.packs import active_vocabulary
from databridge_graph.schema import IDENTITY_BEARING_FIELDS, GraphNode, node_id
from databridge_graph.store import GraphStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pointer(row: str = "1") -> SourcePointer:
    return SourcePointer(source_id="s", kind="table_cell", table="t", column="c", row_id=row)


def _subject_kind() -> NodeKind:
    return NodeKind(sorted(active_vocabulary().subject_kinds)[0])


def _non_subject_kind() -> NodeKind:
    vocab = active_vocabulary()
    return NodeKind(sorted(set(vocab.node_kinds) - set(vocab.subject_kinds))[0])


def _node(kind: NodeKind, slug: str, label: str, *, canonical=None, ground_canonical=False,
          **kw) -> GraphNode:
    attr_sources = dict(kw.pop("attr_sources", {}))
    if ground_canonical and canonical is not None:
        attr_sources["canonical_id"] = _pointer(canonical)
    return GraphNode(id=node_id(kind, slug), kind=kind, label=label, canonical_id=canonical,
                     attr_sources=attr_sources, sources=(_pointer(),),
                     valid_from=_now(), system_from=_now(), **kw)


# --- (1) grounding an identity-bearing field is now possible at all ------------------------

def test_an_identity_field_can_carry_its_own_pointer():
    node = _node(_subject_kind(), "a", "A", canonical="X1", ground_canonical=True)
    assert node.identity_field_is_grounded("canonical_id")
    assert node.source_for("canonical_id") is not None


def test_the_entity_level_sources_do_not_count_as_grounding_for_the_key():
    """The distinction the whole per-attribute wall exists for."""
    node = _node(_subject_kind(), "b", "B", canonical="X2")
    assert node.sources                                   # the entity IS grounded
    assert not node.identity_field_is_grounded("canonical_id")


def test_a_pointer_for_an_absent_identifier_is_still_a_citation_for_nothing():
    """Rule 2 of the wall applies to the identity fields unchanged: a pointer beside a `None`
    reads as though the absence were established."""
    with pytest.raises(ValidationError):
        GraphNode(id="n:1", kind=_subject_kind(), label="L", canonical_id=None,
                  attr_sources={"canonical_id": _pointer()}, sources=(_pointer(),),
                  valid_from=_now(), system_from=_now())


def test_an_attribute_may_not_shadow_an_identity_field():
    """One name, two values and one pointer is unreadable — whichever it cites, the other is
    uncited and looks cited."""
    with pytest.raises(ValidationError):
        GraphNode(id="n:2", kind=_subject_kind(), label="L", canonical_id="X",
                  attrs={"canonical_id": "something else"},
                  attr_sources={"canonical_id": _pointer()}, sources=(_pointer(),),
                  valid_from=_now(), system_from=_now())


def test_a_pointer_for_a_field_that_is_neither_attribute_nor_identity_field_is_refused():
    with pytest.raises(ValidationError):
        GraphNode(id="n:3", kind=_subject_kind(), label="L",
                  attr_sources={"not_a_field": _pointer()}, sources=(_pointer(),),
                  valid_from=_now(), system_from=_now())


def test_identity_field_is_grounded_refuses_a_field_that_is_not_one():
    node = _node(_subject_kind(), "c", "C")
    with pytest.raises(KeyError):
        node.identity_field_is_grounded("weight")
    assert set(node.identity_values()) == set(IDENTITY_BEARING_FIELDS)


# --- (2) an ungrounded identifier cannot merge two subject records -------------------------

def test_an_ungrounded_identifier_does_not_merge_two_subject_records():
    store = GraphStore()
    kind = _subject_kind()
    first = _node(kind, "one", "Someone", canonical="SAME", ground_canonical=True)
    assert upsert_node(store, first).action == "inserted"

    forged = _node(kind, "two", "Someone Else", canonical="SAME")     # same key, no citation
    outcome = upsert_node(store, forged)
    assert outcome.action == "inserted", (
        "an INVENTED identifier merged two subject records — that is the machine deciding whose "
        "record this is (CLAUDE.md §Hard constraints 1)")
    assert len(store.open_nodes_of_kind(kind)) == 2, "the duplicate must stay visible"


def test_a_grounded_identifier_still_corroborates_the_same_subject_record():
    """The tightening must not cost idempotency: a second record carrying the SAME grounded
    identifier corroborates rather than duplicating.

    The two nodes are given different ids on purpose. `_resolve_existing` matches an equal id
    first (that is bookkeeping, not an identity judgement), so re-applying a byte-identical node
    would corroborate without the canonical branch running at all — and the canonical branch is
    what the grounding rule above guards. Different ids, one grounded identifier: the identifier
    is the only thing that can join them, which is the case this test exists for.
    """
    store = GraphStore()
    kind = _subject_kind()
    first = _node(kind, "one", "Someone", canonical="KEY", ground_canonical=True)
    second = _node(kind, "two", "Someone", canonical="KEY", ground_canonical=True)
    assert upsert_node(store, first).action == "inserted"
    again = upsert_node(store, second)
    assert again.action == "corroborated"
    assert again.matched_by == "canonical"
    assert len(store.open_nodes_of_kind(kind)) == 1


def test_a_non_subject_is_unaffected_by_the_identifier_rule():
    """The rule protects subjects. Organisations and documents merge on an exact key as before —
    a tightening that changed unrelated behaviour would be a regression wearing a safety label."""
    store = GraphStore()
    kind = _non_subject_kind()
    a = _node(kind, "one", "Coral Harbour", canonical="CH")
    upsert_node(store, a)
    b = _node(kind, "one", "Coral Harbour", canonical="CH")
    assert upsert_node(store, b).action == "corroborated"


def test_an_upsert_touching_a_subject_says_a_human_still_has_to_choose():
    """Record linkage is not an identity determination, and an outcome that only said
    "corroborated" lost that distinction silently."""
    store = GraphStore()
    subject = upsert_node(store, _node(_subject_kind(), "one", "Someone",
                                       canonical="K", ground_canonical=True))
    assert subject.requires_human_selection is True

    other = upsert_node(store, _node(_non_subject_kind(), "org", "Coral Harbour"))
    assert other.requires_human_selection is False


def test_a_subject_is_still_never_merged_by_name_similarity():
    """The D4b rule this sits beside, re-asserted here so a change to the canonical path cannot
    quietly re-open the fuzzy one."""
    store = GraphStore()
    kind = _subject_kind()
    upsert_node(store, _node(kind, "one", "Loh Wei Ming"))
    upsert_node(store, _node(kind, "two", "Loh Wei Ming"))
    assert len(store.open_nodes_of_kind(kind)) == 2


# --- the honest limit ----------------------------------------------------------------------

def test_an_ungrounded_identifier_is_still_CONSTRUCTIBLE_and_that_is_the_open_half():
    """The wall is at the MERGE boundary, not at the constructor, and this test records why.

    Requiring a pointer for `canonical_id` at construction would be the stronger fix. It is not
    done here because call sites outside `packages/graph` still build such nodes — at the time of
    writing, `packages/evals/databridge_evals/e_intake.py`,
    `apps/gateway/tests/test_gateway.py` and `apps/mcp_server/tests/test_brain_server.py` — and
    this change is scoped to `packages/graph`. Those three must ground their `canonical_id`
    before the constructor can be tightened.

    Until then the harm is closed where it does damage: an ungrounded identifier can exist, and
    it cannot decide that two subject records are one person.
    """
    node = _node(_subject_kind(), "loose", "Someone", canonical="UNCITED")
    assert node.canonical_id == "UNCITED"
    assert not node.identity_field_is_grounded("canonical_id")


def test_label_is_named_as_identity_bearing_but_not_yet_required():
    """`label` is what name-similarity merges on and what a human reads as "who this is", so it is
    listed in `IDENTITY_BEARING_FIELDS` and CAN now be grounded. Requiring it would touch every
    one of the ~50 node construction sites in the tree, so it is not required yet — stated here
    rather than left as a silent gap between the constant and the enforcement."""
    assert "label" in IDENTITY_BEARING_FIELDS
    grounded = GraphNode(id="n:9", kind=_subject_kind(), label="A Name",
                         attr_sources={"label": _pointer()}, sources=(_pointer(),),
                         valid_from=_now(), system_from=_now())
    assert grounded.identity_field_is_grounded("label")
    ungrounded = _node(_subject_kind(), "plain", "A Name")
    assert not ungrounded.identity_field_is_grounded("label")
