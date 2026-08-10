"""Content-hash caching (07_PRD §5) + budget enforcement (BudgetExceeded)."""
from __future__ import annotations

import pytest

from databridge_harness import BudgetExceeded, FakeBackend, Harness

MSGS = [{"role": "user", "content": "classify"}]


def test_cache_hit_skips_backend(out, cfg, valid_str):
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")           # cache on by default
    r1 = h.complete_structured("oai_task", MSGS, out)
    r2 = h.complete_structured("oai_task", MSGS, out)        # identical → cache hit
    assert fake.call_count == 1
    assert r2.event.cached is True
    assert r1.parsed == r2.parsed


def test_cache_key_includes_inputs(out, cfg, valid_str):
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    h.complete_structured("oai_task", MSGS, out)
    h.complete_structured("oai_task", [{"role": "user", "content": "different"}], out)
    assert fake.call_count == 2                               # different inputs → miss


def test_cache_off_matches_cache_on(out, cfg, valid_str):
    # EV-DET groundwork: cache-off replay equals cache-on result (07_PRD §5).
    on = Harness(FakeBackend(valid_str), config=cfg, profile="cloud")
    off = Harness(FakeBackend(valid_str), config=cfg, profile="cloud", cache=None)
    assert on.complete_structured("oai_task", MSGS, out).parsed == \
        off.complete_structured("oai_task", MSGS, out).parsed


def test_run_budget_enforced(out, cfg, valid_str):
    # FakeBackend reports 10 tokens/call; a 10-token run budget allows exactly one call.
    fake = FakeBackend(valid_str, tokens_in=5, tokens_out=5)
    h = Harness(fake, config=cfg, profile="cloud", run_budget=10, cache=None)
    h.complete_structured("oai_task", MSGS, out)             # ok
    with pytest.raises(BudgetExceeded):
        h.complete_structured("oai_task", MSGS, out)         # budget reached


def test_source_budget_enforced(out, cfg, valid_str):
    fake = FakeBackend(valid_str, tokens_in=5, tokens_out=5)
    h = Harness(fake, config=cfg, profile="cloud",
                source_budgets={"src_a": 10}, cache=None)
    h.complete_structured("oai_task", MSGS, out, source_id="src_a")
    with pytest.raises(BudgetExceeded):
        h.complete_structured("oai_task", MSGS, out, source_id="src_a")
    # a different source is unaffected
    h.complete_structured("oai_task", MSGS, out, source_id="src_b")
