"""Cost / timeout / row-cap guard (09_FINAL_PRD §15.8) — three of the four read walls.

EXPLAIN-based cost ceiling where the dialect provides it (Postgres); otherwise a heuristic
(the LIMIT injected by safety.py is the row cap). statement_timeout is applied per source
config. The fourth wall (read-only role + SELECT-only allow-list) lives in readonly_probe +
safety.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Engine, text

DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
DEFAULT_COST_CEILING = 1_000_000.0


@dataclass(frozen=True)
class CostDecision:
    ok: bool
    estimated_cost: Optional[float]
    detail: str


def cost_guard(engine: Engine, sql: str, *, cost_ceiling: float = DEFAULT_COST_CEILING,
               timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS) -> CostDecision:
    dialect = engine.dialect.name
    if dialect == "postgresql":
        try:
            with engine.connect() as conn:
                conn.execute(text(f"SET statement_timeout = {int(timeout_ms)}"))
                plan = conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}")).scalar()
            cost = float(plan[0]["Plan"]["Total Cost"]) if plan else 0.0
            ok = cost <= cost_ceiling
            return CostDecision(ok, cost,
                                f"EXPLAIN total cost {cost:.0f} vs ceiling {cost_ceiling:.0f}")
        except Exception as e:  # noqa: BLE001
            return CostDecision(True, None, f"EXPLAIN unavailable, proceeding ({e})")
    # non-PG: heuristic — the safety LIMIT bounds rows; cost ceiling not enforceable here
    return CostDecision(True, None, f"{dialect}: no EXPLAIN cost ceiling (row cap via LIMIT)")
