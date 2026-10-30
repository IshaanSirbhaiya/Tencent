"""File type → plain text, through Walnut's EXISTING convert readers only (``packages/convert``).

A thin dispatcher used twice: by ``POST /sim/upload-file`` (to refuse a file that is not what its
extension says, or that cannot be read, BEFORE it is put in Drive) and by the Drive connector (to
index a Word / Excel / PowerPoint / PDF / CSV file it fetches, and to tidy a WhatsApp chat export).
No document format is parsed here: every format is read by its reader in ``databridge_convert`` and
the reader's output is flattened to text. The one parser of our own is the WhatsApp line format,
which is plain text.

| kind | reader (``databridge_convert``)        | flattened as                                 |
|------|----------------------------------------|----------------------------------------------|
| docx | ``adapters/source/docx.read``          | the reader's canonical Markdown (headings, paragraphs, tables) |
| pptx | ``adapters/source/pptx.read``          | the same (one ``## <slide title>`` heading per slide, then its text) |
| pdf  | ``adapters/source/pdf_text.read``      | the same (one paragraph per text line)       |
| md   | ``adapters/source/md.read``            | the same                                     |
| txt  | ``adapters/source/txt.read``           | the same; a WhatsApp export is first rewritten by ``whatsapp_text`` |
| xlsx | ``adapters/excel.read_xlsx``           | ``Sheet: <name>`` then ``Row N: <header>=<value>, ...`` |
| csv  | ``adapters/csv.read``                  | ``Row N: <column>=<value>, ...``             |

Safety, all fail closed (``Refused``; the reason is for the ledger only):

* the bytes must match the kind: OOXML is a zip (``PK``) holding ``[Content_Types].xml`` and its
  own part folder (``word/``, ``xl/`` or ``ppt/``); a PDF starts ``%PDF-``; text is strict UTF-8
  with no NUL byte;
* at most ``MAX_FILE_BYTES`` (5 MiB);
* zip bombs: at most ``ZIP_MAX_ENTRIES`` entries, ``ZIP_MAX_TOTAL`` bytes uncompressed in total
  (checked on the declared sizes AND by streaming every member, so a member that lies about its
  size is caught), and a per-entry compression ratio of at most ``ZIP_MAX_RATIO`` for any entry
  larger than ``ZIP_RATIO_FLOOR``; encrypted members and absolute / ``..`` member names are refused;
* XML: an OOXML part that declares a DTD or an entity (``<!DOCTYPE``, ``<!ENTITY``) is refused, so
  no reader ever sees an external entity (XXE) or an entity-expansion bomb. OOXML never needs one;
* macros: any ``vbaProject.bin`` member, or a macro-enabled content type, is refused (macro-enabled
  extensions are not on the whitelist at all); nothing is ever executed;
* PDF: at most ``PDF_MAX_PAGES`` pages;
* .xlsx extent (round 4 Codex B11): both readers build a DENSE grid (calamine's range and
  ``to_python()``, openpyxl's ``iter_rows`` and its merged, hyperlink and comment cells), so a
  small sparse file with two far-apart cells would expand to billions of cells. Before any reader
  runs, ``xlsx_extent`` measures max_row x max_column of every sheet from the XML itself (cell and
  row references, implicit positions, ``dimension``, ``mergeCell``, ``hyperlink`` and ``comment``
  refs) and a workbook over ``XLSX_MAX_CELLS`` is refused (``xlsx_dimensions``). The worker's
  time budget below stays as the backstop;
* a time budget (``TIME_BUDGET_S``): every reader runs in a separate WORKER PROCESS (``spawn``, one
  at a time); a read that has not finished within the budget is refused and the worker process is
  KILLED, so a runaway parse cannot keep using CPU or memory in the Internal Brain. A crashed
  worker is the same refusal. The readers are imported when the worker starts, outside the budget;
* the text is NFC-normalised, control and bidi-override characters (other than newline and tab)
  are removed, HTML tags are stripped, and it is capped at ``MAX_TEXT_CHARS``. Masking (cards,
  NRIC, IBAN) is NOT done here: the ingest path masks every document (``sensitive.mask_doc``)
  before indexing.

``packages/convert`` is not on the Internal Brain's ``PYTHONPATH`` (``scripts/demo_up.sh``), so this
module appends it to ``sys.path`` itself (after every other entry, so it can shadow nothing). When
a reader (or one of its libraries) cannot be imported, that format is refused as ``unavailable``
(``available_kinds()``). ``warm()`` starts the worker ahead of the first upload.
"""
from __future__ import annotations

