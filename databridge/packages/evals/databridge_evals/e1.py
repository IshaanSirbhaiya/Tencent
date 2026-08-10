"""E1 — schema-annotation accuracy eval (07_PRD §7, 06_PRD §8).

Runs the full annotate pipeline (T06 deterministic + T07 LLM) against the fintech
fixture and scores per-column semantic-type accuracy vs a hand-built gold AnnotatedSchema.
Reports the slices 06_PRD §8 mandates: per-table, per-provenance (deterministic vs llm),
and per-confidence-bucket (the reliability diagram — calibration check from §4).

The metric is the SAME whatever backend produces the annotations; pass a FakeBackend
(deterministic CI plumbing) or a real Harness (the honest accuracy number, e.g. local
Ollama). The gate is reported, never silently adjusted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from databridge_harness import Harness
from databridge_ingest import fintech_fixture
from databridge_semantic import annotate_schema, build_report

GOLD_PATH = Path(__file__).parent / "data" / "gold_fintech.json"
# ASCII bucket names (safe to print/serialise on any console; reliability diagram §4).
_BUCKETS = [("lt_0.80", 0.0, 0.80), ("0.80_0.95", 0.80, 0.95), ("gte_0.95", 0.95, 1.01)]


def load_gold() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


@dataclass
class E1Result:
    accuracy: float
    correct: int
    total: int
    role_accuracy: float
    per_provenance: dict = field(default_factory=dict)
    per_table: dict = field(default_factory=dict)
    reliability: dict = field(default_factory=dict)         # bucket → (accuracy, n)
    model_map: dict = field(default_factory=dict)
    mistakes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suite": "E1", "metric": "per_column_semantic_type_accuracy",
            "value": round(self.accuracy, 4), "role_accuracy": round(self.role_accuracy, 4),
            "correct": self.correct, "total": self.total,
            "per_provenance": self.per_provenance, "per_table": self.per_table,
            "reliability": self.reliability, "model_map": self.model_map,
            "mistakes": self.mistakes,
        }


def run_e1(harness: Harness) -> E1Result:
    gold = load_gold()
    extract = fintech_fixture()
    report = build_report(extract)
    schema = annotate_schema(harness, extract, report)

    preds = {f"{t.table}.{c.column}": c for t in schema.tables for c in t.columns}

    correct = 0
    per_prov: dict[str, list[int]] = {}
    per_table: dict[str, list[int]] = {}
    buckets: dict[str, list[int]] = {b[0]: [0, 0] for b in _BUCKETS}
    mistakes = []
    for key, gold_type in gold["columns"].items():
        ann = preds.get(key)
        pred_type = ann.semantic_type.value if ann else "<missing>"
        prov = ann.provenance if ann else "missing"
        conf = ann.confidence if ann else 0.0
        ok = pred_type == gold_type
        correct += ok
        table = key.split(".")[0]
        per_prov.setdefault(prov, [0, 0]); per_prov[prov][0] += ok; per_prov[prov][1] += 1
        per_table.setdefault(table, [0, 0]); per_table[table][0] += ok; per_table[table][1] += 1
        for name, lo, hi in _BUCKETS:
            if lo <= conf < hi:
                buckets[name][0] += ok; buckets[name][1] += 1
                break
        if not ok:
            mistakes.append({"column": key, "gold": gold_type, "predicted": pred_type,
                             "provenance": prov, "confidence": round(conf, 3)})

    total = len(gold["columns"])
    roles_ok = sum(1 for t in schema.tables if t.role.value == gold["roles"].get(t.table))
    model = harness._resolve("schema_annotation")  # the model actually used

    def ratio(pair):
        return round(pair[0] / pair[1], 4) if pair[1] else None

    return E1Result(
        accuracy=correct / total, correct=correct, total=total,
        role_accuracy=roles_ok / len(gold["roles"]),
        per_provenance={k: {"accuracy": ratio(v), "n": v[1]} for k, v in per_prov.items()},
        per_table={k: {"accuracy": ratio(v), "n": v[1]} for k, v in per_table.items()},
        reliability={k: {"accuracy": ratio(v), "n": v[1]} for k, v in buckets.items()},
        model_map={"schema_annotation": model},
        mistakes=mistakes,
    )


# --- deterministic "gold backend" for the FakeBackend (CI plumbing) path ----------
def gold_backend(self_confidence: float = 0.9):
    """A FakeBackend handler that returns gold annotations — makes E1 deterministic in CI
    (proves the pipeline assembles correctly; accuracy on a real model is a separate run)."""
    gold = load_gold()["columns"]

    def handler(*, model, messages, guided_json) -> str:
        sysmsg = messages[0]["content"]
        user = messages[-1]["content"]
        if "BUSINESS OUTCOMES" in sysmsg:                       # label discovery
            return json.dumps({"candidates": [{
                "name": "fraud",
                "outcome_description": "transaction later disputed as fraud",
                "derivation_sql": "SELECT t.id, (d.reason = 'fraud') AS is_fraud "
                                  "FROM transactions t JOIN disputes d ON d.transaction_id = t.id",
                "source_tables": ["transactions", "disputes"],
                "positive_rate_estimate": 0.04,
                "caveats": ["label lags the transaction by the chargeback window (~30d)"]}]})
        if "name each relationship" in sysmsg or "relationship" in sysmsg:  # Stage C
            return json.dumps({"domain": "fintech", "relationships": []})
        # Stage B: pull the table name + the columns to classify, answer from gold
        m = re.search(r"Table:\s*(\w+)", user)
        table = m.group(1) if m else ""
        todo_m = re.search(r"Columns to classify:\s*(.*)", user)
        todo = [c.strip() for c in (todo_m.group(1).split(",") if todo_m else []) if c.strip()
                and c.strip() != "(none)"]
        cols = [{"column": c, "semantic_type": gold.get(f"{table}.{c}", "unknown"),
                 "description": f"{c} of {table}", "self_confidence": self_confidence}
                for c in todo]
        return json.dumps({"concept": f"{table} records", "columns": cols})

    return handler
