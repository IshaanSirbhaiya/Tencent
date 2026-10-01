"""Codex f1 (first review of `brain_server.py`) — each finding pinned by a test that failed first.

Every expectation is derived: subject kinds from the bound vocabulary, ids from the store, and the
"one record by that name" case from `same_label_node_ids`, never from a written-down name.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from brain_server import BrainTools

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def _seeded() -> BrainTools:
    t = BrainTools()
    t.load_seed()
    return t


def _subject_rows(store):
    from databridge_graph.packs import active_vocabulary

    vocab = active_vocabulary()
    return [r for r in store.open_nodes() if vocab.is_subject(r["kind"])]


def _uniquely_named_subject(tools):
    """A subject node whose label no other same-kind node carries, and which a lookup by that
    label lands on — the "only one Alice" case of Codex f1 #1."""
    from databridge_graph import resolve_entity, same_label_node_ids

    for r in _subject_rows(tools.store):
        if (r["label"] and same_label_node_ids(tools.store, r["id"]) == [r["id"]]
                and resolve_entity(tools.store, r["label"]).node_id == r["id"]):
            return r
    pytest.skip("the seed holds no uniquely named subject — the check would be vacuous")


# -------------------------------------------------------------------------------- #1 --------
def test_a_name_matching_exactly_one_subject_returns_a_candidate_not_a_selection():
    tools = _seeded()
    row = _uniquely_named_subject(tools)
    for out in (tools.brain_neighbors(row["label"]), tools.brain_timeline(row["label"]),
                tools.node_detail(row["label"]), tools.brain_path(row["label"], row["id"])):
        assert out.get("error"), out
        assert out["candidates"] == [row["id"]]
        assert "will not choose" in out["error"]
    # the id — a human's selection — resolves
    assert tools.brain_neighbors(row["id"])["root"] == row["id"]
    assert tools.node_detail(row["id"])["node"]["id"] == row["id"]


def test_a_name_that_lands_on_a_non_subject_still_resolves():
    """Non-vacuity: the rule is about SUBJECTS, not about every name."""
    from databridge_graph import resolve_entity

    tools = _seeded()
    subjects = {r["id"] for r in _subject_rows(tools.store)}
    for r in tools.store.open_nodes():
        if r["id"] not in subjects and r["label"] and \
                resolve_entity(tools.store, r["label"]).node_id == r["id"]:
            assert tools.brain_neighbors(r["label"])["root"] == r["id"]
            return
    pytest.fail("no non-subject resolves by its own label — the comparison is vacuous")


def test_brain_query_by_a_unique_subject_name_is_refused_with_the_candidate():
    tools = _seeded()
    row = _uniquely_named_subject(tools)
    out = tools.brain_query(f"Who is {row['label']}?")
    assert out["grounded"] is False and out["answer"] == ""
    assert out["candidates"] == [row["id"]]
    assert out["subject_caveats"][row["id"]]
    again = tools.brain_query(f"Tell me about {row['id']}")
    assert again["grounded"] is True, again["refusal"]


# -------------------------------------------------------------------------------- #2 --------
def _claimed_pair(tools):
    """(document id, subject id) joined by an ABOUT-shaped claim edge."""
    from databridge_graph.kinds import edge_kinds_binding_to
    from databridge_graph.meta import EdgeMetaKind

    claim = {str(k) for k in edge_kinds_binding_to(EdgeMetaKind.ABOUT)}
    subjects = {r["id"] for r in _subject_rows(tools.store)}
    for e in tools.store.open_edges():
        if e["kind"] in claim and e["dst_id"] in subjects:
            return e["src_id"], e["dst_id"]
    pytest.skip("no claim edge into a subject in the seed")


