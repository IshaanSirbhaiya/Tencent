"""C15 unit tests — target compiler, pre-extraction, span selection, confidence/cross-field."""
from __future__ import annotations

import io

import pytest
from pydantic import BaseModel
from reportlab.pdfgen import canvas

from databridge_convert.adapters.source import pdf_text
from databridge_convert.extract import (
    band_of,
    blend_field_confidence,
    compile_target,
    from_pydantic,
    from_sql_ddl,
    pre_extract,
    select_spans,
    validate_cross_fields,
)
from databridge_convert.extract.target_model import from_json_schema


def _doc(lines):
    buf = io.BytesIO(); c = canvas.Canvas(buf)
    y = 740
    for ln in lines:
        c.drawString(72, y, ln); y -= 20
        if y < 72:                              # paginate so no line is drawn off-page
            c.showPage(); y = 740
    c.showPage(); c.save()
    return pdf_text.read(buf.getvalue())


# --- compiler: three input forms → one model ---
def test_compile_json_schema_repeating_and_grounding():
    js = {"properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d+"},
        "total_due": {"type": "number"},
        "bill_to": {"type": "string", "description": "customer"},
        "due_date": {"type": "string", "format": "date"},
        "status": {"type": "string", "enum": ["paid", "unpaid"]},
        "line_items": {"type": "array", "x-repeats": "row_anchor",
                       "items": {"properties": {"sku": {"type": "string"}}, "required": ["sku"]}}},
        "required": ["invoice_number", "total_due"]}
    ct = compile_target(js, name="Invoice")
    g = {f.name: f.grounding for f in ct.fields}
    assert g == {"invoice_number": "id", "total_due": "number", "bill_to": "name",
                 "due_date": "date", "status": "enum", "line_items": "text"}
    assert ct.field("invoice_number").required and not ct.field("bill_to").required
    assert ct.field("line_items").repeats and ct.field("line_items").item_fields
    assert ct.field("invoice_number").pattern == r"INV-\d+"


def test_compile_sql_ddl():
    ddl = ("CREATE TABLE crm (contact_id INTEGER NOT NULL, full_name VARCHAR NOT NULL, "
           "amount NUMERIC, stage VARCHAR CHECK (stage IN ('lead','won')))")
    ct = from_sql_ddl(ddl)
    assert ct.name == "crm"
    assert ct.field("contact_id").grounding == "id" and ct.field("contact_id").required
    assert ct.field("full_name").grounding == "name"
    assert ct.field("stage").enum == ("lead", "won")


def test_compile_pydantic():
    class M(BaseModel):
        order_id: str
        customer_name: str
        amount: float
    ct = from_pydantic(M)
    assert {f.name: f.grounding for f in ct.fields} == {
        "order_id": "id", "customer_name": "name", "amount": "number"}


def test_compile_pydantic_enum_and_money_grounding():
    import enum
    from typing import Literal

    class Stage(str, enum.Enum):
        lead = "lead"; won = "won"

    class M(BaseModel):
        status: Literal["paid", "unpaid"]
        stage: Stage
        account_balance: float                      # money, NOT an id (M1)
        invoice_total: float                        # money, NOT an id (M1)
        contact_id: int                             # a real id
    ct = from_pydantic(M)
    g = {f.name: f.grounding for f in ct.fields}
    assert g["status"] == "enum" and g["stage"] == "enum"           # B1
    assert ct.field("status").enum == ("paid", "unpaid")
    assert g["account_balance"] == "number" and g["invoice_total"] == "number"
    assert g["contact_id"] == "id"


def test_compile_sql_table_level_primary_key_required():
    ddl = "CREATE TABLE t (a INTEGER, b VARCHAR, amount NUMERIC, PRIMARY KEY (a))"
    ct = from_sql_ddl(ddl)
    assert ct.field("a").required is True            # H4: table-level PK → required
    assert ct.field("b").required is False


# --- pre-extraction ---
def test_pre_extract_key_value_and_pattern_locks():
    js = {"properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}"},
        "bill_to": {"type": "string", "description": "bill to"},
        "total_due": {"type": "number"},
        "missing_field": {"type": "string"}},
        "required": ["invoice_number"]}
    ct = compile_target(js)
    doc = _doc(["Invoice Number: INV-2024-0042", "Bill To: Acme Industries",
                "Total Due: 1350.00"])
    pre = pre_extract(doc, ct)
    assert "invoice_number" in pre.locked and "total_due" in pre.locked
    assert pre.locked["invoice_number"].value == "INV-2024-0042"
    assert pre.locked["invoice_number"].pointer.kind == "doc_span"     # every lock is grounded
    assert "missing_field" not in pre.locked
    assert 0.0 < pre.coverage <= 1.0


