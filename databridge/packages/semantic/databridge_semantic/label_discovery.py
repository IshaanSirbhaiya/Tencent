"""Label discovery (06_PRD §5) — P5 made mechanical: find ground truth, never fabricate.

The Stage-C prompt asks the model to surface columns/conditions that record BUSINESS
OUTCOMES usable as ML labels (fraud, churn, default…) as SQL-like derivations — and to
return an EMPTY list if none exist. Every candidate then passes DETERMINISTIC plausibility
checks before any human sees it: the derivation parses (sqlglot), referenced tables exist,
and the estimated positive rate is sanity-bounded. A "fraud" candidate at 40% positive
rate is flagged as probably-mislabelled logic.
"""
from __future__ import annotations

from typing import Optional

import sqlglot
from pydantic import BaseModel

from databridge_core import LabelCandidate, TableAnnotation
from databridge_ingest import SchemaExtract
from databridge_harness import Harness, StructuredOutputError


class _LabelGuess(BaseModel):
    name: str
    outcome_description: str = ""
    derivation_sql: str = ""
    source_tables: list[str] = []
    positive_rate_estimate: Optional[float] = None
    caveats: list[str] = []


class _LabelResult(BaseModel):
    candidates: list[_LabelGuess] = []


def plausibility_check(guess: _LabelGuess, extract: SchemaExtract) -> tuple[bool, list[str]]:
    """Deterministic gate (06_PRD §5). Returns (ok, reasons-it-failed-or-was-caveated)."""
    reasons: list[str] = []

    # 1) derivation parses
    if not guess.derivation_sql.strip():
        reasons.append("empty derivation_sql")
    else:
        try:
            sqlglot.parse_one(guess.derivation_sql)
        except Exception as e:  # noqa: BLE001
            reasons.append(f"derivation_sql does not parse: {e}")

    # 2) referenced tables exist
    unknown = [t for t in guess.source_tables if t not in extract.table_names]
    if unknown:
        reasons.append(f"references unknown tables: {unknown}")
    referenced = _tables_in_sql(guess.derivation_sql)
    unknown_sql = [t for t in referenced if t not in extract.table_names]
    if unknown_sql:
        reasons.append(f"derivation references unknown tables: {unknown_sql}")

    # 3) positive-rate sanity bounds
    pr = guess.positive_rate_estimate
    if pr is not None:
        if not (0.0 <= pr <= 1.0):
            reasons.append(f"positive_rate_estimate {pr} out of [0,1]")
        elif pr > 0.30:
            reasons.append(f"positive_rate_estimate {pr} implausibly high for a rare "
                           "outcome — probable mislabelled logic")

    # A high-but-in-range positive rate is a CAVEAT, not a hard fail; parse failure,
    # unknown table, out-of-range rate, or empty derivation ARE hard fails.
    hard_fail = any(("does not parse" in r) or ("unknown table" in r) or
                    ("out of [0,1]" in r) or ("empty derivation" in r) for r in reasons)
    return (not hard_fail), reasons


def _tables_in_sql(sql: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(sql)
    except Exception:  # noqa: BLE001
        return []
    return sorted({t.name for t in tree.find_all(sqlglot.exp.Table)})


def discover_labels(harness: Harness, extract: SchemaExtract,
                    tables: list[TableAnnotation]) -> list[LabelCandidate]:
    """Stage C label-candidate discovery + plausibility filtering."""
    summaries = "\n".join(f"  - {t.table}: {t.concept} [{t.role.value}] "
                          f"cols={[c.column for c in t.columns]}" for t in tables)
    sys = (
        "Identify columns, or conditions derivable across these tables, that record "
        "BUSINESS OUTCOMES usable as ML labels — disputes/chargebacks (fraud), "
        "cancellations + inactivity (churn), resolution/satisfaction (quality), missed "
        "payments (default). Output derivation logic as SQL-like pseudocode over the named "
        "tables/columns. Do NOT invent outcomes; if none exist, return an empty list. For "
        "each, state the time-lag caveat if the outcome trails the event it labels."
    )
    try:
        result = harness.complete_structured(
            "cross_table_inference",
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"Tables:\n{summaries}"}],
            _LabelResult, strict=True,
        ).parsed
    except StructuredOutputError:
        return []                                       # model couldn't propose cleanly → none

    out: list[LabelCandidate] = []
    for g in result.candidates:
        ok, reasons = plausibility_check(g, extract)
        if not ok:
            continue                                    # hard-failed: drop it
        caveats = list(g.caveats) + [r for r in reasons if "implausibly" in r]
        out.append(LabelCandidate(
            name=g.name, outcome_description=g.outcome_description,
            derivation_sql=g.derivation_sql, source_tables=g.source_tables,
            positive_rate_estimate=g.positive_rate_estimate, caveats=caveats,
            status="proposed",
        ))
    return out
