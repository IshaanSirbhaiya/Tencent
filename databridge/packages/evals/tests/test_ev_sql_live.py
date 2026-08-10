"""EV-SQL against a REAL local model (Ollama) — the honest execution-accuracy number.

Gate is ≥70% for the production model; a 3B local model is expected lower (the §29 risk:
"NL→SQL in S1 underdelivers" — published number, never a silently-adjusted gate). Skips
when Ollama/LiteLLM are unavailable.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from databridge_convert.demos.d9 import fintech_sqlite_engine
from databridge_evals import run_ev_sql
from databridge_harness import Harness


@pytest.mark.integration
def test_ev_sql_against_local_ollama(capsys):
    pytest.importorskip("litellm", reason="litellm not installed")
    from databridge_harness import LiteLLMBackend

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"Ollama not reachable: {e}")
    model = next((m for m in models if m.lower().startswith("qwen") and "vl" not in m.lower()), None)
    if model is None:
        pytest.skip(f"no text qwen model in Ollama (have: {models})")

    cfg = {
        "routing": {"sql_generation": "m", "sql_revision": "m"},
        "aliases": {"m": {"model": f"ollama_chat/{model}"}},
        "profiles": {"cloud": {}, "airgapped": {}},
    }
    res = run_ev_sql(Harness(LiteLLMBackend(), config=cfg, profile="airgapped", cache=None),
                     fintech_sqlite_engine())

    with capsys.disabled():
        print(f"\n[EV-SQL live: {model}] accuracy={res.accuracy:.3f} "
              f"(gate {res.passed and '>=' or '<'} 0.70), correct={res.correct}/{res.total}")
        for q in res.per_question:
            mark = "OK " if q["match"] else ("REF" if q["refusal"] else "xx ")
            print(f"  [{mark}] {q['question']}")

    assert res.total == 12                                  # ran end-to-end; number recorded above
