"""Row-level quarantine store (09_FINAL_PRD §10.5/§19).

No silent dropping anywhere: every excluded row is counted with a reason code, and the
store carries the ORIGINAL payload + reasons. A run whose quarantine rate exceeds its
threshold (default 1%) fails. Persisted as ``quarantine.parquet``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DEFAULT_THRESHOLD = 0.01  # 1% of rows (§10.5)


class QuarantineThresholdExceeded(Exception):
    """A run quarantined more than its allowed fraction → the RUN fails (§10.5/§19)."""

# Canonical reason codes (extended as later tasks add checks).
REASON_COERCION = "coercion"
REASON_ENUM_UNSEEN = "enum_unseen"
REASON_INTEGRITY = "integrity"
REASON_DEDUP = "dedup"
REASON_RANGE = "range"
REASON_PARSE = "parse"


@dataclass(frozen=True)
class QuarantineRecord:
    payload: dict          # the ORIGINAL row (never dropped)
    reason_code: str
    detail: str = ""


class QuarantineStore:
    def __init__(self) -> None:
        self._records: list[QuarantineRecord] = []

    def add(self, payload: dict, reason_code: str, detail: str = "") -> None:
        self._records.append(QuarantineRecord(payload=dict(payload),
                                              reason_code=reason_code, detail=detail))

    @property
    def records(self) -> list[QuarantineRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def rate(self, total_rows: int) -> float:
        return (len(self._records) / total_rows) if total_rows else 0.0

    def exceeds(self, total_rows: int, threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.rate(total_rows) > threshold

    def report(self) -> dict:
        by_reason: dict[str, int] = {}
        for r in self._records:
            by_reason[r.reason_code] = by_reason.get(r.reason_code, 0) + 1
        return {"quarantined": len(self._records), "by_reason": by_reason}

    def to_parquet(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table({
            "payload_json": [json.dumps(r.payload, default=str) for r in self._records],
            "reason_code": [r.reason_code for r in self._records],
            "detail": [r.detail for r in self._records],
        })
        pq.write_table(table, path)
        return path
