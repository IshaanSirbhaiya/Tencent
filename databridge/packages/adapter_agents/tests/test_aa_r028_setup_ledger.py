"""R-028 LEDGER (Codex consistency review #2, #7, #11) for the Set-up decisions.

The documents decision and the hand-off authorisation are records in the common decision ledger
(sealed, chained, anchored, verified), carry the actor object, and refuse a machine actor with the
standard code. A log that does not verify is never read as a decision. Expectations are derived
from what the writers wrote.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from databridge_core import decision_ledger as dl

SID = "crm_src"


def _who(pid="alice", **kw):
    return {"principal_id": pid, "authenticated_by": "bearer:test", "principal_type": "Principal",
            "dev_only": False, **kw}


def _lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


DISC = {"run_id": "disc-1", "documents": {"items": [
    {"item_id": "i1", "sha256": "a" * 64, "decision": "allow"}]}}


def test_a_documents_decision_is_a_sealed_record_with_the_actor_object(tmp_path):
    from databridge_adapter_agents import setup

    rec = setup.record_documents_decision(tmp_path, SID, who=_who(dev_only=True), discovery=DISC)
    led = setup.documents_ledger(tmp_path, SID)
    assert _lines(led.path) == [rec] and rec["seal"] == dl.seal_of(led.key, rec["hash"])
    assert {k: rec[k] for k in dl.ACTOR_FIELDS} == dl.actor_of(_who(dev_only=True))
    got = setup.documents_decision(tmp_path, SID)
    assert got == rec and setup.documents_problems(got, DISC) == []


@pytest.mark.parametrize("bad", [_who(f"{dl.MACHINE_PREFIXES[1]}keeper-x"),
                                 _who(authenticated_by="delegated:sha256:x")])
def test_a_machine_or_a_delegation_never_decides_and_nothing_is_written(tmp_path, bad):
    from databridge_adapter_agents import setup

    for write in (lambda: setup.record_documents_decision(tmp_path, SID, who=bad, discovery=DISC),
                  lambda: setup.record_authorization(tmp_path, SID, "keeper-x", who=bad,
                                                     binding={})):
        with pytest.raises(dl.DecisionRefused) as e:
            write()
        assert e.value.code == dl.MACHINE_ACTOR
    assert not setup.setup_dir(tmp_path, SID).exists()


def test_an_edited_handoff_is_never_an_authorisation(tmp_path):
    from databridge_adapter_agents import setup

    setup.record_authorization(tmp_path, SID, "keeper-x", who=_who(), binding={"a": 1})
    p = setup.handoff_ledger(tmp_path, SID).path
    [rec] = _lines(p)
    rec["binding"] = {"a": 2}                                          # widened after the fact
    rec["hash"] = dl.digest_of(rec, exclude=("hash", "seal"))
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    got = setup.latest_authorization(tmp_path, SID, "keeper-x")
    assert got["refusal"]["code"] == dl.LEDGER_INTEGRITY_FAILED and "binding" not in got
    probs = setup.authorization_problems(tmp_path, SID, "keeper-x", plan_stale=None,
                                         activating=False)
    assert probs and "does not verify" in probs[0]


def test_a_legacy_documents_log_is_not_a_decision_until_decided_again(tmp_path):
    from databridge_adapter_agents import setup

    d = setup.setup_dir(tmp_path, SID)
    d.mkdir(parents=True)
    legacy = {"kind": "documents", "decision": "build", "principal_id": "alice",
              "authenticated_by": "bearer:test", "discovery_run_id": "disc-1",
              "documents_digest": setup.documents_digest(DISC), "at": "2026-10-01T00:00:00"}
    (d / "decisions.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    got = setup.documents_decision(tmp_path, SID)
    assert got["refusal"]["code"] == dl.LEDGER_INTEGRITY_FAILED
    assert "decide again" in setup.documents_problems(got, DISC)[0]
    setup.record_documents_decision(tmp_path, SID, who=_who("bob"), discovery=DISC)
    now = setup.documents_decision(tmp_path, SID)
    assert now["principal_id"] == "bob" and setup.documents_problems(now, DISC) == []


def test_setup_steps_carry_the_actor_object(tmp_path):
    from databridge_workflow_spec import AdapterAgentSpec

    from databridge_adapter_agents import setup

    spec = AdapterAgentSpec.model_validate(setup.draft_spec(SID, "workspace"))
    person = setup.record_step(tmp_path, spec, step="approve_mapping", status="completed",
                               by="alice", reason="r", actor="person", who=_who())
    assert person["actor"] == dl.actor_of(_who()) and person["actor_kind"] == "person"
    agent = setup.record_step(tmp_path, spec, step="propose", status="completed", by="alice",
                              reason="r")
    assert agent["actor"] == setup.agent_actor(spec.id)
    assert dl.is_machine_principal(agent["actor"]["principal_id"])
    with pytest.raises(dl.DecisionRefused):                    # the step names who signed in
        setup.record_step(tmp_path, spec, step="x", status="completed", by="mallory",
                          reason="r", actor="person", who=_who())


def test_the_core_machine_prefixes_are_the_adapter_agents_ones():
    from databridge_adapter_agents.adapt import _MACHINE

    assert tuple(_MACHINE) == dl.MACHINE_PREFIXES


# --- R-028 TIDY #1: a dev-token hand-off keeps its dev marking (the full actor object) -------------
@pytest.mark.parametrize("dev_only", [True, False])
def test_a_handoff_authorisation_stores_the_full_actor_object(tmp_path, dev_only):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry, setup
    from databridge_agent_runtime import register_authenticator

    class _Svc:
        def get_plan(self, *_a, **_k):
            return None

    mgr = AdapterAgentManager(service=_Svc(), registry=AdapterAgentRegistry(root=tmp_path / "aa"),
                              runs_root=tmp_path / "runs")
    d = setup.draft_spec("db", "workspace")
    d["plans"] = {"db": "approved"}
    spec = mgr.create(d, by="alice")
    setup.mark_setup_agent(mgr.registry.root, spec.id, "db", by="alice")
    mech = "tidy-dev" if dev_only else "tidy-real"
    by = register_authenticator(mech, dev_only=dev_only).issue("alice")
    rec = mgr.authorize_setup_handoff(spec.id, by=by, binding={})
    want = dl.actor_of({"principal_id": "alice", "authenticated_by": mech,
                        "principal_type": type(by).__name__, "dev_only": dev_only})
    assert {k: rec[k] for k in want} == want
    assert rec["dev_only"] is dev_only                                    # non-vacuous
    assert setup.latest_authorization(mgr.registry.root, "db", spec.id)["dev_only"] is dev_only
