"""EV-SQL — NL→SQL execution accuracy (09_FINAL_PRD §23). Gate ≥70%.

~12 (question, gold-SQL) pairs against the fintech fixture (partial coverage; the full
60-question / 2-schema target is later once more fixtures exist). Execution accuracy =
does the GENERATED SQL's result set match the gold SQL's result set (value-multiset,
column-name- and order-independent). Run with a gold backend (deterministic CI) or a real
Harness (the honest number); the gate is reported, never silently adjusted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text

from databridge_convert.sqlsub import answer_question
from databridge_evals.fintech_schema import confirmed_fintech_schema

GATE = 0.70

# (question, gold SQL) — gold results are computed by running the gold SQL on the fixture.
PAIRS: list[tuple[str, str]] = [
    ("how many users are there", "SELECT COUNT(*) AS n FROM users"),
    ("list all merchant names", "SELECT name FROM merchants"),
    ("total transaction amount", "SELECT SUM(amount) AS total FROM transactions"),
    ("how many transactions per user", "SELECT user_id, COUNT(*) AS n FROM transactions GROUP BY user_id"),
    ("which transactions are disputed", "SELECT transaction_id FROM disputes"),
    ("top merchants by disputed amount",
     "SELECT m.name, SUM(d.amount_disputed) AS total FROM disputes d "
     "JOIN transactions t ON d.transaction_id = t.id JOIN merchants m ON t.merchant_id = m.id "
     "GROUP BY m.name ORDER BY total DESC"),
    ("count of active users", "SELECT COUNT(*) AS n FROM users WHERE is_active = 1"),
    ("average risk score of users", "SELECT AVG(risk_score) AS avg_risk FROM users"),
    ("merchant names in PH", "SELECT name FROM merchants WHERE country = 'PH'"),
    ("number of disputes", "SELECT COUNT(*) AS n FROM disputes"),
    ("transaction ids with status settled", "SELECT id FROM transactions WHERE status = 'settled'"),
    ("how many support messages are from agents", "SELECT COUNT(*) AS n FROM support_messages WHERE is_from_agent = 1"),
]


def _norm(v: object):
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return round(float(v), 4)
    return v


def _result_key(rows: list[dict]):
    # value-multiset, column-name- and order-independent
    return sorted(tuple(sorted((str(_norm(x)) for x in r.values()))) for r in rows)


def gold_results(engine) -> dict[str, list]:
    out = {}
    with engine.connect() as conn:
        for q, sql in PAIRS:
            out[q] = _result_key([dict(r) for r in conn.execute(text(sql)).mappings().all()])
    return out


@dataclass
class EvSqlResult:
    accuracy: float
    correct: int
    total: int
    passed: bool
    model_map: dict = field(default_factory=dict)
    per_question: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"suite": "EV-SQL", "metric": "execution_accuracy", "value": round(self.accuracy, 4),
                "gate": GATE, "passed": self.passed, "correct": self.correct, "total": self.total,
                "model_map": self.model_map, "per_question": self.per_question}


def run_ev_sql(harness, engine) -> EvSqlResult:
    schema = confirmed_fintech_schema()
    gold = gold_results(engine)
    correct = 0
    per_q = []
    for q, _ in PAIRS:
        res = answer_question(harness, engine, schema, q)
        match = bool(res.ok and _result_key(res.rows) == gold[q])
        correct += match
        per_q.append({"question": q, "ok": res.ok, "match": match,
                      "sql": (res.sql or "")[:120],
                      "refusal": res.refusal.code if res.refusal else None})
    total = len(PAIRS)
    model = harness._resolve("sql_generation")
    return EvSqlResult(accuracy=correct / total, correct=correct, total=total,
                       passed=(correct / total) >= GATE, model_map={"sql_generation": model},
                       per_question=per_q)


def gold_backend():
    """A FakeBackend handler that returns the gold SQL for each question (deterministic CI)."""
    by_q = {q: sql for q, sql in PAIRS}

    def handler(*, model, messages, guided_json) -> str:
        user = messages[-1]["content"]
        m = re.search(r"Question:\s*(.+)", user)
        q = m.group(1).strip() if m else ""
        return json.dumps({"sql": by_q.get(q, "SELECT 1")})

    return handler
