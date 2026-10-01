"""**Refusal is the feature.** One test per refusal, each starting from a declaration that
actually compiles and breaking exactly one thing.

A hand-written broken fixture proves only that a hand-written broken fixture is refused. Mutating
the real file proves the check is what stands between that file and a capability grant - so
deleting the check from `compiler.py` fails the test, which is the property this module exists to
have.

The last three tests are the structural ones:

* every refusal named in `STRUCTURAL_REFUSALS` has a live call site in `compiler.py` - so a
  refactor that drops one fails even if nobody remembers to write a case for it;
* no public signature and no dataclass field anywhere in the package is waiver-shaped;
* nothing reads an environment variable.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import databridge_agentdef
from databridge_agentdef import (
    STRUCTURAL_REFUSALS,
    CompilationRefused,
    Outcome,
    RefusalCode,
    compile_agent,
    compile_or_raise,
)
from databridge_agentdef.errors import DeclarationError

from agentdef_testing import now


def _codes(decision) -> set[RefusalCode]:
    return {r.code for r in decision.refusals}


def _refused(decl, catalogue, purposes):
    decision = compile_agent(decl, catalogue=catalogue, purposes=purposes, now=now)
    assert decision.outcome is Outcome.REFUSED, decision.reasoning
    assert decision.compiled is None
    return decision


# --- 1. no citation rule --------------------------------------------------------------------
def test_a_declaration_with_no_citation_rule_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("citation"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.NO_CITATION_RULE in _codes(decision)
    assert "SourcePointer" in decision.reasoning


def test_citation_none_is_refused_by_name_not_by_omission(mutate, catalogue, finance_purposes):
    """`none` is spellable so that "this agent cites nothing" is refused explicitly. A rule that
    can only be omitted is a rule nobody ever has to argue for."""
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw.__setitem__("citation", "none"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.NO_CITATION_RULE in _codes(decision)
    assert "citation: none" in decision.reasoning


# --- 2. a capability the target adapter does not advertise -----------------------------------
def test_a_capability_the_adapter_does_not_advertise_is_refused(mutate, catalogue,
                                                                finance_purposes):
    """AGENT_PLANE §3 illustrates `core_banking.account_read`. No such capability is advertised
    anywhere in this tree, and the screen is against the ESTATE rather than against this file."""
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["capabilities"].append("core_banking.account_read"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.CAPABILITY_NOT_ADVERTISED in _codes(decision)
    detail = next(r.detail for r in decision.refusals
                  if r.code is RefusalCode.CAPABILITY_NOT_ADVERTISED)
    assert "account_read" in detail and "resolve" in detail      # names both sides of the compare


def test_a_capability_naming_an_unknown_adapter_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["capabilities"].append("shadow_ledger.resolve"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ADAPTER_NOT_DECLARED in _codes(decision)


def test_a_write_capability_cannot_be_granted_as_a_read(mutate, catalogue, government_purposes,
                                                        tmp_path, monkeypatch):
    """A write is an ActionType with a human gate (§6.4), never a read grant with a write's name.

    The shipped MCP adapter declares an EMPTY write set, so this test builds an estate whose
    adapter advertises one - the case a real deployment would eventually meet.
    """
    from databridge_agentdef import load_catalogue

    estate = tmp_path / "government" / "adapters"
    estate.mkdir(parents=True)
    (estate / "permits_registry_mcp.yaml").write_text(
        "adapter: permits_registry_mcp\n"
        "source_id: permits_registry_mcp\n"
        "trust_class: {application_id: trusted, applicant_notes: untrusted}\n"
        "capabilities:\n"
        "  read: [permit_lookup, permit_search]\n"
        "  write: [permit_status_set]\n", encoding="utf-8")
    (tmp_path / "government" / "sources.yaml").write_text(
        "sources:\n"
        "  - id: case_files\n"
        "    trust_class: untrusted\n"
        "    capabilities: [describe, plan, resolve, verify_against_capture]\n", encoding="utf-8")
    widened = load_catalogue(["government"], root=tmp_path)

    decl = mutate("government", "permit_assessment_drafter",
                  lambda raw: raw["capabilities"].append(
                      "permits_registry_mcp.permit_status_set"))
    decision = _refused(decl, widened, government_purposes)
    assert RefusalCode.CAPABILITY_IS_A_WRITE in _codes(decision)


# --- 3. a purpose the pack does not declare ---------------------------------------------------
def test_a_purpose_the_pack_does_not_declare_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw.__setitem__("purpose", "lending.freeform_analysis"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.PURPOSE_NOT_PACK_DECLARED in _codes(decision)
    assert "purposes.yaml" in decision.reasoning


def test_another_packs_purpose_is_not_this_packs_purpose(mutate, catalogue, finance_purposes):
    """`permits.assessment` is a real, declared purpose - in the GOVERNMENT pack. Vocabulary is
    per pack, and borrowing another's lawful basis is not a lawful basis."""
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw.__setitem__("purpose", "permits.assessment"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.PURPOSE_NOT_PACK_DECLARED in _codes(decision)


# --- 4. an action with no automation.mode -----------------------------------------------------
def test_an_action_with_no_automation_mode_is_refused(mutate, catalogue, finance_purposes):
    def drop_mode(raw):
        raw["actions"][0]["draft_credit_memo"]["automation"] = {}

    decl = mutate("finance", "credit_memo_drafter", drop_mode)
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ACTION_WITHOUT_AUTOMATION_MODE in _codes(decision)
    assert "only write path" in decision.reasoning


def test_an_action_with_no_automation_block_at_all_is_refused(mutate, catalogue,
                                                              finance_purposes):
    def drop_block(raw):
        raw["actions"][0]["draft_credit_memo"].pop("automation")

    decl = mutate("finance", "credit_memo_drafter", drop_block)
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ACTION_WITHOUT_AUTOMATION_MODE in _codes(decision)


def test_the_scalar_automation_shorthand_is_refused_at_load(mutate):
    """§3 illustrates `automation: human_required`; §4 defines `automation.mode`. The scalar has
    no room for the compensating reversal an autonomous action needs, so it is not a second
    accepted form - it is a `DeclarationError` that tells the author what to write."""
    with pytest.raises(DeclarationError, match="automation.mode"):
        mutate("finance", "credit_memo_drafter",
               lambda raw: raw["actions"][0]["draft_credit_memo"].__setitem__(
                   "automation", "human_required"))


def test_a_subject_touching_action_may_only_be_human_required(mutate, catalogue,
                                                              finance_purposes):
    """CLAUDE.md hard constraint 1, generalised. No setting in this build reaches this."""
    for mode in ("human_notified", "autonomous"):
        decl = mutate("finance", "credit_memo_drafter",
                      lambda raw, m=mode: raw["actions"][0]["draft_credit_memo"]["automation"]
                      .__setitem__("mode", m))
        decision = _refused(decl, catalogue, finance_purposes)
        assert RefusalCode.SUBJECT_ACTION_NOT_HUMAN_REQUIRED in _codes(decision)
        assert "attests" in decision.reasoning


def test_an_autonomous_action_must_declare_its_reversal(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["actions"][1]["refresh_exposure_cache"].pop("reversal"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.AUTONOMOUS_ACTION_NOT_COMPENSABLE in _codes(decision)


# --- 5. an abstention budget asserted without a feasibility test (Kotte) ----------------------
def test_an_abstention_budget_with_no_measurement_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["abstention"].pop("measured_base_risk"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ABSTENTION_NOT_FEASIBILITY_TESTED in _codes(decision)
    assert "arXiv:2606.29054" in decision.reasoning
    assert "(mu-alpha)/(1-alpha)" in decision.reasoning


def test_an_abstention_budget_measured_on_another_golden_set_is_refused(mutate, catalogue,
                                                                        finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["abstention"].__setitem__("golden_set_digest",
                                                            "sha256:" + "0" * 64))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ABSTENTION_MEASURED_ON_ANOTHER_GOLDEN_SET in _codes(decision)


def test_an_uncertifiable_alpha_for_the_task_class_is_refused(mutate, catalogue,
                                                              government_purposes):
    """Kotte found alpha = 0.10 uncertifiable for NER-class tasks. The government agent is
    span-extraction shaped, so lowering its target to 0.10 makes it uncompilable rather than
    safer."""
    def tighten(raw):
        raw["abstention"]["target_alpha"] = 0.10
        raw["abstention"]["abstain_when"] = "certificate.risk > 0.10"

    decl = mutate("government", "permit_assessment_drafter", tighten)
    decision = _refused(decl, catalogue, government_purposes)
    assert RefusalCode.ABSTENTION_UNCERTIFIABLE_FOR_TASK_CLASS in _codes(decision)
    assert "arXiv:2606.29054" in decision.reasoning
    assert "six models and eight datasets" in decision.reasoning


def test_an_infeasible_abstention_budget_is_refused(mutate, catalogue, finance_purposes):
    """mu = 0.60 against alpha = 0.08 forces (0.60-0.08)/0.92 = 0.565 abstention, well past the
    0.25 this desk declared it could carry."""
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["abstention"].__setitem__("measured_base_risk", 0.60))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ABSTENTION_INFEASIBLE in _codes(decision)
    assert "0.5652" in decision.reasoning or "0.565" in decision.reasoning


def test_an_abstention_calibration_gate_needs_a_budget(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("abstention"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.ABSTENTION_GATE_WITHOUT_BUDGET in _codes(decision)


# --- the other artefacts that cannot be produced ----------------------------------------------
def test_a_declaration_with_no_budget_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("budget"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.NO_BUDGET in _codes(decision)


def test_a_partial_budget_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["budget"].pop("cost_ceiling_usd_per_day"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.BUDGET_INCOMPLETE in _codes(decision)


def test_a_missing_golden_set_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["evals"].__setitem__("golden_set",
                                                       "packs/finance/evals/does_not_exist.jsonl"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.GOLDEN_SET_MISSING in _codes(decision)


def test_a_golden_set_outside_the_tree_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["evals"].__setitem__("golden_set", "../../../etc/passwd"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.GOLDEN_SET_OUTSIDE_TREE in _codes(decision)


def test_no_evals_block_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("evals"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.NO_EVAL_BINDING in _codes(decision)


def test_a_grounding_gate_below_the_citation_rule_is_refused(mutate, catalogue, finance_purposes):
    """'Every emitted field carries a SourcePointer' has no acceptable failure rate. A declaration
    whose citation rule and whose gate disagree is worse than one that claims less."""
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw["evals"]["gates"].__setitem__("grounding", 0.99))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.GROUNDING_GATE_BELOW_CITATION_RULE in _codes(decision)


def test_no_trust_ceiling_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("trust_ceiling"))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.NO_TRUST_CEILING in _codes(decision)


def test_no_capabilities_is_refused(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter",
                  lambda raw: raw.__setitem__("capabilities", []))
    decision = _refused(decl, catalogue, finance_purposes)
    assert RefusalCode.NO_CAPABILITIES_DECLARED in _codes(decision)


# --- every reason at once ----------------------------------------------------------------------
def test_every_reason_is_collected_not_just_the_first(mutate, catalogue, finance_purposes):
    """A declaration that is wrong in three ways must show three reasons. Reporting only the
    first makes the second appear as a surprise on the next attempt."""
    def wreck(raw):
        raw.pop("citation")
        raw["capabilities"].append("core_banking.account_read")
        raw["abstention"].pop("measured_base_risk")

    decl = mutate("finance", "credit_memo_drafter", wreck)
    decision = _refused(decl, catalogue, finance_purposes)
    assert {RefusalCode.NO_CITATION_RULE, RefusalCode.CAPABILITY_NOT_ADVERTISED,
            RefusalCode.ABSTENTION_NOT_FEASIBILITY_TESTED} <= _codes(decision)


def test_compile_or_raise_carries_the_whole_decision(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("citation"))
    with pytest.raises(CompilationRefused) as excinfo:
        compile_or_raise(decl, catalogue=catalogue, purposes=finance_purposes, now=now)
    assert excinfo.value.decision.outcome is Outcome.REFUSED
    assert RefusalCode.NO_CITATION_RULE in _codes(excinfo.value.decision)


def test_a_refused_decision_cannot_carry_a_compiled_agent(mutate, catalogue, finance_purposes):
    """Asserted on the type, not merely on the compiler: there is no 'compiled with
    reservations', because that is the shape a waiver takes when nobody names it a waiver."""
    from dataclasses import replace

    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("citation"))
    decision = _refused(decl, catalogue, finance_purposes)
    with pytest.raises(ValueError, match="must carry the compiled agent"):
        replace(decision, outcome=Outcome.COMPILED, refusals=())


# --- the structural guarantees ------------------------------------------------------------------
def test_every_structural_refusal_has_a_live_check_in_the_compiler():
    """Reads `compiler.py` and fails if a structural code stops having a `refuse(...)` call site.

    This is the test that survives a refactor. A per-code negative test can be deleted alongside
    the check it guards, in one commit, and nothing notices; this one fails on the same commit.
    """
    source = Path(inspect.getsourcefile(compile_agent)).read_text(encoding="utf-8")
    body = source.split("def compile_agent(", 1)[1]
    for code in sorted(STRUCTURAL_REFUSALS, key=lambda c: c.name):
        pattern = rf"refuse\(\s*RefusalCode\.{code.name}\b"
        assert re.search(pattern, body), (
            f"{code.name} is in STRUCTURAL_REFUSALS but has no live `refuse(RefusalCode."
            f"{code.name}, ...)` call in compile_agent. Either the check was removed - in which "
            f"case put it back - or the code should not be structural, which is an argument to "
            f"make in a review, not a line to delete")


#: Words an override would plausibly be spelled with. Matched against the identifier's snake_case
#: TOKENS rather than as substrings, because `enforced_by` contains "force" and is the opposite of
#: a waiver. The test below scans EVERY public callable and every dataclass field rather than
#: checking the three names we thought of first.
_WAIVER_TOKENS = frozenset({
    "force", "forced", "override", "overrides", "overridden", "unsafe", "bypass", "waive",
    "waived", "waiver", "disable", "disabled", "relax", "relaxed", "skip", "ignore", "nocheck",
    "lenient", "permissive",
})
#: Phrases whose waiver-ness lives in the pair rather than in either word.
_WAIVER_PHRASES = ("allow_unsafe", "allow_all", "auto_approve", "permit_all", "no_check",
                   "no_verify")


def _WAIVER_SHAPED(identifier: str):                        # noqa: N802 - reads as a predicate
    tokens = set(identifier.lower().split("_"))
    if tokens & _WAIVER_TOKENS:
        return True
    return any(phrase in identifier.lower() for phrase in _WAIVER_PHRASES)


def test_nothing_in_this_package_takes_a_waiver_shaped_argument():
    """'There is no "run it anyway" flag, because that flag is how every governed system
    eventually becomes ungoverned' (AGENT_PLANE §3). Asserted over the whole public surface."""
    import dataclasses

    def _params(fn) -> list[str]:
        # Builtins and C-implemented members have no introspectable signature. Skipping them is
        # safe here precisely because they are not ours: nothing in this package can add a
        # parameter to `str.strip`.
        try:
            return list(inspect.signature(fn).parameters)
        except (ValueError, TypeError):
            return []

    offenders: list[str] = []
    checked = 0
    for name in databridge_agentdef.__all__:
        obj = getattr(databridge_agentdef, name)
        if dataclasses.is_dataclass(obj):
            offenders += [f"{name}.{f.name}" for f in dataclasses.fields(obj)
                          if _WAIVER_SHAPED(f.name)]
            checked += 1
        if inspect.isclass(obj):
            for attr, member in vars(obj).items():
                if callable(member) and not attr.startswith("__"):
                    checked += 1
                    offenders += [f"{name}.{attr}({p})" for p in _params(member)
                                  if _WAIVER_SHAPED(p)]
        elif callable(obj):
            checked += 1
            offenders += [f"{name}({p})" for p in _params(obj)
                          if _WAIVER_SHAPED(p)]
    # A scan that silently inspected nothing would pass forever. `compile_agent`, `compile_all`,
    # `compile_or_raise`, `GrantedAuthority.require` and every artefact's fields are in there.
    assert checked > 30, f"the waiver scan only reached {checked} members; it is not scanning"
    assert not offenders, (
        f"waiver-shaped parameter(s)/field(s) found: {offenders}. A compilation that can be "
        f"overridden is not a compilation, it is a suggestion")


def test_no_module_in_this_package_reads_an_environment_variable_to_decide_a_refusal():
    """`paths.py` reads two env vars, and they choose WHERE to look - a fact about the world.

    Nothing else may read one at all: an environment variable that changed a refusal would be an
    override flag with a different spelling and no review.
    """
    pkg_dir = Path(inspect.getsourcefile(databridge_agentdef)).parent
    for path in sorted(pkg_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "paths.py":
            continue
        assert "os.environ" not in text and "getenv" not in text, (
            f"{path.name} reads the environment. Only paths.py may, and only to answer 'where do "
            f"packs and estates live'")


def test_an_unparseable_golden_set_is_refused_not_crashed(mutate, catalogue,
                                                          finance_purposes):
    """A compiler whose contract is 'four artefacts or a refusal' must not have a third outcome
    where it raises. An author who pointed `golden_set` at the wrong file needs the sentence."""
    from databridge_agentdef import databridge_root

    bad = Path("packs/finance/evals/_not_jsonl.jsonl")

    target = databridge_root() / bad
    target.write_text("this is prose, not JSONL\n", encoding="utf-8")
    try:
        decl = mutate("finance", "credit_memo_drafter",
                      lambda raw: raw["evals"].__setitem__("golden_set", bad.as_posix()))
        decision = _refused(decl, catalogue, finance_purposes)
        assert RefusalCode.GOLDEN_SET_UNREADABLE in _codes(decision)
        assert "one json object per line" in decision.reasoning.lower()
    finally:
        target.unlink()