def test_brain_path_caveats_every_subject_it_names():
    from brain_server import subject_node_caveat

    tools = _seeded()
    doc, subj = _claimed_pair(tools)
    out = tools.brain_path(doc, subj)
    assert out["found"] and out["steps"]
    subjects = {r["id"]: r["kind"] for r in _subject_rows(tools.store)}
    seen = 0
    for step in out["steps"]:
        if step["to"] in subjects:
            assert step["identity_caveat"] == subject_node_caveat(subjects[step["to"]])
            assert step["identity_verified"] is False
            seen += 1
        else:
            assert "identity_caveat" not in step
        assert step["to_pointers"], "a step's endpoint must carry its own pointers"
    assert seen, "the path to a subject named no subject — vacuous"
    assert subj in out["subject_caveats"] and doc not in out["subject_caveats"]


def test_brain_timeline_caveats_the_subject_root_and_points_every_event():
    tools = _seeded()
    _, subj = _claimed_pair(tools)
    out = tools.brain_timeline(subj)
    assert out["identity_verified"] is False and out["identity_caveat"]
    assert out["events"]
    for ev in out["events"]:
        assert ev["pointers"], f"timeline event with no pointer: {ev['label']}"
        assert subj in ev["subject_caveats"]


def test_answer_citations_of_a_subject_carry_the_caveat():
    from brain_server import subject_node_caveat

    tools = _seeded()
    _, subj = _claimed_pair(tools)
    out = tools.brain_query(f"Tell me about {subj}")
    assert out["grounded"], out["refusal"]
    kinds = {r["id"]: r["kind"] for r in _subject_rows(tools.store)}
    cited = [c for c in out["citations"] if c["entity_id"] in kinds]
    assert cited, "the subject asked about is not cited — vacuous"
    for c in cited:
        assert c["identity_caveat"] == subject_node_caveat(kinds[c["entity_id"]])
    assert subj in out["subject_caveats"]


def test_node_detail_connections_to_a_subject_carry_the_caveat():
    tools = _seeded()
    doc, subj = _claimed_pair(tools)
    detail = tools.node_detail(doc)
    to_subject = [c for c in detail["connections"] if c["other_id"] == subj]
    assert to_subject
    assert all(c["identity_verified"] is False and c["identity_caveat"] for c in to_subject)
    assert all(c["identity_caveat"] for c in detail["claims"])


# -------------------------------------------------------------------------------- #3 --------
def _ptr():
    from databridge_core import SourcePointer

    return SourcePointer(source_id="s", kind="doc_span", document_id="d", char_start=0,
                         char_end=4, capture_digest="cd" * 32)


def _unpointed(kind_cls, **fields):
    """A record the construction wall would refuse — built with ``model_construct``, which is how
    a row written before the wall existed comes back. The serialiser must not trust the wall."""
    return kind_cls.model_construct(**fields)


def test_the_construction_wall_already_refuses_an_unpointed_value():
    """Why the scenario is defence in depth: `GraphNode` refuses it at construction."""
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import active_vocabulary

    kind = active_vocabulary().nodes_binding_to(NodeMetaKind.ORGANISATION)[0]
    with pytest.raises(ValueError, match="no SourcePointer"):
        GraphNode(id="n:1", kind=NodeKind(kind), label="Orrin Vale", attrs={"bare": "1990"},
                  sources=[_ptr()], valid_from=T0, system_from=T0)


