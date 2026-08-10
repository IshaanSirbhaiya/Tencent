"""Email SOURCE adapter (C14, 09_FINAL_PRD §5.2, §11.6, §17.1).

`.eml` (stdlib ``email``) → DocumentRecord(body) + typed headers, and an **attachment
fan-out**: each attachment RE-ENTERS the router as its own source. Provenance chains
email → attachment → page → span via the ``email_part`` pointer (message_id, part) whose
``child`` is a pointer into the attachment's own IR — the §11.6 / §17.1 chain requirement.

`.msg` is a best-effort, read-only path via ``extract-msg`` (warned, §5.2); if the library
is absent we refuse loudly rather than silently degrade.
"""
from __future__ import annotations

import email
import hashlib
import re
import warnings
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses
from typing import Optional

from databridge_core import SourcePointer

from .._docutil import guard_zip_bomb, markdown_serialize
from .._util import AdapterError, snapshot_for
from ...ir import Block, DocumentRecord

# headers we lift into typed metadata (§5.2: from/to/cc/date/message-id as structured headers)
_TYPED_HEADERS = ("from", "to", "cc", "bcc", "date", "subject", "message-id", "reply-to")
_ADDR_HEADERS = ("to", "cc", "bcc")          # multi-address, RFC-parsed (not naive comma split)

# Attachment fan-out budgets (§21.5-class amplification defence). A single .eml cannot mint
# unbounded sources: count, per-attachment bytes, and aggregate bytes are all capped, and the
# OOXML zip-bomb guard runs on every payload BEFORE it leaves the adapter (F1/F2/F3).
_MAX_ATTACHMENTS = 1000
_MAX_ATTACHMENT_BYTES = 256 * 1024 ** 2      # 256 MiB per attachment
_MAX_TOTAL_ATTACHMENT_BYTES = 1024 ** 3      # 1 GiB total across attachments
_ZIP_MAGIC = b"PK\x03\x04"

# attachment format sniffing → registry format keys (extension first, then magic bytes).
_EXT_FORMAT = {
    "pdf": "pdf", "csv": "csv", "tsv": "tsv", "json": "json", "jsonl": "jsonl",
    "parquet": "parquet", "arrow": "arrow", "xml": "xml", "yaml": "yaml", "yml": "yaml",
    "html": "html", "htm": "html", "md": "markdown", "markdown": "markdown", "txt": "txt",
    "docx": "docx", "pptx": "pptx", "xlsx": "xlsx", "xls": "xlsx",
    "png": "image", "jpg": "image", "jpeg": "image", "tif": "image", "tiff": "image",
    "eml": "eml",
}


def _safe_name(filename: str) -> str:
    """Strip path/traversal components from an attacker-controlled MIME filename (F4). The
    result is metadata only — never a filesystem path — but we sanitise at capture as
    defence-in-depth so a downstream consumer can't be tricked by ``../`` / drive / UNC."""
    name = filename.replace("\\", "/").split("/")[-1]
    name = name.lstrip(".") or "attachment"          # no leading-dot / empty
    return re.sub(r"[\x00-\x1f]", "", name)[:255]


