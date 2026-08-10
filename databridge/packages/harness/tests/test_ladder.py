"""The L1/L2/L3 structured-output ladder resolves per call from the capability table."""
from __future__ import annotations

import pytest

from databridge_harness import FakeBackend, Harness

MSGS = [{"role": "user", "content": "classify"}]


def test_l1_clean_first_parse(out, cfg, valid_str):
    # openai task: no guided decoding → first valid parse is L1, zero retries
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    res = h.complete_structured("oai_task", MSGS, out)
    assert res.ladder_used == "L1"
    assert res.retries == 0
    assert res.parsed.label == "x" and res.parsed.score == 1
    assert fake.call_count == 1
    assert res.event.ladder_used == "L1" and res.event.retries == 0


def test_l2_retry_feeds_validation_error_back(out, cfg, valid_str, invalid_str):
    # anthropic task, scripted invalid→valid → one retry → L2
    fake = FakeBackend([invalid_str, valid_str])
    h = Harness(fake, config=cfg, profile="cloud")
    res = h.complete_structured("anth_task", MSGS, out, max_retries=2)
    assert res.ladder_used == "L2"
    assert res.retries == 1
    assert fake.call_count == 2
    # the retry message carried the validation feedback
    retry_msgs = fake.calls[1]["messages"]
    assert "failed validation" in retry_msgs[-1]["content"]


def test_l3_guided_decoding_on_vllm(out, cfg, valid_str):
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    res = h.complete_structured("vllm_task", MSGS, out, strict=True)
    assert res.ladder_used == "L3"
    # the backend received the JSON schema as a grammar constraint
    assert fake.calls[0]["guided_json"] is not None


def test_l3_with_retry_reports_l2_not_l3(out, cfg, valid_str, invalid_str):
    # A guided (L3) call that nonetheless needs a retry must report L2 — the retry is the
    # regression signal ladder_used exists to surface; it must not be hidden behind "L3".
    fake = FakeBackend([invalid_str, valid_str])
    h = Harness(fake, config=cfg, profile="cloud")
    res = h.complete_structured("vllm_task", MSGS, out, strict=True)
    assert res.retries == 1
    assert res.ladder_used == "L2"


def test_strict_false_skips_l3(out, cfg, valid_str):
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    res = h.complete_structured("vllm_task", MSGS, out, strict=False)
    assert res.ladder_used == "L1"
    assert fake.calls[0]["guided_json"] is None


def test_exhausted_retries_raise(out, cfg, invalid_str):
    from databridge_harness import StructuredOutputError
    fake = FakeBackend([invalid_str, invalid_str, invalid_str, invalid_str])
    h = Harness(fake, config=cfg, profile="cloud")
    with pytest.raises(StructuredOutputError):
        h.complete_structured("anth_task", MSGS, out, max_retries=2)
    assert fake.call_count == 3          # initial + 2 retries


def test_one_event_per_logical_call(out, cfg, invalid_str, valid_str):
    # failure-mode 4: the harness emits events, not call sites — an L2 call (2 backend
    # hits) still yields exactly ONE event.
    fake = FakeBackend([invalid_str, valid_str])
    h = Harness(fake, config=cfg, profile="cloud")
    res = h.complete_structured("anth_task", MSGS, out)
    assert fake.call_count == 2
    assert res.event is not None and res.event.tokens == 20   # 2 calls × (5+5)
