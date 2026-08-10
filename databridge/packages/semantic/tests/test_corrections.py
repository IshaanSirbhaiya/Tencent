"""Corrections store: PII redaction + append/persist (06_PRD §6)."""
from __future__ import annotations

from databridge_core import ColumnAnnotation, SemanticType
from databridge_semantic import Correction, CorrectionStore, redact_value


def test_redact_pii():
    assert redact_value("ana@example.com") == "<REDACTED:email>"
    assert redact_value("+1 415 555 1234").startswith("<REDACTED")
    assert redact_value("hello world") == "hello world"
    assert redact_value(42) == 42


def _corr():
    pred = ColumnAnnotation(column="x", semantic_type=SemanticType.FREE_TEXT, description="d")
    corr = ColumnAnnotation(column="x", semantic_type=SemanticType.STATUS_CATEGORICAL, description="d")
    return Correction(context_embedding_key="k",
                      input_context={"sample_values": ["ana@example.com", "ok"]},
                      predicted=pred, corrected=corr, schema_domain="fintech")


def test_store_redacts_and_persists(tmp_path):
    path = tmp_path / "corrections.jsonl"
    store = CorrectionStore(path)
    store.add(_corr())
    assert len(store) == 1
    # samples were redacted at write time (the flywheel must never leak)
    assert store.all()[0].input_context["sample_values"] == ["<REDACTED:email>", "ok"]
    # persisted to JSONL and reloadable
    assert len(CorrectionStore(path)) == 1


def test_retrieve_on_empty_store_is_empty():
    assert CorrectionStore().retrieve_similar("k", "fintech") == []
