"""A "closed" vocabulary that can be widened at runtime is not closed (review R-001/A6).

`Pack` and `Vocabulary` are frozen dataclasses. `dataclass(frozen=True)` freezes the *bindings*,
not the dictionaries they point at, so before 2026-08-16 this worked:

    active_vocabulary().node_kinds["invented"] = NodeKindDecl(...)
    NodeKind("invented")                       # accepted — the closure just widened
    del active_vocabulary().subject_kinds[<a subject>]
    # `never_fuzzy_merge` just shrank; that subject may now be merged by name similarity

Both mutations land on the **process-wide cached** vocabulary, so they persist for every later
caller in that process. The second is the dangerous one: deleting a subject is how the D4b
protection is removed without touching a line of pack YAML or engine code.

The fix is `MappingProxyType` over a **copy**. Both halves matter — a proxy over the caller's own
dict leaves them holding a live handle to the interior, and a proxy is a view, not a snapshot.
"""
from __future__ import annotations

import pytest

from databridge_graph.kinds import NodeKind
from databridge_graph.packs import (
    NodeKindDecl,
    Pack,
    UnknownKind,
    Vocabulary,
    active_vocabulary,
    build_vocabulary,
    load_pack,
)
from databridge_graph.meta import NodeMetaKind


@pytest.mark.parametrize("axis", ["node_kinds", "edge_kinds", "subject_kinds"])
def test_a_vocabulary_mapping_cannot_be_widened(axis):
    vocab = active_vocabulary()
    mapping = getattr(vocab, axis)
    with pytest.raises(TypeError):
        mapping["invented"] = object()


@pytest.mark.parametrize("axis", ["node_kinds", "edge_kinds", "subject_kinds"])
def test_a_vocabulary_mapping_cannot_be_narrowed(axis):
    """Deleting a *subject* is the one that silently disables `never_fuzzy_merge` for it."""
    vocab = active_vocabulary()
    mapping = getattr(vocab, axis)
    existing = next(iter(mapping))
    with pytest.raises(TypeError):
        del mapping[existing]


def test_widening_the_vocabulary_would_have_widened_the_closed_kind_set():
    """Names the consequence, so the test says why it exists rather than only what it checks."""
    with pytest.raises(UnknownKind):
        NodeKind("invented_at_runtime")
    with pytest.raises(TypeError):
        active_vocabulary().node_kinds["invented_at_runtime"] = NodeKindDecl(
            name="invented_at_runtime", binds_to=NodeMetaKind.ENTITY, pack="nowhere")
    with pytest.raises(UnknownKind):
        NodeKind("invented_at_runtime")


def test_removing_a_subject_cannot_disable_its_merge_protection():
    vocab = active_vocabulary()
    subject = next(iter(vocab.subject_kinds))
    assert subject in vocab.never_fuzzy_merge
    with pytest.raises(TypeError):
        del vocab.subject_kinds[subject]
    assert subject in active_vocabulary().never_fuzzy_merge


@pytest.mark.parametrize("axis", ["node_kinds", "edge_kinds", "subject_kinds"])
def test_a_pack_mapping_is_sealed_too(axis):
    """Layer 1 as well as Layer 2: packs are cached by path, so a mutated `Pack` poisons every
    vocabulary later built from it."""
    pack = load_pack("healthcare")
    with pytest.raises(TypeError):
        getattr(pack, axis)["invented"] = object()


def test_the_mapping_is_a_copy_not_a_live_view_of_the_caller_s_dict():
    """`MappingProxyType` is a VIEW. Wrapping the caller's own dict would leave them holding a
    handle to the interior — sealing the front door and leaving the back one open."""
    source = {"a": NodeKindDecl(name="a", binds_to=NodeMetaKind.ENTITY, pack="p")}
    vocab = Vocabulary(packs=("p",), node_kinds=source, edge_kinds={}, subject_kinds={})
    source["b"] = NodeKindDecl(name="b", binds_to=NodeMetaKind.ENTITY, pack="p")
    assert "b" not in vocab.node_kinds
    assert set(vocab.node_kinds) == {"a"}


def test_sealing_did_not_change_what_the_vocabulary_answers():
    """A tightening that changes behaviour is not a tightening. Equality against a plain dict,
    membership, iteration and `.get` all still work — `mappingproxy` compares equal to the dict
    it wraps, which is what keeps every existing call site untouched."""
    built = build_vocabulary(["healthcare"])
    pack = load_pack("healthcare")
    assert built.node_kinds == dict(pack.node_kinds)
    assert set(built.edge_kinds) == set(pack.edge_kinds)
    assert built.node_kinds.get("nothing_here") is None
    assert built.never_fuzzy_merge == frozenset(
        s.kind for s in pack.subject_kinds.values() if s.never_fuzzy_merge)


def test_a_pack_object_is_still_frozen_at_the_field_level():
    pack = load_pack("healthcare")
    with pytest.raises(Exception):
        pack.name = "renamed"                       # type: ignore[misc]


def test_the_seal_survives_construction_from_another_sealed_mapping():
    """`build_vocabulary` feeds `Pack` mappings back into `Vocabulary`. Wrapping a proxy in a
    proxy must not lose the copy, or the second object would alias the first's interior."""
    pack = load_pack("healthcare")
    vocab = Vocabulary(packs=("healthcare",), node_kinds=pack.node_kinds,
                       edge_kinds=pack.edge_kinds, subject_kinds=pack.subject_kinds)
    with pytest.raises(TypeError):
        vocab.node_kinds["invented"] = object()
    assert vocab.node_kinds == dict(pack.node_kinds)
