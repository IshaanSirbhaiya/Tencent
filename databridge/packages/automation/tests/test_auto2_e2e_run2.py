"""AUTO2 — E2E run 2 findings N-04, N-21 and the compiler side of N-03
(wiki/40-reviews/E2E-2026-10-01-run2.md; dispositions in wiki/40-reviews/R-023-AUTO2.md).

The catalogue below mirrors the run's live catalogue (artefacts/http/0006_p1_catalogue.json):
the same connection ids, the same tool names, classified by the SHARED classifier, and the
registered adapter agent `larkspur_graph_sync` with its two connections. Expectations are derived
from the catalogue and the classifier — never recalled. No network, no live model."""
from __future__ import annotations

import json
import re

import pytest

from automation_testing import PACK, PURPOSE, purposes, repo_with_golden
from databridge_agent_runtime import capability_for_tool
from databridge_agent_runtime.classify import classify_tool as shared_classify
from databridge_automation import (
    AdapterAgentInfo,
    Catalogue,
    ConnectionInfo,
    Governance,
    classify_tool,
    compile_english,
    federated_question_id,
)
from databridge_automation.catalogue import load_adapter_agents
from databridge_automation.compiler import ModelProposal, _validate_tool
from databridge_harness import FakeBackend, Harness
from databridge_workflow_spec import WorkflowSpec, parse_tool

# --- the run's catalogue -----------------------------------------------------------------------
DB_TOOLS = ("list_schemas", "list_objects", "get_object_details", "explain_query",
            "analyze_workload_indexes", "analyze_query_indexes", "analyze_db_health",
            "get_top_queries", "execute_sql")
LDB = ConnectionInfo(id="larkspur_db", title="Larkspur CRM", kind="database",
                     tools=tuple(classify_tool(t) for t in DB_TOOLS), aliases=("crm",),
                     locations=("public.invoices", "public.customers"), plan_status="approved")
LFILES = ConnectionInfo(id="larkspur-files", title="Larkspur files", kind="files",
                        tools=tuple(classify_tool(t, read_only=True) for t in
                                    ("list_roots", "list_dir", "stat_file", "read_bytes")),
                        locations=("Meetings", "Contracts"), plan_status="approved")
WRITER = ConnectionInfo(id="larkspur_db_writer", title="Larkspur CRM writer", kind="database",
                        purpose="agent_write", writes_for=("larkspur_db",),
                        tools=tuple(classify_tool(t) for t in DB_TOOLS))
FOLLOWUPS = ConnectionInfo(id="larkspur-followups", title="Larkspur follow-ups", kind="files",
                           purpose="agent_write",
                           tools=(classify_tool("write_file"),), plan_status="approved")
AA = AdapterAgentInfo(id="larkspur_graph_sync",
                      title="Keep Larkspur files and CRM database in the graph",
                      connections=("larkspur-files", "larkspur_db"), status="active")


def run_catalogue(*, adapter_agents=(AA,), golden=None) -> Catalogue:
    return Catalogue(connections=(LFILES, LDB, WRITER, FOLLOWUPS),
                     kinds=("person", "organisation", "document", "invoice", "meeting"),
                     subject_kinds=("person",),
                     governance=Governance(pack=PACK, purpose=PURPOSE, golden_set=golden),
                     adapter_agents=tuple(adapter_agents))


#: The run's exact sentence (artefacts/http/0040_p2_compile_delegate_r0.json).
N04_SENTENCE = ("When I ask, answer my questions that need both larkspur_db and larkspur-files by "
                "delegating them to the larkspur_graph_sync adapter agent. Read only; cite every "
                "number.")


def _assert_delegating_agent(res, aa: str = AA.id, sources=None) -> WorkflowSpec:
    assert res.kind == "agent", (res.kind, [q.text for q in res.open_questions])
    assert res.spec is not None and isinstance(res.spec, WorkflowSpec), \
        [q.text for q in res.open_questions]
    spec = res.spec
    assert list(spec.delegates) == [aa]
    assert f"delegate.ask:{aa}" in spec.tools
    assert spec.mode == "read_only" and not spec.actions
    want = set(sources) if sources is not None else set(AA.connections)
    assert want and want <= set(spec.sources)
    assert f"delegate.ask:{aa}" in spec.instructions
    assert any(d["field"] == f"delegates[{aa}]" for d in res.derivations)
    assert any(d["field"] == f"tools[delegate.ask:{aa}]" for d in res.derivations)
    return spec


