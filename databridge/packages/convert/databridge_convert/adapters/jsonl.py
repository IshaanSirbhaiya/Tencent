"""JSONL / NDJSON adapter → TypedRecord (09_FINAL_PRD §5.1).

One JSON value per line; ``root`` is the list of per-line objects. A malformed line is
WARNED and SKIPPED — it never aborts the batch (full row-quarantine wiring lands in C24).
"""
from __future__ import annotations

import warnings
from decimal import Decimal

import orjson

from ..ir import TypedRecord
from ..router.registry import AdapterInfo, register_adapter
from ._util import snapshot_for


def _default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError


def read(data: bytes) -> TypedRecord:
    rows: list = []
    for i, line in enumerate(data.split(b"\n")):
        if not line.strip():
            continue
        try:
            rows.append(orjson.loads(line))
        except orjson.JSONDecodeError:
            warnings.warn(f"jsonl: skipped malformed line {i}", stacklevel=2)
    return TypedRecord(root=rows, snapshot=snapshot_for("jsonl", data))


def write(ir: TypedRecord) -> bytes:
    root = ir.root if isinstance(ir.root, list) else [ir.root]
    return b"\n".join(orjson.dumps(el, default=_default) for el in root)


register_adapter(AdapterInfo(
    format="jsonl", modality="structured", ir="TypedRecord", lossiness="lossless",
    limits=["malformed line skipped+warned (row-quarantine in C24)"],
    read=read, write=write))
