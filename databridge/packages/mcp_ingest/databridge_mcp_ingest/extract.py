"""Bytes → text. The extractor for a type is chosen (and its existence checked) BEFORE the bytes
are read (P10) — `extractor_for` answers from the extension/mime alone, so collection can exclude
a type nobody can read without fetching it, and extraction can record a skip without parsing.

  text      .txt .md .csv .tsv .json .yaml .log .xml .html …   decoded UTF-8 (BOM-aware; cp1252
                                                                fallback, recorded)
  pdf       .pdf and images                                     packages/convert docling_extract
                                                                (text layer + OCR, page-ordered)
  office    .docx .pptx .xlsx                                   docling DocumentConverter → Markdown

docling is optional. When it is missing the type is still RECOGNISED, and extraction records
`extractor_unavailable` — distinct from `no_extractor`, because the fix is different.
"""
from __future__ import annotations

import hashlib
import html
import importlib.util
import mimetypes
import os
from dataclasses import dataclass
from typing import Optional

TEXT_EXT = frozenset({".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml",
                      ".yml", ".log", ".xml", ".html", ".htm", ".rst", ".ini", ".cfg", ".toml",
                      ".sql", ".eml"})
PDF_EXT = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})
OFFICE_EXT = frozenset({".docx", ".pptx", ".xlsx"})

_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/json": ".json", "text/csv": ".csv", "text/markdown": ".md",
    "text/plain": ".txt", "text/html": ".html", "image/png": ".png", "image/jpeg": ".jpg",
}


class ExtractionFailed(RuntimeError):
    pass


def text_digest(text: str) -> str:
    """sha256 of the extracted text's UTF-8 bytes — what `<capture>/<sha>.txt` must hash to. Recorded
    at extraction so a PDF/Office capture's text can be verified later without re-running docling
    (R-021-materialise round 2)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Extracted:
    text: str
    extractor: str
    warnings: tuple[str, ...] = ()
    text_sha256: str = ""
    """`text_digest(text)`, always — computed here, never taken from a caller."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "text_sha256", text_digest(self.text))


def ext_of(name_or_path: str) -> str:
    base = name_or_path.replace("\\", "/").rsplit("/", 1)[-1]
    return os.path.splitext(base)[1].lower()


def ext_of_mime(mime: Optional[str]) -> str:
    """The extension a declared mime stands for ("" when unknown) — for items whose NAME carries
    no extension (a Google Doc titled "Kick-off notes", captured as text/markdown)."""
    if not mime:
        return ""
    m = mime.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(m) or (mimetypes.guess_extension(m) or "")


def guess_mime(name_or_path: str, declared: Optional[str] = None) -> str:
    if declared and declared != "application/octet-stream":
        return declared.split(";")[0].strip().lower()
    ext = ext_of(name_or_path)
    for mime, e in _MIME_TO_EXT.items():
        if e == ext:
            return mime
    guess, _ = mimetypes.guess_type("x" + ext)
    return guess or "application/octet-stream"


def docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


def extractor_for(name_or_path: str, mime: Optional[str] = None) -> Optional[str]:
    """`text` | `pdf` | `office`, or None when no extractor exists for this type."""
    def by_ext(ext: str) -> Optional[str]:
        if ext in TEXT_EXT:
            return "text"
        if ext in PDF_EXT:
            return "pdf"
        if ext in OFFICE_EXT:
            return "office"
        return None

    found = by_ext(ext_of(name_or_path))
    if found is None and mime:
        m = mime.split(";")[0].strip().lower()
        found = by_ext(_MIME_TO_EXT.get(m, "")) or ("text" if m.startswith("text/") else None)
    return found


def _decode(data: bytes) -> tuple[str, tuple[str, ...]]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), ()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), ()
    try:
        return data.decode("utf-8"), ()
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), ("not UTF-8; decoded as cp1252",)


