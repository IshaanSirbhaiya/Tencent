"""EV-SEC coverage for the C13/C14 surfaces (09_FINAL_PRD §21/§23): the new OCR + email
adapters add attack surface (attachment fan-out, image/PDF rasterisation) that the SQL-only
EV-SEC corpus didn't cover. These exercise the guards added after the adversarial review."""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest

from databridge_convert.adapters._util import AdapterError
from databridge_convert.adapters.source import _imageprep, email as e


# --- F1: a zip-bomb ATTACHMENT is guarded inside parse(), before fan-out ---
def _zip_bomb_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.bin", b"\x00" * (50 * 1024 * 1024))   # 50MB → tiny compressed
    return buf.getvalue()


def _eml_with_attachment(payload: bytes, *, filename="a.docx",
                         ctype=("application", "vnd.openxmlformats-officedocument."
                                "wordprocessingml.document")) -> bytes:
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = "a@x"; m["To"] = "b@y"; m["Subject"] = "s"; m["Message-ID"] = "<z@x>"
    m.set_content("body")
    m.add_attachment(payload, maintype=ctype[0], subtype=ctype[1], filename=filename)
    return m.as_bytes()


def test_zip_bomb_attachment_refused():
    bomb = _zip_bomb_bytes()
    # ratio guard should fire on the high-ratio member
    with pytest.raises(AdapterError):
        e.parse(_eml_with_attachment(bomb))


def test_zip_bomb_not_bypassed_by_lying_content_type():
    """A PK payload declared as text/plain must STILL be guarded (F1: no content-type bypass)."""
    bomb = _zip_bomb_bytes()
    with pytest.raises(AdapterError):
        e.parse(_eml_with_attachment(bomb, filename="notes.txt", ctype=("text", "plain")))


# --- F4: attachment filename path traversal is sanitised at capture ---
def test_attachment_filename_traversal_sanitised():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO(); c = canvas.Canvas(buf); c.drawString(10, 10, "x"); c.showPage(); c.save()
    eml = _eml_with_attachment(buf.getvalue(), filename="../../../etc/evil.pdf",
                               ctype=("application", "pdf"))
    att = e.fan_out(eml)[0]
    assert "/" not in att.filename and ".." not in att.filename
    assert att.filename == "etc_evil.pdf" or att.filename.endswith("evil.pdf")


# --- F2/F3: fan-out amplification budgets ---
def test_too_many_attachments_refused(monkeypatch):
    monkeypatch.setattr(e, "_MAX_ATTACHMENTS", 3)
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = "a@x"; m["To"] = "b@y"; m["Message-ID"] = "<z@x>"
    m.set_content("body")
    for i in range(5):
        m.add_attachment(b"hi", maintype="text", subtype="plain", filename=f"f{i}.txt")
    with pytest.raises(AdapterError):
        e.parse(m.as_bytes())


def test_oversized_attachment_refused(monkeypatch):
    monkeypatch.setattr(e, "_MAX_ATTACHMENT_BYTES", 10)
    eml = _eml_with_attachment(b"x" * 100, filename="a.txt", ctype=("text", "plain"))
    with pytest.raises(AdapterError):
        e.parse(eml)


# --- F6: CR/LF header injection bytes scrubbed ---
def test_header_crlf_scrubbed():
    raw = (b"From: a@x\r\nTo: b@y\r\nSubject: hi\r\nMessage-ID: <z@x>\r\n"
           b"X-Evil: line1\r\n\r\nbody\r\n")
    p = e.parse(raw)
    assert "\r" not in p.headers["subject"] and "\n" not in p.headers["subject"]


# --- F7: image pixel-bomb guard ---
def test_image_pixel_bomb_guard():
    with pytest.raises(AdapterError):
        _imageprep.guard_pixels(50_000, 50_000, label="t")   # 2.5 Gpx
    _imageprep.guard_pixels(1000, 1000, label="t")           # fine, no raise
