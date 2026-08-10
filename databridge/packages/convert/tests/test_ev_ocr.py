"""EV-OCR gate (09_FINAL_PRD §23): field F1 ≥0.75, bbox presence 100%, confusion trap measured.

Skips when no OCR engine is available (models/binary absent), mirroring the live-backend
deselect pattern. The confusion trap is the §23 canary — it MUST score below 1.0 on exact
match, else it isn't exercising the O/0 trap."""
from __future__ import annotations

import pytest

from databridge_convert.adapters.source.ocr_engines import OcrUnavailable, select_engine


def _engine_available() -> bool:
    try:
        select_engine()
        return True
    except OcrUnavailable:
        return False


pytestmark = pytest.mark.skipif(not _engine_available(), reason="no OCR engine (models/binary)")


def test_ev_ocr_field_f1_and_bbox_gates():
    from ev_ocr import run_ev_ocr
    sc = run_ev_ocr()
    assert sc["field_f1"] >= sc["f1_gate"], sc
    assert sc["bbox_presence"] >= sc["bbox_gate"], sc


def test_confusion_trap_measured_and_real():
    from ev_ocr import run_ev_ocr
    trap = run_ev_ocr()["confusion_trap"]
    assert trap["n_fields"] >= 4
    assert trap["exact_accuracy"] < 1.0, trap                       # CANARY: trap must bite
    assert trap["confusion_tolerant_accuracy"] >= trap["exact_accuracy"]
