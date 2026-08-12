"""BM25 entity linking for the clinic graph (`PLAN.md` §6.5).

## The bug this replaces

`link_entities` scored a node by counting how many question tokens appeared in its searchable text —
with `t in text`, a **substring** test over one joined string. Two failures compounded:

1. **Substring matching.** The token ``life`` matched ``…Life Assurance``, so *"What is the meaning
   of life?"* linked four insurers. Reproduced against a live gateway before this rewrite.
2. **Raw counts with no notion of term rarity.** A token appearing in forty nodes counted exactly as
   much as one appearing in a single node, so common words drowned out the discriminating one.

And there was no floor: the ranker always returned its top-k, so *something* was always "relevant".
A retrieval layer that can never say "nothing" is why the identity guardrail had to catch "meaning
of life" at all — retrieval should have declined first.

## What this does instead

Standard BM25 over exact tokens, with two clinic-specific adjustments:

- **Label tokens are repeated `_LABEL_WEIGHT` times** in a node's term vector. A question naming an
  entity should beat a node that merely mentions it in an attribute — the property the old
  tie-break was reaching for, expressed as term frequency instead of as a sort key.
- **The final score is scaled by label coverage** — what fraction of the node's *own name* the
  question accounts for. "meaning of life" explains 1 of 3 tokens in "Northstar Life Assurance"
  (0.33); "WELL2" explains 1 of 1 in "WELL2" (1.0). This is the signal that actually separates a
  question naming a thing from a question that happens to share a word with it.

**No embeddings.** For a ~70-node graph they would add a backend dependency and CI non-determinism
for no measurable gain, and a similarity score is not a `SourcePointer` (`PLAN.md` §6.5 rejects them
explicitly). Everything here is deterministic and offline.

## Tuning

`MIN_SCORE` is a floor, not a knob to turn until an eval passes. `packages/evals/e_retrieval.py`
reports the score over a fixed question set; changing that set is a reviewed change, not a fix.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+")

# Function words plus the interrogative scaffolding of a front-desk question. These carry no
# retrieval signal and, left in, let "what does the ... include" match on "does"/"include".
_STOP = frozenset({
    "the", "is", "are", "was", "were", "a", "an", "of", "to", "for", "in", "on", "at", "as",
    "what", "who", "whom", "whose", "which", "how", "much", "many", "our", "we", "us", "do",
    "does", "did", "and", "or", "but", "by", "with", "from", "that", "this", "these", "those",
    "value", "whats", "tell", "me", "about", "s", "it", "its", "be", "been", "being", "have",
    "has", "had", "can", "could", "would", "should", "will", "shall", "any", "all", "some",
    "there", "their", "you", "your", "i", "am", "if", "then", "than", "so", "not", "no",
})

# BM25 parameters. The literature defaults; nothing here justifies departing from them.
K1 = 1.5
B = 0.75

# A node's name matters more than its attributes. 3 is enough to dominate a single attribute
# mention without making a long label outweigh rarity.
_LABEL_WEIGHT = 3

# How much of the score survives when the question explains none of the node's name. At 0.35 a
# question that merely shares a common word with a long name keeps about a third of its BM25 —
# usually not enough to clear MIN_SCORE, while an exact name match keeps all of it.
_COVERAGE_FLOOR = 0.35

# THE FLOOR, part 1. Below this, return nothing rather than the least-bad node.
MIN_SCORE = 1.15

# THE FLOOR, part 2, and the one that does the real work.
#
# A question is *about* an entity only if it accounts for a decent share of that entity's NAME.
# BM25 alone cannot express this: "life" is genuinely rare across ~60 nodes, so it earns a healthy
# IDF, and "What is the meaning of life?" still scored three insurers above any absolute floor.
# But it explains just 1 of the 3 tokens in "Northstar Life Assurance" — it does not name it.
#
# Contrast the in-domain cases: "Chest X-Ray" 2/2, "Full Blood Count" 3/3, "Everwell Insurance
# Group" 3/3, "Meridian Life Assurance Pte Ltd" 3/5. Half is the natural cut, not a fitted one.
#
# KNOWN LIMITATION, recorded rather than hidden: this is exact-token matching, so an acronym does
# not match its expansion. "urine examination and microscopy" will not find the node labelled
# "UFEME". EV-RETRIEVAL keeps that question as a deliberate, reported miss.
MIN_LABEL_COVERAGE = 0.5


def tokens(text: str) -> list[str]:
    """Content tokens: lowercase, alphanumeric, stop-words and single characters removed."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass(frozen=True)
