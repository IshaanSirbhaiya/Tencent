"""`PlatformReader` — the materialiser's read path over the knowledge platform (ROADMAP Phase F).

`EstateReader` reads ONE source through the adapter's five-verb port. That estate is the old
finance fixture; the platform the product now runs on is three other stores:

* the **shared graph** (`databridge_graph.GraphStore`, BUILD_PLAN §5.1) — bi-temporal nodes and
  edges, a pointer per attribute;
* the **facts stores** (`var/facts/<source>_facts.sqlite`, S2) — rows copied read-only from a
  database, answered by `count_query` with one `query_cell` pointer per returned cell;
* the **passage stores** (`var/passages/*.sqlite`, §5.5) — screened chunks of captured text, each
  a `doc_span` over the capture store's `.txt` (§5.2).

This module reads all three through the SAME interface the builders already consume
(`read(entity, fields) -> EntityRead`, `request`, `decision_table`, `verify`), so `build_tabular`
and `build_chunk_corpus` run unchanged. `EstateReader` stays exactly as it was; which reader a
materialisation uses is the caller's configuration, never a branch in a builder.

**Entities are ontology kinds, not names.** A graph entity is a node kind the bound vocabulary
declares AND the store holds rows of; its fields are the attributes actually seen, plus four
structural columns and one link column per functional outgoing edge kind. Facts tables and
passage stores are named after their source. Nothing below knows what an invoice is.

**As-of semantics (graph).** A read returns every node version BELIEVED at the reader's system
instant ``S`` (``system_from <= S < system_to``) — the "read at latest system time" the temporal
contract prescribes for labels. Each row carries its own ``_system_from`` (when the platform
learned that version) and ``_valid_from`` (when it became true), so the builders' per-row cuts do
the rest: a feature is admitted only if ``_system_from <= T_d``, a label only if it matured by
``T_b - lag``. One consequence is stated rather than hidden: a version believed at ``T_d`` but
superseded between ``T_d`` and ``S`` is not a row, so that feature reads **Absent** (under-reports)
rather than substituting a belief the platform later withdrew. It never leaks.

**Every cell is a cell.** An attribute's value carries ITS OWN pointer from ``attr_sources`` (the
content-hash pointer the ingest wrote, preserved byte for byte). A value the store holds without a
pointer of its own (a legacy row) is ``Absent`` — the same narrowing the store's own
``_grounded_attrs_only`` applies — never cited with the entity's pointer. Structural columns
(``_id``, ``_valid_from``, ``_valid_to``, ``_system_from``, ``<edge>_id``) carry the pointer that
grounds that exact version or edge, the same device `shapes/chunked._literal` uses: they render
coordinates of the record the pointer names.

**Identity is never resolved and identifiers are suppressed by default.** For a node kind a pack
declares a SUBJECT, the declared identifiers, ``canonical_id``, ``label`` and ``_id`` (a node id's
slug is derived from identity) are ``Withheld`` unless the plan's ``policy.pii.direct_identifiers``
is ``retain``. A link column whose target is a subject node is withheld the same way. Facts columns
named like a declared identifier are withheld too. The vocabulary decides which names those are.

**O1b is honoured at the read.** A passage whose stored content differs from the capture bytes its
pointer cites (the per-chunk screen masked it) is a transformed disclosure; it is ``Withheld`` with
that reason instead of being emitted under a verbatim pointer.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from databridge_adapter import Verdict, canonical_json, parse_derivation, sha256_of
from databridge_core import Absent, GroundedValue, SourcePointer, Withheld
from databridge_core.provenance import SnapshotIdentity, WithholdingRecord

from .dataset import Cell
from .errors import SourceCannotReconstructInstant
from .plan import SourceSnapshot
from .reader import EntityRead
from .temporal import to_instant

#: `databridge/` — this file is databridge/packages/materialise/databridge_materialise/…
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]

#: The policy object that decides identifier suppression. Named, because a `WithholdingRecord`
#: nobody can attribute to a policy is not an audit record.
IDENTIFIER_POLICY_ID = "walnut.materialise.subject_identifiers"
IDENTIFIER_REASON = "subject_identifier_suppressed"
TRANSFORMED_POLICY_ID = "walnut.materialise.o1b_transformed_disclosure"
TRANSFORMED_REASON = "screen_masked_passage_o1b"

#: Structural columns every graph entity exposes. Leading underscore so they can never collide
#: with an attribute a pack writes.
GRAPH_KEY = "_id"
GRAPH_STRUCTURAL = ("_id", "_valid_from", "_valid_to", "_system_from")
FACTS_SYSTEM_TIME = "_extracted_at"
PASSAGE_FIELDS = ("chunk_id", "doc_id", "content", "locator", "title", "retrieved_at",
                  "ingested_at")
#: The passage fields that are COORDINATES of the chunk record the pointer names (R-021 Finding 4):
#: its ids, its locator, and the instant the platform learned it (the passage twin of a graph
#: row's `_system_from`). An explicit whitelist: anything else — `title`, `retrieved_at` — is
#: document metadata the chunk-body span does not say, so it is `Absent`, never a coordinate.
PASSAGE_COORDINATES = frozenset({"chunk_id", "doc_id", "locator", "ingested_at"})
#: Where each passage coordinate lives in the passage store, and the row it is keyed by
#: (R-021 round 2, Finding 1): the coordinate is cited with a `table_cell` pointer naming THAT
#: record, and the pointer check re-reads it and compares exactly.
_PASSAGE_RECORD = {"chunk_id": ("chunks", "chunk_id"), "locator": ("chunks", "chunk_id"),
                   "doc_id": ("documents", "doc_id"), "ingested_at": ("documents", "doc_id")}
#: Graph coordinates a `table_cell` pointer may name: the version row's own columns, and an
#: edge version's destination. Nothing else is re-readable through this door.
_GRAPH_RECORD_COLUMNS = {"nodes": frozenset({"id", "valid_from", "valid_to", "system_from"}),
                         "edges": frozenset({"dst_id"})}
#: A capture digest is a lowercase SHA-256 (BUILD_PLAN §5.2). Validated before any path use.
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
#: Files a passage directory legitimately holds beside its stores: not sources, not exclusions.
_PASSAGE_SIDECAR_SUFFIXES = (".json", ".lock", ".tmp", "-wal", "-shm", "-journal")

#: Minimum mean length (characters) for a string attribute to be offered as a chunk-corpus text
#: field. A threshold, not a vocabulary: short strings are names and codes, not passages.
TEXT_FIELD_MIN_MEAN_CHARS = 80


def _env_dir(name: str, *default: str) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else DATABRIDGE_ROOT.joinpath(*default)


def facts_dir() -> Path:
    return _env_dir("WALNUT_FACTS_DIR", "var", "facts")


def passages_dir() -> Path:
    return _env_dir("WALNUT_PASSAGES_DIR", "var", "passages")


def capture_dir() -> Path:
    return _env_dir("WALNUT_CAPTURE_DIR", "var", "capture")


def _iso(dt: datetime) -> str:
    """The graph store's canonical fixed-width UTC form, so comparisons are lexicographic."""
    if dt.tzinfo is None:
        raise ValueError(f"naive instant {dt!r}: an as-of cut needs a timezone")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sqlite_digest(path: Path) -> str:
    """A LOGICAL content hash of one SQLite store: its schema and every row of every table, read
    inside ONE read transaction (R-021 round 2, Finding 2).

    Hashing the main file's bytes misses committed rows still in the ``-wal`` file (a WAL-mode
    store changes without its main file changing) and, conversely, changes when a checkpoint
    moves unchanged rows from the WAL into the main file. What a read SEES is the committed
    logical state, so that is what a pin hashes. Virtual tables are skipped: their content lives
    in their shadow tables, which are ordinary tables and are hashed.
    """
    conn = _ro(path)
    try:
        conn.execute("BEGIN")
        h = hashlib.sha256()
        master = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                              "ORDER BY type, name").fetchall()
        h.update(repr([tuple(r) for r in master]).encode("utf-8"))
        for r in master:
            name, sql = r["name"], (r["sql"] or "")
            if (r["type"] != "table" or name.startswith("sqlite_")
                    or sql.upper().startswith("CREATE VIRTUAL")):
                continue
            q = '"' + name.replace('"', '""') + '"'
            order = ("rowid" if "WITHOUT ROWID" not in sql.upper() else
                     ", ".join(str(i + 1) for i in range(
                         len(conn.execute(f"PRAGMA table_info({q})").fetchall()))))
            h.update(b"\x00table\x00" + name.encode("utf-8"))
            for row in conn.execute(f"SELECT * FROM {q} ORDER BY {order}"):
                h.update(repr(tuple(row)).encode("utf-8"))
                h.update(b"\x1e")
        conn.execute("COMMIT")
        return h.hexdigest()
    finally:
        conn.close()


def _norm_name(name: str) -> str:
    """A column/identifier name compared without case or separators (R-021 round 2, F7):
    ``Email_Address``, ``email-address`` and ``emailaddress`` are one name."""
    return re.sub(r"[^0-9a-z]", "", str(name).casefold())


