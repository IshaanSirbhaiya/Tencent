"""SQLite-backed bi-temporal graph store (T08, 04_PRD §3; ADR-0003).

Schema (one row per bi-temporal VERSION; nodes/edges share an ``id`` across versions):

    nodes(id, kind, label, canonical_id, attrs_json,
          valid_from, valid_to, system_from, system_to)
    edges(id, src_id, dst_id, kind, weight, attrs_json,
          valid_from, valid_to, system_from, system_to)
    sources(entity_id, source_pointer_json)

`*_to IS NULL` = the version is OPEN on that axis. Conflicting facts INVALIDATE (close the open
`*_to`) rather than delete (ADR-0003) — see ``merge.py`` for the upsert that drives this.

Timestamps are stored as ISO-8601 UTC strings: lexicographic order == chronological order, so
the bi-temporal interval predicates are plain string comparisons in SQL.

The store is the choke point for the **anti-fabrication wall**: ``add_node``/``add_edge`` take
``GraphNode``/``GraphEdge`` whose validators already refused any ungrounded value, and they
additionally re-assert ``sources`` is non-empty before any write (defence in depth, P4).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from databridge_core import SourcePointer

from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind

# Open-interval sentinel for SQL: a row open on an axis stores NULL; the "max" string below is
# only used for lexicographic max() in path/timeline ordering, never written.
_OPEN = None


def _iso(dt: datetime) -> str:
    """Canonical UTC ISO string, FIXED-WIDTH so lexicographic order == chronological order (the
    invariant the SQL ORDER BY / interval comparisons rely on). Always UTC, always 6-digit
    microseconds, trailing 'Z' — so a zero-microsecond time can't sort before a fractional one of
    the same instant (the `+00:00`-suffix hazard)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _parse(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None      # Python 3.11+ parses the trailing 'Z'


_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    rowid_pk    INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL,
    canonical_id TEXT,
    attrs_json  TEXT NOT NULL DEFAULT '{}',
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,
    system_from TEXT NOT NULL,
    system_to   TEXT
);
CREATE TABLE IF NOT EXISTS edges (
    rowid_pk    INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL,
    src_id      TEXT NOT NULL,
    dst_id      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    attrs_json  TEXT NOT NULL DEFAULT '{}',
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,
    system_from TEXT NOT NULL,
    system_to   TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    entity_id          TEXT NOT NULL,
    source_pointer_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_nodes_id ON nodes(id);
CREATE INDEX IF NOT EXISTS ix_nodes_canon ON nodes(canonical_id);
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS ix_sources_eid ON sources(entity_id);
"""


class GraphStore:
    """Bi-temporal graph over SQLite. In-memory by default (the demo seed loads in <1s)."""

    def __init__(self, path: str = ":memory:", *, clock: Optional[Callable[[], datetime]] = None):
        # check_same_thread=False: the gateway serves sync endpoints from a threadpool, so the
        # store (built in the lifespan thread) is read across threads. Writes are serialised by
        # ``_lock`` below; the demo is effectively sequential so this is safe (single-writer, §9).
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        # injectable clock → deterministic system_time in tests / reproducible demo seed.
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def now(self) -> datetime:
        return self._clock()

    # -- writes ------------------------------------------------------------------
    def add_node(self, node: GraphNode) -> None:
        if not node.sources:                              # defence-in-depth (P4)
            raise ValueError(f"node {node.id!r} REFUSED: no SourcePointer")
        with self._lock:
            self.conn.execute(
                "INSERT INTO nodes(id, kind, label, canonical_id, attrs_json, valid_from, valid_to,"
                " system_from, system_to) VALUES (?,?,?,?,?,?,?,?,?)",
                (node.id, node.kind.value, node.label, node.canonical_id,
                 json.dumps(node.attrs, sort_keys=True, default=str),
                 _iso(node.valid_from), _iso(node.valid_to) if node.valid_to else _OPEN,
                 _iso(node.system_from), _iso(node.system_to) if node.system_to else _OPEN))
            self._attach_sources(node.id, node.sources)
            self.conn.commit()

    def add_edge(self, edge: GraphEdge) -> None:
        if not edge.sources:
            raise ValueError(f"edge {edge.id!r} REFUSED: no SourcePointer")
        with self._lock:
            self.conn.execute(
                "INSERT INTO edges(id, src_id, dst_id, kind, weight, attrs_json, valid_from, valid_to,"
                " system_from, system_to) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (edge.id, edge.src_id, edge.dst_id, edge.kind.value, edge.weight,
                 json.dumps(edge.attrs, sort_keys=True, default=str),
                 _iso(edge.valid_from), _iso(edge.valid_to) if edge.valid_to else _OPEN,
                 _iso(edge.system_from), _iso(edge.system_to) if edge.system_to else _OPEN))
            self._attach_sources(edge.id, edge.sources)
            self.conn.commit()

    def _attach_sources(self, entity_id: str, sources: list[SourcePointer]) -> None:
        # caller holds _lock (add_node/add_edge); for the corroboration path use append_sources.
        self.conn.executemany(
            "INSERT INTO sources(entity_id, source_pointer_json) VALUES (?, ?)",
            [(entity_id, sp.model_dump_json()) for sp in sources])

    def append_sources(self, entity_id: str, sources: list[SourcePointer]) -> None:
        """Corroboration: add new SourcePointers to an existing entity (locked + committed)."""
        if not sources:
            return
        with self._lock:
            self._attach_sources(entity_id, sources)
            self.conn.commit()

    def close_node(self, entity_id: str, *, at: datetime, valid_to: Optional[datetime] = None) -> int:
        """Invalidate the OPEN version of ``entity_id``: set ``system_to = at`` (we stopped
        believing it) and ``valid_to`` (it stopped being true; defaults to ``at``). ADR-0003."""
        return self._close("nodes", entity_id, at=at, valid_to=valid_to)

    def close_edge(self, entity_id: str, *, at: datetime, valid_to: Optional[datetime] = None) -> int:
        return self._close("edges", entity_id, at=at, valid_to=valid_to)

    def _close(self, table: str, entity_id: str, *, at: datetime, valid_to: Optional[datetime]) -> int:
        assert table in ("nodes", "edges")            # table is a literal; never attacker-controlled
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE {table} SET system_to = ?, valid_to = COALESCE(valid_to, ?) "
                f"WHERE id = ? AND system_to IS NULL",
                (_iso(at), _iso(valid_to or at), entity_id))
            self.conn.commit()
            return cur.rowcount

    def replace_node(self, node: GraphNode, *, at: datetime, valid_to: datetime) -> int:
        """ATOMIC supersession (ADR-0003): close the open version of ``node.id`` AND insert the new
        version in ONE transaction under the lock, so a concurrent reader never sees the entity with
        zero open versions (the torn 'no current belief' window). Returns rows closed."""
        if not node.sources:
            raise ValueError(f"node {node.id!r} REFUSED: no SourcePointer")
        with self._lock:
            cur = self.conn.execute(
                "UPDATE nodes SET system_to = ?, valid_to = COALESCE(valid_to, ?) "
                "WHERE id = ? AND system_to IS NULL", (_iso(at), _iso(valid_to), node.id))
            closed = cur.rowcount
            self.conn.execute(
                "INSERT INTO nodes(id, kind, label, canonical_id, attrs_json, valid_from, valid_to,"
                " system_from, system_to) VALUES (?,?,?,?,?,?,?,?,?)",
                (node.id, node.kind.value, node.label, node.canonical_id,
                 json.dumps(node.attrs, sort_keys=True, default=str),
                 _iso(node.valid_from), _iso(node.valid_to) if node.valid_to else _OPEN,
                 _iso(node.system_from), _iso(node.system_to) if node.system_to else _OPEN))
            self._attach_sources(node.id, node.sources)
            self.conn.commit()
            return closed

    # -- reads -------------------------------------------------------------------
    # ALL reads go through these locked helpers. With check_same_thread=False the gateway serves
    # requests from a threadpool that share ONE connection; concurrent execute()s on a shared
    # sqlite connection interleave on the cursor and can return wrong/empty rows (e.g. sources_for
    # returns [] mid-flight → a sourceless GraphNode crash). The RLock serialises every statement;
    # it is reentrant, so a read that calls another read on the same thread (as_of → sources_for)
    # is safe. (Predicted by the T08 review; surfaced under the brain page's concurrent fetches.)
    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def open_node_row(self, entity_id: str) -> Optional[sqlite3.Row]:
        """The currently-believed (system_to IS NULL) version of a node id, if any."""
        return self.fetchone(
            "SELECT * FROM nodes WHERE id = ? AND system_to IS NULL ORDER BY rowid_pk DESC LIMIT 1",
            (entity_id,))

    def open_nodes(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM nodes WHERE system_to IS NULL ORDER BY rowid_pk")

    def open_edges(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM edges WHERE system_to IS NULL ORDER BY rowid_pk")

    def find_by_canonical(self, kind: NodeKind, canonical_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM nodes WHERE kind = ? AND canonical_id = ? AND system_to IS NULL "
            "ORDER BY rowid_pk DESC LIMIT 1", (kind.value, canonical_id))

    def open_nodes_of_kind(self, kind: NodeKind) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM nodes WHERE kind = ? AND system_to IS NULL ORDER BY rowid_pk",
            (kind.value,))

    def sources_for(self, entity_id: str) -> list[SourcePointer]:
        rows = self.fetchall(
            "SELECT source_pointer_json FROM sources WHERE entity_id = ?", (entity_id,))
        return [SourcePointer.model_validate_json(r["source_pointer_json"]) for r in rows]

    def all_node_versions(self, entity_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM nodes WHERE id = ? ORDER BY system_from, rowid_pk", (entity_id,))

    def edges_touching(self, entity_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM edges WHERE src_id = ? OR dst_id = ? ORDER BY system_from, rowid_pk",
            (entity_id, entity_id))

    # -- counts (eval / demo stats) ---------------------------------------------
    def counts(self) -> dict[str, int]:
        n = self.fetchone("SELECT COUNT(*) c FROM nodes WHERE system_to IS NULL")["c"]
        e = self.fetchone("SELECT COUNT(*) c FROM edges WHERE system_to IS NULL")["c"]
        s = self.fetchone("SELECT COUNT(*) c FROM sources")["c"]
        return {"nodes": n, "edges": e, "sources": s}

    def close(self) -> None:
        self.conn.close()


# --- row → typed helpers (used by query.py / serialisation) -----------------------
def row_to_node(store: GraphStore, row: sqlite3.Row) -> GraphNode:
    return GraphNode(
        id=row["id"], kind=NodeKind(row["kind"]), label=row["label"],
        canonical_id=row["canonical_id"], attrs=json.loads(row["attrs_json"]),
        sources=store.sources_for(row["id"]),
        valid_from=_parse(row["valid_from"]), valid_to=_parse(row["valid_to"]),
        system_from=_parse(row["system_from"]), system_to=_parse(row["system_to"]))


def row_to_edge(store: GraphStore, row: sqlite3.Row) -> GraphEdge:
    return GraphEdge(
        id=row["id"], src_id=row["src_id"], dst_id=row["dst_id"], kind=EdgeKind(row["kind"]),
        weight=row["weight"], attrs=json.loads(row["attrs_json"]),
        sources=store.sources_for(row["id"]),
        valid_from=_parse(row["valid_from"]), valid_to=_parse(row["valid_to"]),
        system_from=_parse(row["system_from"]), system_to=_parse(row["system_to"]))
