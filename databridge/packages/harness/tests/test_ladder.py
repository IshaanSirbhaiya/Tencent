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


# --- L1 actually instructs something (D-1) ----------------------------------------


def test_l1_puts_the_schema_in_the_prompt(out, cfg, valid_str):
    """The rung that had no content. `caps_for("openai/*").guided_decoding` is False, so L3 is
    skipped and the backend was sent only `response_format={"type": "json_object"}` — which
    guarantees the reply is syntactically JSON and says nothing about its FIELD NAMES.

    Measured against the live gateway: gpt-4o returned `patient_name`/`patient_id`/`patient_dob`
    where `IntakeDraft` declares `claimed_name`/`claimed_id_number`/`claimed_dob`. Pydantic
    ignores unknown keys, so the model validated cleanly with every field None — no
    ValidationError, therefore no L2 retry, therefore no error anywhere. All ten chits extracted
    to a blank card while `/health` reported a live model."""
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    h.complete_structured("oai_task", MSGS, out)

    sent = fake.calls[0]["messages"]
    schema_text = "".join(m["content"] for m in sent)
    assert "label" in schema_text and "score" in schema_text, sent
    assert "EXACTLY these field names" in schema_text, sent


def test_the_document_stays_the_last_message(out, cfg, valid_str):
    """Ordering is load-bearing, not cosmetic. The fixture-keyed gold backend dispatches on
    `messages[-1]`, so appending the schema after the document made every gold lookup match the
    schema instead — the whole offline suite silently extracted empty drafts. Instructions before
    data is also the ordering that reads correctly to a model."""
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    h.complete_structured("oai_task", MSGS, out)
    assert fake.calls[0]["messages"][-1] == MSGS[-1]


def test_l3_does_not_repeat_the_schema_in_the_prompt(out, cfg, valid_str):
    """Under a token-level grammar the schema is already binding; restating it only costs
    context."""
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    h.complete_structured("vllm_task", MSGS, out, strict=True)
    assert fake.calls[0]["messages"] == MSGS
    assert fake.calls[0]["guided_json"] is not None


def test_seed_is_withheld_from_a_backend_that_declares_it_cannot_take_one(out, cfg, valid_str):
    """`Caps.seed` was declared per provider and then ignored. Anthropic rejects `seed`, so with
    only ANTHROPIC_API_KEY set every structured call raised `UnsupportedParamsError` — which the
    gateway caught and quietly degraded to the deterministic backend, leaving `/health` still
    advertising a live model. A capability that is declared and not honoured is worse than one
    that was never declared."""
    fake = FakeBackend(valid_str)
    h = Harness(fake, config=cfg, profile="cloud")
    h.complete_structured("anth_task", MSGS, out, seed=7)      # anthropic: seed=False
    assert fake.calls[0]["seed"] is None
    h.complete_structured("oai_task", MSGS, out, seed=7)       # openai: seed=True
    assert fake.calls[-1]["seed"] == 7