# ---------------------------------------------------------------------------------------
# What the reader can read
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PlatformEntity:
    """One readable entity, with the axes it can answer. Computed from the stores, never listed."""

    name: str
    origin: str                                  # "graph" | "facts" | "passages"
    source_id: str
    fields: tuple[str, ...]
    key: tuple[str, ...]
    valid_time_field: Optional[str] = None
    system_time_field: Optional[str] = None
    label_candidates: tuple[str, ...] = ()
    feature_candidates: tuple[str, ...] = ()
    text_field: Optional[str] = None
    rows: int = 0
    subject: bool = False
    #: fields withheld under `identifiers="suppress"`, with the vocabulary reason
    identifier_fields: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"name": self.name, "origin": self.origin, "source_id": self.source_id,
                "fields": list(self.fields), "key": list(self.key),
                "valid_time_field": self.valid_time_field,
                "system_time_field": self.system_time_field,
                "label_candidates": list(self.label_candidates),
                "feature_candidates": list(self.feature_candidates),
                "text_field": self.text_field, "rows": self.rows, "subject": self.subject,
                "identifier_fields": list(self.identifier_fields)}


@dataclass(frozen=True)
class PlatformEntityRead(EntityRead):
    """`EntityRead` for a platform read. There is no `AdapterResult` behind it, so the snapshot
    is carried directly; every other method (`value`, `row_id`) is the base class's own."""

    snapshot_identity: Optional[SnapshotIdentity] = None
    origin: str = ""

    @property
    def snapshot(self):                                         # type: ignore[override]
        return self.snapshot_identity


@dataclass(frozen=True)
class PlatformRequest:
    """What `request()` returns. Carries no capability grant: the platform stores have no
    `PolicyEngine`, so `decision_table()` answers `None` and the O1b screen runs at emission."""

    entity: str
    fields: tuple[str, ...]
    key: tuple[str, ...]
    system_as_of: str

    @property
    def read_hash(self) -> str:
        return sha256_of(canonical_json({"entity": self.entity, "fields": list(self.fields),
                                         "key": list(self.key), "as_of": self.system_as_of}))


