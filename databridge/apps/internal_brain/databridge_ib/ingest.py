"""Ingestion: connectors → index (content + compiled ACL policy). Events only invalidate (plan §4.3).

``sync(platform)`` pulls everything changed since the stored cursor and commits the cursor ONLY
after the whole batch is applied (Codex H19). ``apply_event`` handles one verified webhook event by
re-fetching from the source — the payload is never treated as truth.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

from .connectors.base import SourceConnector
from .index import Index
from .models import ChangeEvent
from .sensitive import mask_doc

log = logging.getLogger("databridge_ib.ingest")


class Ingestor:
    def __init__(self, index: Index, connectors: Mapping[str, SourceConnector],
                 cursor_path: Path, directory=None, ledger=None) -> None:
        self.index = index
        self.connectors = connectors
        self.cursor_path = Path(cursor_path)
        self.directory = directory
        self.ledger = ledger
        self._lock = threading.Lock()                     # single writer (Opus review H6)
        self.status: dict[str, dict] = {p: {"last_sync_at": None, "last_event_at": None,
                                            "docs": 0, "state": "never"} for p in connectors}

    # -- cursors ------------------------------------------------------------------------------
    def _cursors(self) -> dict:
        try:
            return json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cursor(self, platform: str, cursor: Optional[str]) -> None:
        cur = self._cursors()
        cur[platform] = cursor
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cursor_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, sort_keys=True), encoding="utf-8")
        tmp.replace(self.cursor_path)

    # -- full / incremental sync --------------------------------------------------------------
    def sync(self, platform: str, *, full: bool = False, reason: Optional[str] = None) -> dict:
        """Incremental from the stored cursor, or ``full`` (cursor None): a full reconciliation that
        also repairs drift between webhook-applied state and the cursor's snapshot."""
        conn = self.connectors[platform]
        with self._lock:
            batch = conn.list_changes(None if full else self._cursors().get(platform))
            if full:
                seen = {d.doc_id for d in batch.docs}
                for d in [x for x in self.index.doc_ids() if x.startswith(platform + ":")]:
                    if d not in seen:
                        self.index.delete(d)
            for doc in batch.docs:              # card / IBAN / NRIC masked BEFORE indexing (F4)
                self.index.upsert(mask_doc(doc), conn.acl(doc.doc_id))
            for d in batch.deleted:
                self.index.delete(d)
            for d in batch.acl_changed:
                if self.index.doc(d):
                    self.index.set_acl(d, conn.acl(d))
            self._save_cursor(platform, batch.cursor)
            now = datetime.now(timezone.utc)
            st = self.status[platform]
            st.update(last_sync_at=now.isoformat(), state="ok",
                      docs=sum(1 for d in self.index.doc_ids() if d.startswith(platform + ":")))
            summary = {"platform": platform, "upserted": len(batch.docs),
                       "deleted": len(batch.deleted), "acl_changed": len(batch.acl_changed)}
            if full:
                summary["full"] = True
            if reason:
                summary["reason"] = reason
        self._ledger_event("ingest.sync", platform, summary)
        return summary

    def sync_all(self, *, full: bool = False, reason: Optional[str] = None) -> list[dict]:
        out = []
        for p in self.connectors:
            try:
                out.append(self.sync(p, full=full, reason=reason))
            except Exception as exc:                                # noqa: BLE001
                self.status[p]["state"] = f"error: {type(exc).__name__}"
                log.warning("sync %s failed: %s", p, type(exc).__name__)
        return out

    def reconcile(self, reason: str) -> dict:
        """A FULL reconciliation of every connector, now: re-read every document, its ACL and the
        container memberships behind it from the source, re-index what changed and delete what
        is gone; then invalidate the directory's cached readable sets. For a source that changed
        WITHOUT telling us (the simulator's world reset reloads silently, so the incremental
        cursor still holds the pre-reset state and would see no change). Each platform's sync is
        ledgered as usual (``ingest.sync``, with ``full`` and ``reason``)."""
        out = self.sync_all(full=True, reason=reason)
        done = {r["platform"] for r in out}
        failed = sorted(p for p in self.connectors if p not in done)
        if self.directory is not None:
            with self._lock:
                self.directory.epoch += 1
        return {"reason": reason, "platforms": out, "failed": failed}

    # -- events -------------------------------------------------------------------------------
    def apply_event(self, ev: ChangeEvent) -> None:
        conn = self.connectors.get(ev.platform)
        if conn is None:
            return
        with self._lock:
            if ev.type == "content.changed":
                for d in ev.object_ids:
                    doc = conn.fetch(d)
                    if doc is None:
                        self.index.delete(d)
                    else:
                        self.index.upsert(mask_doc(doc), conn.acl(doc.doc_id))
            elif ev.type == "content.deleted":
                for d in ev.object_ids:
                    self.index.delete(d)
            elif ev.type in ("acl.changed", "membership.changed"):
                targets = set(ev.object_ids)
                if ev.container:
                    targets |= set(self.index.docs_in_container(ev.platform, ev.container))
                for d in targets:
                    if self.index.doc(d):
                        self.index.set_acl(d, conn.acl(d))
            elif ev.type == "user.changed" and self.directory is not None:
                # never trust the payload (sec1 #8): Gate 2 asks the source about the user on every
                # query; here we only invalidate cached readable sets.
                self.directory.epoch += 1
            self.status[ev.platform]["last_event_at"] = datetime.now(timezone.utc).isoformat()
        kind = "membership.changed" if ev.type == "membership.changed" else (
            "acl.changed" if ev.type == "acl.changed" else None)
        if kind:
            self._ledger_event(kind, ev.platform, {"object_ids": list(ev.object_ids),
                                                    "container": ev.container, "user": ev.user,
                                                    "delivery_id": ev.delivery_id})

    def _ledger_event(self, kind: str, platform: Optional[str], detail: dict) -> None:
        if self.ledger is None:
            return
        from .models import SourceChangeEvent
        try:
            self.ledger.append(SourceChangeEvent(kind=kind, platform=platform, detail=detail,
                                                 event_time=datetime.now(timezone.utc)))
        except Exception as exc:                                    # noqa: BLE001
            log.warning("ledger append for %s failed: %s", kind, type(exc).__name__)
