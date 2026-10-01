"""Review R-017 (Codex, track t06): one test per finding, each failing before the fix.

Every kind name below is DERIVED from the bound vocabulary (the finance pack is bound only
because it declares subject kinds and constrained edges; nothing here names a kind literally),
and every refusal test has a control case so it cannot pass by refusing everything.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib

import pytest

from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity, SourcePointer
from databridge_graph.extractor import (
    MAX_EVIDENCE_CHARS, _fold, _prompt, extract_graph, extraction_vocabulary, gold_extraction_handler)
from databridge_graph.merge import upsert_node
from databridge_graph.meta import ATTESTED_META_KINDS
from databridge_graph.packs import ANY_KIND, deployment
from databridge_graph.schema import GraphNode, NodeKind, node_id
from databridge_graph.store import GraphStore
from databridge_harness import FakeBackend, Harness

PACK = "finance"
T0 = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def vocab():
    with deployment(PACK) as v:
        assert v.subject_kinds, "the bound pack must declare a subject kind (non-vacuous)"
        yield v


def _doc(text: str, doc_id: str = "d1") -> DocumentRecord:
    snap = SnapshotIdentity(source_id=doc_id, as_of=T0, content_hash="h", schema_hash="s")
    return DocumentRecord(document_id=doc_id, sha256="x", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=text)])


def _at(text: str, needle: str) -> dict:
    i = text.index(needle)
    return {"char_start": i, "char_end": i + len(needle)}


def _harness(ents, rels):
    backend = FakeBackend(gold_extraction_handler(ents, rels))
    return Harness(backend, profile="cloud"), backend


def _subject(v) -> str:
    return sorted(v.subject_kinds)[0]


def _non_subject(v) -> str:
    return sorted(k for k in v.node_kinds if not v.is_subject(k))[0]


def _constrained_edge(v):
    """(decl, src kind it allows, dst kind it allows, a kind its src forbids)."""
    for decl in sorted(v.edge_kinds.values(), key=lambda d: d.name):
        if decl.binds_to in ATTESTED_META_KINDS or not decl.src or ANY_KIND in decl.src:
            continue
        forbidden = sorted(k for k in v.node_kinds if not decl.allows("src", k))
        dst_ok = sorted(k for k in v.node_kinds if decl.allows("dst", k))
        if forbidden and dst_ok:
            return decl, sorted(decl.src)[0], dst_ok[0], forbidden[0]
    pytest.skip("no constrained edge in the bound vocabulary")


def _open_of_kind(store: GraphStore, kind: str, label: str) -> list[str]:
    return sorted({r["id"] for r in store.open_nodes_of_kind(NodeKind(kind))
                   if _fold(r["label"]) == _fold(label)})


# --- #1 (Critical): a subject mention is never merged into another record ---------------------
def test_two_documents_naming_the_same_subject_do_not_fuse(vocab):
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    ids, outcomes = [], []
    for doc_id, text in (("d1", "Alexis Rivera opened an account."),
                         ("d2", "Alexis Rivera closed an account.")):
        ents = [{"name": "Alexis Rivera", "kind": subject, **_at(text, "Alexis Rivera")}]
        res = extract_graph(_doc(text, doc_id), _harness(ents, [])[0], store=store)
        ids.append(res.entities[0].id)
        outcomes.extend(res.upserts)
        last = res
    assert len(set(ids)) == 2
    assert _open_of_kind(store, subject, "Alexis Rivera") == sorted(ids)   # two rows, not one
    assert all(o.action == "inserted" and o.requires_human_selection for o in outcomes)
    # ... and the earlier mention is offered to a human as a CANDIDATE, never linked.
    assert last.subject_candidates == [{"extracted": ids[1], "candidate": ids[0],
                                        "basis": "same label", "requires_human_selection": True}]


def test_a_non_subject_still_corroborates_across_documents(vocab):
    """Control: the scoping is for subjects only. A non-subject named twice is one node."""
    kind = _non_subject(vocab)
    store = GraphStore(clock=lambda: T0)
    for doc_id, text in (("d1", "Coral Harbour was mentioned."), ("d2", "Coral Harbour again.")):
        ents = [{"name": "Coral Harbour", "kind": kind, **_at(text, "Coral Harbour")}]
        extract_graph(_doc(text, doc_id), _harness(ents, [])[0], store=store)
    assert len(_open_of_kind(store, kind, "Coral Harbour")) == 1


def test_a_grounded_identifier_on_a_subject_is_a_candidate_not_a_merge(vocab):
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    reg_ptr = SourcePointer(source_id="registry", kind="table_cell", table="parties",
                            column="party_id", row_id="1")
    registry = GraphNode(id=node_id(NodeKind(subject), "p-1234"), kind=NodeKind(subject),
                         label="A. Rivera", canonical_id="P-1234",
                         attr_sources={"canonical_id": reg_ptr}, sources=[reg_ptr],
                         valid_from=T0, system_from=T0)
    upsert_node(store, registry)
    before = store.sources_for(registry.id)

    text = "Alexis Rivera (party P-1234) opened an account."
    ents = [{"name": "Alexis Rivera", "kind": subject, "canonical_id": "P-1234",
             **_at(text, "Alexis Rivera")}]
    res = extract_graph(_doc(text), _harness(ents, [])[0], store=store)
    node = res.entities[0]
    assert node.canonical_id is None and node.id != registry.id
    ptr = node.attr_sources["extracted_identifier"]
    assert text[ptr.char_start:ptr.char_end] == node.attrs["extracted_identifier"] == "P-1234"
    assert store.sources_for(registry.id) == before           # the registry row was not touched
    assert {c["candidate"]: c["basis"] for c in res.subject_candidates} == {
        registry.id: "same identifier"}


def test_re_extracting_the_same_document_is_idempotent_for_a_subject(vocab):
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    text = "Alexis Rivera opened an account."
    ents = [{"name": "Alexis Rivera", "kind": subject, **_at(text, "Alexis Rivera")}]
    for _ in range(2):
        res = extract_graph(_doc(text), _harness(ents, [])[0], store=store)
    assert len(_open_of_kind(store, subject, "Alexis Rivera")) == 1
    assert res.subject_candidates == []                     # not a candidate for itself
    assert len(store.sources_for(res.entities[0].id)) == 1  # same pointer, not one per run


# --- #2 (Critical): identity values are grounded EXACTLY, each by its own pointer --------------
def test_a_similar_but_different_name_does_not_ground(vocab):
    kind = _non_subject(vocab)
    text = "Jon Smith met the board."
    bad = [{"name": "John Smith", "kind": kind, **_at(text, "Jon Smith")}]
    res = extract_graph(_doc(text), _harness(bad, [])[0])
    assert res.entities == [] and any("fabrication" in r for r in res.refused)
    # control: case and whitespace are the only permitted normalisation
    ok = [{"name": "jon  SMITH", "kind": kind, **_at(text, "Jon Smith")}]
    res = extract_graph(_doc(text), _harness(ok, [])[0])
    assert len(res.entities) == 1
    for n in res.entities:
        p = n.sources[0]
        assert _fold(text[p.char_start:p.char_end]) == _fold(n.label)


def test_an_ungrounded_canonical_id_is_dropped_and_named(vocab):
    kind = _non_subject(vocab)
    text = "Coral Harbour signed the letter."
    ents = [{"name": "Coral Harbour", "kind": kind, "canonical_id": "coralharbour.example",
             **_at(text, "Coral Harbour")}]
    res = extract_graph(_doc(text), _harness(ents, [])[0])
    (n,) = res.entities
    assert n.canonical_id is None and "coralharbour.example" not in n.id
    assert any("coralharbour.example" in u for u in res.unresolved)
    # control: an identifier that IS written in the source is kept, with its OWN pointer
    text2 = "Coral Harbour (coralharbour.example) signed the letter."
    ents2 = [{"name": "Coral Harbour", "kind": kind, "canonical_id": "coralharbour.example",
              **_at(text2, "Coral Harbour")}]
    (n2,) = extract_graph(_doc(text2), _harness(ents2, [])[0]).entities
    p = n2.attr_sources["canonical_id"]
    assert n2.canonical_id == text2[p.char_start:p.char_end] == "coralharbour.example"


# --- #3 (High): an override can only narrow; declarations come from the binding ----------------
def test_an_override_cannot_widen_an_edges_endpoint_rule(vocab):
    decl, src_ok, dst_ok, forbidden = _constrained_edge(vocab)
    widened = dataclasses.replace(decl, src=(ANY_KIND,))
    override = dataclasses.replace(vocab, edge_kinds={**vocab.edge_kinds, decl.name: widened})
    assert extraction_vocabulary(override).edges[decl.name].src == decl.src

    text = "Ember Works deals with Pallas Group."
    def run(src_kind):
        ents = [{"name": "Ember Works", "kind": src_kind, **_at(text, "Ember Works")},
                {"name": "Pallas Group", "kind": dst_ok, **_at(text, "Pallas Group")}]
        rels = [{"src_name": "Ember Works", "rel_type": decl.name, "dst_name": "Pallas Group",
                 "evidence_char_start": 0, "evidence_char_end": len(text)}]
        return extract_graph(_doc(text), _harness(ents, rels)[0], vocabulary=override)

    refused = run(forbidden)
    assert refused.relations == []
    assert any(f"wrong endpoint kinds for '{decl.name}'" in r for r in refused.refused)
    assert len(run(src_ok).relations) == 1                                  # control


# --- #4 (High): the nominated evidence passage must itself state the relation ----------------
def _evidence_run(vocab, text, start, end):
    decl, src_ok, dst_ok, _ = _constrained_edge(vocab)
    ents = [{"name": "Ember Works", "kind": src_ok, **_at(text, "Ember Works")},
            {"name": "Pallas Group", "kind": dst_ok, **_at(text, "Pallas Group")}]
    rels = [{"src_name": "Ember Works", "rel_type": decl.name, "dst_name": "Pallas Group",
             "evidence_char_start": start, "evidence_char_end": end}]
    return extract_graph(_doc(text), _harness(ents, rels)[0])


def test_unrelated_mentions_far_apart_do_not_ground_an_edge(vocab):
    filler = "Nothing relevant is said here. " * (MAX_EVIDENCE_CHARS // 20)
    text = f"Ember Works opened today. {filler}Pallas Group reported results."
    assert len(text) > MAX_EVIDENCE_CHARS                          # non-vacuous
    for start, end, why in ((0, 25, "does not mention"),           # a passage naming one side
                            (0, len(text), "wider than one passage"),
                            (-1, 5, "not a span"), (30, 10, "not a span")):
        res = _evidence_run(vocab, text, start, end)
        assert res.relations == [], (start, end)
        assert any(why in r for r in res.refused), res.refused


def test_evidence_that_states_the_relation_grounds_the_tightest_span(vocab):
    text = "Preamble. Ember Works deals with Pallas Group. Unrelated closing line."
    start, end = 0, len(text)
    res = _evidence_run(vocab, text, start, end)
    (edge,) = res.relations
    p = edge.sources[0]
    assert start <= p.char_start < p.char_end <= end
    assert text[p.char_start:p.char_end] == "Ember Works deals with Pallas Group"


# --- #5 (High): an ambiguous endpoint name is refused, never resolved to the last one ---------
def test_a_shared_name_makes_the_relation_ambiguous(vocab):
    decl, src_ok, dst_ok, forbidden = _constrained_edge(vocab)
    text = "Jordan works with Pallas Group; Jordan is also a place."
    base = [{"name": "Jordan", "kind": src_ok, **_at(text, "Jordan")},
            {"name": "Pallas Group", "kind": dst_ok, **_at(text, "Pallas Group")}]
    rels = [{"src_name": "Jordan", "rel_type": decl.name, "dst_name": "Pallas Group",
             "evidence_char_start": 0, "evidence_char_end": len(text)}]
    assert len(extract_graph(_doc(text), _harness(base, rels)[0]).relations) == 1   # control
    second = {"name": "Jordan", "kind": forbidden, "char_start": text.rindex("Jordan"),
              "char_end": text.rindex("Jordan") + len("Jordan")}
    res = extract_graph(_doc(text), _harness(base + [second], rels)[0])
    assert len(res.entities) == 3 and res.relations == []
    assert any("ambiguous" in r for r in res.refused), res.refused


# --- §5.8: produced_by on the result and on every emitted pointer ------------------------------
def test_produced_by_is_recorded_from_the_harness_result(vocab):
    decl, src_ok, dst_ok, _ = _constrained_edge(vocab)
    text = "Ember Works deals with Pallas Group."
    ents = [{"name": "Ember Works", "kind": src_ok, **_at(text, "Ember Works")},
            {"name": "Pallas Group", "kind": dst_ok, **_at(text, "Pallas Group")}]
    rels = [{"src_name": "Ember Works", "rel_type": decl.name, "dst_name": "Pallas Group",
             "evidence_char_start": 0, "evidence_char_end": len(text)}]
    harness, backend = _harness(ents, rels)
    res = extract_graph(_doc(text), harness, run_id="run-7")
    pb = res.produced_by
    call = backend.calls[0]
    assert pb["agent"] == "extractor" and pb["task_class"] == "entity_extraction"
    assert pb["model"] == call["model"] and pb["run_id"] == "run-7" and pb["at"]
    prompt = _prompt(extraction_vocabulary())            # the exact system prompt sent
    assert any(prompt in m["content"] for m in call["messages"])     # ... and it WAS sent
    assert pb["prompt_version"] == "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()[:16]
    rows = [*res.entities, *res.relations]
    assert len(rows) == 3
    for row in rows:
        for p in row.sources:
            assert pb["model"] in p.derivation and pb["prompt_version"] in p.derivation
