"""databridge_evals — the product's immune system (07_PRD Part II).

T07 ships E1 (schema-annotation accuracy). The full suite board + runner + thresholds
land in T11 / C24.
"""
from __future__ import annotations

from .e1 import E1Result, gold_backend, load_gold, run_e1
from .e_sql import EvSqlResult, run_ev_sql
from .e_sql import gold_backend as sql_gold_backend
from .fintech_schema import confirmed_fintech_schema

__all__ = ["run_e1", "E1Result", "load_gold", "gold_backend",
           "run_ev_sql", "EvSqlResult", "sql_gold_backend", "confirmed_fintech_schema"]
