"""GraphStore — schema, bi-temporal writes, anti-fabrication wall (T08, ADR-0003)."""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_core import SourcePointer
from databridge_graph import GraphEdge, GraphNode, GraphStore, NodeKind, EdgeKind, row_to_node

_UTC = dt.timezone.utc
_T0 = dt.datetime(2024, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2024, 6, 1, tzinfo=_UTC)


def _ptr(src="doc1"):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=0, char_end=5)


def _node(nid="person:a", label="Alice", **kw):
    return GraphNode(id=nid, kind=kw.pop("kind", NodeKind.PERSON), label=label,
                     sources=[_ptr()], valid_from=_T0, system_from=_LEARN, **kw)


def _store():
    return GraphStore(clock=lambda: _LEARN)


def test_add_node_and_read_back():
    s = _store()
    s.add_node(_node())
    row = s.open_node_row("person:a")
    assert row is not None and row["label"] == "Alice"
    node = row_to_node(s, row)
    assert node.sources and node.sources[0].kind == "doc_span"
    assert s.counts() == {"nodes": 1, "edges": 0, "sources": 1}


def test_node_without_pointer_is_refused_at_construction():
    # the anti-fabrication wall fires at CONSTRUCTION (P4) — never reaches the store
    with pytest.raises(ValueError, match="anti-fabrication|SourcePointer|REFUSED"):
        GraphNode(id="x", kind=NodeKind.FACT, label="ghost", sources=[],
                  valid_from=_T0, system_from=_LEARN)


def test_edge_without_pointer_is_refused():
    with pytest.raises(ValueError, match="REFUSED|SourcePointer"):
        GraphEdge(id="e", src_id="a", dst_id="b", kind=EdgeKind.WORKS_AT, sources=[],
                  valid_from=_T0, system_from=_LEARN)


def test_close_node_sets_both_temporal_axes():
    s = _store()
    s.add_node(_node())
    at = dt.datetime(2024, 7, 1, tzinfo=_UTC)
    n = s.close_node("person:a", at=at, valid_to=dt.datetime(2024, 6, 15, tzinfo=_UTC))
    assert n == 1
    row = s.conn.execute("SELECT * FROM nodes WHERE id='person:a'").fetchone()
    assert row["system_to"] is not None and row["valid_to"] is not None
    # closed ⇒ no longer the OPEN (believed) version
    assert s.open_node_row("person:a") is None


def test_sources_accumulate_and_round_trip():
    s = _store()
    s.add_node(_node())
    s._attach_sources("person:a", [_ptr("doc2")])
    s.conn.commit()
    ptrs = s.sources_for("person:a")
    assert {p.source_id for p in ptrs} == {"doc1", "doc2"}
