"""`/brain/query` — E2E run 3 findings P-06 and P-13 (wiki/40-reviews/E2E-2026-10-01-run3.md).

* **P-06** "what is the status of invoice INV-2026-0057?" answered "No entity in the knowledge graph
  matches" although the invoice node existed: a business identifier reached neither
  ``_named_outright`` (graph node ids only) nor ``link_entities`` (which skips DOCUMENT-shaped
  kinds — an invoice is one). And a question about a file the ingestion screen BLOCKED was answered
  as if the file did not exist.
* **P-13** "How are things going with our clients?" got 422 ``UNGROUNDED_OR_NO_MATCH``; it should
  get a question back.

Every store is synthetic and built from the bound pack's own declarations (kinds chosen by
meta-kind, never named). Every expected value is read off the store or the governance record the
test wrote; nothing is a recalled answer.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import AUTH_HEADERS

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
#: An identifier-shaped record id that is in no pack and in no fixture.
REC_ID = "ZQX-4410-077"


@pytest.fixture
def vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as v:
        yield v


def _ptr(doc: str, a: int = 0):
    from databridge_core import SourcePointer
    return SourcePointer(source_id="crm", kind="doc_span", document_id=doc, char_start=a,
                         char_end=a + 1)


def _node(store, nid, kind, label, attrs=None, canonical=None):
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind

    attrs = attrs or {}
    store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, canonical_id=canonical,
                             sources=[_ptr(nid)], attrs=attrs,
                             attr_sources={k: _ptr(f"{nid}#{k}", i + 1)
                                           for i, k in enumerate(attrs)},
                             valid_from=T0, system_from=T0))


def _doc_kinds(v) -> list[str]:
    from databridge_graph.meta import NodeMetaKind
    return sorted(k for k in v.nodes_binding_to(NodeMetaKind.DOCUMENT) if not v.is_subject(k))


def _world(v):
    """A DOCUMENT-shaped record named by ``REC_ID`` (label and canonical id), a second record that
    only CARRIES the id (canonical id), and a SUBJECT whose canonical id is identifier-shaped."""
    from databridge_graph import GraphStore

    kinds = _doc_kinds(v)
    rec_kind, other_kind = kinds[-1], kinds[0]
    subj = sorted(v.subject_kinds)[0]
    store = GraphStore(clock=lambda: T0)
    _node(store, "rec:named", rec_kind, REC_ID, canonical=REC_ID,
          attrs={"status": "open", "due_on": "2026-11-30", "total": "4410.50"})
    _node(store, "rec:carrier", other_kind, f"Follow-up on {REC_ID}", canonical=REC_ID,
          attrs={"due_on": "2026-11-30"})
    _node(store, "subj:1", subj, "Odile Brennan-Vey", canonical="EMP-0042",
          attrs={"role": "Account owner"})
    return store


def _tools(store, ingest_root=None):
    from brain_server import BrainTools

    t = BrainTools(store=store)
    t.passage_search = lambda *a, **k: []
    t.ingest_root = ingest_root
    return t


def _ctx(tools, q):
    from databridge_gateway.llm import build_context
    return build_context(tools, q)


# ------------------------------------------------------------------------------------------------
# P-06 — a record named by its identifier
# ------------------------------------------------------------------------------------------------
def test_the_old_doors_do_not_reach_the_record_so_this_test_is_not_vacuous(vocab):
    """The failure as run 3 saw it: neither existing door reaches a DOCUMENT-shaped record by its
    business identifier. If either ever does, the tests below stop proving the new door."""
    from databridge_graph.query import _named_outright, link_entities

    store = _world(vocab)
    q = f"what is the status of invoice {REC_ID}?"
    assert _named_outright(store, q) is None
    assert "rec:named" not in [n.id for n in link_entities(store, q, top_k=8)]


def test_deterministic_answer_states_the_records_values_each_with_its_own_pointer(vocab):
    store = _world(vocab)
    from databridge_graph import row_to_node

    node = row_to_node(store, store.open_node_row("rec:named"))
    out = _tools(store).brain_query(f"When is {REC_ID} due, and what is its status?")
    assert out["grounded"] and out["answer_kind"] == "graph", out
    assert out["matched_entities"][0] == "rec:named"
    # every attribute the node holds WITH a pointer is printed, and carries THAT pointer
    by_attr = {v["attr"]: v for v in out["values"] if v["entity_id"] == "rec:named"}
    assert set(by_attr) == set(node.attr_sources) and by_attr
    for attr, v in by_attr.items():
        assert v["value"] == str(node.attrs[attr]) and v["value"] in out["answer"]
        assert v["pointer"]["document_id"] == node.attr_sources[attr].document_id
    assert "No entity" not in out["answer"]
    # the other record carrying the id is listed beside it, never merged into the answer
    assert "rec:carrier" in out["matched_entities"]
    assert out["record_lookup"]["answered"] == ["rec:named"]


def test_the_model_context_leads_with_the_record_and_tags_every_value(vocab):
    import re

    store = _world(vocab)
    ctx = _ctx(_tools(store), f"what is the status of invoice {REC_ID}?")
    assert ctx["refusal"] == "" and ctx["nodes_used"][0] == "rec:named", ctx
    assert "rec:carrier" in ctx["nodes_used"]
    line = next(ln for ln in ctx["context"].splitlines() if "status: open" in ln)
    tag = re.search(r"\[(C\d+)\]", line).group(1)
    cite = next(c for c in ctx["citations"] if c["fact_tag"] == tag)
    assert cite["pointers"][0]["document_id"] == "rec:named#status"


def test_a_subjects_identifier_never_selects_the_subject(vocab):
    """Hard constraint 1: the record-id door never opens onto a person."""
    from brain_server import record_id_matches

    store = _world(vocab)
    q = "what is the role of EMP-0042?"
    assert record_id_matches(store, q) == [[], []]
    out = _tools(store).brain_query(q)
    assert out.get("record_lookup") is None
    assert not out["grounded"] or "subj:1" not in out["matched_entities"][:1]


def test_a_plain_word_or_a_year_is_not_an_identifier(vocab):
    from brain_server import _identifier_tokens

    assert _identifier_tokens("how did 2026 go for open invoices?") == []
    assert _identifier_tokens(f"status of {REC_ID} and C-1002?") == [REC_ID, "C-1002"]


# ------------------------------------------------------------------------------------------------
# P-06 — an item the ingestion screen withheld is named as withheld, never as missing
# ------------------------------------------------------------------------------------------------
def _ingest_with_blocked(tmp_path, name: str, rule: str) -> tuple:
    from databridge_mcp_ingest.records import GovernanceRecord, line

    run = tmp_path / "ingest" / "files-src" / "20261001T000000Z-aaaaaa"
    run.mkdir(parents=True)
    (run / "report.json").write_text("{}", encoding="utf-8")
    rec = line(GovernanceRecord, {
        "item_id": "f" * 64, "uri": f"C:\\data\\hr\\{name}", "sha256": "e" * 64,
        "rules_source": "screen.yaml", "at": "2026-10-01T00:00:00Z", "decision": "block",
        "fired": [{"rule": rule, "kind": "decisive", "count": 1, "weight": None}],
        "rationale": f"decisive rule(s) ['{rule}'] matched", "reverse": "The owner approves it.",
        "score": None})
    allowed = line(GovernanceRecord, {**rec, "item_id": "a" * 64,
                                      "uri": "C:\\data\\hr\\Holiday_Rota.md",
                                      "decision": "allow"})
    (run / "governance.jsonl").write_text(json.dumps(rec) + "\n" + json.dumps(allowed) + "\n",
                                          encoding="utf-8")
    return tmp_path / "ingest", rec


def test_a_blocked_file_is_named_as_withheld_with_its_rule(vocab, tmp_path):
    root, rec = _ingest_with_blocked(tmp_path, "Salary_Bands_2026.md", "confidentiality_marking")
    store = _world(vocab)
    out = _tools(store, root).brain_query("What does the salary bands file say about pay?")
    assert out["answer_kind"] == "withheld", out
    [item] = out["withheld_items"]
    assert item["name"] == "Salary_Bands_2026.md" and item["status"] == "withheld"
    assert item["rules"] == [f["rule"] for f in rec["fired"]]
    assert item["reverse"] == rec["reverse"] and item["item_id"] == rec["item_id"]
    assert "withheld" in out["answer"] and item["name"] in out["answer"]
    assert "No entity" not in out["answer"] and "no matching entity" not in out["answer"]
    # an ALLOWED item is never reported as withheld
    assert _tools(store, root).brain_query("What is on the holiday rota?").get(
        "withheld_items") is None


def test_the_llm_branch_hands_a_withheld_question_to_the_deterministic_branch(vocab, tmp_path):
    root, _rec = _ingest_with_blocked(tmp_path, "Salary_Bands_2026.md", "confidentiality_marking")
    ctx = _ctx(_tools(_world(vocab), root), "What does the salary bands file say about pay?")
    assert ctx["context"] == "" and ctx["refusal"] == ""
    assert ctx["deterministic_reason"] == "withheld_or_needs_clarification"


# ------------------------------------------------------------------------------------------------
# P-13 — a vague question gets a question back
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q, vague", [
    ("How are things going with our clients?", True),
    ("What's new with our customers?", True),
    ("What is the outstanding balance for Brackenfold Logistics?", False),   # names a thing
    ("What is the capital of Australia?", False),                            # not our affairs
    ("What is the airspeed velocity of an unladen swallow?", False),
    (f"How is {REC_ID} going for us?", False),                               # names a record
])
def test_vagueness_is_decided_by_rule(q, vague):
    from brain_server import is_vague_question
    assert is_vague_question(q) is vague


def test_a_vague_question_returns_the_federated_needs_input_shape(vocab):
    store = _world(vocab)
    out = _tools(store).brain_query("How are things going with our clients?")
    assert out["answer_kind"] == "needs_clarification" and out["needs_clarification"] is True
    assert out["questions"] and out["question_items"][0]["text"] == out["questions"][0]
    # the options are the record KINDS the store holds (counted), never a subject, never a name
    from collections import Counter
    from brain_server import is_subject_kind

    want = Counter(r["kind"] for r in store.open_nodes() if not is_subject_kind(r["kind"]))
    opts = out["question_items"][0]["options"]
    assert want and len(opts) == len(want)
    for kind, n in want.items():
        assert any(o.startswith(f"{kind} ({n} record") for o in opts)
    assert not any("Odile" in o for o in opts)


class _ModelMustNotBeCalled:
    def complete_text(self, *a, **k):                       # noqa: D401
        raise AssertionError("the model was called for a question with no context")


@pytest.mark.parametrize("with_llm", [False, True])
def test_brain_query_route_answers_vague_200_and_a_no_match_still_422(vocab, with_llm):
    from fastapi.testclient import TestClient

    from databridge_gateway import create_app

    store = _world(vocab)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = _tools(store)
        c.app.state.brain_llm = _ModelMustNotBeCalled() if with_llm else None
        r = c.post("/brain/query", json={"question": "How are things going with our clients?"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["answer_kind"] == "needs_clarification" and body["question_items"]
        r = c.post("/brain/query",
                   json={"question": "What is the airspeed velocity of an unladen swallow?"})
        assert r.status_code == 422
        assert r.json()["refusal"]["code"] == "UNGROUNDED_OR_NO_MATCH"


def test_brain_query_route_answers_a_record_id_deterministically(vocab):
    from fastapi.testclient import TestClient

    from databridge_gateway import create_app

    store = _world(vocab)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = _tools(store)
        c.app.state.brain_llm = None
        r = c.post("/brain/query", json={"question": f"what is the status of {REC_ID}?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounded"] and body["matched_entities"][0] == "rec:named"
    from databridge_graph import row_to_node

    node = row_to_node(store, store.open_node_row("rec:named"))
    got = {v["attr"]: v["value"] for v in body["values"] if v["entity_id"] == "rec:named"}
    assert got == {k: str(node.attrs[k]) for k in node.attr_sources}
