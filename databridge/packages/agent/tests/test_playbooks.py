"""The playbook table is DATA, and it cannot smuggle in a capability the code refuses to have.

Two invariants, both from CLAUDE.md §Hard constraints 3 and 4:

* adding an insurer stays a YAML change — in fact no change at all, because an unlisted code falls
  through to a default;
* nothing in ``databridge_agent``'s Python branches on an issuer or package code.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
from pydantic import ValidationError

import databridge_agent
from databridge_agent import (
    ClinicAgent,
    IdentityToolRefused,
    Playbook,
    PlaybookError,
    SendCapabilityRefused,
    load_playbooks,
    playbooks,
)
from databridge_intake.rules import rules

PKG_ROOT = pathlib.Path(databridge_agent.__file__).resolve().parent

MINIMAL = """
version: 1
defaults: {escalate_to: front_desk_lead, requires_human_approval: true}
playbooks:
  - id: p1
    title: T
    when: W
    tools: [blocked_intakes]
    escalate_to: front_desk_lead
issuers:
  default_escalate_to: insurer_liaison
  overrides: []
"""


def _write(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "agent_playbooks.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_table_loads_and_every_row_names_an_owner():
    table = playbooks()
    assert table.playbooks, "the shipped table is empty"
    for p in table.playbooks:
        assert p.tools, f"{p.id} names no tools"
        assert p.escalate_to
        assert p.requires_human_approval is True


def test_a_playbook_cannot_turn_the_human_gate_off():
    """`Literal[True]` — the YAML cannot express an agent acting alone (constraint 4)."""
    with pytest.raises(ValidationError):
        Playbook(id="p", title="t", when="w", tools=(), escalate_to="lead",
                 requires_human_approval=False)


def test_a_playbook_naming_a_send_tool_is_refused_at_load(tmp_path):
    """Config is edited more casually than code. It must not be a back door around a capability
    `tools.py` refuses to hold."""
    bad = MINIMAL.replace("tools: [blocked_intakes]", "tools: [send_email]")
    with pytest.raises(SendCapabilityRefused):
        load_playbooks(_write(tmp_path, bad))


def test_a_playbook_naming_an_identity_tool_is_refused_at_load(tmp_path):
    bad = MINIMAL.replace("tools: [blocked_intakes]", "tools: [select_patient]")
    with pytest.raises(IdentityToolRefused):
        load_playbooks(_write(tmp_path, bad))


def test_the_minimal_fixture_loads_unmodified(tmp_path):
    """Guards the two tests above: they must fail on the tool name, not on the fixture."""
    assert load_playbooks(_write(tmp_path, MINIMAL)).by_id("p1").tools == ("blocked_intakes",)


def test_duplicate_playbook_ids_are_refused(tmp_path):
    """A shadowed playbook is one whose escalation nobody can find by reading the file."""
    text = MINIMAL.replace("issuers:", "  - id: p1\n"
                                       "    title: T2\n"
                                       "    when: W2\n"
                                       "    tools: [coverage_gap]\n"
                                       "    escalate_to: billing_lead\n"
                                       "issuers:")
    with pytest.raises(ValidationError, match="unique"):
        load_playbooks(_write(tmp_path, text))


def test_an_unknown_playbook_id_fails_loud(tmp_path):
    with pytest.raises(PlaybookError):
        load_playbooks(_write(tmp_path, MINIMAL)).by_id("nope")


# --- constraint 3 -----------------------------------------------------------------


def test_an_unlisted_insurer_falls_through_to_the_default():
    """Adding an insurer to `coverage_rules.yaml` needs NO change here at all."""
    table = playbooks()
    assert table.escalation_for("A_CODE_NOBODY_HAS_ADDED_YET") == "insurer_liaison"
    assert table.escalation_for(None) == "insurer_liaison"


def test_giving_an_insurer_its_own_owner_is_one_yaml_row_and_no_code(tmp_path):
    before = load_playbooks(_write(tmp_path, MINIMAL))
    assert before.escalation_for("NEWCO1") == "insurer_liaison"

    after = load_playbooks(_write(tmp_path, MINIMAL.replace(
        "overrides: []", "overrides:\n    - {code: NEWCO1, escalate_to: newco_desk}")))
    assert after.escalation_for("NEWCO1") == "newco_desk"
    assert after.escalation_for("SOMETHING_ELSE") == "insurer_liaison"


def test_playbook_guidance_reaches_the_prompt_for_the_tools_that_exist(registry):
    """The table has to be LIVE, not decorative. If nothing read it, "adding a playbook is a YAML
    row" would be true and worthless."""
    notes = ClinicAgent(registry).playbook_notes()
    shipped = playbooks().by_id("blocked_intakes_report")
    assert shipped.title in notes
    assert "do not rank, score or hint" in " ".join(notes.split())


def test_a_playbook_whose_tools_are_absent_is_not_offered(registry):
    """Only `blocked_intakes` is registered. Advertising a playbook that reads `coverage_gap`
    would invite the model to answer from memory when the call fails."""
    notes = ClinicAgent(registry).playbook_notes()
    assert playbooks().by_id("coverage_gap_report").title not in notes


def test_adding_a_playbook_is_a_yaml_row_and_no_code(tmp_path, registry):
    added = MINIMAL.replace("    escalate_to: front_desk_lead\nissuers:",
                            "    escalate_to: front_desk_lead\n"
                            "    guidance: Count first, denominator second.\nissuers:")
    agent = ClinicAgent(registry, playbooks=load_playbooks(_write(tmp_path, added)))
    assert "Count first, denominator second." in agent.playbook_notes()


def _codes() -> set[str]:
    return ({i.code for i in rules().issuers}
            | {p.package_code for p in rules().packages if p.package_code})


def _condition_strings(source: str) -> list[str]:
    """String literals appearing in a branch or comparison — where an `if code == "X"` would live.

    Same detector as `packages/intake/tests/test_rules_hardening.py`; the invariant is the repo's,
    not the package's, and the two tests should catch the same thing.
    """
    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        tests = []
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            tests = [node.test]
        elif isinstance(node, ast.Compare):
            tests = [node.left, *node.comparators]
        elif isinstance(node, ast.Match):
            tests = [node.subject] + [c.pattern for c in node.cases]
        for t in tests:
            out += [n.value for n in ast.walk(t)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return out


def test_no_issuer_code_appears_in_a_branch_condition():
    offenders = [f"{py.relative_to(PKG_ROOT)}: {code}"
                 for py in PKG_ROOT.rglob("*.py")
                 for code in _codes()
                 if any(code in s for s in
                        _condition_strings(py.read_text(encoding="utf-8")))]
    assert not offenders, (
        "the agent branches on an issuer/package code — escalation must come from "
        "agent_playbooks.yaml: " + "; ".join(sorted(offenders)))


def test_the_branch_detector_catches_a_planted_branch(tmp_path):
    """A guard test is only worth its runtime if it fails on the thing it claims to catch."""
    code = sorted(_codes())[0]
    planted = f'def pick(c):\n    if c == "{code}":\n        return "special"\n'
    assert code in _condition_strings(planted)
    assert code not in _condition_strings(f'"""docstring naming {code}"""\n')
