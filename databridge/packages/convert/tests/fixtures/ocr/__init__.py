"""OCR fixtures — invoice scans (clean/skewed/noisy) + the O/0 confusion trap (§23 EV-OCR).

Deterministic, hermetic (rendered with a real TTF, seeded noise) — no checked-in binaries.
See ``databridge-brain/50-research/fixtures.md``."""
from __future__ import annotations

from .build import (
    OcrFixture,
    all_fixtures,
    build_clean,
    build_confusion,
    build_noisy,
    build_skewed,
    scan_fixtures,
)

__all__ = [
    "OcrFixture", "all_fixtures", "scan_fixtures",
    "build_clean", "build_skewed", "build_noisy", "build_confusion",
]
