"""Demo D2 (09_FINAL_PRD §27): multi-sheet irregular Excel → clean Parquet + JSON.

Chain: Gauntlet-lite workbook → reader (C10) → header-confirm (the T07-style gate: auto-
accept ≥0.9 confidence, else route to a human override) → rules engine (identity/cleanup)
→ Parquet + JSON targets (C03). Acceptance: header decisions confirmed, merged-title and
hidden columns handled PER POLICY, extracted values 100% faithful through the round-trip.

"Irregular" here = the Orders sheet has a merged TITLE row above the real header (header-
context merge, skipped) and the Stock sheet has a HIDDEN column (excluded, audit-honest)."""
from __future__ import annotations

from typing import Optional

from ..adapters import json as json_adapter
from ..adapters import parquet as parquet_adapter
from ..excelsub import read_workbook, sheet_to_rowbatch
from ..excelsub.header_detection import needs_confirmation
from ..excelsub.policies import ReadOptions
from ..frame import Frame
from ..ir import RowBatch, TypedRecord
from ..rules.engine import execute_transform
from ..rules.spec import TransformSpec

# expected clean records (the oracle; calamine returns ints as floats)
EXPECTED = {
    "Orders": [
        {"order_id": "00100", "customer": "Ana", "amount": 1299.50, "status": "shipped"},
        {"order_id": "00101", "customer": "Ben", "amount": 4.50, "status": "shipped"},
        {"order_id": "00102", "customer": "Cara", "amount": 1999.00, "status": "pending"},
    ],
    "Stock": [                                          # internal_cost (hidden) EXCLUDED
        {"sku": "A-1", "name": "Widget", "qty": 30.0},
        {"sku": "A-2", "name": "Gadget", "qty": 12.0},
    ],
}

_IDENTITY = TransformSpec(language="jsonata", expression="$", coercions=[])


def confirm_headers(wb, overrides: Optional[dict] = None) -> dict:
    """The header-confirm gate (reuses the T07 confirmation PATTERN — non-interactive in a
    scripted demo): a sheet whose header confidence ≥0.9 is AUTO-confirmed at the detected
    row; a low-confidence sheet must be confirmed by a human override (sheet→row_idx).
    Returns {sheet: {"header_row", "confirmed_by", "confidence"}}."""
    overrides = overrides or {}
    decisions = {}
    for s in wb.sheets:
        g = s.header_inference
        if needs_confirmation(g):
            if s.name not in overrides:
                raise ValueError(f"sheet {s.name!r} header conf {g.confidence} needs human "
                                 f"confirm (P6) — provide an override")
            decisions[s.name] = {"header_row": overrides[s.name],
                                 "confirmed_by": "human:demo", "confidence": g.confidence}
        else:
            decisions[s.name] = {"header_row": g.row_idx,
                                 "confirmed_by": "auto(>=0.9)", "confidence": g.confidence}
    return decisions


def run_d2(workbook_bytes: bytes) -> dict:
    """Run the D2 chain over a Gauntlet-lite workbook. The fixture is a versioned test asset
    (``tests/fixtures/excel/build_gauntlet_lite``) supplied by the caller, keeping this demo
    free of test-only imports."""
    wb = read_workbook(workbook_bytes, options=ReadOptions(sheets="all"))
    decisions = confirm_headers(wb)

    out_sheets = {}
    for s in wb.sheets:
        hr = decisions[s.name]["header_row"]
        rb = sheet_to_rowbatch(s, wb.sha256, header_row=hr, include_hidden=False,
                               source_id="d2")
        # route through the rules engine (identity/cleanup) — the S→S path (C06)
        frame = Frame(rows=rb.records(), name=s.name)
        transformed, q = execute_transform(_IDENTITY, frame, enforce_threshold=False)
        clean = RowBatch.from_records(transformed.rows, rb.snapshot,
                                      column_provenance=rb.column_provenance)
        parquet_bytes = parquet_adapter.write(clean)
        json_bytes = json_adapter.write(TypedRecord(root=clean.records(),
                                                    snapshot=clean.snapshot))
        # verify round-trip values
        from_parquet = parquet_adapter.read(parquet_bytes).records()
        from_json = json_adapter.read(json_bytes).root
        out_sheets[s.name] = {
            "columns": clean.columns,
            "parquet_bytes": parquet_bytes,
            "json_bytes": json_bytes,
            "records": clean.records(),
            "parquet_roundtrip": from_parquet,
            "json_roundtrip": from_json,
            "quarantined": q.report()["quarantined"],
        }

    return {
        "decisions": decisions,
        "sheets": out_sheets,
        "hidden_excluded": "internal_cost" not in out_sheets["Stock"]["columns"],
        "merged_title_skipped": out_sheets["Orders"]["columns"] == [
            "order_id", "customer", "amount", "status"],
    }
