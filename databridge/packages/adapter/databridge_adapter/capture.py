"""The Capture Store — content-addressed, write-once storage for bytes that must outlive a source.

Why this exists (spine D6, `ADAPTER_AGENT_SPEC.md` §5)
-----------------------------------------------------

A pointer is only as good as the source's ability to prove what it said at a past instant, and
most sources cannot:

* **T0** — native snapshot / time travel. Can serve the past, within a retention window.
* **T1** — a forward-only cursor (an LSN, a mailbox `historyId`). Replays *forward*; it cannot
  reconstruct the past, and its window closes.
* **T2** — current-addressable only. There is just "now".
* **T3** — unversioned. There was never a past to return to.

So for everything below T0, a citation into the live source is a promise the system cannot keep.
The rule is therefore: **anything ledger-bound is captured** — T3 always. An `email_part` or
`ocr_bbox` pointer resolves against the capture, not the mailbox or the scanner, and
`SourcePointer.capture_digest` is what binds the two together. Without that field the store is
decoration: nobody could prove *which* bytes were parsed.

What "write-once" means here, honestly
--------------------------------------

Real WORM is a property of the storage layer — S3 Object Lock in COMPLIANCE mode, or an appliance.
This module cannot manufacture that on a local filesystem, and pretending otherwise would be the
same class of error as a tier overclaim: a promise that a citation stays checkable when it does
not. What it *does* enforce:

* **content addressing** — the digest IS the name, so a different byte string is a different
  object and cannot overwrite an existing one;
* **refusal to rewrite** — putting content whose digest already exists is a no-op that returns the
  existing capture, and a digest collision with *different* bytes raises rather than overwriting;
* **read-only permission bits** on written files, which stops the accident, not the adversary;
* **verification on read** — bytes are re-digested when fetched, so silent corruption or an
  out-of-band edit is detected rather than served.

`CaptureStore.durability()` reports which of these the backing store actually provides, so a
deployment can be honest in `describe()` about how long a citation stays checkable.

Erasure
-------

Captured content is personal data. `shred()` removes the bytes and leaves a tombstone recording
that a capture existed, when it was destroyed and under what authority — because an append-only
ledger that cited it must still resolve to *something*, and "destroyed under erasure request R-42"
is an honest answer where a missing file is not. A pointer whose capture was shredded verifies as
`VANISHED`, never as `VERIFIED`.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

__all__ = [
    "Capture",
    "CaptureStore",
    "CaptureError",
    "CaptureNotFound",
    "CaptureCorrupt",
    "CaptureShredded",
    "Durability",
]

#: How the digest is computed. Part of the stored identity, so changing it is a migration, not a
#: tweak — every existing `capture_digest` would stop resolving.
DIGEST_ALGO = "sha256"

Retention = Literal["ledger_bound", "transient"]


class CaptureError(RuntimeError):
    """Base for every refusal from the capture store."""


class CaptureNotFound(CaptureError):
    """No capture with that digest. Distinct from shredded — never having existed and having been
    destroyed are different facts, and only one of them is answerable."""


class CaptureCorrupt(CaptureError):
    """The stored bytes no longer match the digest that names them. Served to nobody."""


class CaptureShredded(CaptureError):
    """The capture existed and was destroyed under an erasure request. Carries the tombstone."""

    def __init__(self, message: str, tombstone: dict) -> None:
        super().__init__(message)
        self.tombstone = tombstone


@dataclass(frozen=True)
class Durability:
    """What the backing store actually guarantees — reported, never assumed.

    A deployment writing to a local directory and one writing to S3 Object Lock in COMPLIANCE mode
    both work through this class, and they do NOT offer the same promise. `describe()` surfaces
    this so a tier claim and a retention claim can both be honest.
    """

    backend: str
    content_addressed: bool
    rewrite_refused: bool
    #: True only where the STORAGE layer enforces immutability (Object Lock, WORM appliance).
    #: A local filesystem cannot, whatever permission bits are set.
    immutable_at_rest: bool
    retention_enforced: bool
    notes: str = ""


@dataclass(frozen=True)
class Capture:
    """A stored artefact. `digest` is its identity and its filename."""

    digest: str
    media_type: str
    size: int
    captured_at: datetime
    retention: Retention
    source_id: str = ""
    #: Free-form provenance of the capture act itself — which adapter, which request.
    note: str = ""

    @property
    def qualified_digest(self) -> str:
        """`sha256:abc…` — the form that goes into `SourcePointer.capture_digest`.

        Qualified rather than bare hex so the algorithm travels with the value: a bare digest is
        unverifiable the day the algorithm changes, and nobody would know which one produced it.
        """
        return f"{DIGEST_ALGO}:{self.digest}"


@dataclass
class CaptureStore:
    """Content-addressed store over a directory.

    The directory is sharded two levels by digest prefix, because a flat directory of a few hundred
    thousand captures is slow on every filesystem that matters.
    """

    root: Path
    backend: str = "filesystem"
    #: Set true only when the deployment's storage genuinely enforces immutability.
    immutable_at_rest: bool = False
    _meta_name: str = field(default="meta.json", repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    # --- layout ---------------------------------------------------------------------------

    def _dir_for(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:4] / digest

    def _blob(self, digest: str) -> Path:
        return self._dir_for(digest) / "blob"

    def _meta(self, digest: str) -> Path:
        return self._dir_for(digest) / self._meta_name

    def _tombstone(self, digest: str) -> Path:
        return self._dir_for(digest) / "tombstone.json"

    @staticmethod
    def digest_of(content: bytes) -> str:
        return hashlib.new(DIGEST_ALGO, content).hexdigest()

    @staticmethod
    def parse_digest(value: str) -> str:
        """Accept `sha256:abc…` or bare `abc…`; refuse a different algorithm loudly.

        Refusing rather than coercing matters: a digest from another algorithm would silently never
        match, and the failure would look like corruption rather than a configuration error.
        """
        text = str(value or "").strip()
        if not text:
            raise CaptureError("empty capture digest")
        if ":" in text:
            algo, _, hexpart = text.partition(":")
            if algo != DIGEST_ALGO:
                raise CaptureError(
                    f"capture digest names algorithm {algo!r}, but this store computes "
                    f"{DIGEST_ALGO!r}. A digest from another algorithm cannot be checked here, and "
                    f"treating it as opaque would report a configuration error as corruption.")
            text = hexpart
        if len(text) != 64 or any(c not in "0123456789abcdef" for c in text.lower()):
            raise CaptureError(f"not a {DIGEST_ALGO} digest: {value!r}")
        return text.lower()

    # --- writing --------------------------------------------------------------------------

    def put(self, content: bytes, *, media_type: str, retention: Retention = "ledger_bound",
            source_id: str = "", note: str = "") -> Capture:
        """Store `content` and return its `Capture`. Idempotent by content.

        Putting bytes that are already stored returns the existing capture unchanged — the digest
        is the identity, so re-capturing the same artefact is not a new artefact. The *metadata* of
        the first capture wins, because that is the one a pointer may already cite: `_write_meta`
        commits with `O_EXCL`, so a second writer's metadata for the same digest is dropped rather
        than overwriting the one a citation may already point at (finding 14's "concurrent first
        writers can overwrite 'first capture wins' metadata").

        Blob and metadata are two files and this is not a transaction — a crash between them is
        possible and `durability()` says so honestly. What this method DOES do about it: the blob
        is checked and, if present with matching bytes but no metadata (a prior write that crashed
        after installing the blob and before its metadata), the metadata is written now rather than
        leaving a capture that no later `put()` or read of that digest could ever complete.
        """
        if not isinstance(content, (bytes, bytearray)):
            raise CaptureError("capture content must be bytes")
        content = bytes(content)
        digest = self.digest_of(content)

        tomb = self._tombstone(digest)
        if tomb.is_file():
            raise CaptureShredded(
                f"capture {digest[:12]}… was shredded and may not be re-created: re-capturing "
                f"content destroyed under an erasure request would undo the erasure.",
                json.loads(tomb.read_text(encoding="utf-8")))

        blob = self._blob(digest)
        meta_path = self._meta(digest)
        if blob.is_file():
            existing = blob.read_bytes()
            if existing != content:
                # Cannot happen with an intact digest; if it does, something is very wrong and
                # overwriting would destroy the artefact a pointer already cites.
                raise CaptureCorrupt(
                    f"digest collision at {digest[:12]}…: stored bytes differ from the content "
                    f"being written. Refusing to overwrite.")
            if not meta_path.is_file():
                # The repair path: the bytes are here and match, only the metadata is missing.
                self._write_meta(digest, self._meta_payload(
                    digest, media_type, len(content), retention, source_id, note,
                    datetime.now(timezone.utc)))
            return self._read_meta(digest)

        blob.parent.mkdir(parents=True, exist_ok=True)
        tmp = blob.with_suffix(".partial")
        tmp.write_bytes(content)
        os.replace(tmp, blob)                       # atomic: a reader never sees a partial blob

        self._write_meta(digest, self._meta_payload(
            digest, media_type, len(content), retention, source_id, note,
            datetime.now(timezone.utc)))
        self._make_read_only(blob)
        return self._read_meta(digest)

    @staticmethod
    def _meta_payload(digest: str, media_type: str, size: int, retention: str, source_id: str,
                      note: str, captured_at: datetime) -> dict:
        return {
            "digest": digest,
            "media_type": media_type,
            "size": size,
            "captured_at": captured_at.isoformat(),
            "retention": retention,
            "source_id": source_id,
            "note": note,
            "algo": DIGEST_ALGO,
        }

    def _write_meta(self, digest: str, payload: dict) -> None:
        """Commit `meta.json` with `O_EXCL` — first writer wins, atomically.

        Not a temp-file-and-rename: the metadata is small enough to write in one syscall, and
        `O_CREAT | O_EXCL` is what makes "first wins" true under a race rather than aspirational —
        a second writer's `open()` fails with `FileExistsError` and its metadata is simply not
        written, leaving the first writer's in place.
        """
        path = self._meta(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return                                   # another writer's metadata already won
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def _make_read_only(self, path: Path) -> None:
        """Drop write bits. Stops the accident, not the adversary — and `durability()` says so."""
        try:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except OSError:                                              # pragma: no cover
            pass

    # --- reading --------------------------------------------------------------------------

    def get(self, digest: str) -> bytes:
        """Return the captured bytes, verifying them against the digest that names them.

        Verification on every read is deliberate. Silent corruption, a filesystem fault or an
        out-of-band edit would otherwise be served as though it were the cited artefact — which is
        exactly the failure the capture store exists to prevent.
        """
        d = self.parse_digest(digest)
        tomb = self._tombstone(d)
        if tomb.is_file():
            raise CaptureShredded(
                f"capture {d[:12]}… was destroyed under an erasure request",
                json.loads(tomb.read_text(encoding="utf-8")))
        blob = self._blob(d)
        if not blob.is_file():
            raise CaptureNotFound(f"no capture {d[:12]}… in {self.root}")
        content = blob.read_bytes()
        actual = self.digest_of(content)
        if actual != d:
            raise CaptureCorrupt(
                f"capture {d[:12]}… no longer matches its digest (found {actual[:12]}…). The "
                f"stored bytes are not the cited artefact and will not be served.")
        return content

    def exists(self, digest: str) -> bool:
        return self._blob(self.parse_digest(digest)).is_file()

    def _read_meta(self, digest: str) -> Capture:
        """Read `meta.json` for `digest` — and refuse to hand back a `Capture` naming ANOTHER one.

        `digest` is what the caller (`get`/`put`/`describe`) already computed or validated from the
        actual bytes; the file's own `digest` field is untrusted content on disk, no different from
        anything else a corrupted or tampered `meta.json` could say. Finding 8: without this check,
        a `put()` that hit the "already stored" branch would return whatever the file claims, and a
        pointer minted from it would carry a `capture_digest` that names a DIFFERENT capture than
        the one whose bytes were actually just verified — the store's whole point, defeated by its
        own metadata.
        """
        path = self._meta(digest)
        if not path.is_file():
            raise CaptureCorrupt(
                f"capture {digest[:12]}…'s bytes are stored but its metadata is missing — a prior "
                f"write did not complete. Re-`put()`ing the same content repairs it.")
        meta = json.loads(path.read_text(encoding="utf-8"))
        stored_digest = str(meta.get("digest") or "").lower()
        algo = str(meta.get("algo") or "")
        if stored_digest != digest.lower() or algo != DIGEST_ALGO:
            raise CaptureCorrupt(
                f"capture {digest[:12]}…'s metadata names digest {stored_digest[:12] or '(none)'}"
                f"… (algo {algo!r}), which does not match the {digest[:12]}… this store was asked "
                f"for. A pointer's capture_digest is only trustworthy if the metadata it was "
                f"minted from names the same bytes it is stored under; refusing to return a "
                f"Capture that could point a citation at the wrong content.")
        return Capture(
            digest=digest, media_type=meta.get("media_type", ""),
            size=int(meta.get("size", 0)),
            captured_at=datetime.fromisoformat(meta["captured_at"]),
            retention=meta.get("retention", "ledger_bound"),
            source_id=meta.get("source_id", ""), note=meta.get("note", ""))

    def describe(self, digest: str) -> Capture:
        d = self.parse_digest(digest)
        if not self._meta(d).is_file():
            if self._blob(d).is_file():
                raise CaptureCorrupt(
                    f"capture {d[:12]}…'s bytes are stored but its metadata is missing — a prior "
                    f"write did not complete. Re-`put()`ing the same content repairs it.")
            raise CaptureNotFound(f"no capture {d[:12]}… in {self.root}")
        return self._read_meta(d)

    # --- erasure --------------------------------------------------------------------------

    def shred(self, digest: str, *, authority: str, reason: str) -> dict:
        """Destroy the bytes, keep the fact. Returns the tombstone.

        An append-only ledger may cite this capture, and a citation that resolves to *nothing* is
        indistinguishable from a bug. "Destroyed on this date under this authority" is an answer;
        a missing file is not. A pointer to a shredded capture verifies as `VANISHED`.

        The tombstone is committed BEFORE the blob is unlinked, and atomically (temp file, then
        `os.replace`) — deliberately the opposite order from the previous version, which deleted
        first. A crash between the two steps used to be able to lose the erasure authority and
        reason entirely (bytes gone, no tombstone — indistinguishable from `CaptureNotFound`, which
        is exactly the fact this method exists to avoid). Committing the tombstone first means a
        crash after that point leaves the honest answer on disk even if the bytes have not yet been
        physically removed; `get()` checks the tombstone before it ever looks at the blob, so a
        reader never sees the not-yet-deleted bytes. A retry of `shred()` for an already-tombstoned
        digest returns the ORIGINAL tombstone unchanged, rather than overwriting who authorised the
        erasure and why.
        """
        if not str(authority or "").strip():
            raise CaptureError(
                "shredding requires a named authority — an erasure nobody can be attributed to is "
                "not an erasure, it is data loss")
        d = self.parse_digest(digest)
        tomb_path = self._tombstone(d)
        if tomb_path.is_file():
            return json.loads(tomb_path.read_text(encoding="utf-8"))
        blob = self._blob(d)
        if not blob.is_file() and not self._meta(d).is_file():
            raise CaptureNotFound(f"no capture {d[:12]}… to shred")

        tombstone = {
            "digest": d,
            "shredded_at": datetime.now(timezone.utc).isoformat(),
            "authority": authority,
            "reason": reason,
            "algo": DIGEST_ALGO,
        }
        tomb_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tomb_path.with_suffix(".partial")
        tmp.write_text(json.dumps(tombstone, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, tomb_path)
        if blob.is_file():
            try:
                blob.chmod(stat.S_IWUSR | stat.S_IRUSR)
            except OSError:                                          # pragma: no cover
                pass
            blob.unlink()
        return tombstone

    # --- honesty --------------------------------------------------------------------------

    def durability(self) -> Durability:
        """What this store actually guarantees. Surfaced in `describe()`.

        A local filesystem cannot enforce immutability, and saying so is the difference between a
        retention claim a buyer can rely on and one that fails the first time it is tested.
        """
        return Durability(
            backend=self.backend,
            content_addressed=True,
            rewrite_refused=True,
            immutable_at_rest=self.immutable_at_rest,
            retention_enforced=self.immutable_at_rest,
            notes=("" if self.immutable_at_rest else
                   "filesystem backend: read-only permission bits stop accidents, not an "
                   "operator with write access. For a retention guarantee a citation can rely "
                   "on, back this with S3 Object Lock (COMPLIANCE) or a WORM appliance."),
        )
