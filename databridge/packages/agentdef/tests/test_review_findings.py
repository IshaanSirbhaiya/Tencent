"""**R-013** - one test per finding in `wiki/40-reviews/raw/phase4-agentdef.md` (8 Critical,
3 High, 1 Medium), each written to fail against the pre-fix code.

Every test below was verified to fail with the symptom the finding describes: the fix was
reverted in isolation, the corresponding test was run and confirmed red, then the revert was
undone. See `wiki/40-reviews/R-013-agentdef-dispositions.md` for the disposition, including the
two findings recorded here as false positives / downgrades rather than code changes.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from databridge_adapter.purposes import purpose_vocabulary
from databridge_agentdef import (
    AgentDefError,
    CapabilityGrant,
    CompilationDecision,
    CompiledAgent,
    DeclarationError,
    Outcome,
    RefusalCode,
    build_sink_policy,
    compile_agent,
    load_declaration,
    packs_root,
)
from databridge_agentdef._yaml_strict import strict_safe_load
from databridge_agentdef.artefacts import _COMPILER_MINT
from databridge_agentdef.declaration import normalised_text

from agentdef_testing import declaration_path, now


def _codes(decision: CompilationDecision) -> set[RefusalCode]:
    return {r.code for r in decision.refusals}


def _refused(decl, catalogue, purposes) -> CompilationDecision:
    decision = compile_agent(decl, catalogue=catalogue, purposes=purposes, now=now)
    assert decision.outcome is Outcome.REFUSED, decision.reasoning
    return decision


@pytest.fixture
def finance(finance_declaration, catalogue, finance_purposes) -> CompilationDecision:
    return compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes,
                         now=now)


# --- Finding 1 (Critical): CompiledAgent.__post_init__ - compiler refusals are bypassable -------
def test_a_compiled_agent_cannot_be_hand_assembled_even_from_genuinely_valid_artefacts(finance):
    """The bypass codex demonstrated: take four REAL artefacts from a real compile and assemble a
    `CompiledAgent` directly, skipping `compile_agent` entirely. Using real, valid sub-artefacts
    (rather than forged ones) is the stronger proof - it shows the gap was never about whether the
    parts were individually well-formed, only about whether `compile_agent` was ever called."""
    real = finance.compiled
    with pytest.raises(AgentDefError, match="was not constructed by"):
        CompiledAgent(agent=real.agent, version=real.version, pack=real.pack,
                      purpose=real.purpose, grants=real.grants, sink_policy=real.sink_policy,
                      budget=real.budget, evals=real.evals, citation=real.citation,
                      actions=real.actions, declared_in=real.declared_in,
                      declaration_digest=real.declaration_digest, compiled_at=real.compiled_at)
        # `mint` omitted - the default is not `_COMPILER_MINT`.


def test_a_wrong_mint_token_is_refused_the_same_way(finance):
    """Not just "omitted" - an arbitrary object in the `mint` slot is refused identically. Closes
    the trivial "just pass something" attempt."""
    real = finance.compiled
    with pytest.raises(AgentDefError, match="was not constructed by"):
        CompiledAgent(agent=real.agent, version=real.version, pack=real.pack,
                      purpose=real.purpose, grants=real.grants, sink_policy=real.sink_policy,
                      budget=real.budget, evals=real.evals, citation=real.citation,
                      actions=real.actions, declared_in=real.declared_in,
                      declaration_digest=real.declaration_digest, compiled_at=real.compiled_at,
                      mint=object())


def test_the_real_mint_token_is_what_compile_agent_actually_uses(finance):
    """Positive control: the legitimate path is not accidentally broken by the check above."""
    assert finance.compiled.mint is _COMPILER_MINT


# --- Finding 2 (Critical): _parse_actions - touches_subject silently defaults to non-sensitive --
def test_omitting_touches_subject_is_refused_at_load_not_defaulted(mutate):
    """`spec.get("touches_subject", False)` used to default an OMITTED flag to False - the
    non-sensitive answer - on exactly the axis CLAUDE.md hard constraint 1 says must never default
    open. There is no safe default either direction, so omitting it is refused outright."""
    with pytest.raises(DeclarationError, match="touches_subject"):
        mutate("finance", "credit_memo_drafter",
              lambda raw: raw["actions"][0]["draft_credit_memo"].pop("touches_subject"))


def test_an_identity_narrowing_action_left_unmarked_cannot_reach_autonomous(mutate):
    """The realistic exploit: an author adds a NEW action that merges/narrows a subject, forgets
    `touches_subject`, and sets `automation.mode: autonomous`. Before the fix this loaded as
    `touches_subject=False` and compiled straight past `SUBJECT_ACTION_NOT_HUMAN_REQUIRED`."""
    def add_unmarked_autonomous_action(raw):
        raw["actions"].append({
            "merge_subject_records": {
                "automation": {"mode": "autonomous"},
                "reversal": "restore the prior separate records from their retained versions",
                # touches_subject deliberately absent
            }
        })

    with pytest.raises(DeclarationError, match="touches_subject"):
        mutate("finance", "credit_memo_drafter", add_unmarked_autonomous_action)


# --- Finding 3 (Critical): required_per_answer compiles without per-value provenance ------------
def test_required_per_answer_also_needs_the_grounding_gate(mutate, catalogue, finance_purposes):
    """Before the fix, `GROUNDING_GATE_BELOW_CITATION_RULE` fired only for `required_per_field` -
    an agent could declare `required_per_answer` with a `grounding` gate at 0.0, or none at all,
    and compile anyway."""
    def weaken(raw):
        raw["citation"] = "required_per_answer"
        raw["evals"]["gates"]["grounding"] = 0.5

    decl = mutate("finance", "credit_memo_drafter", weaken)
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.GROUNDING_GATE_BELOW_CITATION_RULE in _codes(decision)
    assert "required_per_answer" in decision.reasoning


def test_the_compiled_artefact_records_which_citation_rule_was_claimed(finance):
    """The second half of finding 3: even when the gate WAS satisfied, nothing downstream could
    learn which citation rule the declaration claimed - `EvalBinding`/`CompiledAgent` recorded
    four artefacts and dropped the fifth fact a harness would need to enforce the claim at
    runtime."""
    assert finance.compiled.citation == "required_per_field"
    assert finance.compiled.as_record()["citation"] == "required_per_field"


# --- Finding 4 (Critical): EvalBinding.check - NaN clears every gate ----------------------------
def test_a_nan_result_fails_the_gate_it_would_otherwise_silently_clear(finance):
    ok, why = finance.compiled.evals.check({"grounding": float("nan"),
                                            "abstention_calibration": 0.97})
    assert not ok
    assert any("grounding" in w and "not a finite score" in w for w in why)


def test_an_infinite_result_fails_the_gate(finance):
    ok, why = finance.compiled.evals.check({"grounding": float("inf"),
                                            "abstention_calibration": 0.97})
    assert not ok


def test_a_boolean_result_fails_the_gate_rather_than_being_treated_as_0_or_1(finance):
    ok, why = finance.compiled.evals.check({"grounding": True, "abstention_calibration": 0.97})
    assert not ok


def test_an_out_of_range_result_fails_the_gate(finance):
    ok, why = finance.compiled.evals.check({"grounding": 1.5, "abstention_calibration": 0.97})
    assert not ok


def test_a_genuinely_passing_run_still_passes(finance):
    """Negative control: the tightened checks do not turn a legitimate pass into a failure."""
    ok, why = finance.compiled.evals.check({"grounding": 1.0, "abstention_calibration": 0.97})
    assert ok and why == ()


# --- Finding 5 (Critical): an injected vocabulary need not belong to the declaration's pack ------
def test_a_single_pack_vocabulary_for_the_wrong_pack_is_refused(finance_declaration, catalogue,
                                                                 government_purposes):
    """`finance_declaration.pack == "finance"`; `government_purposes` covers only `government`.
    Before the fix, `vocab.declares(purpose)` on a vocabulary that never covered `finance` at all
    was the only check, and its message ("not declared by pack X") did not even notice the
    vocabulary was for a different pack entirely."""
    decision = _refused(finance_declaration, catalogue, government_purposes)
    assert RefusalCode.PURPOSE_NOT_PACK_DECLARED in _codes(decision)
    assert "government" in decision.reasoning and "finance" in decision.reasoning


def test_a_multi_pack_vocabulary_cannot_lend_another_packs_purpose(mutate, catalogue):
    """The subtler case: a vocabulary that DOES cover the declaring pack (so the first screen
    passes) but whose only declaration of the NAMED purpose belongs to a different pack in that
    same vocabulary. `vocab.declares(purpose)` alone cannot see this - it only checks membership
    in the merged namespace, not which pack declared the entry."""
    both = purpose_vocabulary(["finance", "government"], packs_root())
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw.__setitem__("purpose", "permits.assessment"))
    decision = _refused(decl, catalogue, both)
    assert RefusalCode.PURPOSE_NOT_PACK_DECLARED in _codes(decision)
    assert "government" in decision.reasoning and "finance" in decision.reasoning


# --- Finding 6 (Critical): action enforcement metadata is discarded ------------------------------
def test_the_compiled_agent_carries_each_actions_automation_metadata(finance, finance_declaration):
    """Before the fix, `CompiledAgent` carried only `action.<name>.arguments` sink NAMES - nothing
    that says `automation.mode`, `touches_subject` or `reversal` for any action. A future D12
    commit-path enforcer would have nothing to check an action against except re-reading the
    declaration file, which contradicts "four artefacts, or refuses"."""
    compiled = finance.compiled
    assert compiled.actions == finance_declaration.actions
    memo = compiled.action("draft_credit_memo")
    assert memo.touches_subject is True
    assert memo.automation_mode == "human_required"
    cache = compiled.action("refresh_exposure_cache")
    assert cache.touches_subject is False
    assert cache.automation_mode == "autonomous"
    assert cache.reversal is not None


# --- Finding 7 (Critical, DOWNGRADED - see disposition): decided_at is wall-clock, but nothing
# hashes it, so the VERDICT is still deterministic without `now=`. -------------------------------
def test_the_verdict_is_deterministic_even_without_a_supplied_clock(finance_declaration, catalogue,
                                                                     finance_purposes):
    """Compile twice with NO `now=` (the scenario the finding describes) and show that everything
    that is supposed to be an identity - `compiled_id`, `outcome`, `refusals`, `reasoning`, and
    every artefact id - agrees, even though the two audit timestamps do not have to."""
    a = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes)
    b = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes)
    assert a.outcome is b.outcome is Outcome.COMPILED
    assert a.reasoning == b.reasoning
    assert a.compiled.compiled_id == b.compiled.compiled_id
    ar, br = a.as_record(), b.as_record()
    ar.pop("decided_at")
    br.pop("decided_at")
    ar["compiled"].pop("compiled_at")
    br["compiled"].pop("compiled_at")
    assert ar == br


# --- Finding 8 (Critical): duplicate YAML governance keys silently accepted ----------------------
def test_strict_safe_load_refuses_a_duplicate_top_level_key():
    with pytest.raises(Exception, match="duplicate key"):
        strict_safe_load("citation: none\ncitation: required_per_field\n")


def test_strict_safe_load_refuses_a_duplicate_nested_key():
    with pytest.raises(Exception, match="duplicate key"):
        strict_safe_load(
            "actions:\n  - draft_x:\n      touches_subject: true\n      touches_subject: false\n")


def test_strict_safe_load_accepts_ordinary_yaml_identically_to_safe_load():
    """Negative control: the stricter loader is not stricter than SafeLoader on ordinary input."""
    import yaml

    text = normalised_text(declaration_path("finance", "credit_memo_drafter"))
    assert strict_safe_load(text) == yaml.safe_load(text)


def test_load_declaration_refuses_a_declaration_file_with_a_duplicated_governance_key(tmp_path):
    """End-to-end: `load_declaration` itself, not just the loader function in isolation. A
    reviewed `citation: required_per_field` followed later by a second `citation: none` in the
    same file used to load as `none` with no trace of the first."""
    real = declaration_path("finance", "credit_memo_drafter")
    text = normalised_text(real)
    assert text.count("citation: required_per_field") == 1
    poisoned = text.replace("citation: required_per_field",
                            "citation: required_per_field\ncitation: none", 1)
    target = tmp_path / "credit_memo_drafter.yaml"
    target.write_text(poisoned, encoding="utf-8")
    with pytest.raises(DeclarationError, match="duplicate key"):
        load_declaration(target)


# --- High 9: non-finite budget ceilings ------------------------------------------------------
def test_an_infinite_cost_ceiling_is_refused_at_load_not_silently_admitted(mutate):
    """`.inf` passed BOTH `cost <= 0` (inf is not <= 0) and `BudgetAdmission`'s own positivity
    check (inf > 0 is True), so an "unlimited" daily cost ceiling compiled as if it were a real
    number."""
    with pytest.raises(DeclarationError, match="finite"):
        mutate("finance", "credit_memo_drafter",
              lambda raw: raw["budget"].__setitem__("cost_ceiling_usd_per_day", float("inf")))


def test_a_nan_cost_ceiling_is_refused_at_load_not_crashed_deep_in_compile(mutate):
    """`.nan` passed `_number` and `cost <= 0` (both comparisons false against NaN), then blew up
    inside `BudgetAdmission.__post_init__` as an uncaught `AgentDefError` - a THIRD outcome next
    to 'compiled' and 'refused' that the compiler's own contract says cannot exist."""
    with pytest.raises(DeclarationError, match="finite"):
        mutate("finance", "credit_memo_drafter",
              lambda raw: raw["budget"].__setitem__("cost_ceiling_usd_per_day", float("nan")))


