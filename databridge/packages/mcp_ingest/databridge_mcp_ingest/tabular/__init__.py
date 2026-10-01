"""databridge_mcp_ingest.tabular — the relational-database recipe (principles §3, §4; S2).

    ingest_database(source_id, store, harness)   profile → mapping → graph → facts → cards
    run_tabular(source_id, store=None)           the same without the graph-row stage
    count_query(source_id, sql)                  "how many / list all" by SQL on the facts
                                                 store, cited [SQL] with the extract date
    describe_facts(source_id)                    facts tables + columns, for writing that SQL
    loads_rows(text, typed=...)                  safe parser for Python-repr row lists
    draft_dialect(source_id) / approve_dialect   a NEW engine: detect → template (or model
                                                 draft) → live probe → human approval (W4)

Numbers → SQL (facts store), prose → passages (captured + indexed), entities → graph (rows via
the approved mapping). Schema cards bridge them and state the extract date and coverage.
"""
from __future__ import annotations

from .count import CountAnswer, FactsQueryError, count_query, describe_facts
from .dialect_draft import (DialectError, DialectProposal, approve_dialect, approved_dialect,
                            draft_dialect, load_templates, validate_template)
from .facts import facts_path, load_manifest
from .pipeline import TabularStage, ingest_database, run_tabular
from .policy import load_policy
from .pyrepr import ReprParseError, loads_rows

__all__ = ["ingest_database", "run_tabular", "TabularStage", "count_query", "describe_facts",
           "CountAnswer", "FactsQueryError", "facts_path", "load_manifest", "load_policy",
           "loads_rows", "ReprParseError", "draft_dialect", "approve_dialect",
           "approved_dialect", "load_templates", "validate_template", "DialectProposal",
           "DialectError"]
