"""Tests for the estate provisioner (`scripts/estates.py`).

These run against the real fixtures on SQLite. They are the reason the provisioner can
be trusted in a demo: if the translation stopped being mechanical, or a load started
dropping rows, or the estates' planted orphans quietly disappeared, one of these fails.

They do NOT hit PostgreSQL. The PostgreSQL path is covered only to the extent that can
be checked without a server — that it sends the shipped SQL unaltered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from estate_lib import probes as probe_mod
from estate_lib import verify as verify_mod
from estate_lib.backends import SqliteBackend
from estate_lib.discovery import discover_estates, resolve_relational
from estate_lib.pg_to_sqlite import (
    TRANSLATOR_VERSION,
    split_statements,
    strip_psql_meta,
    sub_outside_strings,
    translate,
)

REPO = Path(__file__).resolve().parent.parent
VAR = REPO / "var" / "estates"


@pytest.fixture(scope="module")
def estates():
    return discover_estates(REPO)


@pytest.fixture(scope="module")
def relational(estates):
    return [resolve_relational(s) for e in estates for s in e.relational]


@pytest.fixture(scope="module")
def backend():
    return SqliteBackend(VAR)


@pytest.fixture(scope="module")
def provisioned(backend, relational):
    """Load every relational source once. Idempotent, so repeat runs are ~free."""
    return [backend.provision(rel) for rel in relational]


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_every_estate_declares_sources(estates):
    assert estates, "no packs with a sources.yaml were found"
    for e in estates:
        assert e.sources, f"{e.pack} declares no sources"


def test_relational_sources_resolve_their_own_sql(relational):
    assert relational, "no relational sources discovered"
    for rel in relational:
        assert rel.schema, f"{rel.id}: no schema parsed from the DDL"
        assert len(rel.sql_files) > 1, f"{rel.id}: loader resolved to a single file"
        # the schema must come first, or a seed would insert into a table that does not exist
        first = rel.sql_files[0].read_text(encoding="utf-8")
        assert "CREATE SCHEMA" in first, f"{rel.id}: first SQL file is not the schema"
        assert all(p.exists() for p in rel.sql_files)


def test_fingerprint_is_stable_and_content_addressed(relational):
    for rel in relational:
        assert rel.fingerprint(TRANSLATOR_VERSION) == rel.fingerprint(TRANSLATOR_VERSION)
        assert rel.fingerprint("1") != rel.fingerprint("2")


# ---------------------------------------------------------------------------
# translation
# ---------------------------------------------------------------------------


def test_ddl_translates_without_an_unhandled_construct(relational):
    for rel in relational:
        ddl = [p for p in rel.sql_files if "seed" not in p.parts]
        tr = translate("\n".join(p.read_text(encoding="utf-8") for p in ddl), rel.schema)
        joined = "\n".join(tr.statements)
        assert "COMMENT ON" not in joined, f"{rel.id}: a COMMENT survived into SQLite DDL"
        assert "CREATE SCHEMA" not in joined
        assert "::" not in joined, f"{rel.id}: a PostgreSQL cast survived"
        assert "date_trunc" not in joined.lower()
        assert f"{rel.schema}." not in joined, f"{rel.id}: a schema qualifier survived"
        assert tr.comments, f"{rel.id}: COMMENTs were dropped rather than captured"


def test_translation_records_every_rewrite_it_makes(relational):
    for rel in relational:
        ddl = [p for p in rel.sql_files if "seed" not in p.parts]
        tr = translate("\n".join(p.read_text(encoding="utf-8") for p in ddl), rel.schema)
        assert tr.rewrites, f"{rel.id}: PostgreSQL DDL translated with no recorded rewrite"
        for rw in tr.rewrites:
            assert rw.rule and rw.before, "a rewrite must name its rule and what it changed"


def test_schema_qualifier_is_never_stripped_inside_a_string_literal():
    """Free text in this estate genuinely says 'CRM.' — data must survive translation."""
    import re

    sql = (
        "INSERT INTO crm.interaction (interaction_id, summary) VALUES "
        "('I-1', 'Logged in CRM. Follow up via crm.'), ('I-2', 'crm.contact was wrong')"
    )
    out, hit = sub_outside_strings(re.compile(r"\bcrm\.", re.I), "", sql)
    assert hit
    assert out.startswith("INSERT INTO interaction")
    assert "'Logged in CRM. Follow up via crm.'" in out
    assert "'crm.contact was wrong'" in out


def test_casts_and_date_trunc_inside_free_text_are_left_alone():
    """`::` and `date_trunc(` appear in prose. Rewriting one there would edit the data."""
    sql = (
        "INSERT INTO t (note) VALUES "
        "('ref foo::bar, see date_trunc(''month'', x) in the runbook')"
    )
    tr = translate(sql, "nosuchschema")
    assert tr.statements == [sql]
    assert not [r for r in tr.rewrites if r.rule in ("cast_operator", "date_trunc")]


def test_statement_splitter_respects_quotes_and_comments():
    sql = (
        "-- a banner; with a semicolon\n"
        "CREATE TABLE t (a TEXT);\n"
        "INSERT INTO t VALUES ('semi; colon'), ('quote '' inside');\n"
    )
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE TABLE")
    assert "semi; colon" in stmts[1]


def test_psql_meta_commands_are_removed_not_executed(relational):
    for rel in relational:
        raw = "\n".join(p.read_text(encoding="utf-8") for p in rel.sql_files)
        stripped, dropped = strip_psql_meta(raw)
        assert not any(line.lstrip().startswith("\\") for line in stripped.splitlines())
        # \i / \ir are resolved by discovery, so nothing left may be an include
        assert not [d for d in dropped if d.startswith(("\\i ", "\\ir "))]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def test_all_relational_sources_provision(provisioned):
    assert provisioned
    for res in provisioned:
        assert res.fingerprint
        assert (VAR / res.pack / f"{res.source_id}.sqlite").exists()


def test_provisioning_twice_does_not_reload(backend, relational, provisioned):
    for rel in relational:
        again = backend.provision(rel)
        assert not again.loaded, f"{rel.id}: a second provision reloaded the data"
        assert backend.is_provisioned(rel)


def test_row_counts_match_the_estates_declared_counts(backend, estates, relational, provisioned):
    checked = 0
    for estate in estates:
        rels = [r for r in relational if r.pack == estate.pack]
        if not rels:
            continue
        conn = backend.open_estate(rels)
        try:
            res = verify_mod.verify_estate(conn, estate, rels, backend.name)
        finally:
            conn.close()
        assert res.expectations_file is not None, f"{estate.pack}: no declared counts found"
        for c in res.counts:
            if c.expected is not None:
                checked += 1
                assert c.rows == c.expected, f"{c.key}: loaded {c.rows}, expected {c.expected}"
        assert not res.unmatched_expectations, (
            f"{estate.pack}: declared counts with no loaded relation: "
            f"{sorted(res.unmatched_expectations)}"
        )
    assert checked >= 30, "far fewer tables were checked than the estates ship"


def test_declared_foreign_keys_hold(backend, estates, relational, provisioned):
    for estate in estates:
        rels = [r for r in relational if r.pack == estate.pack]
        if not rels:
            continue
        conn = backend.open_estate(rels)
        try:
            results = verify_mod.check_integrity(conn, estate, rels)
        finally:
            conn.close()
        declared = [r for r in results if r.declared]
        assert declared, f"{estate.pack}: no declared foreign keys were found in the catalog"
        broken = [r.fk.label() for r in declared if r.orphans]
        assert not broken, f"{estate.pack}: declared FKs with orphans: {broken}"


def test_planted_orphans_are_actually_present(backend, estates, relational, provisioned):
    """The deliberate orphans are the fixture's point; a load that loses them is broken."""
    seen = 0
    for estate in estates:
        rels = [r for r in relational if r.pack == estate.pack]
        if not rels:
            continue
        conn = backend.open_estate(rels)
        try:
            results = verify_mod.check_integrity(conn, estate, rels)
        finally:
            conn.close()
        for r in results:
            if r.declared or r.declared_rate is None or not r.non_null:
                continue
            seen += 1
            assert r.orphans > 0, f"{r.fk.label()}: declares ~{r.declared_rate:.0%} orphans, found none"
            assert r.rate < r.declared_rate * 3, (
                f"{r.fk.label()}: {r.rate:.1%} orphans against a declared ~{r.declared_rate:.0%}"
            )
    assert seen, "no estate declared an undeclared-FK orphan rate to check"


