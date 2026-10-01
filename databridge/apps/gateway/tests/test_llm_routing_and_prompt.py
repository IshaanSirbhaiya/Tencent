"""`llm.py` — pack-neutral system prompt (E2E F-04) and config-honouring routing (E2E F-21).

F-04: `/brain/query` was a 500 under `DATABRIDGE_PACKS=workspace` because the system prompt was
read from `databridge_intake.assistant_config`, which raises `PackConfigError` when no enabled
pack ships a `config/` directory. The prompt import sits OUTSIDE the handler's fallback `try`, so
the whole route failed. A pack with no assistant config now gets a generic, pack-agnostic prompt;
a pack WITH one gets exactly its own prompt; a pack whose assistant config is BROKEN still fails
loudly (a silent fallback would drop its refusal rules without anyone noticing).

F-21: `build_extraction_harness` remapped the `sonnet` alias to the detected OpenAI model even
when `ANTHROPIC_API_KEY` was present, so extraction ran on openai/gpt-4o instead of the configured
anthropic model. Config routing now wins whenever the routed provider's key exists; the fallback
applies only when it does not, and is recorded in `detect_llm()["routing"]`.

All offline: keys are fake strings, and no harness here is ever asked to complete anything except
the FakeBackend-shaped stubs.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import AUTH_HEADERS


# --------------------------------------------------------------------------- F-04: prompt
@pytest.fixture()
def workspace_env(monkeypatch):
    """Bind the real `workspace` pack for both the graph vocabulary and the intake pack loader."""
    from databridge_graph.packs import clear_caches
    from databridge_intake.assistant import assistant_config

    monkeypatch.setenv("DATABRIDGE_PACKS", "workspace")
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    clear_caches()
    assistant_config.cache_clear()
    yield
    clear_caches()
    assistant_config.cache_clear()


def _enabled_packs_ship_an_assistant() -> bool:
    from databridge_intake.pack import enabled_packs, packs_root

    return any((packs_root() / p / "config" / "assistant.yaml").is_file() for p in enabled_packs())


def test_system_prompt_under_a_pack_with_no_assistant_config_is_generic(workspace_env):
    from databridge_gateway import llm

    assert not _enabled_packs_ship_an_assistant(), "workspace grew an assistant config — vacuous"
    prompt = llm.SYSTEM_PROMPT
    # The platform prompt, plus the bound packs' attestation lines (Codex f1 #6) — no pack text.
    assert prompt == llm.compose_system_prompt(None)
    assert prompt.startswith(llm.GENERIC_SYSTEM_PROMPT)
    assert llm.PACK_GUIDANCE_OPEN not in prompt


def test_the_generic_prompt_names_no_pack_vocabulary():
    """Pack-agnostic means it: no node kind of ANY pack on disk appears in it as a word, except
    the meta-model's own words which every pack is allowed to reuse."""
    import re

    from databridge_gateway.llm import GENERIC_SYSTEM_PROMPT
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
    from databridge_graph.packs import load_pack
    from databridge_intake.pack import packs_root

    meta_words = {m.value for m in NodeMetaKind} | {m.value for m in EdgeMetaKind}
    kinds: set[str] = set()
    for d in packs_root().iterdir():
        if (d / "ontology.yaml").is_file():
            kinds |= {k for k in load_pack(d.name).node_kinds if k not in meta_words}
    assert kinds, "no pack kinds were found — the check below is vacuous"
    words = set(re.findall(r"[a-z_]+", GENERIC_SYSTEM_PROMPT.lower()))
    assert not (kinds & words), sorted(kinds & words)


def test_the_generic_prompt_keeps_the_identity_and_grounding_rules():
    from databridge_gateway.llm import GENERIC_SYSTEM_PROMPT

    text = GENERIC_SYSTEM_PROMPT.lower()
    assert "only" in text and "context" in text          # answer only from the given context
    assert "identity" in text and "verif" in text         # hard constraint 1, pack-neutral


