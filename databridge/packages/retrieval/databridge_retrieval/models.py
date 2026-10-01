"""Public result types. ``Passage`` is the frozen §5.5 contract — do not add or rename fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from databridge_core.provenance import SourcePointer


class Passage(BaseModel):
    """BUILD_PLAN §5.5: ``{chunk_id, doc_id, source_id, text, locator, approx_locator, score,
    pointer}``. ``pointer`` is a ``doc_span`` over the capture ``.txt`` of ``capture_digest``."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    source_id: str
    text: str
    locator: str
    approx_locator: bool
    score: float
    pointer: SourcePointer

    def __getitem__(self, key: str) -> Any:          # dict-style access for {…}-shaped callers
        return getattr(self, key)


class SearchResults(list):
    """``list[Passage]`` (the contract) that also says HOW it was produced.

    ``offline_embedding`` / ``embedding_label`` carry the offline-fallback label (§5.5: results
    made with the deterministic local embedding are labelled as such). ``coverage`` lists which
    stores were searched and which were skipped and why (P7: partial coverage is visible).
    """

    def __init__(self, items=(), *, query: str = "", mode: str = "hybrid",
                 depth: str = "standard", embedding_model: Optional[str] = None,
                 offline_embedding: bool = False, embedding_label: Optional[str] = None,
                 coverage: Optional[dict] = None, notices: Optional[list[str]] = None) -> None:
        super().__init__(items)
        self.query = query
        self.mode = mode
        self.depth = depth
        self.embedding_model = embedding_model
        self.offline_embedding = offline_embedding
        self.embedding_label = embedding_label
        self.coverage = coverage or {"searched": [], "skipped": []}
        self.notices = notices or []

    def describe(self) -> dict:
        return {"query": self.query, "mode": self.mode, "depth": self.depth,
                "embedding_model": self.embedding_model,
                "offline_embedding": self.offline_embedding,
                "embedding_label": self.embedding_label, "coverage": self.coverage,
                "notices": self.notices, "count": len(self)}


class RetrievalError(RuntimeError):
    """Base class."""


class PublishRefused(RetrievalError):
    """P11: the build would publish an empty store, or one that shrank by more than half."""


class BuildInProgress(RetrievalError):
    """Another build holds this store's lock (single-writer rule)."""


@dataclass
class DocIndexResult:
    doc_id: str
    status: str                 # indexed | unchanged | empty_text | missing_capture | removed
    #                             | missing_path | identity_mismatch | capture_mismatch (refusals)
    chunks: int = 0
    excluded: int = 0
    reused_vectors: int = 0
    detail: str = ""


@dataclass
class PublishReport:
    store: str
    source_id: str
    tier: str
    published: bool
    documents: int
    chunks: int
    embeddings: int
    excluded_chunks: int
    previous_documents: Optional[int]
    centroids: int
    embedding_model: Optional[str]
    offline_embedding: bool
    embedding_label: Optional[str]
    throughput: dict = field(default_factory=dict)
    search_ram_bytes: Optional[int] = None
    search_ram_mb: Optional[float] = None
    reasons: list[str] = field(default_factory=list)
    #: documents dropped at publication because their pointers could not resolve (capture gone)
    dropped: list[dict] = field(default_factory=list)