def extract_text(data: bytes, *, name: str, mime: Optional[str] = None,
                 source_id: str = "capture") -> Extracted:
    kind = extractor_for(name, mime)
    if kind is None:
        raise ExtractionFailed(f"no extractor for {name!r} ({mime})")
    if kind == "text":
        text, warnings = _decode(data)
        # R-022 #13: judged on the DECODED text — a BOM-marked UTF-16 file is full of zero bytes
        # and is still text; a NUL character after decoding is binary content
        if "\x00" in text[:4096]:
            raise ExtractionFailed("binary content in a text-typed item")
        return Extracted(text=text, extractor="text", warnings=warnings)
    if not docling_available():
        raise ExtractionFailed("extractor_unavailable: docling is not installed")
    if kind == "pdf":
        from databridge_convert.adapters.source import docling_extract

        try:
            doc = docling_extract.extract(data, source_id=source_id,
                                          media_type=guess_mime(name, mime))
        except Exception as exc:                                     # noqa: BLE001
            raise ExtractionFailed(f"docling could not read this file: {exc}") from exc
        return Extracted(text=doc.text, extractor=f"docling:{doc.engine}",
                         warnings=tuple(doc.warnings))
    # office documents: docling's generic converter, exported as Markdown
    import io

    try:
        from docling.datamodel.base_models import DocumentStream

        # R-022 #8: an extensionless item declared by mime still gets the suffix docling needs
        stream = DocumentStream(name=f"item{ext_of(name) or ext_of_mime(mime)}",
                                stream=io.BytesIO(data))
        result = _office_converter().convert(stream)
        # the Markdown export escapes `&` and friends; the stored text is what pointers index,
        # so it holds the characters the document holds
        text = html.unescape(result.document.export_to_markdown())
    except Exception as exc:                                         # noqa: BLE001
        raise ExtractionFailed(f"docling could not read this file: {exc}") from exc
    return Extracted(text=text, extractor="docling:office")


#: Bump when the conversion itself changes (e.g. the Markdown post-processing above).
#: 2: records carry `text_sha256`, checked on read.
CONVERT_CACHE_VERSION = 2


def _docling_version() -> str:
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:                                                # noqa: BLE001
        return "unknown"


def convert_cache_path(sha: str, kind: str) -> "os.PathLike":
    """`<cache_root>/convert/<sha[:2]>/<sha>.<kind>.json`. Keyed by the CONTENT hash of the bytes,
    so the same file at a second path, in a second source or in a fresh run converts once."""
    from .capture import cache_root

    return cache_root() / "convert" / sha[:2] / f"{sha}.{kind}.json"


def extract_text_cached(data: bytes, *, sha: str, name: str, mime: Optional[str] = None,
                        source_id: str = "capture") -> tuple[Extracted, bool]:
    """`extract_text`, with docling conversions (the slow ones: PDF/images/Office) cached by
    content hash + docling version + CONVERT_CACHE_VERSION. Returns (result, cache_hit). Plain
    text is decoded every time — it costs less than reading the cache would. A failure is never
    cached, so a fixed extractor is retried on the next run."""
    import json

    kind = extractor_for(name, mime)
    if kind not in ("pdf", "office"):
        return extract_text(data, name=name, mime=mime, source_id=source_id), False
    path = convert_cache_path(sha, kind)
    stamp = {"v": CONVERT_CACHE_VERSION, "docling": _docling_version(), "kind": kind}
    if path.is_file():
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if (rec.get("stamp") == stamp and isinstance(rec.get("text"), str)
                    and rec.get("text_sha256") == text_digest(rec["text"])):
                return Extracted(text=rec["text"], extractor=rec["extractor"],
                                 warnings=tuple(rec.get("warnings") or ())), True
        except (OSError, ValueError, KeyError):
            pass
    got = extract_text(data, name=name, mime=mime, source_id=source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # R-022 f2 #6: a temp name unique per WRITER (process, thread and a nonce) — two workers
    # converting the same bytes at once each write their own file; the last rename wins, and both
    # renames carry identical content (same bytes, same converter)
    import secrets
    import threading

    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}."
                         f"{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"stamp": stamp, "sha256": sha, "text": got.text,
                               "extractor": got.extractor, "warnings": list(got.warnings),
                               "text_sha256": got.text_sha256}),
                   encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        # Windows refuses to replace a file another thread is renaming onto that moment; the
        # cache entry is then the other writer's identical one. The result in hand is returned.
        tmp.unlink(missing_ok=True)
    return got, False


_OFFICE = None


def _office_converter():
    global _OFFICE
    if _OFFICE is None:
        from docling.document_converter import DocumentConverter

        _OFFICE = DocumentConverter()
    return _OFFICE


__all__ = ["extractor_for", "extract_text", "extract_text_cached", "convert_cache_path",
           "Extracted", "ExtractionFailed", "guess_mime", "ext_of", "ext_of_mime",
           "docling_available", "text_digest"]
