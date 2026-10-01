"""The two real declarations in this tree compile, and what compilation produced is checkable.

D11's claim is that a declaration compiles to grants rather than to a prompt. These tests read the
two files that actually ship - `packs/finance/agents/credit_memo_drafter.yaml` and
`packs/government/agents/permit_assessment_drafter.yaml` - and assert the four artefacts against
facts held elsewhere: the estate's advertisements, the pack's purpose vocabulary, the golden set
on disk, and `databridge_adapter.TRUSTED_SINKS`.
"""
from __future__ import annotations

import pytest

from databridge_adapter import ADAPTER_SINKS, TRUSTED_SINKS
from databridge_agentdef import (
    GrantedAuthority,
    Outcome,
    compile_agent,
    compile_or_raise,
    load_pack_agents,
    read_golden_set,
)

from agentdef_testing import FINANCE_AGENT, GOVERNMENT_AGENT, now


@pytest.fixture
def finance(finance_declaration, catalogue, finance_purposes):
    return compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes,
                         now=now)


@pytest.fixture
def government(government_declaration, catalogue, government_purposes):
    return compile_agent(government_declaration, catalogue=catalogue,
                         purposes=government_purposes, now=now)


def test_both_declarations_compile(finance, government):
    for decision in (finance, government):
        assert decision.outcome is Outcome.COMPILED, decision.reasoning
        assert decision.refusals == ()
        assert decision.compiled is not None


def test_the_pack_declares_exactly_these_agents():
    """Both packs' `agents/` directories load, and the file name IS the agent name."""
    finance = load_pack_agents("finance")
    government = load_pack_agents("government")
    assert [d.agent for d in finance] == [FINANCE_AGENT]
    assert [d.agent for d in government] == [GOVERNMENT_AGENT]
    assert finance[0].version == 3 and finance[0].supersedes == 2
    assert government[0].version == 2 and government[0].supersedes == 1


# --- artefact 1: the capability grant set --------------------------------------------------
def test_every_grant_is_bound_to_an_adapter_and_a_purpose(finance, government, catalogue):
    """§3: 'a capability grant set - each bound to a specific adapter and purpose'."""
    for decision in (finance, government):
        compiled = decision.compiled
        assert len(compiled.grants) == len(compiled.grants.capabilities)
        for grant in compiled.grants:
            advert = catalogue.require(grant.source_id)
            assert advert.advertises(grant.capability)
            assert grant.purpose == compiled.purpose
            assert grant.resolved_through == advert.declared_in
            assert grant.grant_id.startswith("cg_")


def test_the_grant_set_is_exactly_what_the_declaration_asked_for(finance, finance_declaration):
    assert finance.compiled.grants.capabilities == frozenset(finance_declaration.capabilities)


def test_the_government_agent_is_granted_MCP_TOOLS_not_port_verbs(government):
    """The two capability shapes both survive the screen (AGENT_PLANE §3 vs §6.2)."""
    caps = government.compiled.grants.capabilities
    assert "permits_registry_mcp.permit_lookup" in caps      # a named tool from the allowlist
    assert "case_files.resolve" in caps                      # a port verb from the estate


# --- artefact 2: the sink policy ------------------------------------------------------------
def test_the_sink_policy_covers_the_whole_trusted_sink_inventory(finance, government):
    """§3: 'a sink policy - the TRUSTED_SINKS positions this agent's untrusted values may not
    reach'. The answer is all of them, and the policy says so rather than naming a subset."""
    for decision in (finance, government):
        policy = decision.compiled.sink_policy
        assert tuple(policy.forbidden_sinks) == tuple(TRUSTED_SINKS)
        assert set(policy.adapter_enforced) | set(policy.orchestrator_enforced) == set(
            TRUSTED_SINKS)
        assert not set(policy.adapter_enforced) & set(policy.orchestrator_enforced)
        # The positions no `CapabilityRequest` carries, so `screen_sinks` cannot see them and the
        # policy must name them rather than imply coverage.
        #
        # DERIVED, not listed. This assertion used to hardcode the two business-plane sinks
        # (`action.arguments`, `prompt.instruction`) and broke the moment the inventory grew: the
        # untrusted-decides-access fix added three `policy.*` sinks, because a row rule or subquery
        # column is a data-plane position no request field covers either. A hardcoded expectation
        # turns a deliberate widening of the sink inventory into a red test, which trains whoever
        # sees it to edit the number rather than ask why it moved.
        assert set(policy.orchestrator_enforced) == set(TRUSTED_SINKS) - set(ADAPTER_SINKS)
        # ...and it is not vacuous: these positions genuinely exist and are not adapter-screened.
        assert {"action.arguments", "prompt.instruction"} <= set(policy.orchestrator_enforced)


