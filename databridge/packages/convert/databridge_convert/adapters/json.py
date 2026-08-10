"""JSON adapter → TypedRecord (09_FINAL_PRD §5.1).

JSON preserves types within the JSON type system → a TypedRecord tree (top-level array or
object both fine). orjson does the parse/serialize; it can't serialize Decimal, so the
write ``default`` coerces Decimal→str (keeps >2^53 values, carried as Decimal upstream,
writable — at the cost of representing them as strings; see limits).
"""
from __future__ import annotations

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
    return TypedRecord(root=orjson.loads(data), snapshot=snapshot_for("json", data))


def write(ir: TypedRecord) -> bytes:
    return orjson.dumps(ir.root, default=_default)


register_adapter(AdapterInfo(
    format="json", modality="structured", ir="TypedRecord", lossiness="lossless",
    limits=["numbers >2^53 lose precision unless Decimal"],
    read=read, write=write))
