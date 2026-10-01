"""R-022 f2 (final) — dialect drafting: #3 database-enforced read-only, #11 every required
statement blocks approval, #17 combined profiling verified across all probed tables."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from test_w4_dialects import EngineClient, sqlite_exec


def _db(path: Path) -> Path:
    """`aaa_notes` sorts FIRST (list_tables orders by name) and holds only TEXT; the temporal and
    numeric columns appear only in a LATER table."""
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE aaa_notes (code TEXT PRIMARY KEY, body TEXT);
        CREATE TABLE zzz_orders (id INTEGER PRIMARY KEY, placed DATE, amount REAL);
        INSERT INTO aaa_notes VALUES ('a', 'first note'), ('b', 'second');
        INSERT INTO zzz_orders VALUES (1, '2024-01-01', 10.5), (2, '2024-03-05', 3.25);
    """)
    db.commit()
    db.close()
    return path


def _sqlite_template() -> dict:
    from databridge_mcp_ingest.tabular.dialect_draft import load_templates

    t = next(t for t in load_templates() if t["engine"] == "sqlite")
    return {k: v for k, v in t.items() if not k.startswith("_")}


def _proposal(tpl: dict, db: Path, *, origin: str = "rules"):
    from databridge_mcp_ingest.tabular.dialect_draft import (DialectProposal, SqlTool,
                                                             probe_template, template_digest)

    rep, probed = probe_template(SqlTool(EngineClient(sqlite_exec(db)[0]), "query", "sql"), tpl,
                                 origin=origin)
    rep["template_sha256"] = template_digest(probed)
    return DialectProposal(source_id="x", origin=origin, template=probed, probe=rep), rep


# === #3 approval needs the DATABASE to have enforced read-only =================================
def test_fxj4_3_a_probe_not_read_only_by_the_database_is_not_approvable(tmp_path):
    from databridge_mcp_ingest.tabular.dialect_draft import approval_problems

    db = _db(tmp_path / "ro.sqlite")
    good, rep = _proposal(_sqlite_template(), db)
    assert rep["read_only"]["enforced_by"] == "database session"
    assert approval_problems(good) == []                                  # non-vacuous

    bare = {k: v for k, v in _sqlite_template().items() if k != "read_only_session"}
    p, rep = _proposal(bare, _db(tmp_path / "ro2.sqlite"))
    assert rep["failed"] == [] and rep["unprobed"] == []                  # the probe ran clean
    assert any("read_only_session" in x for x in approval_problems(p))

    # a session switch whose check does not answer the expectation is not database-enforced
    wrong = {**_sqlite_template(), "read_only_session": {
        **_sqlite_template()["read_only_session"], "expect": "^on$"}}
    p, rep = _proposal(wrong, _db(tmp_path / "ro3.sqlite"))
    assert rep["read_only"]["enforced_by"] != "database session"
    assert any("not verified read-only" in x for x in approval_problems(p))


def test_fxj4_3_a_model_drafted_session_check_is_not_trusted(tmp_path):
    from databridge_mcp_ingest.tabular.dialect_draft import approval_problems

    # a check that always answers "1" would "verify" anything
    fake = {**_sqlite_template(), "status": "draft", "unverified": "model draft",
            "read_only_session": {"sql": "PRAGMA query_only = ON",
                                  "check": "SELECT 1 AS v", "expect": "^1$"}}
    p, rep = _proposal(fake, _db(tmp_path / "m.sqlite"), origin="model")
    assert rep["read_only"]["enforced_by"] == "database session"          # it "verified"
    assert any("model-drafted read_only_session" in x for x in approval_problems(p))
    # the SHIPPED pair, verbatim, is accepted for a model draft
    vetted = {**fake, "read_only_session": _sqlite_template()["read_only_session"]}
    p, _ = _proposal(vetted, _db(tmp_path / "m2.sqlite"), origin="model")
    assert not any("read_only" in x for x in approval_problems(p))


# === #11 every REQUIRED statement that fails blocks approval ===================================
@pytest.mark.parametrize("stmt", ["aggregate", "select", "row_estimate"])
def test_fxj4_11_a_failed_required_statement_blocks_approval(walnut_env, tmp_path, monkeypatch,
                                                             stmt):
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.tabular.dialect_draft import (REQUIRED, approval_problems,
                                                             draft_dialect)

    assert stmt in REQUIRED
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")
    monkeypatch.setenv("WALNUT_COMMAND_SOURCES", "never-started")
    register_connection({"id": "r11", "transport": {"type": "stdio", "command": "never-started",
                                                     "args": []}})
    db = _db(tmp_path / "r.sqlite")
    ok = draft_dialect("r11", client=EngineClient(sqlite_exec(db)[0]), save=False,
                       templates=[_sqlite_template()])
    assert ok.problems == [] and approval_problems(ok) == []              # non-vacuous
    broken = {**_sqlite_template(), stmt: _sqlite_template()[stmt].replace(
        "FROM {schema|ident}.{table|ident}", "FROM {schema|ident}.{table|ident} "
                                             "WHERE no_such_column = 1", 1)}
    assert broken[stmt] != _sqlite_template()[stmt]
    p = draft_dialect("r11", client=EngineClient(sqlite_exec(db)[0]), save=False,
                      templates=[broken])
    assert stmt in p.probe["failed"]
    assert any(stmt in x for x in p.problems), p.problems
    # and approval refuses it even if `problems` were emptied on disk
    assert any("required statements failed" in x
               for x in approval_problems(p.model_copy(update={"problems": []})))


# === #17 combined profiling is verified against EVERY probed table's type classes ==============
def test_fxj4_17_a_combined_range_broken_only_for_a_later_tables_types_is_caught(tmp_path):
    from databridge_mcp_ingest.tabular.dialect_draft import SqlTool, probe_template

    db = _db(tmp_path / "c.sqlite")
    rep, _ = probe_template(SqlTool(EngineClient(sqlite_exec(db)[0]), "query", "sql"),
                            _sqlite_template())
    ts = rep["queries"]["table_stats"]
    assert ts["ok"] and any(c.startswith("main.zzz_orders.") for c in ts["columns_compared"])
    tpl = _sqlite_template()
    bad = {**tpl, "table_stats_range": tpl["table_stats_range"].replace(
        "MAX({column|ident}) AS {p}_hi", "MAX({column|ident}) || 'x' AS {p}_hi")}
    assert bad["table_stats_range"] != tpl["table_stats_range"]
    rep, _ = probe_template(SqlTool(EngineClient(sqlite_exec(db)[0]), "query", "sql"), bad)
    assert not rep["queries"]["table_stats"]["ok"]
    assert "hi" in rep["queries"]["table_stats"]["error"]
