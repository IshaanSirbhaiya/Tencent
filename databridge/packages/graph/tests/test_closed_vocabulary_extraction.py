"""The closed vocabulary is enforced on the prose → graph path, and by the seed builder.

KNOWLEDGE_SYSTEM_PRINCIPLES §6 Batch 1 "A" / contract C4. Three properties, each asserted
against a value DERIVED from the bound vocabulary (never a written-down kind list), and each
with a control case so the refusal cannot pass by refusing everything:

1. the schema the model is shown carries an ``enum`` of exactly the bound kinds;
2. an off-ontology node kind or edge type is REFUSED with a reason, never coerced;
3. an edge whose endpoint kinds the ontology's ``src``/``dst`` do not allow is refused — by
   ``extract_graph``, and skipped + counted by the seed builder.
"""
from __future__ import annotations

import datetime as dt
import json
import textwrap
from pathlib import Path

import pytest

from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity
from databridge_graph.estate_seed import build_pack_graph
from databridge_graph.extractor import (
    doc_text, extract_graph, extraction_json_schema, gold_extraction_handler)
from databridge_graph.meta import ATTESTED_META_KINDS
from databridge_graph.packs import (
    ANY_KIND, active_vocabulary, build_vocabulary, clear_caches, deployment)
from databridge_graph.store import GraphStore
from databridge_harness import FakeBackend, Harness

T0 = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)


def _doc(text: str, *, doc_id: str = "d1", **meta) -> DocumentRecord:
    snap = SnapshotIdentity(source_id=doc_id, as_of=T0, content_hash="h", schema_hash="s")
    return DocumentRecord(document_id=doc_id, sha256="x", snapshot=snap,
                          blocks=[Block(kind="Paragraph", text=text)], source_meta=meta)


def _at(text: str, needle: str) -> dict:
    i = text.index(needle)
    return {"char_start": i, "char_end": i + len(needle)}


def _harness(ents, rels):
    backend = FakeBackend(gold_extraction_handler(ents, rels))
    return Harness(backend, profile="cloud"), backend


def _schema_sent(backend: FakeBackend) -> dict:
    """The JSON Schema the harness actually handed the backend — grammar (L3) or prompt (L1)."""
    call = backend.calls[0]
    if call.get("guided_json"):
        return call["guided_json"]
    for m in call["messages"]:
        content = m.get("content", "")
        if "JSON Schema" in content:
            return json.loads(content[content.index("{"):])
    raise AssertionError("no schema reached the backend")


def _enums(schema: dict) -> tuple[list, list]:
    defs = schema["$defs"]
    return (defs["ExtractedEntity"]["properties"]["kind"]["enum"],
            defs["ExtractedRelation"]["properties"]["rel_type"]["enum"])


def _an_edge_with_a_constrained_src(vocab):
    """(edge decl, a bound node kind its src forbids, a kind its src allows, a kind its dst
    allows) — found in the vocabulary, not named."""
    for decl in sorted(vocab.edge_kinds.values(), key=lambda d: d.name):
        if decl.binds_to in ATTESTED_META_KINDS or ANY_KIND in decl.src or not decl.src:
            continue
        forbidden = sorted(k for k in vocab.node_kinds if k not in decl.src)
        dst_ok = sorted(k for k in vocab.node_kinds if decl.allows("dst", k))
        if forbidden and dst_ok:
            return decl, forbidden[0], sorted(decl.src)[0], dst_ok[0]
    pytest.skip("no bound edge kind with a constrained src")


# --- 1. the enum is built at call time from the bound vocabulary -------------------------------
def test_the_schema_shown_to_the_model_is_exactly_the_bound_vocabulary():
    vocab = active_vocabulary()
    expected_nodes = sorted(vocab.node_kinds)
    expected_edges = sorted(n for n, d in vocab.edge_kinds.items()
                            if d.binds_to not in ATTESTED_META_KINDS)
    assert expected_nodes and expected_edges            # non-vacuous: a real vocabulary is bound

    text = "nothing to see"
    harness, backend = _harness([], [])
    res = extract_graph(_doc(text), harness)

    nodes, edges = _enums(_schema_sent(backend))
    assert nodes == expected_nodes
    assert edges == expected_edges
    assert list(res.node_kinds_offered) == expected_nodes
    assert list(res.edge_kinds_offered) == expected_edges
    # a determination kind is never OFFERED, even when the binding declares one
    offered_meta = {vocab.edge_kinds[e].binds_to for e in edges}
    assert not (offered_meta & ATTESTED_META_KINDS)


