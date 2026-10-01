"""databridge_harness — the single model abstraction (LiteLLM wrapper).

The ONLY package allowed to import a vendor SDK (P2 / ADR-0002), and the choke point
where the airgapped profile is enforced (P1). Import the public surface from here.
"""
from __future__ import annotations

from .backends import (
    CAPABILITIES,
    Backend,
    Caps,
    LiteLLMBackend,
    RawCompletion,
    caps_for,
    is_hosted,
    provider_of,
)
from .client import (
    AirgappedRefusal,
    BudgetExceeded,
    Harness,
    HarnessError,
    Message,
    StructuredOutputError,
    StructuredResult,
    TextResult,
)
from .embedding import (
    OFFLINE_EMBED_DIM,
    OFFLINE_EMBED_MODEL,
    OFFLINE_LABEL,
    EmbeddingResult,
    RawEmbedding,
    embedding_key_present,
    hashing_embed,
)
from .testing import FakeBackend, valid_json
from .tracking import build_event

__all__ = [
    # client
    "Harness",
    "StructuredResult",
    "TextResult",
    "Message",
    "HarnessError",
    "AirgappedRefusal",
    "BudgetExceeded",
    "StructuredOutputError",
    # backends
    "Caps",
    "CAPABILITIES",
    "Backend",
    "RawCompletion",
    "LiteLLMBackend",
    "caps_for",
    "is_hosted",
    "provider_of",
    # testing
    "FakeBackend",
    "valid_json",
    # tracking
    "build_event",
    # embeddings (task_class: embed)
    "EmbeddingResult",
    "RawEmbedding",
    "hashing_embed",
    "embedding_key_present",
    "OFFLINE_EMBED_MODEL",
    "OFFLINE_EMBED_DIM",
    "OFFLINE_LABEL",
]
