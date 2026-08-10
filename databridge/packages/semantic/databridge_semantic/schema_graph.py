"""Schema graph builder — deterministic pre-annotation (T06 / 06_PRD §2 Stage A).

Before any LLM sees a schema, structural analysis extracts FREE signal (P3): graph
topology reveals table roles, and cheap classifiers pre-label columns. Airtight rules
resolve 30–50% of columns at ZERO token cost; the rest become HINTS the LLM (T07)
refines. NetworkX (not Neo4j) here: the schema graph is small, ephemeral, analytical.
No LLM, no harness — this whole module is deterministic.
"""
from __future__ import annotations

import re
from datetime import datetime

import networkx as nx

from databridge_core import SemanticType, TableRole
from databridge_ingest import ColumnMeta, SchemaExtract, TableMeta

from .models import ColumnHint, RelationshipAnnotation, SchemaGraphReport, TableStructure

# --- cheap column classifier patterns -------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://", re.I)
_INT_DTYPES = {"integer", "int", "bigint", "smallint", "serial", "bigserial", "uuid"}
_TIMESTAMP_DTYPES = {"timestamp", "timestamptz", "datetime", "date"}
_CREATED_NAME_RE = re.compile(r"(created|registered|signup|sign_up|joined|inserted)", re.I)
_MONEY_NAME_RE = re.compile(r"(amount|price|total|cost|balance|fee|revenue|disputed|charge|paid)", re.I)
_SCORE_NAME_RE = re.compile(r"(score|rating|rank)", re.I)
_GEO_NAME_RE = re.compile(r"(country|city|region|location|geo|state|province|address|postcode|zip)", re.I)
_LOG_NAME_RE = re.compile(r"(message|messages|log|logs|audit|history|event|events|note|notes|comment|comments|activity)", re.I)

LOOKUP_MAX_ROWS = 25


def _all_iso8601(samples: list) -> bool:
    """True if EVERY non-null sample parses as ISO-8601 (the airtight timestamp test)."""
    vals = [s for s in samples if s is not None]
    if not vals:
        return False
    for s in vals:
        if not isinstance(s, str):
            return False
        try:
            datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return False
    return True


def classify_column(col: ColumnMeta, table: TableMeta) -> ColumnHint:
    """Cheap deterministic classifier. Airtight rules first (no LLM needed), then hints."""
    name = col.name
    dtype = col.dtype.lower()

    # --- airtight rules (06_PRD §2 Stage A) ---
    if col.name in table.fk_columns:
        return ColumnHint(column=name, prelabel=SemanticType.FOREIGN_KEY, airtight=True,
                          rationale="declared foreign key")
    if col.is_primary_key:
        # 06_PRD §2/§7: declared PK *int* → entity_id is airtight. A non-int PK (e.g. a
        # varchar code) is only a HINT — it may be a categorical/lookup key, not an entity.
        if dtype in _INT_DTYPES:
            return ColumnHint(column=name, prelabel=SemanticType.ENTITY_ID, airtight=True,
                              rationale="declared integer primary key")
        return ColumnHint(column=name, prelabel=SemanticType.ENTITY_ID, airtight=False,
                          rationale="declared non-int primary key — confirm (may be a code/lookup key)")
    if dtype == "boolean" or dtype == "bool":
        return ColumnHint(column=name, prelabel=SemanticType.BOOLEAN_FLAG, airtight=True,
                          rationale="boolean dtype")
    if dtype in _TIMESTAMP_DTYPES and _all_iso8601(col.sample_values):
        kind = (SemanticType.TIMESTAMP_CREATED if _CREATED_NAME_RE.search(name)
                else SemanticType.TIMESTAMP_EVENT)
        return ColumnHint(column=name, prelabel=kind, airtight=True,
                          rationale="100% ISO-8601 samples; name-based created/event split")

    # --- non-airtight HINTS (suggestions for the LLM) ---
    samples = [s for s in col.sample_values if s is not None]
    if dtype in ("numeric", "decimal", "money", "float", "double", "real"):
        if _MONEY_NAME_RE.search(name):
            return ColumnHint(column=name, prelabel=SemanticType.AMOUNT_MONEY,
                              rationale="numeric + money-like name")
        if _SCORE_NAME_RE.search(name):
            return ColumnHint(column=name, prelabel=SemanticType.SCORE_RATING,
                              rationale="numeric + score-like name")
    if dtype in ("varchar", "text", "char", "string"):
        if samples and all(isinstance(s, str) and _EMAIL_RE.match(s) for s in samples):
            return ColumnHint(column=name, prelabel=SemanticType.PERSONAL_IDENTIFIER,
                              rationale="email-shaped samples")
        if samples and all(isinstance(s, str) and _URL_RE.match(s) for s in samples):
            return ColumnHint(column=name, prelabel=SemanticType.URL, rationale="URL-shaped samples")
        if _GEO_NAME_RE.search(name):
            return ColumnHint(column=name, prelabel=SemanticType.GEOGRAPHIC_LOCATION,
                              rationale="geo-like name")
        # low-cardinality string → categorical; long/high-cardinality text → free text
        if dtype == "text" or (col.unique_count and col.unique_count > 100):
            return ColumnHint(column=name, prelabel=SemanticType.FREE_TEXT,
                              rationale="text / high-cardinality string")
        if col.unique_count and col.unique_count <= 30:
            return ColumnHint(column=name, prelabel=SemanticType.STATUS_CATEGORICAL,
                              rationale="low-cardinality string")
    if dtype in ("json", "jsonb"):
        return ColumnHint(column=name, prelabel=SemanticType.JSON_BLOB, rationale="json dtype")

    return ColumnHint(column=name, prelabel=None, rationale="no cheap rule matched")


