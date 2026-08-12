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
    return GraphNode(id=nid, kind=kw.pop("kind", NodeKind.PATIENT), label=label,
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
    # entity pointers and per-attribute pointers are counted apart: they ground different
    # claims, and one figure covering both says nothing about how much of what is DISPLAYED
    # is cited.
    assert s.counts() == {"nodes": 1, "edges": 0, "sources": 1, "attribute_sources": 0}


def test_node_without_pointer_is_refused_at_construction():
    # the anti-fabrication wall fires at CONSTRUCTION (P4) — never reaches the store
    with pytest.raises(ValueError, match="anti-fabrication|SourcePointer|REFUSED"):
        GraphNode(id="x", kind=NodeKind.TEST, label="ghost", sources=[],
                  valid_from=_T0, system_from=_LEARN)


def test_edge_without_pointer_is_refused():
    with pytest.raises(ValueError, match="REFUSED|SourcePointer"):
        GraphEdge(id="e", src_id="a", dst_id="b", kind=EdgeKind.COVERED_BY, sources=[],
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


# --- per-attribute and per-version provenance (R-01 / R-27) ------------------------------------


def test_entity_pointers_and_attribute_pointers_are_kept_apart():
    """`sources_for` grounds the ENTITY; `attr_sources_for` grounds individual values. Pooling
    them makes one long citation list under a single heading, from which a reader — or a model —
    infers that every value shown was cited by something in it."""
    s = _store()
    entity, attr = _ptr("doc1"), _ptr("registry")
    s.add_node(GraphNode(id="person:b", kind=NodeKind.PATIENT, label="B",
                         attrs={"sex": "F"}, attr_sources={"sex": attr},
                         sources=[entity], valid_from=_T0, system_from=_LEARN))

    assert [p.source_id for p in s.sources_for("person:b")] == ["doc1"]
    assert {k: p.source_id for k, p in s.attr_sources_for("person:b").items()} == {"sex": "registry"}


def test_a_superseded_versions_pointers_do_not_render_beside_its_replacement():
    """The R-27 defect at its root. `sources` was keyed by entity id alone, so every pointer ever
    attached to that id came back for every version — and the store's own limitation notice said
    so. A citation that resolves to a span no longer saying what the value says is worse than an
    absent one: it passes a spot check."""
    s = _store()
    v1, v2 = _ptr("letter-v1"), _ptr("letter-v2")
    s.add_node(GraphNode(id="person:c", kind=NodeKind.PATIENT, label="C",
                         attrs={"sex": "F"}, attr_sources={"sex": v1},
                         sources=[v1], valid_from=_T0, system_from=_LEARN))
    later = _LEARN + dt.timedelta(days=1)
    s.replace_node(GraphNode(id="person:c", kind=NodeKind.PATIENT, label="C",
                             attrs={"sex": "M"}, attr_sources={"sex": v2},
                             sources=[v2], valid_from=_T0, system_from=later),
                   at=later, valid_to=later)

    open_row = s.open_node_row("person:c")
    node = row_to_node(s, open_row)
    assert node.attrs["sex"] == "M"
    assert node.attr_sources["sex"].source_id == "letter-v2", (
        "the current value is cited by the superseded version's pointer")
    assert [p.source_id for p in node.sources] == ["letter-v2"]

    # the earlier version still holds its own evidence — invalidate, never delete (ADR-0003)
    superseded = [r for r in s.all_node_versions("person:c") if r["system_to"] is not None]
    assert [p.source_id for p in row_to_node(s, superseded[0]).sources] == ["letter-v1"]


def test_corroborating_pointers_attach_to_the_version_they_were_checked_against():
    s = _store()
    s.add_node(_node())
    s.append_sources("person:a", [_ptr("doc2")])
    row = s.open_node_row("person:a")
    assert {p.source_id for p in row_to_node(s, row).sources} == {"doc1", "doc2"}


# --- the wall has to hold where values become durable (codex, 2026-08-12) ----------------------


def test_a_mutated_node_is_refused_at_the_store_even_though_it_was_valid_when_built():
    """`ConfigDict(frozen=True)` freezes the model's attributes, not the dicts they point at, so
    `node.attrs["sex"] = "M"` succeeds on a node that passed validation. A wall that fires only at
    construction does not protect anything held across a few statements."""
    s = _store()
    node = GraphNode(id="person:d", kind=NodeKind.PATIENT, label="D",
                     attrs={"sex": "F"}, attr_sources={"sex": _ptr()},
                     sources=[_ptr()], valid_from=_T0, system_from=_LEARN)
    node.attrs["nationality"] = "Filipino"          # no pointer — invented after validation
    with pytest.raises(ValueError, match="no SourcePointer of their own"):
        s.add_node(node)


def test_a_pointer_stripped_after_construction_is_refused_at_the_store():
    s = _store()
    node = GraphNode(id="person:e", kind=NodeKind.PATIENT, label="E",
                     attrs={"sex": "F"}, attr_sources={"sex": _ptr()},
                     sources=[_ptr()], valid_from=_T0, system_from=_LEARN)
    del node.attr_sources["sex"]
    with pytest.raises(ValueError, match="no SourcePointer of their own"):
        s.add_node(node)


def test_corroborating_an_entity_with_no_open_version_is_refused():
    """A pointer written with no version reads as applying to EVERY version, so an orphan would
    lie in wait and corroborate the next entity created with this id — a citation for a record it
    never saw."""
    s = _store()
    with pytest.raises(ValueError, match="no open version"):
        s.append_sources("person:never-existed", [_ptr()])


def test_supersession_refuses_and_writes_nothing_when_there_is_no_single_open_version():
    """The caller's `assert closed == 1` ran AFTER the commit and vanishes under `python -O`, so
    it never protected the transaction."""
    s = _store()
    with pytest.raises(ValueError, match="expected exactly 1"):
        s.replace_node(_node(), at=_LEARN, valid_to=_LEARN)
    assert s.counts()["nodes"] == 0, "a refused supersession still wrote a row"


def test_a_legacy_store_without_the_new_columns_migrates_instead_of_crashing():
    """`CREATE TABLE IF NOT EXISTS` will not add a column, so creating an index that names one
    fails on exactly the old store the migration exists to rescue — before it can run."""
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE nodes (rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,"
            " kind TEXT NOT NULL, label TEXT NOT NULL, canonical_id TEXT,"
            " attrs_json TEXT NOT NULL DEFAULT '{}', valid_from TEXT NOT NULL, valid_to TEXT,"
            " system_from TEXT NOT NULL, system_to TEXT);"
            "CREATE TABLE edges (rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,"
            " src_id TEXT NOT NULL, dst_id TEXT NOT NULL, kind TEXT NOT NULL,"
            " weight REAL NOT NULL DEFAULT 1.0, attrs_json TEXT NOT NULL DEFAULT '{}',"
            " valid_from TEXT NOT NULL, valid_to TEXT, system_from TEXT NOT NULL, system_to TEXT);"
            "CREATE TABLE sources (entity_id TEXT NOT NULL, source_pointer_json TEXT NOT NULL);")
        conn.execute("INSERT INTO sources VALUES ('person:a', ?)", (_ptr().model_dump_json(),))
        conn.commit()
        conn.close()

        store = GraphStore(path)
        store.add_node(_node())
        # the pre-existing pointer has version_pk NULL and stays readable
        assert {p.source_id for p in store.sources_for("person:a")} == {"doc1"}
        store.close()


def test_the_open_version_is_the_one_the_renderers_show():
    """An unordered `UNION ALL ... fetchone` returns the LOWEST rowid, while every renderer reads
    through `open_node_row`, which takes the highest. With two open rows for one id the store
    cited one version and displayed another, and corroboration attached to the version nobody
    looks at — the signal vanished silently."""
    s = _store()
    def org(src):
        return GraphNode(id="org:coral", kind=NodeKind.ORG, label="Coral Harbour",
                         sources=[_ptr(src)], valid_from=_T0, system_from=_LEARN)

    s.add_node(org("d1"))
    s.add_node(org("d2"))                             # a second open row for the same id
    s.append_sources("org:coral", [_ptr("d3")])

    shown = row_to_node(s, s.open_node_row("org:coral"))
    assert "d3" in {p.source_id for p in shown.sources}, (
        "corroboration landed on a version the renderers never display")
