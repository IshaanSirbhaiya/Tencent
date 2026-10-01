"""AA — adapter agents adapt to TABULAR source changes at runtime.

A real stdio MCP server over a SQLite file (dressed as Postgres), the real ingest pipeline and the
workspace pack. Between syncs the database is altered — a column added, renamed, dropped, retyped;
a table added — and each test asserts what the agent did about it. Every expectation is derived
from the database file, the run's artefacts or the store, never typed in.
"""
from __future__ import annotations

import json

import pytest

from aa_drift_kit import OWNER, Principal, db_exec, db_rows, drift_env, write_db

CONN = "crm"
ORGS = "public.organisations"
PROJECTS = "public.projects"


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from drift_env(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "crm.sqlite"
    write_db(path)
    return path


def _setup(env, db, **kw):
    spec = env.setup(CONN, db=db, **kw)
    first = env.sync(spec.id)
    assert first.status == "completed", first.reason
    assert first.connections[0].adaptation["baseline_established"] is True
    return spec


def _adapt(summary) -> dict:
    return summary.connections[0].adaptation


def _versions(env, artifact):
    return env.manager.adaptation.store.versions(CONN, artifact)


def test_an_unchanged_source_does_not_drift(env, db):
    spec = _setup(env, db)
    again = env.sync(spec.id)
    a = _adapt(again)
    assert again.status == "completed" and a["checked"] and a["drifts"] == 0
    assert env.manager.adaptations(spec.id) == [] and _versions(env, "mapping") == []


def test_additive_column_is_applied_as_a_new_mapping_version_and_lands_in_the_graph(env, db):
    spec = _setup(env, db)
    before = env.mapping(CONN)["tables"][ORGS]
    db_exec(db, "ALTER TABLE organisations ADD COLUMN region TEXT;"
                "UPDATE organisations SET region = 'north' WHERE id % 2 = 1;"
                "UPDATE organisations SET region = 'south' WHERE id % 2 = 0;")
    s = env.sync(spec.id)
    a = _adapt(s)
    assert s.status == "completed" and a["drift_kinds"] == ["added_column"]
    assert a.get("applied") and not a.get("pending")
    rec = env.manager.adaptations(spec.id)[-1]
    assert rec["status"] == "applied" and rec["produced_by"]["agent"] == "rules"
    assert rec["produced_by"]["model"] is None
    # a NEW version: the one in force before is kept, the new one follows it
    vs = _versions(env, "mapping")
    assert [v["version"] for v in vs] == list(range(1, len(vs) + 1)) and len(vs) >= 2
    assert vs[-1]["supersedes"] == vs[-2]["version"] and vs[-1]["adaptation_id"]
    assert "region" not in (before["node"]["attrs"] or {}).values()
    after = env.mapping(CONN)["tables"][ORGS]
    assert after["status"] == "approved" and after["node"]["attrs"].get("region") == "region"
    # the values are in the graph, each with a table_cell pointer that resolves (P4, P8)
    from databridge_mcp_ingest import capture

    want = {r["name"]: r["region"] for r in db_rows(db, "organisations")}
    nodes = env.nodes("organisation")
    assert len(nodes) == len(want)
    for n in nodes:
        attrs = env.attrs(n)
        assert attrs["region"] == want[n["label"]]
        ptr = env.store.attr_sources_for(n["id"])["region"]
        assert ptr.kind == "table_cell" and ptr.column == "region" and ptr.table == ORGS
        snap = json.loads(capture.get_bytes(ptr.capture_digest))
        row = next(r for r in snap["rows"] if str(r["id"]) == ptr.row_id)
        assert row["region"] == attrs["region"]
    # the adaptation is recorded in the run directory (no new §5.3 event type)
    run_rec = json.loads((env.run_dir(s, CONN) / "adaptations.json").read_text(encoding="utf-8"))
    assert run_rec["record_type"] == "adaptation_applied"
    assert [r["status"] for r in run_rec["records"]] == ["applied"]
    # the baseline moved: the next sync is clean
    assert _adapt(env.sync(spec.id))["drifts"] == 0


def test_renamed_column_is_a_candidate_held_and_asked_never_applied(env, db):
    spec = _setup(env, db)
    approved = env.mapping(CONN)["tables"][ORGS]
    budgets = {r["name"]: r["budget"] * 2 for r in db_rows(db, "projects")}
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;"
                "UPDATE projects SET budget = budget * 2;")
    s = env.sync(spec.id)
    a = _adapt(s)
    assert a["drift_kinds"] == ["renamed"] and a.get("pending") and not a.get("applied")
    [p] = env.manager.pending_adaptations(spec.id)
    [d] = p["drifts"]
    assert (d["old"], d["new"], d["candidate"]) == ("domain", "web_domain", True)
    assert 0 < d["confidence"] <= 1
    # the proposed version is filed with its evidence and the diff — not applied
    entry = p["proposal"]["content"]["entry"]
    assert "web_domain" in entry["node"]["attrs"].values()
    assert p["proposal"]["previous"]["node"] == approved["node"]
    assert p["evidence"]["baseline"] and p["evidence"]["observed"] and p["evidence"]["drifts"]
    # ONLY the affected table is held (holds file, or its mapping back to `proposed`), nothing
    # rebuilt it
    assert env.is_held(CONN, ORGS) and not env.is_held(CONN, PROJECTS)
    for n in env.nodes("organisation"):
        assert env.store.attr_sources_for(n["id"])["domain"].column == "domain"
    # ... and the rest kept syncing: the projects table was rebuilt with the new budgets
    assert s.connections[0].status == "completed"
    assert {n["label"]: env.attrs(n)["budget"] for n in env.nodes("project")} == budgets
    # never silently dropped: recorded as excluded, with the rule and the reversal (P3)
    ledger = env.jsonl(env.run_dir(s, CONN) / "excluded.drift.jsonl")
    assert [x["uri"] for x in ledger] == [ORGS]
    assert ledger[0]["rule"] == "DRIFT_PENDING_APPROVAL" and "approve" in ledger[0]["reverse"]
    assert s.connections[0].exclusions_by_rule["excluded"]["DRIFT_PENDING_APPROVAL"] == 1
    # and it shows in health
    h = env.manager.health(spec.id)
    assert h["overall"] == "awaiting_approval"
    assert [x["adaptation_id"] for x in h["adaptation"]["pending"]] == [p["adaptation_id"]]
    assert any(p["adaptation_id"] in i for i in h["issues"])


