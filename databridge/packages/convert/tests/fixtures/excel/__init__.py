"""Excel fixtures — the Gauntlet Workbook and its canaries (09_FINAL_PRD §23 EV-XL, §25).

The Gauntlet is a VERSIONED PRODUCT FIXTURE reused by C10 (reader), C11 (writer), C16
(extract) and demos D1/D2. The builder (``build.py``) is the source of truth; it produces
the bytes hermetically so tests need no checked-in binary, and ``build.py --write`` also
materialises the .xlsx/.xlsm files into this directory for the demos. See
``databridge-brain/50-research/fixtures.md`` for the catalogue.
"""
from __future__ import annotations

from .build import (
    EXPECTED,
    build_encrypted,
    build_gauntlet,
    build_gauntlet_1904,
    build_gauntlet_lite,
    build_macro_xlsm,
    build_swapped_epoch,
)

__all__ = [
    "build_gauntlet", "build_gauntlet_1904", "build_swapped_epoch", "build_gauntlet_lite",
    "build_macro_xlsm", "build_encrypted", "EXPECTED",
]
