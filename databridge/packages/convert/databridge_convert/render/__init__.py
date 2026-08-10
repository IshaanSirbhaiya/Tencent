"""render/ — ReportIR rendering (C18, 09_FINAL_PRD §13.4 / §12.4).

The S→U **rendering** half (C17 is the generation half): a finalised ReportIR → deterministic
artefacts. Pipeline:

  finalise_report (blocks + FROZEN Claims, spans located in the md render)        §12.4
    → render_markdown   (native; same ReportIR ⇒ byte-identical .md)               §13.4
    → render_docx       (C12 docx write-path + determinised OOXML zip; byte-identical) §13.4
  templates: data_dictionary (over AnnotatedSchema) · generic_report               §12.4

Byte-identity is the contract for md and docx (PDF + xlsx-report are C19). Claims are frozen at
approval (``approval_digest`` / ``verify_frozen``) so post-approval drift is detectable.
"""
from __future__ import annotations

from .docx_renderer import render_docx, render_docx_from_document
from .md_renderer import render_markdown, render_markdown_bytes
from .report_ir import (
    ClaimSpec,
    ReportBuilder,
    approval_digest,
    claims_cited,
    claims_resolve,
    finalise_report,
    verify_frozen,
)
from .templates import data_dictionary, generic_report, grounded

__all__ = [
    "finalise_report", "ReportBuilder", "ClaimSpec",
    "claims_cited", "claims_resolve", "approval_digest", "verify_frozen",
    "render_markdown", "render_markdown_bytes",
    "render_docx", "render_docx_from_document",
    "data_dictionary", "generic_report", "grounded",
]
