"""Snapshot identity for SQL results (09_FINAL_PRD §15.1/§17.2).

A SQL RowBatch's provenance anchor: server/db, the query, an as-of timestamp, and a
content hash of the result. Re-running the same query against the same snapshot must
reproduce the same content hash (the basis for SQL-as-provenance verification).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from databridge_core import SnapshotIdentity


def _hash(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _content_hash(rows: list[dict]) -> str:
    # ORDER-STABLE: hash the SORTED per-row canonical JSON, so an unordered query
    # (SELECT * with no ORDER BY) still reproduces the same content hash (§15.1).
    serialised = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    return hashlib.sha256("\n".join(serialised).encode("utf-8")).hexdigest()


def _all_keys(rows: list[dict]) -> list[str]:
    keys: set[str] = set()
    for r in rows:
        keys.update(r.keys())
    return sorted(keys)


def sql_snapshot(source_id: str, query: str, result_rows: list[dict]) -> SnapshotIdentity:
    return SnapshotIdentity(
        source_id=source_id,
        as_of=datetime.now(timezone.utc),
        content_hash=_content_hash(result_rows),
        schema_hash=_hash(_all_keys(result_rows)) if result_rows else "",
        query_hash=_hash(query),
    )
