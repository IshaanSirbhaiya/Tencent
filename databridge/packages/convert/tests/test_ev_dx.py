"""EV-DX gate + the grounding canary at the PIPELINE level (09_FINAL_PRD §23, §11).

Gate: field F1 ≥0.85 (D6 wants ≥0.9). Canary: a fabricating backend whose output contains a
value absent from source must be CAUGHT by grounding — required field ⇒ record quarantined,
optional field ⇒ field nulled+flagged (the record degrades to `partial`, never silently
emits the fabrication)."""
from __future__ import annotations

import json

from databridge_harness import Harness, FakeBackend

from databridge_convert.adapters.source import pdf_text
from databridge_convert.extract import extract
from ev_dx import CFG, run_ev_dx


def test_ev_dx_gate_and_d6():
    sc = run_ev_dx()
    assert sc["field_f1"] >= sc["f1_gate"], sc           # ≥0.85 floor
    assert sc["field_f1"] >= sc["d6_gate"], sc           # ≥0.90 for the clean D6 fixtures
    assert sc["n_docs"] == 5
    # honesty (review HIGH-1): the grounding WALL must earn a MAJORITY of the score, not have it
    # all carried by the zero-token pre-extractor — a MAJORITY of scored fields go via grounding.
    assert sc["grounding_scored_fraction"] >= 0.5, sc
    assert sc["tp_via_grounding"] >= 12, sc


def _doc(lines):
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO(); c = canvas.Canvas(buf)
    y = 740
    for ln in lines:
        c.drawString(72, y, ln); y -= 20
    c.showPage(); c.save()
    return pdf_text.read(buf.getvalue())


# schema with a required field (secret_total) NOT present in the source doc, so a fabricating
# backend's value for it is the only source — grounding must reject it.
_CANARY_SCHEMA = {
    "properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}"},
        "bill_to": {"type": "string", "description": "customer name"},
        "secret_total": {"type": "number", "description": "total"},
    },
    "required": ["invoice_number", "secret_total"],
}


def test_canary_required_fabrication_quarantines():
    # doc has the invoice number + customer, but NO total anywhere.
    doc = _doc(["Invoice Number: INV-2024-0042", "Bill To: Acme Industries"])
    # backend FABRICATES secret_total (9999.99 — absent from source).
    fab = FakeBackend(lambda model, messages, guided_json: json.dumps(
        {"invoice_number": "INV-2024-0042", "bill_to": "Acme Industries", "secret_total": 9999.99}))
    res = extract(doc, _CANARY_SCHEMA, Harness(fab, config=CFG, profile="cloud"), name="Inv")
    assert res.outcome == "quarantined"                          # required fabrication caught
    assert any("secret_total" in r for r in res.quarantine_reasons)
    assert res.fields["secret_total"].passed is False
    assert res.record()["secret_total"] is None                  # never emitted

def test_canary_optional_fabrication_nulled_and_flagged():
    doc = _doc(["Invoice Number: INV-2024-0042", "Total Due: 1350.00"])
    schema = {"properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}"},
        "bill_to": {"type": "string", "description": "customer name"},  # OPTIONAL
        "total_due": {"type": "number"}},
        "required": ["invoice_number"]}
    fab = FakeBackend(lambda model, messages, guided_json: json.dumps(
        {"invoice_number": "INV-2024-0042", "bill_to": "Phantom Corp", "total_due": 1350.00}))
    res = extract(doc, schema, Harness(fab, config=CFG, profile="cloud"), name="Inv")
    assert res.outcome == "partial"                              # not quarantined (optional)
    assert res.fields["bill_to"].passed is False                 # fabrication caught
    assert res.record()["bill_to"] is None                       # nulled
    assert res.record()["invoice_number"] == "INV-2024-0042"     # real field survives


def test_no_value_bypasses_grounding():
    """Every passing field carries a resolvable pointer (P4) — asserted directly, per field."""
    from fixtures.extract import INVOICE_SCHEMA, invoice_fixtures
    from databridge_harness import Harness, FakeBackend
    from ev_dx import dx_gold_backend
    h = Harness(FakeBackend(dx_gold_backend()), config=CFG, profile="cloud")
    for fx in invoice_fixtures():
        res = extract(pdf_text.read(fx.pdf), INVOICE_SCHEMA, h, name="Invoice", source_id=fx.name)
        for name, fr in res.fields.items():
            if fr.passed:
                assert fr.grounded is not None and fr.pointer is not None, (fx.name, name)