def build_digraph(extract: SchemaExtract) -> nx.DiGraph:
    """Nodes = tables; an edge child → parent for each FK (child references parent)."""
    g = nx.DiGraph()
    for t in extract.tables:
        g.add_node(t.name, row_count=t.row_count)
    for t in extract.tables:
        for fk in t.foreign_keys:
            g.add_edge(t.name, fk.ref_table, via=fk.column)
    return g


def _text_heavy(table: TableMeta) -> bool:
    return any(c.dtype.lower() == "text" and (c.unique_count or 0) > 100 for c in table.columns)


def infer_structural_role(table: TableMeta, graph: nx.DiGraph) -> tuple[TableRole, float]:
    """Infer a table's role from graph topology + cheap signals (06_PRD §2 Stage A).

    Hints, never final answers — the LLM (Stage B/C) refines them, and ``role_confidence``
    carries the uncertainty (TableRole is a closed 5-value vocab, so an ambiguous table
    gets a best-guess role with LOW confidence, never an ``unknown`` value).

    Key discriminator: an EVENT is a *time-stamped occurrence that references entities*
    (timestamp AND outgoing FK). A real ENTITY commonly has outgoing FKs too (to lookups
    or parents), so an outgoing FK alone must NOT make a table an event.
    """
    name = table.name
    references = {dst for _, dst in graph.out_edges(name)}        # tables this one points to
    referenced_by = {src for src, _ in graph.in_edges(name)}      # tables pointing here
    fk_count = len(table.fk_columns)
    col_count = len(table.columns)
    has_pk = any(c.is_primary_key for c in table.columns)
    has_ts = any(
        c.dtype.lower() in _TIMESTAMP_DTYPES and _all_iso8601(c.sample_values)
        for c in table.columns
    )
    # attribute columns = not PK, not FK, not timestamp, not bool
    attrs = [c for c in table.columns
             if not c.is_primary_key and c.name not in table.fk_columns
             and c.dtype.lower() not in _TIMESTAMP_DTYPES
             and c.dtype.lower() not in ("boolean", "bool")]

    # 1) junction / relationship: ≥2 FKs and almost every column is a key
    if fk_count >= 2 and fk_count >= col_count - 2:
        return TableRole.RELATIONSHIP, 0.85
    # 2) log: a time-stamped append-y stream (log/message name, or a text body + few attrs)
    if has_ts and (_LOG_NAME_RE.search(name) or (_text_heavy(table) and len(attrs) <= 2)):
        return TableRole.LOG, 0.8
    # 3) event: a time-stamped occurrence that references entities (timestamp AND outgoing FK)
    if has_ts and references:
        return TableRole.EVENT, 0.8
    # 4) entity vs lookup: a thing with identity (may reference lookups/parents)
    if has_pk:
        if table.row_count <= LOOKUP_MAX_ROWS and referenced_by and len(attrs) <= 2:
            return TableRole.LOOKUP, 0.7
        # confident when self-contained or clearly referenced; lower when it only points out
        return TableRole.ENTITY, (0.85 if (not references or referenced_by) else 0.6)
    # 5) low-confidence fallbacks (closed vocab → best guess, never an unknown value)
    if has_ts:
        return TableRole.EVENT, 0.4
    return TableRole.ENTITY, 0.3


def infer_cardinality(fk_column: ColumnMeta, child: TableMeta) -> str:
    """1:1 if the FK column is (near-)unique over the child's rows, else N:1."""
    if fk_column is None or not child.row_count:
        return "N:1"
    if fk_column.unique_count and fk_column.unique_count >= 0.95 * child.row_count:
        return "1:1"
    return "N:1"


def build_report(extract: SchemaExtract) -> SchemaGraphReport:
    """Full deterministic pass → SchemaGraphReport (the T06 deliverable, zero LLM)."""
    graph = build_digraph(extract)
    tables: list[TableStructure] = []
    relationships: list[RelationshipAnnotation] = []

    for t in extract.tables:
        role, conf = infer_structural_role(t, graph)
        hints = [classify_column(c, t) for c in t.columns]
        tables.append(TableStructure(table=t.name, role=role, role_confidence=conf,
                                     column_hints=hints))
        for fk in t.foreign_keys:
            card = infer_cardinality(t.column(fk.column), t)
            relationships.append(RelationshipAnnotation(
                src_table=t.name, dst_table=fk.ref_table, via=fk.column,
                name="",                       # Stage C (T07) names the relationship
                cardinality=card, declared=fk.declared, confidence=fk.confidence,
            ))

    return SchemaGraphReport(source_id=extract.source_id, tables=tables,
                             relationships=relationships)
