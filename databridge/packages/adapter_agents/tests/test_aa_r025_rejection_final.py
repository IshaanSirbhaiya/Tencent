"""R-025 / E2E run 4 Q-09 — a rejected drift never comes back bundled with a new one.

Live failure (var/e2e-20261001T1255, `adaptations.jsonl`): a rename + a dropped column on one table
were REJECTED (`ad_d4857be3b6e09ded`); the next sync saw a type change on the same table, superseded
the rejected record and filed `ad_ebcd92602fcef090` = [removed, renamed, type_changed]. Approving the
type change approved the rejected rename and drop.

Guarantees asserted here, on the real stdio MCP server over SQLite and the real pipeline:
  * a rejection is final for that exact change: the rejected drifts stay excluded from later
    proposals; only when the source changes it AGAIN is it re-asked, on its own;
  * every held adaptation carries a digest of exactly the drifts it contains;
  * approving one adaptation never applies (nor absorbs into the baseline) another's change.
Every expectation is derived from the store, the database or the records, never typed in.
"""
from __future__ import annotations

import pytest

from aa_drift_kit import OWNER, db_exec, drift_env, write_db

CONN = "crm"
PROJECTS = "public.projects"
UNIT = f"table:{PROJECTS}"

#: the evidence's shape: a rename (and later a retype) on ONE table
RENAME = "ALTER TABLE projects RENAME COLUMN name TO title;"
RETYPE = """
    ALTER TABLE projects RENAME TO projects_old;
    CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT, budget REAL,
                           organisation_id INTEGER REFERENCES organisations(id));
    INSERT INTO projects SELECT id, title, budget, organisation_id FROM projects_old;
    DROP TABLE projects_old;"""
