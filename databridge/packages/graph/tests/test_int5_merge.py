"""INT5 — the three merge.py changes R-022 build cross-track 2 asked for (N-16 / N-17 root causes).

(a) a merge keeps a node's label unless the incoming label carries its OWN pointer: a surface form
    a later document happens to use does not silently relabel the node, and a relabel that does
    happen carries the pointer that grounds the new label;
(b) a canonical id found for the first time on a node that had none is CARRIED (it used to take
    the corroborate path, which only appends pointers, so the key was dropped);
(c) `resolve_existing` is public (mcp_ingest's build imported the private name).

Org nodes, as in test_merge.py: fuzzy merging is right for organisations, never for subjects.
"""
from __future__ import annotations

import datetime as dt

from databridge_core import SourcePointer
from databridge_graph import GraphNode, GraphStore, NodeKind, upsert_node
from databridge_graph.schema import node_id

_UTC = dt.timezone.utc
_T0 = dt.datetime(2026, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2026, 8, 1, tzinfo=_UTC)
ID = node_id(NodeKind.ORG, "coral harbour pte ltd")


def _ptr(src="d", start=0, end=5):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=start,
                         char_end=end)


def _org(label, *, canonical=None, label_ptr=None, canonical_ptr=None, src="d", attrs=None):
    attr_sources = {k: _ptr(src) for k, v in (attrs or {}).items() if v is not None}
    if label_ptr is not None:
        attr_sources["label"] = label_ptr
    if canonical_ptr is not None:
        attr_sources["canonical_id"] = canonical_ptr
    return GraphNode(id=ID, kind=NodeKind.ORG, label=label, canonical_id=canonical,
                     attrs=attrs or {}, attr_sources=attr_sources, sources=[_ptr(src)],
                     valid_from=_T0, system_from=_LEARN)


def _store():
    return GraphStore(clock=lambda: _LEARN)


def _open(s):
    from databridge_graph.store import row_to_node

    return row_to_node(s, s.open_node_row(ID))


# --- (a) the label ------------------------------------------------------------------------------
def test_int5_6a_an_ungrounded_surface_form_does_not_relabel_the_node():
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd", label_ptr=_ptr("a", 0, 21), src="a"))
    out = upsert_node(s, _org("CORAL HARBOUR PTE LIMITED", src="b"))
    node = _open(s)
    assert node.label == "Coral Harbour Pte Ltd"                       # kept
    assert node.attr_sources["label"].source_id == "a"                 # with ITS pointer
    assert out.action == "corroborated"
    assert {p.source_id for p in node.sources} == {"a", "b"}           # the sighting still counts


def test_int5_6a_a_relabel_carries_the_pointer_that_grounds_the_new_label():
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd", label_ptr=_ptr("a", 0, 21), src="a"))
    out = upsert_node(s, _org("Coral Harbour Holdings", label_ptr=_ptr("b", 3, 25), src="b"))
    node = _open(s)
    assert out.action == "superseded"
    assert node.label == "Coral Harbour Holdings"
    assert node.attr_sources["label"].source_id == "b"


def test_int5_6a_an_ungrounded_label_never_takes_over_a_grounded_one_on_a_real_conflict():
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd", label_ptr=_ptr("a", 0, 21), src="a"))
    out = upsert_node(s, _org("CORAL HARBOUR", src="b", attrs={"country": "SG"}))
    node = _open(s)
    assert out.action == "superseded" and node.attrs["country"] == "SG"   # the new fact lands
    assert node.label == "Coral Harbour Pte Ltd"                           # the label does not
    assert node.attr_sources["label"].source_id == "a"


# --- (b) the canonical id -----------------------------------------------------------------------
def test_int5_6b_a_newly_grounded_canonical_id_is_carried_onto_a_node_that_had_none():
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd", src="a"))
    assert _open(s).canonical_id is None
    out = upsert_node(s, _org("Coral Harbour Pte Ltd", canonical="C-1001",
                              canonical_ptr=_ptr("b", 7, 13), src="b"))
    node = _open(s)
    assert out.action == "superseded"
    assert node.canonical_id == "C-1001"
    assert node.attr_sources["canonical_id"].source_id == "b"
    assert s.find_by_canonical(NodeKind.ORG, "C-1001") is not None       # the join key now works


def test_int5_6b_an_existing_canonical_id_is_not_a_conflict_when_re_seen():
    s = _store()
    first = _org("Coral Harbour Pte Ltd", canonical="C-1001", canonical_ptr=_ptr("a"), src="a")
    upsert_node(s, first)
    out = upsert_node(s, _org("Coral Harbour Pte Ltd", canonical="C-1001",
                              canonical_ptr=_ptr("b"), src="b"))
    assert out.action == "corroborated" and _open(s).canonical_id == "C-1001"


# --- (c) public name ------------------------------------------------------------------------------
def test_int5_6c_resolve_existing_is_public_and_is_the_same_function():
    from databridge_graph import merge

    assert callable(merge.resolve_existing)
    assert merge._resolve_existing is merge.resolve_existing             # back-compat alias
    s = _store()
    upsert_node(s, _org("Coral Harbour Pte Ltd"))
    row, how = merge.resolve_existing(s, _org("Coral Harbour Pte Ltd"))
    assert row is not None and how == "id"
