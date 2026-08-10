"""The corrections flywheel (06_PRD §6) — every human edit is training data.

Built NOW even though nothing reads it yet: Stage B's few-shot retrieval queries this
store starting empty, and every correction makes the next annotation better (the
schema-level network effect — fintech customer A's corrections help customer B).

Privacy boundary (hard line, 06_PRD §6): the store keeps metadata + REDACTED samples
only, partitioned per-deployment. The flywheel must never become the leak.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict

from databridge_core import ColumnAnnotation

# PII patterns redacted before anything is stored (mirrors the connector's redaction).
_PII_PATTERNS = [
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("phone", re.compile(r"^\+?\d[\d\s().-]{6,}$")),
    ("national_id", re.compile(r"^(\d{3}-\d{2}-\d{4}|[A-Z]\d{7}[A-Z])$")),  # SSN / NRIC-ish
]


def redact_value(value: object) -> object:
    """Redact a single sample value if it looks like PII; otherwise return it unchanged."""
    if not isinstance(value, str):
        return value
    for kind, rx in _PII_PATTERNS:
        if rx.match(value.strip()):
            return f"<REDACTED:{kind}>"
    return value


def redact_samples(samples: list) -> list:
    return [redact_value(v) for v in samples]


def context_key(table_summary: str, column_name: str, dtype: str) -> str:
    """A stable retrieval key (stands in for an embedding key until Phase 2)."""
    raw = f"{table_summary}::{column_name}::{dtype}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class Correction(BaseModel):
    """One human edit: (input_context, wrong_pred, correction) — 06_PRD §6."""

    model_config = ConfigDict(frozen=True)

    context_embedding_key: str
    input_context: dict                       # column meta + hints + REDACTED sample digest
    predicted: ColumnAnnotation
    corrected: ColumnAnnotation
    schema_domain: str = "unknown"
    created_at: Optional[datetime] = None


class CorrectionStore:
    """Append-only correction store with optional JSONL persistence.

    ``retrieve_similar`` is a naive placeholder (most-recent in the same domain) for the
    embedding-nearest-neighbour retrieval that Phase 2 adds; the interface is stable so
    Stage B can call it today against an empty store.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._items: list[Correction] = []
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._items.append(Correction.model_validate_json(line))

    def add(self, correction: Correction) -> None:
        # defence in depth: redact any sample digest in input_context before storing
        ctx = dict(correction.input_context)
        if "sample_values" in ctx and isinstance(ctx["sample_values"], list):
            ctx["sample_values"] = redact_samples(ctx["sample_values"])
        correction = correction.model_copy(update={"input_context": ctx})
        self._items.append(correction)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(correction.model_dump_json() + "\n")

    def all(self) -> list[Correction]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def retrieve_similar(self, key: str, schema_domain: str, k: int = 3) -> list[Correction]:
        """Few-shot candidates for Stage B. Naive for now; embedding-NN in Phase 2."""
        same_domain = [c for c in self._items if c.schema_domain == schema_domain]
        pool = same_domain or self._items
        return list(reversed(pool))[:k]
