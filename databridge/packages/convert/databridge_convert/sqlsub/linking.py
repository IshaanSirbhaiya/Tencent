"""Schema linking (09_FINAL_PRD §15.9) — select relevant tables/columns by MEANING.

This is what makes NL→SQL viable: instead of dumping the whole schema at the model, link
on MEANING (concepts, column descriptions, semantic types, relationship names) from the
CONFIRMED AnnotatedSchema (T07), narrow to a "schema slice", then expand one FK hop so
joins are possible. The slice + its hash become part of the artifact (§15.9/§15.12).
"""
from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel, ConfigDict

from databridge_core import AnnotatedSchema, RelationshipAnnotation

_STOP = {"the", "by", "of", "in", "for", "and", "or", "to", "a", "an", "top", "last",
         "per", "each", "show", "list", "with", "most", "many", "much", "me", "give",
         "what", "which", "how", "all", "total", "number", "count"}


def _tokens(text: str) -> set[str]:
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w           # naive singularise
            for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) >= 3}


def _matches(a: set[str], b: set[str]) -> int:
    score = 0
    for x in a:
        for y in b:
            if x == y or (len(x) >= 4 and len(y) >= 4 and x[:4] == y[:4]):
                score += 1
                break
    return score


class SchemaSlice(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    tables: list[str]
    columns: dict[str, list[str]]
    relationships: list[RelationshipAnnotation] = []
    slice_hash: str

    def schema_text(self, schema: AnnotatedSchema) -> str:
        """A compact schema description for the generation prompt (types + joins)."""
        lines = []
        for tname in self.tables:
            tann = next((t for t in schema.tables if t.table == tname), None)
            if not tann:
                continue
            cols = ", ".join(f"{c.column}:{c.semantic_type.value}" for c in tann.columns
                             if c.column in self.columns.get(tname, []))
            lines.append(f"TABLE {tname} ({tann.concept}) [{tann.role.value}]: {cols}")
        for r in self.relationships:
            lines.append(f"JOIN {r.src_table}.{r.via} = {r.dst_table}.{r.via.replace(r.src_table + '_', '') or 'id'} "
                         f"({r.name or r.cardinality})")
        return "\n".join(lines)


def link_schema(schema: AnnotatedSchema, question: str, *, max_tables: int = 6) -> SchemaSlice:
    qtok = _tokens(question)

    scored: dict[str, int] = {}
    for t in schema.tables:
        terms = _tokens(t.table) | _tokens(t.concept) | _tokens(t.summary)
        for c in t.columns:
            terms |= _tokens(c.column) | _tokens(c.description) | _tokens(c.semantic_type.value)
        for r in schema.relationships:
            if r.src_table == t.table or r.dst_table == t.table:
                terms |= _tokens(r.name)
        scored[t.table] = _matches(qtok, terms)

    selected = [t for t, s in sorted(scored.items(), key=lambda kv: -kv[1]) if s > 0][:max_tables]
    if not selected:                                       # fall back to all tables
        selected = [t.table for t in schema.tables][:max_tables]

    # expand one FK hop so the model can join
    expanded = set(selected)
    for r in schema.relationships:
        if r.src_table in selected or r.dst_table in selected:
            expanded.update({r.src_table, r.dst_table})
    tables = [t.table for t in schema.tables if t.table in expanded][:max_tables]

    columns = {t.table: [c.column for c in t.columns]
               for t in schema.tables if t.table in tables}
    rels = [r for r in schema.relationships if r.src_table in tables and r.dst_table in tables]
    slice_hash = hashlib.sha256(
        f"{schema.source_id}|{sorted(tables)}|{sorted((k, tuple(v)) for k, v in columns.items())}"
        .encode("utf-8")).hexdigest()[:16]
    return SchemaSlice(source_id=schema.source_id, tables=tables, columns=columns,
                       relationships=rels, slice_hash=slice_hash)
