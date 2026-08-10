"""Live-Postgres verification (runs when the docker-compose fixture DB is reachable).

`docker compose -f fixtures/postgres/docker-compose.yml up -d`, then this confirms the
LIVE schema matches the static fixture, the heuristic finds the undeclared FK on real PG,
and the deterministic T06 roles hold (E1 groundwork against the live DB — the full LLM E1
re-run is a follow-up needing PG + a model up together). Skips cleanly when PG is down.
"""
from __future__ import annotations

import pytest

RO_URL = "postgresql+psycopg2://databridge_ro:databridge_ro@localhost:5432/fintech"
RW_URL = "postgresql+psycopg2://databridge:databridge@localhost:5432/fintech"

GOLD_ROLES = {"users": "entity", "merchants": "entity", "transactions": "event",
              "disputes": "event", "support_messages": "log"}


def _reachable(url: str):
    from sqlalchemy import create_engine, text
    eng = create_engine(url)
    with eng.connect() as c:
        c.execute(text("SELECT 1"))
    return eng


@pytest.mark.integration
def test_live_schema_matches_fixture_and_heuristic_fk():
    pytest.importorskip("psycopg2")
    try:
        eng = _reachable(RO_URL)
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"fintech Postgres not reachable: {e}")

    from databridge_ingest import extract_schema
    from databridge_semantic import build_report

    s = extract_schema(eng, source_id="fintech_pg")
    assert s.table_names == set(GOLD_ROLES)                 # live schema == static fixture

    sm = s.table("support_messages")
    assert any(not fk.declared and fk.column == "user_id" for fk in sm.foreign_keys)

    # deterministic roles (T06) on the LIVE extract match the gold (≥4/5) — E1 groundwork
    report = build_report(s)
    correct = sum(1 for t in report.tables if t.role.value == GOLD_ROLES.get(t.table))
    assert correct >= 4


@pytest.mark.integration
def test_readonly_role_is_actually_read_only():
    pytest.importorskip("psycopg2")
    try:
        eng = _reachable(RO_URL)
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"fintech Postgres not reachable: {e}")
    from databridge_convert.sqlsub import probe_readonly
    res = probe_readonly(eng, warn=False)
    assert res.read_only is True and res.flagged is False   # the _ro role cannot write
