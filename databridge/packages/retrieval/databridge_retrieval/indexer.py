"""Indexing: capture store → chunks → per-chunk screen → embeddings → verified publication.

The ingest build stage (BUILD_PLAN §6 B, wired in 2b) calls, per source::

    with BuildSession(source_id, tier) as build:          # opens a STAGING copy of the store
        for item in run:                                  # every captured prose item
            build.index_capture(item.sha256, {"uri": ..., "item_id": ..., "title": ...})
        build.retain_only(current_item_ids)               # optional: drop items no longer present
    # leaving the block publishes: verify locally, then one atomic replace (P11)

or, for a single item, ``index_capture(sha256, meta)`` (a one-item session that publishes).

**Identity is the hashed FULL path** (P4): ``doc_id = sha256("mcp://<source_id>/<uri>")`` — the
same rule, over the same inputs, as the ingest pipeline's ``item_identity``, so a passage pointer
and a graph pointer name the same document. It is always COMPUTED here; an ``item_id``/``doc_id``
a caller supplies is checked against it and a mismatch is refused (``identity_mismatch``), never
adopted. An item with no path is refused (``missing_path``).

**Incremental by content hash** (trap 13 — never by id alone): a document whose capture sha256
and chunker version are unchanged, and whose chunks all have vectors, is skipped. A changed
document is re-chunked, and any chunk whose embedded text is byte-identical to before reuses its
vector instead of being re-embedded.

**Every published pointer resolves.** Before the replace, every document (new or carried over
from the previous publication) is checked against the capture store: its ``.txt`` exists, every
chunk span is inside it, and every chunk's stored text either IS the span or is exactly the
screen's same-length mask of it (then the chunk carries a ``derivation`` naming the screen). A
document that fails is re-indexed from its capture; one whose capture is gone is dropped from the
index and the drop is recorded in the append-only ``exclusion_events`` history.

**Publication refuses the silent disasters** (P11): an empty store (no documents or no chunks —
refused even with ``force``; delete a store deliberately, never by publishing nothing), and a
store with fewer than half the previously published documents, or fewer than half the previously
*retrievable* documents (documents with at least one searchable chunk), or — for a store of at
least ``SHRINK_MIN_CHUNKS`` chunks — fewer than half its previously published chunks (one huge
document truncated to one chunk keeps every document count), unless ``force``. Counts must
reconcile (every chunk has one vector, one model, one dimension) before the replace happens.

**The review gate is not a caller keyword** (R-019 round 3). A REVIEW-band chunk is admitted only
against a verified approval record bound to principal, document, capture and screen policy.
Retrieval has no such record to verify yet, so ``override_allow`` is REFUSED
(``approval_unverified``) and the refusal is appended to the store's history.

**The policy is part of the index.** The store records the screen's policy digest; a publication
under a different policy re-screens every document from its capture, so a newly blocking rule
removes content whose capture did not change (and a relaxed rule readmits it). Likewise the
embedding route: an unchanged store is probed with the current route at publication and fully
re-embedded when the model differs.

**Lock fencing.** While a session is open a heartbeat thread refreshes the lock — but only while
the lock body is still this session's token. Publication re-reads the lock immediately before the
replace and refuses (``BuildInProgress``) when ownership was lost.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from . import paths, router
from . import store as st
from .chunker import CHUNKER_VERSION, chunk_document, is_tabular
from .locators import regions_for
from .models import BuildInProgress, DocIndexResult, PublishRefused, PublishReport, RetrievalError
from .runtime import get_harness
from .screen import Screen, load_chunk_screen
from .vectors import (
    centroid_count,
    kmeans,
    load_matrix,
    search_ram_bytes,
    search_ram_mb,
    to_blob,
)

SHRINK_FLOOR = 0.5
#: The chunk-count shrink rule applies from this many previously published chunks (~90k chars at
#: 1,800 per chunk). Below it a normal edit of a small store can halve its chunks legitimately;
#: above it, losing more than half the passages in one publication is treated as a broken run.
SHRINK_MIN_CHUNKS = 50
#: A lock whose owner cannot be checked (another host) and that has not been heartbeat for this
#: long is presumed abandoned. A live builder refreshes its lock's mtime as it works.
LOCK_STALE_SECONDS = 6 * 3600
HEARTBEAT_SECONDS = 60
DEFAULT_TIER = "default"
#: The ingest pipeline's identity rule (``databridge_mcp_ingest.crawl.item_identity``), restated
#: because retrieval must import standalone. ``test_identity_matches_ingest`` pins the two together.
IDENTITY_TEMPLATE = "mcp://{source_id}/{uri}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def doc_id_for(meta: dict, capture_sha: str = "", *, source_id: Optional[str] = None) -> str:
    """P4: identity is the hashed FULL path, source-qualified — never a filename, never a
    caller-chosen id, never the content digest. Raises ``ValueError`` when there is no source or
    no path. (``capture_sha`` is accepted for signature compatibility and deliberately unused.)"""
    source = source_id or meta.get("source_id") or meta.get("source")
    uri = meta.get("uri") or meta.get("source_url")
    if not source:
        raise ValueError("no source_id: document identity is the hashed source-qualified path")
    if not uri:
        raise ValueError("no uri: document identity is the hashed FULL path, and this item has "
                         "none (a content digest is not an identity)")
    return _sha(IDENTITY_TEMPLATE.format(source_id=source, uri=uri))


class CaptureMetaCorrupt(RetrievalError):
    """The capture's metadata failed the capture store's own check (`capture.get_meta`): it was
    swapped between captures or edited in place, so none of it — source, path, title — is trusted."""


def read_capture_checked(sha: str, root: Optional[Path] = None) -> tuple[Optional[str], dict]:
    """``(text, meta)`` from the §5.2 capture layout, the metadata read ONLY through the capture
    store's reader (`databridge_mcp_ingest.capture.get_meta`), which verifies its `meta_digest`
    against the content address. Raises `CaptureMetaCorrupt` when it does not match. A legacy file
    without a digest reads as before; no file (or unreadable legacy JSON) reads as ``{}``."""
    from databridge_mcp_ingest.capture import CaptureCorrupt, get_meta  # noqa: PLC0415

    cap_root = root or paths.capture_dir()
    try:
        meta = get_meta(sha, cap_root) or {}
    except CaptureCorrupt as exc:
        raise CaptureMetaCorrupt(str(exc)) from exc
    # read exactly as the capture store's own reader does (capture.get_text), so offsets agree
    text_p = cap_root / sha[:2] / f"{sha}.txt"
    text = text_p.read_text(encoding="utf-8") if text_p.is_file() else None
    return text, meta


def read_capture(sha: str, root: Optional[Path] = None) -> tuple[Optional[str], dict]:
    """``(text, meta)``; never raises for corrupt metadata — it returns ``{}`` instead, so nothing
    from a tampered file is ever trusted. The indexer itself uses `read_capture_checked` and
    records the refusal; this form is for listings that only need to skip such a capture."""
    try:
        return read_capture_checked(sha, root)
    except CaptureMetaCorrupt:
        text_p = (root or paths.capture_dir()) / sha[:2] / f"{sha}.txt"
        return (text_p.read_text(encoding="utf-8") if text_p.is_file() else None), {}


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a running process on THIS host. Never signals it (on Windows
    ``os.kill(pid, 0)`` would TERMINATE the process), so this is a query, not an action."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.GetLastError() == 5                # ERROR_ACCESS_DENIED: exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                         # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BuildSession:
    """A single-writer build of one store (source x tier) into a staging copy, then publish."""

    def __init__(self, source_id: str, tier: str = DEFAULT_TIER, *,
                 harness: Any = None, passages_dir: Optional[Path] = None,
                 capture_dir: Optional[Path] = None, pack: Optional[str] = None,
                 screen: Union[Screen, Path, str, None] = None, scope: Optional[str] = None,
                 force: bool = False, embed_flush: int = 256, embed_batch: int = 32,
                 workers: int = 4) -> None:
        if not source_id:
            raise ValueError("source_id is required")
        self.source_id = source_id
        self.tier = tier or DEFAULT_TIER
        self.harness = harness
        self.root = Path(passages_dir) if passages_dir else paths.passages_dir()
        self.capture_root = Path(capture_dir) if capture_dir else None
        self.pack = pack
        self._screen_arg = screen
        self.scope = scope
        self.force = force
        self.embed_flush, self.embed_batch, self.workers = embed_flush, embed_batch, workers
        name = st.store_filename(source_id, self.tier)
        self._name = name
        self.published_path = self.root / name
        self.lock_path = self.root / f"{name}.lock"
        self._token = secrets.token_hex(8)
        # unique per owner: a builder that wrongly took over a lock can never share a staging file
        self.staging_path = self.root / f"{name}.{self._token}.building"
        self.conn: Optional[sqlite3.Connection] = None
        self.screen: Optional[Screen] = None
        self.previous_documents: Optional[int] = None
        self.previous_retrievable: Optional[int] = None
        self.previous_chunks: Optional[int] = None
        #: the policy digest the published store was screened under (None: unknown / new store)
        self.previous_screen_digest: Optional[str] = None
        self._rescreen = False
        self._screened: set[str] = set()
        self.results: list[DocIndexResult] = []
        self.dropped: list[dict] = []
        self._pending: list[tuple[str, str]] = []
        self._model: Optional[str] = None
        self._offline = False
        self._label: Optional[str] = None
        self._dim: Optional[int] = None
        self._embed_texts = 0
        self._embed_chars = 0
        self._embed_seconds = 0.0
        self._lock_body: Optional[str] = None
        self._beat = 0.0
        self._done = False
        self._stop_beat = threading.Event()
        self._beater: Optional[threading.Thread] = None
        self._lost_lock = False

    # -- lifecycle -------------------------------------------------------------------------------
    def open(self) -> "BuildSession":
        self.root.mkdir(parents=True, exist_ok=True)
        self._acquire_lock()
        try:
            if isinstance(self._screen_arg, Screen):
                self.screen = self._screen_arg
            else:
                self.screen = load_chunk_screen(
                    self.pack, path=Path(self._screen_arg) if self._screen_arg else None)
            if self.harness is None:
                self.harness = get_harness()
            self._migrate_legacy_name()
            if self.published_path.exists():
                src = st.connect(self.published_path, readonly=True)
                dst = sqlite3.connect(str(self.staging_path))
                try:
                    have = (st.get_meta(src, "source_id"), st.get_meta(src, "tier"))
                    if have != (self.source_id, self.tier):
                        raise RetrievalError(
                            f"{self.published_path.name} belongs to {have[0]!r}/{have[1]!r}, not "
                            f"{self.source_id!r}/{self.tier!r}; refusing to build one store over "
                            "another")
                    src.backup(dst)
                    c = st.counts(src)
                    self.previous_documents = c["documents"]
                    self.previous_chunks = c["chunks"]
                    self.previous_screen_digest = st.get_meta(src, "screen_digest")
                    self.previous_retrievable = src.execute(
                        "SELECT COUNT(DISTINCT doc_id) FROM chunks").fetchone()[0]
                finally:
                    src.close()
                    dst.close()
            self.conn = st.connect(self.staging_path)
            # The staging file is disposable: a crash discards it and the published store is
            # untouched; it is integrity-checked before the one atomic replace. So no fsync per
            # document (measured 2026-10-01, 2,000 synthetic docs on Windows: 61 s → 49 s).
            self.conn.execute("PRAGMA synchronous=OFF")
            st.init_schema(self.conn)
            st.set_meta(self.conn, "source_id", self.source_id)
            st.set_meta(self.conn, "tier", self.tier)
            self.conn.commit()
            self._model = None
            # a store screened under another (or an unrecorded) policy is re-screened in full
            self._rescreen = (self.previous_documents is not None
                              and self.previous_screen_digest != self.screen.digest)
        except BaseException:
            self._cleanup()
            raise
        return self

    def _migrate_legacy_name(self) -> None:
        """A store published under the old, collision-prone name moves to the unique name, but
        only when its recorded identity is exactly this source x tier (else it is left alone)."""
        if self.published_path.exists():
            return
        legacy = self.root / st.legacy_store_filename(self.source_id, self.tier)
        if not legacy.is_file() or not st.is_passage_store(legacy):
            return
        conn = st.connect(legacy, readonly=True)
        try:
            have = (st.get_meta(conn, "source_id"), st.get_meta(conn, "tier"))
        finally:
            conn.close()
        if have == (self.source_id, self.tier):
            _replace(legacy, self.published_path)

    def __enter__(self) -> "BuildSession":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._done:
            return
        if exc_type is None:
            self.publish()
        else:
            self.abort()

    def abort(self) -> None:
        self._cleanup()

    # -- the single-writer lock ------------------------------------------------------------------
    def _read_lock(self) -> tuple[Optional[str], Optional[float]]:
        try:
            return (self.lock_path.read_text(encoding="utf-8"), self.lock_path.stat().st_mtime)
        except FileNotFoundError:
            return None, None

    @staticmethod
    def _lock_stale(body: str, mtime: float) -> bool:
        """Stale when its owner is provably dead on this host; otherwise only when it has not
        been heartbeat for ``LOCK_STALE_SECONDS``. A live owner on this host is never stale."""
        pid, host = None, None
        try:
            data = json.loads(body)
            pid, host = int(data.get("pid")), data.get("host")
        except (ValueError, TypeError, AttributeError):
            try:
                pid = int(body.split()[0])                   # the pre-2026-10-01 "pid time" form
                host = socket.gethostname()
            except (ValueError, IndexError):
                pid = None
        if pid is not None and host == socket.gethostname():
            return not _pid_alive(pid)
        return time.time() - mtime >= LOCK_STALE_SECONDS

    def _acquire_lock(self) -> None:
        body = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                           "token": self._token, "at": _now()})
        for _ in range(3):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                held, mtime = self._read_lock()
                if held is None:
                    continue                                 # released meanwhile: retry
                if not self._lock_stale(held, mtime or 0.0):
                    age = time.time() - (mtime or time.time())
                    raise BuildInProgress(
                        f"{self.lock_path.name} is held (heartbeat {age:.0f}s ago) by a live "
                        f"build of {self.source_id}/{self.tier}. Single writer per store.")
                # move the stale lock aside atomically, then confirm it is the one judged stale:
                # a second recoverer that raced us must not steal the lock we just created
                aside = self.lock_path.with_name(f"{self.lock_path.name}.stale-{self._token}")
                try:
                    os.rename(self.lock_path, aside)
                except FileNotFoundError:
                    continue
                if aside.read_text(encoding="utf-8") != held:
                    try:
                        os.rename(aside, self.lock_path)
                    except OSError:
                        pass
                    raise BuildInProgress(f"{self.lock_path.name} changed hands during recovery")
                aside.unlink()
                continue
            os.write(fd, body.encode("utf-8"))
            os.close(fd)
            self._lock_body = body
            self._beat = time.monotonic()
            self._reclaim_staging()
            self._start_heartbeat()
            return
        raise BuildInProgress(f"could not acquire {self.lock_path.name}")

    def _reclaim_staging(self) -> None:
        """We hold the lock, so any other staging file of this store belongs to a dead owner."""
        for p in [self.root / f"{self._name}.building",
                  *self.root.glob(f"{self._name}.*.building")]:
            if p != self.staging_path:
                try:
                    p.unlink()
                except (FileNotFoundError, PermissionError):
                    pass

    def _owns_lock(self) -> bool:
        held, _ = self._read_lock()
        return held is not None and held == self._lock_body

    def _beat_once(self) -> None:
        """Refresh the lock's mtime — only while it is still OURS. A lock another builder took
        over is never touched (that would keep a usurper's lock alive under our name)."""
        if self._lock_body is None or self._lost_lock:
            return
        if not self._owns_lock():
            self._lost_lock = True
            return
        try:
            os.utime(self.lock_path)
        except OSError:
            pass
        self._beat = time.monotonic()

    def _start_heartbeat(self) -> None:
        """An independent heartbeat: a long embedding call or verification pass cannot starve
        it, so a live builder is not presumed abandoned by another host while it works."""
        self._stop_beat.clear()

        def run() -> None:
            while not self._stop_beat.wait(HEARTBEAT_SECONDS):
                self._beat_once()
                if self._lost_lock:
                    return

        self._beater = threading.Thread(target=run, name=f"lock-heartbeat:{self._name}",
                                        daemon=True)
        self._beater.start()

    def _heartbeat(self) -> None:
        if time.monotonic() - self._beat >= HEARTBEAT_SECONDS:
            self._beat_once()

    def _fence(self) -> None:
        """Refuse to go on when this session no longer owns its lock (fencing, R-019 round 3)."""
        if self._lost_lock or not self._owns_lock():
            self._lost_lock = True
            raise BuildInProgress(
                f"lost ownership of {self.lock_path.name}: another builder took the lock over "
                "(this session was presumed abandoned); refusing to publish over its work")

    def _cleanup(self) -> None:
        self._stop_beat.set()
        if self._beater is not None and self._beater is not threading.current_thread():
            self._beater.join(timeout=5)
            self._beater = None
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        try:
            self.staging_path.unlink()
        except FileNotFoundError:
            pass
        held, _ = self._read_lock()
        if held is not None and held == self._lock_body:     # only ever release OUR lock
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
        self._done = True

    # -- indexing --------------------------------------------------------------------------------
    def _identity(self, meta: dict) -> tuple[Optional[str], Optional[DocIndexResult]]:
        try:
            doc_id = doc_id_for(meta, source_id=self.source_id)
        except ValueError as err:
            return None, DocIndexResult("", "missing_path", detail=str(err))
        for key in ("item_id", "doc_id"):
            given = meta.get(key)
            if given and str(given) != doc_id:
                return None, DocIndexResult(
                    doc_id, "identity_mismatch",
                    detail=f"{key} {given!r} is not the hashed full path of {meta.get('uri')!r} "
                           f"in source {self.source_id!r}; identity is computed, never adopted")
        return doc_id, None

    def _record(self, r: DocIndexResult) -> DocIndexResult:
        self.results.append(r)
        return r

    def _refuse_corrupt(self, sha: str, meta: Optional[dict],
                        exc: Exception) -> DocIndexResult:
        """The capture's metadata failed its digest: refuse, record it, index nothing. The event
        is keyed by the caller's identity when it gave one, else by the capture itself — never by
        anything read from the tampered file."""
        try:
            doc_id = doc_id_for(dict(meta or {}), source_id=self.source_id)
        except ValueError:
            doc_id = f"capture:{sha}"
        detail = (f"capture metadata failed its digest check ({exc}); nothing from this capture "
                  "was indexed")
        st.record_event(self.conn, "capture_corrupt", doc_id, _now(), capture_sha256=sha,
                        rationale=detail)
        self.conn.commit()
        return self._record(DocIndexResult(doc_id, "capture_corrupt", detail=detail))

    def _refuse_override(self, doc_id: str, capture_digest: str) -> DocIndexResult:
        """No caller keyword admits REVIEW-band content. Admission needs an approval record
        verified against principal, document, capture and screen policy; retrieval has no such
        record to verify yet, so the request is refused and the refusal is recorded (P3)."""
        detail = ("override_allow is refused: a review-band chunk is admitted only against a "
                  "verified approval record (principal, document, capture, screen policy), and "
                  "none is available to retrieval. Nothing from this call was indexed.")
        st.record_event(self.conn, "override_refused", doc_id, _now(),
                        capture_sha256=capture_digest, decision="review", rationale=detail)
        self.conn.commit()
        return self._record(DocIndexResult(doc_id, "approval_unverified", detail=detail))

    def index_capture(self, sha256: str, meta: Optional[dict] = None, *,
                      override_allow: bool = False) -> DocIndexResult:
        """Index one captured item. ``override_allow`` is kept for signature compatibility and
        always REFUSED (``approval_unverified``) — see ``_refuse_override``."""
        try:
            text, cap_meta = read_capture_checked(sha256, self.capture_root)
        except CaptureMetaCorrupt as exc:
            return self._refuse_corrupt(sha256, meta, exc)
        merged = {**cap_meta, **(meta or {})}
        doc_id, refused = self._identity(merged)
        if refused is not None:
            return self._record(refused)
        if override_allow:
            return self._refuse_override(doc_id, sha256)
        if text is None:
            return self._record(DocIndexResult(
                doc_id, "missing_capture", detail=f"no extracted text {sha256}.txt in the "
                                                  "capture store"))
        return self._index(text, doc_id, sha256, merged)

    def index_text(self, text: str, *, capture_digest: str, doc_id: Optional[str] = None,
                   meta: Optional[dict] = None, override_allow: bool = False) -> DocIndexResult:
        """Index ``text`` as the capture ``capture_digest``. Refused unless ``text`` IS that
        capture's ``.txt`` (else no pointer could resolve) and the identity is the hashed path."""
        merged = dict(meta or {})
        if doc_id is not None:
            merged.setdefault("doc_id", doc_id)
        doc_id_, refused = self._identity(merged)
        if refused is not None:
            return self._record(refused)
        if override_allow:
            return self._refuse_override(doc_id_, capture_digest)
        try:
            captured, _ = read_capture_checked(capture_digest, self.capture_root)
        except CaptureMetaCorrupt as exc:
            return self._refuse_corrupt(capture_digest, merged, exc)
        if captured is None or captured != text:
            return self._record(DocIndexResult(
                doc_id_, "capture_mismatch",
                detail=f"text is not the capture {capture_digest}.txt, so its pointers would "
                       "not resolve"))
        return self._index(text, doc_id_, capture_digest, merged)

    def _mask_derivation(self) -> str:
        return f"chunk_screen.mask(screen={self.screen.source if self.screen else '?'})"

    def _index(self, text: str, doc_id: str, capture_digest: str, meta: dict) -> DocIndexResult:
        assert self.conn is not None and self.screen is not None, "open() the session first"
        self._heartbeat()
        meta.pop("override_allow", None)        # never authority, from metadata or anywhere else
        conn = self.conn
        old = conn.execute("SELECT sha256, chunker FROM documents WHERE doc_id=?",
                           (doc_id,)).fetchone()
        current_policy = not self._rescreen or doc_id in self._screened
        if (old is not None and old["sha256"] == capture_digest
                and old["chunker"] == CHUNKER_VERSION and current_policy):
            missing = conn.execute(
                "SELECT COUNT(*) FROM chunks c LEFT JOIN embeddings e USING(chunk_id) "
                "WHERE c.doc_id=? AND e.chunk_id IS NULL", (doc_id,)).fetchone()[0]
            if missing == 0:
                return self._record(DocIndexResult(doc_id, "unchanged",
                                                   detail="content hash unchanged"))
        reuse: dict[str, tuple[bytes, str, int]] = {}
        if old is not None:
            for row in conn.execute(
                    "SELECT c.context_header, c.content, e.vector, e.model, e.dim FROM chunks c "
                    "JOIN embeddings e USING(chunk_id) WHERE c.doc_id=?", (doc_id,)):
                reuse[_sha(f"{row[0]}\n{row[1]}")] = (row[2], row[3], row[4])
            st.delete_document(conn, doc_id)

        title = meta.get("title") or ""
        uri = meta.get("uri") or meta.get("source_url")
        ext = Path(str(uri or title)).suffix.lower() or None
        mime = meta.get("mime")
        page_count = meta.get("page_count")
        tabular = is_tabular(mime, ext)
        regions = regions_for(text, meta)
        chunks = chunk_document(text, regions=regions, page_count=page_count, tabular=tabular)
        if not chunks:
            conn.commit()
            return self._record(DocIndexResult(doc_id, "empty_text",
                                               detail="extracted text is empty"))

        self._screened.add(doc_id)
        excluded = reused = stored = 0
        red_counts: dict[str, int] = {}
        now = _now()
        doc_type = mime or ext              # unknown stays None: never guessed as "text"
        if doc_type is None:
            st.record_event(conn, "document_type_unknown", doc_id, now,
                            capture_sha256=capture_digest,
                            rationale="no MIME type and no file extension: doc_type is stored as "
                                      "unknown (NULL), so no kind filter can match it by guess")
        for ch in chunks:
            sr = self.screen.evaluate(ch.text)
            if sr.decision != "allow":
                rules = json.dumps(sr.rules)
                score = None if sr.score == float("inf") else sr.score
                conn.execute(
                    "INSERT OR REPLACE INTO excluded_chunks VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, ch.index, ch.char_start, ch.char_end, sr.decision, rules,
                     sr.reverse, sr.rationale, score, now))
                st.record_event(conn, "chunk_excluded", doc_id, now,
                                capture_sha256=capture_digest, chunk_index=ch.index,
                                char_start=ch.char_start, char_end=ch.char_end,
                                decision=sr.decision, rules_json=rules, reverse=sr.reverse,
                                rationale=sr.rationale, score=score)
                excluded += 1
                continue
            content = sr.masked_text
            if len(content) != len(ch.text):
                # masks are same-length by contract; anything else would break pointer offsets
                raise RuntimeError(f"screen changed chunk length for {doc_id}#{ch.index}")
            for red in sr.redactions:
                red_counts[red["rule"]] = red_counts.get(red["rule"], 0) + 1
            chash = _sha(content)
            header = " — ".join(x for x in (title, ch.section, ch.locator) if x)
            chunk_id = _sha(f"{doc_id}|{ch.index}|{chash}")[:32]
            conn.execute(
                "INSERT INTO chunks(chunk_id, doc_id, chunk_index, section, char_start, char_end, "
                "locator, approx_locator, context_header, content, content_hash, chunk_kind, "
                "derivation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (chunk_id, doc_id, ch.index, ch.section, ch.char_start, ch.char_end,
                 ch.locator, int(ch.approx_locator), header, content, chash, ch.chunk_kind,
                 None if content == ch.text else self._mask_derivation()))
            stored += 1
            embed_text = f"{header}\n{content}"
            hit = reuse.get(_sha(embed_text))
            if hit is not None:
                conn.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                             (chunk_id, hit[0], hit[1], hit[2]))
                reused += 1
            else:
                self._pending.append((chunk_id, embed_text))

        decision = "allow" if not excluded else ("partial" if stored else "excluded")
        conn.execute(
            "INSERT OR REPLACE INTO documents VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, self.source_id, uri, title or None, doc_type, ext,
             meta.get("retrieved_at"), capture_digest, len(text), page_count, self.tier,
             decision, f"{excluded} of {len(chunks)} chunk(s) excluded by the per-chunk screen"
             if excluded else "all chunks allowed", meta.get("conditions"),
             int(bool(red_counts)), json.dumps(red_counts, sort_keys=True),
             meta.get("summary"), json.dumps(meta.get("topics")) if meta.get("topics") else None,
             now, CHUNKER_VERSION))
        conn.commit()
        if len(self._pending) >= self.embed_flush:
            self.flush()
        return self._record(DocIndexResult(doc_id, "indexed", chunks=stored, excluded=excluded,
                                           reused_vectors=reused))

    def remove(self, doc_id: str, *, reason: str = "removed by the build (not in inventory)"
               ) -> DocIndexResult:
        assert self.conn is not None
        row = self.conn.execute("SELECT sha256 FROM documents WHERE doc_id=?",
                                (doc_id,)).fetchone()
        st.record_event(self.conn, "document_removed", doc_id, _now(),
                        capture_sha256=row[0] if row else None, rationale=reason)
        st.delete_document(self.conn, doc_id)
        self.conn.commit()
        return self._record(DocIndexResult(doc_id, "removed"))

    def retain_only(self, doc_ids: Iterable[str]) -> list[DocIndexResult]:
        """Remove every document not in ``doc_ids`` (a full sync's inventory)."""
        assert self.conn is not None
        keep = set(doc_ids)
        gone = [r[0] for r in self.conn.execute("SELECT doc_id FROM documents")
                if r[0] not in keep]
        return [self.remove(d) for d in gone]

    # -- embeddings ------------------------------------------------------------------------------
    def _embed(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        self._heartbeat()
        texts = [t for _, t in items]
        res = self.harness.embed(texts, batch_size=self.embed_batch, workers=self.workers,
                                 source_id=self.source_id)
        if self._model is not None and res.model != self._model:
            raise RuntimeError(f"embedding model changed mid-build: {self._model} → {res.model}")
        if len(res.vectors) != len(items):
            raise RuntimeError(f"embedding returned {len(res.vectors)} vectors for "
                               f"{len(items)} texts")
        bad = {len(v) for v in res.vectors} - {res.dim}
        if bad or not res.dim:
            raise RuntimeError(f"embedding vectors of length {sorted(bad)} under declared "
                               f"dim {res.dim}")
        if self._dim is not None and res.dim != self._dim:
            raise RuntimeError(f"embedding dimension changed mid-build under {res.model}: "
                               f"{self._dim} → {res.dim}")
        self._model, self._offline, self._label, self._dim = (res.model, res.offline, res.label,
                                                              res.dim)
        self._embed_texts += len(texts)
        self._embed_chars += sum(len(t) for t in texts)
        self._embed_seconds += res.duration_ms / 1000
        self.conn.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)",
                              [(cid, to_blob(v), res.model, res.dim)
                               for (cid, _), v in zip(items, res.vectors)])
        self.conn.commit()

    def flush(self) -> None:
        pending, self._pending = self._pending, []
        # a chunk deleted since it was queued (its document re-indexed or dropped before this
        # flush) is not embedded: an orphan vector would break the count reconciliation
        ids = list(dict.fromkeys(cid for cid, _ in pending))
        live: set[str] = set()
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            live.update(r[0] for r in self.conn.execute(
                f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({','.join('?' * len(part))})",
                part))
        pending = list({cid: t for cid, t in pending if cid in live}.items())
        for i in range(0, len(pending), max(self.embed_flush, 1)):
            self._embed(pending[i:i + self.embed_flush])

    def _reembed(self, where: str, params: tuple = ()) -> int:
        rows = self.conn.execute(
            "SELECT c.chunk_id, c.context_header, c.content FROM chunks c "
            f"LEFT JOIN embeddings e USING(chunk_id) WHERE {where}", params).fetchall()
        items = [(r[0], f"{r[1]}\n{r[2]}") for r in rows]
        for i in range(0, len(items), max(self.embed_flush, 1)):
            self._embed(items[i:i + self.embed_flush])
        return len(items)

    # -- pointer verification ---------------------------------------------------------------------
    def _chunks_resolve(self, doc_id: str, text: str) -> bool:
        """Every chunk span lies inside ``text`` and its stored content is that span, or the
        screen's exact same-length mask of it (then ``derivation`` names the screen)."""
        mask_deriv = self._mask_derivation()
        fixes: list[tuple[Optional[str], str]] = []
        for cid, cs, ce, content, deriv in self.conn.execute(
                "SELECT chunk_id, char_start, char_end, content, derivation FROM chunks "
                "WHERE doc_id=?", (doc_id,)).fetchall():
            if not (0 <= cs <= ce <= len(text)):
                return False
            raw = text[cs:ce]
            if content == raw:
                want = None
            elif (len(content) == len(raw)
                  and self.screen.evaluate(raw).masked_text == content):
                want = mask_deriv
            else:
                return False
            if deriv != want:
                fixes.append((want, cid))
        if fixes:
            self.conn.executemany("UPDATE chunks SET derivation=? WHERE chunk_id=?", fixes)
        return True

    def _verify_pointers(self) -> None:
        """Make every pointer this store will publish resolve against the capture store."""
        docs = self.conn.execute(
            "SELECT doc_id, source_url, sha256, title, page_count, retrieved_at FROM documents"
        ).fetchall()
        now = _now()
        for d in docs:
            doc_id, uri, sha = d["doc_id"], d["source_url"], d["sha256"]
            corrupt: Optional[str] = None
            try:
                text, cap_meta = read_capture_checked(sha, self.capture_root)
            except CaptureMetaCorrupt as exc:
                text, cap_meta, corrupt = None, {}, str(exc)
            try:
                expected = doc_id_for({"uri": uri}, source_id=self.source_id)
            except ValueError:
                expected = None
            if corrupt is not None or text is None or expected is None:
                why = (f"capture metadata failed its digest check ({corrupt})" if corrupt
                       else "capture text is gone" if text is None
                       else "no path to derive an identity from")
                st.record_event(self.conn, "document_unresolvable", doc_id, now,
                                capture_sha256=sha, rationale=f"dropped from the index: {why}")
                st.delete_document(self.conn, doc_id)
                self.dropped.append({"doc_id": doc_id, "uri": uri, "reason": why})
                continue
            current_policy = not self._rescreen or doc_id in self._screened
            if expected == doc_id and current_policy and self._chunks_resolve(doc_id, text):
                continue
            # an identity from before the hashed-path rule, or chunks that no longer derive from
            # the capture: rebuild the document from the capture itself
            st.delete_document(self.conn, doc_id)
            meta = {**cap_meta, "uri": uri, "title": d["title"], "page_count": d["page_count"],
                    "retrieved_at": d["retrieved_at"]}
            for k in ("item_id", "doc_id", "override_allow"):
                meta.pop(k, None)
            self._index(text, expected, sha, meta)
        self.conn.commit()

    # -- publication -----------------------------------------------------------------------------
    def _derived_scope(self, docs: int) -> str:
        exts = self.conn.execute(
            "SELECT COALESCE(extension,'(none)'), COUNT(*) n FROM documents GROUP BY 1 "
            "ORDER BY n DESC LIMIT 3").fetchall()
        titles = [r[0] for r in self.conn.execute(
            "SELECT title FROM documents WHERE title IS NOT NULL ORDER BY title LIMIT 3")]
        ext_s = ", ".join(f"{n} {e}" for e, n in exts)
        t_s = f"; e.g. {', '.join(titles)}" if titles else ""
        return f"{self.source_id} [{self.tier}]: {docs} document(s) ({ext_s}){t_s}"

    def publish(self, force: Optional[bool] = None) -> PublishReport:
        assert self.conn is not None, "session is not open"
        force = self.force if force is None else force
        try:
            self._fence()
            self._verify_pointers()
            self.flush()
            # reconcile: every chunk must carry a vector from ONE model before publication
            self._reembed("e.chunk_id IS NULL")
            if self._model is None:
                # nothing was embedded this session, so the CURRENT embedding route is unknown:
                # probe it with one stored chunk. A changed route then re-embeds the whole store
                # below, rather than leaving vectors no query can be compared with.
                measured = (self._embed_texts, self._embed_chars, self._embed_seconds)
                self._reembed("c.rowid IN (SELECT rowid FROM chunks ORDER BY rowid LIMIT 1)")
                # one probe text is not throughput evidence (P17): keep the measured figures
                self._embed_texts, self._embed_chars, self._embed_seconds = measured
            model = self._model or st.get_meta(self.conn, "embed_model")
            if self._model is None:
                row = self.conn.execute(
                    "SELECT model, COUNT(*) n FROM embeddings GROUP BY model ORDER BY n DESC"
                ).fetchone()
                model = row[0] if row else model
            for _ in range(2):          # a second pass settles on the harness's current model
                stale = self.conn.execute("SELECT COUNT(*) FROM embeddings WHERE model<>?",
                                          (model,)).fetchone()[0] if model else 0
                if not stale:
                    break
                self._reembed("e.model <> ?", (model,))
                model = self._model
            c = st.counts(self.conn)
            retrievable = self.conn.execute(
                "SELECT COUNT(DISTINCT doc_id) FROM chunks").fetchone()[0]
            reasons = []
            if c["documents"] == 0 or c["chunks"] == 0:
                reasons.append(f"refusing to publish an EMPTY store ({c['documents']} documents, "
                               f"{c['chunks']} chunks) — it would replace a working store with "
                               "one that answers nothing (P11)")
            elif not force and self.previous_documents and (
                    c["documents"] < SHRINK_FLOOR * self.previous_documents):
                reasons.append(f"refusing to publish: documents shrank {self.previous_documents} → "
                               f"{c['documents']} (below {SHRINK_FLOOR:.0%}); a sudden shrink "
                               "means a broken run. Re-run with force=True if intended (P11)")
            elif not force and self.previous_retrievable and (
                    retrievable < SHRINK_FLOOR * self.previous_retrievable):
                reasons.append(f"refusing to publish: retrievable documents (with at least one "
                               f"searchable chunk) shrank {self.previous_retrievable} → "
                               f"{retrievable} (below {SHRINK_FLOOR:.0%}) — e.g. a screen that now "
                               "excludes everything. Re-run with force=True if intended (P11)")
            elif not force and (self.previous_chunks or 0) >= SHRINK_MIN_CHUNKS and (
                    c["chunks"] < SHRINK_FLOOR * self.previous_chunks):
                reasons.append(f"refusing to publish: searchable chunks shrank "
                               f"{self.previous_chunks} → {c['chunks']} (below {SHRINK_FLOOR:.0%} "
                               f"of a store of at least {SHRINK_MIN_CHUNKS} chunks) — e.g. a "
                               "truncated capture. Re-run with force=True if intended (P11)")
            if not reasons and c["embeddings"] != c["chunks"]:
                reasons.append(f"counts do not reconcile: {c['chunks']} chunks vs "
                               f"{c['embeddings']} embeddings")
            if not reasons:
                dims, bad = self.conn.execute(
                    "SELECT COUNT(DISTINCT dim), SUM(length(vector) <> dim * 4) FROM embeddings"
                ).fetchone()
                if dims != 1 or bad:
                    reasons.append(f"embeddings do not share one dimension ({dims} distinct, "
                                   f"{bad or 0} vector(s) whose length disagrees with its dim)")
            if not reasons:
                ok = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
                if ok != "ok":
                    reasons.append(f"integrity_check failed: {ok}")
            meta_offline = self._offline if self._model else bool(
                st.get_meta(self.conn, "embed_offline"))
            meta_label = self._label if self._model else st.get_meta(self.conn, "embed_label")
            report = PublishReport(
                store=self.published_path.name, source_id=self.source_id, tier=self.tier,
                published=False, documents=c["documents"], chunks=c["chunks"],
                embeddings=c["embeddings"], excluded_chunks=c["excluded_chunks"],
                previous_documents=self.previous_documents, centroids=0,
                embedding_model=model, offline_embedding=meta_offline,
                embedding_label=meta_label, reasons=reasons, dropped=list(self.dropped))
            if reasons:
                raise _refused(reasons, report)

            ids, m = load_matrix(self.conn, model)
            dim = int(m.shape[1]) if m.size else self._dim
            k = centroid_count(len(ids))
            cent, sizes = kmeans(m, k)
            del m
            self.conn.execute("DELETE FROM centroids")
            self.conn.executemany("INSERT INTO centroids VALUES (?,?,?)",
                                  [(i, to_blob(cent[i]), int(sizes[i]))
                                   for i in range(cent.shape[0])])
            throughput = {
                "texts_embedded": self._embed_texts, "chars_embedded": self._embed_chars,
                "seconds": round(self._embed_seconds, 4),
                "texts_per_second": (round(self._embed_texts / self._embed_seconds, 1)
                                     if self._embed_seconds > 0 else None),
                "chars_per_second": (round(self._embed_chars / self._embed_seconds, 1)
                                     if self._embed_seconds > 0 else None),
                "measured_at": _now(),
            } if self._embed_texts else st.get_meta(self.conn, "throughput")
            explicit = (st.get_meta(self.conn, "scope")
                        if st.get_meta(self.conn, "scope_explicit") else None)
            scope = self.scope or explicit or self._derived_scope(c["documents"])
            for key, val in {
                "embed_model": model, "embed_dim": dim, "embed_offline": meta_offline,
                "embed_label": meta_label, "scope": scope,
                "screen_digest": self.screen.digest, "screen_source": self.screen.source,
                "chunker": CHUNKER_VERSION, "published_at": _now(), "throughput": throughput,
                "counts": c,
            }.items():
                st.set_meta(self.conn, key, val)
            if self.scope:
                st.set_meta(self.conn, "scope_explicit", True)
            self.conn.commit()
            self.conn.close()
            self.conn = None
            self._fence()                   # ownership re-checked immediately before the replace
            _replace(self.staging_path, self.published_path)
            report.published = True
            report.centroids = int(cent.shape[0])
            report.throughput = throughput or {}
            report.search_ram_bytes = search_ram_bytes(c["embeddings"], dim)
            report.search_ram_mb = search_ram_mb(c["embeddings"], dim)
            router.refresh(self.root)
            return report
        finally:
            self._cleanup()