RENAME_AGAIN = "ALTER TABLE projects RENAME COLUMN title TO heading;"


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from drift_env(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "crm.sqlite"
    write_db(path)
    return path


def _pending_on(env, spec):
    return [p for p in env.manager.pending_adaptations(spec.id) if p.get("unit") == UNIT]


def _rec(env, spec, aid):
    return {r["adaptation_id"]: r for r in env.manager.adaptations(spec.id)}[aid]


def _base_table(env, spec):
    return env.manager.adaptation.store.baseline(spec.id, CONN)["tables"][PROJECTS]


def _reject_a_rename(env, db):
    spec = env.setup(CONN, db=db)
    assert env.sync(spec.id).status == "completed"
    db_exec(db, RENAME)
    env.sync(spec.id)
    [r] = _pending_on(env, spec)
    assert {d["kind"] for d in r["drifts"]} == {"renamed"}          # non-vacuous
    env.manager.reject_adaptation(spec.id, r["adaptation_id"], by=OWNER, reason="not wanted")
    return spec, r


def test_a_rejected_drift_is_not_rebundled_with_a_new_one(env, db):
    from databridge_adapter_agents.adapt import changes_digest_of

    spec, rejected = _reject_a_rename(env, db)
    entry_before = env.mapping(CONN)["tables"][PROJECTS]
    base_before = _base_table(env, spec)
    db_exec(db, RETYPE)
    env.sync(spec.id)

    # the rejection is final: the rejected record is still rejected, never superseded
    assert _rec(env, spec, rejected["adaptation_id"])["status"] == "rejected"
    [new] = _pending_on(env, spec)
    assert new["adaptation_id"] != rejected["adaptation_id"]
    # one decision covers exactly the new change: none of the rejected drifts are in it
    assert not set(new["drift_ids"]) & set(rejected["drift_ids"])
    assert new["drift_ids"] and {d["kind"] for d in new["drifts"]} == {"type_changed"}
    # and it carries a digest of exactly the drifts it contains
    assert new["changes_digest"] == changes_digest_of(new["drift_ids"])
    assert new["changes_digest"] != changes_digest_of(new["drift_ids"] + rejected["drift_ids"])

    env.manager.approve_adaptation(spec.id, new["adaptation_id"], by=OWNER)

    # approving the type change did NOT apply the rejected rename to the mapping ...
    [ren] = rejected["drifts"]
    entry = env.mapping(CONN)["tables"][PROJECTS]
    reads = set((entry.get("node") or {}).get("attrs", {}).values())
    reads_before = set((entry_before.get("node") or {}).get("attrs", {}).values())
    assert ren["old"] in reads_before                                  # non-vacuous
    assert ren["new"] not in reads and ren["old"] in reads
    # ... nor absorb it into the baseline: the retype moved, the rename did not
    base = _base_table(env, spec)
    [td] = new["drifts"]
    assert base["columns"][td["column"]]["type"] == td["new"] != \
        base_before["columns"][td["column"]]["type"]
    assert ren["old"] in base["columns"] and ren["new"] not in base["columns"]
    # the rejected part stays held, and stays rejected
    assert env.is_held(CONN, PROJECTS)
    assert _rec(env, spec, rejected["adaptation_id"])["status"] == "rejected"

    # the next sync does not ask the rejected change again
    env.sync(spec.id)
    assert _pending_on(env, spec) == []
    assert _rec(env, spec, rejected["adaptation_id"])["status"] == "rejected"

    # the source changes the rejected column AGAIN: re-asked, on its own
    db_exec(db, RENAME_AGAIN)
    env.sync(spec.id)
    [again] = _pending_on(env, spec)
    assert not set(again["drift_ids"]) & set(rejected["drift_ids"])
    assert not set(again["drift_ids"]) & set(new["drift_ids"])
    assert [d["kind"] for d in again["drifts"]] == ["renamed"]
    assert again["drifts"][0]["old"] == ren["old"] and again["drifts"][0]["new"] != ren["new"]
    old = _rec(env, spec, rejected["adaptation_id"])
    assert old["status"] == "superseded" and old.get("rejection_lifted") is True


def test_a_bundle_that_carries_a_rejected_drift_cannot_be_approved(env, db):
    """A record filed before the fix (the live `ad_ebcd...`) bundles a rejected drift: approving
    it is refused and changes nothing; the next sync re-proposes its other change on its own."""
    from databridge_adapter_agents import AdaptationRefused
    from databridge_adapter_agents.adapt import changes_digest_of

    spec, rejected = _reject_a_rename(env, db)
    db_exec(db, RETYPE)
    env.sync(spec.id)
    [new] = _pending_on(env, spec)
    eng = env.manager.adaptation
    ids = sorted(new["drift_ids"] + rejected["drift_ids"])
    # recorded as the old code did: no `changes_digest`
    legacy = {k: v for k, v in new.items() if k not in ("history", "changes_digest")}
    legacy.update(adaptation_id="ad_legacy_bundle", drift_ids=ids,
                  drifts=rejected["drifts"] + new["drifts"], status="pending")
    eng.store.append(spec.id, legacy)
    mapping, base = env.mapping(CONN), eng.store.baseline(spec.id, CONN)
    with pytest.raises(AdaptationRefused, match="REJECTED_CHANGE_BUNDLED"):
        env.manager.approve_adaptation(spec.id, "ad_legacy_bundle", by=OWNER)
    assert env.mapping(CONN) == mapping and eng.store.baseline(spec.id, CONN) == base
    assert _rec(env, spec, "ad_legacy_bundle")["status"] == "pending"
    # a digest that does not name exactly the record's drifts is refused too
    with pytest.raises(AdaptationRefused, match="CHANGES_DIGEST_MISMATCH"):
        env.manager.approve_adaptation(spec.id, new["adaptation_id"], by=OWNER,
                                       changes_digest=changes_digest_of(ids))
    assert _rec(env, spec, new["adaptation_id"])["status"] == "pending"
    env.manager.approve_adaptation(spec.id, new["adaptation_id"], by=OWNER,
                                   changes_digest=new["changes_digest"])
    assert _rec(env, spec, new["adaptation_id"])["status"] == "approved"


def test_an_added_column_still_auto_applies_beside_a_standing_rejection(env, db):
    """The delegated additive path is unchanged on a table that holds a rejected change, and the
    sync's baseline moves over the applied column only, stepping around the rejected rename."""
    spec, rejected = _reject_a_rename(env, db)
    db_exec(db, "ALTER TABLE projects ADD COLUMN region TEXT; UPDATE projects SET region = 'n';")
    env.sync(spec.id)
    recs = [r for r in env.manager.adaptations(spec.id)
            if r.get("unit") == UNIT and r["adaptation_id"] != rejected["adaptation_id"]]
    [added] = recs
    assert {d["kind"] for d in added["drifts"]} == {"added_column"}
    assert added["status"] == "applied" and added["delegation"]["delegation_seal"]["hash"]
    assert not set(added["drift_ids"]) & set(rejected["drift_ids"])
    assert _rec(env, spec, rejected["adaptation_id"])["status"] == "rejected"
    base = _base_table(env, spec)
    [col] = added["drifts"]
    [ren] = rejected["drifts"]
    assert col["column"] in base["columns"]
    assert ren["old"] in base["columns"] and ren["new"] not in base["columns"]
    assert _pending_on(env, spec) == [] and env.is_held(CONN, PROJECTS)
