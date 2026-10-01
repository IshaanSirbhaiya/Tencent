"""SQLite-backed bi-temporal graph store (T08, 04_PRD §3; ADR-0003).

Schema (one row per bi-temporal VERSION; nodes/edges share an ``id`` across versions):

    nodes(id, kind, label, canonical_id, attrs_json,
          valid_from, valid_to, system_from, system_to)
    edges(id, src_id, dst_id, kind, weight, attrs_json,
          valid_from, valid_to, system_from, system_to)
    sources(entity_id, field, version_pk, source_pointer_json)

plus the reverse pointer index (`ptr_index.py`, `SYSTEM_DESIGN` §5) — `ptr_index`, `ptr_parent`,
`ptr_binding`, `emission`, `emission_pointer` — which answers the question the forward pointers
cannot: *given a source that changed, what did we build on it?* Its tables live in this same
database and are written by `_attach_sources`, inside the same transaction as the graph write
they index. Nothing else in this module needs to know about it.

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
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

from databridge_core import SourcePointer

from .attestation import Attestation, AttestationError, enforce_attestation
from .ptr_index import PointerCache, ReverseIndex, bind_pointer, ensure_index_schema
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


def _advance_past(candidate: datetime, floor: datetime) -> datetime:
    """`candidate`, or one microsecond after `floor` if `candidate` would not sort strictly after
    it. A version closed at or before the instant it opened has the system interval `[S, S)` (or
    worse, `[S, s<S)`), which no `as_of()` predicate can ever select — the row becomes permanently
    unreachable, which is exactly what invalidate-never-delete exists to prevent (review finding
    5). `supersede_attr` already guarded this for its own close; `_close` (used by `close_node`/
    `close_edge`, and so by every entity-level step of `ReverseIndex.cascade`) did not."""
    return candidate if candidate > floor else floor + timedelta(microseconds=1)


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
    system_to   TEXT,
    -- The named human's Attestation, serialised. NULL for every edge that is NOT an identity
    -- DETERMINATION, which is all of them except those bound to an ATTESTED_META_KIND. It is a
    -- column rather than an `attrs` key because an edge read back out of here is reconstructed
    -- through `GraphEdge(...)`, whose validator REFUSES a determination without one — so an
    -- attestation that did not persist would make its own edge unreadable.
    attestation_json TEXT,
    -- The attestation's content address, denormalised out of the JSON above. It is what makes
    -- "is this human act already bound to another edge?" an indexed lookup instead of a scan,
    -- and that question is the replay check (attestation findings 1 and 5).
    attestation_id   TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    entity_id          TEXT NOT NULL,
    -- NULL  = this pointer grounds the ENTITY (that it exists, and was read from somewhere).
    -- set   = it grounds ONE named attribute and nothing else. The distinction is the whole
    --         point: a pooled list of entity pointers under a "Sources" heading reads as
    --         vouching for every value printed above it, including values it never saw.
    field              TEXT,
    -- The nodes/edges row (bi-temporal VERSION) this pointer was recorded for. Without it the
    -- table is keyed by entity id alone, so every pointer ever attached to an id pools together
    -- and version 2's values render beside version 1's citations — a pointer that resolves to a
    -- span which no longer says what the value says. NULL only for rows written before this
    -- column existed.
    version_pk         INTEGER,
    source_pointer_json TEXT NOT NULL
);
"""

# Indexes are created SEPARATELY, after `_migrate()`. Two of them name columns that a store
# written by an earlier version does not have, and `CREATE TABLE IF NOT EXISTS` will not add
# them — so running these inside `_SCHEMA` fails with "no such column" on exactly the legacy
# store the migration exists to rescue, before the migration has had a chance to run.
_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_nodes_id ON nodes(id);
CREATE INDEX IF NOT EXISTS ix_nodes_canon ON nodes(canonical_id);
-- `edges` had no index on `id`, so every lookup by edge id was a scan of the whole table.
-- Harmless at demo size and quadratic at estate size: the reverse index resolves a pointer's
-- entity by id, so a backfill over 382k pointers did 52k-row scans in a loop.
CREATE INDEX IF NOT EXISTS ix_edges_id ON edges(id);
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS ix_edges_attestation ON edges(attestation_id);
CREATE INDEX IF NOT EXISTS ix_sources_eid ON sources(entity_id);
CREATE INDEX IF NOT EXISTS ix_sources_field ON sources(entity_id, field);
CREATE INDEX IF NOT EXISTS ix_sources_version ON sources(entity_id, version_pk);
-- `upsert_edge` (merge.py) needs "the OPEN edge on this exact (src_id, dst_id, kind) triple, if
-- any" on EVERY edge it inserts. Before this index the only way to answer that was
-- `store.open_edges()` — every open edge, materialised, then filtered in Python — which made a
-- 52,747-edge estate load O(n^2): ~1.4 BILLION row comparisons, edges never finishing in 35
-- minutes. PARTIAL (`WHERE system_to IS NULL`) so it indexes only what the lookup ever asks for
-- — a closed edge can never be the answer — and so the index stays exactly as large as the open
-- edge set rather than growing with every supersession.
CREATE INDEX IF NOT EXISTS ix_edges_open_triple ON edges(src_id, dst_id, kind)
    WHERE system_to IS NULL;
-- `merge._resolve_existing`'s fuzzy fallback (step 2) narrows to "open nodes of this kind" before
-- scoring. There was no index on `kind` at all, so that narrowing was itself a full table scan.
-- Same partial-index reasoning as above.
CREATE INDEX IF NOT EXISTS ix_nodes_kind_open ON nodes(kind) WHERE system_to IS NULL;
-- `extractor._subject_candidates` asks "which open subject mentions carry this grounded
-- `extracted_identifier`?" (review R-019 t06 M1). An expression index over exactly the
-- expression `open_nodes_with_identifier` queries, so that lookup is a SEARCH, not a scan.
-- Guarded by `json_valid`: a bare `json_extract` in an index makes every write of a malformed
-- `attrs_json` FAIL, and the store deliberately tolerates (and reports) corrupt rows.
CREATE INDEX IF NOT EXISTS ix_nodes_extracted_identifier
    ON nodes(kind, (CASE WHEN json_valid(attrs_json) THEN json_extract(attrs_json, '$.extracted_identifier') END)) WHERE system_to IS NULL;
