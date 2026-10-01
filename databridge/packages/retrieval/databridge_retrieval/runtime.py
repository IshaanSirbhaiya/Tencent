"""The process-wide default harness for retrieval (embeddings + depth-level model calls).

Every model and embedding call goes through ``databridge_harness`` (repo rule). The default is the
real LiteLLM-backed harness routed by ``config/models.yaml``; with no embedding key it embeds with
the harness's LABELLED offline fallback. Tests and the gateway inject their own with
``configure(harness=...)``.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"harness": None}


def configure(*, harness: Optional[Any] = None) -> None:
    with _LOCK:
        _STATE["harness"] = harness


def get_harness() -> Any:
    with _LOCK:
        if _STATE["harness"] is None:
            from databridge_harness import Harness

            # cache=None: an unbounded per-text embedding cache would grow with the corpus.
            _STATE["harness"] = Harness(cache=None)
        return _STATE["harness"]
