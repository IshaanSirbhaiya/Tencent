"""Workbook-cell provenance (09_FINAL_PRD §14.19 + §17.1).

Every value extracted from a workbook gets a ``SourcePointer(kind="workbook_cell", …)``:
cell-level for individual extracted fields, range-level for batch row loads. The pointer
carries the ``workbook_sha`` so a value is verifiable against the exact source bytes.
"""
from __future__ import annotations

from databridge_core import SourcePointer

# A1-notation helpers (0-based indices → Excel column letters and cell refs).


def col_letter(idx0: int) -> str:
    """0-based column index → Excel column letters (0→A, 25→Z, 26→AA)."""
    if idx0 < 0:
        raise ValueError(f"col_letter: negative index {idx0}")
    s = ""
    n = idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cell_ref(row0: int, col0: int) -> str:
    """0-based (row, col) → A1 cell ref, e.g. (0,0) → 'A1', (6,1) → 'B7'."""
    return f"{col_letter(col0)}{row0 + 1}"


def range_ref(r0: int, c0: int, r1: int, c1: int) -> str:
    """0-based inclusive bounds → A1 range, e.g. (1,0,89,3) → 'A2:D90'."""
    return f"{cell_ref(r0, c0)}:{cell_ref(r1, c1)}"


def workbook_cell_pointer(workbook_sha: str, sheet: str, *, row0: int, col0: int,
                          source_id: str = "workbook") -> SourcePointer:
    """Cell-level pointer for a single extracted field (§14.19)."""
    return SourcePointer(
        source_id=source_id, kind="workbook_cell",
        workbook_sha=workbook_sha, sheet=sheet, cell=cell_ref(row0, col0),
    )


def workbook_range_pointer(workbook_sha: str, sheet: str, *, r0: int, c0: int, r1: int,
                           c1: int, source_id: str = "workbook") -> SourcePointer:
    """Range-level pointer for a batch row load (§14.19)."""
    return SourcePointer(
        source_id=source_id, kind="workbook_cell",
        workbook_sha=workbook_sha, sheet=sheet, cell_range=range_ref(r0, c0, r1, c1),
    )
