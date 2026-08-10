"""C16 — extract Excel-irregular + scanned-form paths (09_FINAL_PRD §11 + §14).

Two compositions over the C15 core:
  * irregular Excel whose "headers" are a vertical KEY-VALUE block → routed to C15 extraction,
    every field grounded to a ``workbook_cell`` pointer (not a one-nonsense-row RowBatch);
  * a SECOND, harder scanned form (the ``degraded`` OCR fixture) → C15 → typed record where the
    confidence routing DIFFERENTIATES it from the clean scan (a field degrades to the review band)
    while bbox provenance still resolves.

Plus the grounding-wall generalisation that makes the workbook_cell pointer flow (and the proof
the doc_span fallback is unchanged), and the anti-fabrication canary on the Excel path.
"""
from __future__ import annotations

import json

import pytest

from databridge_core import SnapshotIdentity, SourcePointer
from databridge_harness import FakeBackend, Harness

from databridge_convert.excelsub import read_workbook
from databridge_convert.excelsub.policies import ReadOptions
from databridge_convert.extract import (
    compile_target,
    extract_sheet,
    ground_field,
    looks_like_key_value,
    route_workbook,
    sheet_to_document,
)
from databridge_convert.extract.grounding import GroundingSource
from databridge_convert.ir import Block, CharIndex, DocumentRecord
from fixtures.excel.build import KV_INVOICE_GOLD, build_gauntlet, build_kv_invoice

CFG = {"routing": {"document_extraction": "m"}, "aliases": {"m": {"model": "openai/gpt-4.1"}},
       "profiles": {"cloud": {}, "airgapped": {}}}
SCHEMA = {"properties": {
    "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}",
                       "description": "invoice number"},
    "invoice_date": {"type": "string", "format": "date", "description": "invoice date"},
    "bill_to": {"type": "string", "description": "customer / bill to name"},
    "subtotal": {"type": "number", "description": "subtotal amount"},
    "tax": {"type": "number", "description": "tax amount"},
    "total_due": {"type": "number", "description": "total amount due"}},
    "required": ["invoice_number", "total_due"]}


def _gold_harness(gold: dict) -> Harness:
    return Harness(FakeBackend(lambda model, messages, guided_json: json.dumps(gold)),
                   config=CFG, profile="cloud")


def _ocr_available() -> bool:
    from databridge_convert.adapters.source.ocr_engines import OcrUnavailable, select_engine
    try:
        select_engine(); return True
    except OcrUnavailable:
        return False


# ===================================================================================
# Part A — irregular Excel as key-value block
# ===================================================================================
def test_kv_sheet_detected_and_routed():
    compiled = compile_target(SCHEMA, name="Invoice")
    wb = read_workbook(build_kv_invoice(), options=ReadOptions(sheets="all"))
    sheet = wb.sheets[0]
    v = looks_like_key_value(sheet, compiled)
    assert v.is_key_value, v.reason
    assert v.populated_cols == 2 and v.schema_overlap == 1.0
    # the detector is FOOLED by the typed value column into HIGH header confidence — proving the
    # schema-field-name signal (not low confidence alone) is what catches this case.
    assert sheet.header_inference.confidence >= 0.9


def test_kv_extraction_typed_record_with_workbook_cell_provenance():
    wb = read_workbook(build_kv_invoice(), options=ReadOptions(sheets="all"))
    routing = extract_sheet(wb.sheets[0], SCHEMA, _gold_harness(KV_INVOICE_GOLD),
                            workbook_sha=wb.sha256, name="Invoice", source_id="kv")
    assert routing.path == "key_value"
    res = routing.extraction
    assert res.outcome == "ok"
    assert res.record() == KV_INVOICE_GOLD
    # every emitted field resolves to the SOURCE CELL it came from (§14.19), not a doc_span.
    cells = {}
    for n, fr in res.fields.items():
        assert fr.passed and fr.pointer is not None
        assert fr.pointer.kind == "workbook_cell"
        assert fr.pointer.sheet == "Invoice Summary"
        cells[n] = fr.pointer.cell
    # the value column is column B; the six fields point at B1..B6 in order.
    assert cells["invoice_number"] == "B1" and cells["total_due"] == "B6"


def test_kv_sheet_to_document_blocks_carry_cell_pointers():
    wb = read_workbook(build_kv_invoice(), options=ReadOptions(sheets="all"))
    doc = sheet_to_document(wb.sheets[0], wb.sha256, source_id="kv")
    assert [b.text for b in doc.blocks][0] == "Invoice Number: INV-2024-0042"
    assert all(p.kind == "workbook_cell" for p in doc.block_provenance.values())
    assert doc.block_provenance[0].cell == "B1"


