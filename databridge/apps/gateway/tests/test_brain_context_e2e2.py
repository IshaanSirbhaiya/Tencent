"""`/brain/query`'s graph context: why the E2E run-2 questions failed, and the fix (N-14).

E2E run 2 asked "which city is X based in?", "who is X's head of procurement?", "how many open
invoices does X have?" and "which project do we run for Y and who is their contact?". The graph
held every answer; the context did not show it:

* a 1-hop neighbour's OWN attributes were never written — only an arrow to its name — so a
  contact's role or an invoice's status could not reach the model;
* a matched entity's attributes were cut at six, alphabetically, so the asked one could be lost;
* two NON-subject records sharing a name were refused as "people share names" (x-3), though
  constraint 1 is about people and the pair was one organisation seen by two sources.

Every store here is synthetic and built from the bound pack's own declarations (kinds are picked
by meta-kind and edge permissions, never named), and every expectation is read off it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import AUTH_HEADERS

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as v:
        yield v


def _kinds(v):
    """(organisation-like kind, a subject kind, a non-subject record kind, subject→org edge,
    record→org edge), all chosen from the pack's declarations."""
    from databridge_graph.meta import NodeMetaKind

    org = next(k for k in sorted(v.nodes_binding_to(NodeMetaKind.ORGANISATION))
               if not v.is_subject(k))
    subj = sorted(v.subject_kinds)[0]

    def edge(src, dst):
        return next((e for e, d in sorted(v.edge_kinds.items())
                     if d.allows("src", src) and d.allows("dst", dst)), None)

    s_edge = edge(subj, org)
    rec, r_edge = next((k, edge(k, org)) for k in sorted(v.node_kinds)
                       if not v.is_subject(k) and k != org and edge(k, org)
                       and k not in v.nodes_binding_to(NodeMetaKind.DOCUMENT))
    assert s_edge and r_edge, "the pack cannot link a subject and a record to an organisation"
    return org, subj, rec, s_edge, r_edge


def _ptr(doc: str, a: int = 0):
    from databridge_core import SourcePointer
    return SourcePointer(source_id="crm", kind="doc_span", document_id=doc, char_start=a,
                         char_end=a + 1)


def _node(store, nid, kind, label, attrs=None):
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind

    attrs = attrs or {}
    store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, sources=[_ptr(nid)],
                             attrs=attrs,
                             attr_sources={k: _ptr(f"{nid}#{k}", i + 1)
                                           for i, k in enumerate(attrs)},
                             valid_from=T0, system_from=T0))


def _edge(store, eid, kind, src, dst):
    from databridge_graph import GraphEdge
    from databridge_graph.kinds import EdgeKind

    store.add_edge(GraphEdge(id=eid, src_id=src, dst_id=dst, kind=EdgeKind(kind),
                             sources=[_ptr(eid)], valid_from=T0, system_from=T0))


def _world(v):
    from databridge_graph import GraphStore

    org, subj, rec, s_edge, r_edge = _kinds(v)
    store = GraphStore(clock=lambda: T0)
    # 13 attributes: the asked one ("zone") sorts LAST, so an alphabetical six would drop it
    filler = {f"a{i:02d}": f"v{i}" for i in range(12)}
    _node(store, "org:1", org, "Harbour Lantern Works", {**filler, "zone": "Northgate"})
    _node(store, "subj:1", subj, "Vesna Orlov", {"full_name": "Vesna Orlov",
                                                  "role": "Head of Procurement"})
    _node(store, "rec:1", rec, "HLW-0007", {"amount": "120.00", "b_date": "2026-01-02",
                                             "c_ref": "R-1", "d_note": "n", "e_code": "E",
                                             "f_flag": "x", "status": "open"})
    _edge(store, "e:s", s_edge, "subj:1", "org:1")
    _edge(store, "e:r", r_edge, "rec:1", "org:1")
    return store


def _ctx(store, q):
    from brain_server import BrainTools
    from databridge_gateway.llm import build_context
    return build_context(BrainTools(store=store), q)


def _line_with(ctx, text):
    return next((ln for ln in ctx["context"].splitlines() if text in ln), None)


def _tag_pointer(ctx, line):
    import re
    tag = re.search(r"\[(C\d+)\]", line).group(1)
    return next(c for c in ctx["citations"] if c["fact_tag"] == tag)


