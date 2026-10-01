"""WorkflowSpec → agentdef declaration, verified by the REAL compiler."""
from __future__ import annotations

import pytest
import yaml

from agent_runtime_helpers import (PACK, briefing_spec, followup_spec, governance, make_pair)

from databridge_agentdef import Outcome, RefusalCode, compile_agent
from databridge_agent_runtime import (
    MappingError,
    build_catalogue,
    capability_for_tool,
    declaration_from_workflow,
    pair_problems,
)
from databridge_agent_runtime.runtime import _connection_adverts
from databridge_workflow_spec import SpecError, WorkflowSpec, load_spec, parse_tool


def _catalogue(spec, gateway):
    cat, problems = build_catalogue(pack=PACK, connections=_connection_adverts(spec, gateway),
                                    graph=True, retrieval=True)
    assert problems == []
    return cat


def test_generated_declaration_compiles(gateway, compile_inputs):
    for spec_dict in (briefing_spec(), followup_spec()):
        spec, decl = make_pair(spec_dict, save_followup_draft=False)
        decision = compile_agent(decl, catalogue=_catalogue(spec, gateway),
                                 packs_dir=compile_inputs.packs_dir,
                                 repo_root=compile_inputs.repo_root)
        assert decision.outcome is Outcome.COMPILED, decision.reasoning
        assert pair_problems(spec, decl) == []
        # every non-write workflow tool is granted, and nothing else is
        wanted = {capability_for_tool(t) for t in spec.tools} - {None}
        assert decision.compiled.grants.capabilities == wanted
        assert wanted                                                    # non-vacuous


def test_capability_mapping_follows_the_tool_grammar():
    for tool in ("graph.search", "retrieve.search", "source.query:larkspur-db",
                 "source.read:larkspur-files:read_text_file", "source.read:larkspur-files",
                 "source.write:larkspur-files:write_file"):
        p = parse_tool(tool)
        cap = capability_for_tool(tool)
        if p["verb"] == "write":
            assert cap is None                   # a write is never a capability; it is an action
            continue
        sid, _, name = cap.partition(".")
        if p["family"] != "source":
            assert (sid, name) == (p["family"], p["op"])
        else:
            assert sid == p["connection"].replace("-", "_")
            assert name != ""


def test_read_only_yields_no_actions_and_modes_are_copied_verbatim():
    ro_spec, ro_decl = make_pair(briefing_spec())
    assert ro_decl.actions == ()
    rw_spec, rw_decl = make_pair(followup_spec(), save_followup_draft=False)
    by_name = {a.name: a for a in rw_decl.actions}
    assert set(by_name) == {a.name for a in rw_spec.actions}
    for a in rw_spec.actions:
        assert by_name[a.name].automation_mode == a.automation_mode
        assert by_name[a.name].reversal == a.reversal


def test_notified_action_needs_an_explicit_subject_answer():
    spec = WorkflowSpec.model_validate(followup_spec())
    gov = governance()                                  # touches_subject not answered
    with pytest.raises(MappingError, match="touches_subject"):
        declaration_from_workflow(spec, PACK, gov)
    text = declaration_from_workflow(spec, PACK, governance(save_followup_draft=False))
    data = yaml.safe_load(text)
    flags = {next(iter(a)): next(iter(a.values()))["touches_subject"] for a in data["actions"]}
    # the human_required action defaults to the conservative answer; the notified one is as given
    assert flags == {"save_followup_draft": False, "log_followup_task": True}


def test_autonomous_without_reversal_is_refused_by_the_spec():
    d = followup_spec()
    d["actions"][0] = {**d["actions"][0], "automation_mode": "autonomous", "reversal": None}
    with pytest.raises(SpecError, match="reversal"):
        load_spec({"spec": "workflow/v1", **d})


def test_autonomous_without_reversal_is_refused_by_the_compiler(gateway, compile_inputs):
    spec, decl = make_pair(followup_spec(), save_followup_draft=False)
    text = declaration_from_workflow(spec, PACK, governance(save_followup_draft=False))
    data = yaml.safe_load(text)
    body = data["actions"][0]["save_followup_draft"]
    body["automation"]["mode"] = "autonomous"
    body.pop("reversal", None)
    from databridge_agent_runtime import parse_declaration_text
    bad = parse_declaration_text(yaml.safe_dump(data, sort_keys=False), declared_in="x")
    decision = compile_agent(bad, catalogue=_catalogue(spec, gateway),
                             packs_dir=compile_inputs.packs_dir,
                             repo_root=compile_inputs.repo_root)
    assert decision.outcome is Outcome.REFUSED
    assert RefusalCode.AUTONOMOUS_ACTION_NOT_COMPENSABLE in {r.code for r in decision.refusals}


def test_catalogue_comes_from_the_gateway_not_the_workflow(gateway, compile_inputs):
    """A tool the workflow names but the gateway does not advertise is refused by the compiler."""
    spec, decl = make_pair(briefing_spec(tools=["graph.search",
                                                "source.read:larkspur-files:secret_tool"]))
    decision = compile_agent(decl, catalogue=_catalogue(spec, gateway),
                             packs_dir=compile_inputs.packs_dir,
                             repo_root=compile_inputs.repo_root)
    assert decision.outcome is Outcome.REFUSED
    assert RefusalCode.CAPABILITY_NOT_ADVERTISED in {r.code for r in decision.refusals}


def test_non_snake_agent_id_is_refused():
    spec = WorkflowSpec.model_validate(briefing_spec(agent="account-briefing"))
    with pytest.raises(MappingError, match="snake_case"):
        declaration_from_workflow(spec, PACK, governance())


def test_halves_that_disagree_are_named():
    spec, decl = make_pair(briefing_spec())
    wider = WorkflowSpec.model_validate(briefing_spec(tools=[*briefing_spec()["tools"],
                                                             "graph.path"]))
    problems = pair_problems(wider, decl)
    assert any("graph.path" in p for p in problems)
