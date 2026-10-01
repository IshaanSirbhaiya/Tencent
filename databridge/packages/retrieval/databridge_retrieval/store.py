"""The passage store — one SQLite file per source x tier (principles §3 storage model).

    documents(doc_id PK /* hashed full path */, source_id, source_url, title, doc_type, extension,
              retrieved_at, sha256, char_count, page_count, tier, decision, rationale,
              conditions, contains_personal_data, redactions_json, summary, topics_json,
              ingested_at)
    chunks(chunk_id PK, doc_id, chunk_index, section, char_start, char_end,
           locator, approx_locator BOOL, context_header, content, content_hash, chunk_kind)
    embeddings(chunk_id PK, vector BLOB /* float32 */, model TEXT, dim INT)
    chunks_fts USING fts5(content, content=chunks)          -- kept in sync by triggers
    excluded_chunks(...)     the CURRENT projection: chunks the per-chunk screen keeps out now
    exclusion_events(...)    APPEND-ONLY history (triggers refuse UPDATE/DELETE): every chunk the
                             screen kept out, every document removed or dropped as unresolvable,
                             with rule + reversal + capture digest, at the moment it happened (P3)
    store_meta(key, value)   source_id, tier, scope, embedding model/label, throughput, ...
    centroids(idx, vector, size)   the router's description of this store (P14)

**Discovery is structural** (P14, trap 9): a file is a passage store because it has
``documents`` + ``chunks`` + ``embeddings`` tables, never because of its name — so a facts
database sitting beside the stores is never mistaken for one. ``journal_mode=DELETE`` (trap 7: no
WAL on a file another process may copy or share).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_url TEXT, title TEXT, doc_type TEXT,
  extension TEXT, retrieved_at TEXT, sha256 TEXT NOT NULL, char_count INTEGER,
  page_count INTEGER, tier TEXT, decision TEXT, rationale TEXT, conditions TEXT,
  contains_personal_data INTEGER, redactions_json TEXT, summary TEXT, topics_json TEXT,
  ingested_at TEXT, chunker TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, section TEXT,
  char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, locator TEXT,
  approx_locator INTEGER NOT NULL, context_header TEXT, content TEXT NOT NULL,
  content_hash TEXT NOT NULL, chunk_kind TEXT NOT NULL, derivation TEXT
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id TEXT PRIMARY KEY, vector BLOB NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  content, content='chunks', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
  INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TABLE IF NOT EXISTS excluded_chunks (
  doc_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, char_start INTEGER, char_end INTEGER,
  decision TEXT NOT NULL, rules_json TEXT, reverse TEXT, rationale TEXT, score REAL,
  recorded_at TEXT, PRIMARY KEY (doc_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS exclusion_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, doc_id TEXT NOT NULL,
  capture_sha256 TEXT, chunk_index INTEGER, char_start INTEGER, char_end INTEGER, decision TEXT,
  rules_json TEXT, reverse TEXT, rationale TEXT, score REAL, recorded_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS exclusion_events_no_update BEFORE UPDATE ON exclusion_events BEGIN
  SELECT RAISE(ABORT, 'exclusion_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS exclusion_events_no_delete BEFORE DELETE ON exclusion_events BEGIN
  SELECT RAISE(ABORT, 'exclusion_events is append-only');
END;
CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS centroids (idx INTEGER PRIMARY KEY, vector BLOB NOT NULL,
                                      size INTEGER NOT NULL);
"""

_REQUIRED_TABLES = {"documents", "chunks", "embeddings"}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def store_filename(source_id: str, tier: str) -> str:
    """A readable, filesystem-safe name that is also UNIQUE per exact (source_id, tier).

    The readable part is lossy (``a/b`` and ``a?b`` both sanitise to ``a_b``), so a digest of the
    exact pair is appended: two sources can never share a file, and a tier can never land in
    another tier's store. Identity is also recorded inside the file (store_meta) and checked on
    open (``BuildSession.open``)."""
    digest = hashlib.sha256(json.dumps([source_id, tier]).encode("utf-8")).hexdigest()[:16]
    return f"{_SAFE.sub('_', source_id)}__{_SAFE.sub('_', tier)}__{digest}.sqlite"


def legacy_store_filename(source_id: str, tier: str) -> str:
    """The pre-2026-10-01 collision-prone name, read only to migrate an existing store."""
    return f"{_SAFE.sub('_', source_id)}__{_SAFE.sub('_', tier)}.sqlite"


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def init_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate (additively) a store's schema. Safe on a store copied from an older one."""
    before = _tables(conn)
    if "chunks" in before and not has_column(conn, "chunks", "derivation"):
        conn.execute("ALTER TABLE chunks ADD COLUMN derivation TEXT")
    conn.executescript(SCHEMA)
    if "excluded_chunks" in before and "exclusion_events" not in before:
        # a store from before the append-only history: its current exclusions seed the history
        conn.execute(
            "INSERT INTO exclusion_events(event, doc_id, capture_sha256, chunk_index, char_start, "
            "char_end, decision, rules_json, reverse, rationale, score, recorded_at) "
            "SELECT 'chunk_excluded', x.doc_id, d.sha256, x.chunk_index, x.char_start, "
            "x.char_end, x.decision, x.rules_json, x.reverse, x.rationale, x.score, "
            "COALESCE(x.recorded_at, '') FROM excluded_chunks x "
            "LEFT JOIN documents d USING(doc_id)")
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()


def record_event(conn: sqlite3.Connection, event: str, doc_id: str, recorded_at: str, *,
                 capture_sha256: Optional[str] = None, chunk_index: Optional[int] = None,
                 char_start: Optional[int] = None, char_end: Optional[int] = None,
                 decision: Optional[str] = None, rules_json: Optional[str] = None,
                 reverse: Optional[str] = None, rationale: Optional[str] = None,
                 score: Optional[float] = None) -> None:
    """Append one row to the exclusion history. There is no update or delete path."""
    conn.execute(
        "INSERT INTO exclusion_events(event, doc_id, capture_sha256, chunk_index, char_start, "
        "char_end, decision, rules_json, reverse, rationale, score, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (event, doc_id, capture_sha256, chunk_index, char_start, char_end, decision, rules_json,
         reverse, rationale, score, recorded_at))


def is_passage_store(path: Path) -> bool:
    """Structural test (trap 9): the schema decides, not the file name."""
    try:
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        return _REQUIRED_TABLES <= names
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def discover(root: Path) -> list[Path]:
    """Every published passage store under ``root`` (by schema; building/staging files skipped)."""
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or ".building" in p.name or p.suffix in {".json", ".lock", ".tmp"}:
            continue
        if is_passage_store(p):
            out.append(p)
    return out


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        row = conn.execute("SELECT value FROM store_meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT INTO store_meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))


def all_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    return {r[0]: get_meta(conn, r[0]) for r in conn.execute("SELECT key FROM store_meta")}


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {"documents": q("SELECT COUNT(*) FROM documents"),
            "chunks": q("SELECT COUNT(*) FROM chunks"),
            "embeddings": q("SELECT COUNT(*) FROM embeddings"),
            "excluded_chunks": q("SELECT COUNT(*) FROM excluded_chunks")}


def delete_document(conn: sqlite3.Connection, doc_id: str) -> None:
    ids = [r[0] for r in conn.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,))]
    _delete_embeddings(conn, ids)
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM excluded_chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))


def _delete_embeddings(conn: sqlite3.Connection, chunk_ids: Iterable[str]) -> None:
    conn.executemany("DELETE FROM embeddings WHERE chunk_id=?", [(c,) for c in chunk_ids])
