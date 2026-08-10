"""Span selection (C15, 09_FINAL_PRD §11.2): retrieve the blocks most relevant to each target
field so the LLM sees only the spans it needs (cheaper + less distraction), with a whole-doc
fallback under a size ceiling.

The spec describes embedding field descriptions + block text and doing top-k retrieval. The
harness exposes no embedding backend and the airgapped default forbids a hosted one, so the
POC uses a DETERMINISTIC lexical scorer (TF-IDF cosine over token sets) behind a pluggable
``Scorer`` seam — swapping in a real embedder later is a one-function change, and the result is
reproducible (no model variance) which the eval relies on. (Deviation recorded in the task file.)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Optional

from ..ir import DocumentRecord
from .target_model import CompiledTarget, FieldSpec

# whole-doc fallback when the doc is small enough that retrieval saves nothing.
WHOLE_DOC_CHAR_CEILING = 6000
DEFAULT_TOP_K = 6

_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


@dataclass
class SelectedSpans:
    block_indices: list[int]            # selected block indices (sorted, reading order)
    whole_doc: bool                     # True ⇒ fell back to the whole document
    text: str                           # the concatenated selected text (for the LLM)


def _tfidf_vectors(docs: list[list[str]]) -> tuple[list[dict], dict]:
    n = len(docs)
    df: Counter = Counter()
    for toks in docs:
        for t in set(toks):
            df[t] += 1
    idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
    vecs = []
    for toks in docs:
        tf = Counter(toks)
        v = {t: (tf[t] / max(1, len(toks))) * idf.get(t, 0.0) for t in tf}
        vecs.append(v)
    return vecs, idf


def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return num / (na * nb) if na and nb else 0.0


Scorer = Callable[[str, list[str]], list[float]]   # (query, block_texts) -> per-block scores


def lexical_scorer(query: str, block_texts: list[str]) -> list[float]:
    """Default deterministic scorer: TF-IDF cosine of the query against each block."""
    corpus = [_tokens(t) for t in block_texts] + [_tokens(query)]
    vecs, _idf = _tfidf_vectors(corpus)
    qv = vecs[-1]
    return [_cosine(qv, vecs[i]) for i in range(len(block_texts))]


def _field_query(fs: FieldSpec) -> str:
    parts = [fs.name.replace("_", " "), fs.description or ""]
    if fs.enum:
        parts.append(" ".join(str(e) for e in fs.enum))
    return " ".join(p for p in parts if p)


def select_spans(doc: DocumentRecord, compiled: CompiledTarget, *,
                 top_k: int = DEFAULT_TOP_K, scorer: Optional[Scorer] = None,
                 char_ceiling: int = WHOLE_DOC_CHAR_CEILING) -> SelectedSpans:
    """Select the blocks most relevant to the target fields. Whole-doc fallback when the doc
    is under the size ceiling (retrieval can only lose signal on a small doc)."""
    block_texts = [b.text for b in doc.blocks]
    total_chars = sum(len(t) for t in block_texts)
    if total_chars <= char_ceiling or len(block_texts) <= top_k:
        idxs = list(range(len(block_texts)))
        return SelectedSpans(block_indices=idxs, whole_doc=True,
                             text="\n".join(block_texts))
    scorer = scorer or lexical_scorer
    chosen: set[int] = set()
    for fs in compiled.scalar_fields + compiled.repeating_fields:
        scores = scorer(_field_query(fs), block_texts)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        positive = [i for i in ranked if scores[i] > 0]
        for i in positive[:max(1, top_k // 2)]:
            chosen.add(i)
        # M2: a REQUIRED field that retrieval can't localise (no positive-scoring block) would
        # otherwise be invisible to the LLM → false quarantine of a doc that HAD the value.
        # Fall back to the whole document rather than risk dropping its block.
        if fs.required and not positive:
            idxs = list(range(len(block_texts)))
            return SelectedSpans(block_indices=idxs, whole_doc=True,
                                 text="\n".join(block_texts))
    if not chosen:                                   # nothing scored → whole doc
        idxs = list(range(len(block_texts)))
        return SelectedSpans(block_indices=idxs, whole_doc=True, text="\n".join(block_texts))
    idxs = sorted(chosen)
    return SelectedSpans(block_indices=idxs, whole_doc=False,
                         text="\n".join(block_texts[i] for i in idxs))