def test_kv_path_holds_the_grounding_wall_fabrication_quarantines():
    """The Excel-KV path routes through the SAME wall: a REQUIRED field absent from the sheet,
    fabricated by the model, must be caught (no cell to ground against) → record quarantined."""
    schema = json.loads(json.dumps(SCHEMA))
    schema["properties"]["purchase_order"] = {"type": "string", "description": "purchase order"}
    schema["required"] = ["invoice_number", "purchase_order"]
    fabricated = dict(KV_INVOICE_GOLD, purchase_order="PO-FAKE-9999")  # appears in NO cell
    wb = read_workbook(build_kv_invoice(), options=ReadOptions(sheets="all"))
    routing = extract_sheet(wb.sheets[0], schema, _gold_harness(fabricated),
                            workbook_sha=wb.sha256, name="Invoice", source_id="kv")
    res = routing.extraction
    assert res.outcome == "quarantined"
    assert res.fields["purchase_order"].passed is False
    assert any("purchase_order" in r for r in res.quarantine_reasons)


def test_kv_value_column_is_located_not_assumed_adjacent():
    """A KV sheet with a blank SPACER column between label and value: the value pointer must
    reference the ACTUAL value cell (column C), not a hard-wired col+1 (which would read the
    blank spacer and either drop the field or point at the wrong cell). [review BLOCKER 1]"""
    from databridge_convert.ir import SheetRecord
    sheet = SheetRecord(name="Sum", row_origin=0, col_origin=0, cells=[
        ["Invoice Number", None, "INV-2024-0042"],
        ["Total Due", None, 1350.00]])
    doc = sheet_to_document(sheet, "sha", source_id="kv")
    assert [b.text for b in doc.blocks] == ["Invoice Number: INV-2024-0042", "Total Due: 1350"]
    assert doc.block_provenance[0].cell == "C1"      # column C (index 2), not B
    assert doc.block_provenance[1].cell == "C2"


def test_kv_error_cells_are_skipped_not_leaked():
    """An ExcelError value (#N/A) is not a value — the row is skipped, not rendered as a token
    that could ground or leak into provenance. [review BLOCKER 2]"""
    from databridge_convert.excelsub.policies import ExcelError
    from databridge_convert.ir import SheetRecord
    sheet = SheetRecord(name="Sum", row_origin=0, col_origin=0, cells=[
        ["Invoice Number", "INV-2024-0042"],
        ["Total Due", ExcelError(code="#N/A")]])
    doc = sheet_to_document(sheet, "sha", source_id="kv")
    assert [b.text for b in doc.blocks] == ["Invoice Number: INV-2024-0042"]
    assert all("#N/A" not in b.text for b in doc.blocks)


def test_schema_light_detection_never_routes():
    """Detection is schema-aware: without a compiled target there is nothing for the first column
    to 'look like', so a schema-light call never routes to KV (the safe default). [review HIGH 4]"""
    wb = read_workbook(build_kv_invoice(), options=ReadOptions(sheets="all"))
    assert looks_like_key_value(wb.sheets[0]).is_key_value is False


def test_narrow_lookup_table_with_one_field_match_does_not_route():
    """A narrow 2-col lookup table whose col-0 has ONE entry coincidentally matching a field name
    must NOT route to KV (the old `overlap>0` branch let it through). [review HIGH 4]"""
    from databridge_convert.ir import HeaderGuess, SheetRecord
    compiled = compile_target(SCHEMA, name="Invoice")
    # col-0 = country codes; exactly one row's label normalises to a field ('tax'); low confidence.
    sheet = SheetRecord(name="Lookup", row_origin=0, col_origin=0,
                        header_inference=HeaderGuess(row_idx=0, confidence=0.5),
                        cells=[["US", "1"], ["UK", "44"], ["Tax", "20"], ["DE", "49"]])
    v = looks_like_key_value(sheet, compiled)
    assert v.is_key_value is False and v.schema_overlap < 0.5


def test_real_tables_do_not_misroute_to_key_value():
    """Every Gauntlet sheet is a TABLE (or a 1-col scrap) — none may route to the KV path, even
    the low-confidence multi-row-header 'Quarterly' sheet (wide, col-0-below-header is data)."""
    compiled = compile_target(SCHEMA, name="Invoice")
    wb = read_workbook(build_gauntlet(), options=ReadOptions(sheets="all"))
    routing = route_workbook(wb, SCHEMA, _gold_harness(KV_INVOICE_GOLD), name="Invoice")
    assert all(r.path == "table" for r in routing.values()), {
        n: r.path for n, r in routing.items()}
    # 'Quarterly' is low-confidence but is correctly rejected by the narrow-shape / overlap gates.
    q = looks_like_key_value(next(s for s in wb.sheets if s.name == "Quarterly"), compiled)
    assert not q.is_key_value and q.populated_cols > 2


