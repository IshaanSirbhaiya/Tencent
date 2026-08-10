"""The confirmation flow enforces P6 at the API level (06_PRD §3/§7)."""
from __future__ import annotations

import pytest

from databridge_core import (
    AnnotatedSchema,
    ColumnAnnotation,
    SemanticType,
    TableAnnotation,
    TableRole,
)
from databridge_semantic import ConfirmationSession, CorrectionStore, MandatoryReviewPending


def _draft() -> AnnotatedSchema:
    auto = ColumnAnnotation(column="id", semantic_type=SemanticType.ENTITY_ID,
                            description="pk", confidence=1.0, provenance="deterministic")
    review = ColumnAnnotation(column="weird", semantic_type=SemanticType.FREE_TEXT,
                              description="?", confidence=0.5, provenance="llm")  # <0.80 → review
    t = TableAnnotation(table="t", concept="c", role=TableRole.ENTITY, columns=[auto, review])
    return AnnotatedSchema(source_id="s", version=1, tables=[t])


def test_finalize_refuses_while_review_pending():
    s = ConfirmationSession(_draft())
    assert len(s.mandatory_remaining()) == 1
    with pytest.raises(MandatoryReviewPending):
        s.finalize("alice")          # even a "TUI bug" calling finalize cannot bypass review


def test_accept_resolves_then_finalize_succeeds():
    s = ConfirmationSession(_draft())
    s.accept("t", "weird")
    assert s.mandatory_remaining() == []
    confirmed = s.finalize("alice")
    assert confirmed.confirmed_by == "alice" and confirmed.is_confirmed


def test_edit_records_a_correction():
    store = CorrectionStore()
    s = ConfirmationSession(_draft(), corrections=store)
    s.edit("t", "weird", SemanticType.STATUS_CATEGORICAL)
    assert len(store) == 1
    corr = store.all()[0]
    assert corr.predicted.semantic_type is SemanticType.FREE_TEXT
    assert corr.corrected.semantic_type is SemanticType.STATUS_CATEGORICAL
    s.finalize("bob")                # resolved → no raise


def test_rejects_an_already_confirmed_draft():
    confirmed = _draft().model_copy(update={"confirmed_by": "x"})
    with pytest.raises(ValueError):
        ConfirmationSession(confirmed)
