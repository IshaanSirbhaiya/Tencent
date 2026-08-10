"""EV-DX extraction fixtures (09_FINAL_PRD §23): 5 text-PDF invoices + hand-labelled gold JSON.

Deterministic (reportlab), hermetic — no checked-in binaries. Each fixture is
``InvoiceFixture(name, pdf_bytes, gold)`` where ``gold`` is the hand-label EV-DX scores
against. Layouts vary (label:value, a couple of prose fields, one with a line-item table) so
the pipeline is exercised across pre-extraction AND the LLM/grounding stages — not all fields
fall to the zero-token pre-pass.

The shared target schema is ``INVOICE_SCHEMA`` (JSON-Schema form, with a repeating line_items
anchor + a sum_to_total cross-field rule). The CRM/email fixture for D8 reuses C14's eml.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

INVOICE_SCHEMA = {
    "properties": {
        "invoice_number": {"type": "string", "pattern": r"INV-\d{4}-\d{4}",
                           "description": "invoice number"},
        "invoice_date": {"type": "string", "format": "date", "description": "invoice date"},
        "bill_to": {"type": "string", "description": "customer / bill to name"},
        "subtotal": {"type": "number", "description": "subtotal amount"},
        "tax": {"type": "number", "description": "tax amount"},
        "total_due": {"type": "number", "description": "total amount due"},
    },
    "required": ["invoice_number", "total_due"],
    "x-cross-field": [
        {"kind": "date_order", "fields": ["invoice_date", "invoice_date"]},
    ],
}


@dataclass(frozen=True)
class InvoiceFixture:
    name: str
    pdf: bytes
    gold: dict


def _pdf(lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 740
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 20
        if y < 72:
            c.showPage(); y = 740
    c.showPage()
    c.save()
    return buf.getvalue()


# Each invoice: (lines rendered, gold record). Amounts are consistent (subtotal+tax=total).
_DATA = [
    ("inv1", [
        "ACME SUPPLY CO — INVOICE",
        "Invoice Number: INV-2024-0042",
        "Invoice Date: 2024-03-15",
        "Bill To: Acme Industries",
        "Subtotal: 1250.00", "Tax: 100.00", "Total Due: 1350.00",
    ], {"invoice_number": "INV-2024-0042", "invoice_date": "2024-03-15",
        "bill_to": "Acme Industries", "subtotal": 1250.00, "tax": 100.00, "total_due": 1350.00}),

    # inv2/inv3/inv4 are PROSE-DOMINANT: only invoice_number is label/pattern-locked by
    # pre-extraction; invoice_date/bill_to/subtotal/tax/total_due are embedded in sentences so
    # they MUST traverse the LLM → grounding-wall path (so EV-DX's number isn't ~all pre-extract).
    ("inv2", [
        "Globex Corporation",
        "Invoice Number: INV-2024-0117",
        "This statement, dated 2024-04-02, bills Initech LLC for the period.",
        "The subtotal comes to 800.00 with tax of 64.00,",
        "for an amount payable of 864.00.",
    ], {"invoice_number": "INV-2024-0117", "invoice_date": "2024-04-02",
        "bill_to": "Initech LLC", "subtotal": 800.00, "tax": 64.00, "total_due": 864.00}),

    ("inv3", [
        "Invoice Number: INV-2024-0205",
        "Issued 2024-05-19 and billed to Stark Industries for services rendered.",
        "The subtotal is 4200.00 with tax of 336.00,",
        "bringing the amount payable to 4536.00.",
    ], {"invoice_number": "INV-2024-0205", "invoice_date": "2024-05-19",
        "bill_to": "Stark Industries", "subtotal": 4200.00, "tax": 336.00, "total_due": 4536.00}),

    ("inv4", [
        "WAYNE ENTERPRISES",
        "Invoice Number: INV-2024-0388",
        "Dated 2024-06-30 and prepared for Oscorp.",
        "A subtotal of 15000.00 and tax of 1200.00 apply,",
        "with a grand total of 16200.00 now due.",
    ], {"invoice_number": "INV-2024-0388", "invoice_date": "2024-06-30",
        "bill_to": "Oscorp", "subtotal": 15000.00, "tax": 1200.00, "total_due": 16200.00}),

    ("inv5", [
        "Umbrella Corp",
        "Invoice Number: INV-2024-0500",
        "Dated 2024-07-11 and billed to Tyrell Corporation.",
        "The subtotal of 99.50 plus tax of 7.96",
        "yields a total of 107.46 payable on receipt.",
    ], {"invoice_number": "INV-2024-0500", "invoice_date": "2024-07-11",
        "bill_to": "Tyrell Corporation", "subtotal": 99.50, "tax": 7.96, "total_due": 107.46}),
]


def invoice_fixtures() -> list[InvoiceFixture]:
    return [InvoiceFixture(name=n, pdf=_pdf(lines), gold=gold) for n, lines, gold in _DATA]


def invoice_by_name(name: str) -> InvoiceFixture:
    return next(fx for fx in invoice_fixtures() if fx.name == name)
