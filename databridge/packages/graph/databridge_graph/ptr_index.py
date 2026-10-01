"""The reverse pointer index — `source_id -> pointers -> nodes/edges -> emitted records`.

`SYSTEM_DESIGN.md` §5, from R02 recommendation 4: *"cheap now, near-impossible to retrofit — the
piece most pointer systems omit."*

## Why it exists

The forward direction already works: every value in the graph carries a `SourcePointer` back to
the cell, span or message it came from. The reverse direction is what an operator actually needs
the morning a source changes. `verify()` returns `DRIFT_MATERIAL` for one pointer; the only
useful next question is **"what did we build on that?"** — which versions of which nodes and
edges, and which records we already emitted while believing it.

Without an index that question is a full scan of every pointer in the store. A scan over 382k
pointers is not an invalidation path, so invalidation does not happen, so drift detection
degrades into an alarm nobody can act on.

## The three legs

    ptr_index        one row per DISTINCT pointer  (source_id, kind, locator_key)
    ptr_binding      pointer -> the bi-temporal VERSION of the node/edge it grounds
    emission /       pointer -> the records we emitted that cited it
    emission_pointer

plus `ptr_parent`, which is not in the §5 sketch and has to be: a `derived` or `generated_claim`
pointer is *built from* other pointers, and a source that drifts must reach the values compressed
over it, not merely the values that cited it directly. Trust already propagates up that ancestry
(`effective_trust`); invalidation has to propagate down it.

## Two deviations from the §5 schema, both deliberate

1. **`ptr_binding` is keyed by `source_rowid`** — the `sources` row it was built from — rather
   than by `(pointer_id, entity_kind, version_pk, field)`. That makes the index *exactly*
   alignable with the table it indexes: "which pointers has this index never seen?" is a one-line
   anti-join instead of an estimate, and that anti-join is the whole of §"known vs unknown"
   below. The §5 key is still unique, and is kept as a secondary index.
2. **`trust` is nullable.** D3's migration is in flight and an unlabelled pointer is a real,
   distinguishable state ("never considered" is not "deliberately untrusted"). Where two pointers
   to the same locator disagree, the row keeps the **worse** label, because a reverse index that
   rounded trust down would be the laundering path D3 exists to close.

## Known vs unknown — the invariant that makes this honest

A pointer the index never saw must be reported as **unknown**, never as "nothing affected". They
are different answers, and conflating them is how a drift alarm becomes falsely reassuring: the
operator reads an empty list as "nothing to do" when the truth was "I cannot tell you".

The index therefore never asserts completeness from its own contents. It asserts it from the
anti-join: if a single `sources` row exists with no `ptr_binding` row, **every** answer this index
gives is `Answer.UNKNOWN`, carrying the count of what it has not seen. That covers both ways
coverage is lost — a graph written before the index existed, and a write path that bypassed the
hook — without having to enumerate either.

Nothing here knows anything about any domain: a pointer's locator family comes from
`databridge_core.provenance`, and an emission is a `(kind, ref)` string pair the caller chooses.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Sequence

from pydantic import BaseModel

from databridge_core import SourcePointer, Trust, effective_trust

if TYPE_CHECKING:                                        # pragma: no cover - typing only
    from .store import GraphStore


# --------------------------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------------------------
#: The verdicts that INVALIDATE what was built on a pointer (spine D6; `SYSTEM_DESIGN` §4.7).
#:
#: Held as strings rather than by importing `databridge_adapter.verdicts.Verdict`, because the
#: state plane must not depend on the connection plane — the graph has to be usable, and testable,
#: with no adapter installed. `test_ptr_index.py` asserts these strings ARE the enum's values
#: whenever the adapter package is importable, so the two cannot drift apart silently.
INVALIDATING_VERDICTS: frozenset[str] = frozenset(
    {"drift_material", "vanished", "expired", "forbidden"})

#: Every verdict the lattice defines. An unrecognised verdict is REFUSED rather than treated as
#: benign: a typo that silently meant "nothing to do" is the failure mode this whole module is
#: about.
KNOWN_VERDICTS: frozenset[str] = INVALIDATING_VERDICTS | frozenset(
    {"verified", "verified_as_of_capture", "drift_benign", "unverifiable"})


def normalise_verdict(verdict: Any) -> str:
    """`Verdict.DRIFT_MATERIAL` | `"DRIFT_MATERIAL"` | `"drift_material"` -> `"drift_material"`."""
    raw = getattr(verdict, "value", verdict)
    text = str(raw).strip().lower()
    if text not in KNOWN_VERDICTS:
        raise ValueError(
            f"unknown verdict {verdict!r}. The lattice is closed ({sorted(KNOWN_VERDICTS)}); an "
            f"unrecognised verdict is refused rather than assumed benign, because assuming benign "
            f"is how a cascade silently does nothing")
    return text


# --------------------------------------------------------------------------------------------
# Pointer identity
# --------------------------------------------------------------------------------------------
#: Fields of a `SourcePointer` that are NOT part of its locator. `source_id` and `kind` are the
#: other two thirds of the natural key and are stored in their own columns; `trust` and
#: `capture_digest` are properties of the READ, not of the place read from — two pointers at the
#: same cell captured twice are one cell, and must be one row or the cascade fans out.
_NON_LOCATOR = frozenset({"source_id", "kind", "trust", "capture_digest"})

#: Locator fields that hold other pointers. They are folded to the parents' own identity strings,
#: so an equal ancestry produces an equal key regardless of how the parent was labelled.
_NESTED_LIST_FIELDS = ("derived_from", "facts")
_NESTED_ONE_FIELD = "child"

_MAX_NESTING = 64                # a pointer chain deeper than this is a cycle or a bug

_UNIT_SEP = "\x1f"

#: SQLite caps the number of host parameters in one statement (999 in older builds, 32766 in
#: modern ones). A single source in the estate graph grounds tens of thousands of pointers, so an
#: `IN (?, ?, ...)` over the whole closure is not a theoretical limit — it is the first query the
#: cascade would run on real data, and it would raise rather than answer.
_PARAM_CHUNK = 900

#: `upsert_pointer`'s within-one-write memo: identity -> `(pointer_id, trust, capture_digest)`,
#: not a bare id — see the finding-4 note on `upsert_pointer` for why the extra two fields matter.
PointerCache = dict[str, tuple[int, Optional[str], Optional[str]]]


def _chunks(items: Sequence, size: int = _PARAM_CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _jsonable(value: Any, depth: int) -> Any:
    if isinstance(value, SourcePointer):
        return pointer_identity_string(value, _depth=depth + 1)
    if isinstance(value, BaseModel):
        # e.g. `snapshot: SnapshotIdentity`. `mode="json"` so a datetime canonicalises the same
        # way on every machine rather than through `repr`.
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_jsonable(v, depth) for v in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def locator_key(pointer: SourcePointer, *, _depth: int = 0) -> str:
    """The canonical string of this pointer's locator family. Deterministic across processes.

    Every set field except `_NON_LOCATOR`, canonically ordered. Deliberately inclusive: a key that
    omits a locator field would conflate two different cells into one index row, which is a wrong
    answer; a key that includes a field that turns out to be incidental merely splits one cell
    into two rows, which is a redundant answer. Only one of those two errors is safe.
    """
    if _depth > _MAX_NESTING:
        raise ValueError(
            f"SourcePointer nesting deeper than {_MAX_NESTING}: refusing to canonicalise a chain "
            f"that is either cyclic or generated")
    parts: dict[str, Any] = {}
    for name in type(pointer).model_fields:
        if name in _NON_LOCATOR:
            continue
        value = getattr(pointer, name, None)
        if value is None:
            continue
        parts[name] = _jsonable(value, _depth)
    return json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)


def pointer_identity(pointer: SourcePointer) -> tuple[str, str, str]:
    """`(source_id, kind, locator_key)` — the natural key of a row in `ptr_index`."""
    return (pointer.source_id, str(pointer.kind), locator_key(pointer))


def pointer_identity_string(pointer: SourcePointer, *, _depth: int = 0) -> str:
    src, kind, loc = pointer.source_id, str(pointer.kind), locator_key(pointer, _depth=_depth)
    return f"{src}{_UNIT_SEP}{kind}{_UNIT_SEP}{loc}"


def parents_of(pointer: SourcePointer) -> list[SourcePointer]:
    """Every pointer this one is built on top of, across all three nesting fields.

    Mirrors `databridge_core.provenance._parents_of`, which is private. Collected regardless of
    `kind` for the same reason it is there: `derived_from` legally appears on any kind, so keying
    off `kind` would leave a hole exactly where the chains are longest.
    """
    out: list[SourcePointer] = []
    for name in _NESTED_LIST_FIELDS:
        out.extend(getattr(pointer, name, None) or ())
    child = getattr(pointer, _NESTED_ONE_FIELD, None)
    if child is not None:
        out.append(child)
    return out


#: Worst-first, for merging the denormalised label of two pointers at one locator. `None`
#: (unlabelled) sits between the two REAL labels rather than beside `trusted`: it must not be
#: rounded down to trusted (that is the fail-open D3 refuses) nor up to untrusted (that would
#: erase the distinction between "never considered" and "deliberately untrusted").
_TRUST_SEVERITY = {Trust.UNTRUSTED.value: 0, None: 1, Trust.TRUSTED.value: 2}


def _worse_trust(a: Optional[str], b: Optional[str]) -> Optional[str]:
    return a if _TRUST_SEVERITY.get(a, 1) <= _TRUST_SEVERITY.get(b, 1) else b


def _trust_label(pointer: SourcePointer) -> Optional[str]:
    """The pointer's `effective_trust` as a string — re-derived from ancestry, not the stored
    field, so a forged label is not what the index denormalises."""
    label = effective_trust(pointer)
    return label.value if label is not None else None


# --------------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------------
INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS ptr_index (
    pointer_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id        TEXT NOT NULL,
    kind             TEXT NOT NULL,
    locator_key      TEXT NOT NULL,
    trust            TEXT,
    capture_digest   TEXT,
    first_seen       TEXT NOT NULL,
    last_verified_at TEXT,
    last_verdict     TEXT,
    UNIQUE (source_id, kind, locator_key)
);
CREATE TABLE IF NOT EXISTS ptr_parent (
    child_pointer_id  INTEGER NOT NULL,
    parent_pointer_id INTEGER NOT NULL,
    PRIMARY KEY (child_pointer_id, parent_pointer_id)
);
CREATE TABLE IF NOT EXISTS ptr_binding (
    source_rowid INTEGER PRIMARY KEY,
    pointer_id   INTEGER NOT NULL,
    entity_kind  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    version_pk   INTEGER,
    field        TEXT
);
CREATE TABLE IF NOT EXISTS emission (
    emission_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    ref          TEXT NOT NULL,
    plan_hash    TEXT,
    ledger_bound INTEGER NOT NULL DEFAULT 0,
    emitted_at   TEXT NOT NULL,
    stale_since  TEXT,
    stale_cause  TEXT,
    UNIQUE (kind, ref)
);
CREATE TABLE IF NOT EXISTS emission_pointer (
    emission_id INTEGER NOT NULL,
    pointer_id  INTEGER NOT NULL,
    PRIMARY KEY (emission_id, pointer_id)
);
CREATE TABLE IF NOT EXISTS ptr_index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# `ix_ptr_binding_natural` is deliberately NOT `UNIQUE` (review finding 10). `ptr_binding`'s real
# primary key is `source_rowid` — one binding row per `sources` row, by construction. Two DIFFERENT
# `sources` rows legitimately land on the same `(pointer_id, entity_kind, version_pk, field)` tuple
# whenever a caller cites the same locator twice for the same attribute (a duplicate `SourcePointer`
# in `node.sources`, or two independent reads that happen to agree). A UNIQUE index there forced
# `bind_pointer`'s `INSERT OR REPLACE` to silently DELETE the earlier `source_rowid`'s binding on
# every such collision — the anti-join in `unindexed_pointer_count()` would then report that
# `sources` row as never-indexed again, and a re-run of `backfill()` would just evict a *different*
# row's binding instead of ever converging: an index that could never finish repairing itself. The
# reported UNKNOWN count stayed honest throughout (it never claimed complete coverage while a row
# was silently orphaned), which is why this is Medium and not Critical — but "the answer stays
# honest while never being able to reach 'known'" is still a defect.
INDEX_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_ptr_by_source ON ptr_index(source_id);
CREATE INDEX IF NOT EXISTS ix_ptr_binding_natural
    ON ptr_binding(pointer_id, entity_kind, version_pk, field);
CREATE INDEX IF NOT EXISTS ix_ptr_binding_ptr ON ptr_binding(pointer_id);
CREATE INDEX IF NOT EXISTS ix_ptr_binding_rev ON ptr_binding(entity_kind, entity_id, version_pk);
CREATE INDEX IF NOT EXISTS ix_ptr_parent_parent ON ptr_parent(parent_pointer_id);
CREATE INDEX IF NOT EXISTS ix_emission_by_pointer ON emission_pointer(pointer_id);
"""