import atexit
import io
import multiprocessing
import re
import sys
import threading
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
TIME_BUDGET_S = 15.0
WORKER_START_S = 120.0          # a fresh worker imports the readers (about 10 s); not in the budget
QUEUE_WAIT_S = 60.0             # waiting for the one worker to be free; not in the budget
ZIP_MAX_ENTRIES = 2_000
ZIP_MAX_TOTAL = 64 * 1024 * 1024
ZIP_MAX_RATIO = 100
ZIP_RATIO_FLOOR = 256 * 1024
PDF_MAX_PAGES = 50
MAX_ROWS_PER_TABLE = 5_000
XLSX_MAX_CELLS = 1_000_000      # sum over sheets of max_row x max_column (round 4 Codex B11)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"

#: extension → kind (the whitelist)
EXT_KIND = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".pdf": "pdf", ".csv": "csv",
            ".md": "md", ".txt": "txt"}
#: kind → the Drive mimeType the file is stored under
KIND_MIME = {"docx": DOCX_MIME, "xlsx": XLSX_MIME, "pptx": PPTX_MIME, "pdf": PDF_MIME,
             "csv": "text/csv", "md": "text/markdown", "txt": "text/plain"}
#: mimeTypes the Drive connector downloads and extracts with this module (other text mimeTypes it
#: reads as text). text/csv is here because a raw CSV line is not answerable ("Row N: col=value" is)
EXTRACT_MIME_KIND = {DOCX_MIME: "docx", XLSX_MIME: "xlsx", PPTX_MIME: "pptx", PDF_MIME: "pdf",
                     "text/csv": "csv"}
BINARY_MIME_KIND = {m: k for m, k in EXTRACT_MIME_KIND.items() if k not in ("csv",)}
TEXT_KINDS = frozenset({"csv", "md", "txt"})
_OOXML_PART = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
#: an HTML / XML tag (a lone "<" in "a < b" is left alone). Comments are stripped by
#: ``_strip_comments`` in one linear pass (round 3 L6: a lazy ``<!--.{0,2000}?-->`` regex took
#: ~25 s on 5 MiB of unclosed ``<!--``)
_TAG = re.compile(r"</?[A-Za-z][^<>]{0,2000}>")
_COMMENT_MAX = 2000
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")
_DTD = re.compile(rb"<!\s*(doctype|entity)", re.I)
#: round 3 L2: the XML encodings a part may use. UTF-16/32 parts are scanned with their NUL bytes
#: removed (so an ASCII ``<!DOCTYPE`` is found in any byte order); any other declared encoding
#: (UTF-7, EBCDIC, Latin-1 ...) could hide a DTD from a byte scan, so it is refused.
_XML_DECL_ENC = re.compile(rb"^<\?xml[^>]{0,200}?encoding\s*=\s*[\"']([A-Za-z0-9._\-]{1,40})[\"']", re.I)
_XML_OK_ENC = frozenset({b"utf-8", b"utf8", b"utf-16", b"utf16", b"utf-16le", b"utf-16be",
                         b"utf-32", b"utf-32le", b"utf-32be", b"ucs-2", b"ucs-4", b"iso-10646-ucs-2",
                         b"iso-10646-ucs-4"})
