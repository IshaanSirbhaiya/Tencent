"""Arrow IPC / Feather adapter → RowBatch (09_FINAL_PRD §5.1). Arrow IS the IR (lossless)."""
from __future__ import annotations

import io

import pyarrow as pa

from ..ir import RowBatch
from ..router.registry import AdapterInfo, register_adapter
from ._util import AdapterError, snapshot_for


def _one_batch(table: pa.Table) -> pa.RecordBatch:
    batches = table.combine_chunks().to_batches()
    if not batches:
        return pa.RecordBatch.from_pydict({n: [] for n in table.schema.names},
                                          schema=table.schema)
    if len(batches) > 1:
        raise AdapterError(f"arrow: {table.num_rows} rows span {len(batches)} batches; "
                           "single-RecordBatch read not yet supported (streaming is C24)")
    return batches[0]


def read(data: bytes) -> RowBatch:
    table = pa.ipc.open_stream(pa.BufferReader(data)).read_all()
    return RowBatch(data=_one_batch(table), snapshot=snapshot_for("arrow", data))


def write(ir: RowBatch) -> bytes:
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, ir.data.schema) as writer:
        writer.write_batch(ir.data)
    return sink.getvalue()


register_adapter(AdapterInfo(
    format="arrow", modality="structured", ir="RowBatch", lossiness="lossless",
    limits=[], read=read, write=write))
