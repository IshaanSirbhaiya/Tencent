"""One file-backed store, many processes (contract C1).

The gateway holds one ``GraphStore`` open for its whole life while the ingest CLI and the MCP
writer commit rows to the same file from their own processes. These tests pin that a reader sees
another connection's commits WITHOUT a restart — i.e. that ``GraphStore`` caches nothing — and
that ``refresh()`` follows the one thing a live connection cannot: the file being replaced.
Two connections in one process stand in for two processes; SQLite does not distinguish them.
"""
from __future__ import annotations

import datetime as dt
import os

from databridge_core import SourcePointer
from databridge_graph import GraphNode, upsert_node
from databridge_graph.packs import active_vocabulary
from databridge_graph.schema import NodeKind, node_id
from databridge_graph.store import GraphStore

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _node(label: str) -> GraphNode:
    kind = NodeKind(sorted(active_vocabulary().node_kinds)[0])
    ptr = SourcePointer(source_id="notes.txt", kind="doc_span", document_id="notes.txt",
                        char_start=0, char_end=len(label), trust=None)
    return GraphNode(id=node_id(kind, label.lower()), kind=kind, label=label, sources=[ptr],
                     valid_from=T0, system_from=T0)


def test_a_reader_sees_another_connections_commits_without_a_restart(tmp_path):
    path = str(tmp_path / "workspace.db")
    reader = GraphStore(path)                 # the gateway: opened once, kept open
    writer = GraphStore(path)                 # the ingest CLI / MCP writer
    try:
        assert reader.counts()["nodes"] == 0
        labels = ["Harbour Lantern Works", "Quillon Ferris", "Ostrander Mill"]
        for i, label in enumerate(labels, start=1):
            upsert_node(writer, _node(label))
            # visible on the very next read, no refresh, no reopen
            assert reader.counts()["nodes"] == i
        assert {r["label"] for r in reader.open_nodes()} == set(labels)
    finally:
        reader.close()
        writer.close()


def test_refresh_follows_a_replaced_file(tmp_path):
    path = tmp_path / "workspace.db"
    staged = tmp_path / "staged.db"
    live = GraphStore(str(path))
    builder = GraphStore(str(staged))
    try:
        upsert_node(live, _node("Harbour Lantern Works"))
        for label in ("Quillon Ferris", "Ostrander Mill"):
            upsert_node(builder, _node(label))
        expected = builder.counts()
        builder.close()
        # an atomic publish swaps the file under the open connection. On Windows the live handle
        # pins the file, so it is released first — `refresh()` is what reconnects afterwards.
        live.conn.close()
        os.replace(staged, path)
        counts = live.refresh()
        assert counts == expected and counts["nodes"] == 2
        assert {r["label"] for r in live.open_nodes()} == {"Quillon Ferris", "Ostrander Mill"}
    finally:
        live.close()


def test_refresh_on_an_in_memory_store_is_a_harmless_reread():
    store = GraphStore()
    upsert_node(store, _node("Harbour Lantern Works"))
    assert store.refresh() == store.counts() and store.counts()["nodes"] == 1