def _refused(reasons: list[str], report: PublishReport) -> PublishRefused:
    err = PublishRefused("; ".join(reasons))
    err.report = report  # type: ignore[attr-defined]
    return err


def _replace(src: Path, dst: Path, attempts: int = 20) -> None:
    """Atomic replace; on Windows a reader holding the file open makes this fail briefly."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


def index_capture(sha256: str, meta: Optional[dict] = None, *, force: bool = False,
                  harness: Any = None, tier: Optional[str] = None,
                  passages_dir: Optional[Path] = None, capture_dir: Optional[Path] = None,
                  pack: Optional[str] = None, override_allow: bool = False) -> DocIndexResult:
    """Index ONE captured item into its source's store and publish (BUILD_PLAN §5.5 entry point).

    ``meta`` may carry ``source``/``source_id`` (else the capture ``.json``'s ``source``), ``uri``
    (else the capture's), ``item_id`` (checked against the computed identity), ``title``,
    ``mime``, ``tier``, ``pack``, ``page_count``, ``locators``. For many items use one
    ``BuildSession`` — it copies and verifies the store once, not once per item.
    """
    try:
        _, cap_meta = read_capture_checked(sha256, capture_dir)
    except CaptureMetaCorrupt as exc:
        return DocIndexResult(f"capture:{sha256}", "capture_corrupt",
                              detail=f"capture metadata failed its digest check ({exc}); "
                                     "nothing was indexed")
    merged = {**cap_meta, **(meta or {})}
    source_id = merged.get("source_id") or merged.get("source")
    if not source_id:
        raise ValueError(f"capture {sha256}: no source/source_id in meta or capture .json")
    session = BuildSession(str(source_id), tier or merged.get("tier") or DEFAULT_TIER,
                           harness=harness, passages_dir=passages_dir, capture_dir=capture_dir,
                           pack=pack or merged.get("pack"), force=force)
    session.open()
    try:
        result = session.index_capture(sha256, merged, override_allow=override_allow)
    except BaseException:
        session.abort()
        raise
    session.publish()
    return result
