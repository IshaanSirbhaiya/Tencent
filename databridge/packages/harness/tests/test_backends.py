"""The capability table is EXACTLY 07_PRD §3, and caps lookup is by provider prefix."""
from __future__ import annotations

from databridge_harness.backends import CAPABILITIES, caps_for, is_hosted, provider_of


def test_capability_table_matches_07prd_section_3():
    a = CAPABILITIES["anthropic/*"]
    assert (a.json_mode, a.strict_schema, a.tools, a.guided_decoding, a.seed) == \
        (False, False, True, False, False)
    o = CAPABILITIES["openai/*"]
    assert (o.json_mode, o.strict_schema, o.tools, o.guided_decoding, o.seed) == \
        (True, True, True, False, True)
    ol = CAPABILITIES["ollama_chat/*"]
    assert (ol.json_mode, ol.strict_schema, ol.tools, ol.guided_decoding, ol.seed) == \
        (True, False, "varies", "format_json", True)
    v = CAPABILITIES["hosted_vllm/*"]
    assert (v.json_mode, v.strict_schema, v.tools, v.guided_decoding, v.seed) == \
        (True, True, True, "guided_json", True)


def test_only_vllm_and_ollama_have_guided_decoding():
    assert caps_for("hosted_vllm/x").has_guided_decoding is True
    assert caps_for("ollama_chat/x").has_guided_decoding is True
    assert caps_for("anthropic/x").has_guided_decoding is False
    assert caps_for("openai/x").has_guided_decoding is False


def test_unknown_provider_is_conservative():
    c = caps_for("mystery/model")
    assert c.json_mode is False and c.has_guided_decoding is False


def test_is_hosted_matches_perimeter_rule():
    assert is_hosted("anthropic/claude") is True
    assert is_hosted("openai/gpt") is True
    assert is_hosted("ollama_chat/qwen") is False     # local
    assert is_hosted("hosted_vllm/qwen") is False      # local-served
    assert provider_of("ollama_chat/qwen3:72b") == "ollama_chat"
