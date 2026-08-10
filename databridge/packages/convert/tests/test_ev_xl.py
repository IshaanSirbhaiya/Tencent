"""EV-XL — Excel fidelity eval suite (09_FINAL_PRD §23).

  Fixture : the Gauntlet Workbook (multi-sheet, merged, hidden, serial dates 1900/1904,
            errors, locale numbers, leading zeros, ListObjects, named ranges).
  Metric  : cell-value accuracy ; header-detect decision accuracy.
  Gate    : 100% values ; ≥95% header auto (rest correctly routed to confirm).
  Canary  : the swapped-epoch fixture MUST be flagged (else the run is invalid).

Thresholds are module constants here (the repo's `thresholds.yaml` is a C24 deliverable;
when it lands these move there, per the eval-suite skill). The suite emits an EvalResult-
shaped dict via ``run_ev_xl`` so C24 can wire it into the scorecard unchanged."""
from __future__ import annotations

import datetime as _dt

import pytest

from databridge_convert.excelsub import ReadOptions, read_workbook
from databridge_convert.excelsub.header_detection import needs_confirmation
from fixtures.excel.build import (
    EXPECTED,
    build_gauntlet,
    build_gauntlet_1904,
    build_swapped_epoch,
)

# --- gates (→ thresholds.yaml when C24 lands) -----------------------------------
GATE_CELL_ACCURACY = 1.00          # §23: 100% values
GATE_HEADER_ACCURACY = 0.95        # §23: ≥95% header auto (rest confirmed)


def _by_name(wb):
    return {s.name: s for s in wb.sheets}


# === metric 1: cell-value accuracy (must be 100%) ===============================
def _cell_oracle() -> list[tuple[str, int, int, object]]:
    """(sheet, row, col, expected_value) over every typed kind in the Gauntlet."""
    r2 = EXPECTED["sales_row2"]
    oracle: list[tuple[str, int, int, object]] = [
        # Sales header
        *[("Sales", 0, c, h) for c, h in enumerate(EXPECTED["sales_header"])],
        # Sales row 2 — leading-zero text, locale text, float, cached formula, datetime, status
        ("Sales", 1, 0, r2["id"]), ("Sales", 1, 1, r2["merchant"]),
        ("Sales", 1, 2, r2["amount_text"]), ("Sales", 1, 3, r2["qty"]),
        ("Sales", 1, 4, r2["price"]), ("Sales", 1, 5, r2["total"]),
        ("Sales", 1, 6, r2["created"]), ("Sales", 1, 7, r2["status"]),
        ("Sales", 1, 8, r2["note"]),
        # Sales row 3 cached formula
        ("Sales", 2, 5, EXPECTED["sales_total_row3"]),
        # Ledger — ref id, ambiguous serial, cross-sheet cached, merged anchor, tax, anchor
        ("Ledger", 1, 0, "L-1"),
        ("Ledger", 1, 1, EXPECTED["ledger_ship_serials"][0]),
        ("Ledger", 1, 2, EXPECTED["crosssheet_value"]),
        ("Ledger", 1, 6, "BATCH-7"),
        ("Ledger", 1, 7, EXPECTED["named_range_TaxRate"]),
        # calamine returns a date (not datetime) for a midnight-valued date cell
        ("Ledger", 1, 8, _dt.date(2021, 6, 15)),
        ("Ledger", 1, 9, "2021-06-15"),
        # error cells under null_warn → None
        ("Ledger", 1, 3, None), ("Ledger", 1, 4, None), ("Ledger", 1, 5, None),
        # Quarterly data
        ("Quarterly", 2, 0, "North"), ("Quarterly", 2, 1, 100.0),
    ]
    return oracle


def cell_value_accuracy(wb) -> tuple[float, list]:
    sheets = _by_name(wb)
    mismatches = []
    oracle = _cell_oracle()
    for sheet, r, c, expected in oracle:
        got = sheets[sheet].cells[r][c]
        if got != expected:
            mismatches.append({"sheet": sheet, "cell": f"r{r}c{c}",
                               "expected": repr(expected), "got": repr(got)})
    correct = len(oracle) - len(mismatches)
    return correct / len(oracle), mismatches


