"""Email fixtures — an .eml with a PDF invoice attachment (C14 / Demo D8, §11.6).

Deterministic (fixed Message-ID + Date) so the provenance chain email→attachment→page→span
is reproducible. See ``databridge-brain/50-research/fixtures.md``."""
from __future__ import annotations

from .build import INVOICE_GOLD, eml_bytes, eml_with_html_body, invoice_pdf_bytes

__all__ = ["INVOICE_GOLD", "eml_bytes", "eml_with_html_body", "invoice_pdf_bytes"]