_BOMS = (b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00", b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_EBCDIC_XML = b"\x4c\x6f\xa7\x94"                  # "<?xm" in EBCDIC (libxml2 autodetects it)
_CONVERT = Path(__file__).resolve().parents[3] / "packages" / "convert"


class Refused(Exception):
    """The file is refused. ``reason`` is for the ledger only, never for the uploader."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

    def __reduce__(self):                                  # crosses the worker process boundary
        return (Refused, (self.reason,))


def kind_for_filename(name: str) -> Optional[str]:
    base = re.split(r"[\\/]", name or "")[-1]
    dot = base.rfind(".")
    return EXT_KIND.get(base[dot:].lower()) if dot > 0 else None


# ------------------------------------------------------------------------------------------
# byte-level checks (cheap; no reader involved; run in the Internal Brain process)
# ------------------------------------------------------------------------------------------
_XML_NAME = (".xml", ".rels", ".vml")


def _xml_head(head: bytes) -> bool:
    """Whether a member's first bytes can start an XML document in any encoding libxml2 detects:
    after a BOM, NUL bytes (UTF-16/32) and whitespace, a ``<``; or EBCDIC ``<?xm``. A member that
    does not start like this is not well-formed XML, so no (non-recovering) XML reader parses it.
    Refuses a declared encoding the DTD scan cannot see through (``xml_encoding``)."""
    if head.startswith(_EBCDIC_XML):
        raise Refused("xml_encoding")
    h = head
    for bom in _BOMS:
        if h.startswith(bom):
            h = h[len(bom):]
            break
    h = h.replace(b"\x00", b"").lstrip()
    m = _XML_DECL_ENC.match(h)
    if m and m.group(1).lower() not in _XML_OK_ENC:
        raise Refused("xml_encoding")
    return h.startswith(b"<")


def _scan_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, running: int) -> int:
    """Stream one member (its declared size can lie) and refuse a DTD / entity in an XML-bearing
    member. Round 3 L2: XML-bearing is decided by the name (``.xml``, ``.rels``, ``.vml``) OR by
    the first bytes (``_xml_head``), so a main part renamed ``.dat`` is still scanned; the scan
    runs on the bytes and again with NUL bytes removed (a UTF-16 or UTF-32 part, with or without
    a BOM). Binary members (images, printer settings) are counted but not DTD-scanned. Returns
    the running uncompressed total."""
    xml = info.filename.lower().endswith(_XML_NAME)
    tail = b""
    first = True
    try:
        with z.open(info) as fh:
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                running += len(chunk)
                if running > ZIP_MAX_TOTAL:
                    raise Refused("zip_bomb_actual")
                if first:
                    xml = _xml_head(chunk[:512]) or xml
                    first = False
                if xml:
                    buf = tail + chunk
                    if _DTD.search(buf) or _DTD.search(buf.replace(b"\x00", b"")):
                        raise Refused("xml_dtd")
                    tail = chunk[-128:]
    except Refused:
        raise
    except Exception:                                      # noqa: BLE001 — corrupt member
        raise Refused("bad_zip") from None
    return running


def _check_zip(kind: str, data: bytes) -> None:
    if data[:4] != b"PK\x03\x04":
        raise Refused("magic_mismatch")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise Refused("bad_zip") from None
    with z:
        infos = z.infolist()
        if not infos or len(infos) > ZIP_MAX_ENTRIES:
            raise Refused("zip_entries")
        names = [i.filename for i in infos]
        if "[Content_Types].xml" not in names:
            raise Refused("not_ooxml")
        if not any(n.startswith(_OOXML_PART[kind]) for n in names):
            raise Refused("magic_mismatch")             # e.g. a .xlsx that is really a .docx
        total = 0
        for i in infos:
            n = i.filename.replace("\\", "/")
            if n.startswith("/") or ".." in n.split("/") or ":" in n:
                raise Refused("zip_member_name")
            if n.lower().rsplit("/", 1)[-1] == "vbaproject.bin":
                raise Refused("macros")
            if i.flag_bits & 0x1:
                raise Refused("zip_encrypted")
            total += i.file_size
            if total > ZIP_MAX_TOTAL:
                raise Refused("zip_bomb_total")
            if i.file_size > ZIP_RATIO_FLOOR and i.file_size > ZIP_MAX_RATIO * max(i.compress_size, 1):
                raise Refused("zip_bomb_ratio")
        try:
            ct = z.read("[Content_Types].xml").decode("utf-8", "replace").lower()
        except Exception:                                # noqa: BLE001
            raise Refused("bad_zip") from None
        if "macroenabled" in ct or "vbaproject" in ct:
            raise Refused("macros")
        actual = 0
        for i in infos:
            actual = _scan_member(z, i, actual)


def check_bytes(kind: str, data: bytes) -> None:
    """Refuse bytes that do not match ``kind`` or break a limit. Never parses the content."""
    if kind not in KIND_MIME:
        raise Refused("type")
    if not data:
        raise Refused("empty")
    if len(data) > MAX_FILE_BYTES:
        raise Refused("too_large")
    if kind in _OOXML_PART:
        _check_zip(kind, data)
    elif kind == "pdf":
        if data[:5] != b"%PDF-":
            raise Refused("magic_mismatch")
    else:
        if b"\x00" in data:
            raise Refused("magic_mismatch")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused("not_utf8") from None


# ------------------------------------------------------------------------------------------
# text: cleaning and the WhatsApp chat export (pure functions)
# ------------------------------------------------------------------------------------------
def _strip_comments(text: str) -> str:
    """``<!-- ... -->`` out (body up to 2,000 characters, as before), in one linear pass: the
    closing ``-->`` is searched once per position it can serve, and when none is left the scan
    stops (round 3 L6)."""
    if "<!--" not in text:
        return text
    out, pos, close = [], 0, -1
    n = len(text)
    while pos < n:
        i = text.find("<!--", pos)
        if i < 0:
            break
        if close < i + 4:
            close = text.find("-->", i + 4)
            if close < 0:
                break                                  # no comment after here can close
        if close - (i + 4) <= _COMMENT_MAX:
            out.append(text[pos:i])
            pos = close + 3
        else:
            out.append(text[pos:i + 1])                # too long: leave this "<", go on
            pos = i + 1
    out.append(text[pos:])
    return "".join(out)


def clean_text(text: str) -> str:
    """NFC, control / bidi characters out, HTML tags out, whitespace tidied, capped."""
    text = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL.sub("", text.replace(" ", " ").replace(" ", " "))
    text = _TAG.sub("", _strip_comments(text))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_TEXT_CHARS]


_D = r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})"
_T = r"(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?\s?[Mm]\.?)?)"
#: Android: ``24/10/2026, 02:13 - Name: message``; iOS: ``[24/10/26, 2:13:05 AM] Name: message``
_WA_ANDROID = re.compile(rf"^{_D},?\s{_T}\s[-–]\s(.*)$")
_WA_IOS = re.compile(rf"^\[{_D},?\s{_T}\]\s(.*)$")
_WA_SENDER = re.compile(r"^([^:\n]{1,60}?):\s(.*)$", re.S)
_WA_DROP = re.compile(r"^(<media omitted>|<attached: [^>]*>|(image|video|audio|sticker|gif|document)"
                      r" omitted|this message was deleted|you deleted this message|null)$", re.I)
WA_MAX_LINES = 200_000


def _wa_header(line: str):
    return _WA_ANDROID.match(line) or _WA_IOS.match(line)


def is_whatsapp(text: str) -> bool:
    """A WhatsApp "Export chat" text: its first non-empty line and at least one more line start
    with the export's date-time header (Android or iOS form)."""
    lines = [ln for ln in clean_text(text[:20_000]).split("\n") if ln.strip()]
    return bool(lines) and bool(_wa_header(lines[0])) and sum(1 for ln in lines if _wa_header(ln)) >= 2


def whatsapp_text(text: str) -> Optional[str]:
    """A WhatsApp chat export rewritten as one paragraph per message
    (``<date> <time>, <sender>: <message>``) under a one-line summary; None when ``text`` is not
    one. Dates are kept as written (day/month order is the phone's, never guessed). System lines
    (encryption notice, "created group", joins) and media placeholders are dropped; a message's
    continuation lines are joined to it."""
    if not is_whatsapp(text):
        return None
    msgs: list[list[str]] = []                         # [date, time, sender, message]
    for line in clean_text(text).split("\n")[:WA_MAX_LINES]:
        h = _wa_header(line)
        if h:
            date, time_, rest = h.group(1), re.sub(r"\s+", " ", h.group(2)), h.group(3).strip()
            s = _WA_SENDER.match(rest)
            if s is None:                              # a system line
                msgs.append([date, time_, "", ""])
                continue
            msgs.append([date, time_, s.group(1).strip(), s.group(2).strip()])
        elif msgs and line.strip() and msgs[-1][2]:
            msgs[-1][3] = f"{msgs[-1][3]} {line.strip()}".strip()
    kept = [m for m in msgs if m[2] and m[3] and not _WA_DROP.match(m[3])]
    if not kept:
        return None
    people = sorted({m[2] for m in kept})
    head = (f"WhatsApp chat export: {len(kept)} messages from {', '.join(people)}, "
            f"{kept[0][0]} to {kept[-1][0]}.")
    return "\n\n".join([head] + [f"{d} {t}, {who}: {msg}" for d, t, who, msg in kept])


# ------------------------------------------------------------------------------------------
# readers: run in the worker process only
# ------------------------------------------------------------------------------------------
#: kind → (module, attribute) of its convert reader; "_md" is the Markdown serialiser and "_pdfium"
#: the PDF library (page count). Each is imported on its own, so a missing library switches off
#: only the formats that need it (``adapters/source/__init__`` imports every document reader, so
#: docx / pptx / pdf / md / txt share its libraries: python-docx, python-pptx, pypdfium2).
_READER_SPECS = {
    "csv": ("databridge_convert.adapters.csv", "read"),
    "xlsx": ("databridge_convert.adapters.excel", "read_xlsx"),
    "docx": ("databridge_convert.adapters.source.docx", "read"),
    "pptx": ("databridge_convert.adapters.source.pptx", "read"),
    "pdf": ("databridge_convert.adapters.source.pdf_text", "read"),
    "md": ("databridge_convert.adapters.source.md", "read"),
    "txt": ("databridge_convert.adapters.source.txt", "read"),
    "_md": ("databridge_convert.adapters._docutil", "markdown_serialize"),
    "_pdfium": ("pypdfium2", None),
}
_NEEDS = {"docx": ("_md",), "pptx": ("_md",), "md": ("_md",), "txt": ("_md",),
          "pdf": ("_md", "_pdfium")}
_READERS: dict[str, Any] = {}
_MISSING: set[str] = set()


def _ensure_path() -> None:
    p = str(_CONVERT)
    if _CONVERT.is_dir() and p not in sys.path:
        sys.path.append(p)                                 # last: it can shadow nothing


def _load(name: str) -> Any:
    if name not in _READERS and name not in _MISSING:
        _ensure_path()
        mod, attr = _READER_SPECS[name]
        try:
            import importlib
            m = importlib.import_module(mod)
            _READERS[name] = getattr(m, attr) if attr else m
        except Exception:                                  # noqa: BLE001 — a missing library
            _MISSING.add(name)
    if name in _MISSING:
        raise Refused("unavailable")
    return _READERS[name]


def _readers_for(kind: str) -> dict[str, Any]:
    """The reader for ``kind`` and the helpers it needs; ``Refused("unavailable")`` if any is
    missing here."""
    return {n: _load(n) for n in (kind,) + _NEEDS.get(kind, ())}


def _available() -> list[str]:
    out = []
    for kind in KIND_MIME:
        try:
            _readers_for(kind)
            out.append(kind)
        except Refused:
            pass
    return out


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e17:
        return str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v).strip()


