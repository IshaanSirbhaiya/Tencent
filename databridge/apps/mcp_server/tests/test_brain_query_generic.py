"""`/brain/query`'s shared implementation over a NON-clinic binding, with passages.

`nl_query` used to branch on healthcare kind names (`NodeKind.PATIENT`, `.ORG`, `.PACKAGE`), which
raise AttributeError under any binding that does not declare them — so under
`DATABRIDGE_PACKS=workspace` every question was a 500. These tests bind `workspace`, build a
small store, and check that answers are assembled from meta-kinds, cite every printed value, keep
the identity caveat on the pack's SUBJECT kind, and carry hybrid-retrieval passages with pointers.
"""
from __future__ import annotations

import datetime as dt

import pytest

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
SRC = "larkspur-files"


def _ptr(start=0, end=4):
    from databridge_core import SourcePointer

    return SourcePointer(source_id=SRC, kind="doc_span", document_id="d1",
                         char_start=start, char_end=end, capture_digest="cd" * 32)


def _kind_binding(vocab, meta: str, *, subject: bool):
    return next(k for k, d in sorted(vocab.node_kinds.items())
                if d.binds_to.value == meta and vocab.is_subject(k) is subject)


@pytest.fixture()
def world():
    from databridge_graph import GraphEdge, GraphNode, GraphStore
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        s = GraphStore()
        org = _kind_binding(vocab, "organisation", subject=False)
        person = next(iter(vocab.subject_kinds))
        doc = _kind_binding(vocab, "document", subject=False)
        s.add_node(GraphNode(id="org:q", kind=NodeKind(org), label="Quillfeather Logistics",
                             attrs={"domain": "quillfeather.test", "notes": None},
                             attr_sources={"domain": _ptr(5, 22)}, sources=[_ptr()],
                             valid_from=T0, system_from=T0))
        s.add_node(GraphNode(id="person:a", kind=NodeKind(person), label="Imogen Farrow",
                             attrs={"title": "Head of Finance",
                                    **{i: "hidden@x.test" for i in
                                       vocab.identifiers_for(person)}},
                             attr_sources={"title": _ptr(30, 45),
                                           **{i: _ptr(50, 60) for i in
                                              vocab.identifiers_for(person)}},
                             sources=[_ptr()], valid_from=T0, system_from=T0))
        s.add_node(GraphNode(id="doc:1", kind=NodeKind(doc), label="msa-2026.pdf",
                             sources=[_ptr()], valid_from=T0, system_from=T0))
        for i, name in enumerate(("Marrowgate Analytics", "Tessellate Foundry")):
            s.add_node(GraphNode(id=f"org:o{i}", kind=NodeKind(org), label=name,
                                 sources=[_ptr()], valid_from=T0, system_from=T0))
        authored = next(k for k, d in vocab.edge_kinds.items() if d.binds_to.value == "issued"
                        and d.allows("src", org) and d.allows("dst", doc)) \
            if any(d.binds_to.value == "issued" and d.allows("src", org) and d.allows("dst", doc)
                   for d in vocab.edge_kinds.values()) else None
        if authored:
            s.add_edge(GraphEdge(id="e1", src_id="org:q", dst_id="doc:1",
                                 kind=EdgeKind(authored), sources=[_ptr()],
                                 valid_from=T0, system_from=T0))
        yield s, vocab, person


def test_an_answer_under_a_non_clinic_binding_is_grounded_and_cites_its_values(world):
    from databridge_graph import nl_query

    store, _, _ = world
    a = nl_query(store, "Tell me about Quillfeather Logistics")
    assert a.grounded, a.refusal
    assert a.answer.startswith("Quillfeather Logistics")
    printed = {v.attr: v for v in a.values}
    assert printed["domain"].value in a.answer
    assert printed["domain"].pointer.char_start == 5
    assert "org:q.notes" in a.unresolved, "an attribute with no value is named, never printed"


def test_a_subject_keeps_the_identity_caveat_and_never_prints_its_identifiers(world):
    from databridge_graph import nl_query
    from databridge_graph.identity import identity_caveat

    store, vocab, person = world
    a = nl_query(store, "Who is Imogen Farrow?")
    assert a.grounded, a.refusal
    assert identity_caveat(claimed=False) in a.answer
    identifiers = vocab.identifiers_for(person)
    assert identifiers, "the pack declares no identifiers — the check below is vacuous"
    for ident in identifiers:
        assert ident not in {v.attr for v in a.values}
    assert "hidden@x.test" not in a.answer


def test_brain_query_carries_passages_each_with_a_pointer(world):
    from brain_server import BrainTools

    store, _, _ = world

    class P:
        chunk_id, doc_id, source_id, locator, approx_locator, score = "c1", "d1", SRC, "p.2", \
            True, 0.4

        def __init__(self, text):
            self.text, self.pointer = text, _ptr(0, len(text))

    tools = BrainTools(store)
    tools.passage_search = lambda q, **kw: [P("Quillfeather Logistics pays on net-30 terms.")]
    out = tools.brain_query("Tell me about Quillfeather Logistics")
    assert out["grounded"] and out["answer_kind"] == "graph"
    assert out["passages"] and all(p["pointer"]["source_id"] == SRC for p in out["passages"])
    assert out["passages"][0]["lexical_match"] is True


def test_no_entity_but_a_matching_passage_answers_with_quotes_only(world):
    from brain_server import BrainTools

    store, _, _ = world
    quote = "Invoices are payable within thirty days of receipt."

    class P:
        chunk_id, doc_id, source_id, locator, approx_locator, score = "c9", "d9", SRC, "p.1", \
            False, 0.9

        def __init__(self):
            self.text, self.pointer = quote, _ptr(0, len(quote))

    tools = BrainTools(store)
    tools.passage_search = lambda q, **kw: [P()]
    out = tools.brain_query("When are invoices payable?")
    assert out["grounded"] and out["answer_kind"] == "passages"
    assert quote in out["answer"], "the answer is the verbatim extract, not a paraphrase"
    assert out["citations"] and all(c["pointers"] for c in out["citations"])


def test_no_entity_and_no_relevant_passage_is_still_refused(world):
    from brain_server import BrainTools

    store, _, _ = world

    class P:
        chunk_id, doc_id, source_id, locator, approx_locator, score = "c9", "d9", SRC, "p.1", \
            False, 0.1

        def __init__(self):
            self.text, self.pointer = "Unrelated text.", _ptr(0, 15)

    tools = BrainTools(store)
    tools.passage_search = lambda q, **kw: [P()]
    out = tools.brain_query("What is the meaning of life?")
    assert out["grounded"] is False and out["answer"] == ""
