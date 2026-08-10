"""openpyxl enrichment pass — .xlsx ONLY (09_FINAL_PRD §14).

calamine is fast and types cells, but does NOT expose: named ranges, Excel Tables
(ListObjects), comments, per-cell formula text, error codes, hidden row/col flags, or
workbook metadata. openpyxl does. We run it as a SECOND pass and overlay the detail onto
the calamine value grid. This pass is .xlsx-only (openpyxl cannot read .xls/.xlsb), and
is skipped for the streaming/perf path where only typed values are needed.

Security (§21.5): xlsx files ARE zips → the caller bounds decompression before we parse.
Macros are detected (xl/vbaProject.bin present) but NEVER executed and never copied out.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import CALENDAR_MAC_1904


@dataclass
class SheetEnrichment:
    name: str
    visibility: str = "visible"                 # visible | hidden | very_hidden
    merged_ranges: list[str] = field(default_factory=list)
    tables: dict[str, str] = field(default_factory=dict)        # table name → A1 ref
    named_ranges_local: dict[str, str] = field(default_factory=dict)
    formula_cells: dict[str, str] = field(default_factory=dict)  # cellref → formula text
    error_cells: dict[str, str] = field(default_factory=dict)    # cellref → error code
    comments: dict[str, str] = field(default_factory=dict)       # cellref → comment text
    hidden_rows: list[int] = field(default_factory=list)         # 0-based
    hidden_cols: list[int] = field(default_factory=list)         # 0-based


@dataclass
class WorkbookEnrichment:
    sheets: dict[str, SheetEnrichment] = field(default_factory=dict)
    named_ranges_global: dict[str, str] = field(default_factory=dict)
    author: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    app: Optional[str] = None
    has_macros: bool = False
    epoch: str = "1900"                          # "1900" | "1904"


def detect_macros(data: bytes) -> bool:
    """Macros present iff the package contains a VBA project (§14.17). Cheap zip scan."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return any(n.lower().endswith("vbaproject.bin") for n in z.namelist())
    except zipfile.BadZipFile:
        return False


def _visibility(state: str) -> str:
    return {"visible": "visible", "hidden": "hidden", "veryHidden": "very_hidden"}.get(
        state, "visible")


def enrich(data: bytes, *, scan_cells: bool = True) -> WorkbookEnrichment:
    """Run the openpyxl enrichment pass over .xlsx bytes.

    ``scan_cells=False`` skips the per-cell walk (formulas/errors/comments) — used on huge
    workbooks where only structural metadata is wanted, since the cell walk is O(cells)."""
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False, read_only=False,
                                keep_vba=False, rich_text=False)
    out = WorkbookEnrichment()
    out.epoch = "1904" if wb.epoch == CALENDAR_MAC_1904 else "1900"

    # --- workbook metadata (§14.14) ---
    props = wb.properties
    out.author = props.creator
    out.created = props.created.isoformat() if props.created else None
    out.modified = props.modified.isoformat() if props.modified else None
    out.app = getattr(props, "lastModifiedBy", None) or "openpyxl-read"

    # --- global named ranges (§14.7) ---
    out.named_ranges_global = _defined_names(wb.defined_names)

    for ws in wb.worksheets:
        se = SheetEnrichment(name=ws.title, visibility=_visibility(ws.sheet_state))
        se.merged_ranges = [str(r) for r in ws.merged_cells.ranges]
        # Excel Tables / ListObjects (§14.8) — authoritative headers when present.
        # openpyxl's TableList.items() yields (name, ref_string).
        try:
            se.tables = {name: ref for name, ref in ws.tables.items()}
        except Exception:                        # pragma: no cover - defensive
            se.tables = {}
        # sheet-local named ranges
        se.named_ranges_local = _defined_names(getattr(ws, "defined_names", {}))
        # hidden rows / cols (§14.10)
        for r_idx, dim in ws.row_dimensions.items():
            if dim.hidden:
                se.hidden_rows.append(r_idx - 1)             # 1-based → 0-based
        for col_key, dim in ws.column_dimensions.items():
            if dim.hidden:
                se.hidden_cols.extend(_col_span0(dim, col_key))

        if scan_cells:
            for row in ws.iter_rows():
                for cell in row:
                    dt = cell.data_type
                    if dt == "f" and isinstance(cell.value, str):
                        se.formula_cells[cell.coordinate] = cell.value
                    elif dt == "e":
                        se.error_cells[cell.coordinate] = str(cell.value)
                    if cell.comment is not None:
                        se.comments[cell.coordinate] = cell.comment.text
        out.sheets[ws.title] = se

    wb.close()
    out.has_macros = detect_macros(data)
    return out


def _defined_names(dn: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        items = dn.items()
    except AttributeError:
        return out
    for name, defn in items:
        try:
            out[name] = defn.value                  # e.g. "Sheet1!$A$1:$D$10"
        except Exception:                            # pragma: no cover - defensive
            out[name] = str(defn)
    return out


def _col_span0(dim: Any, col_key: str) -> list[int]:
    """A column-dimension may span several columns (min..max). Return 0-based indices."""
    lo = getattr(dim, "min", None)
    hi = getattr(dim, "max", None)
    if lo and hi:
        return [c - 1 for c in range(lo, hi + 1)]
    # fall back to the single keyed column letter
    from openpyxl.utils import column_index_from_string
    try:
        return [column_index_from_string(col_key) - 1]
    except Exception:                                # pragma: no cover
        return []
