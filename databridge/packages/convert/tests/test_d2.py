"""Demo D2 (09_FINAL_PRD §27): multi-sheet irregular Excel → clean Parquet + JSON.

Acceptance: headers confirmed; merged-title + hidden columns handled per policy; extracted
values 100% through the Parquet/JSON round-trip."""
from __future__ import annotations

from databridge_convert.demos.d2 import EXPECTED, run_d2
from fixtures.excel.build import build_gauntlet_lite


def _run():
    return run_d2(build_gauntlet_lite())


def test_d2_headers_confirmed():
    r = _run()
    # Orders' merged title row is skipped → header at row 1; both sheets auto-confirm (≥0.9)
    assert r["decisions"]["Orders"]["header_row"] == 1
    assert r["decisions"]["Orders"]["confirmed_by"].startswith("auto")
    assert r["decisions"]["Stock"]["header_row"] == 0


def test_d2_merged_title_and_hidden_handled_per_policy():
    r = _run()
    assert r["merged_title_skipped"] is True
    assert r["hidden_excluded"] is True                # internal_cost (hidden) absent
    assert "internal_cost" not in r["sheets"]["Stock"]["columns"]


def test_d2_values_100pct_through_parquet_and_json():
    r = _run()
    for sheet, expected in EXPECTED.items():
        s = r["sheets"][sheet]
        assert s["records"] == expected
        assert s["parquet_roundtrip"] == expected      # Parquet preserves values
        assert s["json_roundtrip"] == expected         # JSON preserves values
        assert s["quarantined"] == 0


def test_d2_leading_zero_ids_preserved():
    r = _run()
    ids = [rec["order_id"] for rec in r["sheets"]["Orders"]["records"]]
    assert ids == ["00100", "00101", "00102"]          # ids stay strings, zeros intact
