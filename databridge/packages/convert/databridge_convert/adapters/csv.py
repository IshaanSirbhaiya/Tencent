"""CSV / TSV adapters → RowBatch (09_FINAL_PRD §5.1).

CSV has values but no types → all-string columns (coercions retype downstream). Read uses
the encoding-fallback chain utf-8 → utf-8-sig → latin-1 (warned). Write applies the
CSV-injection guard (§21.8): a cell starting with = + - @ or tab is apostrophe-prefixed.
"""
from __future__ import annotations

import csv as _csv
import io
import warnings

from ..ir import RowBatch
from ..router.registry import AdapterInfo, register_adapter
from ._util import snapshot_for

_ENCODINGS = ("utf-8", "utf-8-sig", "latin-1")
_DANGEROUS = ("=", "+", "-", "@", "\t")


def _decode(data: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
            if enc != "utf-8":
                warnings.warn(f"csv: decoded with fallback encoding {enc}", stacklevel=2)
            return text
        except UnicodeDecodeError:
            continue
    warnings.warn("csv: undecodable bytes — latin-1 with replacement", stacklevel=2)
    return data.decode("latin-1", errors="replace")


def _read(data: bytes, *, delimiter: str = ",", source_id: str = "csv") -> RowBatch:
    text = _decode(data)
    reader = _csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = [dict(r) for r in reader]
    return RowBatch.from_records(rows, snapshot_for(source_id, data))


def _guard(s: str) -> str:
    # CSV-injection guard (§21.8): apostrophe-prefix if the first NON-SPACE char is
    # dangerous (spreadsheets trim leading spaces, so " =cmd" is still a formula).
    stripped = s.lstrip()
    return "'" + s if stripped and stripped[0] in _DANGEROUS else s


def _cell(value: object) -> str:
    if value is None:
        return ""
    s = _guard(str(value))
    if any(c in s for c in (",", '"', "\n", "\r", "\t")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _write(ir: RowBatch, *, delimiter: str = ",") -> bytes:
    cols = ir.columns
    rows = ir.records()
    lines = [delimiter.join(_cell(c) for c in cols)]
    lines += [delimiter.join(_cell(r.get(c)) for c in cols) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


# CSV
def read(data: bytes) -> RowBatch:
    return _read(data, delimiter=",", source_id="csv")


def write(ir: RowBatch) -> bytes:
    return _write(ir, delimiter=",")


# TSV (a CSV dialect, §5.1)
def read_tsv(data: bytes) -> RowBatch:
    return _read(data, delimiter="\t", source_id="tsv")


def write_tsv(ir: RowBatch) -> bytes:
    return _write(ir, delimiter="\t")


register_adapter(AdapterInfo(
    format="csv", modality="structured", ir="RowBatch", lossiness="lossless",
    limits=["no types in format (all strings)", "dialect sniffing limited",
            "encoding fallback utf-8→utf-8-sig→latin-1 (warned)"],
    read=read, write=write))

register_adapter(AdapterInfo(
    format="tsv", modality="structured", ir="RowBatch", lossiness="lossless",
    limits=["treated as a CSV dialect (tab-delimited)"],
    read=read_tsv, write=write_tsv))
