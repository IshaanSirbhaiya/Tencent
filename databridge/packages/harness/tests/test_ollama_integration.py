"""Integration: the SAME calling code yields valid structured output from a LOCAL model.

This is the 07_PRD §2 / Task 2 Done-when for the local backend (P2 proof). It runs for
real where Ollama + LiteLLM are available, and SKIPS gracefully otherwise — the
assertion is never weakened.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Literal

import pytest
from pydantic import BaseModel

from databridge_harness import Harness


def _ollama_models() -> list[str]:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
        return [m["name"] for m in json.load(r).get("models", [])]


class Sentiment(BaseModel):
    sentiment: Literal["positive", "negative", "neutral"]


@pytest.mark.integration
def test_local_model_produces_valid_structured_output():
    pytest.importorskip("litellm", reason="litellm not installed")
    from databridge_harness import LiteLLMBackend

    try:
        models = _ollama_models()
    except Exception as e:                                    # noqa: BLE001
        pytest.skip(f"Ollama not reachable on :11434 ({e})")
    model = next((m for m in models if m.lower().startswith("qwen")), None)
    if model is None:
        pytest.skip(f"no qwen model pulled in Ollama (have: {models})")

    cfg = {
        "routing": {"smoke": "local"},
        "aliases": {"local": {"model": f"ollama_chat/{model}"}},
        "profiles": {"cloud": {}, "airgapped": {}},
    }
    # airgapped profile + a local model: no refusal, no egress.
    h = Harness(LiteLLMBackend(), config=cfg, profile="airgapped", cache=None)
    res = h.complete_structured(
        "smoke",
        [{"role": "user",
          "content": "Classify the sentiment of this review: 'I absolutely love this "
                     "product, it works great!'. Reply ONLY with JSON of the form "
                     '{"sentiment": "positive"|"negative"|"neutral"}.'}],
        Sentiment,
        strict=True,
        max_retries=2,
    )
    assert res.parsed.sentiment in ("positive", "negative", "neutral")
    assert res.event.model_used == f"ollama_chat/{model}"
    assert res.event.tokens and res.event.tokens > 0
