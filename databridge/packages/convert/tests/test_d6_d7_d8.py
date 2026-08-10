"""Demos D6 (PDF invoice→JSON), D7 (scan→OCR→C15 typed record, bbox e2e), D8 (email+attachment
→ CRM, 4-hop provenance chain). 09_FINAL_PRD §27."""
from __future__ import annotations

import pytest

from databridge_convert.demos.d6 import run_d6
from databridge_convert.demos.d8 import run_d8


def _ocr_available() -> bool:
    from databridge_convert.adapters.source.ocr_engines import OcrUnavailable, select_engine
    try:
        select_engine(); return True
    except OcrUnavailable:
        return False


# --- D6 ---
def test_d6_invoice_to_validated_json():
    out = run_d6()
    assert out["outcome"] == "ok"
    assert out["record"]["invoice_number"] == "INV-2024-0042"
    assert out["record"]["total_due"] == 1350.00
    assert out["every_passing_field_grounded"] is True
    assert out["ev_dx_f1"] >= 0.90 and out["ev_dx_d6_pass"] is True
    # every field carries a resolvable pointer (P4)
    assert all(f["pointer_kind"] for f in out["fields"].values() if f["grounded"])


# --- D7 (completed: OCR → C15 typed record with bbox provenance e2e) ---
@pytest.mark.skipif(not _ocr_available(), reason="no OCR engine (models/binary)")
def test_d7_ocr_to_typed_record_bbox_resolvable():
    from databridge_convert.demos.d7 import run_d7
    out = run_d7()
    assert out["status"].startswith("DONE")
    assert out["all_bboxes_resolvable"] is True
    assert out["typed_record"].get("invoice_number") == "INV-2024-0042"
    # the extracted fields are grounded to OCR bboxes that resolve to scan pixels end-to-end
    assert out["every_field_bbox_resolvable"] is True
    assert any(k == "ocr_bbox" for k in out["field_pointer_kinds"].values())


# --- D8 (email + attachment → CRM, 4-hop chain) ---
def test_d8_email_attachment_to_crm_chain():
    out = run_d8()
    assert out["outcome"] == "ok"
    assert out["target"] == "crm_deal"
    assert out["crm_record"]["invoice_number"] == "INV-2024-0042"
    assert out["crm_record"]["customer_name"] == "Acme Industries"
    assert out["crm_record"]["deal_amount"] == 1350.00
    assert out["attachment"]["format"] == "pdf"
    # the provenance chain resolves through ALL FOUR hops for every CRM field
    assert out["chain_resolves_all_four_hops"] is True
    for name, ch in out["provenance_chains"].items():
        assert ch["hops_ok"], (name, ch)
        assert ch["part"] == "attachment:0" and ch["message_id"] and ch["page"] is not None
    # the attachment is 2 pages → the page hop genuinely DISCRIMINATES (header fields on page 0,
    # deal_amount/total on page 1) — not a vacuous always-page-0 (review MEDIUM-1).
    pages = {ch["page"] for ch in out["provenance_chains"].values()}
    assert pages == {0, 1}, out["provenance_chains"]