def _rows(header: list[str], rows: list[list[Any]], start: int = 1) -> list[str]:
    out = []
    wide = sum(1 for h in header if h) >= 2
    for n, row in enumerate(rows[:MAX_ROWS_PER_TABLE], start=start):
        values = [_cell(v) for v in row if _cell(v)]
        if wide and len(values) == 1:                      # a note line under a table
            out.append(values[0])
            continue
        pairs = [f"{h or f'col{j + 1}'}={_cell(v)}" for j, (h, v) in
                 enumerate(zip(header + [""] * max(0, len(row) - len(header)), row)) if _cell(v)]
        if pairs:
            out.append(f"Row {n}: " + ", ".join(pairs))
    return out


def _flatten(kind: str, rec: Any, r: dict[str, Any]) -> str:
    if kind == "xlsx":
        lines: list[str] = []
        for sheet in rec.sheets:
            cells = [list(row) for row in sheet.cells]
            lines.append(f"Sheet: {sheet.name}")
            # the header row is the reader's own guess (excelsub header inference); a guess with
            # fewer than two labels falls back to the first row that has two. Rows above it (a
            # title, a "prepared by" note) are kept as plain lines.
            h = getattr(getattr(sheet, "header_inference", None), "row_idx", 0) or 0
            if not (0 <= h < len(cells)) or sum(1 for v in cells[h] if _cell(v)) < 2:
                h = next((i for i, r in enumerate(cells) if sum(1 for v in r if _cell(v)) >= 2), 0)
            for row in cells[:h]:
                text = " ".join(_cell(v) for v in row if _cell(v))
                if text:
                    lines.append(text)
            if cells:
                body = [r for r in cells[h + 1:] if any(_cell(v) for v in r)]
                lines.extend(_rows([_cell(x) for x in cells[h]], body))
            lines.append("")
        return "\n".join(lines)
    if kind == "csv":
        cols = list(rec.columns)
        return "\n".join(_rows(cols, [[rec_row.get(c) for c in cols] for rec_row in rec.records()]))
    text, _ = r["_md"](list(rec.blocks))
    return text