class Candidate:
    """One indexed entity. `id` is opaque here — the graph passes node ids."""

    id: str
    label: str
    canonical_id: str | None
    attr_text: str


@dataclass(frozen=True)
class Scored:
    id: str
    score: float
    coverage: float


class Bm25Index:
    """An immutable BM25 index over `Candidate`s. Build once per query; ~70 documents."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._ids: list[str] = []
        self._terms: list[dict[str, int]] = []
        self._lengths: list[int] = []
        self._label_tokens: list[set[str]] = []
        # A node's canonical id (``MRDEB``, ``WELL2``) is the single most identifying string it
        # has — tokenised the way a question is, NOT kept as a raw string. Comparing a raw string
        # against `q_set` (built by `tokens()`, which SPLITS on underscore) meant
        # `"chest_xray" in q_set` could never be true — `q_set` only ever holds `"chest"` and
        # `"xray"` as separate tokens, never the joined string. Every catalogue id with an
        # underscore (`chest_xray`, `full_blood_count`, `resting_ecg`'s siblings) was silently
        # unmatchable this way; only single-token ids like `WELL2` ever hit the branch.
        self._canonical_tokens: list[frozenset[str]] = []

        df: dict[str, int] = {}
        for c in candidates:
            label_toks = tokens(c.label)
            body = label_toks * _LABEL_WEIGHT + tokens(c.canonical_id or "") * _LABEL_WEIGHT \
                + tokens(c.attr_text)
            tf: dict[str, int] = {}
            for t in body:
                tf[t] = tf.get(t, 0) + 1
            self._ids.append(c.id)
            self._terms.append(tf)
            self._lengths.append(len(body))
            self._label_tokens.append(set(label_toks))
            self._canonical_tokens.append(frozenset(tokens(c.canonical_id or "")))
            for t in tf:
                df[t] = df.get(t, 0) + 1

        self._df = df
        self._n = len(candidates)
        self._avg_len = (sum(self._lengths) / self._n) if self._n else 0.0

    def _idf(self, term: str) -> float:
        # Robertson/Sparck-Jones with the +1 smoothing that keeps IDF non-negative — without it a
        # term present in more than half the corpus scores negatively and can push a genuinely
        # relevant node below the floor.
        n_t = self._df.get(term, 0)
        if n_t == 0:
            return 0.0
        return math.log(1.0 + (self._n - n_t + 0.5) / (n_t + 0.5))

    def search(self, question: str, *, top_k: int = 3,
               min_score: float = MIN_SCORE) -> list[Scored]:
        q = tokens(question)
        if not q:
            return []
        q_set = set(q)

        out: list[Scored] = []
        for i, tf in enumerate(self._terms):
            length = self._lengths[i] or 1
            score = 0.0
            for term in q_set:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self._idf(term)
                denom = f + K1 * (1 - B + B * length / (self._avg_len or 1))
                score += idf * (f * (K1 + 1)) / denom
            if score <= 0:
                continue

            label = self._label_tokens[i]
            coverage = (len(label & q_set) / len(label)) if label else 0.0
            # An exact canonical-id hit IS the name, however long the display label is:
            # "PEE226" should fully identify "Executive Screening (PEE226)". Compared as TOKENS,
            # not as the raw string against `q_set`: `q_set` is built by `tokens()`, which splits
            # on underscore, so a raw `"chest_xray" in q_set` could never match — `q_set` only ever
            # holds `"chest"` and `"xray"` separately. A subset check on the tokenised id is the
            # same "the question names this exactly" test, expressed in the vocabulary `q_set`
            # actually uses.
            canonical_toks = self._canonical_tokens[i]
            if canonical_toks and canonical_toks <= q_set:
                coverage = 1.0
            # Gate before scoring: a node whose name the question barely touches is not the
            # subject of that question, however well BM25 likes the one word they share.
            if coverage < MIN_LABEL_COVERAGE:
                continue
            score *= _COVERAGE_FLOOR + (1 - _COVERAGE_FLOOR) * coverage

            if score < min_score:
                continue
            out.append(Scored(id=self._ids[i], score=score, coverage=coverage))

        # Deterministic: score, then coverage, then id. Never rely on insertion order — two nodes
        # can tie exactly, and a demo that reorders between runs is a demo nobody trusts.
        out.sort(key=lambda s: (-s.score, -s.coverage, s.id))
        return out[:top_k]
