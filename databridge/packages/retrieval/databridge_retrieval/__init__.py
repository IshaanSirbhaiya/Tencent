"""databridge_retrieval — hybrid, routed, memory-honest passage retrieval (BUILD_PLAN §5.5, §6 G).

Public surface:

    search(query, *, k=8, sources=None, kinds=None, mode="hybrid") -> list[Passage]   (§5.5)
    deep_search(query, *, depth="facets"|"rerank"|"standard", ...)                     depth levels
    index_capture(sha256, meta) -> DocIndexResult                                        one item
    BuildSession(source_id, tier)                                                        many items
    stats() -> list[dict]                                                                router view
    configure(harness=...)                                                               inject harness

``search`` returns ``SearchResults`` — a ``list[Passage]`` that also carries
``offline_embedding`` / ``embedding_label`` (the offline-fallback label) and ``coverage``.
"""
from __future__ import annotations

from .chunker import CHUNK_CHARS, CHUNKER_VERSION, MIN_CHUNK_CHARS, OVERLAP_FRACTION
from .indexer import BuildSession, doc_id_for, index_capture, read_capture
from .models import (
    BuildInProgress,
    DocIndexResult,
    Passage,
    PublishRefused,
    PublishReport,
    RetrievalError,
    SearchResults,
)
from .runtime import configure, get_harness
from .search import Retriever, deep_search, fts_query, rule_facets, search, stats
from .vectors import centroid_count, search_ram_mb

__all__ = [
    "search", "deep_search", "stats", "index_capture", "BuildSession", "Retriever",
    "Passage", "SearchResults", "DocIndexResult", "PublishReport", "PublishRefused",
    "BuildInProgress", "RetrievalError", "configure", "get_harness", "doc_id_for",
    "read_capture", "fts_query", "rule_facets", "centroid_count", "search_ram_mb",
    "CHUNK_CHARS", "OVERLAP_FRACTION", "MIN_CHUNK_CHARS", "CHUNKER_VERSION",
]