def test_removed_column_is_held_and_asked(env, db):
    spec = _setup(env, db)
    db_exec(db, "ALTER TABLE projects DROP COLUMN budget;")
    a = _adapt(env.sync(spec.id))
    assert a["drift_kinds"] == ["removed"] and a.get("pending")
    [p] = env.manager.pending_adaptations(spec.id)
    want_via = "holds_file" if env.holds_file() else "mapping_status"
    assert p["drifts"][0]["column"] == "budget" and p["hold"]["via"] == want_via
    assert "budget" not in p["proposal"]["content"]["entry"]["node"]["attrs"].values()
    assert env.is_held(CONN, PROJECTS) and not env.is_held(CONN, ORGS)
    assert env.mapping(CONN)["tables"][ORGS]["status"] == "approved"


def test_type_change_is_asked(env, db):
    spec = _setup(env, db)
    db_exec(db, """
        ALTER TABLE projects RENAME TO projects_old;
        CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, budget REAL,
                               organisation_id INTEGER REFERENCES organisations(id));
        INSERT INTO projects SELECT id, name, budget, organisation_id FROM projects_old;
        DROP TABLE projects_old;""")
    a = _adapt(env.sync(spec.id))
    assert a["drift_kinds"] == ["type_changed"] and a.get("pending")
    [p] = env.manager.pending_adaptations(spec.id)
    assert (p["drifts"][0]["old"], p["drifts"][0]["new"]) == ("integer", "real")


def test_subject_touching_column_is_always_asked_even_under_auto_additive(env, db):
    from databridge_mcp_ingest.drift import subject_identifiers

    ident = sorted(subject_identifiers(env.vocab))[0]
    spec = _setup(env, db, adapt={"policy": "auto_additive"})
    db_exec(db, f"ALTER TABLE projects ADD COLUMN {ident} TEXT;")
    a = _adapt(env.sync(spec.id))
    assert "added_column" in a["drift_kinds"] and "subject_touching" in a["drift_kinds"]
    assert a.get("pending") and not a.get("applied")
    [p] = env.manager.pending_adaptations(spec.id)
    assert p["drifts"][0]["subject_touching"] and ident in p["drifts"][0]["subject_reason"]
    assert all(v["asked"] for v in p["classification"].values())
    # not applied: the table is held, and the version in force never read it
    assert env.is_held(CONN, PROJECTS)
    assert ident not in p["proposal"]["previous"]["node"]["attrs"].values()
    assert all(ident not in env.attrs(n) for n in env.nodes("project"))


def test_new_table_is_mapped_as_a_new_version_and_built(env, db):
    spec = _setup(env, db)
    db_exec(db, """
        CREATE TABLE contracts (id INTEGER PRIMARY KEY, title TEXT,
                                organisation_id INTEGER REFERENCES organisations(id));
        INSERT INTO contracts VALUES (100, 'Freight MSA', 1), (101, 'Mill lease', 3);""")
    s = env.sync(spec.id)
    a = _adapt(s)
    assert a["drift_kinds"] == ["new_table"] and a.get("applied")
    entry = env.mapping(CONN)["tables"]["public.contracts"]
    assert entry["status"] == "approved" and entry["node"]["kind"] == "contract"
    assert len(env.nodes("contract")) == len(db_rows(db, "contracts"))