def test_a_neighbours_own_attribute_reaches_the_context_with_its_own_pointer(vocab):
    store = _world(vocab)
    ctx = _ctx(store, "who is Harbour Lantern Works' head of procurement?")
    assert ctx["refusal"] == "" and ctx["nodes_used"] == ["org:1"]
    line = _line_with(ctx, "role: Head of Procurement")
    assert line is not None, ctx["context"]
    cite = _tag_pointer(ctx, line)
    assert cite["pointers"][0]["document_id"] == "subj:1#role"          # the attribute's own
    # the subject's entry carries its identity caveat on the line after its attributes
    lines = ctx["context"].splitlines()
    at = lines.index(line)
    assert any(ln.strip().startswith("IDENTITY:") for ln in lines[at:at + 4])
    from databridge_gateway.llm import subject_labels
    assert "Vesna Orlov" in subject_labels(ctx["context"])


def test_a_records_status_beyond_six_attributes_is_written_when_asked(vocab):
    store = _world(vocab)
    ctx = _ctx(store, "which of Harbour Lantern Works' records are open?")
    line = _line_with(ctx, "status: open")
    assert line is not None, ctx["context"]
    assert _tag_pointer(ctx, line)["pointers"][0]["document_id"] == "rec:1#status"


def test_the_asked_attribute_of_a_matched_entity_survives_the_cap(vocab):
    store = _world(vocab)
    ctx = _ctx(store, "which zone is Harbour Lantern Works in?")
    line = _line_with(ctx, "zone: Northgate")
    assert line is not None, ctx["context"]
    assert _tag_pointer(ctx, line)["pointers"][0]["document_id"] == "org:1#zone"


def test_non_subject_records_sharing_a_name_are_all_listed_never_refused(vocab):
    org = _kinds(vocab)[0]
    store = _world(vocab)
    _node(store, "org:2", org, "Harbour Lantern Works")          # the document's twin
    ctx = _ctx(store, "Which project do we run for Harbour Lantern Works?")
    assert ctx["refusal"] == "" and {"org:1", "org:2"} <= set(ctx["nodes_used"])
    assert "2 separate records carry the name" in ctx["context"]
    assert "NOT merged" in ctx["context"]


def test_subjects_sharing_a_name_are_still_refused_with_candidates(vocab):
    subj = _kinds(vocab)[1]
    store = _world(vocab)
    _node(store, "subj:2", subj, "Vesna Orlov")
    ctx = _ctx(store, "Vesna Orlov")
    assert ctx["refusal"] and set(ctx["candidates"]) == {"subj:1", "subj:2"}
    assert ctx["context"] == ""


def test_identical_passages_are_sent_once():
    from databridge_gateway.llm import _dedupe_passages

    ps = [{"text": "Same  words here.", "chunk_id": "a"}, {"text": "same words here.",
                                                            "chunk_id": "b"},
          {"text": "Different.", "chunk_id": "c"}]
    assert [p["chunk_id"] for p in _dedupe_passages(ps)] == ["a", "c"]


# ------------------------------------------------------------------------------------------------
# the deterministic branch's refusal: a helpful candidate list (x-3)
# ------------------------------------------------------------------------------------------------
def test_the_deterministic_refusal_lists_separate_records_with_their_sources(vocab):
    from fastapi.testclient import TestClient

    from brain_server import BrainTools
    from databridge_gateway import create_app

    org = _kinds(vocab)[0]
    store = _world(vocab)
    _node(store, "org:2", org, "Harbour Lantern Works")
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = BrainTools(store=store)
        c.app.state.brain_llm = None
        r = c.post("/brain/query", json={"question": "Harbour Lantern Works"})
    body = r.json()
    if r.status_code == 200:
        pytest.skip("the deterministic branch resolved the name; no refusal to describe")
    ref = body["refusal"]
    assert r.status_code == 422 and ref["code"] == "AMBIGUOUS_ENTITY", body
    assert "people share names" not in ref["message"]
    assert {d["id"] for d in ref["candidate_details"]} == set(ref["candidates"])
    for d in ref["candidate_details"]:
        assert d["label"] == "Harbour Lantern Works" and d["pointers"] and d["source_ids"]
        assert d["subject"] is False
    assert body["routed_to"] == "graph"