def test_system_prompt_under_the_clinic_pack_is_the_platform_prompt(monkeypatch):
    """Codex f2 #2 (superseding f1 #5): the pack's prompt is read — a broken one still fails —
    but none of its prose is placed in the system message."""
    from databridge_gateway import llm
    from databridge_intake.assistant import assistant_config

    monkeypatch.delenv("DATABRIDGE_PACKS", raising=False)
    assistant_config.cache_clear()
    try:
        if not _enabled_packs_ship_an_assistant():
            pytest.skip("the default deployment enables no pack with an assistant config")
        assert llm.SYSTEM_PROMPT == llm.compose_system_prompt()
        assert llm.PACK_GUIDANCE_OPEN not in llm.SYSTEM_PROMPT
        assert llm.SYSTEM_PROMPT.startswith(llm.GENERIC_SYSTEM_PROMPT)
    finally:
        assistant_config.cache_clear()


def test_a_broken_assistant_config_still_fails_loudly(tmp_path, monkeypatch):
    """The fallback is for a pack that ships NO assistant; one that ships a bad one must not be
    quietly swapped for the generic prompt (its refusal rules would vanish unnoticed)."""
    from databridge_gateway import llm
    from databridge_intake.assistant import assistant_config

    root = tmp_path / "packs"
    (root / "brokenpack" / "config").mkdir(parents=True)
    (root / "brokenpack" / "config" / "assistant.yaml").write_text(
        "version: 1\npersona: x\nrefusal_rules: []\n", encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
    monkeypatch.setenv("DATABRIDGE_PACKS", "brokenpack")
    monkeypatch.delenv("DATABRIDGE_PACK", raising=False)
    assistant_config.cache_clear()
    try:
        with pytest.raises(Exception):
            llm._system_prompt()
    finally:
        assistant_config.cache_clear()


def test_brain_query_answers_under_the_workspace_pack(workspace_env, tmp_path, monkeypatch):
    """F-04 end to end: the keyed branch of `/brain/query` reaches the model with a system
    prompt, instead of a 500 from the prompt import."""
    from fastapi.testclient import TestClient

    from databridge_core import SourcePointer
    from databridge_gateway import create_app
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import active_vocabulary

    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(tmp_path / "graph.db"))
    monkeypatch.delenv("WALNUT_LOAD_CLINIC_SEED", raising=False)
    t0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    label = "Quillfeather Logistics"
    org = active_vocabulary().nodes_binding_to(NodeMetaKind.ORGANISATION)[0]

    class _Res:
        def __init__(self, text):
            self.text = text

    class _Stub:
        def __init__(self):
            self.messages = None

        def complete_text(self, task, messages):
            self.messages = messages
            return _Res(f"{label} is in the graph.")

    with TestClient(create_app()) as c:
        ptr = SourcePointer(source_id="s", kind="doc_span", document_id="d", char_start=0,
                            char_end=len(label), capture_digest="cd" * 32)
        # BM25 needs a corpus: with one node every term is in every document and scores <= 0.
        for i, name in enumerate((label, "Marrowgate Analytics", "Tessellate Foundry",
                                  "Harbour Lantern Works")):
            c.app.state.brain.store.add_node(GraphNode(
                id=f"{org}:n{i}", kind=NodeKind(org), label=name, sources=[ptr],
                valid_from=t0, system_from=t0))
        stub = _Stub()
        monkeypatch.setattr(c.app.state, "brain_llm", stub, raising=False)
        r = c.post("/brain/query", json={"question": f"Tell me about {label}"},
                   headers=AUTH_HEADERS)
        assert r.status_code == 200, r.text
        assert stub.messages is not None, "the model branch was never reached"
        from databridge_gateway.llm import GENERIC_SYSTEM_PROMPT

        from databridge_gateway.llm import compose_system_prompt

        assert stub.messages[0] == {"role": "system", "content": compose_system_prompt(None)}
        assert stub.messages[0]["content"].startswith(GENERIC_SYSTEM_PROMPT)
        assert r.json()["is_real_llm"] is True
        g = c.get("/brain/graph", headers=AUTH_HEADERS)
        assert g.status_code == 200, g.text                  # F-05 through the gateway