def test_new_subject_table_is_asked_and_never_built(env, db):
    from databridge_mcp_ingest.drift import subject_identifiers

    ident = sorted(subject_identifiers(env.vocab))[0]
    spec = _setup(env, db)
    subjects_before = [r["id"] for r in env.store.open_nodes()]
    db_exec(db, f"""
        CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, {ident} TEXT);
        INSERT INTO people VALUES (1, 'Priya Venkataraman', 'p1@aldermoor.example'),
                                  (2, 'Tomasz Brennick', 't2@quillon.example');""")
    a = _adapt(env.sync(spec.id))
    assert "subject_touching" in a["drift_kinds"] and a.get("pending") and not a.get("applied")
    assert env.is_held(CONN, "public.people")          # held until a person decides
    new_ids = {r["id"] for r in env.store.open_nodes()} - set(subjects_before)
    assert not [i for i in new_ids if i.startswith("person:")]


def test_policy_off_detects_and_reports_only(env, db):
    spec = _setup(env, db, adapt={"policy": "off"})
    mapping_before = env.mapping(CONN)["tables"]
    db_exec(db, "ALTER TABLE organisations ADD COLUMN region TEXT;"
                "ALTER TABLE projects RENAME COLUMN budget TO amount;")
    s = env.sync(spec.id)
    a = _adapt(s)
    assert sorted(a["drift_kinds"]) == ["added_column", "renamed"]
    assert a.get("reported") and not a.get("applied") and not a.get("pending")
    assert _versions(env, "mapping") == []
    after = env.mapping(CONN)["tables"]
    assert {t: e["status"] for t, e in after.items()} == \
        {t: e["status"] for t, e in mapping_before.items()}
    assert env.manager.pending_adaptations(spec.id) == []
    n = len(env.manager.adaptations(spec.id))
    env.sync(spec.id)
    assert len(env.manager.adaptations(spec.id)) == n, "a report is not re-filed every sync"


def test_approve_advances_the_baseline_and_the_next_sync_is_clean(env, db):
    from databridge_adapter_agents import AdaptationRefused

    spec = _setup(env, db)
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;")
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    for bad in ("owner@larkspur.example", None, Principal("  "), Principal("agent:bot")):
        with pytest.raises(AdaptationRefused):
            env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=bad)
    history_before = len(env.manager.adaptation.store.baseline_history(spec.id, CONN))
    done = env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    assert done["status"] == "approved"
    assert done["decided_by"]["principal_id"] == OWNER.principal_id
    assert env.mapping(CONN)["tables"][ORGS]["status"] == "approved"
    assert len(env.manager.adaptation.store.baseline_history(spec.id, CONN)) > history_before
    s = env.sync(spec.id)
    assert _adapt(s)["drifts"] == 0 and env.manager.pending_adaptations(spec.id) == []
    assert not (env.run_dir(s, CONN) / "excluded.drift.jsonl").exists()
    # the held table was built again under the approved version ...
    built = {r["uri"] for r in env.jsonl(env.run_dir(s, CONN) / "build.jsonl")}
    assert ORGS in built
    assert env.mapping(CONN)["tables"][ORGS]["node"]["attrs"]["domain"] == "web_domain"
    # ... and every value still resolves against the capture its pointer names. (A value that
    # did not change keeps the pointer it was first grounded with — the graph's merge rule.)
    from databridge_mcp_ingest import capture

    want = {r["name"]: r["web_domain"] for r in db_rows(db, "organisations")}
    for n in env.nodes("organisation"):
        ptr = env.store.attr_sources_for(n["id"])["domain"]
        assert env.attrs(n)["domain"] == want[n["label"]]
        snap = json.loads(capture.get_bytes(ptr.capture_digest))
        row = next(r for r in snap["rows"] if str(r["id"]) == ptr.row_id)
        assert row[ptr.column] == env.attrs(n)["domain"]
    assert env.manager.health(spec.id)["adaptation"]["pending"] == []


def test_reject_stays_held_and_is_recorded(env, db):
    spec = _setup(env, db)
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;")
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    out = env.manager.reject_adaptation(spec.id, p["adaptation_id"], by=OWNER,
                                        reason="web_domain is a different field")
    assert out["status"] == "rejected" and out["decided_by"]["principal_id"] == OWNER.principal_id
    assert env.is_held(CONN, ORGS)
    held = env.mapping(CONN)["tables"][ORGS]
    assert "web_domain" not in held["node"]["attrs"].values()   # not the rejected proposal
    s = env.sync(spec.id)
    a = _adapt(s)
    assert a.get("rejected") == [p["adaptation_id"]] and not a.get("pending")
    assert [x["uri"] for x in env.jsonl(env.run_dir(s, CONN) / "excluded.drift.jsonl")] == [ORGS]
    assert len(env.manager.adaptations(spec.id)) == 1, "a rejection is not re-asked every sync"
    assert [r["adaptation_id"] for r in env.manager.health(spec.id)["adaptation"]["rejected"]] \
        == [p["adaptation_id"]]


def test_reverting_the_source_releases_the_hold(env, db):
    spec = _setup(env, db)
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;")
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN web_domain TO domain;")
    a = _adapt(env.sync(spec.id))
    assert a.get("superseded") == [p["adaptation_id"]] and a["drifts"] == 0
    assert env.mapping(CONN)["tables"][ORGS]["status"] == "approved"
    assert env.manager.pending_adaptations(spec.id) == []
