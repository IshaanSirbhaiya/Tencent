"""Markdown tables adapter → RowBatch (09_FINAL_PRD §5.1). Own GFM pipe-table parser."""
from __future__ import annotations

import re
import warnings

from ..ir import RowBatch
from ..router.registry import AdapterInfo, register_adapter
from ._util import AdapterError, snapshot_for

_ALIGN_RE = re.compile(r"^[\s|:\-]+$")          # the |---|:--:| alignment row


_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    # split on UNescaped pipes, then unescape '\|' → '|' so pipe-bearing cells round-trip
    return [c.strip().replace("\\|", "|") for c in _UNESCAPED_PIPE.split(line)]


def read(data: bytes) -> RowBatch:
    lines = [ln for ln in data.decode("utf-8", errors="replace").splitlines()
             if ln.strip() and "|" in ln]
    if len(lines) < 2:
        raise AdapterError("md_tables: no markdown table found")
    header = _split_row(lines[0])
    body = lines[1:]
    if body and _ALIGN_RE.match(body[0]):
        body = body[1:]                          # alignment row ignored
    records = []
    for ln in body:
        cells = _split_row(ln)
        if len(cells) != len(header):
            warnings.warn("md_tables: ragged row skipped", stacklevel=2)
            continue
        records.append(dict(zip(header, cells)))
    return RowBatch.from_records(records, snapshot_for("md_tables", data))


def write(ir: RowBatch) -> bytes:
    cols = ir.columns
    lines = ["| " + " | ".join(cols) + " |",
             "| " + " | ".join("---" for _ in cols) + " |"]
    for r in ir.records():
        # escape pipes inside cell values so they don't read back as column separators
        cells = [str(r.get(c, "")).replace("|", "\\|") for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return ("\n".join(lines) + "\n").encode("utf-8")


register_adapter(AdapterInfo(
    format="md_tables", modality="structured", ir="RowBatch", lossiness="lossless",
    limits=["alignment row ignored", "ragged rows skipped+warned"],
    read=read, write=write))
