"""Demo D9 (09_FINAL_PRD §27): NL → safe SQL → validated result against the fintech fixture.

"top merchants by disputed amount last quarter" → linking → generate → sqlglot-clean SQL →
read-only execution → result + SQL-as-provenance. Plus the adversarial case: an injection
NL ("...; DROP TABLE users;--") whose generated SQL is REFUSED by safety BEFORE execution.

The model surface is FakeBackend (deterministic, no keys); the honest live number is the
separate EV-SQL run vs Ollama. The DB is in-process SQLite seeded from the fixture SQL.
"""
from __future__ import annotations

import pathlib

import orjson
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from databridge_harness import FakeBackend, Harness
from ..sqlsub.nlquery import answer_question, build_query_artifact, confirm_query, infer_query
from ..sqlsub.query_library import ConfirmedQueryLibrary

_SEED = (pathlib.Path(__file__).resolve().parents[4]
         / "fixtures" / "postgres" / "seed.sql")

GOLD_D9_SQL = (
    "SELECT m.name AS merchant, SUM(d.amount_disputed) AS total_disputed "
    "FROM disputes d "
    "JOIN transactions t ON d.transaction_id = t.id "
    "JOIN merchants m ON t.merchant_id = m.id "
    "GROUP BY m.name ORDER BY total_disputed DESC"
)
INJECTION_SQL = "SELECT * FROM users; DROP TABLE users;--"

_CFG = {
    "routing": {"sql_generation": "m", "sql_revision": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}


def fintech_sqlite_engine() -> Engine:
    """An in-process SQLite engine seeded from the SAME fixture SQL (StaticPool = shared)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    raw = engine.raw_connection()
    raw.executescript(_SEED.read_text(encoding="utf-8"))
    raw.commit()
    return engine


def _harness(sql: str) -> Harness:
    return Harness(FakeBackend(orjson.dumps({"sql": sql}).decode()), config=_CFG, profile="cloud")


def run_d9() -> dict:
    from databridge_evals.fintech_schema import confirmed_fintech_schema   # lazy (avoid cycle)
    engine = fintech_sqlite_engine()
    schema = confirmed_fintech_schema()
    library = ConfirmedQueryLibrary()
    question = "top merchants by disputed amount last quarter"

    result = answer_question(_harness(GOLD_D9_SQL), engine, schema, question, library=library)

    # training-serving reuse (§15.12): confirm → artifact → infer (re-runs SAME SQL, no LLM)
    confirm_query(library, result)
    artifact = build_query_artifact(result, artifact_id="D9", approved_by="auto-test")
    infer_rows = infer_query(artifact, engine)

    return {
        "question": question,
        "ok": result.ok,
        "sql": result.sql,
        "rows": result.rows,
        "provenance_derivation": result.provenance.derivation if result.provenance else None,
        "linked_tables": sorted(result.slice.tables),
        "library_size": len(library),
        "artifact_checksum_ok": artifact.verify_checksum(),
        "infer_rows": infer_rows,
        "infer_matches": infer_rows == result.rows,
    }


def run_injection_attempt() -> dict:
    """The adversarial case: generated SQL contains a stacked DROP → REFUSED before exec."""
    from databridge_evals.fintech_schema import confirmed_fintech_schema   # lazy (avoid cycle)
    engine = fintech_sqlite_engine()
    schema = confirmed_fintech_schema()
    result = answer_question(_harness(INJECTION_SQL), engine, schema,
                             "show users; DROP TABLE users;--")
    return {"ok": result.ok,
            "refusal_code": result.refusal.code if result.refusal else None,
            "rows": result.rows}
