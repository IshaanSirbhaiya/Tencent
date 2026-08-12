"""Build a PDF and a page raster from a real chit fixture — for the T-36 extraction tests.

Deterministic and hermetic: no binaries checked in, the same convention `fixtures/extract/build.py`
and `fixtures/email/build.py` already follow.

**The font matters, and it is why this module exists rather than a two-line helper.** The
authorisation forms and the wellness voucher carry their package selection as ballot boxes — ``☒``
for the option that was chosen, ``☐`` for the ones that were not — and
``databridge_intake.ground.mask_unticked_options`` blanks the unticked lines so the grounding wall
cannot confirm an option the form declined. Render the fixture in Helvetica and both glyphs come
out as ``n``: the test would then "pass" against a PDF that had already lost the very signal it is
supposed to prove survives. So the fixture is built with a font that actually has U+2610/U+2612,
and if this machine has none, the test skips rather than lies.
"""
from __future__ import annotations

import glob
import io
import os
from typing import Optional

# Fonts known to carry U+2610 BALLOT BOX and U+2612 BALLOT BOX WITH X, most portable first.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/seguisym.ttf",
    "C:/Windows/Fonts/arialuni.ttf",
)

BALLOT_TICKED = "\u2612"
BALLOT_UNTICKED = "\u2610"


def _matplotlib_dejavu() -> list[str]:
    try:
        import matplotlib
    except ImportError:                                          # pragma: no cover - env-dependent
        return []
    return glob.glob(os.path.join(os.path.dirname(matplotlib.__file__),
                                  "mpl-data", "fonts", "ttf", "DejaVuSans.ttf"))


def ballot_font_path() -> Optional[str]:
    """A TTF on this machine that can actually draw ☒ and ☐, or ``None``."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:                                          # pragma: no cover - env-dependent
        # Without it we cannot PROVE a font has the glyphs, and a fixture that silently draws ☒ as
        # a fallback box would make the checkbox test pass against a document that had lost it.
        return None

    for path in list(_FONT_CANDIDATES) + _matplotlib_dejavu():
        if not os.path.exists(path):
            continue
        try:
            cmap = TTFont(path).getBestCmap()
        except Exception:                                        # pragma: no cover - broken font
            continue
        if ord(BALLOT_TICKED) in cmap and ord(BALLOT_UNTICKED) in cmap:
            return path
    return None


def text_to_pdf(text: str, *, font_path: str, font_size: int = 9,
                line_height: int = 12) -> bytes:
    """One source line per rendered line, so the extracted line order mirrors the fixture."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("ChitBallot", font_path))
    buf = io.BytesIO()
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("ChitBallot", font_size)
    y = A4[1] - 40
    for line in text.splitlines():
        if y < 40:
            c.showPage()
            c.setFont("ChitBallot", font_size)
            y = A4[1] - 40
        c.drawString(36, y, line[:110])
        y -= line_height
    c.save()
    return buf.getvalue()


def pdf_page_to_png(pdf_bytes: bytes, *, page: int = 0, scale: float = 2.5) -> bytes:
    """Render one PDF page to a PNG — a stand-in for a scan, so the OCR path has real input."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        image = doc[page].render(scale=scale).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()