#: the elements whose position or ``ref`` makes a reader materialise cells, by LOCAL name in any
#: namespace (a chart's ``<c:chart>`` has the local name ``chart`` and never matches ``c``)
_XL_TAGS = frozenset({"row", "c", "dimension", "mergeCell", "hyperlink", "comment"})
_XL_CELL = re.compile(r"^\$?([A-Za-z]{0,3})\$?([0-9]{0,7})$")
_XL_ABSURD = 10 ** 18                               # an unparseable or out-of-range reference


def _xl_point(ref: str) -> tuple[int, int]:
    """``(row, col)`` of ``B12`` / ``$B$12`` / ``B`` / ``12``; absurd when it is none of those."""
    m = _XL_CELL.match(ref.strip()) if len(ref) <= 64 else None
    if not m or not (m.group(1) or m.group(2)):
        return _XL_ABSURD, _XL_ABSURD
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return (int(m.group(2)) if m.group(2) else 0), col


class _XlAbsurd(Exception):
    """The extent is already absurd: stop parsing."""


def _xl_member_extent(xml: bytes) -> int:
    """max_row x max_column of one XML member, STREAM-PARSED with expat (round 5 Codex #6). A start
    tag of any length, a ``>`` inside an attribute value and an ``r=`` inside another attribute's
    value are read exactly as an XML reader reads them; only the unprefixed ``r`` and ``ref``
    attributes count (what openpyxl reads). No tree is built. A DTD or an entity declaration, or
    XML that does not parse, is ``ValueError`` (fail closed: the upload is refused)."""
    from xml.parsers import expat
    st = {"rows": 0, "cols": 0, "row": 0, "col": 0}

    def start(name: str, attrs: dict) -> None:
        tag = name.rsplit(":", 1)[-1]                # no namespace processing: the local name
        if tag not in _XL_TAGS:
            return
        if tag == "row":
            r = attrs.get("r")
            if r is None:
                st["row"] += 1
            else:
                raw = r.strip()
                st["row"] = int(raw) if raw.isdigit() and len(raw) <= 7 else _XL_ABSURD
            st["col"] = 0
            st["rows"] = max(st["rows"], st["row"])
        elif tag == "c":
            r = attrs.get("r")
            if r is None:
                st["col"] += 1
                st["rows"] = max(st["rows"], st["row"])
            else:
                row, st["col"] = _xl_point(r)
                st["rows"] = max(st["rows"], row or st["row"])
            st["cols"] = max(st["cols"], st["col"])
        else:                                          # dimension, mergeCell, hyperlink, comment
            ref = attrs.get("ref")
            if ref is None:
                return
            for end in (ref if len(ref) <= 128 else "!").split(":")[:2]:
                row, col = _xl_point(end)
                st["rows"], st["cols"] = max(st["rows"], row), max(st["cols"], col)
        if st["rows"] * st["cols"] >= _XL_ABSURD:
            raise _XlAbsurd()

    def refuse(*_a) -> None:
        raise ValueError("xml_dtd")

    p = expat.ParserCreate()      # prefixes kept as written; an unprefixed ``r`` is the key "r"
    p.StartElementHandler = start
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    try:
        p.Parse(xml, True)
    except _XlAbsurd:
        return _XL_ABSURD
    except expat.ExpatError:
        raise ValueError("unparseable") from None
    return st["rows"] * st["cols"]


