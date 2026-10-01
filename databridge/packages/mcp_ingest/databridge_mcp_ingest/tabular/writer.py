"""The SEPARATE write-capable database connection for read-write agents (never crawled).

Ingestion reads through a read-only role. An agent's declared write action needs a write-capable
tool, so it gets its OWN connection with its OWN least-privilege role:

* a writer role: CONNECT + USAGE on the schema + INSERT on ONE table (and USAGE on that table's id
  sequence) — no SELECT, no UPDATE, no DELETE, nothing else;
* its own connection (`purpose: agent_write`), listed as a distinct write tool in the agent's
  declared actions — never a read grant.

This module is a GENERIC renderer (R-022 f1 #8): it names no table, role, database, column or
action. What it renders is a WRITER SPEC in pack YAML — `packs/<pack>/writers/<name>.writer.yaml`
(`load_writer_spec`), validated here: name, action, database, schema, table, role, reader_role,
password_env, and the columns (name, SQL type, primary_key / not_null / default, and `insert: true`
for the columns the action may set). Adding a writer is a YAML change.

Secrets (R-022 f1 #9): the rendered DDL never contains a credential. The role is created `LOGIN`
without a password; `apply_writer_password(execute, spec)` resolves the secret from the
environment variable the spec names (`password_env`) and executes it through the caller's
privileged connection — it returns nothing and never logs, prints or persists the statement.
R-022 f2 #6: the PASSWORD never appears in the SQL text handed to `execute`. The statement carries
a SCRAM-SHA-256 verifier computed here (exactly what `psql \\password` sends; PostgreSQL stores
it as is), so a logging or echoing executor sees a salted hash, never the secret; and a failing
`execute` is re-raised as `WriterSpecError` without the statement or the driver's message.
`profiles/postgres/agent_writer.sql` is the rendering of the bundled spec, and a test asserts the
two agree.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
#: A column type: a SQL type name, optionally with a size, e.g. VARCHAR(12), NUMERIC(10, 2).
_TYPE = re.compile(r"^[A-Za-z][A-Za-z ]{0,30}(\(\s*\d+\s*(,\s*\d+\s*)?\))?$")
#: A column default: a numeric literal, a function of no arguments, or a keyword.
_DEFAULT = re.compile(r"^(-?\d+(\.\d+)?|[a-z_]+\(\)|CURRENT_TIMESTAMP|CURRENT_DATE|TRUE|FALSE)$",
                      re.I)
_SPEC_KEYS = {"name", "action", "database", "schema", "table", "role", "reader_role",
              "password_env", "columns"}
_COL_KEYS = {"name", "type", "primary_key", "not_null", "default", "insert"}

class WriterSpecError(ValueError):
    pass


def _ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"{name!r} is not a plain lower-case SQL identifier")
    return name


def validate_writer_spec(spec: dict) -> dict:
    """The spec, checked: every identifier plain, every type / default from a closed shape,
    exactly one table, at least one insertable column. Raises `WriterSpecError`."""
    if not isinstance(spec, dict):
        raise WriterSpecError("a writer spec is a mapping")
    extra = set(spec) - _SPEC_KEYS
    if extra:
        raise WriterSpecError(f"unknown writer spec keys {sorted(extra)}")
    try:
        for k in ("name", "action", "database", "schema", "table", "role", "reader_role"):
            _ident(spec.get(k))
    except ValueError as exc:
        raise WriterSpecError(str(exc)) from None
    env = spec.get("password_env")
    if not isinstance(env, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env):
        raise WriterSpecError("password_env must name an environment variable (never a value)")
    cols = spec.get("columns")
    if not isinstance(cols, list) or not cols:
        raise WriterSpecError("columns must be a non-empty list")
    for c in cols:
        if not isinstance(c, dict) or set(c) - _COL_KEYS:
            raise WriterSpecError(f"bad column entry {c!r}")
        try:
            _ident(c.get("name"))
        except ValueError as exc:
            raise WriterSpecError(str(exc)) from None
        if not isinstance(c.get("type"), str) or not _TYPE.match(c["type"]):
            raise WriterSpecError(f"column {c['name']!r}: type {c.get('type')!r} is not a plain "
                                  f"SQL type")
        if c.get("default") is not None and not _DEFAULT.match(str(c["default"])):
            raise WriterSpecError(f"column {c['name']!r}: default {c['default']!r} is not a "
                                  f"literal, a no-argument function or a keyword")
    if not any(c.get("insert") for c in cols):
        raise WriterSpecError("no column is insertable (`insert: true`)")
    return spec


def load_writer_spec(name: Optional[str] = None, *, path: Optional[Path] = None,
                     packs_root: Optional[Path] = None) -> dict:
    """A writer spec from `packs/<pack>/writers/<name>.writer.yaml` (exactly one match), or from
    `path`. With no name, the ONE writer spec the packs declare (refused when there are several)."""
    if path is None:
        from .. import paths

        root = Path(packs_root) if packs_root else paths.packs_dir()
        pattern = f"*/writers/{_ident(name)}.writer.yaml" if name else "*/writers/*.writer.yaml"
        found = sorted(root.glob(pattern))
        if len(found) != 1:
            raise WriterSpecError(f"writer spec {name or '*'!r}: {len(found)} found under "
                                  f"{root} (expected exactly one)")
        path = found[0]
    return validate_writer_spec(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def _spec(spec: Optional[dict]) -> dict:
    return validate_writer_spec(spec) if spec is not None else load_writer_spec()


def _serial_pk(spec: dict) -> Optional[str]:
    return next((c["name"] for c in spec["columns"]
                 if c.get("primary_key") and "SERIAL" in str(c["type"]).upper()), None)


def _column_ddl(spec: dict) -> str:
    width = max(len(c["name"]) for c in spec["columns"]) + 1
    twidth = max(len(c["type"]) for c in spec["columns"]) + 1
    lines = []
    for c in spec["columns"]:
        extra = []
        if c.get("primary_key"):
            extra.append("PRIMARY KEY")
        if c.get("not_null"):
            extra.append("NOT NULL")
        if c.get("default") is not None:
            extra.append(f"DEFAULT {c['default']}")
        lines.append(f"    {c['name']:<{width}} {c['type']:<{twidth}} {' '.join(extra)}".rstrip())
    return ",\n".join(lines)


def writer_role_sql(spec: Optional[dict] = None, *, schema: Optional[str] = None,
                    role: Optional[str] = None, table: Optional[str] = None,
                    database: Optional[str] = None, create_schema: bool = False) -> str:
    """DDL for the spec's table and its INSERT-only role — with NO credential in it (#9). The
    keyword arguments override the spec's names (a scratch schema / role in a test)."""
    sp = _spec(spec)
    s, r = _ident(schema or sp["schema"]), _ident(role or sp["role"])
    t, d = _ident(table or sp["table"]), _ident(database or sp["database"])
    reader = _ident(sp["reader_role"])
    lines = [f"-- Write-capable connection for read-write agents: INSERT on {s}.{t} ONLY.",
             "-- Apply as the database owner. No credential is rendered here: set the role's "
             f"password with apply_writer_password (from ${sp['password_env']})."]
    if create_schema:
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {s};")
    lines += [f"CREATE TABLE IF NOT EXISTS {s}.{t} (\n{_column_ddl(sp)}\n);",
              f"CREATE ROLE {r} LOGIN;",
              f"GRANT CONNECT ON DATABASE {d} TO {r};",
              f"GRANT USAGE ON SCHEMA {s} TO {r};",
              f"GRANT INSERT ON {s}.{t} TO {r};"]
    serial = _serial_pk(sp)
    if serial:
        lines.append(f"GRANT USAGE ON SEQUENCE {s}.{t}_{serial}_seq TO {r};")
    lines += ["-- The reader sees what agents wrote, so the next sync re-ingests it (closes the "
              "loop):",
              f"GRANT SELECT ON {s}.{t} TO {reader};"]
    return "\n".join(lines) + "\n"


