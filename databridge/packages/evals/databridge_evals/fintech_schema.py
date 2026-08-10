"""A CONFIRMED fintech AnnotatedSchema (stands in for T07's confirmed output).

NL→SQL (C09) and Demo D9 consume a *confirmed* AnnotatedSchema (semantic types + roles +
named relationships). Building the live one needs a model; here we assemble it
deterministically from the E1 gold types + the deterministic T06 roles/relationships and
mark it ``confirmed_by="fixture"`` — the same object the confirmation TUI would emit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from databridge_core import (
    AnnotatedSchema,
    ColumnAnnotation,
    SemanticType,
    TableAnnotation,
    TableRole,
)
from databridge_ingest import fintech_fixture
from databridge_semantic import build_report

from .e1 import load_gold

_CONCEPTS = {
    "users": "customer account", "merchants": "merchant",
    "transactions": "card transaction", "disputes": "transaction dispute",
    "support_messages": "support message",
}


def confirmed_fintech_schema() -> AnnotatedSchema:
    gold = load_gold()
    extract = fintech_fixture()
    report = build_report(extract)

    tables = []
    for t in extract.tables:
        cols = [
            ColumnAnnotation(
                column=c.name,
                semantic_type=SemanticType(gold["columns"][f"{t.name}.{c.name}"]),
                description=f"{c.name} of {t.name}", provenance="human", confidence=1.0)
            for c in t.columns
        ]
        tables.append(TableAnnotation(
            table=t.name, concept=_CONCEPTS.get(t.name, t.name),
            role=TableRole(gold["roles"][t.name]), columns=cols,
            summary=f"{_CONCEPTS.get(t.name, t.name)} ({gold['roles'][t.name]})"))

    rels = [r.model_copy(update={"name": f"{r.src_table} → {r.dst_table}"})
            for r in report.relationships]

    return AnnotatedSchema(
        source_id="fintech_fixture", version=1, tables=tables, relationships=rels,
        label_candidates=[], domain="fintech",
        created_at=datetime.now(timezone.utc), confirmed_by="fixture")
