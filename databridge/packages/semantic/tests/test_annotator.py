"""Stage B/C annotation end-to-end against FakeBackend (no API keys)."""
from __future__ import annotations

from databridge_ingest import fintech_fixture
from databridge_semantic import annotate_schema, build_report


def test_annotate_schema_end_to_end(harness):
    extract = fintech_fixture()
    schema = annotate_schema(harness, extract, build_report(extract))

    # produced an UNCONFIRMED schema (P6 — not usable downstream yet)
    assert schema.confirmed_by is None and schema.is_confirmed is False
    assert schema.version == 1
    assert {t.table for t in schema.tables} == extract.table_names

    users = next(t for t in schema.tables if t.table == "users")
    # airtight columns resolved deterministically at confidence 1.0 (zero LLM)
    id_col = next(c for c in users.columns if c.column == "id")
    assert id_col.provenance == "deterministic"
    assert id_col.confidence == 1.0
    assert id_col.semantic_type.value == "entity_id"
    # non-airtight columns are LLM-decided with a BLENDED confidence
    email = next(c for c in users.columns if c.column == "email")
    assert email.provenance == "llm"
    assert "band" in email.confidence_sources


def test_every_column_annotated(harness):
    extract = fintech_fixture()
    schema = annotate_schema(harness, extract, build_report(extract))
    for t in schema.tables:
        src = extract.table(t.table)
        assert {c.column for c in t.columns} == {c.name for c in src.columns}


def test_relationships_carried_through(harness):
    extract = fintech_fixture()
    schema = annotate_schema(harness, extract, build_report(extract))
    rels = {(r.src_table, r.dst_table, r.via) for r in schema.relationships}
    assert ("transactions", "users", "user_id") in rels
