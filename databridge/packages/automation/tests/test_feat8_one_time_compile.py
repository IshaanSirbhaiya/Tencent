"""FEAT8 (2026-10-02): a ONE-TIME task is compiled by the SAME compiler agents use.

``compile_english(text, catalogue, run_once=True)`` is the agent path with three differences, and
only three:

* **no trigger.** The spec's trigger is ``manual`` and nothing schedules it. Words that name a
  schedule or an event are not silently dropped: they become a question (a recurring job is an
  agent, built in the Builder);
* **every write waits for a person.** Each declared action is ``human_required`` whatever the
  words said ("tell me after" is noted, not applied) — a one-time task never auto-approves;
* **its id says what it is** (``once_…``), so its runs are never mistaken for an agent's.

Everything else — grants, purpose, golden set, the honest question for an unconnected channel, the
clarifying questions — is the agent compiler's own, unchanged. Both estates: the workspace and the
finance manifests' own database and follow-ups write connections, read from ``infra/``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from databridge_automation import Catalogue, ConnectionInfo, Governance, ModelProposal, ToolRef, \
    classify_tool, compile_english

ROOT = Path(__file__).resolve().parents[3]                          # databridge/
MANIFESTS = {"workspace": ROOT / "infra" / "mcp_servers.yaml",
             "finance": ROOT / "infra" / "mcp_servers.finance.yaml"}
KINDS = ("person", "organisation", "document")


def _servers(estate: str) -> list[dict]:
    doc = yaml.safe_load(MANIFESTS[estate].read_text(encoding="utf-8"))
    return [s for s in doc.get("servers") or [] if isinstance(s, dict)]


def estate_connections(estate: str) -> tuple[ConnectionInfo, ConnectionInfo]:
    """The estate's ingest database (read) and its files write connection, as its manifest
    declares them — ids, titles, aliases and tool names are READ, never written down here."""
    servers = _servers(estate)
    db = sorted((s for s in servers if s.get("source_class") == "database"
                 and s.get("purpose", "ingest") != "agent_write"), key=lambda s: s["id"])[0]
    fu = sorted((s for s in servers if s.get("source_class") == "files"
                 and s.get("purpose") == "agent_write"
                 or (s.get("source_class") == "files"
                     and (s.get("tools_hint") or {}).get("write"))), key=lambda s: s["id"])[0]
    query = (db.get("tools_hint") or {}).get("query") or "query"
    reader = ConnectionInfo(id=db["id"], title=db.get("title", ""), kind="database",
                            tools=(classify_tool(query, read_only=True),),
                            aliases=tuple(db.get("aliases") or ()), plan_status="approved")
    writer = ConnectionInfo(id=fu["id"], title=fu.get("title", ""), kind="files",
                            tools=(ToolRef(name=fu["tools_hint"]["write"], write=True,
                                           basis="declared"),),
                            purpose="agent_write")
    return reader, writer


def _purpose(estate: str) -> str:
    return f"{estate}.one_time_test"


def _catalogue(estate: str, golden: str) -> Catalogue:
    reader, writer = estate_connections(estate)
    return Catalogue(connections=(reader, writer), kinds=KINDS, subject_kinds=("person",),
                     governance=Governance(pack=estate, purpose=_purpose(estate),
                                           golden_set=golden))


def _purposes(estate: str):
    from databridge_adapter.purposes import PurposeDecl, PurposeVocabulary
    p = _purpose(estate)
    return PurposeVocabulary({p: PurposeDecl(id=p, pack=estate, description="synthetic",
                                             subject_scoped=False)}, [estate])


@pytest.fixture(params=["workspace", "finance"])
def estate(request, tmp_path):
    rel = f"packs/{request.param}/evals/one_time.jsonl"
    golden = tmp_path / rel
    golden.parent.mkdir(parents=True, exist_ok=True)
    golden.write_text(json.dumps({"question": "synthetic", "expect": "cited"}) + "\n",
                      encoding="utf-8")
    cat = _catalogue(request.param, rel)
    reader, writer = estate_connections(request.param)
    return {"name": request.param, "cat": cat, "root": tmp_path, "reader": reader,
            "writer": writer, "purposes": _purposes(request.param)}


def _compile(e, text, **kw):
    return compile_english(text, e["cat"], purposes=e["purposes"], repo_root=e["root"], **kw)


def _write_text(e, approval: str = "") -> str:
    return (f"Find every overdue record in {e['reader'].id}. Draft a follow-up note for each "
            f"into {e['writer'].id}.{approval}")


# ------------------------------------------------------------------------------------------------
def test_a_write_in_a_one_time_task_waits_for_a_person_and_is_not_asked_about(estate):
    text = _write_text(estate)
    agent_path = _compile(estate, text)
    # the agent path asks how each write is approved — the names come from its own questions
    asked = [q.id.split(":", 1)[1] for q in agent_path.open_questions if q.id.startswith("mode:")]
    assert asked, [q.id for q in agent_path.open_questions]           # non-vacuous

    once = _compile(estate, text, run_once=True)
    assert once.open_questions == [], [q.text for q in once.open_questions]
    assert once.spec is not None and once.compilation.compiled, once.compilation.as_record()
    assert sorted(a.name for a in once.spec.actions) == sorted(asked)
    assert {a.automation_mode for a in once.spec.actions} == {"human_required"}
    decl = yaml.safe_load(once.declaration_yaml)
    assert {body["automation"]["mode"] for a in decl["actions"] for body in a.values()} ==         {"human_required"}
    for name in asked:
        d = next(d for d in once.derivations if d["field"] == f"actions[{name}].automation_mode")
        assert d["value"] == "human_required" and "one-time" in d["evidence"]
    # the same grants, purpose and golden set as the agent compiler gives
    assert decl["purpose"] == estate["cat"].governance.purpose
    assert decl["evals"]["golden_set"] == estate["cat"].governance.golden_set


def test_tell_me_after_is_noted_not_applied(estate):
    agent_path = _compile(estate, _write_text(estate, " Just tell me after it is saved."))
    modes = {a.automation_mode for a in (agent_path.spec.actions if agent_path.spec else [])}
    assert modes == {"human_notified"}, [q.text for q in agent_path.open_questions]   # the words
    once = _compile(estate, _write_text(estate, " Just tell me after it is saved."), run_once=True)
    assert {a.automation_mode for a in once.spec.actions} == {"human_required"}
    assert any("one-time" in n and "human_notified" in n for n in once.notes), once.notes


def test_a_schedule_is_a_question_not_a_dropped_word(estate):
    text = f"Every weekday at 9am list the overdue records in {estate['reader'].id}"
    agent_path = _compile(estate, text, default_timezone="UTC")
    assert agent_path.spec is not None and agent_path.spec.trigger.type == "schedule"
    once = _compile(estate, text, run_once=True, default_timezone="UTC")
    assert once.spec is None
    q = [q for q in once.open_questions if q.id == "one_time_trigger"]
    assert q and "Builder" in q[0].text, [x.id for x in once.open_questions]


def test_a_read_only_task_has_no_trigger_no_actions_and_a_one_time_id(estate):
    text = f"Which records in {estate['reader'].id} are overdue?"
    once = _compile(estate, text, run_once=True)
    assert once.open_questions == [] and once.compilation.compiled
    assert once.spec.trigger.type == "manual" and once.spec.actions == []
    assert once.spec.mode == "read_only"
    assert once.spec.agent.startswith("once_")
    assert not _compile(estate, text).spec.agent.startswith("once_")    # agents are not renamed
    # two compiles of the same words are two tasks
    assert _compile(estate, text, run_once=True).spec.agent != once.spec.agent


def test_an_unconnected_channel_gets_the_agent_compilers_honest_question(estate):
    for channel in ("Slack", "email"):
        text = f"Send a {channel} message listing every overdue record in {estate['reader'].id}"
        agent_q = {q.id for q in _compile(estate, text).open_questions
                   if not q.id.startswith("mode:")}
        once = _compile(estate, text, run_once=True)
        assert once.spec is None
        once_q = {q.id for q in once.open_questions}
        assert agent_q and agent_q <= once_q, (agent_q, once_q)        # the same refusal
        assert not any(q.startswith("mode:") for q in once_q)


def test_a_model_proposing_a_schedule_does_not_ask_when_to_run(estate):
    from databridge_harness import FakeBackend, Harness
    proposal = ModelProposal.model_validate({"kind": "agent", "title": "Overdue list",
                                             "trigger_type": "schedule",
                                             "instructions": ["List the overdue records."]})
    harness = Harness(FakeBackend(json.dumps(proposal.model_dump())), cache=None)
    text = f"List the overdue records in {estate['reader'].id}"
    agent_path = _compile(estate, text, harness=harness)
    assert any(q.id == "trigger" for q in agent_path.open_questions)   # non-vacuous
    once = _compile(estate, text, harness=harness, run_once=True)
    assert not any(q.id == "trigger" for q in once.open_questions)
    assert once.spec is not None and once.spec.trigger.type == "manual"
