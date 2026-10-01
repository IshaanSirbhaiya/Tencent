"""AA3 — the adapter_agents side of the final mcp_ingest fixes (R-022 f2 CROSS-TRACK 1-3, INT4 #2).

1. `_evolve` carries the mapping entry's schema snapshot (`columns` / `fk_columns`) with its drifts.
2. Activation by an AUTHENTICATED principal seals an append-only, hash-chained delegation record;
   `auto_additive` is applied only from a sealed record that is still in force, and mcp_ingest's
   acceptance (`approve_delegated_additive` -> `delegation_of`) re-verifies it.
3. Approving an adaptation advances the baseline only over the drifts it carries.
4. A rejection is final; the drift comes back only as a NEW adaptation, after it is seen again.
Real stdio MCP server over SQLite, the real pipeline, the workspace pack; everything synthetic."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aa_drift_kit import OWNER, Principal, agent_spec, db_exec, drift_env, write_db

CONN = "crm"
ORGS = "public.organisations"
REGION = ("ALTER TABLE organisations ADD COLUMN region TEXT;"
          "UPDATE organisations SET region = 'north';")


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from drift_env(tmp_path, monkeypatch)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "crm.sqlite"
    write_db(path)
    return path


def _seals(env, agent_id):
    return env.manager.delegations.records(agent_id)


# === 1. the schema snapshot moves with the drifts ================================================
def _drift(**kw):
    from databridge_mcp_ingest.drift import Drift

    base = {"unit": f"table:{ORGS}", "table": ORGS, "detail": "test"}
    return Drift(**{**base, **kw})


def test_1_evolve_moves_the_schema_snapshot_with_each_drift(env):
    from databridge_adapter_agents.adapt import AdaptationEngine

    entry = {"node": {"kind": "organisation", "label": "{name}", "sources": ["id"],
                      "attrs": {"name": "name", "domain": "domain", "site": "site"}},
             "edges": [], "columns": ["id", "name", "domain", "site", "owner_id"],
             "fk_columns": ["owner_id"]}
    drifts = [_drift(kind="added_column", column="region", new={"type": "TEXT"}),
              _drift(kind="added_column", column="parent_id",
                     new={"type": "INTEGER", "foreign_key": "organisations.id"}),
              _drift(kind="renamed", column="web_domain", old="domain", new="web_domain"),
              _drift(kind="renamed", column="owner", old="owner_id", new="owner"),
              _drift(kind="removed", column="site", old={"type": "TEXT"})]
    eng = env.manager.adaptation
    assert isinstance(eng, AdaptationEngine)
    out, _ = eng._evolve(CONN, ORGS, entry, drifts, None, {"current": None})
    # derived from the drifts, not written down: start from the old snapshot and apply each one
    want = list(entry["columns"])
    want = [("web_domain" if c == "domain" else "owner" if c == "owner_id" else c)
            for c in want if c != "site"] + ["region", "parent_id"]
    assert sorted(out["columns"]) == sorted(want)
    assert out["fk_columns"] == sorted(["owner", "parent_id"])
    assert entry["columns"] == ["id", "name", "domain", "site", "owner_id"]     # not mutated
    # every column an attribute reads is in the snapshot (what mcp_ingest's #9 check demands)
    assert set(out["node"]["attrs"].values()) <= set(out["columns"])


def test_1_a_legacy_entry_without_a_snapshot_is_left_for_mcp_ingest_to_backfill(env):
    entry = {"node": {"kind": "organisation", "label": "{name}", "sources": ["id"],
                      "attrs": {"name": "name"}}, "edges": []}
    out, _ = env.manager.adaptation._evolve(
        CONN, ORGS, entry, [_drift(kind="added_column", column="region", new={"type": "TEXT"})],
        None, {"current": None})
    assert "columns" not in out and "fk_columns" not in out


# === 2. the sealed delegation ===================================================================
def test_2_activation_by_a_principal_seals_a_chained_record_and_the_approval_cites_it(env, db):
    from databridge_adapter_agents.adapt import Policy, policy_digest
    from databridge_mcp_ingest.mapping import approval_log_path

    spec = env.setup(CONN, db=db)
    [seal] = _seals(env, spec.id)
    assert seal["event"] == "delegation_sealed" and seal["principal_id"] == OWNER.principal_id
    assert seal["authenticated_by"] == OWNER.authenticated_by
    assert seal["policy_digest"] == policy_digest(Policy.of(spec))
    assert seal["scope_digest"] == env.manager.scope_digest(spec)
    assert seal["prev"] == "genesis" and seal["hash"] and seal["at"]
    assert env.manager.delegator(spec) == OWNER.principal_id
    assert env.sync(spec.id).status == "completed"
    db_exec(db, REGION)
    env.sync(spec.id)
    rec = env.manager.adaptations(spec.id)[-1]
    assert rec["status"] == "applied"
    assert rec["delegation"]["delegation_seal"]["hash"] == seal["hash"]
    logged = [json.loads(x) for x in approval_log_path(CONN).read_text("utf-8").splitlines()]
    assert logged[-1]["delegation_seal"]["hash"] == seal["hash"]


def test_2_a_typed_name_seals_nothing_and_the_additive_change_is_held(env, db):
    import databridge_mcp_ingest as mi
    from aa_drift_kit import connection

    mi.register_connection(connection(CONN, db=db))
    assert not mi.propose_plan(CONN).problems
    mi.approve_plan(CONN, principal=OWNER)
    env.approve_first_mapping(CONN)
    spec = env.manager.create(agent_spec(CONN), by=OWNER.principal_id)   # a name, not a principal
    assert _seals(env, spec.id) == [] and env.manager.delegator(spec) is None
    assert env.sync(spec.id).status == "completed"
    db_exec(db, REGION)
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    assert "no sealed delegation" in json.dumps(p["classification"])
    assert "region" not in (env.mapping(CONN)["tables"][ORGS]["node"]["attrs"] or {}).values()


def test_2_a_tampered_log_delegates_nothing(env, db):
    spec = env.setup(CONN, db=db)
    path = env.manager.delegations.path(spec.id)
    raw = path.read_text("utf-8")
    assert OWNER.principal_id in raw                                       # non-vacuous
    path.write_text(raw.replace(OWNER.principal_id, "mallory@larkspur.example"), "utf-8")
    seal, why = env.manager.delegation_status(spec)
    assert seal is None and "hash chain is broken" in why
    assert env.sync(spec.id).status == "completed"
    db_exec(db, REGION)
    env.sync(spec.id)
    assert [r["status"] for r in env.manager.adaptations(spec.id)] == ["pending"]


def test_2_a_policy_or_scope_change_invalidates_it_until_a_person_reactivates(env, db):
    import databridge_mcp_ingest as mi
    from aa_drift_kit import connection

    spec = env.setup(CONN, db=db)
    assert env.manager.delegator(spec) == OWNER.principal_id
    asks = {"policy": "auto_additive", "always_ask": ["renamed", "new_write_tool",
                                                      "subject_touching", "removed"]}
    original = spec.adapt.model_dump(mode="json")
    spec2 = env.manager.update(spec.id, {"adapt": asks}, by=OWNER)
    assert _seals(env, spec.id)[-1]["event"] == "delegation_revoked"
    assert env.manager.delegator(spec2) is None
    # changed back: still a change; only a person's re-activation seals again
    spec3 = env.manager.update(spec.id, {"adapt": original}, by=OWNER)
    assert env.manager.delegator(spec3) is None
    env.manager.pause(spec.id, by=OWNER)
    spec4 = env.manager.resume(spec.id, by=OWNER)
    assert env.manager.delegator(spec4) == OWNER.principal_id
    # the CONNECTION's declared scope widened after activation: no delegation
    mi.register_connection(connection(CONN, db=db, tables_include=["public.*"]))
    seal, why = env.manager.delegation_status(spec4)
    assert seal is None and "declared scope changed" in why
    # a spec whose policy differs from the sealed one (edited on disk, no `update`, so no
    # revocation line) delegates nothing either
    env.manager.pause(spec.id, by=OWNER)
    spec5 = env.manager.resume(spec.id, by=OWNER)
    assert env.manager.delegator(spec5) == OWNER.principal_id
    edited = SimpleNamespace(**{**{k: getattr(spec5, k) for k in ("id", "status",
                                                                   "connections")},
                                "adapt": {"policy": "auto_additive", "always_ask": []},
                                "model_dump": spec5.model_dump})
    assert env.manager.delegation_status(edited)[0] is None


def test_2_an_existing_active_agent_with_no_sealed_record_has_its_additive_changes_held(env, db):
    spec = env.setup(CONN, db=db)
    env.manager.delegations.path(spec.id).unlink()       # as every agent activated before AA3
    assert env.manager.delegator(spec) is None
    assert env.sync(spec.id).status == "completed"
    db_exec(db, REGION)
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    assert "not covered by the delegated approval" in json.dumps(p["classification"])
    # a person decides it
    env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    entry = env.mapping(CONN)["tables"][ORGS]
    assert entry["approval"]["principal_id"] == OWNER.principal_id
    assert "region" in entry["node"]["attrs"].values()


def test_2_mcp_ingest_acceptance_requires_the_sealed_record_in_force(env, db):
    from databridge_adapter_agents.adapt import _mint_delegation, delegation_of, AdaptationRefused
    from databridge_mcp_ingest.mapping import MappingApprovalRefused, approve_delegated_additive

    spec = env.setup(CONN, db=db)
    seal = env.manager.delegation_seal(spec)
    log = env.manager.delegations.path(spec.id)
    good = _mint_delegation(spec, seal=seal, seal_log=log, adaptation_id="ad_aa3")
    assert delegation_of(good, "t")["delegation_seal"]["hash"] == seal["hash"]   # non-vacuous
    unsealed = _mint_delegation(spec, delegated_by=OWNER.principal_id, adaptation_id="ad_aa3")
    with pytest.raises(AdaptationRefused, match="DELEGATION_NOT_SEALED"):
        delegation_of(unsealed, "t")
    entry = env.mapping(CONN)["tables"][ORGS]
    with pytest.raises(MappingApprovalRefused, match="DELEGATION_NOT_SEALED"):
        approve_delegated_additive(CONN, ORGS, entry, delegation=unsealed)
    # revoked AFTER the delegation was minted: mcp_ingest's acceptance re-reads the log and refuses
    env.manager.delegations.revoke(spec.id, by=OWNER.principal_id, reason="test")
    with pytest.raises(MappingApprovalRefused, match="no longer the one in force"):
        approve_delegated_additive(CONN, ORGS, entry, delegation=good)


def test_2_an_unregistered_principal_object_cannot_activate(env, db):
    from databridge_adapter_agents import RegistryError

    class Forged:
        principal_id = "owner@larkspur.example"
        authenticated_by = "test_boundary"

    with pytest.raises(RegistryError, match="not a principal"):
        env.manager.create(agent_spec(CONN), by=Forged())


# === 3. the baseline advances only over the drifts the adaptation carries =======================
def test_3_approval_refuses_to_absorb_a_drift_the_adaptation_does_not_carry(env, db):
    from databridge_adapter_agents import AdaptationRefused
    from databridge_mcp_ingest.drift import DriftAdvanceRefused

    spec = env.setup(CONN, db=db)
    assert env.sync(spec.id).status == "completed"
    db_exec(db, "ALTER TABLE organisations DROP COLUMN domain;" + REGION)
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    assert len(p["drift_ids"]) == 2                                       # non-vacuous
    eng = env.manager.adaptation
    partial = {**p, "drift_ids": p["drift_ids"][:1]}
    base_before = eng.store.baseline(spec.id, CONN)
    with pytest.raises(DriftAdvanceRefused):
        eng._advance_units(spec, CONN, partial, reason="test")
    # the approval path refuses BEFORE anything is applied or released
    real = eng.records
    eng.records = lambda agent: {**real(agent), p["adaptation_id"]: partial}
    versions = len(eng.store.versions(CONN, "mapping"))
    with pytest.raises(AdaptationRefused, match="absorb"):
        eng.approve(spec, p["adaptation_id"], by=OWNER)
    eng.records = real
    assert eng.store.baseline(spec.id, CONN) == base_before
    assert len(eng.store.versions(CONN, "mapping")) == versions
    assert env.is_held(CONN, ORGS)
    # the whole adaptation is approved normally
    env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    assert eng.store.baseline(spec.id, CONN) != base_before


def test_3_a_sync_never_advances_a_unit_over_a_drift_no_adaptation_covers(env, db):
    from databridge_adapter_agents.adapt import PreRun, Policy

    spec = env.setup(CONN, db=db)
    assert env.sync(spec.id).status == "completed"
    eng = env.manager.adaptation
    base = eng.store.baseline(spec.id, CONN)
    db_exec(db, REGION)
    pre = PreRun(connection=CONN, policy=Policy.of(spec))
    pre.current = env.service.probe_shape(CONN)            # observed, never handled
    eng._finish(spec, CONN, pre)
    assert eng.store.baseline(spec.id, CONN) == base


# === 4. a rejection is final =====================================================================
def test_4_approving_a_rejected_adaptation_is_refused_and_changes_nothing(env, db):
    from databridge_adapter_agents import AdaptationRefused

    spec = env.setup(CONN, db=db)
    assert env.sync(spec.id).status == "completed"
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;")
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    env.manager.reject_adaptation(spec.id, p["adaptation_id"], by=OWNER, reason="not wanted")
    before = env.mapping(CONN)
    with pytest.raises(AdaptationRefused, match="ADAPTATION_REJECTED_FINAL") as exc:
        env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    assert "only a pending" in str(exc.value)         # the gateway maps this to 409
    assert env.manager.adaptations(spec.id)[-1]["status"] == "rejected"
    assert env.mapping(CONN) == before and env.is_held(CONN, ORGS)
    # reverted, then changed the same way again: a NEW adaptation, the rejection stays final
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN web_domain TO domain;")
    env.sync(spec.id)
    db_exec(db, "ALTER TABLE organisations RENAME COLUMN domain TO web_domain;")
    env.sync(spec.id)
    [again] = env.manager.pending_adaptations(spec.id)
    assert again["adaptation_id"] != p["adaptation_id"]
    assert again["drift_ids"] == p["drift_ids"]
    old = {r["adaptation_id"]: r for r in env.manager.adaptations(spec.id)}[p["adaptation_id"]]
    assert [h["status"] for h in old["history"]] == ["pending", "rejected", "superseded"]
    env.manager.approve_adaptation(spec.id, again["adaptation_id"], by=OWNER)
    assert not env.is_held(CONN, ORGS)
