"""Shared fixtures for semantic-layer tests: an injected routing config + a simple
annotation backend (valid pipeline responses, not gold — accuracy is E1's job)."""
from __future__ import annotations

import json
import re

import pytest

from databridge_harness import FakeBackend, Harness

CFG = {
    "routing": {"schema_annotation": "m", "cross_table_inference": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}


def _simple_handler(*, model, messages, guided_json) -> str:
    sysmsg = messages[0]["content"]
    user = messages[-1]["content"]
    if "BUSINESS OUTCOMES" in sysmsg:                       # label discovery
        return json.dumps({"candidates": []})
    if "relationship" in sysmsg:                            # Stage C
        return json.dumps({"domain": "fintech", "relationships": []})
    todo_m = re.search(r"Columns to classify:\s*(.*)", user)
    todo = [c.strip() for c in (todo_m.group(1).split(",") if todo_m else [])
            if c.strip() and c.strip() != "(none)"]
    return json.dumps({"concept": "records", "columns": [
        {"column": c, "semantic_type": "free_text", "description": "d",
         "self_confidence": 0.7} for c in todo]})


@pytest.fixture
def cfg() -> dict:
    return CFG


@pytest.fixture
def harness() -> Harness:
    return Harness(FakeBackend(_simple_handler), config=CFG, profile="cloud")
