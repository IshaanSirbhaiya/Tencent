"""INT2 #1 (R-021 follow-up 2) — a federated LIVE read passes the pack screen BEFORE capture.

`run_live` used to capture `res.text` as received, so the federated capture store could hold
content the agent runtime withholds on receipt. The screen is ingestion's own
(`databridge_mcp_ingest.screen`), imported, not copied: a row is kept only on ``allow``; ``block``
and ``review`` rows are withheld and recorded in coverage; every string of a kept row is masked;
only the masked rows are captured. Expectations are derived from the screen itself.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fed_testkit import CRM, TODAY

from databridge_federated import DelegateError, SubTask, run_live

screen_mod = pytest.importorskip("databridge_mcp_ingest.screen")

SECRET, FLAG, PII = "SECRETKEY-4242", "CONFIDENTIAL", "MASKME9911"


def _cfg():
    return screen_mod.parse_screen({
        "decisive": [{"id": "test_secret", "pattern": r"SECRETKEY-\d+"}],
        "signals": [{"id": "test_flag", "pattern": FLAG, "weight": 1}],
        "redactions": [{"id": "test_pii", "pattern": r"MASKME\d+"}],
        "thresholds": {"block": 2, "review": 1, "min_signals_to_block": 2}}, "int2-test")


class _Gateway:
    def __init__(self, rows):
        self.rows = rows

    def query(self, conn, sql):
        return SimpleNamespace(is_error=False, text=json.dumps(self.rows), data=None,
                               mime="application/json")


def _captured(root) -> str:
    return "\n".join(p.read_bytes().decode("utf-8", "replace")
                     for p in root.rglob("*") if p.is_file())


def _task():
    return SubTask.model_validate({"adapter_agent": "crm_sources", "entity_kinds": ["tickets"],
                                   "fields": ["amount"], "freshness": "live"})


ROWS = [{"ticket_id": "T-1", "amount": "10"},
        {"ticket_id": "T-2", "amount": f"12 {PII}"},
        {"ticket_id": "T-3", "amount": f"13 {SECRET}"},
        {"ticket_id": "T-4", "amount": f"14 {FLAG}"}]


def test_live_rows_are_screened_before_capture(world, tmp_path):
    cfg = _cfg()
    verdicts = {r["ticket_id"]: screen_mod.screen_text(json.dumps(r, sort_keys=True), cfg)
                for r in ROWS}
    kept = [t for t, v in verdicts.items() if v.decision == "allow"]
    withheld = {t: v.decision for t, v in verdicts.items() if v.decision != "allow"}
    assert kept and sorted(set(withheld.values())) == ["block", "review"]   # non-vacuous

    entry = world.service().catalogue.agent("crm_sources").connections[0]
    res = run_live(_task(), entry, gateway=_Gateway(ROWS), today=TODAY, principal="x",
                   run_id=None, capture_root=tmp_path / "cap", screen=cfg)

    assert sorted(r.row_id for r in res.rows) == sorted(f"{CRM}:tickets:{t}" for t in kept)
    by_id = {r.row_id.rsplit(":", 1)[1]: r for r in res.rows}
    want = screen_mod.apply_masks(ROWS[1]["amount"], cfg.redactions)[0]
    assert want != ROWS[1]["amount"] and by_id["T-2"].cells["amount"].value == want
    # only the masked, kept rows reach the capture store
    blob = _captured(tmp_path / "cap")
    assert blob and PII not in blob and SECRET not in blob and FLAG not in blob
    assert want in blob
    # withheld rows are recorded, with their decision and rule
    joined = " ".join(res.coverage.excluded)
    for d in set(withheld.values()):
        assert d in joined
    assert "test_secret" in joined and "test_flag" in joined


def test_live_read_fails_closed_without_a_screen(world, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise screen_mod.ScreenError("no screen")

    monkeypatch.setattr(screen_mod, "load_screen", boom)
    entry = world.service().catalogue.agent("crm_sources").connections[0]
    with pytest.raises(DelegateError, match="screen"):
        run_live(_task(), entry, gateway=_Gateway(ROWS[:1]), today=TODAY, principal="x",
                 run_id=None, capture_root=tmp_path / "cap")
    assert not _captured(tmp_path / "cap")                                  # nothing captured
