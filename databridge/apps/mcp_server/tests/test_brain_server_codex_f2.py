"""Codex f2 (second and final review of `brain_server.py`) — each finding pinned by a test.

Expectations are derived from the bound vocabulary, the store and the packs on disk; names in the
synthetic stores are invented and carry no meaning.
"""
from __future__ import annotations

import datetime as dt

import pytest

from brain_server import BrainTools

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def _seeded() -> BrainTools:
    t = BrainTools()
    t.load_seed()
    return t


def _subject_ids(store) -> set[str]:
    from databridge_graph.packs import active_vocabulary

    v = active_vocabulary()
    return {r["id"] for r in store.open_nodes() if v.is_subject(r["kind"])}


def _walk(obj):
    """Every dict nested anywhere in ``obj``."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)


def _ptr(n: int = 4):
    from databridge_core import SourcePointer

    return SourcePointer(source_id="s", kind="doc_span", document_id="d", char_start=0,
                         char_end=n, capture_digest="cd" * 32)


# ------------------------------------------------------------------------- #1 --------------
def test_a_subject_read_by_id_is_served_as_a_candidate_never_as_selected():
    """Codex f2 #1: an agent gets candidate ids back from a name, then sends one straight back.
    The second call reads THAT RECORD — and every subject reference it returns says the record is
    a candidate that no human selected. Nothing claims a selection."""
    from databridge_graph import resolve_entity, same_label_node_ids

    tools = _seeded()
    subjects = _subject_ids(tools.store)
    row = next((r for r in tools.store.open_nodes() if r["id"] in subjects and r["label"]
                and same_label_node_ids(tools.store, r["id"]) == [r["id"]]
                and resolve_entity(tools.store, r["label"]).node_id == r["id"]), None)
    if row is None:
        pytest.skip("no uniquely named subject — vacuous")
    first = tools.brain_neighbors(row["label"])
    assert first["candidates"] == [row["id"]]
    # the refusal no longer tells the caller that sending the id back is a selection
    assert "candidate only" in first["error"] and "human selects" in first["error"]

    payloads = [tools.brain_neighbors(row["id"]), tools.node_detail(row["id"]),
                tools.brain_timeline(row["id"]), tools.brain_query(f"Tell me about {row['id']}")]
    refs = [d for p in payloads for d in _walk(p) if "identity_caveat" in d]
    assert refs, "no subject reference was served — vacuous"
    for d in refs:
        assert d["requires_human_selection"] is True and d["selected_by_human"] is False, d
        assert "does not select" in d["identity_caveat"]
    q = payloads[-1]
    assert q["addressed_record"]["id"] == row["id"]
    assert q["addressed_record"]["requires_human_selection"] is True
    # nothing in any payload says a human selected this record
    assert not [d for p in payloads for d in _walk(p) if d.get("selected_by_human") is True]


# ------------------------------------------------------------------------- #3 --------------
def _two_entity_store():
    """A non-subject whose name ranks first and a SUBJECT sharing one of its words, ranked second
    — the [non-subject, subject] shape Codex f2 #3 names."""
    from databridge_graph import GraphNode, GraphStore
    from databridge_graph.kinds import NodeKind
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import active_vocabulary

    v = active_vocabulary()
    subject = sorted(v.subject_kinds)[0]
    other = next(k for k in v.nodes_binding_to(NodeMetaKind.ORGANISATION) if not v.is_subject(k))
    store = GraphStore(clock=lambda: T0)
    rows = [("o:1", other, "Harbour Lantern Works"), ("s:1", subject, "Lantern Okafor"),
            ("o:2", other, "Marrowgate Analytics"), ("o:3", other, "Tessellate Foundry"),
            ("o:4", other, "Quillfeather Logistics"), ("o:5", other, "Brackenridge Mills")]
    for nid, kind, label in rows:
        store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, sources=[_ptr()],
                                 valid_from=T0, system_from=T0))
    return store


def test_a_subject_ranked_below_the_primary_is_never_presented_as_matched():
    from databridge_graph import nl_query

    store = _two_entity_store()
    q = "Harbour Lantern Works"
    a = nl_query(store, q)
    assert a.grounded and a.matched_entities[0] == "o:1" and "s:1" in a.matched_entities, \
        a.matched_entities                                                 # non-vacuous
    out = BrainTools(store=store).brain_query(q)
    assert "s:1" not in out["matched_entities"]
    assert out["subject_candidates"] == ["s:1"] and out["subject_caveats"]["s:1"]
    assert out["grounded"] is True                   # the answer is about the organisation


def test_a_subject_whose_value_the_answer_prints_forces_a_refusal(monkeypatch):
    """Every entity a printed VALUE belongs to is screened too: if one is a subject reached by
    name, the answer is withheld and the subject comes back as a candidate."""
    import brain_server
    from databridge_graph import nl_query
    from databridge_graph.query import AnswerValue

    store = _two_entity_store()
    real = nl_query(store, "Harbour Lantern Works")

    def fake(_store, question):
        real.values.append(AnswerValue(entity_id="s:1", attr="x", value="v", pointer=_ptr()))
        return real

    monkeypatch.setattr(brain_server, "nl_query", fake)
    out = BrainTools(store=store).brain_query("Harbour Lantern Works")
    assert out["grounded"] is False and out["answer"] == ""
    assert "s:1" in out["candidates"]