class PlatformReader:
    """Reads the shared graph, the facts stores and the passage stores as of one system instant.

    ``system_as_of`` is the instant the graph is read at (``T_b`` for a tabular plan,
    ``corpus_as_of`` for a corpus); ``identifiers`` is the plan's ``policy.pii.
    direct_identifiers``; ``principal`` and ``purpose`` are stamped on every withholding record.
    """

    def __init__(self, *, store: Any = None, vocabulary: Any = None,
                 facts_sources: Optional[Sequence[str]] = None,
                 facts_root: Optional[Path] = None,
                 passages_root: Optional[Path] = None,
                 capture_root: Optional[Path] = None,
                 system_as_of: Optional[datetime] = None,
                 identifiers: str = "suppress",
                 principal: str = "materialiser",
                 purpose: str = "",
                 source_id: str = "walnut-platform",
                 max_rows: int = 200_000) -> None:
        if identifiers not in ("suppress", "retain"):
            raise ValueError(f"identifiers must be 'suppress' or 'retain', not {identifiers!r}")
        if not str(principal or "").strip():
            raise ValueError("a platform read needs a principal: a withholding record must name "
                             "who was denied")
        self._store = store
        self._vocabulary = vocabulary
        self._facts_sources = tuple(facts_sources) if facts_sources is not None else None
        self._facts_root = Path(facts_root) if facts_root else None
        self._passages_root = Path(passages_root) if passages_root else None
        self._capture_root = Path(capture_root) if capture_root else None
        self._as_of = system_as_of or datetime.now(timezone.utc).replace(microsecond=0)
        if self._as_of.tzinfo is None:
            raise ValueError("system_as_of must be timezone-aware")
        self._identifiers = identifiers
        self._principal = str(principal)
        self._purpose = purpose or None
        self._source_id = source_id
        self._max_rows = max_rows
        self._entities: Optional[dict[str, PlatformEntity]] = None
        self._excluded: list[dict] = []
        self._cache: dict[tuple, PlatformEntityRead] = {}
        self._queries: dict[str, tuple[str, str]] = {}           # query_hash -> (source, sql)
        #: every facts answer this reader was given (each carries the query artefact its pointers
        #: need; `count_query` is a pure read, R-029 TIDY2). A publisher persists the shipped ones.
        self.answers: list = []
        self._reruns: dict[str, Any] = {}
        self._capture_text: dict[str, Optional[str]] = {}
        #: digest -> the capture's digest-verified `.json` (R-021 round 2, Finding 4)
        self._capture_meta: dict[str, dict] = {}
        #: digest -> the captured TABLE snapshot, parsed from the very bytes that authenticated
        #: the capture's text (E2E run 3, P-03); only a snapshot whose `.txt` re-derives lands here
        self._capture_tables: dict[str, dict] = {}
        #: `(entity, column)` withheld because its VALUES match the deployment's contact screen
        self._screened_identifiers: set[tuple[str, str]] = set()
        #: passage rows not read: their capture's identity does not name this document
        self.passages_unbound = 0
        #: digest -> why its bytes could not be used (R-021 Findings 4 and 9)
        self._capture_failures: dict[str, str] = {}
        self.transformed_withheld = 0
        #: passage rows not read: capture bytes missing or failing verification (Finding 4)
        self.passages_unverifiable = 0
        #: passage rows not read: ingested after the read instant, or with no ingest instant
        self.passages_after_cut = 0

    # --- identity -----------------------------------------------------------------------
    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def principal(self) -> str:
        return self._principal

    @property
    def notes(self) -> list[str]:
        """What the reads found that a receipt must say. Computed, so it is never stale."""
        out = []
        if self.transformed_withheld:
            out.append(
                f"{self.transformed_withheld} passage read(s) were withheld as transformed "
                f"disclosures (spine O1b): the per-chunk screen masked the text, so the stored "
                f"bytes are not the bytes the pointer cites")
        if self.passages_unverifiable:
            out.append(
                f"{self.passages_unverifiable} passage(s) were excluded because their capture "
                f"bytes are missing or fail verification (digest -> reason: "
                f"{dict(sorted(self._capture_failures.items()))}); a value whose cited bytes "
                f"cannot be re-read is not shipped")
        if self.passages_after_cut:
            out.append(
                f"{self.passages_after_cut} passage(s) were not read: ingested after the read "
                f"instant {self._as_of.isoformat()} (or with no ingest instant). The passage "
                f"store keeps only its latest version of a document, so a belief it replaced "
                f"cannot be reconstructed; the corpus under-reports rather than leaks")
        if self.passages_unbound:
            out.append(
                f"{self.passages_unbound} passage(s) were excluded because their capture's "
                f"digest-verified metadata does not name that document (source and path do not "
                f"hash to its doc_id): the text is not evidence FOR that document")
        if self._screened_identifiers:
            out.append(
                f"facts column(s) {sorted(f'{e}.{c}' for e, c in self._screened_identifiers)} "
                f"were withheld as direct identifiers: their stored values match the "
                f"deployment's contact-identifier screen (screen.yaml redaction rules)")
        return out

    @property
    def system_as_of(self) -> datetime:
        return self._as_of

    @property
    def identifiers(self) -> str:
        return self._identifiers

    def vocabulary(self):
        if self._vocabulary is None:
            from databridge_graph.packs import active_vocabulary

            self._vocabulary = active_vocabulary()
        return self._vocabulary

    def _roots(self) -> tuple[Path, Path, Path]:
        return (self._facts_root or facts_dir(), self._passages_root or passages_dir(),
                self._capture_root or capture_dir())

    # --- discovery ----------------------------------------------------------------------
    def entities(self) -> dict[str, PlatformEntity]:
        if self._entities is None:
            # Every claim from every origin is collected FIRST (R-021 round 2, Finding 8): a name
            # two claimants share is offered to neither, and each claimant is recorded. Keeping
            # the later claimant would silently hide the earlier one — and which origin wins
            # would be an accident of discovery order.
            claims: dict[str, list[PlatformEntity]] = {}
            graph = self._graph_entities() if self._store is not None else []
            for e in [*graph, *self._facts_entities(), *self._passage_entities()]:
                claims.setdefault(e.name, []).append(e)
            found: dict[str, PlatformEntity] = {}
            for name, cs in claims.items():
                if len(cs) == 1:
                    found[name] = cs[0]
                    continue
                who = sorted(_claimant(c) for c in cs)
                for e in cs:
                    self._excluded.append({
                        "entity": name, "origin": e.origin, "claimant": _claimant(e),
                        **({"store": e.detail["path"]} if "path" in e.detail else {}),
                        "reason": f"duplicate entity name: claimed by {who}; not offered until "
                                  f"one is removed or renamed"})
            self._entities = dict(sorted(found.items()))
        return self._entities

    def excluded(self) -> list[dict]:
        """What discovery saw and could not offer, with the reason. Never silently dropped."""
        self.entities()
        return list(self._excluded)

    def entity(self, name: str) -> PlatformEntity:
        try:
            return self.entities()[name]
        except KeyError:
            raise KeyError(
                f"the platform does not describe entity {name!r}; described: "
                f"{sorted(self.entities())}") from None

    def key_for(self, entity: str) -> tuple[str, ...]:
        return self.entity(entity).key

    # --- the builder-facing interface (same as EstateReader) ----------------------------
    def request(self, entity: str, fields: Iterable[str], *,
                order_by: Sequence[str] = ()) -> PlatformRequest:
        e = self.entity(entity)
        projected = tuple(dict.fromkeys([*fields, *e.key]))
        unknown = [f for f in projected if f not in e.fields]
        if unknown:
            raise KeyError(
                f"entity {entity!r} has no field(s) {unknown}; it describes {list(e.fields)}. "
                f"A plan naming a field the source never recorded is refused, not read as empty")
        return PlatformRequest(entity=entity, fields=projected, key=e.key,
                               system_as_of=_iso(self._as_of))

    def decision_table(self, req: PlatformRequest):
        """No `PolicyEngine` sits over the platform stores; identifier suppression happens in the
        read itself and the O1b screen runs at emission (`executor.screen_transformed_cells`)."""
        return None

    def read(self, entity: str, fields: Iterable[str], *,
             order_by: Sequence[str] = ()) -> PlatformEntityRead:
        req = self.request(entity, fields, order_by=order_by)
        cache_key = (entity, req.read_hash)
        if cache_key in self._cache:
            return self._cache[cache_key]
        e = self.entity(entity)
        if e.origin == "graph":
            read = self._read_graph(e, req)
        elif e.origin == "facts":
            read = self._read_facts(e, req)
        else:
            read = self._read_passages(e, req)
        self._cache[cache_key] = read
        return read

    # --- snapshots ----------------------------------------------------------------------
    def snapshots(self) -> tuple[SourceSnapshot, ...]:
        """One pin per store the catalogue reads, content-hashed. Part of `plan_hash` on the agent
        path, so a changed store is a different plan rather than silently different bytes."""
        out: list[SourceSnapshot] = []
        if self._store is not None:
            out.append(SourceSnapshot(source_id=self._graph_source_id(), tier="T1",
                                      as_of=self._as_of, content_hash=self._graph_digest(),
                                      schema_hash=self._vocab_digest()))
        facts_root, pass_root, _ = self._roots()
        # Logical digests, not file bytes (R-021 round 2, Finding 2): committed rows still in a
        # store's WAL are part of what a read sees, so they are part of the pin.
        for source in self._facts_source_ids():
            path = facts_root / f"{source}_facts.sqlite"
            out.append(SourceSnapshot(source_id=f"facts:{source}", tier="T1", as_of=self._as_of,
                                      content_hash=_sqlite_digest(path)))
        for path in self._passage_store_paths():
            out.append(SourceSnapshot(source_id=f"passages:{path.stem}", tier="T1",
                                      as_of=self._as_of, content_hash=_sqlite_digest(path)))
        return tuple(out)

    def sources_for(self, entities: Iterable[str]) -> dict[str, str]:
        """``{entity: source_id}`` — the pin id of the store each named entity is read from
        (R-021 round 2, Finding 3). An entity the platform does not describe raises `KeyError`
        exactly as `read` would."""
        return {name: self.entity(name).source_id for name in dict.fromkeys(entities)}

    # ==================================================================================
    # graph
    # ==================================================================================
    def _graph_source_id(self) -> str:
        path = getattr(self._store, "path", ":memory:")
        return "graph:" + (Path(path).name if path and path != ":memory:" else "memory")

    def _vocab_digest(self) -> str:
        v = self.vocabulary()
        return sha256_of(canonical_json({"packs": list(v.packs),
                                         "nodes": sorted(v.node_kinds),
                                         "edges": sorted(v.edge_kinds),
                                         "subjects": sorted(v.subject_kinds)}))

    def _graph_digest(self) -> str:
        """Content hash of the graph AS BELIEVED AT `S`: every node and edge version believed at
        `S` and the pointer rows that ground them. O(rows), streamed.

        Only the as-of view is hashed (R-021 Finding 3 made these pins binding): a version
        superseded after `S` (its `system_to` set later) or a node ingested after `S` does not
        change what a read at `S` returns, so it must not change the pin either — otherwise every
        later ingest would refuse every pinned plan while the bytes it reads are unchanged.
        """
        h = hashlib.sha256()
        ids: set = set()
        versions: set = set()
        for table in ("nodes", "edges"):
            for row in self._believed(table):
                ids.add(row["id"])
                versions.add((row["id"], row["rowid_pk"]))
                h.update(canonical_json({k: row[k] for k in row.keys()
                                         if k not in ("attestation_json", "system_to")}
                                        ).encode("utf-8"))
        for row in self._store.fetchall(
                "SELECT entity_id, field, version_pk, source_pointer_json FROM sources "
                "ORDER BY rowid"):
            vpk = row["version_pk"]
            if (vpk is None and row["entity_id"] in ids) or (row["entity_id"], vpk) in versions:
                h.update(canonical_json(list(row)).encode("utf-8"))
        return h.hexdigest()

    def _believed(self, table: str, where: str = "", params: tuple = ()) -> list[sqlite3.Row]:
        s = _iso(self._as_of)
        sql = (f"SELECT * FROM {table} WHERE system_from <= ? AND (system_to IS NULL OR "
               f"system_to > ?)" + (f" AND {where}" if where else "") + " ORDER BY id, rowid_pk")
        return self._store.fetchall(sql, (s, s, *params))

    def _pointer_rows(self, table: str, kind_filter: str, params: tuple) -> dict:
        """`{entity_id: {version_pk|None: {field|None: [pointer_json...]}}}` in one query."""
        rows = self._store.fetchall(
            f"SELECT s.entity_id, s.field, s.version_pk, s.source_pointer_json FROM sources s "
            f"WHERE s.entity_id IN (SELECT id FROM {table} WHERE {kind_filter}) ORDER BY s.rowid",
            params)
        out: dict = {}
        for r in rows:
            out.setdefault(r["entity_id"], {}).setdefault(r["version_pk"], {}).setdefault(
                r["field"], []).append(r["source_pointer_json"])
        return out

    @staticmethod
    def _version_pointers(by_version: Mapping, version_pk: int) -> tuple[list[SourcePointer],
                                                                          dict[str, SourcePointer]]:
        """The store's own scoping rule (`GraphStore._pointers`): this version's rows, plus
        legacy `version_pk IS NULL` rows; for an attribute the exact version wins."""
        exact = by_version.get(version_pk, {})
        legacy = by_version.get(None, {})
        entity = [SourcePointer.model_validate_json(j)
                  for j in (exact.get(None, []) + legacy.get(None, []))]
        entity.sort(key=lambda p: p.model_dump_json())
        attrs: dict[str, SourcePointer] = {}
        for fld in set(exact) | set(legacy):
            if fld is None:
                continue
            chosen = (exact.get(fld) or legacy.get(fld) or [None])[0]
            if chosen is not None:
                attrs[fld] = SourcePointer.model_validate_json(chosen)
        return entity, attrs

    def _subject_identifier_fields(self, kind: str) -> tuple[str, ...]:
        v = self.vocabulary()
        if not v.is_subject(kind):
            return ()
        return tuple(dict.fromkeys((GRAPH_KEY, "label", "canonical_id",
                                    *v.identifiers_for(kind))))

    def _all_identifier_names(self) -> frozenset[str]:
        v = self.vocabulary()
        return frozenset(i for s in v.subject_kinds.values() for i in s.identifiers)

    def _graph_entities(self) -> list[PlatformEntity]:
        v = self.vocabulary()
        out: list[PlatformEntity] = []
        present = {r["kind"]: r["n"] for r in self._store.fetchall(
            "SELECT kind, COUNT(*) n FROM nodes WHERE system_from <= ? AND (system_to IS NULL OR "
            "system_to > ?) GROUP BY kind", (_iso(self._as_of), _iso(self._as_of)))}
        for kind in sorted(present):
            if kind not in v.node_kinds:
                self._excluded.append({"entity": kind, "origin": "graph",
                                       "reason": "node kind is not in the bound vocabulary "
                                                 "(deprecated or from an unbound pack)"})
        for kind in sorted(v.node_kinds):
            n = present.get(kind, 0)
            if not n:
                continue
            rows = self._believed("nodes", "kind = ?", (kind,))
            attr_keys: dict[str, list[Any]] = {}
            has_canonical = False
            for r in rows:
                for k, val in json.loads(r["attrs_json"] or "{}").items():
                    attr_keys.setdefault(k, []).append(val)
                has_canonical = has_canonical or r["canonical_id"] is not None
            links = self._functional_links(kind)
            fields = list(GRAPH_STRUCTURAL) + [f"{e}_id" for e in links] + ["label"]
            if has_canonical:
                fields.append("canonical_id")
            reserved = set(fields)
            fields += [k for k in sorted(attr_keys) if k not in reserved]
            label_c, feature_c, text_f = _candidates(
                {k: vals for k, vals in attr_keys.items() if k not in reserved})
            ident = self._subject_identifier_fields(kind)
            ident = tuple(f for f in ident if f in fields)
            if links:
                for e, target_kinds in links.items():
                    if any(v.is_subject(t) for t in target_kinds):
                        ident += (f"{e}_id",)
            out.append(PlatformEntity(
                name=kind, origin="graph", source_id=self._graph_source_id(),
                fields=tuple(fields), key=(GRAPH_KEY,), valid_time_field="_valid_from",
                system_time_field="_system_from", label_candidates=label_c,
                feature_candidates=feature_c, text_field=text_f, rows=len(rows),
                subject=v.is_subject(kind), identifier_fields=ident,
                detail={"kind": kind, "links": {e: sorted(t) for e, t in links.items()}}))
        return out

    def _functional_links(self, kind: str) -> dict[str, set[str]]:
        """Outgoing edge kinds that are FUNCTIONAL for this node kind at `S` — at most one
        believed edge per source node — with the target kinds seen. A many-valued edge cannot be a
        column without choosing one of its values, so it is not offered."""
        v = self.vocabulary()
        rows = self._edge_rows_from_kind(kind)
        per: dict[tuple[str, str], int] = {}
        targets: dict[str, set[str]] = {}
        for r in rows:
            if r["kind"] not in v.edge_kinds:
                continue
            per[(r["kind"], r["src_id"])] = per.get((r["kind"], r["src_id"]), 0) + 1
            targets.setdefault(r["kind"], set()).add(r["dst_kind"] or "")
        out: dict[str, set[str]] = {}
        for ek in sorted(targets):
            if all(n <= 1 for (k, _), n in per.items() if k == ek):
                out[ek] = targets[ek]
            else:
                self._excluded.append({"entity": kind, "origin": "graph", "field": f"{ek}_id",
                                       "reason": f"edge kind {ek!r} is many-valued for {kind!r}; "
                                                 f"not offered as a column"})
        return out

    def _edge_rows_from_kind(self, kind: str) -> list[sqlite3.Row]:
        s = _iso(self._as_of)
        return self._store.fetchall(
            "SELECT e.*, (SELECT n2.kind FROM nodes n2 WHERE n2.id = e.dst_id ORDER BY "
            "n2.rowid_pk DESC LIMIT 1) AS dst_kind FROM edges e "
            "WHERE e.system_from <= ? AND (e.system_to IS NULL OR e.system_to > ?) "
            "AND e.src_id IN (SELECT id FROM nodes WHERE kind = ?) ORDER BY e.src_id, e.rowid_pk",
            (s, s, kind))

    def _coordinate(self, value: Any, *, source_id: str, table: str, column: str, row_id: Any,
                    trust: Any) -> GroundedValue:
        """A coordinate cell (an id, a version instant, an edge's destination, a passage's
        locator) cited with a `table_cell` pointer naming the exact STORE RECORD it is a column
        of — never with the pointer of the document the record was built from.

        R-021 round 2, Finding 1: a coordinate carrying its document's pointer was "verified" by
        asking only that the document resolved, so any value could ride on it. Now the pointer
        check re-reads ``table.column`` at ``row_id`` and compares EXACTLY (`cell_check`); a
        version row is immutable in the bi-temporal store, so the citation stays true.
        """
        pointer = SourcePointer(source_id=source_id, kind="table_cell", table=table,
                                column=column, row_id=str(row_id), trust=trust)
        return GroundedValue(value=value, pointer=pointer, confidence=1.0)

    def _withheld(self, entity: str, fld: str, *, policy_id: str = IDENTIFIER_POLICY_ID,
                  reason: str = IDENTIFIER_REASON) -> Withheld:
        return Withheld(record=WithholdingRecord(
            field_path=f"{entity}.{fld}", policy_id=policy_id, reason_code=reason,
            principal=self._principal, purpose=self._purpose, appealable=True))

    def _suppressed(self, e: PlatformEntity, fld: str) -> bool:
        return self._identifiers == "suppress" and fld in e.identifier_fields

    def _read_graph(self, e: PlatformEntity, req: PlatformRequest) -> PlatformEntityRead:
        kind = e.detail["kind"]
        rows = self._believed("nodes", "kind = ?", (kind,))
        pointers = self._pointer_rows("nodes", "kind = ?", (kind,))
        links = {f"{ek}_id": ek for ek in e.detail.get("links", {})}
        wanted_links = {f: ek for f, ek in links.items() if f in req.fields}
        edge_by_src: dict[tuple[str, str], sqlite3.Row] = {}
        edge_ptrs: dict = {}
        if wanted_links:
            for r in self._edge_rows_from_kind(kind):
                if r["kind"] in wanted_links.values():
                    edge_by_src[(r["kind"], r["src_id"])] = r
            edge_ptrs = self._pointer_rows("edges", "src_id IN (SELECT id FROM nodes WHERE "
                                                    "kind = ?)", (kind,))
        snapshot = SnapshotIdentity(
            source_id=e.source_id, as_of=self._as_of,
            content_hash=sha256_of(canonical_json([
                [r["rowid_pk"], r["id"], r["attrs_json"], r["valid_from"], r["valid_to"],
                 r["system_from"], r["system_to"]] for r in rows])),
            schema_hash=sha256_of(canonical_json(list(e.fields))),
            query_hash=req.read_hash)
        out_rows: list[dict[str, Cell]] = []
        for r in rows:
            entity_ptrs, attr_ptrs = self._version_pointers(pointers.get(r["id"], {}),
                                                            r["rowid_pk"])
            anchor = entity_ptrs[0] if entity_ptrs else None
            attrs = json.loads(r["attrs_json"] or "{}")
            row: dict[str, Cell] = {}
            for fld in req.fields:
                path = f"{e.name}.{fld}"
                if self._suppressed(e, fld):
                    row[fld] = self._withheld(e.name, fld)
                    continue
                if fld in GRAPH_STRUCTURAL:
                    col = fld.lstrip("_") if fld != GRAPH_KEY else "id"
                    value = r[col]
                    # A version nothing grounds is not cited at all (anchor), even by its record.
                    row[fld] = (self._coordinate(value, source_id=e.source_id, table="nodes",
                                                 column=col, row_id=r["rowid_pk"],
                                                 trust=anchor.trust)
                                if value is not None and anchor is not None
                                else Absent(field_path=path, snapshot=snapshot))
                elif fld in wanted_links:
                    edge = edge_by_src.get((wanted_links[fld], r["id"]))
                    if edge is None:
                        row[fld] = Absent(field_path=path, snapshot=snapshot)
                        continue
                    eptrs, _ = self._version_pointers(edge_ptrs.get(edge["id"], {}),
                                                      edge["rowid_pk"])
                    row[fld] = (self._coordinate(edge["dst_id"], source_id=e.source_id,
                                                 table="edges", column="dst_id",
                                                 row_id=edge["rowid_pk"], trust=eptrs[0].trust)
                                if eptrs else Absent(field_path=path, snapshot=snapshot))
                else:
                    value = (r["label"] if fld == "label" else r["canonical_id"]
                             if fld == "canonical_id" else attrs.get(fld))
                    ptr = attr_ptrs.get(fld)
                    # A value with no pointer OF ITS OWN is not cited with the entity's pointer —
                    # that is the laundering the per-attribute wall exists to stop.
                    row[fld] = (GroundedValue(value=value, pointer=ptr, confidence=1.0)
                                if value is not None and ptr is not None
                                else Absent(field_path=path, snapshot=snapshot))
            out_rows.append(row)
        return PlatformEntityRead(entity=e.name, columns=req.fields, key=e.key,
                                  rows=tuple(out_rows), result=None, decisions=None,
                                  snapshot_identity=snapshot, origin="graph")

    # ==================================================================================
    # facts
    # ==================================================================================
    def _facts_source_ids(self, *, record: bool = False) -> list[str]:
        root = self._roots()[0]
        if self._facts_sources is not None:
            present = []
            for s in self._facts_sources:
                store = root / f"{s}_facts.sqlite"
                if store.is_file():
                    present.append(s)
                elif record:
                    # A configured source that vanished is said, not skipped (Finding 11).
                    self._excluded.append({"entity": f"facts.{s}", "origin": "facts",
                                           "reason": f"configured facts source has no store "
                                                     f"at {store}"})
            return present
        if not root.is_dir():
            return []
        return sorted(p.name[: -len("_facts.sqlite")] for p in root.glob("*_facts.sqlite")
                      if p.is_file())

    def _count_query(self, source: str, sql: str):
        """One read-only facts query against THIS reader's facts root.

        R-021 round 2, Finding 9: the previous form pointed the process-global
        ``WALNUT_FACTS_DIR`` at this reader's root for the call, so two concurrent readers over
        different roots could each query the other's store. Nothing here touches the environment.
        When `count_query` accepts an explicit directory it is called with it; until then the
        same statement runs through `_facts_query_at`, which applies `count_query`'s own guards
        (its statement check, read-only open, `query_only`, its authorizer, the manifest hash
        check) and builds the same `CountAnswer` and pointers, against an explicit path.
        """
        from databridge_mcp_ingest.tabular import count as cq

        root = self._roots()[0]
        params = inspect.signature(cq.count_query).parameters
        for name in ("facts_dir", "root"):
            if name in params:
                ans = cq.count_query(source, sql, max_rows=self._max_rows, **{name: root})
                self.answers.append(ans)
                return ans
        ans = _facts_query_at(root, source, sql, max_rows=self._max_rows)
        self.answers.append(ans)
        return ans

    def _facts_entities(self) -> list[PlatformEntity]:
        out: list[PlatformEntity] = []
        idents = frozenset(_norm_name(i) for i in self._all_identifier_names())
        for source in self._facts_source_ids(record=True):
            try:
                tables = self._count_query(
                    source, "SELECT facts_table, mode, rows_in_facts FROM _facts_tables "
                            "WHERE facts_table IS NOT NULL ORDER BY facts_table").rows
                cols = self._count_query(
                    source, "SELECT facts_table, column_name, class, prose, distinct_n, "
                            "non_null FROM _facts_columns ORDER BY facts_table, rowid").rows
            except Exception as exc:                             # noqa: BLE001
                self._excluded.append({"entity": f"facts.{source}", "origin": "facts",
                                       "reason": f"facts store unreadable: {exc}"})
                continue
            by_table: dict[str, list] = {}
            for t, c, cls, prose, distinct, non_null in cols:
                by_table.setdefault(t, []).append((c, cls, bool(prose), distinct, non_null))
            for table, mode, n in tables:
                name = f"facts.{source}.{table}"
                if mode != "rows":
                    self._excluded.append({"entity": name, "origin": "facts",
                                           "reason": f"facts table mode {mode!r}: aggregated or "
                                                     f"count-only tables have no rows to cite"})
                    continue
                columns = by_table.get(table, [])
                key = next((c for c, _, _, distinct, nn in columns
                            if n and distinct == n and nn == n), None)
                if key is None:
                    self._excluded.append({"entity": name, "origin": "facts",
                                           "reason": "no column is unique and non-null in every "
                                                     "row, so no row can be identified"})
                    continue
                names = [c for c, *_ in columns]
                label_c = tuple(c for c, cls, *_ in columns if cls == "boolean" and c != key)
                feature_c = tuple(c for c, cls, *_ in columns if cls == "numeric" and c != key)
                prose_c = [c for c, _, prose, *_ in columns if prose]
                out.append(PlatformEntity(
                    name=name, origin="facts", source_id=f"facts:{source}",
                    fields=(key, *[c for c in names if c != key], FACTS_SYSTEM_TIME),
                    key=(key,), system_time_field=FACTS_SYSTEM_TIME,
                    label_candidates=label_c, feature_candidates=feature_c,
                    text_field=prose_c[0] if prose_c else None, rows=int(n or 0),
                    # Case- and separator-insensitive (R-021 round 2, Finding 7): `Email_Address`
                    # is the declared identifier `email_address`.
                    identifier_fields=tuple(c for c in names if _norm_name(c) in idents),
                    detail={"source": source, "table": table,
                            "text_columns": tuple(c for c, cls, *_ in columns
                                                  if cls in ("text", "other"))}))
        return out

    def _facts_extracted_at(self, source: str) -> Optional[datetime]:
        try:
            rows = self._count_query(
                source, "SELECT value FROM _facts_meta WHERE key = 'extracted_at'").rows
        except Exception:                                        # noqa: BLE001
            return None
        return to_instant(rows[0][0]) if rows else None

    def _read_facts(self, e: PlatformEntity, req: PlatformRequest) -> PlatformEntityRead:
        source, table = e.detail["source"], e.detail["table"]
        # A facts store is ONE whole-file extraction with no history (R-021 Finding 10). It is
        # the platform's belief at S only if it was extracted at or before S; extracted later, it
        # holds a belief the platform did not have at S and cannot reconstruct the one it did.
        extracted = self._facts_extracted_at(source)
        if extracted is None or extracted > self._as_of:
            raise SourceCannotReconstructInstant(
                f"{e.name}: the facts store was extracted at "
                f"{extracted.isoformat() if extracted else 'an unrecorded instant'} and this read "
                f"is as of {self._as_of.isoformat()}. A whole-file store holds only its latest "
                f"extraction, so it cannot say what was believed at an earlier instant; refused "
                f"rather than presented as a historical snapshot")

        def q(name: str) -> str:
            return '"' + name.replace('"', '""') + '"'

        exprs = []
        for fld in req.fields:
            if fld == FACTS_SYSTEM_TIME:
                exprs.append("(SELECT value FROM _facts_meta WHERE key = 'extracted_at') AS "
                             + q(FACTS_SYSTEM_TIME))
            else:
                exprs.append(q(fld))
        sql = (f"SELECT {', '.join(exprs)} FROM {q(table)} ORDER BY "
               + ", ".join(q(k) for k in e.key))
        ans = self._count_query(source, sql)
        if ans.truncated:
            raise RuntimeError(
                f"{e.name}: the facts table has more than {self._max_rows} rows; refused rather "
                f"than materialised from a silently truncated read")
        qhash = hashlib.sha256(ans.sql.encode("utf-8")).hexdigest()
        self._queries[qhash] = (source, sql)
        ncols = len(ans.columns)
        snapshot = (ans.pointers[0].snapshot if ans.pointers else SnapshotIdentity(
            source_id=source, as_of=self._as_of, content_hash=ans.facts_sha256,
            schema_hash="", query_hash=qhash))
        screened = self._screen_contact_columns(e, ans)
        rows: list[dict[str, Cell]] = []
        for i, values in enumerate(ans.rows):
            row: dict[str, Cell] = {}
            for j, (col, value) in enumerate(zip(ans.columns, values)):
                if self._suppressed(e, col) or col in screened:
                    row[col] = self._withheld(e.name, col)
                elif value is None:
                    row[col] = Absent(field_path=f"{e.name}.{col}", snapshot=snapshot)
                else:
                    row[col] = GroundedValue(value=value, pointer=ans.pointers[i * ncols + j],
                                             confidence=1.0)
            rows.append(row)
        return PlatformEntityRead(entity=e.name, columns=tuple(ans.columns), key=e.key,
                                  rows=tuple(rows), result=None, snapshot_identity=snapshot,
                                  origin="facts")

    def _screen_contact_columns(self, e: PlatformEntity, ans) -> frozenset[str]:
        """Facts text columns whose STORED VALUES match the deployment's contact-identifier
        screen (the pack's `screen.yaml` redaction rules), withheld whole under ``suppress``.

        R-021 round 2, Finding 7 (containment): a facts table has no pack-declared subject or
        identifier mapping, so exact-name matching let ``email_address`` through. A column is
        withheld WHOLE when any of its values matches — never cell by cell, which would make the
        mask itself a signal. Names a screen cannot see (``full_name``) still need the pack
        mapping recorded as REAL-deferred in R-021. If the screen cannot load, every text column
        is withheld: fail closed.
        """
        if self._identifiers != "suppress":
            return frozenset()
        text_cols = [c for c in e.detail.get("text_columns", ()) if c in ans.columns
                     and not self._suppressed(e, c)]
        if not text_cols:
            return frozenset()
        try:
            from databridge_mcp_ingest.screen import apply_masks, load_screen

            if not hasattr(self, "_screen_rules"):
                self._screen_rules = load_screen().redactions
            rules = self._screen_rules
        except Exception:                                        # noqa: BLE001
            hit = set(text_cols)
        else:
            hit = set()
            for c in text_cols:
                j = ans.columns.index(c)
                if any(isinstance(r[j], str) and apply_masks(r[j], rules)[1] for r in ans.rows):
                    hit.add(c)
        self._screened_identifiers.update((e.name, c) for c in hit)
        return frozenset(hit)

    # ==================================================================================
    # passages
    # ==================================================================================
    def _passage_store_paths(self, *, record: bool = False) -> list[Path]:
        root = self._roots()[1]
        if not root.is_dir():
            return []
        out = []
        for p in sorted(root.iterdir()):
            if (not p.is_file() or ".building" in p.name
                    or p.name.endswith(_PASSAGE_SIDECAR_SUFFIXES)):
                continue
            if _is_passage_store(p):
                out.append(p)
            elif record:
                # Every rejected file is said, never skipped silently (R-021 Finding 11).
                self._excluded.append({"entity": p.name, "origin": "passages",
                                       "reason": "not a readable passage store (not SQLite, or "
                                                 "no documents/chunks/embeddings/store_meta "
                                                 "schema)"})
        return out

    def _passage_entities(self) -> list[PlatformEntity]:
        out: list[PlatformEntity] = []
        for path in self._passage_store_paths(record=True):
            conn = _ro(path)
            try:
                meta: dict = {}
                for k, raw in conn.execute("SELECT key, value FROM store_meta").fetchall():
                    try:
                        meta[k] = json.loads(raw)                # set_meta stores JSON
                    except (TypeError, ValueError):
                        meta[k] = raw
                n =conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            finally:
                conn.close()
            source, tier = meta.get("source_id") or path.stem, meta.get("tier") or "default"
            out.append(PlatformEntity(
                name=f"passages.{source}.{tier}", origin="passages",
                source_id=f"passages:{path.stem}", fields=PASSAGE_FIELDS, key=("chunk_id",),
                system_time_field="ingested_at", text_field="content", rows=int(n),
                detail={"path": str(path), "source": source, "tier": tier}))
        return out

    def _capture(self, digest: Optional[str]) -> Optional[str]:
        """The extracted text of one capture, or `None` — only after the digest is proven.

        R-021 Finding 9: the digest comes from a database, so it is validated as a lowercase
        64-hex SHA-256 BEFORE it touches a path; the directory it names must resolve directly
        under the capture root; and the raw bytes (`<digest>`, BUILD_PLAN §5.2) must hash to the
        digest. A capture failing any of those is not evidence, and nothing it says verifies.
        """
        if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
            return None
        if digest not in self._capture_text:
            self._capture_text[digest] = None
            try:
                root = self._roots()[2].resolve()
                base = (root / digest[:2]).resolve()
                blob, text = base / digest, base / f"{digest}.txt"
                if base.parent != root:
                    self._capture_failures[digest] = "outside the capture root"
                elif not blob.is_file():
                    self._capture_failures[digest] = "raw bytes missing"
                elif _file_sha256(blob) != digest:
                    self._capture_failures[digest] = "raw bytes do not hash to the digest"
                elif not text.is_file():
                    self._capture_failures[digest] = "extracted text missing"
                else:
                    stored = text.read_text(encoding="utf-8")
                    why = self._authenticate_text(digest, root, blob.read_bytes(), stored)
                    if why:
                        self._capture_failures[digest] = why
                    else:
                        self._capture_text[digest] = stored
            except OSError as exc:
                self._capture_failures[digest] = f"unreadable ({type(exc).__name__})"
        return self._capture_text[digest]

    def _authenticate_text(self, digest: str, root: Path, raw: bytes,
                           stored: str) -> Optional[str]:
        """Why the stored `.txt` is NOT authenticated by the raw bytes, or `None` when it is.

        R-021 round 2, Finding 4: the raw bytes prove themselves (content address) but every
        pointer indexes into the EXTRACTED text, which the §5.2 contract does not hash. So the text
        is authenticated by re-deriving it: the capture's `.json` is read through the capture
        store's own verifier (`capture.get_meta`, which checks its `meta_digest` against the
        content address — metadata carrying no digest is not trusted), and the extraction the
        ingest runs for that item (`extract.extract_text` for its name/mime, or `table_text` for a
        table snapshot) is re-run over the raw bytes. The stored text must equal one of them
        byte for byte. An extraction that cannot be re-run here (its extractor is unavailable) is
        refused, never assumed.
        """
        try:
            from databridge_mcp_ingest.capture import CaptureCorrupt, get_meta
            from databridge_mcp_ingest.crawl import table_text
            from databridge_mcp_ingest.extract import ExtractionFailed, extract_text, extractor_for
        except ImportError as exc:                               # pragma: no cover - env-bound
            return f"the capture verifier is unavailable ({exc})"
        try:
            meta = get_meta(digest, root)
        except CaptureCorrupt:
            return "capture metadata fails its digest check (swapped or edited)"
        if not isinstance(meta, dict) or not meta.get("meta_digest"):
            return "capture metadata missing or carries no meta_digest (unanchored)"
        self._capture_meta[digest] = meta
        derived: list[str] = []
        name = meta.get("title") or meta.get("uri") or ""
        mime = meta.get("mime")
        if extractor_for(name, mime) is not None:
            try:
                derived.append(extract_text(raw, name=name, mime=mime).text)
            except ExtractionFailed as exc:
                if not derived and "extractor_unavailable" in str(exc):
                    return (f"the extraction cannot be re-run here to authenticate the text "
                            f"({exc})")
            except Exception:                                    # noqa: BLE001
                pass
        table_snap: Optional[dict] = None
        try:
            snap = json.loads(raw.decode("utf-8"))
            if isinstance(snap, dict) and isinstance(snap.get("rows"), list):
                table_snap = snap
                derived.append(table_text(snap))
        except (UnicodeDecodeError, ValueError):
            pass
        if not derived:
            return f"no extraction re-derives text from these bytes (name {name!r}, mime {mime!r})"
        if stored not in derived:
            return "extracted text does not re-derive from the raw bytes (edited after capture)"
        if table_snap is not None and stored == table_text(table_snap):
            self._capture_tables[digest] = table_snap
        return None

    def _capture_names_document(self, digest: str, source_id: Any, doc_id: Any) -> bool:
        """Whether a capture's digest-verified metadata names this passage document: its source
        and path (the first sighting's, or a later sighting's) hash to ``doc_id`` exactly as the
        indexer computes identity (`databridge_retrieval.indexer.doc_id_for`)."""
        meta = self._capture_meta.get(digest)
        if meta is None:
            return False
        try:
            from databridge_retrieval.indexer import doc_id_for
        except ImportError:                                      # pragma: no cover - env-bound
            return False
        uris = [meta.get("uri")] + [s.get("uri") for s in meta.get("sightings") or ()
                                    if isinstance(s, dict)]
        for uri in uris:
            if not uri:
                continue
            try:
                if doc_id_for({"uri": uri}, source_id=str(source_id)) == str(doc_id):
                    return True
            except ValueError:
                continue
        return False

    def _read_passages(self, e: PlatformEntity, req: PlatformRequest) -> PlatformEntityRead:
        path = Path(e.detail["path"])
        conn = _ro(path)
        try:
            rows = conn.execute(
                "SELECT c.chunk_id, c.doc_id, c.content, c.locator, c.char_start, c.char_end, "
                "d.source_id, d.sha256, d.title, d.retrieved_at, d.ingested_at, "
                "d.redactions_json FROM chunks c JOIN documents d USING(doc_id) "
                "ORDER BY c.doc_id, c.chunk_index").fetchall()
        finally:
            conn.close()
        snapshot = SnapshotIdentity(source_id=e.source_id, as_of=self._as_of,
                                    content_hash=_sqlite_digest(path),
                                    schema_hash=sha256_of(canonical_json(list(e.fields))),
                                    query_hash=req.read_hash)
        from databridge_core import Trust

        out_rows: list[dict[str, Cell]] = []
        for r in rows:
            # The as-of cut, in the read itself (R-021 Finding 10): a passage the platform learned
            # after S is not something it believed at S. No ingest instant -> not placeable.
            learned = to_instant(r["ingested_at"])
            if learned is None or learned > self._as_of:
                self.passages_after_cut += 1
                continue
            captured = self._capture(r["sha256"])
            if captured is None:
                # R-021 Finding 4: the cited bytes are gone or fail verification, so the value
                # cannot be grounded and its coordinates cite nothing that resolves. The row is
                # excluded and counted (see `notes`), never emitted under a dead pointer.
                self.passages_unverifiable += 1
                continue
            if not self._capture_names_document(r["sha256"], r["source_id"], r["doc_id"]):
                # R-021 round 2, Finding 4: authentic bytes are evidence for the document their
                # verified metadata names, not for whatever row cites them.
                self.passages_unbound += 1
                continue
            pointer = SourcePointer(source_id=r["source_id"], kind="doc_span",
                                    document_id=r["doc_id"], char_start=int(r["char_start"]),
                                    char_end=int(r["char_end"]), capture_digest=r["sha256"],
                                    trust=Trust.UNTRUSTED)
            transformed = captured[int(r["char_start"]):int(r["char_end"])] != r["content"]
            row: dict[str, Cell] = {}
            for fld in req.fields:
                path_ = f"{e.name}.{fld}"
                value = r[fld]
                if fld == "content" and transformed:
                    # O1b: the stored text is not the cited bytes (the screen masked it). Neither
                    # grounded (not fully disclosed) nor absent (it exists) — withheld, by name.
                    self.transformed_withheld += 1
                    row[fld] = self._withheld(e.name, fld, policy_id=TRANSFORMED_POLICY_ID,
                                              reason=TRANSFORMED_REASON)
                elif value is None:
                    row[fld] = Absent(field_path=path_, snapshot=snapshot)
                elif fld == "content":
                    row[fld] = GroundedValue(value=value, pointer=pointer, confidence=1.0)
                elif fld in PASSAGE_COORDINATES:
                    table, key = _PASSAGE_RECORD[fld]
                    row[fld] = self._coordinate(value, source_id=e.source_id, table=table,
                                                column=fld, row_id=r[key],
                                                trust=Trust.UNTRUSTED)
                else:
                    # Document metadata (`title`, `retrieved_at`): the chunk-body span does not
                    # say it and it has no pointer of its own, so it is Absent (Finding 4).
                    row[fld] = Absent(field_path=path_, snapshot=snapshot)
            out_rows.append(row)
        return PlatformEntityRead(entity=e.name, columns=req.fields, key=e.key,
                                  rows=tuple(out_rows), result=None, snapshot_identity=snapshot,
                                  origin="passages")

    # ==================================================================================
    # verification
    # ==================================================================================
    def resolve(self, pointer: SourcePointer) -> Optional[Any]:
        """Re-read what a pointer cites: the captured text span, the facts cell, or a slice of a
        resolved parent. `None` means this reader cannot re-read that pointer kind."""
        if pointer.kind == "doc_span":
            text = self._capture(pointer.capture_digest)
            if text is None or pointer.char_end is None or pointer.char_end > len(text):
                return None
            return text[pointer.char_start:pointer.char_end]
        if pointer.kind == "query_cell":
            ans = self._rerun(pointer.query_hash or "")
            if ans is None:
                # DSD: a delegate's read (bound parameters) is re-run from the query artefact the
                # facts store kept for it, once per statement
                return self._resolve_query_artefact(pointer)
            if pointer.snapshot is not None and ans.facts_sha256 != pointer.snapshot.content_hash:
                return None
            try:
                return ans.rows[pointer.row_ordinal][ans.columns.index(pointer.column)]
            except (IndexError, ValueError):
                return None
        if pointer.kind == "table_cell":
            if self._record_store(pointer) is not None:
                return self._resolve_record(pointer)
            return self._resolve_captured_cell(pointer)
        if pointer.kind == "derived" and _dsd_kind(pointer) is not None:
            from .delegated import resolve_dsd
            return resolve_dsd(pointer, facts_dir=self._roots()[0], resolve_leaf=self.resolve,
                               graph_store=self._store)
        if pointer.kind == "derived" and pointer.predicate is not None:
            return self._rerun_aggregate(pointer)
        if pointer.kind == "derived" and pointer.derived_from:
            call = parse_derivation(pointer.derivation or "")
            if call.name != "text_slice":
                return None
            parent = self.resolve(pointer.derived_from[0])
            if not isinstance(parent, str):
                return None
            args = dict(call.args)
            try:
                return parent[int(args["start"]):int(args["end"])]
            except (KeyError, ValueError):
                return None
        return None

    def _record_store(self, pointer: SourcePointer) -> Optional[tuple[str, Any]]:
        """``("graph", store)`` or ``("passages", path)`` for a record pointer this reader can
        re-read, else `None`. Tables and columns are a closed whitelist, never taken as SQL."""
        if self._store is not None and pointer.source_id == self._graph_source_id():
            if pointer.column in _GRAPH_RECORD_COLUMNS.get(pointer.table or "", ()):
                return ("graph", self._store)
            return None
        if pointer.source_id.startswith("passages:"):
            stem = pointer.source_id[len("passages:"):]
            ok = {(t, c) for c, (t, _) in _PASSAGE_RECORD.items()}
            if (pointer.table, pointer.column) not in ok:
                return None
            for path in self._passage_store_paths():
                if path.stem == stem:
                    return ("passages", path)
        return None

    def _resolve_record(self, pointer: SourcePointer) -> Optional[Any]:
        """Re-read the one store record a coordinate cites (R-021 round 2, Finding 1)."""
        where = self._record_store(pointer)
        if where is None:
            return None
        origin, handle = where
        table, column = pointer.table, pointer.column
        try:
            if origin == "graph":
                rows = handle.fetchall(f"SELECT {column} FROM {table} WHERE rowid_pk = ?",
                                       (int(pointer.row_id),))
            else:
                key = next(k for c, (t, k) in _PASSAGE_RECORD.items() if t == table)
                conn = _ro(handle)
                try:
                    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {key} = ?",
                                        (pointer.row_id,)).fetchall()
                finally:
                    conn.close()
        except (ValueError, sqlite3.Error):
            return None
        return rows[0][0] if len(rows) == 1 else None

    def _captured_table(self, pointer: SourcePointer) -> Optional[dict]:
        """The captured table snapshot a ``table_cell`` pointer cites, or `None`.

        E2E run 3, P-03: a value ingested from a DATABASE (`mcp_ingest.build.table_rows`) is cited
        with a ``table_cell`` naming the connection (``source_id``), ``schema.table``, the row's
        primary key and the captured snapshot (``capture_digest``). Its evidence is that capture:
        proven by `_capture` (content address, digest-checked metadata, the `.txt` re-derived from
        the bytes) and BOUND — the metadata's authenticated ``source`` is the pointer's source and
        the snapshot's ``schema.table`` is the pointer's table. Anything else vouches for nothing.
        """
        digest = pointer.capture_digest
        if not digest or self._capture(digest) is None:
            return None
        snap = self._capture_tables.get(digest)
        meta = self._capture_meta.get(digest) or {}
        if snap is None or str(meta.get("source") or "") != pointer.source_id:
            return None
        schema, table = snap.get("schema") or "", snap.get("table")
        tref = f"{schema}.{table}" if schema else str(table)
        return snap if tref == pointer.table else None

    def _resolve_captured_cell(self, pointer: SourcePointer) -> Optional[Any]:
        """Re-read one captured table cell: the ONE row ``row_id`` names, by the rule ingestion
        minted it with (`build.table_rows`: the primary-key values joined by ``","`` when every one
        is present, else ``#<ordinal>``). A ``row_id`` naming no row, or — the composite-key
        encoding is ambiguous — several rows, resolves to nothing; so does a missing column."""
        snap = self._captured_table(pointer)
        if snap is None or pointer.row_id is None or not pointer.column:
            return None
        pk = list(snap.get("primary_key") or [])
        hits = []
        for n, row in enumerate(snap.get("rows") or []):
            if not isinstance(row, dict):
                continue
            vals = [row.get(c) for c in pk]
            key = (",".join(str(v) for v in vals) if pk and all(v is not None for v in vals)
                   else f"#{n}")
            if key == str(pointer.row_id):
                hits.append(row)
        if len(hits) != 1 or pointer.column not in hits[0]:
            return None
        return hits[0][pointer.column]

    def _rerun_aggregate(self, pointer: SourcePointer) -> Optional[int]:
        """Re-execute a compressed aggregate's PREDICATE against the store and return the count
        if the contributing rows are the rows the pointer cites (`rows_digest` + `row_count`).

        This is the check `verify_compressed_aggregate` names: the per-row pointers were
        compressed away on purpose, so the value is re-derivable only by re-running the cut. The
        entity is re-read from the store (not from this reader's cache), so a changed store is
        caught rather than agreed with.
        """
        from .derivations import rows_digest
        from .temporal import to_instant, within_lookback

        try:
            pred = json.loads(pointer.predicate or "")
            e = self.entity(pred["entity"])
            decision = to_instant(str(pred["cut"]).split("<=", 1)[1].strip())
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        if decision is None:
            return None
        fields = [pred["join_key"], pred["system_time_field"]]
        req = self.request(e.name, fields)
        fresh = (self._read_graph(e, req) if e.origin == "graph" else
                 self._read_facts(e, req) if e.origin == "facts" else
                 self._read_passages(e, req))
        ids = sorted(rid for rid in (
            fresh.row_id(r) for r in fresh.rows
            if str(fresh.value(r, pred["join_key"])) == str(pred["join_value"])
            and within_lookback(fresh.value(r, pred["system_time_field"]), decision,
                                pred.get("window"))) if rid is not None)
        if rows_digest(ids) != pointer.rows_digest or len(ids) != pointer.row_count:
            return None
        return len(ids)

    def _resolve_query_artefact(self, pointer: SourcePointer) -> Optional[Any]:
        """A `query_cell` of a read this reader did not make (a delegate's, with bound
        parameters): its statement and parameters come from the query ARTEFACT the facts store
        kept, it is re-run read-only against the file the pointer captured, and its identity is
        recomputed (`count.resolve_query_cell`'s rule) — once per statement, then reused."""
        from databridge_mcp_ingest.tabular import count as cq
        from databridge_mcp_ingest.tabular.facts import facts_path, sha256_file

        qhash = pointer.query_hash or ""
        key = ("artefact", pointer.source_id, qhash)
        if key not in self._reruns:
            got = None
            try:
                root = self._roots()[0]
                path = facts_path(pointer.source_id, root)
                art = cq.queries_dir(pointer.source_id, root) / f"{qhash}.json"
                if path.is_file() and art.is_file() and                         sha256_file(path) == pointer.capture_digest:
                    body = json.loads(art.read_text(encoding="utf-8"))
                    if body.get("params_kind") == "named":
                        params: Any = {k: cq._param_restore(v)
                                       for k, v in (body.get("params") or {}).items()}
                    else:
                        params = tuple(cq._param_restore(v) for v in body.get("params") or [])
                    max_rows = int(body.get("max_rows") or cq.MAX_ROWS)
                    _schema, ordered, cols, fetched = cq._run(path, body["sql"], params,
                                                              max_rows)
                    rows = [list(r) for r in fetched[:max_rows]]
                    rsha = cq.result_digest(cols, [[str(v) if isinstance(v, bytes) else v
                                                    for v in r] for r in rows])
                    if cq.query_identity(body["sql"], cq.params_digest(params), rsha,
                                         ordered=ordered, rows=len(rows)) == qhash:
                        got = (cols, rows)
            except Exception:                                    # noqa: BLE001 - unresolved
                got = None
            self._reruns[key] = got
        got = self._reruns[key]
        if got is None:
            return None
        cols, rows = got
        try:
            return rows[pointer.row_ordinal][cols.index(pointer.column)]
        except (IndexError, ValueError, TypeError):
            return None

    def _rerun(self, query_hash: str):
        """Re-execute a facts read by its query hash — once per build, then reused. A re-run, not
        the cached answer from the read itself: resolving against the read's own rows would be a
        tautology."""
        if query_hash in self._reruns:
            return self._reruns[query_hash]
        entry = self._queries.get(query_hash)
        ans = None
        if entry is not None:
            try:
                ans = self._count_query(entry[0], entry[1])
            except Exception:                                    # noqa: BLE001
                ans = None
        self._reruns[query_hash] = ans
        return ans

    def verify(self, pointer: SourcePointer) -> Verdict:
        """The adapter-shaped verdict the executor's `verify_sample` tallies.

        A pointer this reader can re-read and whose pinned digest still holds is VERIFIED (a
        capture-backed span: VERIFIED_AS_OF_CAPTURE). One whose store is gone is VANISHED. One it
        cannot re-read at all is UNVERIFIABLE — an honest property of the tier, never a pass.
        """
        if pointer.kind == "doc_span":
            if not pointer.capture_digest:
                return Verdict.UNVERIFIABLE
            return (Verdict.VERIFIED_AS_OF_CAPTURE if self.resolve(pointer) is not None
                    else Verdict.UNVERIFIABLE)
        if pointer.kind == "query_cell":
            entry = self._queries.get(pointer.query_hash or "")
            if entry is None:
                return Verdict.UNVERIFIABLE
            if not (self._roots()[0] / f"{entry[0]}_facts.sqlite").is_file():
                return Verdict.VANISHED
            return Verdict.VERIFIED if self.resolve(pointer) is not None else \
                Verdict.UNVERIFIABLE
        if pointer.kind == "table_cell":
            if self._record_store(pointer) is None:
                # a captured table cell (P-03): verified as of its capture, like a doc_span
                return (Verdict.VERIFIED_AS_OF_CAPTURE
                        if self._resolve_captured_cell(pointer) is not None
                        else Verdict.UNVERIFIABLE)
            return (Verdict.VERIFIED if self._resolve_record(pointer) is not None
                    else Verdict.VANISHED)
        if pointer.kind == "derived" and pointer.predicate is not None:
            return (Verdict.VERIFIED if self._rerun_aggregate(pointer) is not None
                    else Verdict.UNVERIFIABLE)
        if pointer.kind == "derived" and pointer.derived_from:
            return (self.verify(pointer.derived_from[0]) if self.resolve(pointer) is not None
                    else Verdict.UNVERIFIABLE)
        return Verdict.UNVERIFIABLE

    def cell_check(self, cell: Cell) -> tuple[bool, bool]:
        """`(pointer_resolves, value_matches)` for one grounded cell.

        ``value_matches`` asks whether the re-read bytes SAY the value: exact for a facts cell, a
        store record (a coordinate: id, version instant, edge destination, locator) or a
        passage/chunk, containment (case-folded) for an attribute extracted from a span. There is
        no "structural" pass that asks only for resolution (R-021 round 2, Finding 1): a value
        its pointer does not support fails here, whatever it is.
        """
        if not isinstance(cell, GroundedValue):
            return (False, False)
        got = self.resolve(cell.pointer)
        if got is None:
            return (False, False)
        if cell.pointer.kind in ("query_cell", "table_cell") or _dsd_kind(cell.pointer):
            return (True, got == cell.value)
        if isinstance(cell.value, str) and got == cell.value:
            return (True, True)
        needle = str(cell.value).casefold()
        return (True, needle in str(got).casefold())


