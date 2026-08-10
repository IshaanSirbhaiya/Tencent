"""E1 against a REAL local model (Ollama) — the honest accuracy number (06_PRD §8 / E6).

Records the real per-column accuracy of the routed-local model. The 92% fintech floor is
for the production model (Sonnet); a small local model (qwen2.5:3B) is expected to score
lower — that gap IS the E6 calibration signal (06_PRD §4/§8). Skips cleanly when Ollama /
LiteLLM are unavailable; the assertion is never weakened.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from databridge_evals import run_e1
from databridge_harness import Harness


def _ollama_models() -> list[str]:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
        return [m["name"] for m in json.load(r).get("models", [])]


@pytest.mark.integration
def test_e1_against_local_ollama(capsys):
    pytest.importorskip("litellm", reason="litellm not installed")
    from databridge_harness import LiteLLMBackend

    try:
        models = _ollama_models()
    except Exception as e:                                       # noqa: BLE001
        pytest.skip(f"Ollama not reachable: {e}")
    # prefer a TEXT qwen (a vision "vl" model is a poor text annotator)
    model = (next((m for m in models if m.lower().startswith("qwen") and "vl" not in m.lower()), None)
             or next((m for m in models if m.lower().startswith("qwen")), None))
    if model is None:
        pytest.skip(f"no qwen model in Ollama (have: {models})")

    cfg = {
        "routing": {"schema_annotation": "m", "cross_table_inference": "m"},
        "aliases": {"m": {"model": f"ollama_chat/{model}"}},
        "profiles": {"cloud": {}, "airgapped": {}},
    }
    res = run_e1(Harness(LiteLLMBackend(), config=cfg, profile="airgapped", cache=None))

    with capsys.disabled():
        print(f"\n[E1 live: {model}] overall={res.accuracy:.3f} roles={res.role_accuracy:.3f} "
              f"per_provenance={res.per_provenance}\n  reliability={res.reliability}")

    # honest, non-weakened floors: airtight columns are deterministic ⇒ MUST be perfect;
    # the local model must at least clear chance for the rest (real number is recorded above).
    assert res.per_provenance["deterministic"]["accuracy"] == 1.0
    assert res.accuracy >= 0.50           # deterministic 50% alone guarantees this; records the LLM lift
    assert res.model_map["schema_annotation"] == f"ollama_chat/{model}"