"""


class GraphStore:
    """Bi-temporal graph over SQLite. In-memory by default (the demo seed loads in <1s)."""

    def __init__(self, path: str = ":memory:", *, clock: Optional[Callable[[], datetime]] = None):
        # check_same_thread=False: the gateway serves sync endpoints from a threadpool, so the
        # store (built in the lifespan thread) is read across threads. Writes are serialised by
        # ``_lock`` below; the demo is effectively sequential so this is safe (single-writer, §9).
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.executescript(_INDEXES)
        # The reverse pointer index (`ptr_index.py`, SYSTEM_DESIGN §5). Its tables live in THIS
        # database, so it survives a restart with the graph and cannot be separated from it. It is
        # maintained on write, inside the same transaction as the write it indexes — see
        # `_attach_sources`. A store opened from a file written before the index existed gets the
        # tables but no rows, and `ReverseIndex.coverage()` says so rather than pretending.
        ensure_index_schema(self.conn)
        self.conn.commit()
        # injectable clock → deterministic system_time in tests / reproducible demo seed.
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _migrate(self) -> None:
        """Additive migrations for a graph created by an earlier version.

        `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, so a new column has to
        be added explicitly or every write against an on-disk store fails. Every pre-existing row
        gets `field = NULL`, which is exactly right: those pointers were attached to the entity.
        """
        edge_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(edges)")}
        if edge_cols and "attestation_json" not in edge_cols:
            # A store written before the attestation wall existed. Every pre-existing edge gets
            # NULL, which is right: none of them recorded a determination, because there was no
            # determination edge kind that could be written at the time.
            self.conn.execute("ALTER TABLE edges ADD COLUMN attestation_json TEXT")
            self.conn.commit()
        if edge_cols and "attestation_id" not in edge_cols:
            # The content address of the attestation, denormalised out of the JSON so that replay
            # ("is this human act already bound to another edge?") is one indexed lookup rather
            # than a scan of every determination in the graph.
            self.conn.execute("ALTER TABLE edges ADD COLUMN attestation_id TEXT")
            self.conn.commit()
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(sources)")}
        if not have:
            return
        if "field" not in have:
            self.conn.execute("ALTER TABLE sources ADD COLUMN field TEXT")
        if "version_pk" not in have:
            self.conn.execute("ALTER TABLE sources ADD COLUMN version_pk INTEGER")
        # A store written before per-attribute pointers existed holds `attrs_json` values with
        # only ENTITY-level citations. `row_to_node`/`row_to_edge` construct a `GraphNode`/
        # `GraphEdge` around every row they read, and the grounding wall inside those validators
        # refuses any non-None attribute with no per-field pointer — so simply reading such a row
        # back, open OR long since superseded, would raise.
        #
        # Review finding 3 (CRITICAL): this used to be "fixed" by rewriting `attrs_json` in place
        # for EVERY row, including closed, historical ones — nulling out the uncited values in
        # storage. That is a delete wearing an upgrade's clothes: a superseded version is supposed
        # to answer "what did we believe on date X" forever (ADR-0003, invalidate-never-delete),
        # and a value the migration blanked cannot answer that anymore. There is no such thing as
        # "invalidating" a system-closed row's attributes after the fact — closing is what makes a
        # row historical, and this ran on rows that already were.
        #
        # The fix moves the same rule ("a value with no pointer is not a value; it reads as None")
        # from a persisted, destructive rewrite to a transient one applied at construction time —
        # see `_grounded_attrs_only`, used by `row_to_node`/`row_to_edge`. The raw `attrs_json`
        # bytes are never touched by this migration at all now: reading them directly (as
        # `ReverseIndex._entity_detail` does) still shows exactly what was recorded, open or
        # closed, and the typed view still refuses to fabricate grounding it does not have.

    def now(self) -> datetime:
        return self._clock()

    # -- writes ------------------------------------------------------------------
    @staticmethod
    def _recheck_grounding(entity) -> None:
        """Re-run the per-attribute wall at the store boundary.

        `ConfigDict(frozen=True)` freezes the pydantic model's ATTRIBUTES, not the dictionaries
        they point at: `node.attrs["sex"] = "M"` and `del node.attr_sources["sex"]` both succeed
        on a "frozen" node that passed validation. A wall that only fires at construction is
        therefore not a wall at all for anything held across a few statements — and the store is
        the last place a value can be stopped before it is durable.
        """
        from .schema import _attrs_are_grounded

        _attrs_are_grounded(entity.attrs, entity.attr_sources,
                            f"{type(entity).__name__} {entity.id!r}",
                            identity_fields=entity.identity_values())

    def add_node(self, node: GraphNode) -> None:
        if not node.sources:                              # defence-in-depth (P4)
            raise ValueError(f"node {node.id!r} REFUSED: no SourcePointer")
        self._recheck_grounding(node)
        with self._lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO nodes(id, kind, label, canonical_id, attrs_json, valid_from,"
                    " valid_to, system_from, system_to) VALUES (?,?,?,?,?,?,?,?,?)",
                    (node.id, node.kind.value, node.label, node.canonical_id,
                     json.dumps(node.attrs, sort_keys=True, default=str),
                     _iso(node.valid_from), _iso(node.valid_to) if node.valid_to else _OPEN,
                     _iso(node.system_from), _iso(node.system_to) if node.system_to else _OPEN))
                self._attach_sources(node.id, node.sources, node.attr_sources, cur.lastrowid,
                                     entity_kind="node")
                self.conn.commit()
            except Exception:
                # The index write is part of THIS transaction (see `_attach_sources`). Letting a
                # failure half-way through leave an uncommitted node behind would be an index that
                # disagrees with the graph — the one thing maintaining it on write exists to
                # prevent — and the next unrelated commit would silently publish it.
                self.conn.rollback()
                raise

    def _screen_determination(self, edge: GraphEdge) -> Optional[str]:
        """The store-side half of the attestation wall. Returns the attestation id, or ``None``.

        **This is the real enforcement point, and saying so is the point.** The model validator
        fires at construction, and `model_construct()` / `model_copy(update=...)` skip it — a limit
        this codebase documents honestly for the grounding wall. For an identity determination that
        limit is not good enough: it is the one constraint the product refuses to compromise. So
        the store does not trust the object it is handed. It re-derives the meaning of the edge
        from its own fields, rebuilds the attestation through the validators
        (`attestation.revalidated`, called inside `enforce_attestation`), and adds the two checks
        only a store can make:

        * **referential integrity** (attestation finding 8). `subject_kind_of_node_id` reads a
          kind out of an id STRING, so ``"<a-subject-kind>:anything"`` passed the subject test
          without any such node existing. The store can look, so it looks: a determination must
          point at a node that is actually in the graph and whose stored kind agrees with its id.

        * **replay** (attestation findings 1 and 5). An attestation is a record of one decision,
          and its `attestation_id` is derived from that decision's content. Finding the same id
          already bound to a DIFFERENT edge means one human act is being used twice, which is the
          bearer-token failure the binding fields exist to prevent — belt and braces, because the
          binding check depends on the attestation being well-formed and this one does not.

        The clock is passed too, so `attested_at` is bounded by the store's time rather than
        unbounded (finding 5).
        """
        enforce_attestation(edge.kind, edge.dst_id, edge.attestation,
                            src_id=edge.src_id, edge_id=edge.id, now=self.now())
        if edge.attestation is None:
            return None

        prefix = edge.dst_id.split(":", 1)[0] if ":" in edge.dst_id else ""
        row = self.conn.execute(
            "SELECT kind FROM nodes WHERE id = ? AND system_to IS NULL LIMIT 1",
            (edge.dst_id,)).fetchone()
        if row is None:
            raise AttestationError(
                f"edge {edge.id!r} REFUSED: the determination points at {edge.dst_id!r}, which is "
                f"not an open node in this graph. A node id is a string; being shaped like a "
                f"subject is not being one, and a determination about a subject that does not "
                f"exist is a determination about nobody")
        if row["kind"] != prefix:
            raise AttestationError(
                f"edge {edge.id!r} REFUSED: {edge.dst_id!r} is stored with kind "
                f"{row['kind']!r}, which disagrees with the kind in its id ({prefix!r}). The "
                f"subject test reads the prefix, so a disagreement is a determination pointed at "
                f"a kind nobody declared it against")

        attestation_id = edge.attestation.attestation_id
        clash = self.conn.execute(
            "SELECT id FROM edges WHERE attestation_id = ? AND id <> ? LIMIT 1",
            (attestation_id, edge.id)).fetchone()
        if clash is not None:
            raise AttestationError(
                f"edge {edge.id!r} REFUSED: attestation {attestation_id} is already bound to edge "
                f"{clash['id']!r}. One human act authorises one determination; replaying it onto "
                f"a second edge is the bearer-token failure this record exists to prevent "
                f"(CLAUDE.md §Hard constraints 1)")
        return attestation_id

    def add_edge(self, edge: GraphEdge) -> None:
        if not edge.sources:
            raise ValueError(f"edge {edge.id!r} REFUSED: no SourcePointer")
        self._recheck_grounding(edge)
        with self._lock:
            attestation_id = self._screen_determination(edge)
            try:
                cur = self.conn.execute(
                    "INSERT INTO edges(id, src_id, dst_id, kind, weight, attrs_json, valid_from,"
                    " valid_to, system_from, system_to, attestation_json, attestation_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (edge.id, edge.src_id, edge.dst_id, edge.kind.value, edge.weight,
                     json.dumps(edge.attrs, sort_keys=True, default=str),
                     _iso(edge.valid_from), _iso(edge.valid_to) if edge.valid_to else _OPEN,
                     _iso(edge.system_from), _iso(edge.system_to) if edge.system_to else _OPEN,
                     edge.attestation.model_dump_json() if edge.attestation else None,
                     attestation_id))
                self._attach_sources(edge.id, edge.sources, edge.attr_sources, cur.lastrowid,
                                     entity_kind="edge")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _attach_sources(self, entity_id: str, sources: list[SourcePointer],
                        attr_sources: Optional[dict] = None,
                        version_pk: Optional[int] = None, *,
                        entity_kind: str = "unknown") -> None:
        """Write the pointers for one version, AND index them in the same transaction.

        The reverse index is maintained here rather than in a background job, and that is the
        whole design: an index that can lag is an index that is wrong exactly when it matters —
        the moment a source drifts and somebody asks what relied on it. Every path that writes to
        `sources` goes through this method, so hooking it is what makes the anti-join in
        `ReverseIndex.unindexed_pointer_count()` a coverage GUARANTEE rather than a hope.

        Rows are inserted one at a time rather than by `executemany` because the index binds to
        each `sources` row by its rowid, and `executemany` does not report them. `lastrowid` after
        a single-row insert is exact.
        """
        rows = [(entity_id, None, version_pk, sp) for sp in sources]
        rows += [(entity_id, field, version_pk, sp)
                 for field, sp in sorted((attr_sources or {}).items())]
        now = self._index_now(None)
        cache: PointerCache = {}
        for eid, field, vpk, pointer in rows:
            cur = self.conn.execute(
                "INSERT INTO sources(entity_id, field, version_pk, source_pointer_json) "
                "VALUES (?, ?, ?, ?)", (eid, field, vpk, pointer.model_dump_json()))
            bind_pointer(self.conn, int(cur.lastrowid), pointer, entity_kind=entity_kind,
                         entity_id=eid, version_pk=vpk, field=field, now=now, cache=cache)

    def _index_now(self, at: Optional[datetime]) -> str:
        """Canonical timestamp string for an index row. Uses the store's injectable clock, so an
        index built under a fixed clock in a test is as deterministic as the graph beside it."""
        return _iso(at if at is not None else self.now())

    @property
    def reverse_index(self) -> ReverseIndex:
        """`source_id -> pointers -> nodes/edges -> emitted records` (SYSTEM_DESIGN §5)."""
        return ReverseIndex(self)

    def append_sources(self, entity_id: str, sources: list[SourcePointer]) -> None:
        """Corroboration: add new SourcePointers to an existing entity (locked + committed).

        The pointers attach to the version that is OPEN now, because that is the version whose
        values they were just found to support. Attaching them to the entity at large would put
        them in front of superseded values they were never checked against.
        """
        if not sources:
            return
        with self._lock:
            entity_kind, version_pk = self._open_version(entity_id)
            if version_pk is None:
                # REFUSE rather than write `version_pk = NULL`. NULL means "written before
                # versions were distinguished" and is read as applying to every version, so an
                # orphan here would lie in wait and attach itself to the next entity created with
                # this id — corroboration for a record it never saw.
                raise ValueError(
                    f"append_sources REFUSED: {entity_id!r} has no open version to corroborate. "
                    f"A pointer with no version would apply to every future version of this id.")
            try:
                self._attach_sources(entity_id, sources, None, version_pk,
                                     entity_kind=entity_kind)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def close_node(self, entity_id: str, *, at: datetime, valid_to: Optional[datetime] = None,
                  _commit: bool = True) -> int:
        """Invalidate the OPEN version(s) of ``entity_id``: set ``system_to = at`` (we stopped
        believing it/them). ADR-0003.

        ``valid_to`` is passed through AS GIVEN — including ``None`` — never fabricated (review
        finding 1). ``at`` is when the SYSTEM stopped believing this; it is not evidence of when
        the fact itself stopped being true in the world, and treating detection time as if it
        were is the exact conflation bi-temporal modelling exists to keep apart. Pass an explicit
        ``valid_to`` when one is actually known (as `merge.py` always does, from the incoming
        record's own `valid_from`); otherwise the column is left as it was — `NULL` if it already
        was, which honestly says "we don't know when this stopped being true", not "now"."""
        return self._close("nodes", entity_id, at=at, valid_to=valid_to, _commit=_commit)

    def close_edge(self, entity_id: str, *, at: datetime, valid_to: Optional[datetime] = None,
                  _commit: bool = True) -> int:
        return self._close("edges", entity_id, at=at, valid_to=valid_to, _commit=_commit)

    def close_node_version(self, entity_id: str, version_pk: int, *, at: datetime,
                          valid_to: Optional[datetime] = None, _commit: bool = True) -> int:
        """Close ONE bi-temporal version by ``rowid_pk``, not "whichever version is open now".

        Review finding 2: the store does not forbid more than one open version of the same
        ``entity_id`` existing at once (`_open_version_pk`'s own docstring says so), and
        `ReverseIndex.cascade` binds its impact to a specific `version_pk` per pointer. Acting by
        ``entity_id`` alone — as `close_node`/`close_edge` do — closes EVERY open version sharing
        that id, including ones the cited pointer never touched: a cascade could invalidate a
        belief nothing drifted, or (for `supersede_attr_version`) edit the wrong one while the
        actually-cited version stays live. Returns 1 if that exact version was open and got
        closed, 0 if it was already closed (a benign race, not an error: something else got there
        first, and there is nothing left to invalidate)."""
        return self._close_version("nodes", entity_id, version_pk, at=at, valid_to=valid_to,
                                   _commit=_commit)

    def close_edge_version(self, entity_id: str, version_pk: int, *, at: datetime,
                          valid_to: Optional[datetime] = None, _commit: bool = True) -> int:
        return self._close_version("edges", entity_id, version_pk, at=at, valid_to=valid_to,
                                   _commit=_commit)

    def _floor_system_from(self, table: str, entity_id: str) -> Optional[datetime]:
        """The latest ``system_from`` among ``entity_id``'s currently OPEN version(s) in `table`.

        `MAX(...)` rather than a single row: closing by ``entity_id`` (not by version) can match
        more than one open row (finding 2's torn-entity case), and the clamp has to clear ALL of
        them, not just whichever one a `LIMIT 1` happened to return."""
        row = self.conn.execute(
            f"SELECT MAX(system_from) AS floor FROM {table} WHERE id = ? AND system_to IS NULL",
            (entity_id,)).fetchone()
        return _parse(row["floor"]) if row is not None else None

    def _close(self, table: str, entity_id: str, *, at: datetime, valid_to: Optional[datetime],
              _commit: bool = True) -> int:
        assert table in ("nodes", "edges")            # table is a literal; never attacker-controlled
        with self._lock:
            floor = self._floor_system_from(table, entity_id)
            when = _advance_past(at, floor) if floor is not None else at
            cur = self.conn.execute(
                f"UPDATE {table} SET system_to = ?, valid_to = COALESCE(valid_to, ?) "
                f"WHERE id = ? AND system_to IS NULL",
                (_iso(when), _iso(valid_to) if valid_to is not None else None, entity_id))
            if _commit:
                self.conn.commit()
            return cur.rowcount

    def _close_version(self, table: str, entity_id: str, version_pk: int, *, at: datetime,
                       valid_to: Optional[datetime], _commit: bool = True) -> int:
        assert table in ("nodes", "edges")
        with self._lock:
            row = self.conn.execute(
                f"SELECT system_from FROM {table} WHERE rowid_pk = ? AND id = ? "
                f"AND system_to IS NULL", (version_pk, entity_id)).fetchone()
            if row is None:
                return 0                     # already closed, or not this id — nothing to do
            floor = _parse(row["system_from"])
            when = _advance_past(at, floor)
            cur = self.conn.execute(
                f"UPDATE {table} SET system_to = ?, valid_to = COALESCE(valid_to, ?) "
                f"WHERE rowid_pk = ? AND system_to IS NULL",
                (_iso(when), _iso(valid_to) if valid_to is not None else None, version_pk))
            if _commit:
                self.conn.commit()
            return cur.rowcount

    def replace_node(self, node: GraphNode, *, at: datetime, valid_to: Optional[datetime] = None,
                     _commit: bool = True, expected_version_pk: Optional[int] = None) -> int:
        """ATOMIC supersession (ADR-0003): close the open version of ``node.id`` AND insert the new
        version in ONE transaction under the lock, so a concurrent reader never sees the entity with
        zero open versions (the torn 'no current belief' window). Returns rows closed.

        ``valid_to`` defaults to ``None`` (unknown), never to ``at`` (review finding 1) — the two
        are different claims and only the caller who actually knows when the fact changed (e.g.
        `merge.py`, from the incoming record's `valid_from`) should assert it.

        ``expected_version_pk``, when given, scopes the close to that EXACT ``rowid_pk`` instead of
        "whichever version of ``node.id`` is open" (review finding 2). The store does not forbid
        more than one open version sharing an id; without this, superseding one specific version
        (as `supersede_attr_version` must, for the version a cascade's citation is actually bound
        to) would close EVERY open version of the id — or, since this method already refuses
        unless exactly one row closed, raise a spurious "had 2, expected 1" for an entity that was
        never ambiguous about which version was meant, only about how many happened to be open."""
        if not node.sources:
            raise ValueError(f"node {node.id!r} REFUSED: no SourcePointer")
        self._recheck_grounding(node)
        with self._lock:
            try:
                if expected_version_pk is not None:
                    row = self.conn.execute(
                        "SELECT system_from FROM nodes WHERE rowid_pk = ? AND id = ? "
                        "AND system_to IS NULL", (expected_version_pk, node.id)).fetchone()
                    floor = _parse(row["system_from"]) if row is not None else None
                    when = _advance_past(at, floor) if floor is not None else at
                    cur = self.conn.execute(
                        "UPDATE nodes SET system_to = ?, valid_to = COALESCE(valid_to, ?) "
                        "WHERE rowid_pk = ? AND system_to IS NULL",
                        (_iso(when), _iso(valid_to) if valid_to is not None else None,
                         expected_version_pk))
                else:
                    floor = self._floor_system_from("nodes", node.id)
                    when = _advance_past(at, floor) if floor is not None else at
                    cur = self.conn.execute(
                        "UPDATE nodes SET system_to = ?, valid_to = COALESCE(valid_to, ?) "
                        "WHERE id = ? AND system_to IS NULL",
                        (_iso(when), _iso(valid_to) if valid_to is not None else None, node.id))
                closed = cur.rowcount
                if closed != 1:
                    # Supersession means "this replaces exactly one open belief". Zero means
                    # there was nothing to replace (this is an insert wearing the wrong name, or
                    # — with `expected_version_pk` — a race: that exact version is no longer
                    # open); more than one (only reachable without `expected_version_pk`) means
                    # the store already held a torn entity and the caller did not say which
                    # version it meant. The caller's `assert` ran AFTER the commit and vanishes
                    # under `python -O`, so it never protected anything — the transaction has to
                    # refuse itself.
                    raise ValueError(
                        f"replace_node REFUSED: {node.id!r} had {closed} open versions matching "
                        f"the target, expected exactly 1. Nothing was written.")
                inserted = self.conn.execute(
                    "INSERT INTO nodes(id, kind, label, canonical_id, attrs_json, valid_from, "
                    "valid_to, system_from, system_to) VALUES (?,?,?,?,?,?,?,?,?)",
                    (node.id, node.kind.value, node.label, node.canonical_id,
                     json.dumps(node.attrs, sort_keys=True, default=str),
                     _iso(node.valid_from), _iso(node.valid_to) if node.valid_to else _OPEN,
                     _iso(node.system_from), _iso(node.system_to) if node.system_to else _OPEN))
                # Review finding 11: this INSERT used to sit AFTER the `try:` started below it —
                # i.e. OUTSIDE any exception handling at all. A failure here (a constraint
                # violation, a full disk, any I/O error) left the UPDATE above pending on the
                # shared connection with nothing to roll it back: the next unrelated `commit()`
                # on this connection would publish a version closed with no replacement — the
                # exact torn state this method exists to make impossible. The INSERT is now
                # inside the same try/except as `_attach_sources`, so ANY failure anywhere in the
                # supersession rolls back the WHOLE thing, close included.
                self._attach_sources(node.id, node.sources, node.attr_sources,
                                     inserted.lastrowid, entity_kind="node")
                if _commit:
                    self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            return closed

    def supersede_attr(self, entity_id: str, field: str, *, at: datetime,
                       valid_to: Optional[datetime] = None, _commit: bool = True) -> str:
        """Invalidate ONE attribute on the version currently OPEN for ``entity_id``.

        The attribute-precise half of the invalidation cascade (`SYSTEM_DESIGN` §5.3). The value
        is set to `None` and its pointer is dropped, so the key lands in `unresolved` — *not* in
        `withheld`. The distinction is the point: the value is gone from the world, not hidden
        from this reader (spine D4).

        Returns what it did: ``"superseded"``, ``"closed"``, or ``"noop"``.

        Two cases close the whole version instead, because there is no honest way to blank them:

        * ``label`` and ``canonical_id`` (`IDENTITY_BEARING_FIELDS`) are not attributes and are
          not optional — a node with no label is not a node with a missing value, and
          `canonical_id` is the exact-merge key, so leaving a node behind with the identifier
          silently removed would change how the next upsert resolves it;
        * an entity with no open version, or one where the attribute is already absent, is a
          no-op — nothing is written, and the caller is told so rather than shown a count that
          suggests work happened.

        Acts on "whichever version is open now" — use `supersede_attr_version` when the caller
        (the cascade) knows the exact `version_pk` the citation is bound to and must not touch a
        DIFFERENT open version that happens to share the same `entity_id` (finding 2).
        """
        from .schema import IDENTITY_BEARING_FIELDS

        with self._lock:
            row = self.open_node_row(entity_id)
            if row is None:
                return "noop"
            if field in IDENTITY_BEARING_FIELDS:
                self._close("nodes", entity_id, at=at, valid_to=valid_to, _commit=_commit)
                return "closed"
            return self._supersede_attr_row(row, field, at=at, valid_to=valid_to,
                                            _commit=_commit)

    def supersede_attr_version(self, entity_id: str, field: str, version_pk: int, *,
                              at: datetime, valid_to: Optional[datetime] = None,
                              _commit: bool = True) -> str:
        """`supersede_attr`, scoped to the exact ``version_pk`` a citation is bound to.

        If that version is no longer open (already closed by an entity-level step earlier in the
        same cascade, or by something else entirely), this is a `"noop"` — there is nothing left
        on THAT version to invalidate, and reaching for "whichever version is open now" instead is
        precisely the bug this method exists to avoid (finding 2)."""
        from .schema import IDENTITY_BEARING_FIELDS

        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM nodes WHERE rowid_pk = ? AND id = ? AND system_to IS NULL",
                (version_pk, entity_id)).fetchone()
            if row is None:
                return "noop"
            if field in IDENTITY_BEARING_FIELDS:
                self._close_version("nodes", entity_id, version_pk, at=at, valid_to=valid_to,
                                    _commit=_commit)
                return "closed"
            return self._supersede_attr_row(row, field, at=at, valid_to=valid_to,
                                            _commit=_commit)

    def _supersede_attr_row(self, row: sqlite3.Row, field: str, *, at: datetime,
                            valid_to: Optional[datetime], _commit: bool) -> str:
        node = row_to_node(self, row)
        if field not in node.attrs:
            return "noop"
        if node.attrs[field] is None:
            return "noop"                      # already unresolved; nothing to invalidate
        attrs = dict(node.attrs)
        attrs[field] = None
        attr_sources = {k: v for k, v in node.attr_sources.items() if k != field}
        # Force the close strictly AFTER the prior version's system_from, exactly as `merge.py`
        # does when it supersedes: a version closed at the instant it opened has the interval
        # [S, S), which no `as_of()` can ever select — so "what we believed before the source
        # changed" would be unreachable, which is the one thing invalidate-don't-delete exists to
        # keep.
        when = _advance_past(at, node.system_from)
        superseded = node.model_copy(update={
            "attrs": attrs, "attr_sources": attr_sources, "system_from": when})
        # `valid_to` is passed through as given (`None` unless the caller actually knows) — not
        # defaulted to `when`, for the same reason `close_node`/`_close` no longer default it
        # (finding 1): the instant we stopped believing this is not evidence of when it stopped
        # being true.
        #
        # `expected_version_pk=row["rowid_pk"]` (finding 2): `row` is the EXACT version this call
        # was asked to supersede — `open_node_row`'s pick for `supersede_attr`, or the caller's own
        # `version_pk` for `supersede_attr_version`. Without pinning `replace_node` to it, its
        # closing step falls back to "whichever version of this id is open", which — on a torn
        # entity with more than one open version — either closes a version this call was never
        # told to touch, or refuses with a spurious "had 2, expected 1" for an id that was never
        # actually ambiguous about which row was meant.
        self.replace_node(superseded, at=when, valid_to=valid_to, _commit=_commit,
                          expected_version_pk=row["rowid_pk"])
        return "superseded"

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

    def find_open_edge(self, src_id: str, dst_id: str, kind: str) -> Optional[sqlite3.Row]:
        """The OPEN edge on the exact ``(src_id, dst_id, kind)`` triple, if any.

        This is the lookup `merge.upsert_edge` needs before every insert: "have I already got
        this relationship open?". It used to be answered by scanning `open_edges()` in Python —
        every open edge, every insert — which is what made a 52,747-edge estate build O(n^2).
        Backed by the partial index `ix_edges_open_triple` (`src_id, dst_id, kind` WHERE
        `system_to IS NULL`), so this is a SEARCH, not a SCAN, at every scale
        (`test_find_open_edge_uses_an_index`).

        ``kind`` is a plain string (``EdgeKind.value``), not an `EdgeKind`, so a caller with
        either in hand can pass it straight through without importing the enum here.

        `ORDER BY rowid_pk LIMIT 1` — ascending, first match wins — reproduces exactly what the
        old `for row in store.open_edges(): ...; return ...` loop returned: the OLDEST open edge
        on the triple. There should only ever be one (an edge upsert corroborates the existing
        row rather than opening a second), but matching the old tie-break keeps behaviour
        identical for a store that predates this index or was written to some other way.
        """
        return self.fetchone(
            "SELECT * FROM edges WHERE src_id = ? AND dst_id = ? AND kind = ? "
            "AND system_to IS NULL ORDER BY rowid_pk LIMIT 1",
            (src_id, dst_id, kind))

    def find_by_canonical(self, kind: NodeKind, canonical_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM nodes WHERE kind = ? AND canonical_id = ? AND system_to IS NULL "
            "ORDER BY rowid_pk DESC LIMIT 1", (kind.value, canonical_id))

    def open_nodes_with_identifier(self, kind: NodeKind, identifier: str,
                                   limit: int = 50) -> list[sqlite3.Row]:
        """Open nodes of ``kind`` whose ``attrs.extracted_identifier`` equals ``identifier``,
        newest first, at most ``limit``. A CANDIDATE lookup for a human to review — nothing in the
        store or in ``merge`` ever resolves on it (backed by ``ix_nodes_extracted_identifier``)."""
        return self.fetchall(
            "SELECT * FROM nodes WHERE kind = ? AND "
            "(CASE WHEN json_valid(attrs_json) THEN json_extract(attrs_json, "
            "'$.extracted_identifier') END) = ? AND system_to IS NULL "
            "ORDER BY rowid_pk DESC LIMIT ?", (kind.value, identifier, int(limit)))

    def open_nodes_of_kind(self, kind: NodeKind) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM nodes WHERE kind = ? AND system_to IS NULL ORDER BY rowid_pk",
            (kind.value,))

    def open_node_candidates_of_kind(self, kind: NodeKind) -> list[sqlite3.Row]:
        """Lean projection of ``open_nodes_of_kind`` for the fuzzy-match candidate scan in
        `merge._resolve_existing` step 2: just ``(rowid_pk, label, canonical_id)`` — the three
        columns the scorer actually reads — instead of every column (`attrs_json`, the four
        temporal columns, ...) that `open_nodes_of_kind` returns and that the scorer never
        touches.

        Same predicate, same order as `open_nodes_of_kind`, so the CANDIDATE SET returned is
        identical — only the per-row I/O and `sqlite3.Row` construction cost of scanning it is
        smaller. This is a speed change, not a behaviour change: use `node_row_by_rowid` to
        hydrate the eventual winner back into a full row before it is returned as a match.
        """
        return self.fetchall(
            "SELECT rowid_pk, label, canonical_id FROM nodes WHERE kind = ? AND system_to IS NULL "
            "ORDER BY rowid_pk", (kind.value,))

    def node_row_by_rowid(self, rowid_pk: int) -> Optional[sqlite3.Row]:
        """The full node row for a rowid already known (e.g. from
        `open_node_candidates_of_kind`) — used once, to hydrate the winning fuzzy-match
        candidate, rather than fetching every candidate's full row during scoring."""
        return self.fetchone("SELECT * FROM nodes WHERE rowid_pk = ?", (rowid_pk,))

    def _open_version_pk(self, entity_id: str) -> Optional[int]:
        """The rowid of the version believed NOW, for either table.

        `ORDER BY rowid_pk DESC` matters and its absence was a real defect: an unordered
        `UNION ALL … fetchone` returns the LOWEST rowid, while every renderer reads through
        `open_node_row`, which takes the highest. An id with more than one open row — which the
        store allows and `add_node` does not prevent — then cited one version while displaying
        another, and corroboration attached to the version nobody looks at.
        """
        return self._open_version(entity_id)[1]

    def _open_version(self, entity_id: str) -> tuple[str, Optional[int]]:
        """`(entity_kind, rowid)` of the version believed NOW — the same answer as
        `_open_version_pk`, plus WHICH table it came from.

        The reverse index binds a pointer to a node or an edge, and a bare rowid cannot say which:
        the two tables have independent AUTOINCREMENT sequences, so the same integer is a valid
        row in both. Reading the kind out of the same query is the only way to be sure, and
        guessing it afterwards would put a node's pointer under an edge of the same rowid.
        """
        row = self.fetchone(
            "SELECT entity_kind, rowid_pk FROM ("
            " SELECT 'node' AS entity_kind, rowid_pk FROM nodes WHERE id = ? AND system_to IS NULL"
            " UNION ALL"
            " SELECT 'edge' AS entity_kind, rowid_pk FROM edges WHERE id = ? AND system_to IS NULL"
            ") ORDER BY rowid_pk DESC LIMIT 1",
            (entity_id, entity_id))
        if row is None:
            return ("unknown", None)
        return (row["entity_kind"], row["rowid_pk"])

    def _pointers(self, entity_id: str, *, field_is_null: bool,
                  version_pk: Optional[int], all_versions: bool) -> list:
        sql = ("SELECT field, version_pk, source_pointer_json FROM sources WHERE entity_id = ? "
               "AND field IS " + ("NULL" if field_is_null else "NOT NULL"))
        params: tuple = (entity_id,)
        if not all_versions:
            # SCOPING IS THE DEFAULT, and that is the point. Making it opt-in put the burden on
            # every call site to remember, and two of them promptly did not: the evidence sheet's
            # edge builder and the pack's bulk history loader both went on pooling pointers across
            # versions while the store advertised the defect as fixed. A safety property that has
            # to be requested is a safety property that will be missed somewhere.
            if version_pk is None:
                version_pk = self._open_version_pk(entity_id)
            if version_pk is None:
                # Nothing open: an entity fully closed on the system axis. Its pointers are still
                # reachable through `all_versions=True`, which an audit path asks for explicitly.
                sql += " AND version_pk IS NULL"
            else:
                # A pointer written before `version_pk` existed (NULL) is kept: it was attached to
                # this entity at a time when versions were not distinguished, and dropping it
                # would silently remove real provenance from an upgraded store.
                sql += " AND (version_pk = ? OR version_pk IS NULL)"
                params += (version_pk,)
        rows = self.fetchall(sql, params)
        if field_is_null:
            return [SourcePointer.model_validate_json(r["source_pointer_json"]) for r in rows]
        return self._collapse_attr_rows(rows)

    @staticmethod
    def _collapse_attr_rows(rows: list[sqlite3.Row]) -> dict[str, SourcePointer]:
        """``{field: pointer}``, deterministic even when more than one row names the same field.

        Review finding 8: a plain dict comprehension over unordered rows picked whichever one
        SQLite happened to return LAST for a given field — nondeterministic, and able to cite
        bytes belonging to a version OTHER than the one being read. Two shapes actually reach
        this scoped query (the `(version_pk = ? OR version_pk IS NULL)` clause above deliberately
        allows both to appear together):

        * an exact-version row and a legacy ``version_pk IS NULL`` row (predates per-version
          tracking) for the SAME field — the exact-version row wins, always, because it is the
          more specific evidence for the version actually being read;
        * two rows with DIFFERENT non-``NULL`` ``version_pk`` for the same field — only reachable
          via ``all_versions=True`` — there is no honest priority between two real, specific
          versions' citations, so this is REFUSED rather than silently resolved by row order.
        """
        by_field: dict[str, tuple[Optional[int], SourcePointer]] = {}
        for row in rows:
            field, vpk = row["field"], row["version_pk"]
            pointer = SourcePointer.model_validate_json(row["source_pointer_json"])
            prior = by_field.get(field)
            if prior is None:
                by_field[field] = (vpk, pointer)
            elif prior[0] is None and vpk is not None:
                by_field[field] = (vpk, pointer)         # exact version beats the legacy NULL row
            elif prior[0] is not None and vpk is None:
                pass                                      # keep the exact version already held
            elif prior[0] != vpk:
                raise ValueError(
                    f"attribute {field!r} is cited by pointers from two different versions "
                    f"({prior[0]!r} and {vpk!r}) in the same query — there is no honest way to "
                    f"pick one. Scope the read to a single version (the default) instead of "
                    f"all_versions=True if only one version's citation is wanted.")
            # else: the same version_pk named twice (an exact duplicate row) — keep the first.
        return {field: pointer for field, (_, pointer) in by_field.items()}

    def sources_for(self, entity_id: str, *, version_pk: Optional[int] = None,
                    all_versions: bool = False) -> list[SourcePointer]:
        """Pointers grounding the ENTITY itself — deliberately NOT the per-attribute ones.

        Mixing the two produces the laundering this schema exists to prevent: a longer citation
        list under one heading, from which a reader (or a model) infers that every value shown
        was cited. Per-attribute pointers come back keyed, from `attr_sources_for`.

        The answer is scoped to ONE bi-temporal version: the one named by ``version_pk``, or the
        version open now. Pooling across versions is what made a superseded value's citation
        appear beside its replacement, and is available only by asking for ``all_versions``.
        """
        return self._pointers(entity_id, field_is_null=True, version_pk=version_pk,
                              all_versions=all_versions)

    def attr_sources_for(self, entity_id: str, *, version_pk: Optional[int] = None,
                         all_versions: bool = False) -> dict[str, SourcePointer]:
        """``{attribute: pointer}`` for one version — the named one, or the version open now."""
        return self._pointers(entity_id, field_is_null=False, version_pk=version_pk,
                              all_versions=all_versions)

    # -- BOUNDED provenance reads (review R-019 t07 H3) ---------------------------------------
    # `sources_for` / `attr_sources_for` return every pointer of a version. That is right for a
    # write path that must see all of them and wrong for a read surface: a heavily cited entity
    # made one detail response arbitrarily large. These return a window AND the exact total, so a
    # caller can say how many it left out instead of silently dropping them.
    def _version_scope(self, entity_id: str, version_pk: Optional[int]) -> tuple[str, tuple]:
        if version_pk is None:
            version_pk = self._open_version_pk(entity_id)
        if version_pk is None:
            return " AND version_pk IS NULL", ()
        return " AND (version_pk = ? OR version_pk IS NULL)", (version_pk,)

    def sources_window(self, entity_id: str, *, version_pk: Optional[int] = None,
                       limit: int = 200) -> tuple[list[SourcePointer], int]:
        """``(pointers, total)`` — the first ``limit`` ENTITY pointers of one version (exact-version
        rows before legacy ``NULL``-version rows, then insertion order) and how many there are."""
        scope, params = self._version_scope(entity_id, version_pk)
        total = self.fetchone(
            f"SELECT COUNT(*) c FROM sources WHERE entity_id = ? AND field IS NULL{scope}",
            (entity_id, *params))["c"]
        rows = self.fetchall(
            f"SELECT source_pointer_json FROM sources WHERE entity_id = ? AND field IS NULL{scope} "
            f"ORDER BY (version_pk IS NULL), rowid LIMIT ?", (entity_id, *params, int(limit)))
        return [SourcePointer.model_validate_json(r["source_pointer_json"]) for r in rows], int(total)

    def attr_sources_window(self, entity_id: str, *, version_pk: Optional[int] = None,
                            fields: Optional[Sequence[str]] = None,
                            limit: int = 500) -> tuple[dict[str, SourcePointer], int]:
        """``({field: pointer}, total_fields)`` for one version: at most ``limit`` fields (in field
        order), optionally only ``fields``, and the exact number of distinct cited fields. Same
        collapse rule as ``attr_sources_for`` (exact version beats a legacy ``NULL`` row)."""
        scope, params = self._version_scope(entity_id, version_pk)
        only, only_params = "", ()
        if fields is not None:
            fields = list(fields)
            if not fields:
                return {}, 0
            only = f" AND field IN ({','.join('?' * len(fields))})"
            only_params = tuple(fields)
        total = self.fetchone(
            f"SELECT COUNT(DISTINCT field) c FROM sources WHERE entity_id = ? "
            f"AND field IS NOT NULL{scope}{only}", (entity_id, *params, *only_params))["c"]
        # window by FIELD, not by row: every row of a windowed field is read, so the collapse
        # below sees both an exact-version and a legacy row for it when both exist
        rows = self.fetchall(
            f"SELECT field, version_pk, source_pointer_json FROM sources WHERE entity_id = ? "
            f"AND field IS NOT NULL{scope}{only} AND field IN ("
            f"  SELECT DISTINCT field FROM sources WHERE entity_id = ? AND field IS NOT NULL"
            f"{scope}{only} ORDER BY field LIMIT ?) ORDER BY field, rowid",
            (entity_id, *params, *only_params, entity_id, *params, *only_params, int(limit)))
        return self._collapse_attr_rows(rows), int(total)

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
        # Reported apart, because they are not the same claim. `sources` grounds entities;
        # `attribute_sources` grounds individual values. Summing them into one "sources" figure
        # would inflate every provenance count on the dashboard the moment per-attribute pointers
        # landed, and would say nothing about how much of what is DISPLAYED is actually cited.
        s = self.fetchone("SELECT COUNT(*) c FROM sources WHERE field IS NULL")["c"]
        a = self.fetchone("SELECT COUNT(*) c FROM sources WHERE field IS NOT NULL")["c"]
        return {"nodes": n, "edges": e, "sources": s, "attribute_sources": a}

    def refresh(self) -> dict[str, int]:
        """Re-open the connection to a FILE-backed store and return fresh counts.

        Not needed to see another process's writes: this store caches nothing — every read is a
        SQL query, and with no transaction held open between calls SQLite serves the latest
        committed state (tested in ``test_shared_store_*``). It exists for the one case a live
        connection cannot follow: the file at ``path`` being REPLACED (an atomic publish swaps the
        file; the old handle keeps reading the old inode). Cheap, locked, and a no-op re-read for
        an in-memory store, which has no file to follow.
        """
        with self._lock:
            if self.path != ":memory:":
                old = self.conn
                conn = sqlite3.connect(self.path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self.conn = conn
                old.close()
            return self.counts()

    def close(self) -> None:
        self.conn.close()


# --- row → typed helpers (used by query.py / serialisation) -----------------------
def _version_of(row) -> Optional[int]:
    """The bi-temporal version a row IS, so its pointers are read for that version alone.

    A row selected without `rowid_pk` returns None, which the store reads as "scope to whatever
    is open" — not as "pool everything". The unsafe reading has to be asked for by name.
    """
    try:
        return row["rowid_pk"]
    except (IndexError, KeyError):
        return None



def _grounded_attrs_only(attrs: dict, attr_sources: dict) -> dict:
    """The read-time half of the anti-fabrication wall for a row from an older schema.

    `_attrs_are_grounded` (`schema.py`) refuses to CONSTRUCT a `GraphNode`/`GraphEdge` around any
    non-None attribute that has no per-field pointer in `attr_sources`. A store written before
    per-attribute pointers existed holds `attrs_json` with values that only ever had an
    ENTITY-level citation — reading such a row back through `row_to_node`/`row_to_edge` would
    raise on every call, for both open and long-closed versions, unless the uncited values are
    treated as unresolved first.

    This used to be done ONCE, destructively, by rewriting `attrs_json` in storage at migration
    time (`_migrate`) — which meant a superseded, system-closed row's historical values were
    permanently erased, breaking "what did we believe on date X" for exactly the rows that
    guarantee exists to protect (review finding 3). Doing the narrowing HERE instead — every read,
    transiently, never touching the stored bytes — gives the same result (the typed view still
    only shows what it can cite) without destroying the raw historical record: `attrs_json` on
    disk still holds whatever was actually written, readable by anyone who queries it directly
    (`ReverseIndex._entity_detail` does, deliberately, for exactly this reason).

    A no-op for every row written under the current schema: `_recheck_grounding` already refuses
    to durably store a `attrs` key with no matching `attr_sources` entry, so nothing here is ever
    actually uncited for them — this only ever changes behaviour for a legacy row.
    """
    if not attrs:
        return attrs
    return {k: (v if k in attr_sources else None) for k, v in attrs.items()}


def row_to_node(store: GraphStore, row: sqlite3.Row) -> GraphNode:
    attr_sources = store.attr_sources_for(row["id"], version_pk=_version_of(row))
    attrs = _grounded_attrs_only(json.loads(row["attrs_json"]), attr_sources)
    return GraphNode(
        id=row["id"], kind=NodeKind(row["kind"]), label=row["label"],
        canonical_id=row["canonical_id"], attrs=attrs, attr_sources=attr_sources,
        sources=store.sources_for(row["id"], version_pk=_version_of(row)),
        valid_from=_parse(row["valid_from"]), valid_to=_parse(row["valid_to"]),
        system_from=_parse(row["system_from"]), system_to=_parse(row["system_to"]))


def _attestation_of(row: sqlite3.Row) -> Optional[Attestation]:
    """The row's persisted `Attestation`, or ``None``.

    Tolerates a row from a store written before the column existed — such a row predates any
    determination edge kind, so ``None`` is the true answer rather than a lenient default.
    """
    try:
        raw = row["attestation_json"]
    except (IndexError, KeyError):
        return None
    return Attestation.model_validate_json(raw) if raw else None


def row_to_edge(store: GraphStore, row: sqlite3.Row) -> GraphEdge:
    attr_sources = store.attr_sources_for(row["id"], version_pk=_version_of(row))
    attrs = _grounded_attrs_only(json.loads(row["attrs_json"]), attr_sources)
    return GraphEdge(
        id=row["id"], src_id=row["src_id"], dst_id=row["dst_id"], kind=EdgeKind(row["kind"]),
        weight=row["weight"], attrs=attrs, attr_sources=attr_sources,
        sources=store.sources_for(row["id"], version_pk=_version_of(row)),
        attestation=_attestation_of(row),
        valid_from=_parse(row["valid_from"]), valid_to=_parse(row["valid_to"]),
        system_from=_parse(row["system_from"]), system_to=_parse(row["system_to"]))
