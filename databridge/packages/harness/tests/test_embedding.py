"""``Harness.embed`` — task-class routing, the LABELLED offline fallback, batching, caching.

No test here calls a live model or the network: the LiteLLM backend is only ever exercised in the
branch where its provider has no key, which never reaches LiteLLM.
"""
from __future__ import annotations

import math

import pytest

from databridge_harness import (
    OFFLINE_EMBED_DIM,
    OFFLINE_EMBED_MODEL,
    OFFLINE_LABEL,
    AirgappedRefusal,
    FakeBackend,
    Harness,
    LiteLLMBackend,
    hashing_embed,
)

EMBED_CFG = {
    "routing": {"embed": "emb", "leak_embed": "leak"},
    "aliases": {"emb": {"model": "openai/text-embedding-x"},
                "leak": {"model": "openai/text-embedding-leak"}},
    "profiles": {"cloud": {}, "airgapped": {"emb": {"model": "ollama/nomic-embed-text"}}},
}


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def test_hashing_embed_is_deterministic_normalised_and_lexical():
    texts = ["invoice INV-7 is overdue", "invoice INV-7 is overdue", "the weather is mild"]
    a, b, c = hashing_embed(texts)
    assert a == b
    assert len(a) == OFFLINE_EMBED_DIM
    assert abs(_norm(a) - 1.0) < 1e-9
    same = sum(x * y for x, y in zip(a, b))
    other = sum(x * y for x, y in zip(a, c))
    assert same > other
    assert hashing_embed([""])[0] == [0.0] * OFFLINE_EMBED_DIM   # zero vector, not NaN


def test_no_key_uses_offline_fallback_and_labels_it(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WALNUT_EMBED_OFFLINE", raising=False)
    h = Harness(LiteLLMBackend(), config=EMBED_CFG, profile="cloud")
    res = h.embed(["alpha beta", "gamma"])
    assert res.offline is True
    assert res.label == OFFLINE_LABEL and "offline" in res.label
    assert res.model == OFFLINE_EMBED_MODEL
    assert len(res.vectors) == 2 and res.dim == OFFLINE_EMBED_DIM


def test_backend_without_embed_falls_back_offline():
    class NoEmbed:
        name = "gold"

        def generate(self, **kw):  # pragma: no cover - never called
            raise AssertionError

    res = Harness(NoEmbed(), config=EMBED_CFG, profile="cloud").embed(["x y"])
    assert res.offline is True and res.label == OFFLINE_LABEL


def test_force_offline_env(monkeypatch):
    monkeypatch.setenv("WALNUT_EMBED_OFFLINE", "1")
    fake = FakeBackend()
    res = Harness(fake, config=EMBED_CFG, profile="cloud").embed(["x"])
    assert res.offline is True and fake.embed_calls == []


def test_fake_backend_routes_by_task_class_batches_and_is_not_labelled_offline(monkeypatch):
    monkeypatch.delenv("WALNUT_EMBED_OFFLINE", raising=False)
    fake = FakeBackend()
    texts = [f"text number {i}" for i in range(10)]
    res = Harness(fake, config=EMBED_CFG, profile="cloud").embed(texts, batch_size=3, workers=4)
    assert res.offline is False and res.label is None
    assert res.model == "openai/text-embedding-x"
    expected_batches = math.ceil(len(texts) / 3)
    assert len(fake.embed_calls) == expected_batches == res.batches
    assert {c["model"] for c in fake.embed_calls} == {"openai/text-embedding-x"}
    # order preserved across parallel batches
    assert res.vectors == hashing_embed(texts)
    assert res.texts_per_second is not None and res.texts_per_second > 0
    assert len(res.events) == expected_batches


def test_cache_skips_already_embedded_texts(monkeypatch):
    monkeypatch.delenv("WALNUT_EMBED_OFFLINE", raising=False)
    fake = FakeBackend()
    h = Harness(fake, config=EMBED_CFG, profile="cloud")
    h.embed(["a b", "c d"])
    n = len(fake.embed_calls)
    res = h.embed(["a b", "c d", "e f"])
    assert len(fake.embed_calls) == n + 1
    assert fake.embed_calls[-1]["inputs"] == ["e f"]
    assert len(res.vectors) == 3


def test_airgapped_hosted_embedding_refuses_loudly():
    h = Harness(FakeBackend(), config=EMBED_CFG, profile="airgapped")
    with pytest.raises(AirgappedRefusal):
        h.embed(["x"], task_class="leak_embed")
    # the remapped route is local and works
    assert h.embed(["x"]).model == "ollama/nomic-embed-text"


def test_repo_routing_table_has_embed_route():
    from databridge_core.config import load_model_config, resolve_model

    assert "embed" in load_model_config()["routing"]
    from databridge_harness import is_hosted
    assert not is_hosted(resolve_model("embed", profile="airgapped"))
