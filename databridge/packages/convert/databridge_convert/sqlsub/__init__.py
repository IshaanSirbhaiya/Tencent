"""sqlsub/ — SQL source/target subsystem (C08 part 1). NL→SQL (C09) lands in PROMPT 8."""
from __future__ import annotations

from .cost import cost_guard
from .linking import SchemaSlice, link_schema
from .nl2sql import fix_sql, generate_sql, revise_sql
from .nlquery import (
    NLResult,
    answer_question,
    build_query_artifact,
    confirm_query,
    infer_query,
)
from .query_library import ConfirmedQuery, ConfirmedQueryLibrary
from .readonly_probe import ProbeResult, probe_readonly
from .safety import SafetyResult, validate_sql
from .snapshot import sql_snapshot
from .sources import read_sql
from .targets import write_sql

__all__ = [
    "read_sql", "write_sql", "probe_readonly", "ProbeResult", "sql_snapshot",
    # C09 NL→SQL
    "link_schema", "SchemaSlice", "validate_sql", "SafetyResult",
    "generate_sql", "fix_sql", "revise_sql", "cost_guard",
    "ConfirmedQueryLibrary", "ConfirmedQuery",
    "answer_question", "NLResult", "confirm_query", "build_query_artifact", "infer_query",
]
