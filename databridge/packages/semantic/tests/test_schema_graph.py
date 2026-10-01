"""T06 schema-graph builder — deterministic role inference + airtight column hints.

Done-when (02_TRD Task 6): structural role accuracy ≥ 4/5 tables with ZERO LLM calls.
"""
from __future__ import annotations

from databridge_core import SemanticType, TableRole
from databridge_ingest import ColumnMeta, ForeignKey, SchemaExtract, TableMeta, fintech_fixture
from databridge_semantic import build_report

# 06_PRD §8 E1 groundwork: the fintech fixture's structural-role ground truth.
GOLD_ROLES = {
    "users": TableRole.ENTITY,
    "merchants": TableRole.ENTITY,
    "transactions": TableRole.EVENT,
    "disputes": TableRole.EVENT,
    "support_messages": TableRole.LOG,
}


def report():
    return build_report(fintech_fixture())


def test_structural_role_at_least_4_of_5():
    r = report()
    correct = sum(1 for name, gold in GOLD_ROLES.items() if r.structure(name).role == gold)
    assert correct >= 4, {t.table: t.role for t in r.tables}


def test_all_five_roles_correct():
    r = report()
    assert {t.table: t.role for t in r.tables} == GOLD_ROLES


def _airtight_by_the_stage_a_rule(extract) -> int:
    """06_PRD §2 Stage A, restated over the fixture's raw metadata — NOT through
    `classify_column` — so the count is derived from the fixture, not recalled (HC2, SWEEP #24-34).
    The dtype sets are the rule's parameters, read from the module that applies them."""
    from datetime import datetime

    from databridge_semantic.schema_graph import _INT_DTYPES, _TIMESTAMP_DTYPES

    def iso(vals):
        vals = [v for v in vals if v is not None]
        try:
            return bool(vals) and all(
                isinstance(v, str) and datetime.fromisoformat(v.replace("Z", "+00:00"))
                for v in vals)
        except ValueError:
            return False

    n = 0
    for t in extract.tables:
        for c in t.columns:
            d = c.dtype.lower()
            n += (c.name in t.fk_columns
                  or (c.is_primary_key and d in _INT_DTYPES)
                  or (not c.is_primary_key and d in ("boolean", "bool"))
                  or (not c.is_primary_key and d in _TIMESTAMP_DTYPES and iso(c.sample_values)))
    return n


def test_airtight_resolves_30_to_50_percent():
    r = report()
    extract = fintech_fixture()
    expected_total = sum(len(t.columns) for t in extract.tables)
    expected_airtight = _airtight_by_the_stage_a_rule(extract)
    assert expected_total >= 1 and 1 <= expected_airtight < expected_total   # non-vacuous
    assert r.airtight_columns == expected_airtight
    assert r.total_columns == expected_total
    assert 0.30 <= r.airtight_fraction <= 0.55           # spec target ~30-50%


def test_airtight_rules_are_exact():
    r = report()

    def hint(t, c):
        return r.structure(t).hint(c)

    assert hint("users", "id").prelabel is SemanticType.ENTITY_ID and hint("users", "id").airtight
    fk = hint("transactions", "user_id")
    assert fk.prelabel is SemanticType.FOREIGN_KEY and fk.airtight
    assert hint("users", "created_at").prelabel is SemanticType.TIMESTAMP_CREATED
    assert hint("disputes", "opened_at").prelabel is SemanticType.TIMESTAMP_EVENT
    assert hint("support_messages", "sent_at").prelabel is SemanticType.TIMESTAMP_EVENT
    assert hint("users", "is_active").prelabel is SemanticType.BOOLEAN_FLAG


def test_non_airtight_hints_are_suggestions():
    r = report()
    email = r.structure("users").hint("email")
    assert email.prelabel is SemanticType.PERSONAL_IDENTIFIER and email.airtight is False
    assert r.structure("merchants").hint("website").prelabel is SemanticType.URL
    assert r.structure("transactions").hint("amount").prelabel is SemanticType.AMOUNT_MONEY
    assert r.structure("users").hint("country").prelabel is SemanticType.GEOGRAPHIC_LOCATION


def test_relationships_and_cardinality():
    r = report()
    rels = {(x.src_table, x.dst_table, x.via) for x in r.relationships}
    assert ("transactions", "users", "user_id") in rels
    assert ("transactions", "merchants", "merchant_id") in rels
    assert ("disputes", "transactions", "transaction_id") in rels
    txn_user = next(x for x in r.relationships
                    if x.via == "user_id" and x.src_table == "transactions")
    assert txn_user.cardinality == "N:1"           # many transactions per user


def test_entity_with_outgoing_fk_is_not_event():
    # regression: an ordinary entity that references a lookup must stay ENTITY, not EVENT.
    countries = TableMeta(name="countries", row_count=200, columns=[
        ColumnMeta(name="id", dtype="integer", is_primary_key=True, unique_count=200),
        ColumnMeta(name="name", dtype="varchar", unique_count=200),
    ])
    users = TableMeta(
        name="users", row_count=1000,
        foreign_keys=[ForeignKey(column="country_id", ref_table="countries", ref_column="id")],
        columns=[
            ColumnMeta(name="id", dtype="integer", is_primary_key=True, unique_count=1000),
            ColumnMeta(name="country_id", dtype="integer", unique_count=200),
            ColumnMeta(name="email", dtype="varchar", unique_count=1000, sample_values=["a@b.com"]),
        ],
    )
    r = build_report(SchemaExtract(source_id="t", tables=[countries, users]))
    assert r.structure("users").role is TableRole.ENTITY


def test_pkless_table_does_not_crash():
    # regression: the role fallback must return a valid TableRole, never a removed `unknown`.
    weird = TableMeta(name="weird", row_count=5, columns=[ColumnMeta(name="x", dtype="varchar")])
    r = build_report(SchemaExtract(source_id="t", tables=[weird]))
    assert r.structure("weird").role in set(TableRole)


def test_varchar_pk_is_not_airtight():
    cur = TableMeta(name="currencies", row_count=150, columns=[
        ColumnMeta(name="code", dtype="varchar", is_primary_key=True, unique_count=150,
                   sample_values=["USD", "SGD"]),
    ])
    r = build_report(SchemaExtract(source_id="t", tables=[cur]))
    h = r.structure("currencies").hint("code")
    assert h.prelabel is SemanticType.ENTITY_ID and h.airtight is False


def test_junction_table_is_relationship():
    user_roles = TableMeta(
        name="user_roles", row_count=500,
        foreign_keys=[ForeignKey(column="user_id", ref_table="users", ref_column="id"),
                      ForeignKey(column="role_id", ref_table="roles", ref_column="id")],
        columns=[ColumnMeta(name="user_id", dtype="integer"),
                 ColumnMeta(name="role_id", dtype="integer")],
    )
    users = TableMeta(name="users", row_count=10,
                      columns=[ColumnMeta(name="id", dtype="integer", is_primary_key=True)])
    roles = TableMeta(name="roles", row_count=5,
                      columns=[ColumnMeta(name="id", dtype="integer", is_primary_key=True)])
    r = build_report(SchemaExtract(source_id="t", tables=[user_roles, users, roles]))
    assert r.structure("user_roles").role is TableRole.RELATIONSHIP


def test_deterministic_and_no_harness():
    # the module never imports the harness — this whole pass is zero-LLM
    import databridge_semantic.schema_graph as sg
    assert not any("harness" in m for m in vars(sg))
    # same input → byte-identical report
    assert report().model_dump() == report().model_dump()
