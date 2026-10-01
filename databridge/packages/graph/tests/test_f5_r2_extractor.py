"""Review R-019 (Codex batch 2, track t06 round 2): one test per finding, each failing first.

Kind names are DERIVED from the bound vocabulary (finance is bound because it declares subject
kinds); every refusal test carries a control so it cannot pass by refusing everything.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from databridge_graph import merge as merge_mod
from databridge_graph.extractor import (
    MAX_EVIDENCE_CHARS, _fold, _produced_by, extract_graph, gold_extraction_handler)
from databridge_graph.merge import upsert_node
from databridge_graph.meta import ATTESTED_META_KINDS
from databridge_graph.packs import ANY_KIND, deployment
from databridge_graph.schema import GraphNode, NodeKind, node_id
from databridge_graph.store import GraphStore
from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity, SourcePointer
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


def _at(text: str, needle: str, nth: int = 0) -> dict:
    i = -1
    for _ in range(nth + 1):
        i = text.index(needle, i + 1)
    return {"char_start": i, "char_end": i + len(needle)}


def _harness(ents, rels=()):
    return Harness(FakeBackend(gold_extraction_handler(list(ents), list(rels))), profile="cloud")


def _subject(v) -> str:
    return sorted(v.subject_kinds)[0]


def _non_subject(v) -> str:
    return sorted(k for k in v.node_kinds if not v.is_subject(k))[0]


def _subject_edge(v):
    """(decl, dst kind) for an offered edge whose src admits a subject kind."""
    subject = _subject(v)
    for decl in sorted(v.edge_kinds.values(), key=lambda d: d.name):
        if decl.binds_to in ATTESTED_META_KINDS or not decl.allows("src", NodeKind(subject)):
            continue
        dst = sorted(k for k in v.node_kinds
                     if decl.allows("dst", NodeKind(k)) and not v.is_subject(k))
        if dst:
            return decl, dst[0]
    pytest.skip("no edge from a subject kind to a non-subject in the bound vocabulary")


def _registry_subject(kind: str, label: str) -> GraphNode:
    ptr = SourcePointer(source_id="registry", kind="table_cell", table="parties",
                        column="name", row_id="1")
    return GraphNode(id=node_id(NodeKind(kind), "registry-1"), kind=NodeKind(kind), label=label,
                     sources=[ptr], valid_from=T0, system_from=T0)


# --- Critical 1: a subject never reaches the fuzzy/canonical merge path ------------------------
def test_an_extracted_subject_is_never_fuzzy_merged_even_if_the_pack_guard_is_absent(
        vocab, monkeypatch):
    """The only thing that kept a subject off the fuzzy step was `never_fuzzy_merge_kinds`.
    Take that one guard away and the extractor must STILL insert a separate mention."""
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    registry = _registry_subject(subject, "Alice Smith")
    upsert_node(store, registry)
    before = store.sources_for(registry.id)
    monkeypatch.setattr(merge_mod, "never_fuzzy_merge_kinds", lambda v=None: frozenset())

    text = "Alice Smith opened an account."
    res = extract_graph(_doc(text), _harness([{"name": "Alice Smith", "kind": subject,
                                               **_at(text, "Alice Smith")}]), store=store)
    (outcome,) = res.upserts
    assert outcome.action == "inserted" and outcome.id != registry.id
    assert outcome.requires_human_selection is True
    assert store.sources_for(registry.id) == before                  # registry row untouched
    assert {c["candidate"] for c in res.subject_candidates} == {registry.id}


def test_upsert_node_never_fuzzy_merges_a_subject_kind(vocab, monkeypatch):
    """Defence in depth at the store layer: `is_subject` alone closes the fuzzy step."""
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    upsert_node(store, _registry_subject(subject, "Alice Smith"))
    monkeypatch.setattr(merge_mod, "never_fuzzy_merge_kinds", lambda v=None: frozenset())
    ptr = SourcePointer(source_id="other", kind="table_cell", table="t", column="c", row_id="9")
    other = GraphNode(id=node_id(NodeKind(subject), "other-9"), kind=NodeKind(subject),
                      label="Alice Smith", sources=[ptr], valid_from=T0, system_from=T0)
    assert upsert_node(store, other).action == "inserted"
    # control: a non-subject with an equal label still fuzzy-merges
    kind = _non_subject(vocab)
    a = GraphNode(id=node_id(NodeKind(kind), "a"), kind=NodeKind(kind), label="Coral Harbour",
                  sources=[ptr], valid_from=T0, system_from=T0)
    b = a.model_copy(update={"id": node_id(NodeKind(kind), "b")})
    upsert_node(store, a)
    assert upsert_node(store, b).matched_by == "fuzzy"


# --- Critical 2: two same-named subjects in one document stay distinct --------------------------
def test_two_same_named_subjects_in_one_document_stay_two_nodes(vocab):
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    text = ("Alex Lee (alex.one@example.test) met Alex Lee (alex.two@example.test) today.")
    ents = [{"name": "Alex Lee", "kind": subject, "canonical_id": "alex.one@example.test",
             **_at(text, "Alex Lee", 0)},
            {"name": "Alex Lee", "kind": subject, "canonical_id": "alex.two@example.test",
             **_at(text, "Alex Lee", 1)}]
    res = extract_graph(_doc(text), _harness(ents), store=store)
    assert len(res.entities) == 2 and len({n.id for n in res.entities}) == 2
    got = sorted(n.attrs["extracted_identifier"] for n in res.entities)
    assert got == ["alex.one@example.test", "alex.two@example.test"]
    for n in res.entities:                     # each identifier is cited by its own pointer
        p = n.attr_sources["extracted_identifier"]
        assert text[p.char_start:p.char_end] == n.attrs["extracted_identifier"]
    assert len(_open(store, subject, "Alex Lee")) == 2
    assert all(o.action == "inserted" for o in res.upserts)
    # neither mention is offered as a candidate for the other's OWN sighting as a merge
    assert all(c["requires_human_selection"] is True for c in res.subject_candidates)


def test_the_same_mention_listed_twice_is_still_one_node(vocab):
    subject = _subject(vocab)
    text = "Alex Lee signed."
    ent = {"name": "Alex Lee", "kind": subject, **_at(text, "Alex Lee")}
    res = extract_graph(_doc(text), _harness([ent, dict(ent)]))
    assert len(res.entities) == 1


def test_a_name_only_relation_to_two_same_named_subjects_is_ambiguous(vocab):
    decl, dst_kind = _subject_edge(vocab)
    subject = _subject(vocab)
    text = "Alex Lee joined Pallas Group. Alex Lee also joined Pallas Group."
    ents = [{"name": "Alex Lee", "kind": subject, **_at(text, "Alex Lee", 0)},
            {"name": "Alex Lee", "kind": subject, **_at(text, "Alex Lee", 1)},
            {"name": "Pallas Group", "kind": dst_kind, **_at(text, "Pallas Group")}]
    whole = [{"src_name": "Alex Lee", "rel_type": decl.name, "dst_name": "Pallas Group",
              "evidence_char_start": 0, "evidence_char_end": len(text)}]
    res = extract_graph(_doc(text), _harness(ents, whole))
    assert res.relations == [] and any("ambiguous" in r for r in res.refused), res.refused
    # control: a passage that contains exactly ONE of the two grounded mentions binds to it
    first = text.index(".") + 1
    one = [{"src_name": "Alex Lee", "rel_type": decl.name, "dst_name": "Pallas Group",
            "evidence_char_start": 0, "evidence_char_end": first}]
    res = extract_graph(_doc(text), _harness(ents, one))
    (edge,) = res.relations
    first_mention = next(n for n in res.entities
                         if n.sources[0].char_start == text.index("Alex Lee"))
    assert edge.src_id == first_mention.id


# --- High: a canonical identifier must sit in a bounded passage with its entity -----------------
def test_an_identifier_far_from_its_entity_is_not_attributed_to_it(vocab):
    kind = _non_subject(vocab)
    filler = "Nothing relevant is said here. " * (MAX_EVIDENCE_CHARS // 20)
    text = f"Ember Works opened today. {filler}Pallas Group (pallas.example) reported."
    ents = [{"name": "Ember Works", "kind": kind, "canonical_id": "pallas.example",
             **_at(text, "Ember Works")},
            {"name": "Pallas Group", "kind": kind, "canonical_id": "pallas.example",
             **_at(text, "Pallas Group")}]
    res = extract_graph(_doc(text), _harness(ents))
    by_label = {n.label: n for n in res.entities}
    assert by_label["Ember Works"].canonical_id is None
    assert any("Ember Works" in u and "pallas.example" in u for u in res.unresolved)
    assert by_label["Pallas Group"].canonical_id == "pallas.example"          # control


# --- High: identifier grounding is identifier-aware, not word-boundary ---------------------------
@pytest.mark.parametrize("text,ident", [
    ("Write to user@example.com.attacker for access.", "user@example.com"),
    ("Hosted at team.foo.example.com today.", "foo.example.com"),
    ("Mail xuser@example.com now.", "user@example.com"),
    ("See foo.example.com-mirror now.", "foo.example.com"),
])
def test_an_identifier_inside_a_longer_identifier_does_not_ground(vocab, text, ident):
    kind = _non_subject(vocab)
    name = text.split()[0]
    res = extract_graph(_doc(text), _harness([{"name": name, "kind": kind,
                                               "canonical_id": ident, **_at(text, name)}]))
    (n,) = res.entities
    assert n.canonical_id is None, text
    assert any(ident in u for u in res.unresolved)


@pytest.mark.parametrize("text,ident", [
    ("Write to user@example.com.", "user@example.com"),           # sentence-final period
    ("Hosted at (foo.example.com) today.", "foo.example.com"),
    ("Contact USER@Example.com, please.", "user@example.com"),
])
def test_a_complete_identifier_token_still_grounds(vocab, text, ident):
    kind = _non_subject(vocab)
    name = text.split()[0]
    res = extract_graph(_doc(text), _harness([{"name": name, "kind": kind,
                                               "canonical_id": ident, **_at(text, name)}]))
    (n,) = res.entities
    p = n.attr_sources["canonical_id"]
    assert _fold(text[p.char_start:p.char_end]) == _fold(ident) == _fold(n.canonical_id)


# --- Medium: identifier candidates find earlier EXTRACTED subject mentions ----------------------
def test_an_earlier_extracted_mention_with_the_same_identifier_is_a_candidate(vocab):
    subject = _subject(vocab)
    store = GraphStore(clock=lambda: T0)
    t1 = "Alice Smith (alice@example.test) opened an account."
    r1 = extract_graph(_doc(t1, "d1"), _harness([{
        "name": "Alice Smith", "kind": subject, "canonical_id": "alice@example.test",
        **_at(t1, "Alice Smith")}]), store=store)
    t2 = "A. Smith (alice@example.test) closed it."
    r2 = extract_graph(_doc(t2, "d2"), _harness([{
        "name": "A. Smith", "kind": subject, "canonical_id": "alice@example.test",
        **_at(t2, "A. Smith")}]), store=store)
    assert {(c["candidate"], c["basis"]) for c in r2.subject_candidates} == {
        (r1.entities[0].id, "same identifier")}
    assert all(c["requires_human_selection"] is True for c in r2.subject_candidates)
    assert all(o.action == "inserted" for o in r2.upserts)                  # never linked
    # control: a different identifier is not a candidate
    t3 = "Bea Smith (bea@example.test) called."
    r3 = extract_graph(_doc(t3, "d3"), _harness([{
        "name": "Bea Smith", "kind": subject, "canonical_id": "bea@example.test",
        **_at(t3, "Bea Smith")}]), store=store)
    assert r3.subject_candidates == []


# --- Medium: produced_by never invents a timestamp ----------------------------------------------
def test_produced_by_does_not_invent_a_production_time():
    event = SimpleNamespace(task_class="entity_extraction", model_used="m-1", cached=False)
    pb = _produced_by(SimpleNamespace(event=event), "sha256:x", "run-1")
    assert pb["at"] is None and "at" in pb["missing"]
    assert pb["run_id"] == "run-1" and pb["model"] == "m-1"
    # control: a real timestamp is carried through and nothing is named missing
    event.created_at = T0
    pb = _produced_by(SimpleNamespace(event=event), "sha256:x", "run-1")
    assert pb["at"] == T0.isoformat() and pb["missing"] == []


def _open(store: GraphStore, kind: str, label: str) -> list[str]:
    return sorted({r["id"] for r in store.open_nodes_of_kind(NodeKind(kind))
                   if _fold(r["label"]) == _fold(label)})