def sniff_format(filename: str, data: bytes) -> Optional[str]:
    """Best-effort source-format guess for a fanned-out attachment (magic FIRST for the
    security-relevant types, then extension). A ``PK`` payload is ALWAYS treated as an OOXML
    zip family for guarding purposes regardless of declared extension/content-type (F1)."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
        return "image"
    if data[:2] in (b"II", b"MM"):
        return "image"              # TIFF
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if data[:4] == _ZIP_MAGIC:
        # OOXML container — disambiguate by extension; never let content-type route it AWAY
        # from a zip-aware reader (the payload is guarded regardless, but pick the zip family).
        return _EXT_FORMAT.get(ext, "docx") if ext in ("docx", "pptx", "xlsx", "xls") else None
    if ext in _EXT_FORMAT:
        return _EXT_FORMAT[ext]
    return None


@dataclass(frozen=True)
class EmailAttachment:
    idx: int
    filename: str                            # sanitised basename (metadata only, never a path)
    content_type: str
    data: bytes
    format_guess: Optional[str]
    parent_pointer: SourcePointer            # email_part(message_id, part="attachment:idx")
    source_id: str                           # UNIQUE id for re-routing (message_id+idx) — H2:
    #                                          prevents media_id/document_id collisions across
    #                                          attachments and across a batch (§17.3 resolvable)


@dataclass
class EmailParse:
    doc: DocumentRecord
    message_id: str
    headers: dict
    attachments: list[EmailAttachment] = field(default_factory=list)


def _scrub(v: str) -> str:
    """Strip CR/LF (header-injection bytes) and outer whitespace from a header value (F6)."""
    return re.sub(r"[\r\n]+", " ", str(v)).strip()


def _message_id(msg: EmailMessage, data: bytes) -> str:
    mid = msg.get("message-id")
    if mid:
        # canonicalise: scrub folding/CRLF and collapse internal whitespace so two semantically
        # equal Message-IDs produce equal pointer keys (L3).
        return re.sub(r"\s+", "", _scrub(mid))
    # synthesise a stable id from the bytes so pointers are still deterministic.
    return f"<sha256:{hashlib.sha256(data).hexdigest()[:24]}@databridge.local>"


def _typed_headers(msg: EmailMessage) -> dict:
    out: dict = {}
    for h in _TYPED_HEADERS:
        v = msg.get(h)
        if v is None:
            continue
        if h in _ADDR_HEADERS:
            # RFC-correct address parse — honours quoted display names containing commas
            # ("Doe, John" <j@x>) instead of a naive comma split (#2).
            out[h] = [addr for _name, addr in getaddresses([_scrub(v)]) if addr]
        else:
            out[h] = _scrub(v)
    return out


def _mid_token(mid: str) -> str:
    """A filesystem/identifier-safe token derived from a Message-ID (for unique source ids)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", mid.strip("<>"))[:80] or "email"


def _body_text(msg: EmailMessage, body=None) -> str:
    """Extract the body as text. ``body`` may be pre-fetched (avoids a redundant get_body)."""
    if body is None:
        body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_type() == "text/html":
        # reuse the hardened HTML source adapter (no-network lxml + readability) for text.
        try:
            from . import html as s_html
            doc = s_html.read(content.encode("utf-8", "replace"))
            return "\n".join(b.text for b in doc.blocks if b.text)
        except Exception:                       # pragma: no cover - degrade to raw text
            return content
    return content


def parse(data: bytes) -> EmailParse:
    """Parse .eml bytes into a DocumentRecord(body) + typed headers + fanned-out attachments."""
    if not data:
        raise AdapterError("email: empty input")
    msg: EmailMessage = email.message_from_bytes(data, policy=default_policy)  # type: ignore[assignment]
    mid = _message_id(msg, data)
    headers = _typed_headers(msg)
    snapshot = snapshot_for("email", data)

    # --- body → blocks; each block carries email_part(body) with a SPAN child (M4) so a
    #     body-extracted field resolves to a specific char range, not just "the body". ---
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body_is_html = body_part is not None and body_part.get_content_type() == "text/html"
    body = _body_text(msg, body_part)
    paras = [p.strip() for p in body.replace("\r\n", "\n").split("\n") if p.strip()]
    blocks: list[Block] = [Block(kind="Paragraph", text=p) for p in paras]
    _text, char_index = markdown_serialize(blocks)
    provenance: dict[int, SourcePointer] = {
        i: SourcePointer(
            source_id="email", kind="email_part", message_id=mid, part="body",
            child=SourcePointer(source_id="email", kind="doc_span", document_id="email",
                                char_start=char_index.spans[i][0],
                                char_end=char_index.spans[i][1]))
        for i in range(len(blocks))
    }

    # --- attachments → fan-out list: guarded, budgeted, uniquely identified ---
    attachments: list[EmailAttachment] = []
    total_bytes = 0
    for i, part in enumerate(msg.iter_attachments()):
        if i >= _MAX_ATTACHMENTS:
            raise AdapterError(f"email: >{_MAX_ATTACHMENTS} attachments — fan-out amplification "
                               "refused (§21.5-class)")
        payload = part.get_payload(decode=True) or b""
        if len(payload) > _MAX_ATTACHMENT_BYTES:
            raise AdapterError(f"email: attachment {i} is {len(payload)}B (>{_MAX_ATTACHMENT_BYTES}) "
                               "— refused")
        total_bytes += len(payload)
        if total_bytes > _MAX_TOTAL_ATTACHMENT_BYTES:
            raise AdapterError(f"email: attachments exceed {_MAX_TOTAL_ATTACHMENT_BYTES}B total "
                               "— fan-out amplification refused")
        # F1: zip-bomb guard runs on EVERY payload before it leaves the adapter (no-ops on
        # non-PK bytes). A PK payload cannot slip past via a lying content-type.
        guard_zip_bomb(payload, label=f"email attachment {i}")
        filename = _safe_name(part.get_filename() or f"attachment-{i}")
        ctype = part.get_content_type()
        fmt = sniff_format(filename, payload)
        if fmt is None and ctype:
            fmt = _CONTENT_TYPE_FORMAT.get(ctype)
        parent = SourcePointer(source_id="email", kind="email_part",
                               message_id=mid, part=f"attachment:{i}")
        att_source_id = f"{_mid_token(mid)}#attachment:{i}"     # H2: globally unique
        attachments.append(EmailAttachment(
            idx=i, filename=filename, content_type=ctype, data=payload, format_guess=fmt,
            parent_pointer=parent, source_id=att_source_id))

    meta = {
        "headers": headers,
        "message_id": mid,
        "attachments": [{"idx": a.idx, "filename": a.filename, "content_type": a.content_type,
                         "size": len(a.data), "format_guess": a.format_guess,
                         "source_id": a.source_id}
                        for a in attachments],
        "loss_report": (["email: html body main-content extracted; remote images/scripts dropped"]
                        if body_is_html else []),
    }
    doc = DocumentRecord(document_id="email", sha256=snapshot.content_hash, snapshot=snapshot,
                         blocks=blocks, char_index=char_index, block_provenance=provenance,
                         source_meta=meta)
    return EmailParse(doc=doc, message_id=mid, headers=headers, attachments=attachments)


