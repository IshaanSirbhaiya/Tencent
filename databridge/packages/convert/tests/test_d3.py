"""Demo D3 (09_FINAL_PRD §27): nested JSON → flattened CSV, full lifecycle + EV-DET."""
from __future__ import annotations

from decimal import Decimal

from databridge_convert.demos.d3 import run_d3, run_d3_via_router


def test_d3_full_cycle():
    r = run_d3()
    assert r["syntactic"][0] is True
    assert all(v.passed for v in r["semantic"])
    assert r["quarantine"]["quarantined"] == 0


def test_d3_flattens_and_coerces():
    r = run_d3()
    csv = r["csv"]
    assert csv.splitlines()[0] == "id,user_name,city,amount"     # flattened columns
    assert "001,Ana,Manila,1234.56" in csv                       # leading zero kept; amount coerced


def test_d3_ev_det_byte_identical():
    # EV-DET: execute and replay of the same artifact produce byte-identical output.
    r = run_d3()
    assert r["deterministic"] is True
    assert r["csv"] == r["csv_replay"]


def test_d3_infer_single_record():
    r = run_d3()
    assert r["infer"] == {"id": "001", "user_name": "Ana", "city": "Manila",
                          "amount": Decimal("1234.56")}


def test_d3_artifact_checksum_and_audit():
    r = run_d3()
    assert r["artifact"].verify_checksum() is True
    assert r["audit"]["checksum_ok"] is True
    assert r["audit"]["fitted_params"] == "none"                 # s2s has no fitted params


def test_d3_via_real_router_matches_shortcut():
    # the rewired path (real json+csv adapters + router) yields the SAME CSV as run_d3
    routed = run_d3_via_router()
    assert routed["csv"] == run_d3()["csv"]
    assert routed["decision"].direction == "s2s"
    assert routed["decision"].refusals == []
    assert routed["quarantine"]["quarantined"] == 0