def test_the_enum_follows_the_binding_not_the_import():
    """Built per call: rebinding changes what the next call offers."""
    with deployment("healthcare"):
        a = _enums(extraction_json_schema())[0]
        assert a == sorted(active_vocabulary().node_kinds)
    with deployment("finance"):
        b = _enums(extraction_json_schema())[0]
        assert b == sorted(active_vocabulary().node_kinds)
    assert a and b and a != b


def test_a_vocabulary_override_narrows_and_never_widens():
    bound = active_vocabulary()
    narrow = build_vocabulary([bound.packs[0]])
    ev_nodes = _enums(extraction_json_schema(narrow))[0]
    assert ev_nodes == sorted(k for k in narrow.node_kinds if k in bound.node_kinds)
    assert set(ev_nodes) <= set(bound.node_kinds) and ev_nodes


# --- 2. off-ontology output is refused, never coerced --------------------------------------------
def test_an_off_ontology_node_kind_is_refused_and_an_on_ontology_one_is_kept():
    vocab = active_vocabulary()
    good_kind = sorted(vocab.node_kinds)[0]
    bad_kind = "zz_" + "_".join(sorted(vocab.node_kinds))[:40]     # derived: cannot be bound
    assert bad_kind not in vocab.node_kinds
    text = "Harbour Lantern Works signed with Quillon Ferris yesterday."
    ents = [{"name": "Harbour Lantern Works", "kind": good_kind, **_at(text, "Harbour Lantern Works")},
            {"name": "Quillon Ferris", "kind": bad_kind, **_at(text, "Quillon Ferris")}]
    harness, _ = _harness(ents, [])
    res = extract_graph(_doc(text), harness)

    assert [n.label for n in res.entities] == ["Harbour Lantern Works"]      # the control survives
    assert str(res.entities[0].kind) == good_kind
    assert any("Quillon Ferris" in r and "unknown kind" in r and "not coerced" in r
               for r in res.refused), res.refused


def test_an_off_ontology_edge_type_is_refused():
    vocab = active_vocabulary()
    decl, _, src_kind, dst_kind = _an_edge_with_a_constrained_src(vocab)
    bad_type = "zz_" + decl.name
    assert bad_type not in vocab.edge_kinds
    text = "Harbour Lantern Works appointed Quillon Ferris."
    ents = [{"name": "Harbour Lantern Works", "kind": src_kind, **_at(text, "Harbour Lantern Works")},
            {"name": "Quillon Ferris", "kind": dst_kind, **_at(text, "Quillon Ferris")}]
    rels = [{"src_name": "Harbour Lantern Works", "rel_type": bad_type, "dst_name": "Quillon Ferris",
             "evidence_char_start": 0, "evidence_char_end": len(text)}]
    harness, _ = _harness(ents, rels)
    res = extract_graph(_doc(text), harness)
    assert res.relations == []
    assert any(bad_type in r and "unknown type" in r for r in res.refused), res.refused


# --- 3. endpoint kinds are the ontology's --------------------------------------------------------
def test_an_edge_between_the_wrong_kinds_is_refused_and_the_right_kinds_pass():
    vocab = active_vocabulary()
    decl, forbidden_src, allowed_src, dst_kind = _an_edge_with_a_constrained_src(vocab)
    text = "Harbour Lantern Works appointed Quillon Ferris."

    def run(src_kind):
        ents = [{"name": "Harbour Lantern Works", "kind": src_kind,
                 **_at(text, "Harbour Lantern Works")},
                {"name": "Quillon Ferris", "kind": dst_kind, **_at(text, "Quillon Ferris")}]
        rels = [{"src_name": "Harbour Lantern Works", "rel_type": decl.name,
                 "dst_name": "Quillon Ferris",
                 "evidence_char_start": 0, "evidence_char_end": len(text)}]
        harness, _ = _harness(ents, rels)
        return extract_graph(_doc(text), harness)

    wrong = run(forbidden_src)
    assert wrong.relations == [] and len(wrong.entities) == 2
    assert any("wrong endpoint kinds" in r and forbidden_src in r for r in wrong.refused), \
        wrong.refused

    right = run(allowed_src)                                       # the control
    assert [str(e.kind) for e in right.relations] == [decl.name], right.refused