# ================================================================================================
# N-04 — a workflow agent that delegates is built from English
# ================================================================================================
def test_n04_the_runs_exact_sentence_compiles_to_a_delegating_agent(tmp_path):
    root = repo_with_golden(tmp_path)
    cat = run_catalogue(golden="packs/workspace/evals/automations.jsonl")
    res = compile_english(N04_SENTENCE, cat, purposes=purposes(), repo_root=root)
    assert res.open_questions == []
    spec = _assert_delegating_agent(res)
    kind = next(d for d in res.derivations if d["field"] == "kind")
    assert kind["value"] == "agent" and kind["by"] == "rule"
    # the reference is in the user's words, at the span the derivation names
    lo, hi = kind["span"]
    assert AA.id in N04_SENTENCE[lo:hi]
    # the governance half grants exactly the delegate's capability, and the draft can be activated
    assert res.compilation is not None and res.compilation.compiled, res.compilation.as_record()
    assert capability_for_tool(f"delegate.ask:{AA.id}") in res.declaration_yaml
    assert res.activatable
    assert "delegates" in res.understood


@pytest.mark.parametrize("text, sources", [
    ("Ask larkspur_graph_sync to answer my questions across larkspur_db and larkspur-files. "
     "Read only.", None),
    ("Answer my cross-source questions via the larkspur graph sync agent; read only.", None),
    ("Route questions that span the CRM and the Larkspur files to the Keep Larkspur files and CRM "
     "database in the graph adapter agent. Read only.", None),
    ("Use the larkspur_graph_sync adapter agent to answer questions about invoices and meeting "
     "notes.", None),
    ("Delegate questions about larkspur_db to larkspur_graph_sync.", ("larkspur_db",)),
])
def test_n04_paraphrases_compile_to_the_same_kind_of_agent(text, sources):
    res = compile_english(text, run_catalogue())
    spec = _assert_delegating_agent(res, sources=sources)
    if sources is not None:                  # the words named a source: only it is declared
        assert set(spec.sources) == set(sources)


def test_n04_no_named_source_declares_the_delegates_connections_from_the_registry():
    res = compile_english("Use the larkspur_graph_sync adapter agent to answer questions about "
                          "invoices and meeting notes.", run_catalogue())
    for cid in AA.connections:
        d = next(d for d in res.derivations if d["field"] == f"sources[{cid}]")
        assert d["by"] == "catalogue" and AA.id in d["evidence"]


def test_n04_an_unregistered_adapter_agent_is_a_question_not_a_new_adapter_agent():
    text = ("Answer my questions that need both larkspur_db and larkspur-files by delegating them "
            "to the larkspur_crm_mirror adapter agent. Read only.")
    res = compile_english(text, run_catalogue())
    assert res.kind == "agent" and res.spec is None
    q = next(q for q in res.open_questions if q.id.startswith("delegate:"))
    assert q.options == [AA.id]
    res2 = compile_english(text, run_catalogue(), answers={q.id: AA.id})
    _assert_delegating_agent(res2)
    d = next(d for d in res2.derivations if d["field"] == f"delegates[{AA.id}]")
    assert d["by"] == "answer"
    # an answer that is not a registered adapter agent keeps the question open
    res3 = compile_english(text, run_catalogue(), answers={q.id: "larkspur_crm_mirror"})
    assert any(x.id == q.id for x in res3.open_questions) and res3.spec is None


def test_n04_with_no_adapter_agent_registered_the_request_is_asked_not_rebuilt():
    res = compile_english(N04_SENTENCE, run_catalogue(adapter_agents=()))
    assert res.kind == "agent" and res.spec is None
    assert any(q.id.startswith("delegate:") and q.options == [] for q in res.open_questions)
    # "none" reads the sources directly: an agent, never an adapter agent
    qid = next(q.id for q in res.open_questions if q.id.startswith("delegate:"))
    res2 = compile_english(N04_SENTENCE, run_catalogue(adapter_agents=()), answers={qid: "none"})
    assert res2.kind == "agent" and res2.spec is not None and not res2.spec.delegates
    assert not any(parse_tool(t)["family"] == "delegate" for t in res2.spec.tools)


