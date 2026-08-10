"""Shared fixtures for harness tests.

CFG is an injected routing table (so tests don't depend on the repo's models.yaml). It
deliberately includes a ``leak`` alias that is NOT remapped under the airgapped profile,
so a task routed to it resolves to a HOSTED model under airgapped — the case the P1
refusal must catch.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel


class Out(BaseModel):
    label: str
    score: int


CFG = {
    "routing": {
        "anth_task": "a",      # anthropic  → no guided decoding → L1/L2
        "oai_task": "o",       # openai     → json_mode, strict_schema (no guided) → L1/L2
        "olla_task": "ol",     # ollama     → format_json
        "vllm_task": "v",      # vLLM       → guided_json → L3
        "leak_task": "leak",   # hosted, intentionally unremapped under airgapped
    },
    "aliases": {
        "a": {"model": "anthropic/claude-x"},
        "o": {"model": "openai/gpt-x"},
        "ol": {"model": "ollama_chat/qwen"},
        "v": {"model": "hosted_vllm/qwen"},
        "leak": {"model": "anthropic/claude-leak"},
    },
    "profiles": {
        "cloud": {},
        "airgapped": {"a": "ol", "o": "ol", "v": {"model": "ollama_chat/llama"}},
    },
}


@pytest.fixture
def out() -> type[BaseModel]:
    return Out


@pytest.fixture
def cfg() -> dict:
    return CFG


@pytest.fixture
def valid_str() -> str:
    return '{"label": "x", "score": 1}'


@pytest.fixture
def invalid_str() -> str:
    return '{"label": "x"}'          # missing required `score` → drives an L2 retry
