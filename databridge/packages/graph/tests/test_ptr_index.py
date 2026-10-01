"""The reverse pointer index — `source_id -> pointers -> nodes/edges -> emitted records`.

`SYSTEM_DESIGN.md` §5 / R02 recommendation 4. Every test here names an invariant, and each was
checked by removing the invariant and watching the test go red — a test that passes with the
guard deleted is documentation, not a test.

The invariant this file exists for, above all the others:

    A pointer the index never saw is UNKNOWN, never "nothing affected".

`test_a_pointer_the_index_never_saw_is_unknown_not_none` is the one to read first.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
from pathlib import Path

import pytest

from databridge_core import SourcePointer, Trust
from databridge_graph import (
    Answer,
    EdgeKind,
    GraphEdge,
    GraphNode,
    GraphStore,
    NodeKind,
    locator_key,
    pointer_identity,
)
from databridge_graph.ptr_index import INVALIDATING_VERDICTS, KNOWN_VERDICTS, normalise_verdict

_UTC = dt.timezone.utc
_T0 = dt.datetime(2024, 1, 1, tzinfo=_UTC)
_LEARN = dt.datetime(2024, 6, 1, tzinfo=_UTC)
_LATER = dt.datetime(2024, 9, 1, tzinfo=_UTC)


def _cell(src="registry", column="dob", row_id="R1", **kw):
    return SourcePointer(source_id=src, kind="table_cell", table="subjects", column=column,
                         row_id=row_id, **kw)


def _span(src="doc1"):
    return SourcePointer(source_id=src, kind="doc_span", document_id=src, char_start=0,
                         char_end=5)


def _store(path: str = ":memory:", clock=None):
    return GraphStore(path, clock=clock or (lambda: _LEARN))


def _node(nid="test:a", label="A", *, sources=None, attrs=None, attr_sources=None):
    return GraphNode(id=nid, kind=NodeKind.TEST, label=label,
                     sources=list(sources or [_span()]), attrs=dict(attrs or {}),
                     attr_sources=dict(attr_sources or {}),
                     valid_from=_T0, system_from=_LEARN)


# ---------------------------------------------------------------------------------------------
# 1. Maintained on write, in the same transaction, and it survives a restart
# ---------------------------------------------------------------------------------------------
def test_the_index_is_maintained_on_write_not_by_a_later_scan():
    """An index that can lag is wrong exactly when it matters. It is written by the same
    statement sequence as the graph, so it is complete the instant `add_node` returns."""
    s = _store()
    s.add_node(_node(attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    index = s.reverse_index

    assert index.coverage()[0] is Answer.KNOWN
    assert index.unindexed_pointer_count() == 0
    report = index.impact_of(source_id="registry")
    assert report.is_complete
    assert [(n.entity_id, n.field, n.value) for n in report.nodes] == [("test:a", "dob",
                                                                       "1990-01-01")]


def test_the_index_is_written_inside_the_graphs_own_transaction(monkeypatch):
    """If indexing fails, the graph write must fail with it.

    An index maintained in a *separate* transaction can disagree with the graph, and a reverse
    index that disagrees with the graph is worse than none: it answers confidently and wrongly.
    """
    s = _store()
    from databridge_graph import store as store_mod

    def explode(*a, **kw):
        raise RuntimeError("index write failed")

    monkeypatch.setattr(store_mod, "bind_pointer", explode)
    with pytest.raises(RuntimeError):
        s.add_node(_node())

    monkeypatch.undo()
    # Neither half landed. Without the rollback the node row sits in an open transaction and the
    # next unrelated commit publishes a node the index has never heard of.
    assert s.open_node_row("test:a") is None
    assert s.counts()["nodes"] == 0
    assert s.reverse_index.counts()["bindings"] == 0


def test_the_index_survives_a_process_restart():
    """It lives in the graph's own SQLite file, so it cannot be lost or separated from it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "g.db")
        s = _store(path)
        s.add_node(_node(attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
        s.reverse_index.record_emission("determination", "D-1", [_cell()])
        s.close()

        reopened = _store(path)
        report = reopened.reverse_index.impact_of(source_id="registry")
        assert report.is_complete
        assert [n.entity_id for n in report.nodes] == ["test:a"]
        assert [e.ref for e in report.emissions] == ["D-1"]
        reopened.close()


# ---------------------------------------------------------------------------------------------
# 2. Honesty: unknown is not none
# ---------------------------------------------------------------------------------------------
def test_a_pointer_the_index_never_saw_is_unknown_not_none():
    """**The invariant this module exists for.**

    "I have no record of anything relying on this" and "nothing relies on this" are different
    answers. An empty list presented as the second when it is the first is how a drift alarm
    becomes falsely reassuring — the operator reads it as "nothing to do".
    """
    s = _store()
    s.add_node(_node())
    index = s.reverse_index

    # Baseline: a fully indexed store may say "nothing affected", and that is a real answer.
    clean = index.impact_of(pointer=_cell(src="never-read"))
    assert clean.answer is Answer.KNOWN
    assert clean.nodes == () and clean.emissions == ()

    # Now write a pointer the way a pre-index graph or a bypassing path would: straight into
    # `sources`, with no binding row.
    s.conn.execute(
        "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) VALUES (?,?,?,?)",
        ("test:a", None, None, _cell(src="bypassed").model_dump_json()))
    s.conn.commit()

    for report in (index.impact_of(pointer=_cell(src="bypassed")),
                   index.impact_of(source_id="registry"),
                   index.impact_of(pointer=_cell(src="never-read"))):
        assert report.answer is Answer.UNKNOWN, "an unindexed pointer must not read as 'none'"
        assert not report.is_complete
        assert report.unindexed_pointers == 1
        assert "never indexed" in report.reason


def test_the_coverage_claim_counts_every_unindexed_pointer():
    s = _store()
    s.add_node(_node())
    for i in range(3):
        s.conn.execute(
            "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) "
            "VALUES (?,?,?,?)", ("test:a", None, None, _cell(row_id=f"R{i}").model_dump_json()))
    s.conn.commit()
    answer, reason, missing = s.reverse_index.coverage()
    assert (answer, missing) == (Answer.UNKNOWN, 3)
    assert "backfill" in reason.lower()


def test_a_verdict_on_an_unindexed_pointer_does_not_forge_coverage():
    """Verifying a pointer nothing cites must not make the index claim it covers it."""
    s = _store()
    s.add_node(_node())
    assert s.reverse_index.record_verdict(_cell(src="never-read"), "drift_material") is None
    assert s.reverse_index.counts()["pointers"] == 1        # only the node's own doc_span


# ---------------------------------------------------------------------------------------------
# 3. Pointer identity
# ---------------------------------------------------------------------------------------------
def test_the_same_cell_cited_by_two_values_is_one_row():
    """The natural key is `(source_id, kind, locator_key)`. If the same cell became two rows, a
    cascade over it would fan out quadratically and double-count everything it touched."""
    s = _store()
    s.add_node(_node("test:a", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    s.add_node(_node("test:b", attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    ptrs = s.fetchall("SELECT * FROM ptr_index WHERE source_id = 'registry'")
    assert len(ptrs) == 1
    report = s.reverse_index.impact_of(pointer=_cell())
    assert sorted(n.entity_id for n in report.nodes) == ["test:a", "test:b"]


def test_the_locator_key_separates_two_different_cells():
    assert locator_key(_cell(column="dob")) != locator_key(_cell(column="name"))
    assert locator_key(_cell(row_id="R1")) != locator_key(_cell(row_id="R2"))
    assert locator_key(_cell()) == locator_key(_cell())
    # trust and capture binding are properties of the READ, not of the place read from
    assert locator_key(_cell()) == locator_key(_cell(trust=Trust.TRUSTED, capture_digest="ab"))


def test_the_index_keeps_the_worse_trust_label():
    """Denormalised so a taint sweep needs no join — and it may only ever move towards
    untrusted, or the index becomes a laundering path (spine D3)."""
    s = _store()
    s.add_node(_node("test:a", sources=[_cell(trust=Trust.TRUSTED)]))
    s.add_node(_node("test:b", sources=[_cell(trust=Trust.UNTRUSTED)]))
    row = s.fetchone("SELECT trust FROM ptr_index WHERE source_id = 'registry'")
    assert row["trust"] == Trust.UNTRUSTED.value

    s2 = _store()
    s2.add_node(_node("test:a", sources=[_cell(trust=Trust.UNTRUSTED)]))
    s2.add_node(_node("test:b", sources=[_cell(trust=Trust.TRUSTED)]))
    assert s2.fetchone("SELECT trust FROM ptr_index")["trust"] == Trust.UNTRUSTED.value


def test_ancestry_is_indexed_so_a_derived_value_is_reachable():
    """A `derived` pointer compresses the rows it was computed over. When one of those rows
    drifts, the aggregate drifted too — so the closure walks DOWN the ancestry, not only across
    the direct citations. Without this leg an aggregate survives the invalidation of every input
    it had."""
    s = _store()
    parent = _cell(trust=Trust.UNTRUSTED)
    derived = SourcePointer(source_id="calc", kind="derived", derivation="COUNT over subjects",
                            derived_from=[parent], trust=Trust.UNTRUSTED)
    s.add_node(_node("test:agg", sources=[derived]))

    report = s.reverse_index.impact_of(source_id="registry")
    assert [n.entity_id for n in report.nodes] == ["test:agg"], (
        "a value derived from the drifted cell must be reachable from the cell's source")
    # and the parent itself is indexed as its own row, under its own source
    assert s.reverse_index.pointer_id_of(parent) is not None
    assert pointer_identity(parent)[0] == "registry"


# ---------------------------------------------------------------------------------------------
# 4. The cascade
# ---------------------------------------------------------------------------------------------
def test_drift_material_invalidates_the_version_and_never_deletes_it():
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    clock["t"] = _LATER

    report = s.reverse_index.cascade("drift_material", source_id="registry")
    assert report.nodes_closed == 1
    assert s.open_node_row("test:a") is None
    # invalidate, never delete: the old belief is still reachable through as_of()
    from databridge_graph.query import as_of
    when = _LEARN + dt.timedelta(days=1)
    assert any(n.id == "test:a" for n in as_of(s, when, when).nodes)
    assert s.fetchone("SELECT COUNT(*) c FROM nodes WHERE id = 'test:a'")["c"] == 1


def test_an_attribute_pointer_invalidates_only_that_attribute():
    """Attribute-precise: the value goes to `unresolved`, NOT to `withheld`. It is gone from the
    world, not hidden from this reader (spine D4)."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", attrs={"dob": "1990-01-01", "town": "Riverbank"},
                     attr_sources={"dob": _cell(), "town": _cell(column="town")}))
    clock["t"] = _LATER

    report = s.reverse_index.cascade("drift_material", source_id="registry",
                                     locator_keys=[locator_key(_cell())])
    assert report.attributes_superseded == 1
    assert report.nodes_closed == 0
    from databridge_graph import row_to_node
    live = row_to_node(s, s.open_node_row("test:a"))
    assert live.attrs["dob"] is None and "dob" in live.unresolved
    assert live.attrs["town"] == "Riverbank"          # untouched
    assert "dob" not in live.attr_sources             # the citation went with the value


def test_a_closed_version_is_not_reported_as_live_and_is_not_re_closed():
    """`version_pk` is in the schema for exactly this: invalidating a superseded value must not
    close a live one, and a version already closed needs no action."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    clock["t"] = _LATER
    s.close_node("test:a", at=_LATER)

    report = s.reverse_index.impact_of(source_id="registry")
    assert report.nodes and all(not n.is_open for n in report.nodes)
    assert report.open_versions == ()
    cascade = s.reverse_index.cascade("drift_material", source_id="registry")
    assert cascade.nodes_closed == 0


def test_an_edge_is_closed_by_the_cascade():
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a"))
    s.add_node(_node("test:b"))
    s.add_edge(GraphEdge(id="e1", src_id="test:a", dst_id="test:b", kind=EdgeKind.REFERENCES,
                         sources=[_cell()], valid_from=_T0, system_from=_LEARN))
    clock["t"] = _LATER
    report = s.reverse_index.cascade("vanished", source_id="registry")
    assert report.edges_closed == 1
    assert [e.entity_id for e in report.impact.edges] == ["e1"]
    assert s.fetchone("SELECT system_to FROM edges WHERE id = 'e1'")["system_to"] is not None


def test_an_emission_is_marked_stale_never_rewritten():
    """*Which answers did we give while we believed the wrong thing?* — the leg every pointer
    system omits. A materialised dataset that silently changes under a model trained on it is
    worse than a stale one that says so."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    index = s.reverse_index
    index.record_emission("materialisation", "plan-abc", [_cell()], plan_hash="abc")
    clock["t"] = _LATER

    report = index.cascade("drift_material", source_id="registry")
    assert report.emissions_marked_stale == 1
    stale = report.impact.emissions[0]
    assert stale.ref == "plan-abc" and stale.plan_hash == "abc"
    assert stale.stale_cause == "drift_material" and stale.stale_since is not None
    # the row is still there — marked, not deleted, not regenerated
    assert s.fetchone("SELECT COUNT(*) c FROM emission")["c"] == 1


def test_a_ledger_bound_record_is_returned_but_never_marked_stale():
    """A ledger entry records what was believed and confirmed at a past instant, and that stays
    true. It is returned — a human needs to know which determinations rested on this — and never
    rewritten; the correction is a new entry citing the old one."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    index = s.reverse_index
    index.record_emission("ledger_entry", "seq-7", [_cell()], ledger_bound=True)
    index.record_emission("dataset", "plan-abc", [_cell()])
    clock["t"] = _LATER

    report = index.cascade("drift_material", source_id="registry")
    assert report.ledger_entries_untouched == 1
    assert report.emissions_marked_stale == 1
    ledger = s.fetchone("SELECT stale_since, stale_cause FROM emission WHERE ref = 'seq-7'")
    assert ledger["stale_since"] is None and ledger["stale_cause"] is None
    assert [e.ref for e in report.impact.ledger_bound] == ["seq-7"]


def test_flipping_the_ledger_flag_after_the_fact_is_refused():
    s = _store()
    s.add_node(_node("test:a", sources=[_cell()]))
    index = s.reverse_index
    index.record_emission("ledger_entry", "seq-7", [_cell()], ledger_bound=True)
    # Review finding 7 (packages/graph/tests/test_ptr_index_review_findings.py) tightened this
    # further: ANY second call against an existing ledger-bound (kind, ref) is refused now, not
    # just one that disagrees on the flag — so the message names "immutable"/"ledger-bound"
    # rather than the bare `ledger_bound=` flip it used to describe.
    with pytest.raises(ValueError, match="immutable"):
        index.record_emission("ledger_entry", "seq-7", [_cell()], ledger_bound=False)


def test_a_non_invalidating_verdict_changes_nothing():
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    s.reverse_index.record_emission("dataset", "plan-abc", [_cell()])
    clock["t"] = _LATER
    report = s.reverse_index.cascade("drift_benign", source_id="registry")
    assert (report.nodes_closed, report.emissions_marked_stale) == (0, 0)
    assert s.open_node_row("test:a") is not None
    assert report.impact.nodes                       # it still ANSWERS the question


def test_an_unknown_verdict_is_refused_rather_than_assumed_benign():
    """A typo that silently means "nothing to do" is the exact failure this module is about."""
    s = _store()
    with pytest.raises(ValueError, match="unknown verdict"):
        s.reverse_index.cascade("drift_maybe", source_id="registry")
    assert normalise_verdict("DRIFT_MATERIAL") == "drift_material"


def _adapter_verdicts():
    """The adapter package's verdict enum, or a skip.

    `packages/adapter` is in the root pytest `testpaths` but not yet in its `pythonpath`, so a
    plain import fails when the graph suite runs on its own. Adding the path here keeps the
    cross-check live today rather than skipping quietly, which for a test whose whole job is to
    stop two definitions drifting apart would be the same as not having it.
    """
    try:
        import databridge_adapter.verdicts as verdicts
    except ImportError:
        import sys
        root = Path(__file__).resolve().parents[3] / "packages" / "adapter"
        if root.is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            import databridge_adapter.verdicts as verdicts
        except ImportError:
            pytest.skip("databridge_adapter is not importable in this environment")
    return verdicts


def test_the_verdict_strings_are_the_adapter_lattices_own_values():
    """The graph holds the verdict names as strings rather than importing the adapter package —
    the state plane must not depend on the connection plane. This test is what stops the two
    drifting apart silently."""
    verdicts = _adapter_verdicts()
    assert KNOWN_VERDICTS == {v.value for v in verdicts.Verdict}
    assert INVALIDATING_VERDICTS == {
        verdicts.Verdict.DRIFT_MATERIAL.value, verdicts.Verdict.VANISHED.value,
        verdicts.Verdict.EXPIRED.value, verdicts.Verdict.FORBIDDEN.value}
    assert normalise_verdict(verdicts.Verdict.DRIFT_MATERIAL) == "drift_material"


def test_the_cascade_needs_a_subject():
    s = _store()
    with pytest.raises(ValueError, match="needs one of"):
        s.reverse_index.impact_of()


def test_the_report_carries_enough_to_act_on_not_just_ids():
    s = _store()
    s.add_node(_node("test:a", label="Riverbank Depot",
                     attrs={"dob": "1990-01-01"}, attr_sources={"dob": _cell()}))
    entity = s.reverse_index.impact_of(source_id="registry").nodes[0]
    assert entity.label == "Riverbank Depot"
    assert entity.kind == str(NodeKind.TEST)
    assert (entity.field, entity.value, entity.is_open) == ("dob", "1990-01-01", True)
    assert entity.source_id == "registry" and entity.locator_key


# ---------------------------------------------------------------------------------------------
# 5. Backfill
# ---------------------------------------------------------------------------------------------
def _pre_index_store(path: str, rows: int = 40) -> None:
    """A graph written the way one was before the index existed: `sources` rows, no bindings."""
    s = _store(path)
    for i in range(rows):
        s.add_node(_node(f"test:{i}", attrs={"dob": f"199{i % 10}-01-01"},
                         attr_sources={"dob": _cell(row_id=f"R{i}")}))
    s.conn.execute("DELETE FROM ptr_binding")
    s.conn.execute("DELETE FROM ptr_index")
    s.conn.execute("DELETE FROM ptr_parent")
    s.conn.commit()
    s.close()


def test_backfill_closes_the_coverage_gap_and_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "legacy.db")
        _pre_index_store(path, rows=40)
        s = _store(path)
        index = s.reverse_index
        assert index.coverage()[0] is Answer.UNKNOWN
        assert index.unindexed_pointer_count() == 80        # 40 entity + 40 attribute pointers

        first = index.backfill(batch_size=7)
        assert first.rows_scanned == 80
        assert first.bindings_written == 80
        assert first.pointers_indexed == 41                 # 1 shared doc_span + 40 distinct cells
        assert first.unindexed_remaining == 0
        assert index.coverage()[0] is Answer.KNOWN

        second = index.backfill()
        assert (second.rows_scanned, second.pointers_indexed) == (0, 0)
        assert index.coverage()[0] is Answer.KNOWN

        report = index.impact_of(pointer=_cell(row_id="R7"))
        assert report.is_complete and [n.entity_id for n in report.nodes] == ["test:7"]
        s.close()


def test_backfill_binds_a_pointer_to_the_right_table():
    """A node rowid and an edge rowid are drawn from independent sequences, so the same integer
    is a valid row in both tables. Guessing the table from the rowid puts a node's pointer under
    an unrelated edge."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "legacy.db")
        s = _store(path)
        s.add_node(_node("test:a"))
        s.add_node(_node("test:b"))
        s.add_edge(GraphEdge(id="e1", src_id="test:a", dst_id="test:b",
                             kind=EdgeKind.REFERENCES, sources=[_cell()],
                             valid_from=_T0, system_from=_LEARN))
        s.conn.executescript("DELETE FROM ptr_binding; DELETE FROM ptr_index;")
        s.conn.commit()
        s.reverse_index.backfill()
        report = s.reverse_index.impact_of(source_id="registry")
        assert [e.entity_id for e in report.edges] == ["e1"]
        assert report.nodes == ()
        s.close()


def test_backfill_records_an_orphan_pointer_rather_than_leaving_a_hole():
    """A `sources` row whose entity is in neither table is a real finding. Dropping it would
    leave the anti-join permanently non-zero and every answer permanently `UNKNOWN`."""
    s = _store()
    s.conn.execute(
        "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) VALUES (?,?,?,?)",
        ("test:ghost", None, None, _cell().model_dump_json()))
    s.conn.commit()
    report = s.reverse_index.backfill()
    assert report.unresolved_entities == 1
    assert s.reverse_index.coverage()[0] is Answer.KNOWN
    impact = s.reverse_index.impact_of(source_id="registry")
    assert impact.is_complete and impact.nodes == () and impact.edges == ()
    # reported in its own bucket: a citation for something the graph does not hold is a finding
    # about the store, and a cascade must not act on it as though it were an edge
    assert [o.entity_id for o in impact.orphans] == ["test:ghost"]
    assert s.reverse_index.cascade("drift_material", source_id="registry").nodes_closed == 0


def test_backfill_rescues_a_store_written_before_the_index_existed():
    """The literal retrofit case: a database with the OLD schema — no index tables at all."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "ancient.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE nodes (rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,"
            " kind TEXT NOT NULL, label TEXT NOT NULL, canonical_id TEXT,"
            " attrs_json TEXT NOT NULL DEFAULT '{}', valid_from TEXT NOT NULL, valid_to TEXT,"
            " system_from TEXT NOT NULL, system_to TEXT);"
            "CREATE TABLE edges (rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,"
            " src_id TEXT NOT NULL, dst_id TEXT NOT NULL, kind TEXT NOT NULL,"
            " weight REAL NOT NULL DEFAULT 1.0, attrs_json TEXT NOT NULL DEFAULT '{}',"
            " valid_from TEXT NOT NULL, valid_to TEXT, system_from TEXT NOT NULL,"
            " system_to TEXT);"
            "CREATE TABLE sources (entity_id TEXT NOT NULL, source_pointer_json TEXT NOT NULL);")
        conn.execute("INSERT INTO nodes(id, kind, label, attrs_json, valid_from, system_from)"
                     " VALUES ('test:a', 'test', 'A', '{}', '2024-01-01T00:00:00.000000Z',"
                     " '2024-06-01T00:00:00.000000Z')")
        conn.execute("INSERT INTO sources VALUES ('test:a', ?)", (_cell().model_dump_json(),))
        conn.commit()
        conn.close()

        s = _store(path)
        assert s.reverse_index.coverage()[0] is Answer.UNKNOWN
        report = s.reverse_index.backfill()
        assert report.bindings_written == 1
        impact = s.reverse_index.impact_of(pointer=_cell())
        assert impact.is_complete and [n.entity_id for n in impact.nodes] == ["test:a"]
        s.close()


# ---------------------------------------------------------------------------------------------
# 6. Scale — the shapes that only appear on a real estate graph
# ---------------------------------------------------------------------------------------------
def test_a_closure_wider_than_sqlites_parameter_limit_still_answers():
    """SQLite caps host parameters per statement. A single source in the estate graph grounds
    tens of thousands of pointers, so an `IN (...)` over the whole closure is not a theoretical
    limit — it is the first query the cascade runs on real data."""
    from databridge_graph.ptr_index import _PARAM_CHUNK

    s = _store()
    width = _PARAM_CHUNK * 2 + 5
    for i in range(width):
        s.add_node(_node(f"test:{i}", sources=[_cell(row_id=f"R{i}")]))
    report = s.reverse_index.impact_of(source_id="registry")
    assert len(report.pointer_ids) == width
    assert len(report.nodes) == width
    assert report.is_complete


def test_a_truncated_report_still_states_its_true_size():
    """`max_detail` bounds how much a report materialises, never what it claims. Showing fewer
    affected records than there are is the same failure as answering "none" for "unknown"."""
    s = _store()
    for i in range(50):
        s.add_node(_node(f"test:{i}", sources=[_cell(row_id=f"R{i}")]))
    report = s.reverse_index.impact_of(source_id="registry", max_detail=10)
    assert report.truncated is True
    assert len(report.nodes) == 10
    assert report.binding_count == 50            # the exact number, not the number shown
    assert "50 binding(s) in total" in report.summary()

    full = s.reverse_index.impact_of(source_id="registry")
    assert (full.truncated, full.binding_count, len(full.nodes)) == (False, 50, 50)


def test_an_emission_citing_pointers_across_chunk_boundaries_keeps_them_all():
    """One emission can cite pointers from several chunks. Replacing rather than merging would
    make the reported citation set depend on where the chunk boundary happened to fall."""
    from databridge_graph.ptr_index import _PARAM_CHUNK

    s = _store()
    cells = [_cell(row_id=f"R{i}") for i in range(_PARAM_CHUNK + 20)]
    for i, cell in enumerate(cells):
        s.add_node(_node(f"test:{i}", sources=[cell]))
    s.reverse_index.record_emission("dataset", "plan-wide", cells)
    report = s.reverse_index.impact_of(source_id="registry")
    assert len(report.emissions) == 1
    assert len(report.emissions[0].pointer_ids) == len(cells)


def test_a_cascade_over_partial_coverage_reports_unknown_not_handled():
    """It still acts — leaving a known-bad value live because some OTHER pointer is unindexed
    would be worse. What it must never do is read as "handled"."""
    clock = {"t": _LEARN}
    s = _store(clock=lambda: clock["t"])
    s.add_node(_node("test:a", sources=[_cell()]))
    s.conn.execute(
        "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) VALUES (?,?,?,?)",
        ("test:a", None, None, _cell(src="bypassed").model_dump_json()))
    s.conn.commit()
    clock["t"] = _LATER

    report = s.reverse_index.cascade("drift_material", source_id="registry")
    assert report.nodes_closed == 1                  # it acted on what it knew
    assert report.answer is Answer.UNKNOWN           # and did not claim to be finished
    assert report.impact.unindexed_pointers == 1