def test_n04_a_paused_adapter_agent_is_not_offered():
    paused = AdapterAgentInfo(id=AA.id, title=AA.title, connections=AA.connections,
                              status="paused")
    res = compile_english(N04_SENTENCE, run_catalogue(adapter_agents=(paused,)))
    assert res.spec is None or not res.spec.delegates
    assert _validate_tool(f"delegate.ask:{AA.id}", run_catalogue(adapter_agents=(paused,)),
                          write=False)
    assert _validate_tool(f"delegate.ask:{AA.id}", run_catalogue(), write=False) is None
    assert _validate_tool(f"delegate.ask:{AA.id}", run_catalogue(), write=True)


def test_n04_building_an_adapter_agent_still_builds_one():
    """The widening must not swallow the adapter-agent rule: no registered name, no delegation."""
    res = compile_english("Keep the Larkspur files folder in the graph; check hourly",
                          run_catalogue())
    assert res.kind == "adapter_agent"
    assert not any(d["field"].startswith("delegates[") for d in res.derivations)


def test_n04_delegating_and_building_at_once_is_asked():
    text = "Keep larkspur-files in the graph and delegate questions to larkspur_graph_sync."
    res = compile_english(text, run_catalogue())
    q = next(q for q in res.open_questions if q.id == "kind")
    assert set(q.options) == {"agent", "adapter_agent"} and res.spec is None
    res2 = compile_english(text, run_catalogue(), answers={"kind": "agent"})
    _assert_delegating_agent(res2, sources=("larkspur-files",))


def _proposal(**kw) -> str:
    return json.dumps(ModelProposal.model_validate(kw).model_dump())


def test_n04_the_model_sees_the_adapter_agents_and_cannot_turn_the_request_into_one():
    other = AdapterAgentInfo(id="drive_sync", title="Drive in the graph",
                             connections=("larkspur-files",))

    def respond(model, messages, guided_json):
        # the run's model: proposed an adapter agent; here also a delegate the user never named
        return _proposal(kind="adapter_agent", title="Cross-source answers",
                         sources=["larkspur_db", "larkspur-files"],
                         tools=[f"delegate.ask:{AA.id}", "delegate.ask:drive_sync"])
    fb = FakeBackend(respond)
    res = compile_english(N04_SENTENCE, run_catalogue(adapter_agents=(AA, other)),
                          harness=Harness(fb))
    shown = json.dumps(fb.calls[0]["messages"])
    assert f"delegate.ask:{AA.id}" in shown and "adapter_agents" in shown
    assert not any(q.id == "model_kind" for q in res.open_questions)
    spec = _assert_delegating_agent(res)
    assert "delegate.ask:drive_sync" not in spec.tools
    assert any("delegate.ask:drive_sync" in n for n in res.notes)


def test_n04_load_adapter_agents_reads_the_registry_like_the_registry(tmp_path):
    import yaml
    base = tmp_path / "adapter_agents"
    (base / "_state").mkdir(parents=True)

    def spec(aid, status):
        return {"spec": "adapter_agent/v1", "id": aid, "title": f"{aid} title",
                "created_from": "Keep larkspur-files in the graph",
                "connections": ["larkspur-files"], "sync": {"type": "manual"},
                "extract": {"pack": PACK}, "screen": {"pack": PACK},
                "freshness": {"max_age_hours": 24}, "status": status}
    for aid, status in (("aa_active", "active"), ("aa_draft", "draft"),
                        ("aa_paused", "paused"), ("aa_deleted", "paused")):
        (base / f"{aid}.yaml").write_text(yaml.safe_dump(spec(aid, status)), encoding="utf-8")
    (base / "_state" / "aa_deleted.json").write_text(json.dumps({"deleted": True}),
                                                     encoding="utf-8")
    (base / "broken.yaml").write_text("spec: adapter_agent/v1\nid: [", encoding="utf-8")
    got = {a.id: a for a in load_adapter_agents(base)}
    assert set(got) == {"aa_active", "aa_draft", "aa_paused"}          # deleted + broken absent
    assert got["aa_active"].connections == ("larkspur-files",)
    cat = Catalogue(connections=(LFILES,), adapter_agents=tuple(got.values()))
    from databridge_automation.catalogue import adapter_agents_of
    offered = {a.id for a in adapter_agents_of(cat)}
    assert offered == {aid for aid, a in got.items() if a.status != "paused"} and offered
    rec = cat.as_record()["adapter_agents"]
    assert {r["id"] for r in rec if r["delegable"]} == offered