# --- High 10: abstention measurement provenance omits measured_by --------------------------------
def test_an_abstention_budget_with_no_measurer_named_is_refused(mutate, catalogue,
                                                                 finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["abstention"].pop("measured_by"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ABSTENTION_NOT_FEASIBILITY_TESTED in _codes(decision)
    assert "measured_by" in decision.reasoning


# --- High 11: compiled_id / grant_id do not cover the whole compiled identity --------------------
def _grant(**overrides) -> CapabilityGrant:
    base = dict(agent="a", version=1, pack="finance", source_id="core_banking",
               capability="resolve", purpose="lending.underwriting", max_trust="untrusted",
               declaration_digest="sha256:deadbeef",
               resolved_through="fixtures/packs/finance/sources.yaml")
    base.update(overrides)
    return CapabilityGrant(**base)


def test_grant_id_moves_when_resolved_through_moves():
    base = _grant()
    moved = _grant(resolved_through="fixtures/packs/finance/adapters/alt_core_banking.yaml")
    assert base.grant_id != moved.grant_id


def test_compiled_id_moves_when_the_sink_policy_moves(finance):
    compiled = finance.compiled
    alt_policy = build_sink_policy(
        agent=compiled.agent, version=compiled.version, read_ceiling="trusted",
        action_names=[a.name for a in compiled.actions],
        untrusted_capable_sources=["core_banking"])
    assert alt_policy.policy_id != compiled.sink_policy.policy_id
    moved = replace(compiled, sink_policy=alt_policy)
    assert moved.compiled_id != compiled.compiled_id


def test_compiled_id_moves_when_the_citation_rule_moves(finance):
    compiled = finance.compiled
    moved = replace(compiled, citation="required_per_answer")
    assert moved.compiled_id != compiled.compiled_id


def test_compiled_id_moves_when_the_action_metadata_moves(finance):
    compiled = finance.compiled
    moved = replace(compiled, actions=compiled.actions[:1])
    assert moved.compiled_id != compiled.compiled_id


# --- Medium 12: content_digest omits governance semantics -----------------------------------------
def test_content_digest_distinguishes_declarations_that_differ_only_in_budget(finance_declaration):
    other = replace(finance_declaration,
                    budget=replace(finance_declaration.budget, tokens_per_run=999999))
    assert other.content_digest() != finance_declaration.content_digest()


def test_content_digest_distinguishes_declarations_that_differ_only_in_eval_gates(
        finance_declaration):
    other = replace(finance_declaration,
                    evals=replace(finance_declaration.evals, gates=(("grounding", 1.0),)))
    assert other.content_digest() != finance_declaration.content_digest()


def test_content_digest_distinguishes_declarations_that_differ_only_in_abstention(
        finance_declaration):
    other = replace(finance_declaration,
                    abstention=replace(finance_declaration.abstention,
                                       max_abstention_rate=0.99))
    assert other.content_digest() != finance_declaration.content_digest()