def test_a_value_without_its_own_pointer_is_withheld_and_named_unresolved():
    from brain_server import edge_to_dict, node_to_dict, pointer_to_dict
    from databridge_graph import GraphEdge, GraphNode
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.meta import NodeMetaKind
    from databridge_graph.packs import active_vocabulary

    vocab = active_vocabulary()
    kind = vocab.nodes_binding_to(NodeMetaKind.ORGANISATION)[0]
    node = _unpointed(GraphNode, id="n:1", kind=NodeKind(kind), label="Orrin Vale",
                      canonical_id=None,
                      attrs={"grounded_attr": "yes", "bare_attr": "1990-01-01"},
                      attr_sources={"grounded_attr": _ptr()}, sources=(_ptr(),),
                      valid_from=T0, valid_to=None, system_from=T0, system_to=None)
    n = node_to_dict(node)
    assert n["attrs"]["bare_attr"] is None and "bare_attr" in n["unresolved"]
    assert n["attrs"]["grounded_attr"] == "yes" and "grounded_attr" not in n["unresolved"]
    assert n["sources"] == [pointer_to_dict(_ptr())]
    edge = _unpointed(GraphEdge, id="e:1", src_id="n:1", dst_id="n:2",
                      kind=EdgeKind(sorted(vocab.edge_kinds)[0]), weight=1.0,
                      attrs={"score": 0.9}, attr_sources={}, sources=(_ptr(),),
                      valid_from=T0, valid_to=None, system_from=T0, system_to=None)
    e = edge_to_dict(edge)
    assert e["attrs"]["score"] is None and "score" in e["unresolved"]
    assert e["sources"] == [pointer_to_dict(_ptr())]


def test_graph_payload_carries_pointers_not_counts_alone():
    g = _seeded().graph(limit=50)
    assert g["nodes"] and g["edges"]
    assert all(n["sources"] and len(n["sources"]) == n["n_sources"] for n in g["nodes"])
    assert all(e["sources"] and len(e["sources"]) == e["n_sources"] for e in g["edges"])


# -------------------------------------------------------------------------------- #4 --------
def test_the_reader_path_never_creates_a_directory(tmp_path, monkeypatch):
    from brain_server import GRAPH_DB_ENV, ReaderStoreUnavailable, graph_db_path, open_reader_store

    target = tmp_path / "missing" / "graph.db"
    monkeypatch.setenv(GRAPH_DB_ENV, str(target))
    assert graph_db_path(create=False) == str(target.resolve())
    assert not target.parent.exists()
    with pytest.raises(ReaderStoreUnavailable):
        open_reader_store()
    assert not target.parent.exists() and not target.exists()


def test_the_reader_store_is_sqlite_read_only(tmp_path, monkeypatch):
    from brain_server import GRAPH_DB_ENV, open_reader_store, open_shared_store

    target = tmp_path / "graph.db"
    writer = open_shared_store(str(target))
    writer.close()
    before = target.read_bytes()
    monkeypatch.setenv(GRAPH_DB_ENV, str(target))
    reader = open_reader_store()
    assert reader.counts()["nodes"] == 0                  # reads work
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.conn.execute("CREATE TABLE planted (x)")
    reader.refresh()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.conn.execute("CREATE TABLE planted (x)")  # still read-only after a refresh
    reader.close()
    assert target.read_bytes() == before


def test_the_reader_process_never_loads_the_seed(monkeypatch):
    """Seeding is a write; the conftest sets the seed flag, so this is the flag being ignored."""
    import brain_reader_server
    import brain_server

    assert brain_server.demo_seed_enabled(), "the seed flag is off — this would be vacuous"
    calls = []
    monkeypatch.setattr(brain_server.BrainTools, "load_seed", lambda self: calls.append(1))
    brain_reader_server.default_reader_tools()
    assert calls == []


# -------------------------------------------------------------------------------- #7 --------
def test_the_demo_seed_is_refused_cleanly_under_a_binding_that_does_not_declare_it():
    from databridge_graph.packs import deployment

    with deployment("workspace"):
        tools = BrainTools()
        out = tools.load_seed()
        assert out["loaded"] is False and out["refusal"]
        assert tools.store.counts()["nodes"] == 0


def test_brain_server_imports_no_domain_seed_module_at_import_time():
    import ast
    import inspect

    import brain_server

    tree = ast.parse(inspect.getsource(brain_server))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = [getattr(n, "module", "") or "" for n in top] + \
            [a.name for n in top for a in n.names]
    assert not [x for x in names if "seed" in x], names
