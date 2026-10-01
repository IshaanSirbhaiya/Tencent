"""E2E run 3, P-07 — a paraphrased delegation became a request to build a NEW adapter agent.

The run's sentence (artefacts/p3_rq.json ``a4_delegate``) names the adapter agent by a word of
what it declares ("the Larkspur adapter agent"), not by its id or title, and carries no delegation
verb before "to/via". The prepass therefore saw only "adapter agent" and compiled
``kind: adapter_agent``. It must match the reference against what the REGISTERED adapter agents
declare and ask a ``delegate:<slug>`` question offering the existing one.

The catalogue mirrors the run's (the same connection ids; the registered adapter agent's id, title
and connections from ``var/e2e-20261001T0836/adapter_agents/larkspur_files_crm_adapter.yaml``).
Expectations are derived from the catalogue; no network, no model.
"""
from __future__ import annotations

import re

import pytest

from databridge_automation import AdapterAgentInfo, compile_english
from databridge_automation.prepass import _capability_words
from test_auto2_e2e_run2 import run_catalogue

RUN3 = AdapterAgentInfo(id="larkspur_files_crm_adapter",
                        title="Larkspur files and CRM database graph adapter",
                        connections=("larkspur-files", "larkspur_db"), status="active")
#: A second registered adapter agent that declares nothing the sentence mentions.
UNRELATED = AdapterAgentInfo(id="drive_mirror", title="Shared drive in the graph",
                             connections=("shared-drive",), status="active")

#: The run's exact sentence.
A4 = ("When I ask about a customer, have the Larkspur adapter agent pull together everything both "
      "sources know about them. Read only.")


def _cat(*agents):
    return run_catalogue(adapter_agents=agents or (RUN3,))


def _ref_words(text: str) -> set[str]:
    m = re.search(r"\bthe\s+(.+?)\s+adapter\s+agent\b", text, re.I)
    return {w.lower() for w in m.group(1).split()}


def test_the_sentence_names_the_agent_only_through_what_it_declares():
    """Non-vacuous guard: the reference is NOT the id or the title, and DOES overlap what the
    registered adapter agent declares — so the match below is by capability, not by name."""
    words = _ref_words(A4)
    assert RUN3.id not in A4.lower() and RUN3.title.lower() not in A4.lower()
    assert words & _capability_words(RUN3, _cat())
    assert not (words & _capability_words(UNRELATED, _cat(RUN3, UNRELATED)))


def test_the_runs_sentence_is_a_delegate_question_not_a_new_adapter_agent():
    res = compile_english(A4, _cat(RUN3, UNRELATED))
    assert res.kind == "agent", (res.kind, [q.text for q in res.open_questions])
    assert res.spec is None
    q = next(q for q in res.open_questions if q.id.startswith("delegate:"))
    # the matching adapter agent is offered FIRST; the unrelated one is still available after it
    assert q.options[0] == RUN3.id and set(q.options) == {RUN3.id, UNRELATED.id}
    assert not any(q.id == "connections" for q in res.open_questions)


def test_answering_the_question_compiles_a_read_only_delegating_agent():
    res = compile_english(A4, _cat())
    qid = next(q.id for q in res.open_questions if q.id.startswith("delegate:"))
    res2 = compile_english(A4, _cat(), answers={qid: RUN3.id})
    assert res2.kind == "agent" and res2.spec is not None, [q.text for q in res2.open_questions]
    assert list(res2.spec.delegates) == [RUN3.id]
    assert f"delegate.ask:{RUN3.id}" in res2.spec.tools
    assert res2.spec.mode == "read_only" and not res2.spec.actions
    assert set(RUN3.connections) <= set(res2.spec.sources)
    d = next(d for d in res2.derivations if d["field"] == f"delegates[{RUN3.id}]")
    assert d["by"] == "answer"


def test_none_reads_the_sources_directly():
    res = compile_english(A4, _cat())
    qid = next(q.id for q in res.open_questions if q.id.startswith("delegate:"))
    res2 = compile_english(A4, _cat(), answers={qid: "none"})
    assert res2.kind == "agent"
    assert res2.spec is None or not res2.spec.delegates


@pytest.mark.parametrize("text", [
    # a definite reference that matches nothing any registered adapter agent declares
    "Have the Quillmoor adapter agent keep the shared drive in the graph; check hourly.",
    # a request to CREATE one, even when it names a declared word
    "Create the Larkspur adapter agent that keeps larkspur-files in the graph; check hourly.",
])
def test_a_reference_to_build_one_or_matching_nothing_is_untouched(text):
    res = compile_english(text, _cat())
    assert not any(q.id.startswith("delegate:") for q in res.open_questions)
    assert res.kind == "adapter_agent"
