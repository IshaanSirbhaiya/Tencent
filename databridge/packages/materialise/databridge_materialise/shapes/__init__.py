"""The shapes. Two are produced; two refuse honestly and name what is missing.

`SYSTEM_DESIGN` §6.6 works four examples. This build implements the first and the third:

* **tabular (XGBoost)** — `tabular.py`. Features on system time per row, labels on valid time with
  a maturity gate, `None` preserved with `*_missing_reason` companions, temporal split.
* **chunk_corpus (RAG)** — `chunked.py`. Membership on system time, late chunking, and each chunk
  IS a pointer whose offsets re-slice byte-identically.

The other two refuse (`unimplemented.py`) rather than emitting a plausible-looking approximation:

* **graph_tensor** — needs a materialised, hashed NEGATIVE SAMPLE SET before it is reproducible,
  and this build has no negative sampler. A temporal-graph dataset without pinned negatives is not
  reproducible however well the positives are versioned.
* **conversational** — needs a `generated_claim` provenance path with depth ≤ 1 and a
  `min_grounded_fraction` floor, and it must exclude identity-verification turns *by
  construction*. None of that exists here, and a shape that produced dialogue without it would be
  training a model to assert identity matches.

A refusal that names the missing piece is a design artefact. A stub that returns an empty rectangle
is a bug waiting to be mistaken for a dataset.
"""
from __future__ import annotations

from .chunked import ChunkBuild, build_chunk_corpus
from .tabular import TabularBuild, build_tabular
from .unimplemented import build_conversational, build_graph_tensor

__all__ = ["TabularBuild", "build_tabular", "ChunkBuild", "build_chunk_corpus",
           "build_graph_tensor", "build_conversational"]
