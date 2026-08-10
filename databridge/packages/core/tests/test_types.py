"""Tests for closed vocabularies + the semantic annotation model."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from databridge_core.types import (
    AnnotatedColumn,
    AnnotatedSchema,
    AnnotatedTable,
    ColumnAnnotation,
    LabelCandidate,
    SemanticType,
    TableAnnotation,
    TableRole,
)


def test_semantic_type_is_closed_and_includes_unknown():
    values = {m.value for m in SemanticType}
    assert "unknown" in values
    assert len(SemanticType) == 16  # 15 real types + unknown
    assert SemanticType.AMOUNT_MONEY.value == "amount_money"


def test_table_role_is_the_closed_five():
    # exactly the five spec roles — NO `unknown` (uncertainty lives in confidence, not
    # the vocabulary; widening it needs an ADR).
    assert {m.value for m in TableRole} == {"entity", "event", "relationship", "lookup", "log"}
    assert len(TableRole) == 5


def test_trd_and_prd_names_are_the_same_models():
    assert AnnotatedColumn is ColumnAnnotation
    assert AnnotatedTable is TableAnnotation


def test_annotated_schema_confirmation_gate():
    schema = AnnotatedSchema(source_id="pg_main", version=1)
    assert schema.is_confirmed is False           # confirmed_by None ⇒ unusable (P6)
    confirmed = schema.model_copy(update={"confirmed_by": "alice"})
    assert confirmed.is_confirmed is True


def test_full_schema_construction():
    col = ColumnAnnotation(column="amount", semantic_type=SemanticType.AMOUNT_MONEY,
                           description="charge amount", ml_role="feature", confidence=0.96)
    tbl = TableAnnotation(table="transactions", concept="financial transaction",
                          role=TableRole.EVENT, columns=[col], summary="card charges")
    label = LabelCandidate(name="fraud", outcome_description="customer disputed",
                           derivation_sql="SELECT ... JOIN disputes", source_tables=["transactions"],
                           caveats=["label lags event ~30d (chargeback window)"])
    schema = AnnotatedSchema(source_id="pg_main", version=1, tables=[tbl],
                             label_candidates=[label], domain="fintech")
    assert schema.tables[0].columns[0].semantic_type is SemanticType.AMOUNT_MONEY
    assert schema.label_candidates[0].status == "proposed"


def test_annotation_models_are_frozen():
    col = ColumnAnnotation(column="x", semantic_type=SemanticType.UNKNOWN, description="d")
    with pytest.raises(ValidationError):
        col.confidence = 0.9


def test_invalid_semantic_type_rejected():
    with pytest.raises(ValidationError):
        ColumnAnnotation(column="x", semantic_type="not_a_type", description="d")