def test_postgres_comments_are_preserved_not_discarded(backend, relational, provisioned):
    """SQLite has no COMMENT ON, and those comments carry the estate's hazard notes.

    The count in `_pg_comment` must equal the number of COMMENT ON statements in the
    shipped DDL — no keyword matching, so this holds for any pack.
    """
    import re
    import sqlite3

    for rel in relational:
        ddl = "\n".join(
            p.read_text(encoding="utf-8") for p in rel.sql_files if "seed" not in p.parts
        )
        declared = len(re.findall(r"\bCOMMENT\s+ON\b", ddl, re.I))
        con = sqlite3.connect(backend.db_path(rel))
        try:
            captured = con.execute("SELECT count(*) FROM _pg_comment").fetchone()[0]
            longest = con.execute("SELECT max(length(comment)) FROM _pg_comment").fetchone()[0]
        finally:
            con.close()
        assert declared > 0, f"{rel.id}: the DDL declares no COMMENTs to preserve"
        assert captured == declared, (
            f"{rel.id}: {declared} COMMENT ON in the DDL but {captured} in _pg_comment"
        )
        assert longest and longest > 20, f"{rel.id}: comment bodies were truncated"


# ---------------------------------------------------------------------------
# the PostgreSQL path (as much of it as can be checked with no server)
# ---------------------------------------------------------------------------


