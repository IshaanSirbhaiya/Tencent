"""Tests for model routing resolution — cloud vs airgapped, fail-loud on bad input."""
from __future__ import annotations

import pytest

from databridge_core.config import (
    ConfigError,
    UnknownTaskClass,
    active_profile,
    resolve_model,
)


def test_resolution_differs_cloud_vs_airgapped():
    # The TASK 1 Done-when: same task_class resolves to different models per profile.
    cloud = resolve_model("schema_annotation", profile="cloud")
    airgapped = resolve_model("schema_annotation", profile="airgapped")
    assert cloud == "anthropic/claude-sonnet-4-6"
    assert airgapped == "ollama_chat/qwen3:72b"
    assert cloud != airgapped


def test_frontier_routes_to_local_when_airgapped():
    assert resolve_model("cross_table_inference", profile="cloud") == "anthropic/claude-fable-5"
    assert resolve_model("cross_table_inference", profile="airgapped") == "ollama_chat/qwen3:72b"


def test_verification_uses_a_different_model_than_generator():
    # adversarial separation (A.4): verification_pass != data_to_text
    assert resolve_model("verification_pass", profile="cloud") == "openai/gpt-4.1"
    assert resolve_model("verification_pass", profile="airgapped") == "ollama_chat/llama3.3:70b"
    assert resolve_model("verification_pass", "cloud") != resolve_model("data_to_text", "cloud")


def test_unknown_task_class_fails_loud():
    with pytest.raises(UnknownTaskClass):
        resolve_model("totally_made_up_task", profile="cloud")


def test_unknown_profile_fails_loud():
    with pytest.raises(ConfigError):
        resolve_model("schema_annotation", profile="does_not_exist")


def test_env_profile_is_honoured(monkeypatch):
    monkeypatch.setenv("DATABRIDGE_PROFILE", "airgapped")
    assert active_profile() == "airgapped"
    # profile=None falls back to the env-derived active profile
    assert resolve_model("schema_annotation") == "ollama_chat/qwen3:72b"


def test_default_profile_is_cloud(monkeypatch):
    monkeypatch.delenv("DATABRIDGE_PROFILE", raising=False)
    assert active_profile() == "cloud"
    assert resolve_model("schema_annotation") == "anthropic/claude-sonnet-4-6"
