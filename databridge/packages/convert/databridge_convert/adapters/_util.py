"""Shared adapter helpers — every adapter builds provenance the SAME way.

An IR is unconstructable without provenance (§7), so each adapter MUST stamp a
SnapshotIdentity from the raw bytes it read. ``snapshot_for`` is the one way to do it.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from databridge_core import SnapshotIdentity


def snapshot_for(source_id: str, data: bytes, *, schema_hash: str = "") -> SnapshotIdentity:
    """Build the provenance anchor for an IR from the source bytes (content hash)."""
    return SnapshotIdentity(
        source_id=source_id,
        as_of=datetime.now(timezone.utc),
        content_hash=hashlib.sha256(data).hexdigest(),
        schema_hash=schema_hash,
    )


class AdapterError(Exception):
    """An adapter could not read/write (malformed input, unsupported feature, security)."""