# ------------------------------------------------------------------------- #5 --------------
def test_node_detail_labels_carry_the_referenced_nodes_own_pointers():
    """`other_label` and a claim's `label` are values; each travels with the pointers of the node
    it names (its entity pointers), never the edge's."""
    from brain_server import pointer_to_dict
    from databridge_graph import row_to_node
    from databridge_graph.kinds import edge_kinds_binding_to
    from databridge_graph.meta import EdgeMetaKind

    tools = _seeded()
    claim = {str(k) for k in edge_kinds_binding_to(EdgeMetaKind.ABOUT)}
    subjects = _subject_ids(tools.store)
    doc = next((e["src_id"] for e in tools.store.open_edges()
                if e["kind"] in claim and e["dst_id"] in subjects), None)
    if doc is None:
        pytest.skip("no claim edge into a subject — vacuous")
    detail = tools.node_detail(doc)
    assert detail["connections"] and detail["claims"]
    for c in detail["connections"]:
        other = row_to_node(tools.store, tools.store.open_node_row(c["other_id"]))
        assert c["other_label"] == other.label
        assert c["other_label_pointers"] == [pointer_to_dict(p) for p in other.sources]
    for c in detail["claims"]:
        other = row_to_node(tools.store, tools.store.open_node_row(c["id"]))
        assert c["label_pointers"] == [pointer_to_dict(p) for p in other.sources]


def test_a_referenced_label_with_no_pointer_of_its_own_is_withheld(monkeypatch):
    """A node that comes back with no entity pointers (a row written before the wall) has no
    grounding for its label: the label is null and named unresolved."""
    import brain_server

    tools = _seeded()
    subjects = _subject_ids(tools.store)
    conn = next((e for e in tools.store.open_edges()
                 if (e["src_id"] in subjects) != (e["dst_id"] in subjects)), None)
    if conn is None:
        pytest.skip("no edge between a subject and a non-subject — vacuous")
    sid = conn["src_id"] if conn["src_id"] in subjects else conn["dst_id"]
    anchor = conn["src_id"] if conn["dst_id"] == sid else conn["dst_id"]
    real = brain_server.row_to_node

    def stripped(store, row):
        n = real(store, row)
        return n.model_copy(update={"sources": ()}) if n.id == sid else n

    monkeypatch.setattr(brain_server, "row_to_node", stripped)
    detail = tools.node_detail(anchor)
    hit = [c for c in detail["connections"] if c["other_id"] == sid]
    assert hit and all(c["other_label"] is None and "other_label" in c["unresolved"]
                       for c in hit)


def test_the_incomplete_document_insight_carries_the_counts_own_pointer():
    from brain_server import pointer_to_dict
    from databridge_graph import row_to_node

    tools = _seeded()
    items = [i for i in tools.insights()["insights"] if "fields not stated" in i["text"]]
    if not items:
        pytest.skip("no document with unresolved fields in the seed — vacuous")
    item = items[0]
    node = row_to_node(tools.store, tools.store.open_node_row(item["focus"]))
    ptr = pointer_to_dict(node.source_for("unresolved_count"))
    assert item["values"]["unresolved_count"] == {"value": node.attrs["unresolved_count"],
                                                  "pointer": ptr}
    assert ptr in item["citations"]
    assert str(node.attrs["unresolved_count"]) in item["text"]


# ------------------------------------------------------------------------- #6 --------------
def test_the_brain_surface_names_no_seed_and_no_fixture():
    """The seed and its demo source come from the enabled packs' `demo_seed.yaml`."""
    import inspect

    import brain_server
    import brain_writer_server

    for mod in (brain_server, brain_writer_server):
        src = inspect.getsource(mod)
        for needle in ("databridge_graph.clinic_seed", "load_clinic_seed", "FIXTURE_TEXT"):
            assert needle not in src, (mod.__name__, needle)
    assert not hasattr(brain_server.BrainTools, "DEMO_SEED")


def test_the_seed_comes_from_the_enabled_packs_declaration():
    from databridge_graph.pack_seed import declared_demo_seeds

    seeds = declared_demo_seeds()
    if not seeds:
        pytest.skip("the default binding enables no pack that declares a seed")
    tools = BrainTools()
    out = tools.load_seed()
    assert out["loaded"] is True and out["seeded_packs"] == [s.pack for s in seeds]
    assert tools.store.counts()["nodes"] > 0


def test_the_writer_takes_its_demo_sources_from_the_packs(monkeypatch, tmp_path):
    import brain_writer_server
    from brain_server import demo_seed_sources

    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("DATABRIDGE_FORCE_GOLD", "1")
    declared = demo_seed_sources()
    if not declared:
        pytest.skip("no enabled pack declares a demo source — vacuous")
    tools = brain_writer_server.default_writer_tools()
    assert tools.sources == declared


# ------------------------------------------------------------------------- #7 --------------
def test_an_ingestion_result_reports_the_extraction_route(monkeypatch, tmp_path):
    """The writer used to discard the routing metadata; the route now travels with the result."""
    import brain_writer_server
    from brain_server import demo_seed_sources

    monkeypatch.setenv("WALNUT_GRAPH_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("DATABRIDGE_FORCE_GOLD", "1")
    tools = brain_writer_server.default_writer_tools()
    assert tools.extraction_route["backend"] == "gold"
    name = next(iter(demo_seed_sources()), None)
    if name is None:
        pytest.skip("no registered demo source to ingest")
    out = tools.brain_ingest(name)
    assert out["extraction_route"] == tools.extraction_route
