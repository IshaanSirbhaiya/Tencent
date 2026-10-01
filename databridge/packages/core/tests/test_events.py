"""Tests for content hashing + PipelineEvent."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from databridge_core.events import PipelineEvent, content_hash
from databridge_core.provenance import SourcePointer, Trust


def test_content_hash_is_order_independent():
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_content_hash_changes_with_content():
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_content_hash_handles_pydantic_models():
    p = SourcePointer(source_id="s", kind="table_cell", table="t", column="c",
                      row_id="1", trust=Trust.TRUSTED)
    h = content_hash(p)
    assert isinstance(h, str) and len(h) == 64        # sha256 hex


def test_record_is_deterministic():
    e1 = PipelineEvent.record("step", {"x": 1}, {"y": 2})
    e2 = PipelineEvent.record("step", {"x": 1}, {"y": 2})
    assert e1.inputs_hash == e2.inputs_hash
    assert e1.outputs_hash == e2.outputs_hash


def test_record_distinguishes_outputs():
    e1 = PipelineEvent.record("step", {"x": 1}, {"y": 2})
    e2 = PipelineEvent.record("step", {"x": 1}, {"y": 3})
    assert e1.outputs_hash != e2.outputs_hash


def test_event_is_frozen():
    e = PipelineEvent.record("step", {"x": 1}, {"y": 2}, task_class="schema_annotation")
    assert e.task_class == "schema_annotation"
    with pytest.raises(ValidationError):
        e.step = "other"


def test_event_carries_harness_telemetry():
    # additive fields the harness (T02) fills (07_PRD §2/§5)
    e = PipelineEvent.record("complete_structured", {"x": 1}, {"y": 2},
                             ladder_used="L2", retries=1, cached=False)
    assert e.ladder_used == "L2" and e.retries == 1 and e.cached is False
    # default None when not supplied (deterministic steps)
    assert PipelineEvent.record("rule_execute", {}, {}).ladder_used is None
