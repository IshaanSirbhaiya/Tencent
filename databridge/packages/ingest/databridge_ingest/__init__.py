"""databridge_ingest — connectors + adapters that produce SchemaExtract / records.

PROMPT 3 ships only the SchemaExtract models + the static fixture loader; the live
PostgreSQL connector (T03) lands in PROMPT 7.
"""
from __future__ import annotations

from .fixtures import available_fixtures, fintech_fixture, load_fixture
from .schema_extract import ColumnMeta, ForeignKey, SchemaExtract, TableMeta

__all__ = [
    "SchemaExtract",
    "TableMeta",
    "ColumnMeta",
    "ForeignKey",
    "load_fixture",
    "fintech_fixture",
    "available_fixtures",
    "extract_schema",
]


def __getattr__(name: str):
    # lazy: postgres.py imports SQLAlchemy (heavy / optional) — only load on use
    if name == "extract_schema":
        from .postgres import extract_schema
        return extract_schema
    raise AttributeError(name)
