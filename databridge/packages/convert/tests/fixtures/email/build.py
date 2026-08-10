"""Build the email fixture chain (C14 / Demo D8, 09_FINAL_PRD §11.6).

A realistic ``.eml``: typed headers (from/to/cc/date/subject/message-id), a short text body,
and ONE PDF attachment (an invoice) — so the attachment can re-enter the router and the
provenance chain email→attachment→page→span can be exercised end to end.

Deterministic: fixed Message-ID and Date so snapshots/pointers are reproducible. The PDF is
built with reportlab (already a test dep, used by the C12 fixtures).
"""
from __future__ import annotations

import io
from email.message import EmailMessage

_MESSAGE_ID = "<order-9921@acme.example>"
_DATE = "Fri, 15 Mar 2024 09:30:00 +0000"

# The invoice that lives INSIDE the email — gold values for D8 extraction downstream.
INVOICE_GOLD = {
    "invoice_number": "INV-2024-0042",
    "bill_to": "Acme Industries",
    "total_due": "1350.00",
}


def invoice_pdf_bytes() -> bytes:
    """A TWO-PAGE invoice: header on page 1, totals on page 2 — so the D8 provenance chain's
    PAGE hop must genuinely discriminate which page a field's span falls on (§11.2)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 730, "Invoice Number: INV-2024-0042")
    c.drawString(72, 710, "Date: 2024-03-15")
    c.drawString(72, 690, "Bill To: Acme Industries")
    c.showPage()                                   # --- page break ---
    c.drawString(72, 730, "Subtotal: 1250.00")
    c.drawString(72, 710, "Tax: 100.00")
    c.drawString(72, 690, "Total Due: 1350.00")
    c.showPage()
    c.save()
    return buf.getvalue()


def eml_bytes() -> bytes:
    msg = EmailMessage()
    msg["From"] = "billing@acme.example"
    msg["To"] = "ap@buyer.example, accounts@buyer.example"
    msg["Cc"] = "manager@buyer.example"
    msg["Subject"] = "Invoice INV-2024-0042 for March"
    msg["Date"] = _DATE
    msg["Message-ID"] = _MESSAGE_ID
    msg.set_content(
        "Hello,\n\nPlease find attached invoice INV-2024-0042 for 1350.00 due 2024-03-30.\n"
        "Regards,\nBilling Team\n")
    msg.add_attachment(invoice_pdf_bytes(), maintype="application", subtype="pdf",
                       filename="invoice-0042.pdf")
    return msg.as_bytes()


def eml_with_html_body() -> bytes:
    """An HTML-bodied variant (exercises the html-body → text path)."""
    msg = EmailMessage()
    msg["From"] = "billing@acme.example"
    msg["To"] = "ap@buyer.example"
    msg["Subject"] = "HTML invoice"
    msg["Date"] = _DATE
    msg["Message-ID"] = "<html-9922@acme.example>"
    msg.set_content("plain fallback: invoice INV-2024-0042")
    msg.add_alternative(
        "<html><body><h1>Invoice</h1><p>Invoice INV-2024-0042 total 1350.00</p>"
        "</body></html>", subtype="html")
    return msg.as_bytes()