def xlsx_extent(data: bytes) -> int:
    """Sum over the workbook's XML members of max_row x max_column: the cells a dense reader
    could build. Read from the XML only (no reader; stream-parsed, round 5 Codex #6); an
    unparseable reference counts as absurd, and XML that does not parse is ``ValueError``.
    The zip limits (``check_bytes``) bound what is decompressed here."""
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            with z.open(info) as f:
                head = f.read(64)
            try:
                xml_like = info.filename.lower().endswith(".xml") or _xml_head(head)
            except Refused:
                xml_like = True
            if not xml_like:
                continue
            total += _xl_member_extent(z.read(info))   # expat reads UTF-8/16 itself
            if total >= _XL_ABSURD:
                return _XL_ABSURD
    return total


def check_xlsx_extent(data: bytes) -> None:
    try:
        n = xlsx_extent(data)
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        raise Refused("unreadable") from None
    if n > XLSX_MAX_CELLS:
        raise Refused("xlsx_dimensions")


def _read(kind: str, data: bytes) -> str:
    """Worker side: page cap, the .xlsx extent, the kind's reader, flatten, clean. Raises
    ``Refused``."""
    r = _readers_for(kind)
    if kind == "xlsx":
        check_xlsx_extent(data)                        # before any reader builds a grid (B11)
    if kind == "pdf":
        try:
            doc = r["_pdfium"].PdfDocument(data)
            try:
                pages = len(doc)
            finally:
                doc.close()
        except Exception:                                  # noqa: BLE001 — unreadable / encrypted
            raise Refused("unreadable") from None
        if pages > PDF_MAX_PAGES:
            raise Refused("pdf_pages")
    if kind == "txt":
        chat = whatsapp_text(data.decode("utf-8"))
        if chat is not None:
            data = chat.encode("utf-8")
    try:
        text = _flatten(kind, r[kind](data), r)
    except Refused:
        raise
    except Exception:                                      # noqa: BLE001 — any reader error
        raise Refused("unreadable") from None
    return clean_text(text)


