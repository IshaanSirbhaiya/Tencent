"""The execution engine (09_FINAL_PRD §10.2) — deterministic, zero-token (P3).

Structural reshape via JSONata (trees) or DuckDB SQL (relational) over Arrow, then named
coercions applied per column. A row whose coercion fails is QUARANTINED with a reason
code (never dropped, never guessed). Same spec + same input → byte-identical output (the
basis of EV-DET).

JSONata library: ``jsonata-python`` (import ``jsonata``; `Jsonata(expr).evaluate(data)`).
This was UNPINNED in the docs — recorded as a resolved open item.
"""
from __future__ import annotations

from typing import Optional

import duckdb
import jsonata

from ..frame import Frame
from ..quarantine import (
    DEFAULT_THRESHOLD,
    REASON_COERCION,
    REASON_ENUM_UNSEEN,
    QuarantineStore,
    QuarantineThresholdExceeded,
)
from .coercions import CoercionError, apply_coercion
from .spec import TransformSpec


def _structural(spec: TransformSpec, frame: Frame) -> list[dict]:
    if spec.language == "jsonata":
        result = jsonata.Jsonata(spec.expression).evaluate(frame.rows)
        if result is None:
            return []
        return result if isinstance(result, list) else [result]
    # SQL: a 'data' table backed by the frame's Arrow representation
    con = duckdb.connect()
    try:
        con.register("data", frame.to_arrow())
        return con.execute(spec.expression).to_arrow_table().to_pylist()
    finally:
        con.close()


def execute_transform(
    spec: TransformSpec,
    frame: Frame,
    *,
    quarantine: Optional[QuarantineStore] = None,
    enforce_threshold: bool = True,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[Frame, QuarantineStore]:
    """Run a confirmed transform → (output Frame, quarantine store). Total accounting:
    ``len(input) == len(output) + len(quarantine)`` (EV-QC). If the quarantine rate
    exceeds ``threshold`` (default 1%), the RUN FAILS with ``QuarantineThresholdExceeded``
    (§10.5) — pass ``enforce_threshold=False`` to inspect a run without the gate."""
    q = quarantine or QuarantineStore()
    reshaped = _structural(spec, frame)

    out_rows: list[dict] = []
    for row in reshaped:
        if not isinstance(row, dict):
            q.add({"value": row}, REASON_COERCION, "non-record output row")
            continue
        try:
            new = dict(row)
            for cu in spec.coercions:
                for col in cu.columns:
                    if col in new and new[col] is not None:
                        new[col] = apply_coercion(cu.name, new[col], **cu.params)
            out_rows.append(new)
        except CoercionError as e:
            code = REASON_ENUM_UNSEEN if "enum_map" in str(e) else REASON_COERCION
            q.add(row, code, str(e))

    total = len(reshaped)
    if enforce_threshold and q.exceeds(total, threshold):
        raise QuarantineThresholdExceeded(
            f"quarantine rate {q.rate(total):.1%} exceeds threshold {threshold:.1%} "
            f"(run fails, §10.5): {q.report()}"
        )
    return Frame(rows=out_rows, name=frame.name, source_id=frame.source_id), q
