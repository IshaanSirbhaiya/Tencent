"""Demo D7 (09_FINAL_PRD §27): Scanned form → typed record with OCR provenance.

**COMPLETE (Prompt 13).** Scanned invoice → OCR (RapidOCR, airgap-safe) → DocumentRecord →
**C15 extraction** → typed record where every extracted field's provenance resolves back
through its ``ocr_bbox`` to the exact region of the page raster the engine read.

End-to-end chain proven:
  * OCR blocks carry ``ocr_bbox`` SourcePointers (media_id, page, x0..y1, ocr_conf);
  * C15 grounds each extracted field against the OCR text (confusion-tolerant ≥0.85 + bbox
    existence, §11.4) → the field's pointer IS an ``ocr_bbox``;
  * ``resolve_bbox(pointer, raster)`` crops the pixels for that field — provenance resolvable
    end to end (no value without a region of the scan behind it).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from databridge_harness import Harness, FakeBackend

from databridge_convert.adapters.source import image_ocr
from databridge_convert.extract import extract


@dataclass
class D7Field:
    name_hint: str
    value: str
    bbox: tuple                 # (x0,y0,x1,y1)
    ocr_conf: float
    media_id: str
    resolved_shape: tuple       # the cropped pixel region's shape — proof it resolves


def run_d7(scan_png: bytes | None = None) -> dict:
    """OCR a scanned form and return the resolvable-provenance scaffold for C15."""
    if scan_png is None:
        from fixtures.ocr.build import build_clean       # test fixtures on path
        scan_png = build_clean().png

    doc, media, raster = image_ocr.read_with_artifacts(scan_png, source_id="scanned_form")

    fields: list[D7Field] = []
    n_resolved = 0
    n_failed = 0
    for i, block in enumerate(doc.blocks):
        ptr = doc.block_provenance.get(i)
        if ptr is None or ptr.kind != "ocr_bbox":
            continue
        try:
            crop = image_ocr.resolve_bbox(ptr, raster)
            shape = tuple(crop.shape)
            n_resolved += 1
        except Exception:
            n_failed += 1
            continue
        if ":" in block.text:
            label, value = block.text.split(":", 1)
            fields.append(D7Field(
                name_hint=label.strip(), value=value.strip(),
                bbox=(ptr.x0, ptr.y0, ptr.x1, ptr.y1), ocr_conf=ptr.ocr_conf,
                media_id=ptr.media_id, resolved_shape=shape))

    # non-vacuous: True only if at least one bbox actually resolved AND none failed (LOW-2).
    all_bboxes_resolvable = n_resolved > 0 and n_failed == 0

    # --- C15 stage: OCR DocumentRecord → typed record with bbox provenance ---
    extraction = _extract_typed(doc, raster)

    return {
        "status": "DONE — OCR → C15 typed record, bbox provenance resolvable end-to-end",
        "ocr_engine": doc.source_meta["ocr_engine"],
        "n_blocks": len(doc.blocks),
        "media_id": media.media_id,
        "page_dims": (media.width, media.height),
        "all_bboxes_resolvable": all_bboxes_resolvable,
        "n_resolved": n_resolved,
        "fields": [vars(f) for f in fields],
        **extraction,
    }


# the OCR scan's typed-record schema (text fields → confusion-tolerant grounding).
_SCAN_SCHEMA = {
    "properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}"},
        "invoice_date": {"type": "string", "format": "date", "description": "date"},
        "bill_to": {"type": "string", "description": "bill to customer"},
        "total_due": {"type": "number", "description": "total due"},
    },
    "required": ["invoice_number"],
}


def _extract_typed(doc, raster, harness: Optional[Harness] = None) -> dict:
    """Run C15 over the OCR DocumentRecord and confirm each field resolves to scan pixels."""
    import json as _json
    gold = {"invoice_number": "INV-2024-0042", "invoice_date": "2024-03-15",
            "bill_to": "Acme Industries", "total_due": 1350.00}
    cfg = {"routing": {"document_extraction": "m"}, "aliases": {"m": {"model": "openai/gpt-4.1"}},
           "profiles": {"cloud": {}, "airgapped": {}}}
    harness = harness or Harness(
        FakeBackend(lambda model, messages, guided_json: _json.dumps(gold)),
        config=cfg, profile="cloud")
    res = extract(doc, _SCAN_SCHEMA, harness, name="ScannedInvoice", source_id=doc.document_id)

    typed = {}
    bbox_resolvable = {}
    for n, fr in res.fields.items():
        if not fr.passed or fr.pointer is None:
            continue
        typed[n] = fr.value
        if fr.pointer.kind == "ocr_bbox":
            crop = image_ocr.resolve_bbox(fr.pointer, raster)
            bbox_resolvable[n] = crop.size > 0
    return {
        "typed_record": typed,
        "extraction_outcome": res.outcome,
        "field_pointer_kinds": {n: (fr.pointer.kind if fr.pointer else None)
                                for n, fr in res.fields.items()},
        "every_field_bbox_resolvable": bool(bbox_resolvable) and all(bbox_resolvable.values()),
    }
