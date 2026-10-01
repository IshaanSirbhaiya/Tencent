"""extractor.py — IR → graph with the grounding wall.

These tests are about the WALL, not the vocabulary: an entity name that is not in the source is
refused, a substring cannot ride inside a longer word, a relation is only emitted when its two
endpoints genuinely co-occur, and wrong model offsets are relocated rather than trusted. The
fixture text is a clinic referral because that is what this repo now processes (ADR-0009), but
every property asserted here would hold over any corpus.
"""
from __future__ import annotations

import datetime as dt

from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity
from databridge_harness import FakeBackend, Harness

from databridge_graph import GraphStore, extract_graph, gold_extraction_handler
from databridge_graph.clinic_seed import SEED_SYSTEM_TIME
from databridge_graph.packs import deployment

_UTC = dt.timezone.utc
_TEXT = ("Meridian Life Assurance refers Loh Wei Ming for examination. "
         "Please perform a Chest X-Ray and a Treadmill ECG.")


def _doc():
    snap = SnapshotIdentity(source_id="d1", as_of=dt.datetime(2026, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    return DocumentRecord(document_id="d1", sha256="x", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=_TEXT)])


def _harness(ents, rels):
    return Harness(FakeBackend(gold_extraction_handler(ents, rels)), profile="cloud")


def _at(needle: str, text: str = _TEXT) -> dict:
    start = text.find(needle)
    assert start >= 0, f"fixture drift: {needle!r} is not in the test text"
    return {"char_start": start, "char_end": start + len(needle)}


def test_extracts_grounded_entities_and_relations():
    ents = [
        {"name": "Loh Wei Ming", "kind": "patient", "canonical_id": "S8010946C",
         **_at("Loh Wei Ming")},
        {"name": "Meridian Life Assurance", "kind": "org", **_at("Meridian Life Assurance")},
    ]
    rels = [{"src_name": "Loh Wei Ming", "rel_type": "covered_by",
             "dst_name": "Meridian Life Assurance",
             "evidence_char_start": 0, "evidence_char_end": 60}]
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    res = extract_graph(_doc(), _harness(ents, rels), store=store)
    assert {n.label for n in res.entities} == {"Loh Wei Ming", "Meridian Life Assurance"}
    assert len(res.relations) == 1 and res.relations[0].kind.value == "covered_by"
    assert store.counts()["nodes"] == 2 and store.counts()["edges"] == 1


def test_fabricated_entity_name_is_refused():
    # name NOT in the source text ⇒ fabrication ⇒ refused (the grounding wall)
    ents = [{"name": "Ghost McPhantom", "kind": "patient", "char_start": 0, "char_end": 5}]
    res = extract_graph(_doc(), _harness(ents, []))
    assert res.entities == []
    assert any("not found in source" in r for r in res.refused)


def test_unknown_kind_is_skipped_never_coined():
    """The closed-vocabulary principle carried over from ADR-0008 into ADR-0009: a kind outside
    the frozen set is skipped, never invented."""
    ents = [{"name": "Loh Wei Ming", "kind": "alien_species", **_at("Loh Wei Ming")}]
    res = extract_graph(_doc(), _harness(ents, []))
    assert res.entities == []
    assert any("unknown kind" in r for r in res.refused)


def test_a_retired_vocabulary_member_is_no_longer_accepted():
    """ADR-0009 replaced the Acme vocabulary. `person` was a valid kind under ADR-0008 and must
    now be refused — otherwise the old vocabulary lives on wherever a model happens to emit it."""
    # SCOPED to healthcare, because that is the pack whose vocabulary retired `person`. The
    # government pack declares `person` as a subject kind of its own, so once the deployment binds
    # all three (2026-08-16) the bare assertion below is false for a good reason. The rule under
    # test is "a kind THIS pack retired is refused", not "the string `person` is always invalid".
    with deployment("healthcare"):
        ents = [{"name": "Loh Wei Ming", "kind": "person", **_at("Loh Wei Ming")}]
        res = extract_graph(_doc(), _harness(ents, []))
        assert res.entities == []
        assert any("unknown kind 'person'" in r for r in res.refused)


def test_relation_to_ungrounded_endpoint_is_refused():
    ents = [{"name": "Loh Wei Ming", "kind": "patient", **_at("Loh Wei Ming")}]
    rels = [{"src_name": "Loh Wei Ming", "rel_type": "covered_by", "dst_name": "Ghost",
             "evidence_char_start": 0, "evidence_char_end": 10}]
    res = extract_graph(_doc(), _harness(ents, rels))
    assert res.relations == []
    assert any("endpoint not a grounded entity" in r for r in res.refused)


def test_substring_ride_is_refused():
    # a fabricated entity whose name is only a SUBSTRING of a real word must NOT ground
    # ("man" inside "management") — the boundary-aware grounding wall.
    snap = SnapshotIdentity(source_id="d2", as_of=dt.datetime(2026, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    doc = DocumentRecord(document_id="d2", sha256="x", snapshot=snap,
                         blocks=[Block(kind="Paragraph", text="The management team met today.")])
    ents = [{"name": "man", "kind": "patient", "char_start": 4, "char_end": 7}]
    res = extract_graph(doc, _harness(ents, []))
    assert res.entities == [] and any("not found" in r for r in res.refused)


def test_relation_endpoints_must_co_occur():
    # both endpoint names are grounded entities, but the relation is only emitted if they REALLY
    # co-occur in the source — no whole-document fallback pointer.
    text = "Loh Wei Ming attended on Monday. Meridian Life Assurance was billed separately."
    snap = SnapshotIdentity(source_id="d3", as_of=dt.datetime(2026, 3, 1, tzinfo=_UTC),
                            content_hash="h", schema_hash="s")
    doc = DocumentRecord(document_id="d3", sha256="x", snapshot=snap,
                         blocks=[Block(kind="Paragraph", text=text)])
    ents = [{"name": "Loh Wei Ming", "kind": "patient", **_at("Loh Wei Ming", text)},
            {"name": "Meridian Life Assurance", "kind": "org",
             **_at("Meridian Life Assurance", text)}]
    rels = [{"src_name": "Loh Wei Ming", "rel_type": "covered_by",
             "dst_name": "Meridian Life Assurance",
             # R-017 #4: the nominated passage must itself mention both endpoints (a 0..5 span
             # used to be ignored and replaced by first occurrences — now refused; see
             # test_f5_extractor_review.py). The emitted pointer is the tightest span inside it.
             "evidence_char_start": 0, "evidence_char_end": len(text)}]
    res = extract_graph(doc, _harness(ents, rels))
    assert len(res.relations) == 1
    p = res.relations[0].sources[0]
    span = text[p.char_start:p.char_end]
    assert "Loh Wei Ming" in span and "Meridian Life Assurance" in span
    assert (p.char_start, p.char_end) != (0, len(text))   # not the whole-doc fallback


def test_offset_drift_tolerated_when_name_really_present():
    # model offsets are wrong, but the name IS in the text → grounded by fallback locate
    ents = [{"name": "Treadmill ECG", "kind": "test", "char_start": 999, "char_end": 1005}]
    res = extract_graph(_doc(), _harness(ents, []))
    assert [n.label for n in res.entities] == ["Treadmill ECG"]
    p = res.entities[0].sources[0]
    assert _TEXT[p.char_start:p.char_end] == "Treadmill ECG"   # relocated to the real span
