"""The four artefacts as TYPES: what each one refuses to be, independently of the compiler.

`compile_agent` is the only thing that builds a `CompiledAgent`, but "compiled" has to mean the
same thing to a caller assembling one by hand - otherwise a future code path routes around the
compiler and the word stops meaning anything. Every invariant below is enforced in
`__post_init__`, not in the compiler.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from databridge_adapter import ADAPTER_SINKS, TRUSTED_SINKS
from databridge_agentdef import (
    AgentDefError,
    BudgetAdmission,
    CapabilityGrant,
    CapabilityGrantSet,
    CompiledAgent,
    EvalBinding,
    SinkPolicy,
    build_sink_policy,
    compile_agent,
    read_golden_set,
)

from agentdef_testing import now


def _grant(capability: str = "resolve", purpose: str = "lending.underwriting",
           agent: str = "a", version: int = 1) -> CapabilityGrant:
    return CapabilityGrant(
        agent=agent, version=version, pack="finance", source_id="core_banking",
        capability=capability, purpose=purpose, max_trust="untrusted",
        declaration_digest="sha256:deadbeef", resolved_through="fixtures/packs/finance/sources.yaml")


# --- 1. the capability grant set --------------------------------------------------------------
def test_a_grant_set_cannot_be_empty():
    with pytest.raises(AgentDefError, match="EMPTY grant set"):
        CapabilityGrantSet(agent="a", version=1, grants=())


def test_a_grant_set_holds_one_agent_version():
    """Four artefacts from two declarations is not a compilation, and 'what could this agent do
    in March?' would have no answer (D10)."""
    with pytest.raises(AgentDefError, match="belonging to"):
        CapabilityGrantSet(agent="a", version=1,
                           grants=(_grant(), _grant(agent="b", version=2)))


def test_a_grant_set_refuses_duplicates():
    with pytest.raises(AgentDefError, match="duplicate grant"):
        CapabilityGrantSet(agent="a", version=1, grants=(_grant(), _grant()))


def test_a_grant_id_moves_when_anything_about_the_grant_moves():
    base = _grant()
    assert base.grant_id != _grant(purpose="kyc.onboarding").grant_id
    assert base.grant_id != _grant(capability="plan").grant_id


# --- 2. the sink policy -------------------------------------------------------------------------
def _policy(**overrides) -> dict:
    base = dict(agent="a", version=1, read_ceiling="untrusted",
                forbidden_sinks=tuple(TRUSTED_SINKS),
                adapter_enforced=tuple(s for s in TRUSTED_SINKS if s in ADAPTER_SINKS),
                orchestrator_enforced=tuple(s for s in TRUSTED_SINKS if s not in ADAPTER_SINKS),
                action_sinks=(), requires_field_level_filter=(), reasoning="")
    base.update(overrides)
    return base


def test_a_sink_policy_may_not_name_a_subset_of_the_inventory():
    """An untrusted value may reach NO sink, so a policy naming a subset would be recording an
    exception nobody argued for."""
    narrowed = tuple(TRUSTED_SINKS)[:-1]
    with pytest.raises(AgentDefError, match="whole inventory"):
        SinkPolicy(**_policy(forbidden_sinks=narrowed))


def test_a_new_trusted_sink_lands_in_every_compiled_policy(finance_declaration, catalogue,
                                                            finance_purposes):
    """The equality check means a sink added to `databridge_adapter` cannot be silently missing
    from an agent's policy: every compilation picks it up, and a stale hand-built one fails."""
    decision = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes,
                             now=now)
    assert tuple(decision.compiled.sink_policy.forbidden_sinks) == tuple(TRUSTED_SINKS)


def test_the_enforcement_split_must_partition_the_inventory():
    with pytest.raises(AgentDefError, match="partition"):
        SinkPolicy(**_policy(orchestrator_enforced=()))
    overlap = tuple(TRUSTED_SINKS)
    with pytest.raises(AgentDefError):
        SinkPolicy(**_policy(adapter_enforced=overlap, orchestrator_enforced=overlap))


def test_the_policy_reasoning_says_what_this_package_cannot_check():
    policy = build_sink_policy(agent="a", version=1, read_ceiling="untrusted",
                               action_names=["draft"], untrusted_capable_sources=["core_banking"])
    assert "action.arguments" in policy.reasoning
    assert "the orchestrator enforces them or nothing does" in policy.reasoning
    assert policy.action_sinks == ("action.draft.arguments",)


