"""extractor.py — IR → graph with the grounding wall (T08 Part 1, 04_PRD §5.3)."""
from __future__ import annotations

import datetime as dt

from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity
from databridge_harness import FakeBackend, Harness

from databridge_graph import GraphStore, extract_graph, gold_extraction_handler
from databridge_graph.demo_seed import SEED_SYSTEM_TIME

_UTC = dt.timezone.utc
_TEXT = "Sarah Chen is the CEO of Acme Corp. She works with James Miller."


def _doc():
    snap = SnapshotIdentity(source_id="d1", as_of=dt.datetime(2024, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    return DocumentRecord(document_id="d1", sha256="x", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=_TEXT)])


def _harness(ents, rels):
    return Harness(FakeBackend(gold_extraction_handler(ents, rels)), profile="cloud")


def test_extracts_grounded_entities_and_relations():
    ents = [
        {"name": "Sarah Chen", "kind": "person", "char_start": 0, "char_end": 10,
         "canonical_id": "sarah@acmecorp.com"},
        {"name": "Acme Corp", "kind": "org", "char_start": _TEXT.find("Acme Corp"),
         "char_end": _TEXT.find("Acme Corp") + 9},
    ]
    rels = [{"src_name": "Sarah Chen", "rel_type": "works_at", "dst_name": "Acme Corp",
             "evidence_char_start": 0, "evidence_char_end": 34}]
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    res = extract_graph(_doc(), _harness(ents, rels), store=store)
    assert {n.label for n in res.entities} == {"Sarah Chen", "Acme Corp"}
    assert len(res.relations) == 1 and res.relations[0].kind.value == "works_at"
    assert store.counts()["nodes"] == 2 and store.counts()["edges"] == 1


def test_fabricated_entity_name_is_refused():
    # name NOT in the source text ⇒ fabrication ⇒ refused (the grounding wall)
    ents = [{"name": "Ghost McPhantom", "kind": "person", "char_start": 0, "char_end": 5}]
    res = extract_graph(_doc(), _harness(ents, []))
    assert res.entities == []
    assert any("not found in source" in r for r in res.refused)


def test_unknown_kind_is_skipped_never_coined():
    ents = [{"name": "Sarah Chen", "kind": "alien_species", "char_start": 0, "char_end": 10}]
    res = extract_graph(_doc(), _harness(ents, []))
    assert res.entities == []
    assert any("unknown kind" in r for r in res.refused)


def test_relation_to_ungrounded_endpoint_is_refused():
    ents = [{"name": "Sarah Chen", "kind": "person", "char_start": 0, "char_end": 10}]
    rels = [{"src_name": "Sarah Chen", "rel_type": "works_at", "dst_name": "Ghost",
             "evidence_char_start": 0, "evidence_char_end": 10}]
    res = extract_graph(_doc(), _harness(ents, rels))
    assert res.relations == []
    assert any("endpoint not a grounded entity" in r for r in res.refused)


def test_substring_ride_is_refused():
    # a fabricated entity whose name is only a SUBSTRING of a real word must NOT ground
    # ("man" inside "management") — the boundary-aware grounding wall (review BLOCKER 1).
    snap = SnapshotIdentity(source_id="d2", as_of=dt.datetime(2024, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    doc = DocumentRecord(document_id="d2", sha256="x", snapshot=snap,
                         blocks=[Block(kind="Paragraph", text="The management team met today.")])
    ents = [{"name": "man", "kind": "person", "char_start": 4, "char_end": 7}]
    res = extract_graph(doc, _harness(ents, []), )
    assert res.entities == [] and any("not found" in r for r in res.refused)


def test_relation_endpoints_must_co_occur():
    # both endpoint names are grounded entities, but the relation is only emitted if they REALLY
    # co-occur in the source — no whole-document fallback pointer (review BLOCKER 2).
    text = "Sarah Chen is the CEO. James Miller is the CTO."
    snap = SnapshotIdentity(source_id="d3", as_of=dt.datetime(2024, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    doc = DocumentRecord(document_id="d3", sha256="x", snapshot=snap,
                         blocks=[Block(kind="Paragraph", text=text)])
    ents = [{"name": "Sarah Chen", "kind": "person", "char_start": 0, "char_end": 10},
            {"name": "James Miller", "kind": "person", "char_start": text.find("James"),
             "char_end": text.find("James") + 12}]
    # a works_at between two PEOPLE who do co-occur in text → grounded to the covering span
    rels = [{"src_name": "Sarah Chen", "rel_type": "mentioned", "dst_name": "James Miller",
             "evidence_char_start": 0, "evidence_char_end": 5}]
    res = extract_graph(doc, _harness(ents, rels))
    assert len(res.relations) == 1
    p = res.relations[0].sources[0]
    assert "Sarah Chen" in text[p.char_start:p.char_end] and "James Miller" in text[p.char_start:p.char_end]
    assert (p.char_start, p.char_end) != (0, len(text))   # not the whole-doc fallback


def test_offset_drift_tolerated_when_name_really_present():
    # model offsets are wrong, but the name IS in the text → grounded by fallback locate
    ents = [{"name": "James Miller", "kind": "person", "char_start": 999, "char_end": 1005}]
    res = extract_graph(_doc(), _harness(ents, []))
    assert [n.label for n in res.entities] == ["James Miller"]
    p = res.entities[0].sources[0]
    assert _TEXT[p.char_start:p.char_end] == "James Miller"   # relocated to the real span
