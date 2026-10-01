"""Live reads go into the capture store (§5.2) BEFORE they are cited.

An agent's `source.read` / `source.query` result is not evidence until it has a content address:
the bytes are written to ``WALNUT_CAPTURE_DIR/<sha[:2]>/<sha256>`` (+ ``.txt``, ``.json``) and the
pointer the answer cites carries ``capture_digest=<sha256>`` — exactly like an ingested item, so
`/evidence` re-verifies it the same way.

`databridge_mcp_ingest.capture` owns the layout and is the ONLY writer and reader: its `put_meta`
binds the metadata to the content address with a ``meta_digest`` and its `get_meta` refuses
metadata that was swapped or edited (`CaptureCorrupt`). This module imports it — it never copies
it. Without it there is no capture store: a live read is then not captured, so it is not citable
(the runtime used to write a digest-less copy of the layout here; that copy could not be verified,
so it is gone).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from databridge_core import SourcePointer
from databridge_core.provenance import Trust

from . import paths

try:                                                     # one writer when it exists
    from databridge_mcp_ingest import capture as _ingest_capture   # type: ignore
except Exception:                                        # noqa: BLE001 - absent or broken: fallback
    _ingest_capture = None


def _store():
    """The ingest capture store module, or `RuntimeError` — never a local substitute."""
    if _ingest_capture is None:
        raise RuntimeError("the capture store (databridge_mcp_ingest.capture) is not available, "
                           "so this live read cannot be captured and is not evidence")
    return _ingest_capture


def _put(data: bytes, text: str, meta: dict, root: Path) -> str:
    store = _store()
    sha = store.put_bytes(data, root)
    store.put_text(sha, text, root)
    store.put_meta(sha, meta, root)                      # carries meta_digest (W1)
    return sha


def _put_redacted(sha: str, redacted: str, root: Path) -> None:
    """The screen's masked view, beside the text — ingestion's layout (``<sha>.redacted.txt``)."""
    _store().put_redacted(sha, redacted, root)


def capture_live_read(*, connection: str, uri: str, text: str, data: Optional[bytes] = None,
                      mime: Optional[str] = None, title: Optional[str] = None,
                      root: Optional[Path] = None,
                      redacted: Optional[str] = None) -> tuple[str, SourcePointer]:
    """Capture one live result and return ``(sha256, pointer)``.

    The pointer is a ``doc_span`` over the WHOLE captured text (``char_start=0``,
    ``char_end=len(text)``) with ``capture_digest`` — the honest granularity for a tool result whose
    internal structure the runtime does not own. Trust is UNTRUSTED: third-party MCP content never
    decides control flow (spine D3).
    """
    raw = data if data else text.encode("utf-8")
    meta = {"source": connection, "uri": uri, "mime": mime or "text/plain", "title": title or uri,
            "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    base = Path(root) if root is not None else paths.capture_dir()
    sha = _put(raw, text, meta, base)
    if redacted is not None and redacted != text:
        _put_redacted(sha, redacted, base)
    ptr = SourcePointer(source_id=connection, kind="doc_span",
                        document_id=document_identity(connection, uri), char_start=0,
                        char_end=len(text), capture_digest=sha, trust=Trust.UNTRUSTED)
    return sha, ptr


def document_identity(connection: str, uri: str) -> str:
    """P4: identity is the source-qualified FULL path, hashed — the same id ingestion gives the
    same item (`databridge_mcp_ingest.crawl.item_identity`), so a live read and an ingested copy
    of one document are one document. The readable URI stays in the capture's metadata
    (R-017 t02 finding 11)."""
    try:
        from databridge_mcp_ingest.crawl import item_identity   # noqa: PLC0415 - one rule
    except Exception:                                        # noqa: BLE001 - absent: same formula
        return hashlib.sha256(f"mcp://{connection}/{uri}".encode("utf-8")).hexdigest()
    return item_identity(connection, uri)


def read_captured_meta(sha: str, root: Optional[Path] = None) -> Optional[dict]:
    """The capture's metadata through ingest's `get_meta` (W1): `CaptureCorrupt` when it was
    swapped or edited — and ALSO when it carries no ``meta_digest``: everything this runtime or the
    federated service captures goes through `put_meta`, so a digest-less file is one whose digest
    was stripped. None when there is no metadata file at all."""
    store = _store()
    base = Path(root) if root is not None else paths.capture_dir()
    meta = store.get_meta(sha, base)
    if meta is not None and not meta.get("meta_digest"):
        raise store.CaptureCorrupt(f"capture metadata for {sha} carries no meta_digest; it cannot "
                                   f"be verified against its content address")
    return meta


def read_captured_text(sha: str, root: Optional[Path] = None) -> Optional[str]:
    base = Path(root) if root is not None else paths.capture_dir()
    p = base / sha[:2] / f"{sha}.txt"
    return p.read_text(encoding="utf-8") if p.is_file() else None


__all__ = ["capture_live_read", "document_identity", "read_captured_meta", "read_captured_text"]
