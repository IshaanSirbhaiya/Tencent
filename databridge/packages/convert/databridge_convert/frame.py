"""Frame — a minimal internal tabular representation (PROMPT-5 shortcut).

The S→S engine operates over records. The CANONICAL ``RowBatch`` IR (Arrow-backed, with
provenance) is built in PROMPT 6 (C01) and will REPLACE this — the build prompt explicitly
sequences the rule engine before the IR. Until then a Frame is just ``rows: list[dict]``
plus enough to (de)serialise to Arrow for the DuckDB path. Kept deliberately small so the
swap to RowBatch is mechanical.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa


@dataclass(frozen=True)
class Frame:
    rows: list[dict]
    name: str = "data"
    source_id: str = "inline"           # placeholder provenance; RowBatch formalises this

    def columns(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            for k in r:
                if k not in seen:
                    seen.append(k)
        return seen

    def to_arrow(self) -> pa.Table:
        cols = self.columns()
        return pa.table({c: [r.get(c) for r in self.rows] for c in cols}) if cols \
            else pa.table({})

    @classmethod
    def from_arrow(cls, table: pa.Table, name: str = "data") -> "Frame":
        return cls(rows=table.to_pylist(), name=name)

    @classmethod
    def from_records(cls, rows: list[dict], name: str = "data") -> "Frame":
        return cls(rows=list(rows), name=name)
