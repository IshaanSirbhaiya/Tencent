"""R-029 TIDY2 item 6 (TABX CROSS-TRACK 3a): the per-row lookups of a table build are SELECTIVE
without statistics.

`find_by_canonical` asks for the open node of one kind with one canonical id, and the declared-key
twin lookup for the open node of one kind with one exact label. With only `ix_nodes_canon` /
`ix_nodes_kind_open`, the planner (no ANALYZE has run on a fresh store) may pick the kind index and
walk every open node of that kind. Two partial composite indexes make both lookups one SEARCH on
both columns. Asserted on the planner's own plan, for the SQL the store and the build issue.
"""
from __future__ import annotations

import pytest

from databridge_graph.store import GraphStore

KIND_CANON = ("SELECT * FROM nodes WHERE kind = ? AND canonical_id = ? AND system_to IS NULL "
              "ORDER BY rowid_pk DESC LIMIT 1")
KIND_LABEL = ("SELECT id FROM nodes WHERE kind = ? AND system_to IS NULL AND valid_to IS NULL "
              "AND canonical_id IS NULL AND label = ?")


def _plan(store: GraphStore, sql: str) -> str:
    return " | ".join(str(r["detail"]) for r in store.fetchall("EXPLAIN QUERY PLAN " + sql,
                                                                ("k", "v")))


@pytest.fixture
def store(tmp_path):
    return GraphStore(str(tmp_path / "g.db"))


def test_the_composite_indexes_exist_and_are_partial(store):
    rows = {r["name"]: r["sql"] for r in store.fetchall(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'nodes'")}
    for name, cols in (("ix_nodes_kind_canon_open", "kind, canonical_id"),
                       ("ix_nodes_kind_label_open", "kind, label")):
        assert name in rows, sorted(rows)
        assert cols in rows[name] and "system_to IS NULL" in rows[name]


@pytest.mark.parametrize("sql,index,cols", [
    (KIND_CANON, "ix_nodes_kind_canon_open", ("kind=?", "canonical_id=?")),
    (KIND_LABEL, "ix_nodes_kind_label_open", ("kind=?", "label=?")),
])
def test_the_lookup_is_one_search_on_both_columns(store, sql, index, cols):
    plan = _plan(store, sql)
    assert f"USING INDEX {index}" in plan, plan
    assert all(c in plan.replace(" ", "") for c in cols), plan
    # the store's own lookup issues exactly this SQL
    from databridge_graph import store as store_mod
    import inspect
    if index == "ix_nodes_kind_canon_open":
        src = inspect.getsource(store_mod.GraphStore.find_by_canonical)
        assert "kind = ? AND canonical_id = ? AND system_to IS NULL" in src