def _worker_init(paths: list[str]) -> None:
    for p in paths:
        if p not in sys.path:
            sys.path.append(p)
    _available()                                           # import now, outside any budget


def _ping() -> list[str]:
    return _available()


# ------------------------------------------------------------------------------------------
# the worker process (Internal Brain side)
# ------------------------------------------------------------------------------------------
_POOL: Any = None
_KINDS: list[str] = []                                     # formats the worker can read
_POOL_LOCK = threading.Lock()                              # creating / killing the worker
_RUN_LOCK = threading.Lock()                               # one read at a time


def _kill() -> None:
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        try:
            pool.terminate()                               # SIGTERM / TerminateProcess
        except Exception:                                  # noqa: BLE001
            pass


def _pool() -> Any:
    """The running worker, started (and its readers imported) on first use. None: unavailable."""
    global _POOL, _KINDS
    with _POOL_LOCK:
        if _POOL is None:
            _ensure_path()
            ctx = multiprocessing.get_context("spawn")
            pool = ctx.Pool(1, initializer=_worker_init, initargs=(list(sys.path),))
            try:
                kinds = list(pool.apply_async(_ping).get(WORKER_START_S))
            except Exception:                              # noqa: BLE001 — could not start
                kinds = []
            if not kinds:
                pool.terminate()
                return None
            _POOL, _KINDS = pool, kinds
        return _POOL


