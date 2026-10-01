"""R-019 (Codex batch 2, High): ``drop_params=True`` does not fix a model that rejects a sampling
override — temperature is a supported parameter NAME whose allowed VALUE is restricted, so it
would still be sent. The reviewed capability table omits it instead, and nothing is dropped
globally. ``litellm`` is replaced by a recorder: no network, no key."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import yaml

from databridge_harness.backends import LiteLLMBackend, accepts_sampling_override


@pytest.fixture
def recorder(monkeypatch):
    calls: list[dict] = []
    reply = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))], usage=None)
    fake = types.SimpleNamespace(completion=lambda **kw: calls.append(kw) or reply,
                                 completion_cost=lambda **kw: 0.0)
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return calls


def test_models_that_reject_a_sampling_override_are_sent_none(recorder):
    rejecting = ["anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5",
                 "bedrock/anthropic.claude-opus-5-5"]
    for model in rejecting:
        LiteLLMBackend().generate(model=model, messages=[{"role": "user", "content": "hi"}])
    assert len(recorder) == len(rejecting)
    assert all("temperature" not in c and "drop_params" not in c for c in recorder)


def test_other_models_keep_temperature_zero_and_nothing_is_dropped(recorder):
    accepting = ["openai/gpt-4o-mini", "ollama_chat/llama3.1", "anthropic/claude-3-5-haiku"]
    for model in accepting:
        LiteLLMBackend().generate(model=model, messages=[{"role": "user", "content": "hi"}])
    assert [c["temperature"] for c in recorder] == [0] * len(accepting)
    assert all("drop_params" not in c for c in recorder)


def test_the_configured_claude_5_5_routes_are_covered():
    """Every Claude 5.5 model config/models.yaml names must be in the table, or the live 400
    comes back the day the table and the config drift apart."""
    cfg = yaml.safe_load((Path(__file__).resolve().parents[3] / "config" / "models.yaml")
                         .read_text(encoding="utf-8"))

    def models(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "model" and isinstance(v, str):
                    yield v
                else:
                    yield from models(v)
        elif isinstance(node, list):
            for v in node:
                yield from models(v)

    claude = {m for m in models(cfg) if "claude" in m and "5-5" in m}
    assert claude, "the config names Claude 5.5 routes, so this check is not vacuous"
    assert all(not accepts_sampling_override(m) for m in claude)


# --- R-019 round 3 (Codex gpt-5.6-sol, High #7): a caller kwarg must not re-add the override -------
def test_a_caller_supplied_sampling_override_never_reaches_a_rejecting_model(recorder):
    from databridge_harness.backends import REJECTED_SAMPLING_CONTROLS

    sampling = {name: 0 for name in REJECTED_SAMPLING_CONTROLS}
    assert "temperature" in sampling, "the explicit temperature case is exercised"
    LiteLLMBackend().generate(model="anthropic/claude-opus-5-5",
                              messages=[{"role": "user", "content": "hi"}],
                              max_tokens=16, **sampling)
    (call,) = recorder
    assert not set(call) & set(REJECTED_SAMPLING_CONTROLS)
    assert call["max_tokens"] == 16, "only the rejected controls are removed; the rest pass"
    # a model that accepts an override still gets determinism, not the caller's value
    LiteLLMBackend().generate(model="openai/gpt-4o-mini",
                              messages=[{"role": "user", "content": "hi"}], temperature=0.9)
    assert recorder[-1]["temperature"] == 0