def ensure_index_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(INDEX_SCHEMA)
    # A store built before finding 10's fix may already carry `ix_ptr_binding_natural` as UNIQUE.
    # `CREATE INDEX IF NOT EXISTS` cannot change an existing index's definition, so the old, unsafe
    # unique index has to be dropped explicitly before the non-unique replacement is (re)created —
    # on every open, which is cheap and idempotent (a no-op once the store is on the fixed schema).
    conn.execute("DROP INDEX IF EXISTS ix_ptr_binding_natural")
    conn.executescript(INDEX_INDEXES)


# --------------------------------------------------------------------------------------------
# Write path — called from inside the graph write's own transaction
# --------------------------------------------------------------------------------------------
def upsert_pointer(conn: sqlite3.Connection, pointer: SourcePointer, now: str,
                   cache: Optional[PointerCache] = None,
                   *, _depth: int = 0) -> int:
    """Index one pointer AND its whole ancestry; return its `pointer_id`.

    `INSERT ... ON CONFLICT DO UPDATE` rather than insert-or-select, so two writes of the same
    cell in one transaction cannot produce two rows, and so the denormalised `trust` moves only
    ever towards the worse label.

    The cache holds `(pointer_id, trust, capture_digest)`, not a bare id. Review finding 4: a
    cache keyed only by id let a SECOND pointer at the same locator, within the same write, skip
    the worse-trust merge entirely — an early `return hit` before `trust` was even recomputed. Two
    pointer objects can share a locator identity (`trust` is excluded from it by design, see
    `_NON_LOCATOR`) while disagreeing on trust, or on an ancestor's trust; the first one seen would
    then keep its label forever within that cache's lifetime regardless of what arrived after it —
    exactly the trust-laundering path D3 exists to close. A cache hit therefore still recomputes
    and merges `trust`/`capture_digest` (in memory, against the cached values — no re-SELECT), and
    still walks `parents_of`, so an ancestor sharing this cache gets the same chance to merge.
    """
    if _depth > _MAX_NESTING:
        raise ValueError(f"SourcePointer nesting deeper than {_MAX_NESTING}")
    identity = pointer_identity_string(pointer, _depth=_depth)
    trust = _trust_label(pointer)
    cached = cache.get(identity) if cache is not None else None

    if cached is not None:
        pointer_id, cached_trust, cached_digest = cached
        merged = _worse_trust(cached_trust, trust)
        # Same fill-once rule as the miss path: a digest is only ever filled in, never replaced.
        digest = cached_digest if cached_digest is not None else pointer.capture_digest
        if merged != cached_trust or digest != cached_digest:
            conn.execute("UPDATE ptr_index SET trust = ?, capture_digest = ? WHERE pointer_id = ?",
                         (merged, digest, pointer_id))
            if cache is not None:
                cache[identity] = (pointer_id, merged, digest)
    else:
        source_id, kind, loc = (pointer.source_id, str(pointer.kind),
                                identity.rsplit(_UNIT_SEP, 1)[-1])
        row = conn.execute(
            "SELECT pointer_id, trust, capture_digest FROM ptr_index "
            "WHERE source_id = ? AND kind = ? AND locator_key = ?",
            (source_id, kind, loc)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO ptr_index(source_id, kind, locator_key, trust, capture_digest, "
                "first_seen) VALUES (?,?,?,?,?,?)",
                (source_id, kind, loc, trust, pointer.capture_digest, now))
            pointer_id = int(cur.lastrowid)
            merged, digest = trust, pointer.capture_digest
        else:
            pointer_id = int(row[0])
            merged = _worse_trust(row[1], trust)
            # A capture binding is only ever FILLED IN, never replaced: the first bytes this
            # locator was read out of stay named. A later, different digest is a real signal, but
            # rewriting the column would destroy the earlier claim rather than record the
            # disagreement.
            digest = row[2] if row[2] is not None else pointer.capture_digest
            if merged != row[1] or digest != row[2]:
                conn.execute(
                    "UPDATE ptr_index SET trust = ?, capture_digest = ? WHERE pointer_id = ?",
                    (merged, digest, pointer_id))
        if cache is not None:
            cache[identity] = (pointer_id, merged, digest)

    for parent in parents_of(pointer):
        parent_id = upsert_pointer(conn, parent, now, cache, _depth=_depth + 1)
        conn.execute(
            "INSERT OR IGNORE INTO ptr_parent(child_pointer_id, parent_pointer_id) VALUES (?,?)",
            (pointer_id, parent_id))
    return pointer_id


def bind_pointer(conn: sqlite3.Connection, source_rowid: int, pointer: SourcePointer, *,
                 entity_kind: str, entity_id: str, version_pk: Optional[int],
                 field: Optional[str], now: str,
                 cache: Optional[PointerCache] = None) -> int:
    """Index the pointer and bind it to the `sources` row that recorded it. Returns `pointer_id`.

    `INSERT OR REPLACE` on the `source_rowid` primary key: the binding is a projection of exactly
    one `sources` row, so re-running over it (a backfill re-run, say) is a no-op rather than a
    duplicate. This is safe ONLY because `ix_ptr_binding_natural` is a plain index, not a unique
    one (finding 10) — a unique secondary key would make this same `INSERT OR REPLACE` evict a
    DIFFERENT `source_rowid`'s binding whenever two `sources` rows cite the same locator for the
    same attribute, which is a legitimate, expected case, not a conflict to resolve.
    """
    pointer_id = upsert_pointer(conn, pointer, now, cache)
    conn.execute(
        "INSERT OR REPLACE INTO ptr_binding(source_rowid, pointer_id, entity_kind, entity_id, "
        "version_pk, field) VALUES (?,?,?,?,?,?)",
        (source_rowid, pointer_id, entity_kind, entity_id, version_pk, field))
    return pointer_id


# --------------------------------------------------------------------------------------------
# Answers
# --------------------------------------------------------------------------------------------
class Answer(str, Enum):
    """Whether the index is entitled to call an answer complete.

    `UNKNOWN` is not an error and not an empty result. It is the statement "there are pointers in
    this store I have never seen, so I cannot tell you that nothing else is affected" — and it is
    the single most important thing this module says, because the alternative is an empty list
    that reads as reassurance.

    `UNAVAILABLE` is a third, DIFFERENT statement (review finding 9): "every pointer is indexed,
    but at least one bound row could not actually be READ" — a corrupt `attrs_json`, most
    concretely. Before this existed, `_entity_detail` swallowed a JSON parse failure into
    `value=None` while the surrounding report stayed `KNOWN` — corruption rendered identically to
    "this field is genuinely absent", which is the same dishonesty `UNKNOWN` exists to prevent,
    just at the level of one row instead of the whole index. `KNOWN`, `UNKNOWN` and `UNAVAILABLE`
    stay mutually exclusive in what they claim: only `KNOWN` with an empty result means "nothing
    cites this" — `UNKNOWN` and `UNAVAILABLE` both mean "I cannot tell you that".
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AffectedEntity:
    """One bi-temporal VERSION of a node or edge that cited an affected pointer.

    Carries what a human needs to decide, not only what a machine needs to join: what the thing is
    called, which attribute is implicated, what the attribute currently says, and whether the
    version is still believed. A closed version needs no action and must not be counted as if it
    did.
    """

    entity_kind: str                     # "node" | "edge"
    entity_id: str
    version_pk: Optional[int]
    field: Optional[str]                 # None = the pointer grounds the ENTITY, not one value
    kind: Optional[str] = None           # the node/edge kind as stored
    label: Optional[str] = None          # node label, or "src -kind-> dst" for an edge
    value: Any = None                    # what `field` currently says, if it names an attribute
    is_open: bool = False                # still believed (system_to IS NULL)?
    #: `False` when `field`'s value could not actually be read (corrupt `attrs_json`) — `value` is
    #: then `None` NOT because the field is absent, but because it is UNREADABLE. Distinct on
    #: purpose (finding 9): a reader that only checked `value is None` would treat corruption as
    #: absence, which is the exact conflation `Answer.UNAVAILABLE` exists to name at the report
    #: level. `is_open`/`kind`/`label` do not depend on `attrs_json` and stay trustworthy even when
    #: this is `False`.
    readable: bool = True
    pointer_id: int = 0
    source_id: str = ""
    locator_key: str = ""


@dataclass(frozen=True)
class AffectedEmission:
    """A record we emitted while believing an affected pointer."""

    emission_id: int
    kind: str
    ref: str
    plan_hash: Optional[str]
    ledger_bound: bool
    emitted_at: str
    stale_since: Optional[str]
    stale_cause: Optional[str]
    pointer_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ImpactReport:
    """What is affected — and whether that list is allowed to be read as complete."""

    answer: Answer
    reason: str
    pointer_ids: tuple[int, ...] = ()
    nodes: tuple[AffectedEntity, ...] = ()
    edges: tuple[AffectedEntity, ...] = ()
    #: Bindings whose entity is in neither table — a `sources` row for an id the graph does not
    #: hold. Reported in their own bucket rather than folded into `edges`, because "we recorded a
    #: citation for something that is not here" is a finding about the STORE, and a cascade must
    #: not act on it as though it were an edge.
    orphans: tuple[AffectedEntity, ...] = ()
    emissions: tuple[AffectedEmission, ...] = ()
    ledger_bound: tuple[AffectedEmission, ...] = ()
    unindexed_pointers: int = 0
    #: How many (pointer -> version) bindings the closure actually has. Equal to
    #: `len(nodes) + len(edges) + len(orphans)` unless `max_detail` truncated the lists — in which
    #: case this is still the true size, because a report that silently shows fewer affected
    #: records than there are is the same failure as reporting "none" for "unknown".
    binding_count: int = 0
    truncated: bool = False
    #: How many entries in `nodes`/`edges` have `readable=False` (finding 9) — a row the closure
    #: found but whose `attrs_json` could not be parsed, so its `value` is unknown, not absent.
    #: `answer` is downgraded to `Answer.UNAVAILABLE` whenever this is nonzero and coverage was
    #: otherwise `KNOWN`, so the count is never silently absorbed into a clean answer.
    unreadable_entities: int = 0

    @property
    def is_complete(self) -> bool:
        return self.answer is Answer.KNOWN

    @property
    def open_versions(self) -> tuple[AffectedEntity, ...]:
        return tuple(e for e in self.nodes + self.edges if e.is_open)

    def summary(self) -> str:
        if self.answer is Answer.KNOWN:
            head = "complete"
        elif self.answer is Answer.UNAVAILABLE:
            head = f"UNAVAILABLE ({self.unreadable_entities} record(s) could not be read)"
        else:
            head = f"INCOMPLETE ({self.unindexed_pointers} pointer(s) never indexed)"
        orphan = f", {len(self.orphans)} orphan binding(s)" if self.orphans else ""
        if self.truncated:
            orphan += f" [detail truncated; {self.binding_count} binding(s) in total]"
        return (f"{len(self.pointer_ids)} pointer(s) -> {len(self.nodes)} node version(s), "
                f"{len(self.edges)} edge version(s), {len(self.emissions)} emission(s), "
                f"{len(self.ledger_bound)} ledger-bound record(s){orphan}; {head}")


@dataclass(frozen=True)
class CascadeReport:
    """What the cascade found AND what it did about it."""

    verdict: str
    impact: ImpactReport
    nodes_closed: int = 0
    edges_closed: int = 0
    attributes_superseded: int = 0
    emissions_marked_stale: int = 0
    ledger_entries_untouched: int = 0

    @property
    def answer(self) -> Answer:
        return self.impact.answer


@dataclass
class BackfillReport:
    rows_scanned: int = 0
    bindings_written: int = 0
    pointers_indexed: int = 0
    parent_links: int = 0
    unresolved_entities: int = 0        # a `sources` row whose entity is in neither table
    seconds: float = 0.0
    unindexed_remaining: int = 0
    batches: int = 0

    def summary(self) -> str:
        return (f"backfill: {self.rows_scanned} source row(s) in {self.seconds:.1f}s -> "
                f"{self.pointers_indexed} distinct pointer(s), {self.bindings_written} binding(s), "
                f"{self.parent_links} ancestry link(s); {self.unindexed_remaining} remaining")


# --------------------------------------------------------------------------------------------
# The index
# --------------------------------------------------------------------------------------------
class ReverseIndex:
    """Queries and maintenance over the four index tables of one `GraphStore`.

    Construct it from a store (`store.reverse_index`); it holds no state of its own beyond a
    pointer-identity cache, so it is safe to build one per call.
    """

    def __init__(self, store: "GraphStore"):
        self.store = store

    # -- coverage ---------------------------------------------------------------------------
    def unindexed_pointer_count(self) -> int:
        """`sources` rows with no `ptr_binding` row. The exact size of what this index cannot say.

        This is the anti-join the whole honesty property rests on, and it covers both ways
        coverage is lost — a store written before the index existed, and a write path that
        bypassed the hook — without having to know which happened.
        """
        row = self.store.fetchone(
            "SELECT COUNT(*) FROM sources s "
            "LEFT JOIN ptr_binding b ON b.source_rowid = s.rowid WHERE b.source_rowid IS NULL")
        return int(row[0])

    def coverage(self) -> tuple[Answer, str, int]:
        missing = self.unindexed_pointer_count()
        if missing == 0:
            return (Answer.KNOWN, "every pointer in this store is indexed", 0)
        return (
            Answer.UNKNOWN,
            f"{missing} pointer(s) in this store were never indexed — written before the index "
            f"existed, or by a path that bypassed it. Any list of affected records is therefore a "
            f"LOWER BOUND, not an answer. Run backfill() to close the gap.",
            missing,
        )

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            return int(self.store.fetchone(sql)[0])
        return {
            "pointers": one("SELECT COUNT(*) FROM ptr_index"),
            "bindings": one("SELECT COUNT(*) FROM ptr_binding"),
            "ancestry_links": one("SELECT COUNT(*) FROM ptr_parent"),
            "emissions": one("SELECT COUNT(*) FROM emission"),
            "emission_pointers": one("SELECT COUNT(*) FROM emission_pointer"),
            "unindexed_pointers": self.unindexed_pointer_count(),
        }

    # -- lookups ----------------------------------------------------------------------------
    def pointer_id_of(self, pointer: SourcePointer) -> Optional[int]:
        source_id, kind, loc = pointer_identity(pointer)
        row = self.store.fetchone(
            "SELECT pointer_id FROM ptr_index WHERE source_id = ? AND kind = ? AND locator_key = ?",
            (source_id, kind, loc))
        return int(row[0]) if row is not None else None

    def pointer_ids_for_source(self, source_id: str) -> list[int]:
        return [int(r[0]) for r in self.store.fetchall(
            "SELECT pointer_id FROM ptr_index WHERE source_id = ? ORDER BY pointer_id",
            (source_id,))]

    def _descendants(self, seed: Iterable[int]) -> list[int]:
        """`seed` plus every pointer transitively DERIVED from it.

        A `derived` pointer compresses the rows it was computed over; a `generated_claim` names
        the facts it rests on. When one of those facts drifts, the compression drifted too — so
        the closure walks down the ancestry edges, not only across the direct citations. Without
        this leg the aggregate survives the invalidation of every input it had.
        """
        seen: set[int] = set()
        frontier = [int(p) for p in seed]
        while frontier:
            batch = [p for p in frontier if p not in seen]
            seen.update(batch)
            if not batch:
                break
            rows = []
            for chunk in _chunks(batch):
                marks = ",".join("?" * len(chunk))
                rows += self.store.fetchall(
                    f"SELECT child_pointer_id FROM ptr_parent "
                    f"WHERE parent_pointer_id IN ({marks})", tuple(chunk))
            frontier = [int(r[0]) for r in rows if int(r[0]) not in seen]
        return sorted(seen)

    # -- the cascade question ---------------------------------------------------------------
    def impact_of(self, *, source_id: Optional[str] = None,
                  pointer: Optional[SourcePointer] = None,
                  pointer_ids: Optional[Sequence[int]] = None,
                  locator_keys: Optional[Iterable[str]] = None,
                  include_closed: bool = True,
                  max_detail: Optional[int] = None) -> ImpactReport:
        """**What relies on this?** — the first-class query the drift verdict needs answered.

        Give it a `source_id` (everything read from that source), a specific `pointer`, or a set
        of `pointer_ids`. Optionally narrow a `source_id` to the `locator_keys` that actually
        changed, which is what a CDC event carries.

        Pure: it reads, it never writes. `cascade()` is the one that acts.
        """
        answer, reason, missing = self.coverage()

        if pointer_ids is not None:
            seed = [int(p) for p in pointer_ids]
        elif pointer is not None:
            found = self.pointer_id_of(pointer)
            seed = [found] if found is not None else []
            if found is None and answer is Answer.KNOWN:
                reason = ("this pointer is not in the index, and every pointer in this store is "
                          "indexed — so nothing in this store cites it")
        elif source_id is not None:
            if locator_keys is None:
                seed = self.pointer_ids_for_source(source_id)
            else:
                keys = list(locator_keys)
                seed = []
                for chunk in _chunks(keys):
                    marks = ",".join("?" * len(chunk))
                    seed += [int(r[0]) for r in self.store.fetchall(
                        f"SELECT pointer_id FROM ptr_index WHERE source_id = ? "
                        f"AND locator_key IN ({marks})", (source_id, *chunk))]
                seed.sort()
        else:
            raise ValueError(
                "impact_of() needs one of source_id, pointer or pointer_ids — a cascade with no "
                "subject would return the whole graph")

        closure = self._descendants(seed)
        nodes, edges, orphans, total, truncated, unreadable = self._entities_for(
            closure, include_closed=include_closed, max_detail=max_detail)
        emissions, ledger = self._emissions_for(closure)
        # Finding 9: a coverage answer of KNOWN must not survive discovering that some of what it
        # is vouching for could not actually be read. UNKNOWN (a coverage gap) is a strictly
        # broader doubt and takes precedence when both are true at once.
        if unreadable and answer is Answer.KNOWN:
            answer = Answer.UNAVAILABLE
            reason = (f"every pointer in this store is indexed, but {unreadable} affected "
                      f"record's attrs could not be read (corrupt attrs_json) — the value(s) "
                      f"shown for them is unknown, not absent")
        return ImpactReport(answer=answer, reason=reason, pointer_ids=tuple(closure),
                            nodes=nodes, edges=edges, orphans=orphans, emissions=emissions,
                            ledger_bound=ledger, unindexed_pointers=missing,
                            binding_count=total, truncated=truncated,
                            unreadable_entities=unreadable)

    def _entities_for(self, pointer_ids: Sequence[int], *, include_closed: bool,
                      max_detail: Optional[int] = None
                      ) -> tuple[tuple[AffectedEntity, ...], tuple[AffectedEntity, ...],
                                 tuple[AffectedEntity, ...], int, bool, int]:
        if not pointer_ids:
            return ((), (), (), 0, False, 0)
        rows = []
        for chunk in _chunks(list(pointer_ids)):
            marks = ",".join("?" * len(chunk))
            rows += self.store.fetchall(
                f"SELECT b.pointer_id, b.entity_kind, b.entity_id, b.version_pk, b.field, "
                f"p.source_id, p.locator_key FROM ptr_binding b "
                f"JOIN ptr_index p ON p.pointer_id = b.pointer_id "
                f"WHERE b.pointer_id IN ({marks})", tuple(chunk))
        rows.sort(key=lambda r: (r["entity_kind"], r["entity_id"], r["version_pk"] or 0,
                                 r["field"] or ""))
        total = len(rows)
        truncated = max_detail is not None and total > max_detail
        if truncated:
            # Reading one row per binding is what makes the report actionable; doing it 380k times
            # is what makes it unusable. `total` above stays exact, so a truncated report still
            # states the true size of what it is describing.
            rows = rows[:max_detail]
        nodes: list[AffectedEntity] = []
        edges: list[AffectedEntity] = []
        orphans: list[AffectedEntity] = []
        unreadable = 0
        for row in rows:
            entity_kind = row["entity_kind"]
            detail = self._entity_detail(entity_kind, row["entity_id"], row["version_pk"],
                                         row["field"])
            item = AffectedEntity(
                entity_kind=entity_kind, entity_id=row["entity_id"],
                version_pk=row["version_pk"], field=row["field"],
                pointer_id=int(row["pointer_id"]), source_id=row["source_id"],
                locator_key=row["locator_key"], **detail)
            if not item.readable:
                unreadable += 1
            if not include_closed and not item.is_open:
                continue
            bucket = (nodes if entity_kind == "node"
                      else edges if entity_kind == "edge" else orphans)
            bucket.append(item)
        return (tuple(nodes), tuple(edges), tuple(orphans), total, truncated, unreadable)

    def _entity_detail(self, entity_kind: str, entity_id: str, version_pk: Optional[int],
                       field: Optional[str]) -> dict:
        """Read the exact VERSION this binding names, so a superseded value is not reported as
        live. Falls back to the open version only where `version_pk` is NULL — a pointer written
        before versions were distinguished."""
        table = "nodes" if entity_kind == "node" else "edges" if entity_kind == "edge" else None
        if table is None:
            return {"is_open": False}
        if version_pk is not None:
            row = self.store.fetchone(f"SELECT * FROM {table} WHERE rowid_pk = ?", (version_pk,))
        else:
            row = self.store.fetchone(
                f"SELECT * FROM {table} WHERE id = ? AND system_to IS NULL "
                f"ORDER BY rowid_pk DESC LIMIT 1", (entity_id,))
        if row is None:
            return {"is_open": False}
        keys = row.keys()
        label = row["label"] if "label" in keys else (
            f"{row['src_id']} -{row['kind']}-> {row['dst_id']}")
        value = None
        readable = True
        if field is not None:
            try:
                attrs = json.loads(row["attrs_json"] or "{}")
            except (TypeError, ValueError):
                # Finding 9: this used to fall back to `attrs = {}` and carry on — corruption
                # rendered exactly like "this field is absent", and the caller (`impact_of`) had
                # no way to tell the two apart, so the report stayed `Answer.KNOWN`. `readable`
                # names the difference: `value` is `None` here because it is UNREADABLE, not
                # because it is unset, and `_entities_for` counts this so `impact_of` can downgrade
                # the whole report's answer rather than silently absorbing it into a clean one.
                return {"kind": row["kind"], "label": label, "value": None,
                        "is_open": row["system_to"] is None, "readable": False}
            value = attrs.get(field, row[field] if field in keys else None)
        return {"kind": row["kind"], "label": label, "value": value,
                "is_open": row["system_to"] is None, "readable": readable}

    def _emissions_for(self, pointer_ids: Sequence[int]
                       ) -> tuple[tuple[AffectedEmission, ...], tuple[AffectedEmission, ...]]:
        if not pointer_ids:
            return ((), ())
        by_id: dict[int, Any] = {}
        for chunk in _chunks(list(pointer_ids)):
            marks = ",".join("?" * len(chunk))
            for row in self.store.fetchall(
                    f"SELECT e.*, GROUP_CONCAT(ep.pointer_id) AS ptrs FROM emission e "
                    f"JOIN emission_pointer ep ON ep.emission_id = e.emission_id "
                    f"WHERE ep.pointer_id IN ({marks}) GROUP BY e.emission_id", tuple(chunk)):
                # One emission can cite pointers from several chunks; merge rather than replace,
                # or the reported citation set would depend on where the chunk boundary fell.
                seen = by_id.get(int(row["emission_id"]))
                merged = set((row["ptrs"] or "").split(",")) | (seen[1] if seen else set())
                by_id[int(row["emission_id"])] = (row, merged)
        live: list[AffectedEmission] = []
        ledger: list[AffectedEmission] = []
        for row, ptrs in (by_id[k] for k in sorted(by_id)):
            item = AffectedEmission(
                emission_id=int(row["emission_id"]), kind=row["kind"], ref=row["ref"],
                plan_hash=row["plan_hash"], ledger_bound=bool(row["ledger_bound"]),
                emitted_at=row["emitted_at"], stale_since=row["stale_since"],
                stale_cause=row["stale_cause"],
                pointer_ids=tuple(sorted(int(p) for p in ptrs if p)))
            (ledger if item.ledger_bound else live).append(item)
        return (tuple(live), tuple(ledger))

    # -- emissions --------------------------------------------------------------------------
    def record_emission(self, kind: str, ref: str, pointers: Iterable[SourcePointer], *,
                        plan_hash: Optional[str] = None, ledger_bound: bool = False,
                        emitted_at: Optional[datetime] = None) -> int:
        """Record that `(kind, ref)` was emitted while citing `pointers`. Returns `emission_id`.

        This is the leg every pointer system omits, and the one that makes *"which answers did we
        give while we believed the wrong thing?"* a query instead of an archaeology project.

        `ledger_bound=True` marks a record the cascade may **never** rewrite or mark stale. A
        ledger entry records what was believed and confirmed at a past instant, and that stays
        true; the correction is a new entry citing the old one. It is a structural flag rather
        than a check on the `kind` string, so no vocabulary decides whether history is editable.

        Review finding 7: a SECOND call against an already-ledger-bound `(kind, ref)` used to be
        accepted whenever the `ledger_bound` flag matched, and would then add MORE
        `emission_pointer` rows to it — so a supposedly immutable historical citation set could
        grow after the fact, and a differing `plan_hash` on the retry was silently dropped with no
        error at all. Ledger-bound is now enforced as fully immutable once created: a second call
        against it is refused outright, whatever it carries, identical payload or not — a
        correction is a NEW `(kind, ref)` that cites this one (CLAUDE.md hard constraint 4). A
        non-ledger-bound emission may still accrue citations across calls (a materialised view can
        be built up incrementally); only the ledger-bound case is append-only-at-the-row level.
        """
        if not kind or not ref:
            raise ValueError("an emission needs both a kind and a ref to be referable at all")
        now = self.store._index_now(emitted_at)
        with self.store._lock:
            conn = self.store.conn
            try:
                row = conn.execute(
                    "SELECT emission_id, ledger_bound FROM emission WHERE kind = ? AND ref = ?",
                    (kind, ref)).fetchone()
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO emission(kind, ref, plan_hash, ledger_bound, emitted_at) "
                        "VALUES (?,?,?,?,?)", (kind, ref, plan_hash, int(ledger_bound), now))
                    emission_id = int(cur.lastrowid)
                else:
                    emission_id = int(row[0])
                    existing_bound = bool(row[1])
                    if existing_bound:
                        raise ValueError(
                            f"emission {kind}:{ref} is already recorded as a LEDGER-BOUND "
                            f"emission. A ledger-bound emission is immutable once created: its "
                            f"citation set and plan_hash cannot be extended or rewritten by a "
                            f"second call, even one with identical arguments. Record a correction "
                            f"as a new (kind, ref) that cites this one instead "
                            f"(CLAUDE.md hard constraint 4: the ledger is append-only).")
                    if existing_bound != ledger_bound:
                        raise ValueError(
                            f"emission {kind}:{ref} is already recorded with "
                            f"ledger_bound={existing_bound}; flipping it would make an append-only "
                            f"record editable (or the reverse) after the fact")
                cache: PointerCache = {}
                for pointer in pointers:
                    pointer_id = upsert_pointer(conn, pointer, now, cache)
                    conn.execute(
                        "INSERT OR IGNORE INTO emission_pointer(emission_id, pointer_id) "
                        "VALUES (?,?)", (emission_id, pointer_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return emission_id

    # -- verification bookkeeping -----------------------------------------------------------
    def record_verdict(self, pointer: SourcePointer, verdict: Any, *,
                       at: Optional[datetime] = None) -> Optional[int]:
        """Cache the last verdict on a pointer. Returns the `pointer_id`, or `None` if unknown.

        Deliberately does NOT index the pointer as a side effect: verifying a pointer nothing
        cites must not make the index claim it covers something it never saw.
        """
        text = normalise_verdict(verdict)
        pointer_id = self.pointer_id_of(pointer)
        if pointer_id is None:
            return None
        now = self.store._index_now(at)
        with self.store._lock:
            self.store.conn.execute(
                "UPDATE ptr_index SET last_verdict = ?, last_verified_at = ? WHERE pointer_id = ?",
                (text, now, pointer_id))
            self.store.conn.commit()
        return pointer_id

    # -- the cascade ------------------------------------------------------------------------
    def cascade(self, verdict: Any, *, source_id: Optional[str] = None,
                pointer: Optional[SourcePointer] = None,
                pointer_ids: Optional[Sequence[int]] = None,
                locator_keys: Optional[Iterable[str]] = None,
                apply: bool = True, at: Optional[datetime] = None) -> CascadeReport:
        """Answer *what is affected*, and — for an invalidating verdict — act on it.

        Three rules, from `SYSTEM_DESIGN` §5.3:

        * **the graph is INVALIDATED, never deleted.** Closing the system axis is the honest
          statement "we stopped believing this at T", and it leaves the old answer reachable by
          `as_of()` forever;
        * **emissions are MARKED stale, never rewritten.** A materialised dataset that silently
          changes under a model trained on it is worse than a stale one that says so;
        * **ledger-bound records are never touched.** They are returned, because a human needs to
          know which determinations rested on this — but they are historical fact, and a
          correction is a new entry.

        A non-invalidating verdict still returns the impact (that is useful on its own) and
        changes nothing.

        **A cascade over partial coverage still runs, and still says so.** Acting on what the
        index does know is right — leaving a known-bad value live because some *other* pointer is
        unindexed would be worse. What must never happen is the report reading as "handled":
        `CascadeReport.answer` is `UNKNOWN` in that case, and the counts below are a lower bound
        on the damage, not a total.

        Two review findings fixed here:

        * **Finding 2 (CRITICAL) — mutate the version the pointer actually cites, not "whichever
          version is open for this id".** The store does not forbid more than one open version of
          the same `entity_id` (`_open_version_pk`'s own docstring says so), so entity-id-only
          actions could close a version the drifted pointer never touched (over-invalidation) or
          leave the actually-cited version live (under-invalidation) whenever the id was torn.
          Every `AffectedEntity` with a known `version_pk` is now acted on through
          `close_node_version`/`close_edge_version`/`supersede_attr_version`, which take the exact
          `rowid_pk`. Only a legacy binding with `version_pk IS NULL` (pre-dates per-version
          tracking) still falls back to the entity-id-wide action, honestly, because that is all
          it was ever told.
        * **Finding 6 (HIGH) — one transaction, not one commit per row.** Every close/supersede
          used to commit independently, followed by a SEPARATE emission-staling transaction: an
          exception partway through (a malformed `attrs_json` row, say) left an arbitrarily
          partial cascade durably committed, with no way to tell afterwards which pointers it had
          actually reached. The whole mutation phase below — every close, every supersession,
          every stale mark, every verdict stamp — now runs under ONE lock with ONE commit at the
          end; any exception anywhere in it rolls back everything this call did and re-raises, so
          a cascade either fully lands or leaves no trace, never something in between.
        """
        text = normalise_verdict(verdict)
        impact = self.impact_of(source_id=source_id, pointer=pointer, pointer_ids=pointer_ids,
                                locator_keys=locator_keys)
        if text not in INVALIDATING_VERDICTS or not apply:
            return CascadeReport(verdict=text, impact=impact,
                                 ledger_entries_untouched=len(impact.ledger_bound))

        when = at or self.store.now()
        nodes_closed = edges_closed = attrs_superseded = 0
        # Sort so the outcome does not depend on row order: entity-level bindings (field IS NULL)
        # close the whole version, and doing that first makes the attribute work below a no-op
        # rather than a race between two orderings of the same set.
        entity_level = [e for e in impact.nodes + impact.edges if e.field is None and e.is_open]
        attribute_level = [e for e in impact.nodes + impact.edges
                           if e.field is not None and e.is_open]
        # Keyed by `version_pk` too (finding 2): two DIFFERENT open versions of the same
        # `entity_id` are two different rows to act on, not one. A legacy item (`version_pk`
        # `None`) still keys on the id alone, matching the entity-id-wide action it falls back to.
        closed_ids: set[tuple[str, str, Optional[int]]] = set()
        stale = 0
        stamp = self.store._index_now(when)

        with self.store._lock:
            conn = self.store.conn
            try:
                for item in entity_level:
                    key = (item.entity_kind, item.entity_id, item.version_pk)
                    if key in closed_ids:
                        continue
                    closed_ids.add(key)
                    if item.entity_kind == "node":
                        if item.version_pk is not None:
                            nodes_closed += self.store.close_node_version(
                                item.entity_id, item.version_pk, at=when, _commit=False)
                        else:
                            nodes_closed += self.store.close_node(
                                item.entity_id, at=when, _commit=False)
                    else:
                        if item.version_pk is not None:
                            edges_closed += self.store.close_edge_version(
                                item.entity_id, item.version_pk, at=when, _commit=False)
                        else:
                            edges_closed += self.store.close_edge(
                                item.entity_id, at=when, _commit=False)

                for item in sorted(attribute_level, key=lambda e: (e.entity_id, e.field or "")):
                    key = (item.entity_kind, item.entity_id, item.version_pk)
                    if key in closed_ids:
                        continue
                    if item.entity_kind == "node":
                        if item.version_pk is not None:
                            outcome = self.store.supersede_attr_version(
                                item.entity_id, item.field, item.version_pk, at=when,
                                _commit=False)
                        else:
                            outcome = self.store.supersede_attr(
                                item.entity_id, item.field, at=when, _commit=False)
                        if outcome == "superseded":
                            attrs_superseded += 1
                        elif outcome == "closed":
                            nodes_closed += 1
                            closed_ids.add(key)
                    else:
                        # An edge has no attribute-supersession path in this store (there is no
                        # `replace_edge`), and an edge bound to an attestation must not be
                        # rewritten at all. Closing the version is the conservative, honest
                        # action: it says we stopped believing the edge, and leaves it readable
                        # through `as_of()`.
                        if item.version_pk is not None:
                            edges_closed += self.store.close_edge_version(
                                item.entity_id, item.version_pk, at=when, _commit=False)
                        else:
                            edges_closed += self.store.close_edge(
                                item.entity_id, at=when, _commit=False)
                        closed_ids.add(key)

                for emission in impact.emissions:
                    if emission.stale_since is not None:
                        continue
                    conn.execute(
                        "UPDATE emission SET stale_since = ?, stale_cause = ? "
                        "WHERE emission_id = ?", (stamp, text, emission.emission_id))
                    stale += 1
                for pointer_id in impact.pointer_ids:
                    conn.execute(
                        "UPDATE ptr_index SET last_verdict = ?, last_verified_at = ? "
                        "WHERE pointer_id = ?", (text, stamp, pointer_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        # Re-read, so the report a caller acts on describes the graph AFTER the cascade.
        after = self.impact_of(source_id=source_id, pointer=pointer,
                               pointer_ids=tuple(impact.pointer_ids), locator_keys=locator_keys)
        return CascadeReport(
            verdict=text, impact=after, nodes_closed=nodes_closed, edges_closed=edges_closed,
            attributes_superseded=attrs_superseded, emissions_marked_stale=stale,
            ledger_entries_untouched=len(impact.ledger_bound))

    # -- backfill ---------------------------------------------------------------------------
    def backfill(self, *, batch_size: int = 5000,
                 on_progress: Optional[Callable[[BackfillReport], None]] = None
                 ) -> BackfillReport:
        """Build the index from the `sources` rows already in the store. **Idempotent.**

        Restartable by construction: it selects the rows that have no binding yet and commits per
        batch, so a run that dies half way leaves a partially built index that the next run
        continues — and `unindexed_pointer_count()` keeps telling the truth throughout, which is
        the property that matters. Re-running a complete backfill does no work.
        """
        report = BackfillReport()
        started = time.perf_counter()
        before = self.counts()
        entity_kinds: dict[str, str] = {}
        # Rowids are monotonic and every row this loop touches gets a binding, so the sweep only
        # ever moves forward. Without the watermark each batch re-scans the whole table looking
        # for the first unbound row, which turns the backfill quadratic in the size of the estate
        # — the exact shape of "we will index it later" never happening.
        watermark = 0

        while True:
            rows = self.store.fetchall(
                "SELECT s.rowid AS source_rowid, s.entity_id, s.field, s.version_pk, "
                "s.source_pointer_json FROM sources s "
                "LEFT JOIN ptr_binding b ON b.source_rowid = s.rowid "
                "WHERE b.source_rowid IS NULL AND s.rowid > ? ORDER BY s.rowid LIMIT ?",
                (watermark, batch_size))
            if not rows:
                break
            watermark = int(rows[-1]["source_rowid"])
            with self.store._lock:
                conn = self.store.conn
                cache: PointerCache = {}
                # One timestamp per batch, not per row: `first_seen` records when the index
                # learned of a pointer, and a batch is one such moment. Calling the clock 382k
                # times would be a measurable share of the backfill for no added truth.
                now = self.store._index_now(None)
                try:
                    for row in rows:
                        entity_id = row["entity_id"]
                        entity_kind = entity_kinds.get(entity_id)
                        if entity_kind is None:
                            entity_kind = self._entity_kind_of(entity_id)
                            entity_kinds[entity_id] = entity_kind
                        if entity_kind == "unknown":
                            # Recorded, never dropped: the binding row is what keeps the anti-join
                            # exact, and an orphan pointer is a real finding rather than a reason
                            # to leave a hole in the coverage claim.
                            report.unresolved_entities += 1
                        pointer = SourcePointer.model_validate_json(row["source_pointer_json"])
                        bind_pointer(conn, int(row["source_rowid"]), pointer,
                                     entity_kind=entity_kind, entity_id=entity_id,
                                     version_pk=row["version_pk"], field=row["field"],
                                     now=now, cache=cache)
                        report.rows_scanned += 1
                        report.bindings_written += 1
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            report.batches += 1
            if on_progress is not None:
                report.seconds = time.perf_counter() - started
                on_progress(report)

        after = self.counts()
        report.pointers_indexed = after["pointers"] - before["pointers"]
        report.parent_links = after["ancestry_links"] - before["ancestry_links"]
        report.unindexed_remaining = after["unindexed_pointers"]
        report.seconds = time.perf_counter() - started
        with self.store._lock:
            self.store.conn.execute(
                "INSERT INTO ptr_index_meta(key, value) VALUES ('last_backfill', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self.store._index_now(None),))
            self.store.conn.commit()
        return report

    def _entity_kind_of(self, entity_id: str) -> str:
        if self.store.fetchone("SELECT 1 FROM nodes WHERE id = ? LIMIT 1", (entity_id,)):
            return "node"
        if self.store.fetchone("SELECT 1 FROM edges WHERE id = ? LIMIT 1", (entity_id,)):
            return "edge"
        return "unknown"


__all__ = [
    "Answer", "AffectedEmission", "AffectedEntity", "BackfillReport", "CascadeReport",
    "ImpactReport", "INVALIDATING_VERDICTS", "KNOWN_VERDICTS", "ReverseIndex",
    "bind_pointer", "ensure_index_schema", "locator_key", "normalise_verdict", "parents_of",
    "pointer_identity", "pointer_identity_string", "upsert_pointer",
]
