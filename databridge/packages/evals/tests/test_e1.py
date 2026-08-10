"""E1 eval — deterministic (gold-backend) path for CI plumbing + metric correctness."""
from __future__ import annotations

import json
import re

from databridge_evals import gold_backend, run_e1
from databridge_harness import FakeBackend, Harness

CFG = {
    "routing": {"schema_annotation": "m", "cross_table_inference": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}


def _gold_harness() -> Harness:
    return Harness(FakeBackend(gold_backend()), config=CFG, profile="cloud")


def test_gold_backend_scores_perfect():
    res = run_e1(_gold_harness())
    assert res.accuracy == 1.0
    assert res.role_accuracy == 1.0
    assert res.total == 36


def test_airtight_provenance_is_always_correct():
    res = run_e1(_gold_harness())
    det = res.per_provenance.get("deterministic")
    assert det and det["accuracy"] == 1.0 and det["n"] == 18      # the 50% resolved with no LLM


def test_reliability_buckets_present():
    res = run_e1(_gold_harness())
    assert set(res.reliability) == {"lt_0.80", "0.80_0.95", "gte_0.95"}


def test_metric_detects_wrong_answers():
    # a backend that labels every LLM column 'free_text' must score below 1.0 — but the
    # deterministic (airtight) columns stay correct.
    def bad(*, model, messages, guided_json):
        sysmsg, user = messages[0]["content"], messages[-1]["content"]
        if "BUSINESS OUTCOMES" in sysmsg:
            return '{"candidates": []}'
        if "relationship" in sysmsg:
            return '{"domain": "x", "relationships": []}'
        m = re.search(r"Columns to classify:\s*(.*)", user)
        todo = [c.strip() for c in (m.group(1).split(",") if m else [])
                if c.strip() and c.strip() != "(none)"]
        return json.dumps({"concept": "x", "columns": [
            {"column": c, "semantic_type": "free_text", "description": "d",
             "self_confidence": 0.6} for c in todo]})

    res = run_e1(Harness(FakeBackend(bad), config=CFG, profile="cloud"))
    assert res.accuracy < 1.0
    assert res.per_provenance["deterministic"]["accuracy"] == 1.0
    assert res.mistakes                                          # the wrong LLM columns listed