# --------------------------------------------------------------------------- F-21: routing
def _cfg():
    from databridge_core.config import load_model_config

    return load_model_config()


def _alias(name):
    return _cfg()["aliases"][name]["model"]


def _provider(model):
    return model.split("/", 1)[0]


def _keys(monkeypatch, *, openai: bool, anthropic: bool):
    monkeypatch.delenv("DATABRIDGE_FORCE_GOLD", raising=False)
    for env, on in (("OPENAI_API_KEY", openai), ("ANTHROPIC_API_KEY", anthropic)):
        if on:
            monkeypatch.setenv(env, "sk-fake-not-used")
        else:
            monkeypatch.delenv(env, raising=False)


def _routed_alias(task_class: str) -> str:
    return _cfg()["routing"][task_class]


@pytest.mark.parametrize("task_class", ["intake_extraction", "entity_extraction",
                                        "schema_annotation"])
def test_extraction_honours_config_routing_when_the_routed_key_is_present(monkeypatch,
                                                                         task_class):
    """F-21 reproduction: both keys present, and extraction must run on what models.yaml routes
    (not on the detected brain-query model)."""
    from databridge_gateway.llm import build_extraction_harness, detect_llm

    _keys(monkeypatch, openai=True, anthropic=True)
    configured = _alias(_routed_alias(task_class))
    h = build_extraction_harness(detect_llm())
    assert h is not None
    assert h._resolve(task_class) == configured


def test_extraction_falls_back_only_when_the_routed_key_is_absent_and_says_so(monkeypatch):
    from databridge_gateway.llm import build_extraction_harness, detect_llm

    alias = _routed_alias("intake_extraction")
    configured = _alias(alias)
    # Hold ONLY the key the configured model does NOT use, so its provider is unreachable.
    other = {"openai": "anthropic", "anthropic": "openai"}.get(_provider(configured))
    if other is None:
        pytest.skip(f"configured {configured!r} is not a keyed provider this test can starve")
    _keys(monkeypatch, openai=other == "openai", anthropic=other == "anthropic")
    d = detect_llm()
    h = build_extraction_harness(d)
    assert h._resolve("intake_extraction") == d["model"] != configured
    route = d["routing"][alias]
    assert {k: route[k] for k in ("configured", "effective", "fallback")} == \
        {"configured": configured, "effective": d["model"], "fallback": True}
    assert route["reason"]                                  # the fallback says why (f2 #7)


def test_routing_is_recorded_when_honoured(monkeypatch):
    from databridge_gateway.llm import detect_llm

    _keys(monkeypatch, openai=True, anthropic=True)
    alias = _routed_alias("intake_extraction")
    route = detect_llm()["routing"][alias]
    assert {k: route[k] for k in ("configured", "effective", "fallback")} == \
        {"configured": _alias(alias), "effective": _alias(alias), "fallback": False}


def test_anthropic_only_brain_model_comes_from_config_not_a_literal(monkeypatch):
    """`detect_llm` used to hardcode `anthropic/claude-sonnet-4-6` for an Anthropic-only
    deployment while models.yaml had moved on; the model it reports must be the configured one."""
    from databridge_gateway.llm import build_query_harness, detect_llm

    _keys(monkeypatch, openai=False, anthropic=True)
    d = detect_llm()
    reachable = [m for a, m in ((a, v.get("model")) for a, v in _cfg()["aliases"].items())
                 if a.startswith(_routed_alias("brain_query")) and m
                 and _provider(m) == "anthropic"]
    assert reachable, "models.yaml names no anthropic brain-query alternative — vacuous"
    assert d["backend"] == "anthropic" and d["model"] == reachable[0]
    assert build_query_harness(d)._resolve("brain_query") == d["model"]


def test_no_key_is_still_the_offline_backend(monkeypatch):
    from databridge_gateway.llm import build_extraction_harness, detect_llm

    _keys(monkeypatch, openai=False, anthropic=False)
    d = detect_llm()
    assert d["backend"] == "fake" and build_extraction_harness(d) is None