# --- span selection ---
def test_repeating_items_from_table_block(monkeypatch):
    """H3: deterministic table rows populate a repeating field (not just the LLM)."""
    from databridge_convert.extract.extractor import _rows_to_items
    from databridge_convert.extract import FieldSpec
    rf = FieldSpec(name="line_items", repeats=True,
                   item_fields=(FieldSpec(name="sku", grounding="id"),
                                FieldSpec(name="amount", grounding="number")))
    rows = [{"SKU": "A-1", "Amount": "10.00"}, {"SKU": "B-2", "Amount": "20.00"}]
    items = _rows_to_items(rows, rf)
    assert items == [{"sku": "A-1", "amount": "10.00"}, {"sku": "B-2", "amount": "20.00"}]


def test_span_selection_required_field_unmatched_falls_back_to_whole_doc():
    # a required field whose value shares NO tokens with its name → must not be dropped (M2)
    ct = compile_target({"properties": {"zzz_required": {"type": "string"}},
                         "required": ["zzz_required"]})
    lines = [f"filler line {i} lorem ipsum dolor" for i in range(200)]
    sel = select_spans(_doc(lines), ct, char_ceiling=500)
    assert sel.whole_doc is True                     # fell back rather than risk a false quarantine


def test_span_selection_whole_doc_fallback_small():
    ct = compile_target({"properties": {"x": {"type": "string"}}})
    sel = select_spans(_doc(["short doc"]), ct)
    assert sel.whole_doc is True

def test_span_selection_retrieves_on_large_doc():
    ct = compile_target({"properties": {"invoice_total": {"type": "number",
                                        "description": "grand total amount due"}}})
    lines = [f"filler line number {i} lorem ipsum dolor sit amet" for i in range(200)]
    lines.insert(100, "Grand Total Amount Due: 4242.00")
    sel = select_spans(_doc(lines), ct, char_ceiling=500)
    assert sel.whole_doc is False
    assert "4242.00" in sel.text                       # the relevant block was retrieved


# --- confidence blend + bands ---
def test_blend_locked_capped_at_grounding():
    # a lock that grounded EXACTLY (1.0) is auto at 1.0; a lock that only grounded fuzzily is
    # capped at its grounding score — the lock doesn't overstate a weak match (review L7).
    assert blend_field_confidence(1.0, locked=True) == 1.0
    assert blend_field_confidence(0.9, locked=True) == 0.9

def test_blend_capped_at_grounding():
    # a rosy self-report can't push a weakly-grounded field to auto
    c = blend_field_confidence(0.85, self_report=0.99, ocr_word_conf=0.99)
    assert c <= 0.85 and band_of(c) in ("spot_check", "review")

def test_bands():
    assert band_of(0.99) == "auto" and band_of(0.88) == "spot_check" and band_of(0.5) == "review"


# --- cross-field validation ---
def test_cross_field_sum_and_enum_and_dates():
    js = {"properties": {
        "status": {"type": "string", "enum": ["paid", "unpaid"]},
        "start": {"type": "string", "format": "date"},
        "end": {"type": "string", "format": "date"},
        "total": {"type": "number"},
        "items": {"type": "array", "x-repeats": "row_anchor",
                  "items": {"properties": {"amount": {"type": "number"}}}}},
        "x-cross-field": [
            {"kind": "date_order", "fields": ["start", "end"]},
            {"kind": "sum_to_total", "items": "items", "item_amount": "amount", "total": "total"}]}
    ct = compile_target(js)
    bad = {"status": "void", "start": "2024-05-01", "end": "2024-01-01", "total": 100.0,
           "items": [{"amount": 30.0}, {"amount": 30.0}]}
    v = {viol.rule for viol in validate_cross_fields(bad, ct)}
    assert v == {"enum", "date_order", "sum_to_total"}
    good = {"status": "paid", "start": "2024-01-01", "end": "2024-05-01", "total": 60.0,
            "items": [{"amount": 30.0}, {"amount": 30.0}]}
    assert validate_cross_fields(good, ct) == []
