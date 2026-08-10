"""HTML tables adapter → RowBatch (09_FINAL_PRD §5.1). lxml; first <table> → one RowBatch."""
from __future__ import annotations

import warnings

from lxml import html as lxml_html

from ..ir import RowBatch
from ..router.registry import AdapterInfo, register_adapter
from ._util import AdapterError, snapshot_for


def read(data: bytes) -> RowBatch:
    doc = lxml_html.fromstring(data)
    tables = doc.xpath("//table")
    if not tables:
        raise AdapterError("html_tables: no <table> element found")
    if len(tables) > 1:
        warnings.warn(f"html_tables: {len(tables)} tables; returning the first", stacklevel=2)
    rows: list[list[str]] = []
    for tr in tables[0].xpath(".//tr"):
        rows.append([(c.text_content() or "").strip() for c in tr.xpath("./th|./td")])
    rows = [r for r in rows if r]
    if not rows:
        raise AdapterError("html_tables: empty table")
    header = rows[0]
    records = []
    for r in rows[1:]:
        if len(r) != len(header):
            warnings.warn("html_tables: ragged/colspan row normalised", stacklevel=2)
        records.append({header[i]: (r[i] if i < len(r) else None) for i in range(len(header))})
    return RowBatch.from_records(records, snapshot_for("html_tables", data))


register_adapter(AdapterInfo(
    format="html_tables", modality="structured", ir="RowBatch", lossiness="lossy",
    limits=["one RowBatch per table (first table returned)", "colspan/rowspan normalised (warned)"],
    read=read, write=None))
