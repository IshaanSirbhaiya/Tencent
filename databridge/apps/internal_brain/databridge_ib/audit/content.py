"""The answer content store: the ONLY place an answer's text is kept (CONTRACTS §4, plan §4.7).

The ledger holds ``answer_sha256`` and ``answer_ref``; the text lives here, in
``var/ib/content/answers.sqlite`` (``req_id``, ``answer_ref``, ``answer``, ``sha256``). A compliance
officer reads it only through ``POST /audit/reveal``, which is itself ledgered. `get` re-hashes the
text so an edit to this store is visible against the sealed ``answer_sha256`` in the ledger.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import IbConfig


class ContentStoreUnavailable(RuntimeError):
    """The answer could not be stored or read. On the write path the caller answers 503."""


@dataclass(frozen=True)
class StoredAnswer:
    answer_ref: str
    req_id: str
    answer: str
    sha256: str                  # as stored at put()
    created_at: str

    @property
    def intact(self) -> bool:
        """Does the text still hash to the digest recorded when it was stored?"""
        return sha256_text(self.answer) == self.sha256


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    answer_ref TEXT PRIMARY KEY,
    req_id     TEXT NOT NULL UNIQUE,
    answer     TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class AnswerStore:
    """``AnswerStore(cfg)`` — SQLite at ``cfg.content_db``. One answer per request id."""

    def __init__(self, cfg: IbConfig, *, path: Optional[Path] = None) -> None:
        self.path = Path(path or cfg.content_db)
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except (OSError, sqlite3.Error):
            raise ContentStoreUnavailable("the answer content store could not be opened") from None

    def put(self, req_id: str, answer: str) -> tuple[str, str]:
        """Store ``answer`` for ``req_id``; returns ``(answer_ref, sha256)``. A second answer for the
        same request is refused (content is write-once, like the ledger that points at it)."""
        if not req_id or not isinstance(answer, str):
            raise ContentStoreUnavailable("an answer needs a request id and text")
        digest = sha256_text(answer)
        ref = f"ans_{secrets.token_hex(12)}"
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._lock:
            try:
                self._db.execute("INSERT INTO answers(answer_ref, req_id, answer, sha256, created_at)"
                                 " VALUES (?,?,?,?,?)", (ref, req_id, answer, digest, now))
                self._db.commit()
            except sqlite3.Error:
                self._db.rollback()
                raise ContentStoreUnavailable("the answer could not be stored") from None
        return ref, digest

    def get(self, answer_ref: str) -> Optional[StoredAnswer]:
        with self._lock:
            try:
                row = self._db.execute("SELECT answer_ref, req_id, answer, sha256, created_at "
                                       "FROM answers WHERE answer_ref = ?",
                                       (answer_ref,)).fetchone()
            except sqlite3.Error:
                raise ContentStoreUnavailable("the answer content store could not be read") from None
        return StoredAnswer(*row) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()


__all__ = ["AnswerStore", "StoredAnswer", "ContentStoreUnavailable", "sha256_text"]
