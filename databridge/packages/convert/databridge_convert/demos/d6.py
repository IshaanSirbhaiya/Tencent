"""Demo D6 (09_FINAL_PRD §27): PDF invoice → validated JSON record with provenance.

Runs the full C15 pipeline on a text-PDF invoice: deterministic pre-extraction → strict
structured LLM → GROUNDING WALL → confidence/bands → a typed record where EVERY field carries
a resolvable SourcePointer. The 5-invoice EV-DX run (field F1 ≥0.9 on these clean fixtures,
§27) is the gate; this demo shows one invoice end-to-end + the scorecard.

Deterministic in CI via the EV-DX gold backend (pipeline proof); the honest live number is
whatever a real Harness produces.
"""
from __future__ import annotations

from typing import Optional

from databridge_harness import Harness, FakeBackend

from ..adapters.source import pdf_text
from ..extract import extract


def run_d6(harness: Optional[Harness] = None) -> dict:
    from ev_dx import CFG, dx_gold_backend, run_ev_dx          # test-side fixtures/scorer
    from fixtures.extract import INVOICE_SCHEMA, invoice_by_name

    harness = harness or Harness(FakeBackend(dx_gold_backend()), config=CFG, profile="cloud")
    fx = invoice_by_name("inv1")
    doc = pdf_text.read(fx.pdf)
    res = extract(doc, INVOICE_SCHEMA, harness, name="Invoice", source_id=fx.name)

    fields = {n: {"value": fr.value, "confidence": round(fr.confidence, 3), "band": fr.band,
                  "grounded": fr.grounded is not None, "locked": fr.locked,
                  "pointer_kind": fr.pointer.kind if fr.pointer else None}
              for n, fr in res.fields.items()}
    every_field_grounded = all(fr.grounded is not None for fr in res.fields.values() if fr.passed)
    scorecard = run_ev_dx(harness)
    return {
        "outcome": res.outcome,
        "record": res.record(),
        "fields": fields,
        "pre_extract_coverage": round(res.pre_extract_coverage, 2),
        "every_passing_field_grounded": every_field_grounded,
        "ev_dx_f1": round(scorecard["field_f1"], 4),
        "ev_dx_d6_pass": scorecard["d6_pass"],
        "violations": [v.detail for v in res.violations],
    }
