"""INT3 — `auto_additive` is a DELEGATED approval by the person who activated the agent.

It covers additive changes inside the declared scope only, it goes through mcp_ingest's own
approval API (never a `status: approved` typed into YAML), and it is recorded distinctly. Breaking
changes are held for a person even when the spec takes them out of `always_ask`. An agent nobody
activated delegates nothing. D2: a spec's `screen.pack` must be the deployment's resolved screen.
Real stdio MCP server over SQLite, the real pipeline, the workspace pack; everything synthetic."""
from __future__ import annotations

import json

import pytest

from aa_drift_kit import OWNER, agent_spec, db_exec, drift_env, write_db

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


def _log(conn):
    from databridge_mcp_ingest.mapping import approval_log_path

    return [json.loads(x) for x in approval_log_path(conn).read_text("utf-8").splitlines()]


def test_an_additive_change_is_approved_by_delegation_and_recorded_as_such(env, db):
    from databridge_adapter_agents.adapt import Policy, policy_digest

    spec = env.setup(CONN, db=db)
    assert env.manager.delegator(spec) == OWNER.principal_id
    assert env.sync(spec.id).status == "completed"
    db_exec(db, REGION)
    s = env.sync(spec.id)
    assert s.status == "completed"
    rec = env.manager.adaptations(spec.id)[-1]
    assert rec["status"] == "applied"
    want = f"delegated:{policy_digest(Policy.of(spec))}"
    assert rec["delegation"]["authenticated_by"] == want
    assert rec["delegation"]["delegated_by"] == OWNER.principal_id
    entry = env.mapping(CONN)["tables"][ORGS]
    assert entry["status"] == "approved" and entry["node"]["attrs"].get("region") == "region"
    assert entry["approval"]["authenticated_by"] == want
    # the person's own approval and the delegated one are told apart in the append-only log
    kinds = [ln["authenticated_by"].startswith("delegated:") for ln in _log(CONN)
             if ln["table"] == ORGS]
    assert kinds[0] is False and kinds[-1] is True


def test_an_agent_nobody_activated_delegates_nothing(env, db):
    import databridge_mcp_ingest as mi

    mi.register_connection(__import__("aa_drift_kit").connection(CONN, db=db))
    assert not mi.propose_plan(CONN).problems
    mi.approve_plan(CONN, principal=OWNER)
    env.approve_first_mapping(CONN)
    spec = env.manager.create(agent_spec(CONN))                  # no `by`: nobody activated it
    assert env.manager.delegator(spec) is None
    assert env.sync(spec.id).status == "completed"
    db_exec(db, REGION)
    env.sync(spec.id)
    [p] = env.manager.pending_adaptations(spec.id)
    assert "not covered by the delegated approval" in json.dumps(p["classification"])
    assert "region" not in (env.mapping(CONN)["tables"][ORGS]["node"]["attrs"] or {})
    # a person decides it, through the same approval API
    env.manager.approve_adaptation(spec.id, p["adaptation_id"], by=OWNER)
    entry = env.mapping(CONN)["tables"][ORGS]
    assert entry["status"] == "approved" and entry["approval"]["principal_id"] == \
        OWNER.principal_id
    assert not entry["approval"]["authenticated_by"].startswith("delegated:")


def test_a_breaking_change_taken_out_of_always_ask_is_still_held_for_a_person(env, db):
    spec = env.setup(CONN, db=db, adapt={"policy": "auto_additive",
                                           "always_ask": ["new_write_tool", "subject_touching"]})
    assert env.sync(spec.id).status == "completed"
    before = env.mapping(CONN)["tables"][ORGS]
    assert "domain" in (before["node"]["attrs"] or {}).values()           # non-vacuous
    db_exec(db, "ALTER TABLE organisations DROP COLUMN domain;")
    env.sync(spec.id)
    pend = env.manager.pending_adaptations(spec.id)
    assert pend and "a person must approve" in json.dumps(pend[-1]["classification"])
    assert all(r["status"] != "applied" for r in env.manager.adaptations(spec.id)
               if "removed" in json.dumps(r.get("drifts")))


def test_a_policy_changed_after_activation_is_delegated_by_nobody(env, db):
    spec = env.setup(CONN, db=db)
    assert env.manager.delegator(spec) == OWNER.principal_id
    spec2 = env.manager.update(spec.id, {"adapt": {"policy": "auto_additive",
                                                   "always_ask": ["renamed", "new_write_tool",
                                                                  "subject_touching"]}},
                               by="someone-else")
    assert env.manager.delegator(spec2) is None


# --- D2 -------------------------------------------------------------------------------------------
def test_a_spec_screen_pack_must_be_the_deployments_resolved_screen(env, db, monkeypatch):
    from databridge_mcp_ingest import screen

    spec = env.manager.create({**agent_spec(CONN), "screen": {"pack": "workspace"}},
                              by=OWNER.principal_id)
    resolved = screen.screen_pack_of(screen.load_screen())
    if resolved == "workspace":
        assert env.manager.screen_pack_refusal(spec) is None
    defaults = screen.load_screen(path=screen.paths.default_screen_path())
    assert screen.screen_pack_of(defaults) is None
    monkeypatch.setattr(screen, "load_screen", lambda *a, **k: defaults)
    why = env.manager.screen_pack_refusal(spec)
    assert why and "bundled defaults" in why
    assert why in env.manager.agent_gate(spec)                 # activate, resume and sync refuse
    plain = env.manager.create(agent_spec(CONN, agent_id="no_screen_pack"), by=OWNER.principal_id)
    assert env.manager.screen_pack_refusal(plain) is None       # no pack named: unchanged
