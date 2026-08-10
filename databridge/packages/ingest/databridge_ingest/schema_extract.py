"""SchemaExtract — the ingestion layer's output (02_TRD Task 3).

The connector's job is to extract schema metadata + bounded samples, NOT to copy data
(P1: minimal movement). These are the input types the semantic layer (T06/T07) consumes.

In PROMPT 3 only the STATIC-fixture producer exists (``fixtures.py``); the live
PostgreSQL connector that fills these from ``information_schema``/``pg_catalog`` lands in
a later prompt (PROMPT 7). The models are the stable contract both paths target.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ForeignKey(BaseModel):
    """A foreign-key edge. ``declared`` FKs come from constraints (confidence 1.0);
    heuristic FKs (``*_id`` whose values ⊆ a candidate PK) are proposed with <1.0."""

    model_config = ConfigDict(frozen=True)

    column: str
    ref_table: str
    ref_column: str
    declared: bool = True
    confidence: float = 1.0


class ColumnMeta(BaseModel):
    """Per-column metadata + a bounded sample (≤20 values). Stats are FULL-table
    aggregates (06_PRD §9.5: the LLM sees both, and which is which)."""

    model_config = ConfigDict(frozen=True)

    name: str
    dtype: str                               # "integer" | "varchar" | "text" | "numeric"
                                             #   | "boolean" | "timestamp" | ...
    null_fraction: float = 0.0
    unique_count: int = 0
    sample_values: list[Any] = []            # ≤20, randomly sampled (PII-redacted)
    min_val: Optional[Any] = None
    max_val: Optional[Any] = None
    is_primary_key: bool = False


class TableMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    row_count: int = 0
    columns: list[ColumnMeta] = []
    sample_rows: list[dict] = []             # ≤50 rows
    foreign_keys: list[ForeignKey] = []
    indexes: list[str] = []

    def column(self, name: str) -> Optional[ColumnMeta]:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def fk_columns(self) -> set[str]:
        return {fk.column for fk in self.foreign_keys}


class SchemaExtract(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    tables: list[TableMeta] = []
    extracted_at: Optional[datetime] = None

    def table(self, name: str) -> Optional[TableMeta]:
        return next((t for t in self.tables if t.name == name), None)

    @property
    def table_names(self) -> set[str]:
        return {t.name for t in self.tables}