def apply_writer_password(execute: Callable[[str], Any], spec: Optional[dict] = None, *,
                          role: Optional[str] = None, password_env: Optional[str] = None) -> None:
    """Set the writer role's password from the environment variable the spec names, through the
    caller's privileged `execute` (R-022 f1 #9). The secret is read here and handed straight to
    `execute`; nothing is returned, logged or written. Raises when the variable is unset."""
    sp = _spec(spec)
    r = _ident(role or sp["role"])
    env = password_env or sp["password_env"]
    secret = os.environ.get(env)
    if not secret:
        raise WriterSpecError(f"environment variable {env} is not set: the writer role's "
                              f"password comes only from the environment")
    verifier = scram_sha256_verifier(secret)
    failed: Optional[str] = None
    try:
        execute(f"ALTER ROLE {r} PASSWORD '{verifier}'")
    except Exception as exc:                                         # noqa: BLE001
        failed = type(exc).__name__
    if failed is not None:
        # raised OUTSIDE the handler: neither the statement nor the driver's message (which may
        # quote it) propagates, not even as __context__
        raise WriterSpecError(f"setting the password of role {r} failed ({failed}); the "
                              f"statement is not shown")


SCRAM_ITERATIONS = 4096


def scram_sha256_verifier(password: str, *, salt: Optional[bytes] = None,
                          iterations: int = SCRAM_ITERATIONS) -> str:
    """The PostgreSQL SCRAM-SHA-256 verifier of `password` (RFC 5802/7677, the format
    `pg_authid.rolpassword` stores): `SCRAM-SHA-256$<iter>:<salt>$<StoredKey>:<ServerKey>`. The
    password is normalised NFKC (SASLprep's normalisation step). The result is a salted hash, not
    the password, and contains no quote."""
    salt = salt if salt is not None else os.urandom(16)
    pw = unicodedata.normalize("NFKC", password).encode("utf-8")
    salted = hashlib.pbkdf2_hmac("sha256", pw, salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")            # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def writer_drop_sql(spec: Optional[dict] = None, *, schema: Optional[str] = None,
                    role: Optional[str] = None, table: Optional[str] = None,
                    database: Optional[str] = None, drop_schema: bool = False) -> str:
    sp = _spec(spec)
    s, r = _ident(schema or sp["schema"]), _ident(role or sp["role"])
    t, d = _ident(table or sp["table"]), _ident(database or sp["database"])
    lines = [f"REVOKE ALL ON {s}.{t} FROM {r};"]
    serial = _serial_pk(sp)
    if serial:
        lines.append(f"REVOKE ALL ON SEQUENCE {s}.{t}_{serial}_seq FROM {r};")
    lines += [f"REVOKE ALL ON SCHEMA {s} FROM {r};",
              f"REVOKE ALL ON DATABASE {d} FROM {r};",
              f"DROP ROLE IF EXISTS {r};"]
    if drop_schema:
        lines.append(f"DROP SCHEMA IF EXISTS {s} CASCADE;")
    return "\n".join(lines) + "\n"


def insert_sql(values: dict, spec: Optional[dict] = None, *, schema: Optional[str] = None,
               table: Optional[str] = None) -> str:
    """One INSERT for the spec's action, values quoted as literals, only the columns the spec
    marks `insert: true`. The runtime shows this exact statement in the approval card before it
    runs."""
    sp = _spec(spec)
    allowed = {c["name"] for c in sp["columns"] if c.get("insert")}
    bad = set(values) - allowed
    if bad:
        raise ValueError(f"unknown {sp['table']} columns {sorted(bad)}")
    cols = sorted(values)
    lits = ["NULL" if values[c] is None else "'" + str(values[c]).replace("'", "''") + "'"
            for c in cols]
    return (f"INSERT INTO {_ident(schema or sp['schema'])}.{_ident(table or sp['table'])} "
            f"({', '.join(cols)}) VALUES ({', '.join(lits)})")


__all__ = ["writer_role_sql", "writer_drop_sql", "insert_sql", "apply_writer_password",
           "scram_sha256_verifier",
           "load_writer_spec", "validate_writer_spec", "WriterSpecError"]