def _dsd_kind(pointer: SourcePointer) -> Optional[str]:
    """The DSD pointer family (`delegated.dsd_kind`), imported lazily."""
    from .delegated import dsd_kind
    return dsd_kind(pointer)


def _claimant(e: PlatformEntity) -> str:
    """Who claims an entity name: origin plus the store/source/table that yields it."""
    d = e.detail
    if e.origin == "graph":
        return f"graph:{d.get('kind')}"
    if e.origin == "facts":
        return f"facts:{d.get('source')}/{d.get('table')}"
    return f"passages:{d.get('path')}"


def _candidates(attrs: Mapping[str, list[Any]]) -> tuple[tuple[str, ...], tuple[str, ...],
                                                         Optional[str]]:
    """Label / feature / text candidates, computed from the values actually stored."""
    labels, features, texts = [], [], []
    for name in sorted(attrs):
        values = [v for v in attrs[name] if v is not None]
        if not values:
            continue
        if all(isinstance(v, bool) for v in values):
            labels.append(name)
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            features.append(name)
        elif all(isinstance(v, str) for v in values):
            mean = sum(len(v) for v in values) / len(values)
            if mean >= TEXT_FIELD_MIN_MEAN_CHARS:
                texts.append((mean, name))
    text = max(texts)[1] if texts else None
    return tuple(labels), tuple(features), text