# ================================================================================================
# N-21 — only write-classified tools are offered as writes
# ================================================================================================
OVERDUE = ("Overdue invoice follow-up: every weekday at 9am, find open invoices in larkspur_db "
           "that are overdue or due within the next 30 days. For each one, save a follow-up "
           "email draft to larkspur-followups and tell me after. Also log a follow-up task in "
           "the CRM for each invoice, but ask me before doing that.")


def test_n21_the_writer_question_offers_only_write_classified_tools():
    res = compile_english(OVERDUE, run_catalogue())
    q = next(q for q in res.open_questions if q.id.startswith("tool:") and "log" in q.id)
    classified = [t for t in DB_TOOLS if shared_classify(t).writes]
    assert classified                                    # non-vacuous: there are writes to offer
    assert q.options and set(q.options) <= set(classified)
    reads = [t for t in DB_TOOLS if not shared_classify(t).writes]
    assert reads and not set(q.options) & set(reads)   # list_schemas, explain_query, … are absent
    # answering a READ tool does not make it the write; answering a write does
    res_read = compile_english(OVERDUE, run_catalogue(), answers={q.id: reads[0]})
    assert any(x.id == q.id for x in res_read.open_questions)
    res_ok = compile_english(OVERDUE, run_catalogue(), answers={q.id: q.options[0]})
    assert not any(x.id == q.id for x in res_ok.open_questions)


def test_n21_a_model_write_on_a_read_classified_tool_of_a_writer_is_refused():
    reason = _validate_tool("source.write:larkspur_db_writer:list_schemas", run_catalogue(),
                            write=True)
    assert reason and "write" in reason
    assert _validate_tool("source.write:larkspur_db_writer:execute_sql", run_catalogue(),
                          write=True) is None


def test_n21_the_model_is_shown_only_classified_writes():
    seen = {}

    def respond(model, messages, guided_json):
        seen["m"] = messages
        return _proposal(kind="agent", title="x")
    compile_english(OVERDUE, run_catalogue(), harness=Harness(FakeBackend(respond)))
    cat = json.loads(seen["m"][1]["content"].split("CATALOGUE:\n", 1)[1].split("\n\n", 1)[0])
    writer = next(c for c in cat["connections"] if c["id"] == WRITER.id)
    assert writer["write_tools"] == [t for t in DB_TOOLS if shared_classify(t).writes]


# ================================================================================================
# N-03 (compiler side) — a federated planner question is answerable through `answers`
# ================================================================================================
def test_n03_federated_question_id_is_the_agreed_format():
    q = "Account owner is stored only on the customers table. Which field should group the rows?"
    qid = federated_question_id(q)
    assert re.fullmatch(r"fed:[0-9a-f]{40}", qid)
    import hashlib
    assert qid == "fed:" + hashlib.sha1(" ".join(q.split()).encode("utf-8")).hexdigest()
    assert federated_question_id("  " + q.replace(" ", "   ") + "\n") == qid
    assert federated_question_id(q + "?") != qid


def test_n03_an_answer_to_a_planner_question_reaches_the_delegating_agent_and_the_model():
    q = "Sub-task t1 has no field account_owner. Which table holds it?"
    qid = federated_question_id(q)
    answer = "customers.account_owner, joined on invoices.customer_id"
    seen = {}

    def respond(model, messages, guided_json):
        seen["m"] = messages
        return _proposal(kind="agent", title="Cross-source answers")
    res = compile_english(N04_SENTENCE, run_catalogue(), harness=Harness(FakeBackend(respond)),
                          answers={qid: answer})
    spec = _assert_delegating_agent(res)
    assert answer in spec.instructions
    assert answer in json.dumps(seen["m"])
    d = next(d for d in res.derivations if d["field"] == f"answers[{qid}]")
    assert d["by"] == "answer"
    # without a delegate there is no federated planner to clarify: nothing is added
    plain = compile_english("Summarise the open invoices in larkspur_db.", run_catalogue(),
                            answers={qid: answer})
    assert plain.spec is not None and answer not in plain.spec.instructions
