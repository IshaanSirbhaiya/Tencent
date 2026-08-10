"""adapters/source/ — document SOURCE adapters (C12/C13/C14): bytes → DocumentRecord."""
from __future__ import annotations

from . import docx, email, html, image_ocr, md, pdf_ocr, pdf_text, pptx, txt  # noqa: F401

__all__ = ["md", "txt", "html", "pdf_text", "docx", "pptx",
           "image_ocr", "pdf_ocr", "email"]
