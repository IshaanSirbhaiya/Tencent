"""EV-SF / EV-RT round-trips (3 cases). Real format adapters land in PROMPT 6 (C03/C04);
here we round-trip through Frame↔Arrow / Parquet / JSON to prove value-identity (§10.5)."""
from __future__ import annotations

import orjson
import pyarrow.parquet as pq

from databridge_convert import Frame
from databridge_convert.validators import RoundTrip

ROWS = [
    {"id": 1, "name": "alpha", "amount": 1.5, "active": True},
    {"id": 2, "name": "beta", "amount": 2.5, "active": False},
]


def test_arrow_roundtrip():
    back = Frame.from_arrow(Frame(rows=ROWS).to_arrow())
    assert RoundTrip().validate(original=ROWS, roundtrip=back.rows).passed


def test_parquet_roundtrip(tmp_path):
    path = tmp_path / "data.parquet"
    pq.write_table(Frame(rows=ROWS).to_arrow(), path)
    back = Frame.from_arrow(pq.read_table(path))
    assert RoundTrip().validate(original=ROWS, roundtrip=back.rows).passed


def test_json_roundtrip():
    back = orjson.loads(orjson.dumps(ROWS))
    assert RoundTrip().validate(original=ROWS, roundtrip=back).passed
