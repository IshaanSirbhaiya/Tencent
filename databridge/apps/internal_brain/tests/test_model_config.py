"""Which model chain the Internal Brain builds from the environment (no network call is made)."""
from __future__ import annotations

import pytest

from databridge_ib import llm

KEYS = ("TOKENHUB_API_KEY", "TOKENHUB_BASE", "OPENAI_API_KEY", "OPENAI_API_BASE", "IB_MODELS", "IB_FORCE_OFFLINE")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_no_key_means_the_deterministic_path():
    assert llm.ModelChain.tokenhub() is None


def test_tokenhub_key_gives_the_tencent_chain(monkeypatch):
    monkeypatch.setenv("TOKENHUB_API_KEY", "test-key-not-real")
    chain = llm.ModelChain.tokenhub()
    assert chain is not None
    assert list(chain.models) == ["hy3", "deepseek-v4.1-flash"]
    assert chain.provider == "tencent-tokenhub"


def test_own_openai_compatible_endpoint_and_model_list(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")
    monkeypatch.setenv("IB_MODELS", "qwen2.5:7b, llama3.1:8b")
    chain = llm.ModelChain.tokenhub()
    assert chain is not None
    assert list(chain.models) == ["qwen2.5:7b", "llama3.1:8b"]
    assert chain.provider == "openai-compatible"


def test_force_offline_wins_over_any_key(monkeypatch):
    monkeypatch.setenv("TOKENHUB_API_KEY", "test-key-not-real")
    monkeypatch.setenv("IB_FORCE_OFFLINE", "1")
    assert llm.ModelChain.tokenhub() is None


def test_a_key_without_an_endpoint_is_not_enough(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    assert llm.ModelChain.tokenhub() is None