# --- pointers: resolvable, and carrying the capture digest -------------------------------------
def test_every_pointer_resolves_and_carries_the_capture_digest():
    vocab = active_vocabulary()
    kind = sorted(vocab.node_kinds)[0]
    text = "Report filed by Harbour Lantern Works on Tuesday."
    ents = [{"name": "Harbour Lantern Works", "kind": kind, **_at(text, "Harbour Lantern Works")}]

    harness, _ = _harness(ents, [])
    explicit = extract_graph(_doc(text), harness, capture_digest="sha256:abc123")
    harness, _ = _harness(ents, [])
    from_meta = extract_graph(_doc(text, capture_digest="sha256:def456"), harness)
    harness, _ = _harness(ents, [])
    absent = extract_graph(_doc(text), harness)

    for res, digest in ((explicit, "sha256:abc123"), (from_meta, "sha256:def456"), (absent, None)):
        assert res.entities, res.refused
        for node in res.entities:
            for p in node.sources:
                assert p.document_id == "d1" and p.kind == "doc_span"
                assert doc_text(_doc(text))[p.char_start:p.char_end] == node.label
                assert p.capture_digest == digest


def test_an_empty_vocabulary_makes_no_model_call(tmp_path):
    d = tmp_path / "hollow"
    d.mkdir()
    (d / "ontology.yaml").write_text(textwrap.dedent("""
        pack: hollow
        version: "1.0"
        node_kinds: []
        edge_kinds: []
    """), encoding="utf-8")
    clear_caches()
    try:
        empty = build_vocabulary(["hollow"], tmp_path)
    finally:
        clear_caches()
    harness, backend = _harness([], [])
    res = extract_graph(_doc("anything"), harness, vocabulary=empty)
    assert backend.calls == [] and res.entities == [] and res.refused


# --- the seed builder skips + counts wrong endpoint kinds --------------------------------------
_ONTOLOGY = """
pack: {pack}
version: "1.0"
node_kinds:
  - name: customer
    binds_to: entity
  - name: invoice
    binds_to: document
  - name: region
    binds_to: location
edge_kinds:
  - name: billed_to
    binds_to: party_to
    src: [invoice]
    dst: [customer]
"""

_SEED_MAP = """
pack: {pack}
fixtures_root: "data"
system_time: "2026-08-16T00:00:00+00:00"
default_valid_from: "2020-01-01T00:00:00+00:00"
datasets:
  - id: invoices
    reader: csv
    table: invoices
    trust: trusted
    row_key: "{{invoice_id}}"
    path: "invoices.csv"
    nodes:
      - kind: invoice
        key: "{{invoice_id}}"
        label: "{{invoice_id}}"
        sources: [invoice_id]
      - kind: customer
        key: "{{customer_id}}"
        label: "{{customer_id}}"
        sources: [customer_id]
      - kind: region
        key: "{{region}}"
        label: "{{region}}"
        sources: [region]
    edges:
      - kind: billed_to
        src: "invoice:{{invoice_id}}"
        dst: "customer:{{customer_id}}"
        evidence: [customer_id]
      - kind: billed_to
        src: "region:{{region}}"
        dst: "customer:{{customer_id}}"
        evidence: [region]
"""


def test_the_seed_builder_skips_and_counts_an_edge_between_the_wrong_kinds(tmp_path, monkeypatch):
    pack = "scratchjoin"
    d = tmp_path / pack
    (d / "data").mkdir(parents=True)
    (d / "ontology.yaml").write_text(_ONTOLOGY.format(pack=pack), encoding="utf-8")
    (d / "seed_map.yaml").write_text(_SEED_MAP.format(pack=pack), encoding="utf-8")
    rows = [("inv-1", "cus-1", "north"), ("inv-2", "cus-2", "south"), ("inv-3", "cus-1", "north")]
    (d / "data" / "invoices.csv").write_text(
        "invoice_id,customer_id,region\n" + "\n".join(",".join(r) for r in rows) + "\n",
        encoding="utf-8")
    monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(tmp_path))
    clear_caches()
    try:
        with deployment(pack):
            nodes, edges, report = build_pack_graph(pack, packs_dir=tmp_path)
    finally:
        clear_caches()

    # the declared join: one edge per row, derived from the rows written above
    assert report.edges_by_kind == {"billed_to": len(rows)}
    assert {e.src_id.split(":")[0] for e in edges} == {"invoice"}
    # the off-ontology join (region → customer) fired on every row and wrote nothing
    assert report.skipped_edges_wrong_endpoint_kind == {"invoices:billed_to": len(rows)}
    assert report.as_record()["skipped_edges_wrong_endpoint_kind"] == {
        "invoices:billed_to": len(rows)}
    assert not any(e.src_id.startswith("region:") for e in edges)
