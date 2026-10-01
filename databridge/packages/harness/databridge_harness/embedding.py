"""Embeddings through the harness — ``task_class: embed`` (BUILD_PLAN §5.5, principles P14).

Same rule as every other model call: a call site names a *task class*, never a model, and the
routing table (``config/models.yaml``) decides. This module holds the pieces the ``Harness.embed``
method is built from:

* ``RawEmbedding`` — what a backend's ``embed()`` returns (the embedding twin of ``RawCompletion``).
* ``hashing_embed`` — the **offline deterministic fallback**: a signed hashing vectorizer over word
  unigrams and bigrams, L2-normalised. It is *lexical*, not semantic: two texts are close when
  they share words, not when they mean the same thing. That difference is why every result it
  produces is **labelled** (``OFFLINE_LABEL``) — a reader must never mistake a word-overlap score
  for a semantic one. It exists so tests and an offline demo work with no key and no network.
* ``EmbeddingResult`` — vectors plus the model that made them, whether that was the offline
  fallback, and **measured** throughput for the call (P17: no figure is quoted unless measured).
* ``embedding_key_present`` — whether the provider a model id routes to has its credential in the
  environment. Resolved per call, never at import.

Deliberately dependency-free (no numpy): the harness must import in a bare environment; the
consumer (``packages/retrieval``) packs the vectors into float32 itself.
"""
from __future__ import annotations

import math
import os
import re
import zlib
from dataclasses import dataclass, field
from typing import Optional

from databridge_core import PipelineEvent

#: Model id recorded against vectors made by the offline fallback. The ``offline/`` provider
#: prefix is not a LiteLLM provider, so nothing can ever route a real call to it by accident.
OFFLINE_EMBED_MODEL = "offline/hashing-v1"
OFFLINE_EMBED_DIM = 384
OFFLINE_LABEL = ("offline deterministic hashing embedding (no embedding key configured): "
                 "lexical word-overlap similarity, not semantic")

#: Env switch that forces the offline fallback even when a key is present (demos, CI).
FORCE_OFFLINE_ENV = "WALNUT_EMBED_OFFLINE"

#: Credential env vars per LiteLLM provider prefix. A provider absent from this table is assumed
#: to be local (ollama, hosted_vllm, ...) and needs no key. ``anthropic`` has no embeddings API, so
#: it maps to nothing that could ever be present.
_PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "azure": ("AZURE_API_KEY", "AZURE_OPENAI_API_KEY"),
    "cohere": ("COHERE_API_KEY",),
    "voyage": ("VOYAGE_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
    "vertex_ai": ("GOOGLE_APPLICATION_CREDENTIALS",),
    "bedrock": ("AWS_ACCESS_KEY_ID",),
    "anthropic": (),          # no embeddings endpoint: never "present"
    "offline": (),
}

_WORD = re.compile(r"[0-9a-z]+(?:[-_.][0-9a-z]+)*")


@dataclass(frozen=True)
class RawEmbedding:
    """What a backend's ``embed()`` returns for one batch."""

    vectors: list[list[float]]
    tokens: int = 0
    cost_usd: float = 0.0
    model_used: str = ""


@dataclass(frozen=True)
class EmbeddingResult:
    """Vectors for every input text, in input order, plus how they were made."""

    vectors: list[list[float]]
    model: str
    dim: int
    offline: bool
    label: Optional[str]                  # OFFLINE_LABEL when offline, else None
    tokens: int
    cost_usd: float
    duration_ms: float
    batches: int
    workers: int
    texts_per_second: Optional[float]     # measured over this call; None when nothing was timed
    chars_per_second: Optional[float]
    events: list[PipelineEvent] = field(default_factory=list)


def force_offline() -> bool:
    return os.environ.get(FORCE_OFFLINE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def embedding_key_present(model: str) -> bool:
    """True if the provider ``model`` routes to has a credential in the environment (or needs none)."""
    provider = model.split("/", 1)[0]
    if provider not in _PROVIDER_KEYS:
        return True                        # local provider (ollama, hosted_vllm, ...)
    return any(os.environ.get(name) for name in _PROVIDER_KEYS[provider])


def _features(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    feats = list(words)
    feats.extend(f"{a} {b}" for a, b in zip(words, words[1:]))
    return feats


def hashing_embed(texts: list[str], dim: int = OFFLINE_EMBED_DIM) -> list[list[float]]:
    """Signed feature hashing (CRC32 — stable across processes, unlike ``hash()``), L2-normalised.

    An empty or wordless text gets the zero vector; a consumer normalising it must guard zero norm.
    """
    out: list[list[float]] = []
    for text in texts:
        vec = [0.0] * dim
        for feat in _features(text):
            h = zlib.crc32(feat.encode("utf-8"))
            vec[h % dim] += 1.0 if (h >> 31) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        out.append(vec)
    return out


__all__ = ["RawEmbedding", "EmbeddingResult", "hashing_embed", "embedding_key_present",
           "force_offline", "OFFLINE_EMBED_MODEL", "OFFLINE_EMBED_DIM", "OFFLINE_LABEL",
           "FORCE_OFFLINE_ENV"]
