"""ReportIR → DOCX bytes (C18, 09_FINAL_PRD §13.4).

Builds on C12's DOCX **write-path** (``adapters/target/docx.py``): a ReportIR's blocks are the same
``Block`` type a DocumentRecord carries, so the report renders through the existing python-docx
writer (headings / paragraphs / tables), then we **determinise the package**.

A .docx is an OOXML zip. Sources of cross-render / cross-platform byte drift we neutralise:
  * zip member **order** → emit members sorted by name;
  * zip member **mod-time** → fixed ZipInfo ``date_time`` (1980-01-01, the value python-docx itself
    uses) + fixed ``external_attr``;
  * zip **"version made by"** host → ``create_system`` PINNED to 3 (Unix) regardless of OS, else a
    Windows build (0/FAT) and a Linux build (3/Unix) differ byte-for-byte;
  * ``docProps/core.xml`` ``dcterms:created`` / ``dcterms:modified`` timestamps → blanked;
  * ``word/document.xml`` ``w:rsid*`` revision-session attributes (per-edit, non-semantic) → stripped.

Result: **same ReportIR ⇒ byte-identical docx** (§13.4 — the guarantee md and docx share; PDF
byte-identity is best-effort, that's C19). Untrusted text is scrubbed of XML-illegal control chars
before it reaches python-docx (else a control char in extracted text produces invalid OOXML).
"""
from __future__ import annotations

import io
import re
import zipfile

from ..adapters.target.docx import write as _docx_write
from ..ir import Block, DocumentRecord, ReportIR

_FIXED_TIME = (1980, 1, 1, 0, 0, 0)        # the zip epoch python-docx itself uses; pinned here
_UNIX = 3                                  # ZipInfo.create_system = Unix, for cross-platform identity
# blank the *content* of the two timestamp elements (keep the tags so the package stays valid)
_CORE_DATE = re.compile(
    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)")
# per-edit revision-session ids python-docx may emit on runs/paragraphs (non-semantic)
_RSID = re.compile(rb'\s+w:rsid[A-Za-z]*="[^"]*"')
# XML 1.0 illegal control chars (keep TAB \x09, LF \x0a, CR \x0d)
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _scrub(text: str) -> str:
    return _XML_ILLEGAL.sub("", text)


def _scrub_block(b: Block) -> Block:
    """A copy of the block with XML-illegal control chars removed from its text/table cells."""
    rows = [[_scrub(str(c)) for c in row] for row in b.rows] if b.rows else b.rows
    return b.model_copy(update={"text": _scrub(b.text), "rows": rows})


def _report_to_document(report: ReportIR) -> DocumentRecord:
    """A ReportIR viewed as a DocumentRecord for the C12 writer. ``document_id``/``sha256`` are
    deterministic constants (this is a render, not an ingest — provenance lives on the Claims)."""
    blocks = [_scrub_block(b) for b in report.blocks]
    return DocumentRecord(document_id="report", sha256="0" * 64, snapshot=report.snapshot,
                          blocks=blocks)


def _determinise(raw: bytes) -> bytes:
    """Rewrite the OOXML zip deterministically: sorted members, fixed timestamps + host, blanked
    core dates, stripped rsids. Idempotent — determinising an already-determinised package returns
    identical bytes."""
    src = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            data = src.read(name)
            if name == "docProps/core.xml":
                data = _CORE_DATE.sub(rb"\1\2", data)
            elif name == "word/document.xml":
                data = _RSID.sub(b"", data)
            info = zipfile.ZipInfo(filename=name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = _UNIX                  # pin host → cross-platform byte-identity
            info.external_attr = 0o600 << 16
            dst.writestr(info, data)
    return out.getvalue()


def render_docx(report: ReportIR) -> bytes:
    """ReportIR → deterministic .docx bytes. Same ReportIR ⇒ byte-identical output (§13.4)."""
    raw = _docx_write(_report_to_document(report))
    return _determinise(raw)


def render_docx_from_document(doc: DocumentRecord) -> bytes:
    """Determinised .docx straight from a DocumentRecord (the plain document write-path)."""
    scrubbed = doc.model_copy(update={"blocks": [_scrub_block(b) for b in doc.blocks]})
    return _determinise(_docx_write(scrubbed))


__all__ = ["render_docx", "render_docx_from_document"]
