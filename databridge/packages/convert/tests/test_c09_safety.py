"""C09 SQL safety allow-list (§15.5/§15.11) — the injection-prevention wall + EV-SEC corpus."""
from __future__ import annotations

import pytest

from databridge_convert.sqlsub import validate_sql

ALLOWED = {"users", "merchants", "transactions"}


def _ok(sql):
    return validate_sql(sql, allowed_tables=ALLOWED)


def test_clean_select_passes_and_gets_limit():
    r = _ok("SELECT name FROM merchants")
    assert r.ok and "LIMIT" in r.safe_sql.upper()


def test_existing_limit_preserved():
    r = _ok("SELECT name FROM merchants LIMIT 5")
    assert r.ok and "5" in r.safe_sql


# --- EV-SEC injection corpus: every one of these MUST be refused ----------------
INJECTIONS = [
    ("stacked DROP", "SELECT * FROM users; DROP TABLE users;--"),
    ("stacked DELETE", "SELECT 1; DELETE FROM users"),
    ("bare DROP", "DROP TABLE users"),
    ("DELETE", "DELETE FROM users WHERE 1=1"),
    ("UPDATE", "UPDATE users SET email='x'"),
    ("INSERT", "INSERT INTO users (id) VALUES (1)"),
    ("CREATE", "CREATE TABLE evil (x int)"),
    ("multi via comment", "SELECT * FROM users;-- ; DROP TABLE users\n; DROP TABLE merchants"),
    # write nested in a non-top-level position (caught by the whole-tree walk)
    ("SELECT INTO dump", "SELECT * INTO dump FROM users"),
    ("UNION INTO", "SELECT 1 UNION SELECT * INTO dump FROM users"),
    # statement-level side-effecting primitives in a wider dialect
    ("ATTACH", "ATTACH DATABASE 'evil.db' AS e"),
    ("PRAGMA", "PRAGMA table_info(users)"),
    # function-surface side effects (FINDING 1.1 — spec §15.5 system-function deny-list)
    ("sqlite load_extension", "SELECT load_extension('evil.so')"),
    ("sqlite writefile", "SELECT writefile('/tmp/x', email) FROM users"),
    ("pg_read_file", "SELECT pg_read_file('/etc/passwd')"),
    ("pg_sleep DoS", "SELECT pg_sleep(10)"),
    ("mssql xp_cmdshell", "SELECT name FROM users WHERE xp_cmdshell('dir') = 1"),
    ("mysql benchmark DoS", "SELECT benchmark(1000000, md5('x'))"),
]


@pytest.mark.parametrize("name,sql", INJECTIONS, ids=[n for n, _ in INJECTIONS])
def test_injection_refused(name, sql):
    r = _ok(sql)
    assert not r.ok and r.refusal is not None
    assert r.refusal.code in {"SQL_MULTI_STATEMENT", "SQL_FORBIDDEN", "SQL_NOT_SELECT",
                              "SQL_PARSE_ERROR", "SQL_FORBIDDEN_FUNCTION"}


def test_cte_reference_not_false_refused():
    # a CTE alias references the WITH body, not a base table — must NOT be SQL_UNKNOWN_TABLE
    r = _ok("WITH recent AS (SELECT id FROM merchants) SELECT id FROM recent")
    assert r.ok, r.refusal


def test_unknown_table_refused():
    r = _ok("SELECT * FROM secrets")
    assert not r.ok and r.refusal.code == "SQL_UNKNOWN_TABLE"


def test_known_join_passes():
    r = _ok("SELECT u.email FROM users u JOIN transactions t ON t.user_id = u.id")
    assert r.ok
