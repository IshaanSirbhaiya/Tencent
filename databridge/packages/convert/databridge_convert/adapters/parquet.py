"""Parquet adapter → RowBatch (09_FINAL_PRD §5.1). Typed, column-oriented, lossless."""
from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.parquet as pq

from ..ir import RowBatch
from ..router.registry import AdapterInfo, register_adapter
from ._util import AdapterError, snapshot_for


def _one_batch(table: pa.Table, fmt: str) -> pa.RecordBatch:
    batches = table.combine_chunks().to_batches()
    if not batches:
        return pa.RecordBatch.from_pydict({n: [] for n in table.schema.names},
                                          schema=table.schema)
    if len(batches) > 1:
        # Fail LOUD rather than silently keep only the first batch (data loss).
        raise AdapterError(f"{fmt}: {table.num_rows} rows span {len(batches)} batches; "
                           "single-RecordBatch read not yet supported (streaming is C24)")
    return batches[0]


def read(data: bytes) -> RowBatch:
    table = pq.read_table(pa.BufferReader(data))
    return RowBatch(data=_one_batch(table, "parquet"), snapshot=snapshot_for("parquet", data))


def write(ir: RowBatch) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.Table.from_batches([ir.data]), sink)
    return sink.getvalue()


register_adapter(AdapterInfo(
    format="parquet", modality="structured", ir="RowBatch", lossiness="lossless",
    limits=[], read=read, write=write))
