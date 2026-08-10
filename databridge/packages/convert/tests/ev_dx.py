"""EV-DX scorer — document extraction field P/R/F1 (09_FINAL_PRD §23). Gate F1 ≥0.85
(D6 wants ≥0.9 on these clean fixtures, §27).

Runs the full C15 pipeline over the 5 invoice fixtures and scores the grounded record against
hand-labelled gold. Like EV-SQL, a deterministic GOLD BACKEND lets CI prove the PIPELINE
(pre-extract → LLM → grounding → confidence) end-to-end without a live model; the honest
live number is whatever a real Harness produces. Grounding is what makes the number
trustworthy — a value that doesn't appear in source is dropped before it can score a TP.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from databridge_harness import Harness, FakeBackend

from databridge_convert.extract import extract
from fixtures.extract import INVOICE_SCHEMA, invoice_fixtures

F1_GATE = 0.85
D6_GATE = 0.90

CFG = {"routing": {"document_extraction": "m"},
       "aliases": {"m": {"model": "openai/gpt-4.1"}},
       "profiles": {"cloud": {}, "airgapped": {}}}


def dx_gold_backend(fixtures=None):
    """A backend that returns the gold extraction for whichever invoice is in the prompt
    (matched by its unique invoice number) — deterministic CI, like EV-SQL's gold backend."""
    fixtures = fixtures or invoice_fixtures()
    table = {fx.gold["invoice_number"]: fx.gold for fx in fixtures}

    def _backend(model, messages, guided_json):
        text = " ".join(m.get("content", "") for m in messages)
        for inv_no, gold in table.items():
            if inv_no in text:
                return json.dumps(gold)
        return "{}"
    return _backend


def _norm(v):
    """Value-equality normaliser: numeric strings compare as numbers (so the pre-extracted
    text "1250.00" equals the gold float 1250.0), everything else as collapsed lower-case."""
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = re.sub(r"\s+", " ", str(v)).strip()
    cleaned = s.replace(",", "").replace("$", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return round(float(cleaned), 2)
    return s.lower()


@dataclass
class DocScore:
    name: str
    tp: int = 0
    fp: int = 0
    fn: int = 0


def score_doc(name: str, extracted: dict, gold: dict) -> DocScore:
    sc = DocScore(name=name)
    for k, gv in gold.items():
        ev = extracted.get(k)
        if ev is None:
            sc.fn += 1                          # gold field not produced (or nulled by grounding)
        elif _norm(ev) == _norm(gv):
            sc.tp += 1
        else:
            sc.fp += 1                          # produced a wrong value
    # a field produced that isn't in gold also counts as FP
    for k, ev in extracted.items():
        if ev is not None and k not in gold:
            sc.fp += 1
    return sc


def _score_record(name: str, extracted: dict, gold: dict) -> dict:
    """field P/R/F1 for ONE extracted record against its gold (the EV-DX metric, per-doc)."""
    sc = score_doc(name, extracted, gold)
    p = sc.tp / (sc.tp + sc.fp) if (sc.tp + sc.fp) else 0.0
    r = sc.tp / (sc.tp + sc.fn) if (sc.tp + sc.fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "field_f1": round(f1, 3),
            "tp": sc.tp, "fp": sc.fp, "fn": sc.fn}


def run_ev_dx_excel_kv(harness: Harness | None = None) -> dict:
    """EV-DX, fixture type **irregular-excel-as-kv** (C16): a finance-export sheet whose "headers"
    are a vertical key-value block → routed to C15 → typed record. Records field P/R/F1 AND
    asserts every emitted field resolves to a ``workbook_cell`` pointer (§14.19 provenance)."""
    import json
    from databridge_convert.excelsub import read_workbook
    from databridge_convert.excelsub.policies import ReadOptions
    from databridge_convert.extract import compile_target, extract_sheet
    from fixtures.excel.build import KV_INVOICE_GOLD, build_kv_invoice

    schema = {"properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}",
                           "description": "invoice number"},
        "invoice_date": {"type": "string", "format": "date", "description": "invoice date"},
        "bill_to": {"type": "string", "description": "customer / bill to name"},
        "subtotal": {"type": "number", "description": "subtotal amount"},
        "tax": {"type": "number", "description": "tax amount"},
        "total_due": {"type": "number", "description": "total amount due"}},
        "required": ["invoice_number", "total_due"]}
    compiled = compile_target(schema, name="Invoice")
    harness = harness or Harness(
        FakeBackend(lambda model, messages, guided_json: json.dumps(KV_INVOICE_GOLD)),
        config=CFG, profile="cloud")

    wb = read_workbook(build_kv_invoice(), options=ReadOptions(sheets="all"))
    sheet = wb.sheets[0]
    routing = extract_sheet(sheet, schema, harness, workbook_sha=wb.sha256, name="Invoice",
                            source_id="ev_dx_kv", compiled=compiled)
    res = routing.extraction
    metrics = _score_record("excel_kv", res.record(), KV_INVOICE_GOLD)
    # provenance: every passing field must carry a workbook_cell pointer to its source cell.
    pkinds = {n: (fr.pointer.kind if fr.pointer else None)
              for n, fr in res.fields.items() if fr.passed}
    all_workbook_cell = bool(pkinds) and all(k == "workbook_cell" for k in pkinds.values())
    # HONESTY split (§2, mirrors run_ev_dx): a canonical Label|value sheet locks every field in
    # the zero-token pre-pass (labels == field names), so this F1 measures "the pipeline doesn't
    # corrupt a locked value + attaches the right CELL pointer" — NOT grounding discrimination.
    # The wall's discrimination on this path is exercised separately by the fabrication canary
    # (test_kv_path_holds_the_grounding_wall_fabrication_quarantines).
    via_lock = sum(1 for n, fr in res.fields.items()
                   if fr.passed and fr.locked and _norm(res.record().get(n)) == _norm(
                       KV_INVOICE_GOLD.get(n)))
    via_grounding = sum(1 for n, fr in res.fields.items()
                        if fr.passed and not fr.locked and _norm(res.record().get(n)) == _norm(
                            KV_INVOICE_GOLD.get(n)))
    scored = via_lock + via_grounding
    return {"fixture_type": "irregular-excel-as-kv", **metrics,
            "f1_gate": F1_GATE, "f1_pass": metrics["field_f1"] >= F1_GATE,
            "routed_path": routing.path, "outcome": res.outcome,
            "all_workbook_cell_provenance": all_workbook_cell,
            "pointer_kinds": pkinds, "kv_verdict": routing.verdict.reason,
            "tp_via_pre_extract_lock": via_lock, "tp_via_grounding": via_grounding,
            "grounding_scored_fraction": (via_grounding / scored) if scored else 0.0,
            "honesty_note": ("F1 is lock-carried (labels == field names ⇒ all fields lock at "
                             "zero tokens); provenance + the fabrication canary carry the wall.")}