# ===================================================================================
# Part B — the grounding-wall generalisation (workbook_cell flows; doc_span unchanged)
# ===================================================================================
def _plain_doc(text: str, provenance=None) -> DocumentRecord:
    snap = SnapshotIdentity(source_id="t", as_of="2024-01-01T00:00:00", content_hash="h",
                            schema_hash="s")
    return DocumentRecord(document_id="t", sha256="h", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=text)],
                          char_index=CharIndex(spans={0: (0, len(text))}),
                          block_provenance=provenance or {})


def test_grounding_doc_span_fallback_unchanged_without_provenance():
    from databridge_convert.extract.target_model import FieldSpec
    doc = _plain_doc("Total Due: 1350.00")
    out = ground_field(FieldSpec(name="total", grounding="number"), 1350.00, GroundingSource(doc))
    assert out.passed and out.pointer.kind == "doc_span"


def test_grounding_emits_intrinsic_block_pointer_when_present():
    from databridge_convert.extract.target_model import FieldSpec
    ptr = SourcePointer(source_id="wb", kind="workbook_cell", workbook_sha="abc",
                        sheet="S", cell="B1")
    doc = _plain_doc("Invoice Number: INV-2024-0042", provenance={0: ptr})
    out = ground_field(FieldSpec(name="inv", grounding="id"), "INV-2024-0042",
                       GroundingSource(doc))
    assert out.passed and out.pointer.kind == "workbook_cell" and out.pointer.cell == "B1"


# ===================================================================================
# Part C — second, harder scanned form (needs an OCR engine)
# ===================================================================================
@pytest.mark.skipif(not _ocr_available(), reason="no OCR engine (models/binary)")
def test_degraded_scan_still_typed_with_bbox_but_lower_confidence():
    from databridge_convert.adapters.source import image_ocr
    from databridge_convert.extract import extract
    from fixtures.ocr.build import build_degraded
    schema = {"properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}"},
        "invoice_date": {"type": "string", "format": "date", "description": "date"},
        "bill_to": {"type": "string", "description": "bill to customer"},
        "total_due": {"type": "number", "description": "total due"}},
        "required": ["invoice_number"]}
    gold = {"invoice_number": "INV-2024-0042", "invoice_date": "2024-03-15",
            "bill_to": "Acme Industries", "total_due": 1350.00}
    fx = build_degraded()
    doc, _media, raster = image_ocr.read_with_artifacts(fx.png, source_id="degraded")
    res = extract(doc, schema, _gold_harness(gold), name="ScannedInvoice", source_id=doc.document_id)
    # STILL a typed record (required field read) — the degradation does not destroy the scan.
    assert res.record()["invoice_number"] == "INV-2024-0042"
    # appropriately LOWER confidence: at least one field routes to the REVIEW band → partial.
    assert res.outcome == "partial"
    assert any(fr.band == "review" for fr in res.fields.values())
    # every field that DID pass still carries a resolvable bbox pointer (provenance intact).
    for fr in res.fields.values():
        if fr.passed and fr.pointer is not None and fr.pointer.kind == "ocr_bbox":
            assert image_ocr.resolve_bbox(fr.pointer, raster).size > 0


@pytest.mark.skipif(not _ocr_available(), reason="no OCR engine (models/binary)")
def test_confidence_routing_differentiates_clean_vs_degraded():
    from ev_dx import run_ev_dx_noisy_scan
    out = run_ev_dx_noisy_scan()
    assert out["differentiates_clean_vs_degraded"] is True
    assert out["per_fixture"]["clean"]["review_fields"] == []
    assert out["per_fixture"]["degraded"]["review_fields"]            # ≥1 field to review
    assert out["all_passing_bbox_resolvable"] is True
    # the mild EV-OCR 'noisy' fixture does NOT differentiate (RapidOCR shrugs it off) — this is a
    # CALIBRATION finding we surface honestly, not hide (§2 honesty).
    assert out["calibration_flag_mild_noisy_no_differentiation"] is True


# ===================================================================================
# Part D — EV-DX extended scores (both new fixture types clear the gate)
# ===================================================================================
def test_ev_dx_excel_kv_scores():
    from ev_dx import run_ev_dx_excel_kv
    out = run_ev_dx_excel_kv()
    assert out["field_f1"] == 1.0 and out["f1_pass"] is True
    assert out["all_workbook_cell_provenance"] is True
    assert out["routed_path"] == "key_value"


@pytest.mark.skipif(not _ocr_available(), reason="no OCR engine (models/binary)")
def test_ev_dx_noisy_scan_scores():
    from ev_dx import run_ev_dx_noisy_scan
    out = run_ev_dx_noisy_scan()
    # the HARDER scan still clears the EV-DX field-F1 gate (0.85) — graceful degradation, not
    # collapse — while scoring strictly below the clean scan.
    assert out["field_f1"] >= 0.85
    assert out["per_fixture"]["degraded"]["field_f1"] < out["per_fixture"]["clean"]["field_f1"]
