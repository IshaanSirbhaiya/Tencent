"""Semantic-layer models.

The annotation models (ColumnAnnotation/TableAnnotation/RelationshipAnnotation/
LabelCandidate/AnnotatedSchema) ALREADY live in ``databridge_core`` (built in Task 1).
We re-export them here so semantic code has one import home — we do NOT redefine them
(06_PRD §3; the build prompt's "extend/import, don't duplicate").

New here: the deterministic STRUCTURAL output of T06 (Stage A) — per-column hints and
per-table structural roles — which Stage B/C (T07) consume as inputs, never as answers.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

# Re-exported from core — the canonical annotation model (06_PRD §3).
from databridge_core import (  # noqa: F401
    AnnotatedSchema,
    ColumnAnnotation,
    LabelCandidate,
    RelationshipAnnotation,
    SemanticType,
    TableAnnotation,
    TableRole,
)


class ColumnHint(BaseModel):
    """A cheap, deterministic pre-label for one column (06_PRD §2 Stage A).

    ``airtight`` means a deterministic rule RESOLVED it with no LLM call (declared PK →
    entity_id, declared FK → foreign_key, 100%-ISO8601 → timestamp_*, bool → boolean_flag).
    Non-airtight hints are SUGGESTIONS the LLM may override.
    """

    model_config = ConfigDict(frozen=True)

    column: str
    prelabel: Optional[SemanticType] = None
    airtight: bool = False
    rationale: str = ""


class TableStructure(BaseModel):
    """Deterministic structural read of one table: its inferred role + column hints."""

    model_config = ConfigDict(frozen=True)

    table: str
    role: TableRole
    role_confidence: float
    column_hints: list[ColumnHint] = []

    def hint(self, column: str) -> Optional[ColumnHint]:
        return next((h for h in self.column_hints if h.column == column), None)


class SchemaGraphReport(BaseModel):
    """The output of T06, consumed by T07. Structural roles + FK-derived relationships +
    per-column hints — all produced with ZERO LLM calls."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    tables: list[TableStructure] = []
    relationships: list[RelationshipAnnotation] = []

    def structure(self, table: str) -> Optional[TableStructure]:
        return next((t for t in self.tables if t.table == table), None)

    @property
    def airtight_columns(self) -> int:
        return sum(1 for t in self.tables for h in t.column_hints if h.airtight)

    @property
    def total_columns(self) -> int:
        return sum(len(t.column_hints) for t in self.tables)

    @property
    def airtight_fraction(self) -> float:
        n = self.total_columns
        return self.airtight_columns / n if n else 0.0
