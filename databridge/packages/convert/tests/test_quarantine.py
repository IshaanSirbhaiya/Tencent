"""Quarantine store: counting, thresholds, parquet, reason accounting (§10.5/§19)."""
from __future__ import annotations

import pyarrow.parquet as pq

from databridge_convert import QuarantineStore
from databridge_convert.quarantine import REASON_COERCION, REASON_DEDUP


def test_rate_and_threshold():
    q = QuarantineStore()
    q.add({"id": 1}, REASON_COERCION, "bad value")
    assert len(q) == 1
    assert q.rate(100) == 0.01
    assert not q.exceeds(100)          # exactly 1% is NOT over the 1% threshold
    assert q.exceeds(50)               # 2% > 1% → run fails


def test_report_by_reason():
    q = QuarantineStore()
    q.add({}, REASON_COERCION)
    q.add({}, REASON_COERCION)
    q.add({}, REASON_DEDUP)
    assert q.report() == {"quarantined": 3, "by_reason": {"coercion": 2, "dedup": 1}}


def test_parquet_carries_original_payload(tmp_path):
    q = QuarantineStore()
    q.add({"id": 1, "x": "v"}, REASON_COERCION, "detail")
    path = q.to_parquet(tmp_path / "quarantine.parquet")
    table = pq.read_table(path)
    assert table.num_rows == 1
    assert set(table.column_names) == {"payload_json", "reason_code", "detail"}
