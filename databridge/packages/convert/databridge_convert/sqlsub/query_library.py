"""Confirmed-query library (09_FINAL_PRD §15.4/§15.12) — the NL→SQL flywheel.

Same pattern as T07's corrections store: every CONFIRMED (question, schema-slice, SQL)
triple is stored and retrieved as dynamic few-shots for future generation, and becomes a
reusable artifact (`infer` re-runs the SAME SQL without the LLM).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ConfirmedQuery(BaseModel):
    model_config = ConfigDict(frozen=True)
    question: str
    slice_hash: str
    sql: str
    created_at: Optional[datetime] = None


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


class ConfirmedQueryLibrary:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._items: list[ConfirmedQuery] = []
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._items.append(ConfirmedQuery.model_validate_json(line))

    def add(self, question: str, slice_hash: str, sql: str) -> None:
        q = ConfirmedQuery(question=question, slice_hash=slice_hash, sql=sql,
                           created_at=datetime.now(timezone.utc))
        self._items.append(q)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(q.model_dump_json() + "\n")

    def __len__(self) -> int:
        return len(self._items)

    def retrieve(self, question: str, slice_hash: str, k: int = 3) -> list[ConfirmedQuery]:
        same = [q for q in self._items if q.slice_hash == slice_hash]
        pool = same or self._items
        qt = _tok(question)
        pool = sorted(pool, key=lambda q: len(_tok(q.question) & qt), reverse=True)
        return pool[:k]
