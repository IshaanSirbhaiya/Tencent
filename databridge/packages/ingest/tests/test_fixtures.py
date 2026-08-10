"""The static fintech SchemaExtract fixture loads and has the expected shape."""
from __future__ import annotations

from databridge_ingest import SchemaExtract, available_fixtures, fintech_fixture


def test_fixture_loads_five_tables():
    s = fintech_fixture()
    assert isinstance(s, SchemaExtract)
    assert s.table_names == {"users", "merchants", "transactions", "disputes", "support_messages"}


def test_declared_foreign_keys():
    s = fintech_fixture()
    assert s.table("transactions").fk_columns == {"user_id", "merchant_id"}
    assert s.table("disputes").fk_columns == {"transaction_id"}
    assert s.table("users").foreign_keys == []           # entity root, no outgoing FKs


def test_primary_keys_present():
    s = fintech_fixture()
    for name in s.table_names:
        pks = [c.name for c in s.table(name).columns if c.is_primary_key]
        assert pks == ["id"]


def test_available_fixtures_lists_fintech():
    assert "fintech_schema_extract" in available_fixtures()