def test_a_trusted_ceiling_still_names_its_filter_obligation():
    """Trust is per field, so a `trusted` ceiling over an adapter with untrusted columns is a
    filter obligation, not a contradiction - and naming it is the difference between a ceiling
    that is enforced and one that is aspirational."""
    policy = build_sink_policy(agent="a", version=1, read_ceiling="trusted", action_names=[],
                               untrusted_capable_sources=["core_banking", "filings"])
    assert policy.requires_field_level_filter == ("core_banking", "filings")
    assert "field-level filter" in policy.reasoning


# --- 3. the budget admission record -------------------------------------------------------------
def test_a_budget_ceiling_of_zero_is_not_a_budget():
    for kwargs in ({"tokens_per_run": 0}, {"escalations_per_day": 0},
                   {"cost_ceiling_usd_per_day": 0.0}):
        base = dict(agent="a", version=1, tokens_per_run=10, escalations_per_day=1,
                    cost_ceiling_usd_per_day=1.0)
        base.update(kwargs)
        with pytest.raises(AgentDefError):
            BudgetAdmission(**base)


# --- 4. the eval binding --------------------------------------------------------------------------
def _binding(**overrides) -> dict:
    base = dict(agent="a", version=1, golden_set="packs/finance/evals/credit_memo_v3.jsonl",
                golden_set_digest="sha256:x", records=15,
                gates=(("grounding", 1.0),), feasibility=None)
    base.update(overrides)
    return base


def test_an_empty_golden_set_is_not_evidence():
    with pytest.raises(AgentDefError, match="reads as evidence"):
        EvalBinding(**_binding(records=0))


def test_a_golden_set_with_no_gates_is_a_file():
    with pytest.raises(AgentDefError, match="not a gate"):
        EvalBinding(**_binding(gates=()))


def test_the_golden_set_digest_survives_line_ending_normalisation(tmp_path):
    """A checkout under `core.autocrlf` must not produce a different digest for the same reviewed
    file, or every Windows machine would report the abstention measurement as made on another set.
    """
    lf = tmp_path / "lf.jsonl"
    crlf = tmp_path / "crlf.jsonl"
    body = '{"id": "a"}\n{"id": "b"}\n'
    lf.write_bytes(body.encode())
    crlf.write_bytes(body.replace("\n", "\r\n").encode())
    assert read_golden_set(lf).digest == read_golden_set(crlf).digest
    assert read_golden_set(lf).records == 2


def test_comments_and_blank_lines_are_not_records(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text('# a header\n\n{"id": "a"}\n# trailing note\n', encoding="utf-8")
    assert read_golden_set(path).records == 1


def test_a_golden_set_line_that_is_not_json_is_a_loud_failure(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text('{"id": "a"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not JSON"):
        read_golden_set(path)


# --- the four, together ---------------------------------------------------------------------------
def test_a_compiled_agent_cannot_be_assembled_from_two_declarations(finance_declaration,
                                                                    catalogue, finance_purposes):
    from dataclasses import replace

    compiled = compile_agent(finance_declaration, catalogue=catalogue,
                             purposes=finance_purposes, now=now).compiled
    other_budget = BudgetAdmission(agent="someone_else", version=9, tokens_per_run=1,
                                   escalations_per_day=1, cost_ceiling_usd_per_day=1.0)
    with pytest.raises(AgentDefError, match="not a compilation"):
        replace(compiled, budget=other_budget)


def test_a_compiled_agent_cannot_be_built_with_an_artefact_missing(finance_declaration,
                                                                   catalogue, finance_purposes):
    compiled = compile_agent(finance_declaration, catalogue=catalogue,
                             purposes=finance_purposes, now=now).compiled
    with pytest.raises(AgentDefError, match="four artefacts"):
        CompiledAgent(agent=compiled.agent, version=compiled.version, pack=compiled.pack,
                      purpose=compiled.purpose, grants=compiled.grants,
                      sink_policy=compiled.sink_policy, budget=None, evals=compiled.evals,
                      citation=compiled.citation, actions=compiled.actions,
                      declared_in=compiled.declared_in,
                      declaration_digest=compiled.declaration_digest,
                      compiled_at=datetime.now(timezone.utc))
    # (`mint` deliberately omitted above too - but the "artefact missing" check fires first, so
    # this test still proves what it always proved. The mint bypass itself is finding 1's own
    # test, in test_review_findings.py.)


def test_the_record_is_json_shaped_end_to_end(finance_declaration, catalogue, finance_purposes):
    """A decision is what a UI renders and what a ledger row would cite, so it has to serialise."""
    import json

    decision = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes,
                             now=now)
    blob = json.dumps(decision.as_record(), sort_keys=True)
    assert '"outcome": "compiled"' in blob
    assert "cg_" in blob and "ba_" in blob and "eb_" in blob and "ca_" in blob
