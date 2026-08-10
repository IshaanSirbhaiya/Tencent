"""EV-PERF (Excel slice) — large-workbook streaming (09_FINAL_PRD §14.16 / §22).

Target: a 1M-row × 30-col sheet reads via calamine streaming + chunked RowBatch conversion
while peak process memory stays under the §22 bound (<4GB). This is a SEPARATE perf test
(not part of EV-XL proper, per the session plan).

CI runs a smaller smoke size (fast, proves the streaming plumbing). Set
``DATABRIDGE_PERF_FULL=1`` to run the full 1M-row target. The measured peak is reported
either way (misses are reported, not hidden — §22)."""
from __future__ import annotations

import io
import os
import tempfile

import openpyxl
import pytest

from databridge_convert.excelsub import stream_sheet_rowbatches

MEM_BUDGET_BYTES = 4 * 1024 ** 3            # §22: <4GB
FULL = os.environ.get("DATABRIDGE_PERF_FULL") == "1"
ROWS = 1_000_000 if FULL else 50_000
COLS = 30
CHUNK = 100_000


def peak_working_set() -> int:
    """Peak process memory (bytes). Windows: GetProcessMemoryInfo.PeakWorkingSetSize;
    POSIX: resource.getrusage (ru_maxrss). Returns 0 if unavailable."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC),
                                               wintypes.DWORD]
        h = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return int(pmc.PeakWorkingSetSize)
        return 0
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return ru * 1024 if os.uname().sysname != "Darwin" else ru   # KB on Linux, B on mac
    except Exception:                          # pragma: no cover
        return 0


def _make_big_xlsx(path: str, rows: int, cols: int) -> None:
    """Write a synthetic sheet using openpyxl write_only mode (bounded writer memory)."""
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Big")
    ws.append([f"c{j}" for j in range(cols)])
    for i in range(rows):
        ws.append([i] + [i * j for j in range(1, cols)])
    wb.save(path)


@pytest.fixture(scope="module")
def big_workbook_bytes():
    path = os.path.join(tempfile.gettempdir(), f"databridge_perf_{ROWS}x{COLS}.xlsx")
    if not os.path.exists(path):
        _make_big_xlsx(path, ROWS, COLS)
    with open(path, "rb") as f:
        return f.read()


def test_large_workbook_streams_under_memory_budget(big_workbook_bytes):
    total = 0
    chunks = 0
    for rb in stream_sheet_rowbatches(big_workbook_bytes, "Big",
                                      header_row=0, chunk_rows=CHUNK):
        total += rb.num_rows
        chunks += 1
        assert rb.num_rows <= CHUNK              # chunking actually bounds the batch
    peak = peak_working_set()
    print(f"\nEV-PERF(excel): rows={total} chunks={chunks} "
          f"peak={peak / 1024**3:.2f}GB budget={MEM_BUDGET_BYTES / 1024**3:.0f}GB "
          f"full={FULL}")
    assert total == ROWS
    assert chunks >= 1
    if peak:                                     # report+gate only when measurable
        assert peak < MEM_BUDGET_BYTES, f"peak {peak} exceeds §22 budget {MEM_BUDGET_BYTES}"