_CONTENT_TYPE_FORMAT = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv", "application/json": "json", "text/html": "html",
    "text/plain": "txt", "application/xml": "xml", "text/xml": "xml",
    "image/png": "image", "image/jpeg": "image", "image/tiff": "image",
}


def read(data: bytes) -> DocumentRecord:
    """Registry entry point: .eml bytes → DocumentRecord (body + headers). Attachments are
    fanned out via ``parse``/``fan_out`` (they re-enter the router as their own sources)."""
    return parse(data).doc


def fan_out(data: bytes) -> list[EmailAttachment]:
    """Each attachment as its own source, carrying the parent ``email_part`` pointer."""
    return parse(data).attachments


def chain_pointer(parent: SourcePointer, child: SourcePointer) -> SourcePointer:
    """Complete the provenance chain: attach a pointer INTO the attachment's IR as the
    ``child`` of the parent email_part pointer (email → attachment → page → span)."""
    if parent.kind != "email_part":
        raise AdapterError(f"chain_pointer: parent kind {parent.kind!r} is not email_part")
    return SourcePointer(source_id=parent.source_id, kind="email_part",
                         message_id=parent.message_id, part=parent.part, child=child)


# --- .msg best-effort (read-only, warned — §5.2) --------------------------------
def read_msg(data: bytes) -> DocumentRecord:
    """Outlook ``.msg`` → DocumentRecord via ``extract-msg`` (best-effort, read-only). Refuses
    loudly (not silently) if the optional library is unavailable."""
    try:
        import extract_msg
    except ImportError as e:
        raise AdapterError(
            "msg: reading .msg requires the optional `extract-msg` library (best-effort, §5.2). "
            "Install it or convert the message to .eml.") from e
    warnings.warn("msg: .msg is a best-effort read-only path (extract-msg); fidelity is lower "
                  "than .eml (§5.2)", stacklevel=2)
    m = extract_msg.openMsg(data) if hasattr(extract_msg, "openMsg") else extract_msg.Message(data)
    snapshot = snapshot_for("msg", data)
    mid = (getattr(m, "messageId", None) or
           f"<sha256:{hashlib.sha256(data).hexdigest()[:24]}@databridge.local>")
    body = getattr(m, "body", "") or ""
    paras = [p.strip() for p in str(body).replace("\r\n", "\n").split("\n") if p.strip()]
    blocks = [Block(kind="Paragraph", text=p) for p in paras]
    provenance = {i: SourcePointer(source_id="msg", kind="email_part", message_id=str(mid),
                                   part="body") for i in range(len(blocks))}
    _t, char_index = markdown_serialize(blocks)
    headers = {h: getattr(m, attr, None) for h, attr in
               (("from", "sender"), ("to", "to"), ("cc", "cc"), ("subject", "subject"),
                ("date", "date"))}
    meta = {"headers": {k: v for k, v in headers.items() if v}, "message_id": str(mid),
            "loss_report": ["msg: best-effort extract-msg parse; attachments/rich formatting "
                            "may be incomplete (§5.2)"]}
    return DocumentRecord(document_id="msg", sha256=snapshot.content_hash, snapshot=snapshot,
                          blocks=blocks, char_index=char_index, block_provenance=provenance,
                          source_meta=meta)
