"""AA — the adaptation policy, the principal rule and attribute naming, without a server."""
from __future__ import annotations

import json

import pytest

from databridge_adapter_agents.adapt import (ADDITIVE, AdaptationRefused, Policy, _concept,
                                             attr_name_for, principal_of)
from databridge_mcp_ingest.drift import DRIFT_KINDS, Drift

from aa_drift_kit import Principal, agent_spec


def _spec(adapt=None):
    from databridge_workflow_spec import AdapterAgentSpec

    return AdapterAgentSpec.model_validate(agent_spec("c", adapt=adapt))


def _d(kind, **kw):
    return Drift(kind=kind, unit="table:t", detail=kind, **kw)


def test_policy_reads_the_spec_adapt_block_and_defaults():
    default = Policy.of(_spec())
    assert default.policy == "auto_additive" and default.on_sync and default.on_list_changed
    off = Policy.of(_spec({"policy": "off", "watch": {"on_sync": False, "on_list_changed": True}}))
    assert off.policy == "off" and not off.on_sync
    assert Policy.of(object()).policy == "auto_additive"            # a spec from before `adapt`


def test_classification_table():
    auto = Policy.of(_spec())
    structural = [k for k in DRIFT_KINDS if k != "subject_touching"]
    for kind in structural:
        asked, why = auto.asks(_d(kind))
        assert asked == (kind not in ADDITIVE), (kind, why)
    # always asked, whatever the policy
    assert auto.asks(_d("added_column", subject_touching=True, subject_reason="x"))[0]
    narrowed = Policy.of(_spec({"always_ask": ["new_write_tool", "subject_touching"]}))
    assert narrowed.asks(_d("new_write_tool"))[0] and narrowed.asks(_d("renamed"))[0]
    assert not narrowed.asks(_d("type_changed"))[0]          # the spec took it out: adapts
    ask_all = Policy.of(_spec({"policy": "ask_all"}))
    assert all(ask_all.asks(_d(k))[0] for k in structural)
    off = Policy.of(_spec({"policy": "off"}))
    # `off` reports everything EXCEPT the mandatory kinds, which are asked under every policy
    # (R-019 #1 — this line used to assert that `off` asked nothing, which was the bug)
    from databridge_adapter_agents.adapt import MANDATORY

    assert not any(off.asks(_d(k))[0] for k in structural if k not in MANDATORY)
    assert all(off.asks(_d(k))[0] for k in structural if k in MANDATORY)


def test_a_name_is_not_a_principal():
    for bad in (None, "owner", Principal(""), {"principal_id": "rules"}):
        with pytest.raises(AdaptationRefused):
            principal_of(bad, "approve")
    ok = principal_of(Principal("owner@x.example"), "approve")
    assert ok == {"principal_id": "owner@x.example", "authenticated_by": "test_boundary",
                  "principal_type": "Principal"}


def test_attribute_names_rules_first_model_second_validated():
    from databridge_harness import FakeBackend, Harness

    assert attr_name_for("region")[:2] == ("region", "rules")
    assert attr_name_for("Contract Value")[:2] == ("contract_value", "rules")
    assert attr_name_for("日付")[0] is None                    # no rule, no model: not guessed
    calls = []

    def answer(name):
        def handler(*, model, messages, guided_json):
            calls.append(messages)
            return json.dumps({"attribute": name, "rationale": "a date"})
        return Harness(FakeBackend(handler), profile="cloud")

    name, mode, why = attr_name_for("日付", harness=answer("signed_on"), table="t")
    assert (name, mode) == ("signed_on", "model") and len(calls) == 1
    assert attr_name_for("日付", harness=answer("Bad Name!"))[0] is None
    assert attr_name_for("日付", harness=answer("region"), taken=("region",))[0] is None


def test_concept_names():
    assert _concept("public.shipments_2025") == "shipment"
    assert _concept("companies") == "company"
    assert _concept("public.address") == "address"