def _facts_query_at(root: Path, source_id: str, sql: str, *, max_rows: int):
    """`count_query` against an explicit facts directory (see `PlatformReader._count_query`).

    Every guard is `count_query`'s own object, not a copy: `_check_statement`, `_authorizer`,
    `sha256_file`, `CountAnswer`, `FactsQueryError`. Only the directory is explicit.
    """
    import datetime as _dt

    from databridge_core import Trust
    from databridge_mcp_ingest.tabular import count as cq

    path = Path(root) / f"{source_id}_facts.sqlite"
    manifest_p = Path(root) / f"{source_id}_facts.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.is_file() else {}
    if not path.is_file():
        raise cq.FactsQueryError(f"no facts store for source {source_id!r} at {path}; run the "
                                 f"tabular stage first")
    stmt = cq._check_statement(sql)
    digest = cq.sha256_file(path)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        schema = "\n".join(r[0] or "" for r in conn.execute(
            "SELECT sql FROM sqlite_master ORDER BY name"))
        conn.set_authorizer(cq._authorizer)
        try:
            cur = conn.execute(stmt)
        except (sqlite3.DatabaseError, sqlite3.Warning) as exc:
            raise cq.FactsQueryError(f"refused or failed: {exc}") from exc
        columns = [d[0] for d in cur.description or []]
        fetched = cur.fetchmany(max_rows + 1)
    finally:
        conn.close()
    truncated = len(fetched) > max_rows
    rows = [list(r) for r in fetched[:max_rows]]
    extracted = manifest.get("extracted_at") or _dt.datetime.fromtimestamp(
        path.stat().st_mtime, _dt.timezone.utc).isoformat(timespec="seconds")
    if manifest.get("sha256") and manifest["sha256"] != digest:
        raise cq.FactsQueryError("the facts file does not match its manifest hash — it was "
                                 "altered after publication; re-run the tabular stage")
    qhash = hashlib.sha256(stmt.encode("utf-8")).hexdigest()
    snap = SnapshotIdentity(source_id=source_id,
                            as_of=_dt.datetime.fromisoformat(extracted.replace("Z", "+00:00")),
                            content_hash=digest,
                            schema_hash=hashlib.sha256(schema.encode("utf-8")).hexdigest(),
                            query_hash=qhash)
    pointers = [SourcePointer(source_id=source_id, kind="query_cell", snapshot=snap,
                              query_hash=qhash, row_ordinal=i, column=c, capture_digest=digest,
                              trust=Trust.UNTRUSTED)
                for i in range(len(rows)) for c in columns]
    citation = (f"[SQL] {stmt} — facts store {path.name} (sha256 {digest[:12]}…), source "
                f"{source_id}, extracted {extracted}")
    return cq.CountAnswer(source_id=source_id, sql=stmt, columns=columns, rows=rows,
                          row_count=len(rows), truncated=truncated, extract_date=extracted,
                          facts_file=str(path), facts_sha256=digest, citation=citation,
                          pointers=pointers)


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _is_passage_store(path: Path) -> bool:
    """Structural discovery (retrieval trap 9): the schema decides, never the file name."""
    try:
        conn = _ro(path)
    except sqlite3.Error:
        return False
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        return {"documents", "chunks", "embeddings", "store_meta"} <= names
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


__all__ = ["PlatformReader", "PlatformEntity", "PlatformEntityRead", "PlatformRequest",
           "IDENTIFIER_POLICY_ID", "IDENTIFIER_REASON", "TRANSFORMED_POLICY_ID",
           "TRANSFORMED_REASON", "GRAPH_STRUCTURAL", "FACTS_SYSTEM_TIME", "facts_dir",
           "passages_dir", "capture_dir"]
