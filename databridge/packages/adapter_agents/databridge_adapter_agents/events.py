"""The §5.3 event log — `run_completed` / `run_failed` as emitted by the adapter-agent manager.

Format is the frozen contract (BUILD_PLAN §5.3), one JSON object per line with exactly these keys:

    event_id, ts, type, source_id, item_id, uri, content_hash, run_id, attrs

Writers append with a single `write()` of one line + newline, then flush (fsync), so a reader
tailing by byte offset never sees half a record. The ingest pipeline writes the same file with the
same rule; `attrs.emitted_by` says which writer a line came from.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from . import paths

EVENT_KEYS = ("event_id", "ts", "type", "source_id", "item_id", "uri", "content_hash", "run_id",
              "attrs")
MANAGER_EVENT_TYPES = frozenset({"run_completed", "run_failed"})
EMITTED_BY = "databridge_adapter_agents"


def now_iso(clock: Optional[dt.datetime] = None) -> str:
    t = clock or dt.datetime.now(dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit(type_: str, *, source_id: str, run_id: str, attrs: Optional[dict[str, Any]] = None,
         ts: Optional[str] = None, path: Optional[Path] = None) -> dict[str, Any]:
    """Append one run-level event. Item-level fields are `null` — a run event names no item."""
    if type_ not in MANAGER_EVENT_TYPES:
        raise ValueError(f"the adapter-agent manager emits only {sorted(MANAGER_EVENT_TYPES)}, "
                         f"not {type_!r}")
    record = {"event_id": str(uuid.uuid4()), "ts": ts or now_iso(), "type": type_,
              "source_id": source_id, "item_id": None, "uri": None, "content_hash": None,
              "run_id": run_id, "attrs": {"emitted_by": EMITTED_BY, **dict(attrs or {})}}
    assert tuple(record) == EVENT_KEYS
    target = path or paths.events_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str).encode("utf-8") + b"\n"
    fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


def read_events(path: Optional[Path] = None) -> list[dict[str, Any]]:
    target = path or paths.events_path()
    if not target.is_file():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()]


__all__ = ["EVENT_KEYS", "MANAGER_EVENT_TYPES", "EMITTED_BY", "emit", "read_events", "now_iso"]
