"""scripts/eval_summary.py copies the eval's own numbers (results.json, else the RESULTS.md headline)
into the analytics page's JSON; nothing is typed in by hand."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "eval_summary.py"
EVAL = ROOT / "evals" / "internal_brain"


def _mod():
    spec = importlib.util.spec_from_file_location("eval_summary", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summary_matches_the_eval_output(tmp_path):
    mod = _mod()
    res = json.loads((EVAL / "results.json").read_text(encoding="utf-8"))
    s = mod.from_results_json(res)
    h = res["headline"]
    assert s["leaks"] == h["leaks"] and s["requests"] == h["n_requests_all_modes"]
    assert s["upper95"] == h["leak_rate_upper95"]
    assert s["differential"] == {"pairs": h["p2_compared"], "mismatches": h["p2_mismatches"],
                                 "control_mismatches": h["p2_control_mismatches"]}
    assert s["injection"]["hard_failures"] == h["injection_hard_failures"]
    rng = s["cited_recall"]["range"]
    assert 0 <= rng["min"] <= rng["max"] <= 1
    # the markdown fallback agrees with the JSON on the headline
    md = mod.from_results_md((EVAL / "RESULTS.md").read_text(encoding="utf-8"))
    assert (md["leaks"], md["requests"]) == (s["leaks"], s["requests"])
    assert md["differential"]["pairs"] == s["differential"]["pairs"]
    # an end-to-end write to a scratch path (no pytest collection)
    out = tmp_path / "eval_summary.json"
    assert mod.main(["--out", str(out), "--no-collect"]) == 0
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["leaks"] == h["leaks"] and body["tests"] == {"backend": None, "e2e": 124}   # the --e2e default (TEST_REPORT.md)
    assert body["stale"] in (True, False, None) and body["note"]


def test_staleness_uses_the_code_digest_the_eval_recorded():
    mod = _mod()
    from evals.internal_brain.report import ib_code_sha256
    assert mod.ib_code_sha256() == ib_code_sha256()          # the two copies agree
    assert mod.stale("2000-01-01T00:00:00+00:00", ib_code_sha256()) is False
    assert mod.stale("2999-01-01T00:00:00+00:00", "0" * 64) is True
