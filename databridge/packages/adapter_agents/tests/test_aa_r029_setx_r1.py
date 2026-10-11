"""R-029 SETX, Codex round 1, at the package level.

#1: the items a hand-off build is PINNED to (`run_ingest(only_items=...)`).
#4: an unreadable latest discovery reaches the setup APIs as a fixed sentence and a reference.
#6: a setup Activity record's trigger always carries an actor - the person's, or, for an agent
    step nobody asked for, the agent's own MACHINE actor.
Expectations are derived from the inputs, never written down.
"""
from __future__ import annotations

import re

from test_aa_r029_setx import RUN5, _disc, _item


def test_c1_a_handoff_is_pinned_to_exactly_what_the_person_authorised():
    """The pins: the decided documents (built or kept) and each person-approved table at the
    capture the discovery recorded - nothing held, blocked, reviewed or unapproved."""
    from databridge_adapter_agents.setup import document_items, pinned_items

    tables = {"s.a": {"item_id": "ta", "sha256": "sa"}, "s.b": {"item_id": "tb", "sha256": "sb"},
              "s.h": {"item_id": "th", "sha256": "sh", "held": True}}
    disc = _disc([*RUN5, _item(7, "allow", "new.md")], tables=tables)
    build, keep = document_items(disc)
    want = {d["item_id"]: d["sha256"] for d in [*build, *keep]}
    assert want                                                          # non-vacuous
    got = pinned_items(disc, ["s.a", "s.h"])
    assert got == {**want, "ta": "sa"}                    # s.b unapproved, s.h held: not pinned
    assert RUN5[0]["item_id"] not in got and RUN5[2]["item_id"] not in got   # review / block


def test_m4_an_unreadable_discovery_carries_no_exception_text(monkeypatch, tmp_path, caplog):
    import logging

    import databridge_mcp_ingest.runner as runner
    from databridge_adapter_agents.setup import load_gate

    secret = "C:\\srv\\secret\\discovery.json"

    def boom(source_id):
        raise RuntimeError(f"cannot read {secret}")

    monkeypatch.setattr(runner, "latest_discovery", boom)
    with caplog.at_level(logging.ERROR):
        g = load_gate("feat8-src", tmp_path, plan_stale=None)
    msg = g["discovery"]["message"]
    assert secret not in msg and "RuntimeError" not in msg
    ref = re.search(r"ERR-[0-9a-f]{10}", msg).group(0)
    assert ref in caplog.text and secret in caplog.text


def test_m6_an_agent_step_nobody_asked_for_names_the_agents_machine_actor(tmp_path):
    from databridge_adapter_agents.registry import AdapterAgentRegistry  # noqa: F401
    from databridge_adapter_agents.setup import agent_actor, draft_spec, record_step
    from databridge_workflow_spec import load_spec

    spec = load_spec(draft_spec("feat8-src", "workspace"))
    rec = record_step(tmp_path, spec, step="build", status="completed", by="scheduler",
                      reason="r")
    assert rec["trigger"]["actor"] == agent_actor(spec.id)
    assert rec["trigger"]["actor"]["principal_id"].startswith("adapter_agent:")


def test_c1_the_manager_runs_a_handoff_build_only_under_the_authorisation_it_carries(
        tmp_path, monkeypatch):
    """The manager's own gate (behind the route's): a `setup_handoff` trigger names ONE
    authorisation; it must be the latest, a person's, and still cover the setup as it is."""
    from test_aa_feat8_setup_explain import _issued, _setup_mgr

    from databridge_adapter_agents import setup as setup_mod

    mgr, spec = _setup_mgr(tmp_path)
    gate = {"problems": [], "pending": [], "plan_digest": "p", "discovery": {"run_id": "r"},
            "mapping": None, "explanation": None, "documents_decision": None}
    monkeypatch.setattr(setup_mod, "load_gate", lambda *a, **k: dict(gate))
    binding = setup_mod.gate_binding(plan_digest="p", discovery={"run_id": "r"}, mapping=None,
                                     explanation=None, documents=None, deferred=[])
    first = setup_mod.authorization_ref(
        mgr.authorize_setup_handoff(spec.id, by=_issued("alice"), binding=binding))

    def handoff(ref):
        return mgr.agent_gate(spec, handoff={"type": "setup_handoff", "authorization": ref})

    assert handoff(first) == []
    assert any("carries no hand-off authorisation" in p for p in handoff(None))
    gate["discovery"] = {"run_id": "newer"}                     # the setup moved on
    assert any("changed since" in p for p in handoff(first))
    gate["discovery"] = {"run_id": "r"}
    second = setup_mod.authorization_ref(
        mgr.authorize_setup_handoff(spec.id, by=_issued("alice"), binding=binding))
    assert second != first and handoff(second) == []
    assert any("newer hand-off" in p for p in handoff(first))    # a superseded build refuses
