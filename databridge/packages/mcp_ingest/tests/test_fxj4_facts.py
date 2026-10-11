"""FXJ4 (R-022 f2, final): facts / count / writer fixes — #5, #6, #8, #10, #15."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import test_principal


@pytest.fixture
def facts_env(walnut_env, tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    return walnut_env


# === #5 a forced publication needs an authenticated person, and is audited ======================
def test_5_force_needs_an_authenticated_principal_and_is_logged(facts_env, tmp_path):
    from test_s2_units import _big_table, _stage

    from databridge_mcp_ingest.tabular.facts import (FactsOverrideRefused, build_facts,
                                                     logged_overrides, overrides_log_path)

    src = tmp_path / "src.sqlite"
    _big_table(src, 40)
    runner, policy, profiles = _stage(src, {})
    profiles[0]["row_count"] = 41                                       # INCONSISTENT
    refused = build_facts("f5", runner, profiles, policy)
    assert refused["status"] == "refused" and "INCONSISTENT" in refused["refused"]
    for bad in (True, "alice", {"principal_id": "alice"}):
        with pytest.raises(FactsOverrideRefused):
            build_facts("f5", runner, profiles, policy, force=bad)
    assert not overrides_log_path("f5").exists()
    # an override bound to ANOTHER candidate does not publish this one
    other = build_facts("f5", runner, profiles, policy, force=test_principal("alice"),
                        force_digest="0" * 64)
    assert other["status"] == "refused" and "OVERRIDE MISMATCH" in other["refused"]
    ok = build_facts("f5", runner, profiles, policy, force=test_principal("alice"),
                     force_digest=refused["content_sha256"])
    assert ok["status"] == "ok" and ok["override"]["principal_id"] == "alice"
    recs = logged_overrides("f5")
    assert len(recs) == 1 and recs[0]["content_sha256"] == refused["content_sha256"]
    assert recs[0]["authenticated_by"] and "INCONSISTENT" in recs[0]["overrides"][0]


# === #6 the writer password never reaches the executor's SQL text ===============================
def test_6_the_writer_password_is_not_in_the_sql_and_errors_do_not_echo_it(monkeypatch):
    from databridge_mcp_ingest.tabular.writer import (WriterSpecError, apply_writer_password,
                                                      load_writer_spec, scram_sha256_verifier)

    spec = load_writer_spec()
    secret = "Sup3r-s3cret-pw"
    monkeypatch.setenv(spec["password_env"], secret)
    sent: list[str] = []
    apply_writer_password(sent.append, spec)
    assert len(sent) == 1 and secret not in sent[0]
    assert sent[0].startswith(f"ALTER ROLE {spec['role']} PASSWORD 'SCRAM-SHA-256$4096:")

    def echo(sql):
        raise RuntimeError(sql)

    with pytest.raises(WriterSpecError) as ei:
        apply_writer_password(echo, spec)
    text = str(ei.value) + repr(ei.value.__cause__) + repr(ei.value.__context__)
    assert secret not in text and "SCRAM" not in text
    # RFC 7677 test vector shape: deterministic for a fixed salt
    v = scram_sha256_verifier("pencil", salt=b"0123456789abcdef")
    assert v == scram_sha256_verifier("pencil", salt=b"0123456789abcdef") and "pencil" not in v


# === #8 parameterised / unproven-order pointers resolve =========================================
def _store(source_id: str, d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(d / f"{source_id}_facts.sqlite")
    db.executescript("CREATE TABLE t (id INTEGER, v TEXT); "
                     "INSERT INTO t VALUES (1, 'a'), (1, 'b'), (2, 'c'); "
                     "CREATE TABLE k (code TEXT PRIMARY KEY, v TEXT); "
                     "INSERT INTO k VALUES ('x', 'p'), ('y', 'q');")
    db.commit()
    db.close()


def test_8_parameterised_and_unproven_order_pointers_resolve(facts_env, tmp_path):
    import hashlib

    from databridge_mcp_ingest.tabular.count import (FactsQueryError, count_query,
                                                     resolve_query_cell)

    d = tmp_path / "facts"
    _store("q8", d)
    # R-029 Codex f1 #1: the read writes nothing; persisting the artefact is the explicit write
    a = count_query("q8", "SELECT v FROM t WHERE id = ? ORDER BY v", params=(1,),
                    persist_query=True)
    assert a.query_ref and (d / a.query_ref).is_file()
    assert resolve_query_cell(a.pointers[1]) == a.rows[1][0]
    # ORDER BY a non-unique column (ties on id) is NOT proof: the result digest is in the identity
    tie = count_query("q8", "SELECT id, v FROM t ORDER BY id", persist_query=True)
    assert not tie.ordered and tie.query_ref
    assert tie.pointers[0].query_hash != hashlib.sha256(tie.sql.encode()).hexdigest()
    assert resolve_query_cell(tie.pointers[3]) == tie.rows[1][1]
    # a declared key, or rowid, IS proof: statement-only identity, resolved by the statement
    key = count_query("q8", "SELECT code, v FROM k ORDER BY code")
    assert key.ordered and not key.query_ref
    assert resolve_query_cell(key.pointers[0], sql=key.sql) == "x"
    assert count_query("q8", "SELECT id FROM t ORDER BY rowid").ordered
    # a pointer to a changed facts file never resolves
    db = sqlite3.connect(d / "q8_facts.sqlite")
    db.execute("INSERT INTO t VALUES (1, 'z')")
    db.commit()
    db.close()
    with pytest.raises(FactsQueryError):
        resolve_query_cell(a.pointers[0])


# === #10 the incremental key covers the whole logical publication ===============================
def test_10_a_renamed_column_with_identical_values_is_republished(facts_env, tmp_path):
    from test_s2_units import _big_table, _stage

    from databridge_mcp_ingest.tabular.facts import build_facts

    src = tmp_path / "src.sqlite"
    _big_table(src, 30)
    runner, policy, profiles = _stage(src, {})
    first = build_facts("f10", runner, profiles, policy)
    again = build_facts("f10", runner, profiles, policy)
    assert first["status"] == "ok" and again.get("unchanged")
    db = sqlite3.connect(src)
    db.execute("ALTER TABLE orders RENAME COLUMN city TO town")
    db.commit()
    db.close()
    runner, policy, profiles = _stage(src, {})
    renamed = build_facts("f10", runner, profiles, policy)
    assert renamed["status"] == "ok" and not renamed.get("unchanged")
    assert renamed["content_sha256"] != first["content_sha256"]
    with sqlite3.connect(renamed["facts_file"]) as fdb:
        cols = [r[1] for r in fdb.execute("PRAGMA table_info(orders)")]
    assert "town" in cols and "city" not in cols


# === #15 facts table names never collide ========================================================
def test_15_facts_table_names_are_collision_checked():
    from databridge_mcp_ingest.tabular.facts import facts_table_names

    profiles = [{"ref": "s.foo", "schema": "s", "table": "foo"},
                {"ref": "x.foo", "schema": "x", "table": "foo"},
                {"ref": "s.s__foo", "schema": "s", "table": "s__foo"},
                {"ref": "s._facts_tables", "schema": "s", "table": "_facts_tables"},
                {"ref": "s.Bar", "schema": "s", "table": "Bar"},
                {"ref": "y.bar", "schema": "y", "table": "bar"}]
    names = facts_table_names(profiles)
    assert len(names) == len(profiles)
    lowered = [n.lower() for n in names.values()]
    assert len(set(lowered)) == len(lowered), names
    assert not names["s._facts_tables"].lower() == "_facts_tables"
    assert names == facts_table_names(list(reversed(profiles)))        # order-independent
