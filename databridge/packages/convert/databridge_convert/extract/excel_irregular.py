"""Irregular-Excel → typed record (C16, 09_FINAL_PRD §11 + §14).

Most Excel sheets are tables → ``sheet_to_rowbatch`` (C10/D2). But a finance export often
ships a sheet whose "headers" are not a table header at all — they are a vertical KEY-VALUE
block (``Field name | value`` down two columns: invoice summaries, account statements, config
sheets). Treating that as a RowBatch yields one nonsense row ``{"Invoice Number": "INV-…"}``.

This module DETECTS that case — the §14.2 header detector reports low confidence AND the first
column is a column of distinct field-name labels over a narrow (≈2-column) shape — and routes
the sheet through the C15 extraction pipeline instead: the KV sheet is rendered to a
``DocumentRecord`` of ``Label: value`` blocks whose provenance is the source **workbook cell**
(§14.19), so C15's deterministic pre-extraction locks the fields and the grounding wall (§11.4)
confirms every emitted value against the cell it came from — emitting a ``workbook_cell``
pointer, not a synthesised doc_span.

Detection is conservative by design: anything that is not clearly a KV block stays on the
RowBatch path (the safe default). A low-confidence MULTI-ROW-HEADER table (e.g. the Gauntlet
``Quarterly`` sheet) is rejected here by the narrow-shape gate — its first column below the
header is DATA, and it is wide.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from databridge_harness import Harness

from ..adapters._docutil import markdown_serialize
from ..adapters._util import snapshot_for
from ..excelsub.header_detection import _is_blank, _is_string_label, needs_confirmation
from ..excelsub.policies import ExcelError
from ..excelsub.provenance import workbook_cell_pointer
from ..ir import Block, DocumentRecord, SheetRecord
from .extractor import ExtractionResult, extract
from .target_model import CompiledTarget, compile_target

# A KV sheet's value column is one column wide; populated columns beyond {label, value} disqualify.
_KV_MAX_POPULATED_COLS = 2
_COL0_DISTINCT_MIN = 0.70       # the label column is distinct field names, not repeated data
_COL0_LABEL_MIN = 0.80          # …and predominantly string labels (§14.2 _STRING_RATIO)
_FILL_MIN = 0.50                # a column counts as "populated" at ≥50% non-blank cells


def _norm_label(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")


@dataclass
class KVVerdict:
    """Why a sheet was (not) routed to the key-value extractor — auditable, not a bare bool."""
    is_key_value: bool
    header_confidence: float
    col0_label_ratio: float
    col0_distinct: float
    populated_cols: int
    schema_overlap: float           # fraction of col0 labels matching a schema field (if known)
    reason: str = ""


def _used_rows(sheet: SheetRecord) -> list[list[Any]]:
    """Rows of the sheet that carry any value (trailing/leading blank rows dropped)."""
    return [r for r in sheet.cells if any(not _is_blank(c) for c in r)]


def _populated_cols(rows: list[list[Any]]) -> int:
    """How many columns are populated at ≥_FILL_MIN — the KV shape has exactly two."""
    if not rows:
        return 0
    width = max(len(r) for r in rows)
    n = 0
    for c in range(width):
        filled = sum(1 for r in rows if c < len(r) and not _is_blank(r[c]))
        if filled / len(rows) >= _FILL_MIN:
            n += 1
    return n


_SCHEMA_OVERLAP_MIN = 0.50      # "first column looks like field names": ≥½ of col0 matches a field


def looks_like_key_value(sheet: SheetRecord, compiled: Optional[CompiledTarget] = None) -> KVVerdict:
    """Decide whether ``sheet`` is a vertical key-value block (route to C15) vs a table.

    The prompt's heuristic is "header detection confidence very low + first column looks like
    field names". The first half alone is NOT reliable: a KV block whose value column is strongly
    TYPED (a real date + floats, as in a finance export) reads as "data-like" to the §14.2
    detector, which then reports HIGH confidence for a (wrong) 2-column-table header — the very
    case that silently emits garbage rows. So the LOAD-BEARING, decisive signal is the second
    half, made precise and schema-aware; the confidence is reported for audit but is NOT a gate
    (it cannot be trusted in either direction here):

      structural : narrow shape (≈2 populated columns) + the first column is ≥80% DISTINCT
                   string labels — a label column over a single value column;
      field_names: ≥50% of those first-column labels match a TARGET field name/description
                   ("looks like field names", §11 — a real 2-col data table's col-0 entries are
                   data values, not schema field names, so they do not match; this is what keeps
                   a narrow lookup table OFF the KV path even when it is low-confidence).

    Route to KV only when (structural AND field_names). Detection is therefore schema-aware by
    construction — "the first column looks like the TARGET's field names" needs the target — so a
    schema-light caller (no ``compiled``) never routes (overlap is 0); ``extract_sheet`` /
    ``route_workbook`` always compile the target first. Anything not clearly KV stays on the safe
    RowBatch path."""
    rows = _used_rows(sheet)
    conf = sheet.header_inference.confidence
    col0 = [r[0] for r in rows if r]
    n = len(col0)
    pop = _populated_cols(rows)
    if n < 2:
        return KVVerdict(False, conf, 0.0, 0.0, pop, 0.0, "too few rows for a KV block")
    label_ratio = sum(1 for c in col0 if _is_string_label(c)) / n
    distinct = len({_norm_label(c) for c in col0 if not _is_blank(c)}) / n

    overlap = 0.0
    if compiled is not None:
        names = {f.name for f in compiled.scalar_fields}
        names |= {_norm_label(f.description) for f in compiled.scalar_fields if f.description}
        overlap = sum(1 for c in col0 if _norm_label(c) in names) / n

    low_conf = needs_confirmation(sheet.header_inference)
    structural = (label_ratio >= _COL0_LABEL_MIN and distinct >= _COL0_DISTINCT_MIN
                  and pop <= _KV_MAX_POPULATED_COLS)
    field_names = overlap >= _SCHEMA_OVERLAP_MIN
    is_kv = structural and field_names
    if is_kv:
        reason = (f"KV block: first column is {label_ratio:.0%} distinct labels, "
                  f"{overlap:.0%} match target fields over {pop} populated columns "
                  f"(header conf {conf:.2f})")
    else:
        reason = (f"not KV: structural={structural} field_names={field_names} "
                  f"low_conf={low_conf} overlap={overlap:.2f} populated_cols={pop}")
    return KVVerdict(is_kv, conf, round(label_ratio, 3), round(distinct, 3), pop,
                     round(overlap, 3), reason)


def _render_cell(v: Any) -> str:
    """Render a typed cached cell value as the workbook displays it, so grounding matches the
    SAME token the user sees. Dates → ISO; integral floats → no trailing '.0'; bools → words.
    An ``ExcelError`` cell (#N/A, #VALUE!, …) is NOT a value → "" so the row is skipped (the
    field is then absent: required ⇒ quarantine, optional ⇒ null — never a leaked error token)."""
    if v is None or isinstance(v, ExcelError):
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, _dt.datetime):
        return v.date().isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    if isinstance(v, str):
        return v.strip()
    return ""               # unknown cell object → no value (don't leak a repr into provenance)


def sheet_to_document(sheet: SheetRecord, workbook_sha: str, *,
                      source_id: str = "workbook") -> DocumentRecord:
    """Render a key-value sheet to a ``DocumentRecord`` of ``Label: value`` blocks, each carrying
    a ``workbook_cell`` pointer to its VALUE cell (col 1) so a grounded field resolves to the
    exact source cell (§14.19). Rows with a blank label or blank value are skipped (they carry
    no field)."""
    blocks: list[Block] = []
    provenance: dict[int, Any] = {}
    for r, row in enumerate(sheet.cells):
        if not row or _is_blank(row[0]):
            continue
        label = str(row[0]).strip()
        # the value is the FIRST populated cell to the right of the label column — not a hard
        # col+1 (which would read a spacer column and point provenance at the wrong cell). The
        # pointer references that ACTUAL value column (§14.19), so it resolves to the real source.
        value_col = next((j for j in range(1, len(row)) if not _is_blank(row[j])), None)
        if value_col is None:
            continue
        value = _render_cell(row[value_col])
        if not value:
            continue
        idx = len(blocks)
        blocks.append(Block(kind="Paragraph", text=f"{label}: {value}"))
        provenance[idx] = workbook_cell_pointer(
            workbook_sha, sheet.name, row0=sheet.row_origin + r,
            col0=sheet.col_origin + value_col, source_id=source_id)
    _text, char_index = markdown_serialize(blocks)
    snapshot = snapshot_for(source_id, workbook_sha.encode())
    meta = {"workbook_sha": workbook_sha, "sheet": sheet.name,
            "extraction_path": "excel_irregular_key_value",
            "loss_report": [f"sheet {sheet.name!r} read as a key-value block, not a table "
                            f"(header conf {sheet.header_inference.confidence:.2f}<0.9, §14.2)"]}
    return DocumentRecord(document_id=source_id, sha256=workbook_sha, snapshot=snapshot,
                          blocks=blocks, char_index=char_index, block_provenance=provenance,
                          source_meta=meta)


@dataclass
class SheetRouting:
    """The routing decision + result for one sheet of an irregular workbook."""
    sheet: str
    path: str                                   # "key_value" | "table"
    verdict: KVVerdict
    extraction: Optional[ExtractionResult] = None


def extract_sheet(sheet: SheetRecord, schema: Any, harness: Harness, *, workbook_sha: str,
                  name: Optional[str] = None, source_id: str = "workbook",
                  compiled: Optional[CompiledTarget] = None) -> SheetRouting:
    """Route ONE sheet. If it is a KV block → render to a DocumentRecord and run C15 extraction;
    otherwise return a ``table`` routing (the caller takes the RowBatch path, C10/D2)."""
    compiled = compiled or compile_target(schema, name=name)
    verdict = looks_like_key_value(sheet, compiled)
    if not verdict.is_key_value:
        return SheetRouting(sheet=sheet.name, path="table", verdict=verdict)
    doc = sheet_to_document(sheet, workbook_sha, source_id=source_id)
    res = extract(doc, schema, harness, name=name, source_id=source_id)
    return SheetRouting(sheet=sheet.name, path="key_value", verdict=verdict, extraction=res)


def route_workbook(wb, schema: Any, harness: Harness, *, name: Optional[str] = None,
                   source_id: str = "workbook", include_hidden: bool = False
                   ) -> dict[str, SheetRouting]:
    """Route the VISIBLE sheets of a ``WorkbookRecord``: KV blocks → C15 extraction (typed records
    with workbook_cell provenance), tables → the RowBatch path (flagged, handled by the caller).
    Hidden/very-hidden sheets are skipped by default (§14.10 audit honesty — hidden often means
    stale; don't spend a model call on a sheet the author deliberately hid); ``include_hidden``
    opts them in."""
    compiled = compile_target(schema, name=name)
    out: dict[str, SheetRouting] = {}
    for s in wb.sheets:
        if s.visibility != "visible" and not include_hidden:
            continue
        out[s.name] = extract_sheet(s, schema, harness, workbook_sha=wb.sha256, name=name,
                                     source_id=f"{source_id}:{s.name}", compiled=compiled)
    return out
