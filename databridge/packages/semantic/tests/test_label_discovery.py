"""Label-candidate plausibility checks (06_PRD §5)."""
from __future__ import annotations

from databridge_ingest import fintech_fixture
from databridge_semantic import plausibility_check
from databridge_semantic.label_discovery import _LabelGuess


def test_empty_derivation_hard_fails():
    g = _LabelGuess(name="x", derivation_sql="", source_tables=["users"])
    ok, _ = plausibility_check(g, fintech_fixture())
    assert not ok


def test_unknown_table_hard_fails():
    g = _LabelGuess(name="x", derivation_sql="SELECT id FROM ghosts", source_tables=["ghosts"])
    ok, reasons = plausibility_check(g, fintech_fixture())
    assert not ok
    assert any("unknown" in r for r in reasons)


def test_high_positive_rate_is_a_caveat_not_a_failure():
    g = _LabelGuess(name="fraud", derivation_sql="SELECT id FROM transactions",
                    source_tables=["transactions"], positive_rate_estimate=0.45)
    ok, reasons = plausibility_check(g, fintech_fixture())
    assert ok                                          # in range → kept, but flagged
    assert any("implausibly" in r for r in reasons)


def test_good_candidate_passes_clean():
    g = _LabelGuess(
        name="fraud",
        derivation_sql="SELECT t.id FROM transactions t JOIN disputes d "
                       "ON d.transaction_id = t.id WHERE d.reason = 'fraud'",
        source_tables=["transactions", "disputes"], positive_rate_estimate=0.04)
    ok, reasons = plausibility_check(g, fintech_fixture())
    assert ok
    assert not any("implausibly" in r for r in reasons)
