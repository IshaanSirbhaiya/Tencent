"""excelsub/ — the Excel subsystem (09_FINAL_PRD §14). A workbook is a WorkbookRecord,
never "a document".

Library decision (§14): **python-calamine** is the primary reader for ALL formats
(.xlsx/.xls/.xlsb) — Rust-fast, typed cells, epoch-aware dates, cached formula values.
**openpyxl** enriches .xlsx ONLY (named ranges, ListObjects, merged ranges, hidden flags,
comments, formula text, error codes, metadata, macros) — things calamine does not expose.

This package is a sibling of sqlsub/ (§25) because it owns the EV-XL eval suite.
"""
from __future__ import annotations

from .policies import (
    ENCRYPTED_WORKBOOK,
    ExcelError,
    ExcelRefusal,
    ReadOptions,
)
from .provenance import workbook_cell_pointer, workbook_range_pointer
from .reader import read_workbook, sheet_to_rowbatch, stream_sheet_rowbatches
from .writer import (
    SEMANTICS_UNCONFIRMED,
    WorkbookRecordBuilder,
    WriteOptions,
    guard_formula,
    render_report,
    write_rowbatch,
    write_workbook_record,
)

__all__ = [
    "read_workbook", "stream_sheet_rowbatches", "sheet_to_rowbatch", "ReadOptions", "ExcelError",
    "ExcelRefusal", "ENCRYPTED_WORKBOOK",
    "workbook_cell_pointer", "workbook_range_pointer",
    "WorkbookRecordBuilder", "WriteOptions", "write_rowbatch", "write_workbook_record",
    "render_report", "guard_formula", "SEMANTICS_UNCONFIRMED",
]
