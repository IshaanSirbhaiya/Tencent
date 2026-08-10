"""Document-adapter registration (C12) — wires each format's source read + target write
into the ONE registry, so a format is registered exactly once with both sides.

Per §5.2 R/W: markdown/txt/html/docx are R+W; pdf (text) is R here (controlled write is the
§13.4 render path, C19); pptx is R-only (lossy)."""
from __future__ import annotations

from ..router.registry import AdapterInfo, register_adapter
from .source import docx as s_docx
from .source import email as s_email
from .source import html as s_html
from .source import image_ocr as s_image
from .source import md as s_md
from .source import pdf_text as s_pdf
from .source import pptx as s_pptx
from .source import txt as s_txt
from .target import docx as t_docx
from .target import html as t_html
from .target import md as t_md
from .target import txt as t_txt

_DOC = "DocumentRecord"

register_adapter(AdapterInfo(
    format="markdown", modality="document", ir=_DOC, lossiness="lossless",
    limits=["the document IR's native serialisation (near-identity)"],
    read=s_md.read, write=t_md.write))

register_adapter(AdapterInfo(
    format="txt", modality="document", ir=_DOC, lossiness="lossless",
    limits=["encoding-sniffed; blank-line-separated paragraphs"],
    read=s_txt.read, write=t_txt.write))

register_adapter(AdapterInfo(
    format="html", modality="document", ir=_DOC, lossiness="lossy",
    limits=["readability main-content extraction (full-DOM via flag); scripts/styles/media "
            "dropped", "lxml no-network parsing (§21.8)"],
    read=s_html.read, write=t_html.write))

register_adapter(AdapterInfo(
    format="pdf", modality="document", ir=_DOC, lossiness="lossy",
    limits=["TEXT PDFs only (scanned → OCR, C13); layout simplified to reading order; "
            "images/forms dropped", "write is the controlled §13.4 render path (C19)"],
    read=s_pdf.read, write=None))

register_adapter(AdapterInfo(
    format="docx", modality="document", ir=_DOC, lossiness="lossy",
    limits=["tracked changes flattened to accepted state; images/styles/headers dropped"],
    read=s_docx.read, write=t_docx.write))

register_adapter(AdapterInfo(
    format="pptx", modality="document", ir=_DOC, lossiness="lossy",
    limits=["READ-ONLY; slide→section reading-order heuristic; animations/media dropped"],
    read=s_pptx.read, write=None))

register_adapter(AdapterInfo(
    format="image", modality="document", ir=_DOC, lossiness="lossy",
    limits=["OCR (RapidOCR→Tesseract, §11.3); text reconstructed from raster, layout/figures "
            "dropped; bbox provenance retained (ocr_bbox)", "READ-ONLY scanned source"],
    read=s_image.read, write=None))

register_adapter(AdapterInfo(
    format="eml", modality="document", ir=_DOC, lossiness="lossy",
    limits=["body + typed headers; attachments fan out as their OWN sources (§11.6); "
            "remote images/scripts dropped", "READ-ONLY"],
    read=s_email.read, write=None))
