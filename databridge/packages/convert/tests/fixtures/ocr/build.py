"""Build the OCR fixture set (09_FINAL_PRD §23 EV-OCR) — deterministic, no network.

Three scans of the SAME synthetic invoice form, increasingly degraded, plus an O/0
confusion-trap fixture:
  * ``clean``  — crisp black-on-white render.
  * ``skewed`` — rotated ~4° (the deskewer must recover it).
  * ``noisy``  — gaussian noise + light blur (the binariser/denoise must cope).
  * ``confusion`` — fields packed with O/0 and 1/l/I lookalikes (the §23 O/0 trap; we
                    MEASURE its accuracy, it need not be 100%).

Each fixture ships GOLD field values so EV-OCR computes field F1 honestly. Rendered with a
real TTF (Arial) at a legible size so OCR is meaningful; falls back to PIL's bitmap font if
the TTF is missing (still deterministic). All randomness is seeded.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
_SEED = 1729


@dataclass(frozen=True)
class OcrFixture:
    name: str
    png: bytes
    gold: dict          # field name → expected value (the hand-label)
    note: str = ""


def _font(size: int) -> ImageFont.FreeTypeFont:
    # A real TTF is REQUIRED: the bitmap fallback (load_default) renders glyphs the OCR engine
    # reads very differently, which would silently move EV-OCR's F1 (HIGH-1). Fail loudly so a
    # missing font is recognised as a fixture problem, not absorbed into the score. (EV-OCR
    # SKIPS when no OCR engine is present, so this only fires where OCR is actually exercised.)
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise RuntimeError(
        "OCR fixtures need a TTF (Arial/DejaVuSans); none found. EV-OCR field-F1 is "
        "glyph-dependent — a bitmap fallback would make the number non-comparable. "
        f"Looked in: {_FONT_CANDIDATES}")


# The canonical invoice form — gold values are the source of truth for EV-OCR scoring.
_LINES = [
    ("Invoice Number: INV-2024-0042", {"invoice_number": "INV-2024-0042"}),
    ("Date: 2024-03-15", {"date": "2024-03-15"}),
    ("Bill To: Acme Industries", {"bill_to": "Acme Industries"}),
    ("Subtotal: 1250.00", {"subtotal": "1250.00"}),
    ("Tax: 100.00", {"tax": "100.00"}),
    ("Total Due: 1350.00", {"total_due": "1350.00"}),
]


def _render_form(lines: list[tuple[str, dict]], *, w: int = 720, h: int = 360) -> tuple[Image.Image, dict]:
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    font = _font(28)
    gold: dict = {}
    y = 30
    for text, g in lines:
        d.text((40, y), text, fill="black", font=font)
        gold.update(g)
        y += 52
    return img, gold


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build_clean() -> OcrFixture:
    img, gold = _render_form(_LINES)
    return OcrFixture(name="clean", png=_to_png(img), gold=gold, note="crisp render")


def build_skewed(angle: float = 4.0) -> OcrFixture:
    img, gold = _render_form(_LINES)
    rot = img.rotate(angle, expand=True, fillcolor="white", resample=Image.BICUBIC)
    return OcrFixture(name="skewed", png=_to_png(rot), gold=gold,
                      note=f"rotated {angle}° — deskewer must recover")


def build_noisy() -> OcrFixture:
    img, gold = _render_form(_LINES)
    rng = np.random.default_rng(_SEED)
    arr = np.asarray(img).astype(np.int16)
    noise = rng.normal(0, 22, arr.shape).astype(np.int16)
    noisy = np.clip(arr + noise, 0, 255).astype(np.uint8)
    out = Image.fromarray(noisy).filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(0.6))
    return OcrFixture(name="noisy", png=_to_png(out), gold=gold,
                      note="gaussian noise + blur — denoise/binarise must cope")


def build_degraded() -> OcrFixture:
    """The C16 SECOND, HARDER scanned form: strong gaussian noise (σ=40) + heavy blur (1.5px).

    Unlike ``build_noisy`` (σ=22/blur 0.6 — RapidOCR shrugs it off, scoring ≥0.95 like the clean
    scan, so it does NOT exercise the review band), this degradation is heavy enough that the
    blur MERGES a label into its value ('Bill To' → the space is lost), so the grounding wall can
    no longer confirm that field's value against the OCR text → it routes to the REVIEW band and
    the record outcome is `partial`, while the structured fields (invoice number/date/total) are
    still read and carry resolvable bbox provenance. This is the fixture that makes confidence
    routing DIFFERENTIATE clean vs degraded (C16, §11.5)."""
    img, gold = _render_form(_LINES)
    rng = np.random.default_rng(_SEED)
    arr = np.asarray(img).astype(np.int16)
    noisy = np.clip(arr + rng.normal(0, 40, arr.shape).astype(np.int16), 0, 255).astype(np.uint8)
    out = Image.fromarray(noisy).filter(
        __import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(1.5))
    return OcrFixture(name="degraded", png=_to_png(out), gold=gold,
                      note="σ=40 noise + 1.5px blur — a field degrades past the grounding wall")


def build_confusion() -> OcrFixture:
    """The §23 O/0 (and 1/l/I) confusion trap — lookalike-dense ids. MEASURED, not gated."""
    lines = [
        ("Order ID: O0O0-1l1I", {"order_id": "O0O0-1l1I"}),
        ("Account: 0OO0123456", {"account": "0OO0123456"}),
        ("Ref Code: lI1l-O0O0", {"ref_code": "lI1l-O0O0"}),
        ("PIN: 100I00l", {"pin": "100I00l"}),
    ]
    img, gold = _render_form(lines, h=260)
    return OcrFixture(name="confusion", png=_to_png(img), gold=gold,
                      note="O/0 and 1/l/I lookalikes — §23 confusion trap, measured")


def all_fixtures() -> list[OcrFixture]:
    return [build_clean(), build_skewed(), build_noisy(), build_confusion()]


def scan_fixtures() -> list[OcrFixture]:
    """The 3 invoice scans (clean/skewed/noisy) used for the EV-OCR field-F1 gate.

    NOTE (scope, §23): the PRD advertises 20 scans; the POC ships a 3-scan SMOKE version
    (clean/skewed/noisy) — enough to exercise the gate + canary deterministically. Scaling
    toward 20 (more content/degradation variants) is tracked as future fixture work."""
    return [build_clean(), build_skewed(), build_noisy()]