atexit.register(_kill)                                     # no orphaned worker at exit


def available_kinds() -> list[str]:
    """The formats the worker can read here (starts the worker). A format whose library is not
    installed is missing, and its files are refused as ``unavailable``."""
    return list(_KINDS) if _pool() is not None else []


def worker_pid() -> Optional[int]:
    """The worker process id (tests: a budget overrun kills it)."""
    with _POOL_LOCK:
        procs = getattr(_POOL, "_pool", None) or []
        return procs[0].pid if procs else None


def run_in_worker(fn: Callable, args: tuple, budget_s: float) -> Any:
    """Run ``fn(*args)`` in the worker within ``budget_s`` seconds; past it the worker process is
    killed and the call is refused (``time_budget``). Waiting for the worker to be free and
    starting it are not part of the budget."""
    import multiprocessing.pool as mpp
    if not _RUN_LOCK.acquire(timeout=QUEUE_WAIT_S):
        raise Refused("busy")
    try:
        pool = _pool()
        if pool is None:
            raise Refused("unavailable")
        res = pool.apply_async(fn, args)
        try:
            return res.get(timeout=budget_s)
        except mpp.TimeoutError:
            _kill()                                        # the runaway read dies with it
            raise Refused("time_budget") from None
        except Refused:
            raise
        except Exception:                                  # noqa: BLE001 — reader / pickling error
            raise Refused("unreadable") from None
    finally:
        _RUN_LOCK.release()


def warm() -> bool:
    """Start the worker and import the readers now (about 5 to 10 s), so the first upload does
    not pay for it. False when no reader can be imported here."""
    try:
        return _pool() is not None
    except Exception:                                      # noqa: BLE001
        return False


def extract(kind: str, data: bytes, *, budget_s: float = TIME_BUDGET_S) -> str:
    """Check the bytes, then read them with the kind's convert reader in the worker within the
    budget, flatten and clean. Raises ``Refused``; an empty result is refused (nothing to index)."""
    check_bytes(kind, data)
    text = run_in_worker(_read, (kind, data), budget_s)
    if not isinstance(text, str):
        raise Refused("unreadable")
    text = text[:MAX_TEXT_CHARS]
    if not text.strip():
        raise Refused("no_text")
    return text


__all__ = ["Refused", "extract", "check_bytes", "clean_text", "kind_for_filename", "warm",
           "available_kinds",
           "is_whatsapp", "whatsapp_text", "run_in_worker", "worker_pid",
           "EXT_KIND", "KIND_MIME", "EXTRACT_MIME_KIND", "BINARY_MIME_KIND", "TEXT_KINDS",
           "MAX_FILE_BYTES", "MAX_TEXT_CHARS", "TIME_BUDGET_S", "PDF_MAX_PAGES", "ZIP_MAX_TOTAL",
           "ZIP_MAX_ENTRIES", "ZIP_MAX_RATIO", "XLSX_MAX_CELLS", "xlsx_extent", "check_xlsx_extent"]
