"""Rule engine: JSONata + DuckDB SQL paths, coercion → quarantine, EV-QC accounting."""
from __future__ import annotations

from decimal import Decimal

import pytest

from databridge_convert import Frame, TransformSpec
from databridge_convert.rules.engine import execute_transform
from databridge_convert.rules.spec import CoercionUse


def test_jsonata_reshape():
    frame = Frame(rows=[{"a": {"b": 1}}, {"a": {"b": 2}}])
    spec = TransformSpec(language="jsonata", expression='$.{ "x": a.b }')
    out, q = execute_transform(spec, frame)
    assert out.rows == [{"x": 1}, {"x": 2}]
    assert len(q) == 0


def test_duckdb_sql_path():
    frame = Frame(rows=[{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    spec = TransformSpec(language="sql", expression="SELECT a AS x, b AS y FROM data ORDER BY a")
    out, q = execute_transform(spec, frame)
    assert out.rows == [{"x": 1, "y": 2}, {"x": 3, "y": 4}]


def test_coercion_applied():
    frame = Frame(rows=[{"amt": "1,234.56"}])
    spec = TransformSpec(language="jsonata", expression="$",
                         coercions=[CoercionUse(name="str_to_decimal",
                                                params={"locale": "us"}, columns=["amt"])])
    out, _ = execute_transform(spec, frame)
    assert out.rows[0]["amt"] == Decimal("1234.56")


def test_enum_unseen_quarantined_not_dropped():
    frame = Frame(rows=[{"code": "A"}, {"code": "Z"}])     # Z is unseen
    spec = TransformSpec(language="jsonata", expression="$",
                         coercions=[CoercionUse(name="enum_map",
                                                params={"table": {"A": "Alpha"}}, columns=["code"])])
    # 50% quarantine here — disable the gate to inspect accounting (the gate is tested below)
    out, q = execute_transform(spec, frame, enforce_threshold=False)
    assert out.rows == [{"code": "Alpha"}]
    assert len(q) == 1 and q.records[0].reason_code == "enum_unseen"
    # EV-QC: nothing vanishes — output + quarantine == source
    assert len(out.rows) + len(q) == len(frame.rows)


def test_quarantine_threshold_fails_the_run():
    # §10.5: a run whose quarantine rate exceeds the threshold FAILS (does not return).
    from databridge_convert.quarantine import QuarantineThresholdExceeded
    frame = Frame(rows=[{"code": "A"}, {"code": "Z"}])     # 50% will quarantine
    spec = TransformSpec(language="jsonata", expression="$",
                         coercions=[CoercionUse(name="enum_map",
                                                params={"table": {"A": "Alpha"}}, columns=["code"])])
    with pytest.raises(QuarantineThresholdExceeded):
        execute_transform(spec, frame)                    # default enforce_threshold=True


def test_clean_run_under_threshold_succeeds():
    frame = Frame(rows=[{"code": "A"}] * 200 + [{"code": "Z"}])   # <1% quarantine
    spec = TransformSpec(language="jsonata", expression="$",
                         coercions=[CoercionUse(name="enum_map",
                                                params={"table": {"A": "Alpha"}}, columns=["code"])])
    out, q = execute_transform(spec, frame)               # 1/201 ≈ 0.5% < 1% → ok
    assert len(out.rows) == 200 and len(q) == 1
