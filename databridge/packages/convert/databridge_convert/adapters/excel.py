"""Excel adapters → WorkbookRecord (09_FINAL_PRD §14). Thin registry wrappers around the
excelsub reader (C10). Writing is C11 (excelsub.writer), so ``write`` is None here.

The reader is imported LAZILY inside each wrapper: this module is imported during
``adapters/__init__`` (registry population), and excelsub imports back into adapters for
its AdapterError base — a top-level import here would be circular. Registration only needs
the callable references, which is cycle-free.

Lossy by the §14 fidelity table: values+types lossless, but formulas→cached values,
styles/charts/pivots dropped, macros stripped, comments→sidecar."""
from __future__ import annotations

from ..router.registry import AdapterInfo, register_adapter

_LIMITS = [
    "formulas read as CACHED values; recalculation out of scope (§14.5)",
    "styles/charts/pivots dropped (lossy, warned); comments → sidecar",
    "macros never executed; .xlsm payload stripped (§14.17)",
    "password-protected → REFUSED ENCRYPTED_WORKBOOK (§14.18)",
    "openpyxl enrichment (named ranges/tables/formulas/errors) is .xlsx-only",
]


def read_xlsx(data: bytes):
    from ..excelsub import ReadOptions, read_workbook
    return read_workbook(data, options=ReadOptions(), source_id="xlsx")


def read_xls(data: bytes):
    # calamine reads .xls values; openpyxl enrichment N/A (xlsx-only) — recorded limit.
    from ..excelsub import ReadOptions, read_workbook
    return read_workbook(data, options=ReadOptions(), source_id="xls")


def read_xlsb(data: bytes):
    from ..excelsub import ReadOptions, read_workbook
    return read_workbook(data, options=ReadOptions(), source_id="xlsb")


def write_xlsx(ir) -> bytes:
    # C11 writer: WorkbookRecord → .xlsx (values+layout; §21.7 injection-guarded).
    from ..excelsub import write_workbook_record
    return write_workbook_record(ir)


# .xlsx is the only writable Excel format (openpyxl writes .xlsx; §14 library decision).
register_adapter(AdapterInfo(
    format="xlsx", modality="structured", ir="WorkbookRecord", lossiness="lossy",
    limits=_LIMITS, read=read_xlsx, write=write_xlsx))
for _fmt, _reader in (("xlsm", read_xlsx), ("xls", read_xls), ("xlsb", read_xlsb)):
    register_adapter(AdapterInfo(
        format=_fmt, modality="structured", ir="WorkbookRecord", lossiness="lossy",
        limits=_LIMITS, read=_reader, write=None))