_SCAN_SCHEMA = {"properties": {
    "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}"},
    "invoice_date": {"type": "string", "format": "date", "description": "date"},
    "bill_to": {"type": "string", "description": "bill to customer"},
    "total_due": {"type": "number", "description": "total due"}},
    "required": ["invoice_number"]}
_SCAN_GOLD = {"invoice_number": "INV-2024-0042", "invoice_date": "2024-03-15",
              "bill_to": "Acme Industries", "total_due": 1350.00}


def _run_one_scan(fx, harness: Harness) -> dict:
    from databridge_convert.adapters.source import image_ocr
    from databridge_convert.extract import extract
    doc, _media, raster = image_ocr.read_with_artifacts(fx.png, source_id=fx.name)
    res = extract(doc, _SCAN_SCHEMA, harness, name="ScannedInvoice", source_id=doc.document_id)
    metrics = _score_record(fx.name, res.record(), _SCAN_GOLD)
    bands = {n: fr.band for n, fr in res.fields.items()}
    # bbox provenance must RESOLVE for every passing field (P4, §11.3).
    resolvable = True
    for fr in res.fields.values():
        if fr.passed and fr.pointer is not None and fr.pointer.kind == "ocr_bbox":
            resolvable = resolvable and image_ocr.resolve_bbox(fr.pointer, raster).size > 0
    wc = doc.source_meta.get("ocr_word_confs") or []
    flat = [c for line in wc for c in line] if wc and isinstance(wc[0], list) else wc
    return {**metrics, "outcome": res.outcome, "bands": bands,
            "review_fields": [n for n, b in bands.items() if b == "review"],
            "all_passing_bbox_resolvable": resolvable,
            "avg_ocr_word_conf": round(sum(flat) / len(flat), 3) if flat else None}


