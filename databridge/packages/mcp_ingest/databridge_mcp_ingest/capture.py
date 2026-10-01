"""The Capture Store — raw bytes first, content-addressed, write-once (spine D6; principles §0).

Layout (frozen contract, BUILD_PLAN §5.2 — other packages read it)::

    WALNUT_CAPTURE_DIR/<sha[:2]>/<sha256>                raw bytes, never overwritten
                      /<sha[:2]>/<sha256>.txt            extracted text, UTF-8 — every doc_span
                                                         pointer with capture_digest=<sha256>
                                                         indexes into THIS text
                      /<sha[:2]>/<sha256>.json           {source, uri, mime, title, retrieved_at}
                      /<sha[:2]>/<sha256>.txt.json       (additive) {text_sha256, extractor,
                                                         capture_meta_digest}: the digest of
                                                         the extracted text, so a reader can
                                                         verify `.txt` without re-extracting
                      /<sha[:2]>/<sha256>.redacted.txt   (additive) the same text with contact
                                                         identifiers masked, SAME LENGTH, so the
                                                         same offsets resolve in both

The same bytes seen at a second location keep the first `.json` and gain a `sightings` list
(additive), because one content address cannot carry two first locations.

The `.json` is bound to its content address (R-019-real-connections deferred #5): it carries
`meta_digest` = sha256 over the content sha and the first sighting's record (`meta_digest()`;
`sightings` is additive and excluded), and `get_meta` refuses a file whose digest does not match —
metadata swapped between two captures, or edited in place, raises `CaptureCorrupt`. The runner
also anchors the digest in `captured.jsonl` (`meta_digest`), so a reader holding the ledger
(`get_meta(expect_digest=...)`) also refuses a file whose digest was stripped.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from . import paths


def cache_root() -> Path:
    """Derived-output caches (model output per chunk, document conversions), keyed by content.
    `WALNUT_CACHE_DIR`, else a `cache/` beside the ingest directory — so a test that points
    `WALNUT_INGEST_DIR` at a temp dir gets an isolated cache without setting anything else, and the
    default install lands in `var/cache/`. A cache is disposable: deleting it costs re-work only."""
    value = os.environ.get("WALNUT_CACHE_DIR")
    return Path(value) if value else paths.ingest_dir().parent / "cache"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dir(sha: str, root: Optional[Path] = None) -> Path:
    return (root or paths.capture_dir()) / sha[:2]


def blob_path(sha: str, root: Optional[Path] = None) -> Path:
    return _dir(sha, root) / sha


def text_path(sha: str, root: Optional[Path] = None) -> Path:
    return _dir(sha, root) / f"{sha}.txt"


def redacted_path(sha: str, root: Optional[Path] = None) -> Path:
    return _dir(sha, root) / f"{sha}.redacted.txt"


def meta_path(sha: str, root: Optional[Path] = None) -> Path:
    return _dir(sha, root) / f"{sha}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid AND a random part: two threads of one process never share a temp file (Codex f1 #8)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class CaptureCorrupt(RuntimeError):
    """Bytes on disk do not hash to their own name — the store was altered."""


class TextConflict(CaptureCorrupt):
    """A DIFFERENT extraction of the same captured bytes is already stored (Codex f2 #5). The
    extracted text is write-once: every `doc_span` pointer with this capture digest indexes into
    it, so it is never replaced. `existing_digest` is the stored text's sha256."""

    def __init__(self, message: str, existing_digest: str) -> None:
        super().__init__(message)
        self.existing_digest = existing_digest


def _create_exclusive(path: Path, data: bytes) -> bool:
    """Write `path` atomically ONLY if it does not exist yet (True: this call created it). The
    bytes go to a unique temp file which is then HARD-LINKED into place — a link never replaces an
    existing name, so two writers racing for a first record cannot overwrite each other (Codex f2
    #12). Where hard links are unavailable, an exclusive `open(..., "xb")` decides the winner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.tmp")
    tmp.write_bytes(data)
    try:
        try:
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
        except (AttributeError, NotImplementedError, OSError) as exc:
            if isinstance(exc, FileExistsError) or path.exists():
                return False
            try:
                with open(path, "xb") as fh:
                    fh.write(data)
                return True
            except FileExistsError:
                return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def put_bytes(data: bytes, root: Optional[Path] = None) -> str:
    sha = sha256_bytes(data)
    p = blob_path(sha, root)
    if p.exists():
        if sha256_bytes(p.read_bytes()) != sha:
            raise CaptureCorrupt(f"capture {p} does not hash to its name")
        return sha
    _atomic_write(p, data)
    return sha


def get_bytes(sha: str, root: Optional[Path] = None) -> bytes:
    data = blob_path(sha, root).read_bytes()
    if sha256_bytes(data) != sha:
        raise CaptureCorrupt(f"capture {sha} does not hash to its name")
    return data


def has_blob(sha: str, root: Optional[Path] = None) -> bool:
    return blob_path(sha, root).is_file()


def text_meta_path(sha: str, root: Optional[Path] = None) -> Path:
    return _dir(sha, root) / f"{sha}.txt.json"


def put_text(sha: str, text: str, root: Optional[Path] = None, *,
             text_sha256: Optional[str] = None, extractor: Optional[str] = None) -> Path:
    """Write the extracted text. With ``text_sha256`` (the extractor's `text_digest`) the text must
    hash to it (`CaptureCorrupt` otherwise) and a sidecar `<sha>.txt.json` records it with the
    extractor and the capture's `meta_digest` (R-022 build cross-track 5). The capture's `.json`
    is never rewritten for this: its `meta_digest` is what `captured.jsonl` anchors."""
    p = text_path(sha, root)
    data = text.encode("utf-8")
    if text_sha256 is not None and sha256_bytes(data) != text_sha256:
        raise CaptureCorrupt(f"extracted text for {sha[:12]} does not hash to the digest the "
                             f"extractor recorded")
    # WRITE-ONCE (Codex f2 #5): the same bytes extracted differently (another filename / mime
    # picked another extractor, or a new docling) never replace the text existing pointers index
    if not _create_exclusive(p, data):
        have = p.read_bytes()
        if have != data:
            raise TextConflict(f"extracted text for {sha[:12]} already exists and differs from "
                               f"this extraction; the stored text is kept (pointers index it)",
                               existing_digest=sha256_bytes(have))
    if text_sha256 is not None:
        side = {"text_sha256": text_sha256, "extractor": extractor,
                "capture_meta_digest": stored_meta_digest(sha, root)}
        sp = text_meta_path(sha, root)
        current = get_text_meta(sha, root)
        if not current or current.get("text_sha256") != text_sha256:
            _atomic_write(sp, json.dumps(side, sort_keys=True).encode("utf-8"))
    return p


def put_redacted(sha: str, text: str, root: Optional[Path] = None) -> Path:
    """The SHARED convenience copy `<sha>.redacted.txt` (last writer wins; other packages read it
    by the §5.2 layout). Ingestion never trusts it for a decision — it reads the rule-set-keyed,
    write-once variant (`put_redacted_variant`) and checks its digest against governance."""
    p = redacted_path(sha, root)
    _atomic_write(p, text.encode("utf-8"))
    return p


def redaction_variant(fingerprint: str, override_allow: bool) -> str:
    """The key of ONE masking rule set: the screen's rule fingerprint plus the owner-override flag
    (the two things that change what is masked). 16 hex chars."""
    return sha256_bytes(f"{fingerprint}|override={bool(override_allow)}".encode("utf-8"))[:16]


def redacted_variant_path(sha: str, variant: str, root: Optional[Path] = None) -> Path:
    return _dir(sha, root) / f"{sha}.redacted.{variant}.txt"


def put_redacted_variant(sha: str, variant: str, text: str,
                         root: Optional[Path] = None) -> tuple[Path, str]:
    """Write-once masked text for (content, rule set) — Codex f1 #8: two rule sets masking the same
    bytes no longer overwrite each other's masked view. Returns (path, sha256 of the text). An
    existing file with DIFFERENT text raises `CaptureCorrupt` (the same rule set over the same bytes
    is deterministic, so a difference means the file was altered)."""
    p = redacted_variant_path(sha, variant, root)
    data = text.encode("utf-8")
    digest = sha256_bytes(data)
    if p.is_file():
        if sha256_bytes(p.read_bytes()) != digest:
            raise CaptureCorrupt(f"masked text {p} differs from this rule set's masking of the same "
                                 f"bytes (altered)")
        return p, digest
    _atomic_write(p, data)
    return p, digest


def get_redacted_variant(sha: str, variant: str, *, expect_digest: str,
                         root: Optional[Path] = None) -> str:
    """The masked text of (content, rule set), verified against the digest governance recorded."""
    p = redacted_variant_path(sha, variant, root)
    data = p.read_bytes() if p.is_file() else None
    if data is None or sha256_bytes(data) != expect_digest:
        raise CaptureCorrupt(f"masked text for {sha[:12]} (rule set {variant}) is missing or does "
                             f"not match the digest its screen decision recorded")
    return data.decode("utf-8")


def get_text(sha: str, root: Optional[Path] = None, *,
             expect_text_digest: Optional[str] = None) -> Optional[str]:
    """The extracted text (None: none). With ``expect_text_digest`` (the ledger's `text_sha256`)
    a missing or different text raises `CaptureCorrupt`."""
    p = text_path(sha, root)
    if not p.is_file():
        if expect_text_digest is not None:
            raise CaptureCorrupt(f"extracted text for {sha[:12]} is missing but the ledger "
                                 f"anchors digest {expect_text_digest}")
        return None
    data = p.read_bytes()
    if expect_text_digest is not None and sha256_bytes(data) != expect_text_digest:
        raise CaptureCorrupt(f"extracted text for {sha[:12]} does not match the digest the "
                             f"ledger anchors (altered)")
    return data.decode("utf-8")


def get_text_meta(sha: str, root: Optional[Path] = None) -> Optional[dict]:
    """The `<sha>.txt.json` sidecar (None: none, or unreadable)."""
    p = text_meta_path(sha, root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return raw if isinstance(raw, dict) else None


#: The frozen §5.2 keys — always present (None when unknown), never renamed.
META_KEYS = ("source", "uri", "mime", "title", "retrieved_at")


#: Keys of the `.json` that the digest does not cover: the additive sightings list and itself.
_UNDIGESTED = ("sightings", "meta_digest")


def meta_digest(sha: str, record: dict) -> str:
    """sha256 binding a metadata record to the content address `sha`: the sha, a newline, then
    the canonical JSON of the record without `sightings` / `meta_digest`."""
    body = {k: v for k, v in record.items() if k not in _UNDIGESTED}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes((sha + "\n" + canon).encode("utf-8"))


#: Serialises the read-modify-write of an EXISTING `.json` (legacy digest, sightings) within a
#: process; the FIRST record is decided by an exclusive create, across processes too.
_META_GUARD = threading.Lock()


def put_meta(sha: str, meta: dict, root: Optional[Path] = None) -> Path:
    """Write the first sighting's metadata. The five §5.2 keys are always written; any OTHER key
    with a non-None, JSON-serialisable value (e.g. `derived_from`, `converter`, `export_mime`) is
    kept beside them as additive provenance — a reader of the contract sees the five it expects.

    Codex f2 #4: locator and free-text fields (`uri`, `title`, `attrs`, ...) are scrubbed of
    credentials before they are stored. Codex f2 #12: the first record is created EXCLUSIVELY —
    two sources capturing the same bytes at once cannot replace each other's first sighting; the
    loser is appended to `sightings`."""
    from .ledger import scrub_locators

    meta = scrub_locators(dict(meta))
    p = meta_path(sha, root)
    record = {k: meta.get(k) for k in META_KEYS}
    for k, v in meta.items():
        if k in record or k in _UNDIGESTED or v is None:
            continue
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        record[k] = v
    first = dict(record)
    first["meta_digest"] = meta_digest(sha, first)
    if _create_exclusive(p, json.dumps(first, sort_keys=True).encode("utf-8")):
        return p
    with _META_GUARD:
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        if isinstance(existing, dict):
            changed = False
            if existing.get("meta_digest") is None:
                # a LEGACY first sighting (written before digests): bind it to its content address
                # now, over exactly what it already says (Codex f1 #7) — additive, never a rewrite
                existing["meta_digest"] = meta_digest(sha, existing)
                changed = True
            if (existing.get("source"), existing.get("uri")) != (record["source"], record["uri"]):
                sightings = list(existing.get("sightings") or [])
                key = {"source": record["source"], "uri": record["uri"]}
                if key not in sightings:
                    sightings.append(key)
                    existing["sightings"] = sightings
                    changed = True
            if changed:
                _atomic_write(p, json.dumps(existing, sort_keys=True).encode("utf-8"))
            return p
        # an unreadable existing file is NOT overwritten: get_meta reports it as corrupt
        return p


def stored_meta_digest(sha: str, root: Optional[Path] = None) -> Optional[str]:
    """The `meta_digest` the capture's `.json` carries (None: no file, or a legacy one)."""
    p = meta_path(sha, root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return raw.get("meta_digest") if isinstance(raw, dict) else None


def get_meta(sha: str, root: Optional[Path] = None, *,
             expect_digest: Optional[str] = None) -> Optional[dict]:
    """The capture's metadata, verified against its content address. Raises `CaptureCorrupt` when
    the file carries a `meta_digest` that does not match (swapped or edited), or when
    `expect_digest` (the ledger's anchor) is given and the file does not carry exactly it. A file
    with no digest reads as legacy only when nothing expects one."""
    p = meta_path(sha, root)
    if not p.is_file():
        if expect_digest is not None:
            raise CaptureCorrupt(f"capture metadata {p} is missing but the ledger anchors "
                                 f"digest {expect_digest}")
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CaptureCorrupt(f"capture metadata {p} is not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CaptureCorrupt(f"capture metadata {p} is not an object")
    have = raw.get("meta_digest")
    if have is not None and have != meta_digest(sha, raw):
        raise CaptureCorrupt(f"capture metadata {p} does not match its content address "
                             f"(swapped or edited)")
    if expect_digest is not None and have != expect_digest:
        raise CaptureCorrupt(f"capture metadata {p} carries digest {have!r}, the ledger anchors "
                             f"{expect_digest!r}")
    return raw


__all__ = ["cache_root", "sha256_bytes", "put_bytes", "get_bytes", "has_blob", "put_text", "get_text",
           "put_redacted", "put_meta", "get_meta", "meta_digest", "stored_meta_digest", "blob_path", "text_path", "meta_path",
           "redacted_path", "CaptureCorrupt", "redaction_variant", "redacted_variant_path",
           "put_redacted_variant", "get_redacted_variant", "text_meta_path", "get_text_meta",
           "TextConflict"]