def test_postgres_path_sends_the_shipped_sql_unaltered(relational):
    """PostgreSQL is the dialect the estates were written in — no translation applies.

    This asserts the property that makes the second backend worth having: the DDL that
    reaches a real server is the estate's own, schema qualifiers, COMMENTs and casts
    intact. Only the psql meta-commands are handled here rather than by psql.
    """
    for rel in relational:
        raw = "\n".join(p.read_text(encoding="utf-8") for p in rel.sql_files)
        sql, meta = strip_psql_meta(raw)
        statements = split_statements(sql)
        joined = "\n".join(statements)
        assert any(s.upper().startswith("CREATE SCHEMA") for s in statements), rel.id
        assert "COMMENT ON" in joined, f"{rel.id}: COMMENTs must reach PostgreSQL intact"
        assert f"{rel.schema}." in joined, f"{rel.id}: schema qualifiers must survive"
        assert all(not m.startswith(("\\i ", "\\ir ")) for m in meta)


def test_postgres_backend_reports_unavailability_rather_than_hanging():
    """A demo must be told PostgreSQL is down, not wait on a TCP timeout."""
    from estate_lib.backends import BackendUnavailable, PostgresBackend

    be = PostgresBackend("postgresql://nobody:nobody@127.0.0.1:1/none", connect_timeout=1)
    assert "connect_timeout" in be.dsn
    assert "nobody:***@" in be._safe_dsn(), "credentials must not be echoed"
    with pytest.raises(BackendUnavailable):
        be.probe()


# ---------------------------------------------------------------------------
# non-relational
# ---------------------------------------------------------------------------


def test_every_source_is_either_loaded_or_probed(estates, relational):
    loaded = {(r.pack, r.id) for r in relational}
    for estate in estates:
        for spec in estate.sources:
            if (estate.pack, spec.id) in loaded:
                continue
            p = probe_mod.probe_source(spec)
            assert not p.problems, f"{estate.pack}/{spec.id}: {p.problems}"
            if spec.kind != "json_api":
                assert p.files > 0, f"{estate.pack}/{spec.id}: probed no files"
                assert p.metrics, f"{estate.pack}/{spec.id}: probed no countable metric"


def test_non_relational_counts_match_where_the_estate_declares_them(estates, relational):
    loaded_schemas = {r.schema for r in relational}
    matched = 0
    for estate in estates:
        expectations, _ = verify_mod.load_expectations(estate)
        expectations = {
            k: v for k, v in expectations.items() if k.split(".", 1)[0] not in loaded_schemas
        }
        probes, _ = probe_mod.probe_estate(estate, expectations)
        for p in probes:
            for m in p.matches:
                matched += 1
                assert m.verdict == "OK", (
                    f"{m.key}: probed {m.observed}, estate declares {m.expected}"
                )
            assert not p.problems, f"{estate.pack}/{p.id}: {p.problems}"
    assert matched >= 10, "hardly any non-relational counts were checked against the estates"