def test_cell_value_accuracy_is_100pct():
    wb = read_workbook(build_gauntlet(), options=ReadOptions(sheets="all"))
    acc, mism = cell_value_accuracy(wb)
    assert acc == GATE_CELL_ACCURACY, f"cell accuracy {acc:.3f} < 1.0; mismatches={mism}"


# === metric 2: header-detect decision accuracy (≥95%) ==========================
def header_decision_accuracy(wb) -> tuple[float, dict]:
    """For each sheet: did we make the right call — auto-detect (conf≥0.9) on the clean
    sheets, route-to-confirm on the multi-row-header sheet? A correct confirm-route is a
    PASS, not a miss (§14.2)."""
    sheets = _by_name(wb)
    auto_expected = set(EXPECTED["auto_sheets"])
    confirm_expected = set(EXPECTED["confirm_sheets"])
    detail, correct, total = {}, 0, 0
    for name in auto_expected | confirm_expected:
        sr = sheets[name]
        routed_confirm = needs_confirmation(sr.header_inference)
        if name in auto_expected:
            ok = (not routed_confirm) and sr.header_inference.row_idx == 0
        else:
            ok = routed_confirm                              # correctly flagged for a human
        detail[name] = {"row": sr.header_inference.row_idx,
                        "conf": sr.header_inference.confidence,
                        "routed_confirm": routed_confirm, "ok": ok}
        correct += ok
        total += 1
    return correct / total, detail


def test_header_detect_accuracy_meets_gate():
    wb = read_workbook(build_gauntlet(), options=ReadOptions(sheets="all"))
    acc, detail = header_decision_accuracy(wb)
    assert acc >= GATE_HEADER_ACCURACY, f"header accuracy {acc:.3f} < 0.95; {detail}"


def test_multirow_header_routed_to_confirm():
    # The documented confirm case (§14.2): multi-row headers route to a human, not a guess.
    wb = read_workbook(build_gauntlet(), options=ReadOptions(sheets=["Quarterly"]))
    q = wb.sheets[0]
    assert needs_confirmation(q.header_inference)
    assert any("multi-row header" in w for w in q.warnings)


# === canary: swapped epoch MUST flag (else run invalid) ========================
def test_swapped_epoch_canary_flags():
    wb = read_workbook(build_swapped_epoch(), options=ReadOptions(sheets="all"))
    flagged = [w for w in wb.warnings if "EPOCH_MISMATCH" in w]
    assert flagged, "swapped-epoch canary did NOT flag — EV-XL run is INVALID"
    assert any("EPOCH_MISMATCH" in x for x in wb.loss_report)


def test_consistent_1904_does_not_false_flag():
    # The honest 1904 variant must NOT trip the epoch canary (no false positives).
    wb = read_workbook(build_gauntlet_1904(), options=ReadOptions(sheets="all"))
    assert wb.epoch == "1904"
    assert not [w for w in wb.warnings if "EPOCH_MISMATCH" in w]


# === EvalResult emission (C24 wiring) ==========================================
def run_ev_xl() -> dict:
    """Emit the EvalResult-shaped record EV-XL contributes to the scorecard."""
    wb = read_workbook(build_gauntlet(), options=ReadOptions(sheets="all"))
    cell_acc, mism = cell_value_accuracy(wb)
    hdr_acc, hdr_detail = header_decision_accuracy(wb)
    swapped = read_workbook(build_swapped_epoch(), options=ReadOptions(sheets="all"))
    canary_fired = bool([w for w in swapped.warnings if "EPOCH_MISMATCH" in w])
    return {
        "suite": "EV-XL", "metric": "cell_value_accuracy + header_decision_accuracy",
        "value": round(cell_acc, 4),
        "header_accuracy": round(hdr_acc, 4),
        "threshold": GATE_CELL_ACCURACY,
        "passed": cell_acc >= GATE_CELL_ACCURACY and hdr_acc >= GATE_HEADER_ACCURACY
                  and canary_fired,
        "profile": "deterministic",            # no model in the loop — pure parsing
        "model_map": {},
        "slices": {"header_detail": hdr_detail, "cell_mismatches": mism,
                   "swapped_epoch_canary_fired": canary_fired},
    }


def test_run_ev_xl_passes():
    res = run_ev_xl()
    assert res["passed"], res
