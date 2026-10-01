"""adapters/source/ — document SOURCE adapters (C12/C13/C14): bytes → DocumentRecord."""
from __future__ import annotations

from . import (docling_extract, docx, email, html, image_ocr, md, pdf_ocr,  # noqa: F401
               pdf_text, pptx, sidecar_producer, txt)

__all__ = ["md", "txt", "html", "pdf_text", "docx", "pptx",
           "image_ocr", "pdf_ocr", "email", "docling_extract", "sidecar_producer"]