def test_the_sink_policy_names_a_position_per_declared_action(finance, finance_declaration):
    expected = {f"action.{a.name}.arguments" for a in finance_declaration.actions}
    assert set(finance.compiled.sink_policy.action_sinks) == expected


def test_the_sink_policy_names_the_adapters_that_can_serve_untrusted_cells(government):
    policy = government.compiled.sink_policy
    assert policy.read_ceiling == "untrusted"
    assert "permits_registry_mcp" in policy.requires_field_level_filter


# --- artefact 3: the budget admission record ------------------------------------------------
def test_the_budget_is_admitted_with_all_three_ceilings(finance):
    budget = finance.compiled.budget
    assert (budget.tokens_per_run, budget.escalations_per_day) == (40000, 25)
    assert budget.cost_ceiling_usd_per_day == pytest.approx(12.00)
    assert budget.enforced_by == "databridge_harness"
    assert budget.admission_id.startswith("ba_")


# --- artefact 4: the eval binding -----------------------------------------------------------
def test_the_eval_binding_pins_the_golden_set_by_digest(finance, repo_root):
    binding = finance.compiled.evals
    on_disk = read_golden_set(repo_root / binding.golden_set)
    assert binding.golden_set_digest == on_disk.digest
    # Derived (HC2, SWEEP #24-34): the record count is the file's JSON-object lines, counted here
    # independently of `read_golden_set` — never a number written down.
    import json
    raw = (repo_root / binding.golden_set).read_text(encoding="utf-8").splitlines()
    counted = sum(1 for line in raw if line.strip() and not line.strip().startswith("#")
                  and isinstance(json.loads(line), dict))
    assert counted >= 1                                                    # non-vacuous
    assert binding.records == on_disk.records == counted
    assert binding.gate_map == {"grounding": 1.0, "abstention_calibration": 0.95}


def test_the_agent_may_not_run_until_the_gates_pass(finance):
    binding = finance.compiled.evals
    ok, why = binding.check({"grounding": 1.0, "abstention_calibration": 0.97})
    assert ok and why == ()
    ok, why = binding.check({"grounding": 0.99, "abstention_calibration": 0.97})
    assert not ok and "grounding" in why[0]
    # A gate with no reported result FAILS. An eval suite that stopped emitting a metric would
    # otherwise switch the gate off by omission.
    ok, why = binding.check({"grounding": 1.0})
    assert not ok and "abstention_calibration" in why[0]


def test_the_feasibility_report_rides_on_the_binding(finance, government):
    finance_report = finance.compiled.evals.feasibility
    assert finance_report.feasible and finance_report.certifiable
    assert finance_report.minimum_abstention == pytest.approx((0.11 - 0.08) / (1 - 0.08))
    assert finance_report.golden_set_digest == finance.compiled.evals.golden_set_digest

    gov_report = government.compiled.evals.feasibility
    assert gov_report.task_class == "span_extraction"
    assert gov_report.target_alpha > 0.10           # above Kotte's uncertifiable level
    assert gov_report.feasible


# --- the decision as a record ---------------------------------------------------------------
def test_compilation_is_deterministic(finance_declaration, catalogue, finance_purposes):
    a = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes, now=now)
    b = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes, now=now)
    assert a.as_record() == b.as_record()
    assert a.compiled.compiled_id == b.compiled.compiled_id


def test_the_decision_carries_the_inputs_it_was_computed_against(finance, catalogue):
    from databridge_agentdef import catalogue_digest

    assert finance.catalogue_digest == catalogue_digest(catalogue)
    assert finance.vocabulary_packs == ("finance",)
    assert finance.declaration_digest.startswith("sha256:")


def test_the_reasoning_names_all_four_artefacts(finance):
    text = finance.reasoning
    for phrase in ("capability grant", "sink policy", "budget admission record", "eval binding"):
        assert phrase in text, f"reasoning does not mention {phrase!r}:\n{text}"
    assert "no escalation path" in text


def test_compile_or_raise_returns_the_compiled_agent(finance_declaration, catalogue,
                                                     finance_purposes):
    compiled = compile_or_raise(finance_declaration, catalogue=catalogue,
                                purposes=finance_purposes, now=now)
    assert compiled.ref == "finance/credit_memo_drafter@v3"
    authority = GrantedAuthority(compiled)
    assert authority.purpose == "lending.underwriting"
