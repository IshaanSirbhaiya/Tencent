"""Uploaded files → the one text the record is built from, plus the page regions behind it.

This is the intake side of T-36. `packages/convert` does the parsing (``docling_extract``); this
module decides *what* gets extracted, guarantees it happens **once**, and hangs the page geometry
off the pointers the pipeline produces.

**The invariant this module exists to protect.** Every ``SourcePointer`` the pipeline emits is
``(char_start, char_end)`` into the document text. Extraction must therefore happen exactly once,
server-side, and the text returned here must be *both* what is stored and what pointers resolve
against. Re-extracting later — a second call with different pipeline options, a different docling
version, even a different page order — silently moves every offset, and the symptom is not a
crash: the evidence click-through quietly frames the wrong characters, and the wall starts
refusing values that are genuinely on the page. So callers take ``UploadExtraction.text`` and store
that, and re-runs of the pipeline for the same intake reuse the stored text and the stored index.

**The pointer chain.** ``ground.py`` produces a ``doc_span`` into that text, and
``attach_media_provenance`` adds the child that says where on the original that span sits:

    pointer: doc_span(document_id, char_start, char_end)          ← the extracted text
      └─ child: ocr_bbox(media_id, page, x0,y0,x1,y1, ocr_conf)   ← the region on the scan

The child is attached **only** where the text was genuinely OCR'd — see ``docling_extract``
restriction (2). A PDF's own text layer has geometry but no recognition step, so it has no OCR
confidence, and manufacturing one would be a value without a source wearing a source's clothes.
Pasted text has neither, and gets no child at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from databridge_core import GroundedValue, SourcePointer

from .ground import TICKED_BOX, UNTICKED_BOX


class UnsupportedUpload(Exception):
    """The file is not a kind this system reads. Never a guess — an explicit refusal."""


class ExtractionUnavailable(Exception):
    """The extraction engine cannot run here — not installed, or its models will not load.

    Distinct from ``UnsupportedUpload``: this says the SERVER cannot read PDFs and scans at the
    moment, not that this particular file is unreadable. The gateway answers it with a 503, so a
    cold model cache is never reported to the front desk as a bad document.
    """


# Extensions and media types read as text verbatim. For these the extracted text IS the decoded
# bytes: no re-serialisation, so offsets are identical to what a paste of the same content
# produces. That keeps the file path and the paste path on one set of pointers.
_TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown",
                               "message/rfc822", "application/json"})
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".eml", ".json"})

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class MediaIndex:
    """char span → the page region it was recognised from. Empty for pasted or text-file input.

    Only OCR-derived regions are held: a region without an OCR confidence cannot become an
    ``ocr_bbox`` pointer (the kind requires ``ocr_conf``), and this index exists to build those.
    """

    document_id: str
    regions: tuple = ()          # tuple[TextRegion, ...] from databridge_convert

    def __bool__(self) -> bool:
        return bool(self.regions)

    def child_for(self, char_start: int, char_end: int) -> Optional[SourcePointer]:
        """The ``ocr_bbox`` covering a span of the extracted text, or ``None``.

        A grounded value is usually inside one recognised line, but it can straddle two — a name
        wrapped across a form's line break, say. The union of the overlapping boxes is returned,
        carrying the **weakest** confidence of the lines it covers, because the value is only as
        trustworthy as the worst character in it. Regions on other pages are excluded rather than
        merged: a box spanning two pages is not a region on any page.
        """
        hits = [r for r in self.regions
                if r.from_ocr and r.ocr_conf is not None
                and r.char_start < char_end and char_start < r.char_end]
        if not hits:
            return None
        page = hits[0].page
        hits = [r for r in hits if r.page == page]
        return SourcePointer(
            source_id=self.document_id, kind="ocr_bbox",
            media_id=hits[0].media_id, page=page,
            x0=min(r.x0 for r in hits), y0=min(r.y0 for r in hits),
            x1=max(r.x1 for r in hits), y1=max(r.y1 for r in hits),
            ocr_conf=min(r.ocr_conf for r in hits))


@dataclass(frozen=True)
class UploadExtraction:
    """The single extraction of one uploaded file."""

    text: str
    media: MediaIndex
    media_type: str
    warnings: tuple[str, ...] = ()
    page_count: int = 0
    ocr_pages: tuple[int, ...] = ()
    engine: str = "verbatim"
    meta: dict = field(default_factory=dict)


def _suffix(filename: str) -> str:
    name = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""


def _is_text(filename: str, media_type: str) -> bool:
    return (media_type.split(";")[0].strip().lower() in _TEXT_MEDIA_TYPES
            or _suffix(filename) in _TEXT_SUFFIXES)


def extract_upload(data: bytes, *, document_id: str, filename: str = "",
                   media_type: str = "") -> UploadExtraction:
    """Read one uploaded file into text — **once**.

    Raises ``UnsupportedUpload`` for a file type we do not read and ``ExtractionUnavailable`` when
    the engine is missing. Neither is answered with a best-effort decode: a PDF decoded as latin-1
    would produce a page of binary noise that the grounding wall would then dutifully refuse every
    value against, which reads to the front desk as "this letter says nothing".
    """
    from databridge_convert.adapters._docutil import decode_text
    from databridge_convert.adapters._util import AdapterError
    from databridge_convert.adapters.source import docling_extract
    from databridge_convert.adapters.source.docling_extract import DoclingUnavailable

    if not data:
        raise UnsupportedUpload("the uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsupportedUpload(
            f"the file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")

    sniffed = docling_extract.sniff(data)
    if sniffed is None:
        if not _is_text(filename, media_type):
            raise UnsupportedUpload(
                "this file is neither text, a PDF, nor an image this system reads")
        # Verbatim: the decoded bytes ARE the document text, so a .txt upload and a paste of the
        # same file produce byte-identical pointers.
        return UploadExtraction(text=decode_text(data, label=document_id),
                                media=MediaIndex(document_id=document_id),
                                media_type=media_type or "text/plain")

    if not docling_extract.available():
        raise ExtractionUnavailable(
            "document extraction is not installed on this server, so PDFs and scans cannot be "
            "read here. Paste the document's text instead.")
    try:
        extracted = docling_extract.extract(data, source_id=document_id, media_type=media_type)
    except DoclingUnavailable as exc:
        # `available()` only proves the package imports. The models load on first use, and a cold
        # cache with no network fails there — which is an unavailable server, not a bad document.
        raise ExtractionUnavailable(
            "document extraction could not start on this server, so PDFs and scans cannot be read "
            "here right now. Paste the document's text instead.") from exc
    except AdapterError as exc:
        # A FIXED message, not `str(exc)`. The engine's error text can quote the bytes it choked
        # on, and this exception is answered straight to the caller — the same rule the gateway
        # applies to every other failure on this path. The detail is chained for the server log.
        raise UnsupportedUpload("this file could not be read as a PDF or an image") from exc

    warnings = list(extracted.warnings)
    if extracted.ocr_pages and TICKED_BOX not in extracted.text \
            and UNTICKED_BOX not in extracted.text:
        # The checkbox state IS the package signal on the authorisation forms and the wellness
        # voucher — `ground.mask_unticked_options` blanks the unticked lines so the wall cannot
        # confirm an option the form declined. OCR does not reliably recognise ☒/☐ (the bundled
        # engine drops them entirely), and when they are gone the masking silently becomes a
        # no-op: every declined option is back on the page as far as the wall is concerned, and
        # the wrong examination can be authorised and billed. We cannot tell "this scan had no
        # checkboxes" from "this scan's checkboxes were lost", so we say so rather than guess.
        warnings.append(
            "checkbox marks (☒/☐) were not recovered from this scan; if the document is "
            "a tick-box form, any package or test selection must be checked against the original")
    return UploadExtraction(
        text=extracted.text,
        media=MediaIndex(document_id=document_id, regions=extracted.regions),
        media_type=sniffed,
        warnings=tuple(warnings),
        page_count=extracted.page_count,
        ocr_pages=extracted.ocr_pages,
        engine=extracted.engine,
        meta={"loss_report": extracted.document.source_meta.get("loss_report", []),
              "media": extracted.document.source_meta.get("media", {})})


# --- hanging the page region off the pointers the pipeline produces ---------------
def attach_media_provenance(value: Optional[GroundedValue],
                            media: Optional[MediaIndex]) -> Optional[GroundedValue]:
    """Return ``value`` with an ``ocr_bbox`` child on every ``doc_span`` pointer it carries.

    Walks the pointer tree rather than just the top pointer: a classified field's pointer is
    ``derived`` and cites its evidence through ``derived_from``, so the span that needs the child
    is one level down. Missing that produced records where the quoted fields could be traced to the
    scan and the derived ones could not, for no reason a reader could see.

    Existing children are never replaced — a pointer that already names its origin is not improved
    by a second opinion.
    """
    if value is None or not media:
        return value
    pointer = _with_children(value.pointer, media)
    if pointer is value.pointer:
        return value
    return value.model_copy(update={"pointer": pointer})


def _with_children(pointer: SourcePointer, media: MediaIndex) -> SourcePointer:
    if pointer.kind == "doc_span" and pointer.child is None \
            and pointer.char_start is not None and pointer.char_end is not None:
        child = media.child_for(pointer.char_start, pointer.char_end)
        if child is not None:
            return pointer.model_copy(update={"child": child})
        return pointer
    if pointer.derived_from:
        rebuilt = [_with_children(p, media) for p in pointer.derived_from]
        if any(a is not b for a, b in zip(rebuilt, pointer.derived_from)):
            return pointer.model_copy(update={"derived_from": rebuilt})
    return pointer