def run_ev_dx_noisy_scan(harness: Harness | None = None) -> dict:
    """EV-DX, fixture type **noisy-scanned-form** (C16): the clean scan vs the SECOND, harder
    ``degraded`` scan, each OCR'd → C15 → typed record with bbox provenance. Records field P/R/F1
    per fixture and confirms confidence routing DIFFERENTIATES the two (the degraded scan must
    push ≥1 field to the review band / a lower outcome). The mild ``noisy`` fixture is included to
    EXPOSE the calibration finding: RapidOCR shrugs it off, so it scores like the clean scan —
    `calibration_flag` is raised when a degradation does NOT lower any band (both >0.95)."""
    import json
    from fixtures.ocr.build import build_clean, build_degraded, build_noisy
    harness = harness or Harness(
        FakeBackend(lambda model, messages, guided_json: json.dumps(_SCAN_GOLD)),
        config=CFG, profile="cloud")
    per = {fx.name: _run_one_scan(fx, harness)
           for fx in (build_clean(), build_noisy(), build_degraded())}
    clean, degraded = per["clean"], per["degraded"]
    # differentiation: the harder scan routes ≥1 field to review (clean routes none).
    differentiates = (not clean["review_fields"]) and bool(degraded["review_fields"])
    # calibration flag: the MILD noisy fixture fails to lower any band vs clean (both all-auto).
    mild_no_diff = per["noisy"]["bands"] == clean["bands"]
    return {"fixture_type": "noisy-scanned-form",
            "per_fixture": per,
            # headline P/R/F1 = the harder (degraded) scan.
            "precision": degraded["precision"], "recall": degraded["recall"],
            "field_f1": degraded["field_f1"],
            "differentiates_clean_vs_degraded": differentiates,
            "degraded_outcome": degraded["outcome"],
            "all_passing_bbox_resolvable": degraded["all_passing_bbox_resolvable"],
            "calibration_flag_mild_noisy_no_differentiation": mild_no_diff,
            "calibration_note": (
                "RapidOCR self-reports ≥0.95 word-conf even on mild noise, and grounding is "
                "binary for exact-kind fields, so confidence on CORRECTLY-read fields does not "
                "fall smoothly with noise; differentiation comes from the grounding WALL catching "
                "misreads (failed field → review), not graded confidence on correct reads.")}


def run_ev_dx(harness: Harness | None = None) -> dict:
    harness = harness or Harness(FakeBackend(dx_gold_backend()), config=CFG, profile="cloud")
    from databridge_convert.adapters.source import pdf_text
    scores: list[DocScore] = []
    per_doc = {}
    via_grounding = 0          # correctly-scored fields that were NOT pre-extract-locked
    via_lock = 0               # correctly-scored fields resolved deterministically (zero-token)
    for fx in invoice_fixtures():
        doc = pdf_text.read(fx.pdf)
        res = extract(doc, INVOICE_SCHEMA, harness, name="Invoice", source_id=fx.name)
        sc = score_doc(fx.name, res.record(), fx.gold)
        scores.append(sc)
        for k in fx.gold:
            fr = res.fields.get(k)
            if fr is not None and fr.passed and _norm(res.record().get(k)) == _norm(fx.gold[k]):
                if fr.locked:
                    via_lock += 1
                else:
                    via_grounding += 1
        per_doc[fx.name] = {"outcome": res.outcome, "tp": sc.tp, "fp": sc.fp, "fn": sc.fn,
                            "coverage": round(res.pre_extract_coverage, 2)}
    tp = sum(s.tp for s in scores); fp = sum(s.fp for s in scores); fn = sum(s.fn for s in scores)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    scored = via_grounding + via_lock
    return {"precision": p, "recall": r, "field_f1": f1,
            "f1_gate": F1_GATE, "d6_gate": D6_GATE,
            "f1_pass": f1 >= F1_GATE, "d6_pass": f1 >= D6_GATE,
            "per_doc": per_doc, "n_docs": len(scores),
            # honesty split (§2): how much of the score the WALL earns vs the zero-token pre-pass.
            "tp_via_grounding": via_grounding, "tp_via_pre_extract_lock": via_lock,
            "grounding_scored_fraction": (via_grounding / scored) if scored else 0.0}
