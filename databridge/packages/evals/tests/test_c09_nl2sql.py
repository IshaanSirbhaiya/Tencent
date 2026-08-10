"""C09 NL→SQL: Demo D9 (result + provenance + injection refusal + training-serving reuse),
schema linking, and EV-SQL execution accuracy (gold backend = deterministic CI)."""
from __future__ import annotations

from databridge_convert.demos.d9 import fintech_sqlite_engine, run_d9, run_injection_attempt
from databridge_convert.sqlsub import link_schema
from databridge_evals import confirmed_fintech_schema, run_ev_sql, sql_gold_backend
from databridge_harness import FakeBackend, Harness

CFG = {
    "routing": {"sql_generation": "m", "sql_revision": "m"},
    "aliases": {"m": {"model": "anthropic/test"}},
    "profiles": {"cloud": {}, "airgapped": {}},
}


def test_d9_result_and_sql_provenance():
    r = run_d9()
    assert r["ok"] is True
    assert "join" in r["sql"].lower() and "merchant" in r["sql"].lower()
    assert len(r["rows"]) >= 1
    assert r["provenance_derivation"] == r["sql"]            # SQL-as-provenance (§17.1)
    assert {"merchants", "disputes", "transactions"} <= set(r["linked_tables"])


def test_d9_training_serving_reuse():
    r = run_d9()
    assert r["library_size"] == 1                           # confirmed query stored
    assert r["artifact_checksum_ok"] is True
    assert r["infer_matches"] is True                       # infer re-ran SAME SQL, no LLM


def test_d9_injection_refused_before_execution():
    r = run_injection_attempt()
    assert r["ok"] is False
    assert r["refusal_code"] == "SQL_MULTI_STATEMENT"
    assert r["rows"] == []                                  # never executed


def test_linking_selects_by_meaning():
    slc = link_schema(confirmed_fintech_schema(),
                      "top merchants by disputed amount last quarter")
    assert {"merchants", "disputes"} <= set(slc.tables)


def test_ev_sql_gold_backend_passes_gate():
    engine = fintech_sqlite_engine()
    h = Harness(FakeBackend(sql_gold_backend()), config=CFG, profile="cloud")
    res = run_ev_sql(h, engine)
    assert res.accuracy == 1.0 and res.passed             # gold SQL → 100% (pipeline proof)
    assert res.total == 12


def test_ev_sql_detects_wrong_sql():
    engine = fintech_sqlite_engine()

    def bad(*, model, messages, guided_json):
        return '{"sql": "SELECT 1 AS x"}'

    res = run_ev_sql(Harness(FakeBackend(bad), config=CFG, profile="cloud"), engine)
    assert res.accuracy < 1.0                             # metric isn't trivially 1.0
