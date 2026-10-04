"""IDN (E2E run 5 W-02): `merge.reconcile_keyless` — a join must not depend on which source was read
first, and must never become a way to merge what write time would not.

The names are the workspace fixture's own customers (`fixtures/workspace_demo/db`) and finance's
own CRM organisations; each case is the run-5 shape: a document's keyless mention written BEFORE
the keyed register row it names.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from databridge_core import SourcePointer
from databridge_graph.packs import clear_caches, deployment

ROOT = Path(__file__).resolve().parents[3] / "fixtures"
NOW = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)


def _customers() -> list[dict]:
    sql = (ROOT / "workspace_demo" / "db" / "01_schema_and_data.sql").read_text(encoding="utf-8")
    cols_s, body = re.search(r"INSERT INTO customers \(([^)]*)\) VALUES(.*?);", sql, re.S).groups()
    cols = [c.strip() for c in cols_s.split(",")]
    rows = [dict(zip(cols, re.findall(r"'([^']*)'", t))) for t in re.findall(r"\(([^()]*)\)", body)]
    assert len(rows) >= 3
    return rows


def _org_names_finance() -> list[str]:
    sql = (ROOT / "packs" / "finance" / "crm" / "seed" / "02_account_org_01.sql").read_text(
        encoding="utf-8")
    return re.findall(r"^\s*\('[^']*', '([^']*)'", sql, re.M)


@pytest.fixture
def ws(tmp_path):
    from databridge_graph.store import GraphStore

    clear_caches()
    with deployment("workspace") as vocab:
        yield GraphStore(str(tmp_path / "g.db")), vocab
    clear_caches()


def _row(kind, rid, label, key=None, *, table="public.customers"):
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode

    cell = SourcePointer(source_id="db", kind="table_cell", table=table, column="name",
                         row_id=rid)
    keyp = SourcePointer(source_id="db", kind="table_cell", table=table, column="id",
                         row_id=rid)
    return GraphNode(id=f"{kind}:db-{rid.lower()}", kind=NodeKind(kind), label=label,
                     canonical_id=key, attrs={"name": label}, attr_sources={
                         "name": cell, **({"canonical_id": keyp} if key else {})},
                     sources=(SourcePointer(source_id="db", kind="derived", derived_from=[keyp],
                                            derivation="row key"),),
                     valid_from=NOW, system_from=NOW)


def _mention(kind, label, doc="doc-1", at=0):
    from databridge_graph.kinds import NodeKind
    from databridge_graph.schema import GraphNode

    p = SourcePointer(source_id="files", kind="doc_span", document_id=doc, char_start=at,
                      char_end=at + len(label))
    return GraphNode(id=f"{kind}:{label.lower()}", kind=NodeKind(kind), label=label,
                     sources=(p,), valid_from=NOW, system_from=NOW)


def test_a_mention_read_before_its_keyed_record_joins_it_afterwards(ws):
    from databridge_graph.merge import reconcile_keyless, upsert_edge, upsert_node
    from databridge_graph.schema import GraphEdge
    from databridge_graph.kinds import EdgeKind, NodeKind

    store, vocab = ws
    cust = _customers()
    doc = _mention("document", "notes.md")
    upsert_node(store, doc, vocab)
    mentions = []
    for c in cust:                          # the documents first: full names and first words
        for surface in (c["name"], c["name"].split()[0]):
            m = _mention("organisation", surface, at=len(mentions) * 50)
            upsert_node(store, m, vocab)
            upsert_edge(store, GraphEdge(id=f"{doc.id}|mentions|{m.id}", src_id=doc.id,
                                         dst_id=m.id, kind=EdgeKind("mentions"),
                                         sources=m.sources, valid_from=NOW, system_from=NOW))
            mentions.append((m, c))
    for c in cust:                          # then the register, written id-only like a row
        upsert_node(store, _row("organisation", c["customer_id"], c["name"], c["customer_id"]),
                    vocab, id_only=True)
    rep = reconcile_keyless(store, vocab)
    for m, c in mentions:
        open_ = store.open_node_row(m.id)
        assert open_ is None, m.id                                  # absorbed, not left beside
        survivor = [r for r in store.open_nodes() if r["canonical_id"] == c["customer_id"]]
        assert len(survivor) == 1 and survivor[0]["label"] == c["name"]
        targets = {e["dst_id"] for e in store.open_edges() if e["src_id"] == doc.id}
        assert survivor[0]["id"] in targets                         # its edges re-pointed
    assert sum(len(x["absorbed"]) for x in rep.merged) == len(mentions)
    # a surface form that is not the record's name is cited as a derivation over its own span,
    # never as a bare span beside a label it does not hold (E2E run-2 N-17)
    from databridge_graph.store import row_to_node
    for m, c in mentions:
        node = row_to_node(store, [r for r in store.open_nodes()
                                   if r["canonical_id"] == c["customer_id"]][0])
        span = m.sources[0]
        if m.label == c["name"]:
            assert span in node.sources
        else:
            assert span not in node.sources
            assert any(p.kind == "derived" and span in p.derived_from
                       and repr(m.label) in p.derivation for p in node.sources), m.label
    # a later sighting of an absorbed mention re-finds the record (not a fresh keyless copy)
    from databridge_graph.merge import resolve_existing
    m, c = mentions[0]
    row, how = resolve_existing(store, m, vocab)
    assert row is not None and row["canonical_id"] == c["customer_id"] and how == "absorbed"
    assert reconcile_keyless(store, vocab).merged == []           # idempotent


def test_a_name_written_in_another_order_joins_by_similarity_alone(ws):
    """A register's inverted form ("Authority, Tamsinbay Water") shares every word with the
    record's name but does not begin it: only the similarity step (the write-time rule) joins it."""
    from databridge_graph.merge import reconcile_keyless, upsert_node

    store, vocab = ws
    cust = _customers()
    ms = []
    for i, c in enumerate(cust):
        w = c["name"].split()
        m = _mention("organisation", f"{w[-1]}, {' '.join(w[:-1])}", doc=f"d{i}")
        assert upsert_node(store, m, vocab).id == m.id
        ms.append((m, c))
    for c in cust:
        upsert_node(store, _row("organisation", c["customer_id"], c["name"], c["customer_id"]),
                    vocab, id_only=True)
    rep = reconcile_keyless(store, vocab)
    joined = {a: x["survivor"] for x in rep.merged for a in x["absorbed"]}
    assert len(joined) >= 2                                          # non-vacuous
    for m, c in ms:
        if m.id in joined:
            assert store.open_node_row(joined[m.id])["canonical_id"] == c["customer_id"]
            assert "name similarity" in [r for x in rep.merged for r in x["rules"]]


def test_a_keyless_table_row_is_never_absorbed(ws):
    """Only a name read from prose is joined: a register row without a key is a row, and rows
    are never matched by name (`_upsert_row_node`)."""
    from databridge_graph.merge import reconcile_keyless, upsert_node

    store, vocab = ws
    c = _customers()[0]
    upsert_node(store, _row("organisation", "X-1", c["name"], None, table="public.suppliers"),
                vocab, id_only=True)
    upsert_node(store, _row("organisation", c["customer_id"], c["name"], c["customer_id"]),
                vocab, id_only=True)
    assert reconcile_keyless(store, vocab).merged == []
    assert store.open_node_row("organisation:db-x-1") is not None


def test_two_records_equally_named_are_never_chosen_between(ws):
    from databridge_graph.merge import reconcile_keyless, upsert_node

    store, vocab = ws
    c = _customers()[0]
    upsert_node(store, _mention("organisation", c["name"]), vocab)
    for key in (c["customer_id"], c["customer_id"] + "-B"):
        upsert_node(store, _row("organisation", key, c["name"], key), vocab, id_only=True)
    rep = reconcile_keyless(store, vocab)
    assert rep.merged == [] and rep.refused
    assert store.open_node_row(f"organisation:{c['name'].lower()}") is not None


def test_a_person_mention_is_never_joined(ws):
    from databridge_graph.merge import reconcile_keyless, upsert_node

    store, vocab = ws
    person = sorted(vocab.person_kinds)[0]
    upsert_node(store, _row(person, "P-1", "Rowan Achterberg", "P-1"), vocab, id_only=True)
    upsert_node(store, _mention(person, "Rowan Achterberg"), vocab, id_only=True)
    upsert_node(store, _mention(person, "Rowan"), vocab, id_only=True)
    assert reconcile_keyless(store, vocab).merged == []
    assert len([r for r in store.open_nodes() if r["kind"] == person]) == 3


def test_a_kind_without_name_similarity_is_left_to_its_approved_rules(tmp_path, monkeypatch):
    """D-004: finance `legal_entity` turns similarity off; neither the similarity step nor the
    prefix join may join its mentions — only `consolidate_identity` under an approved rule."""
    from databridge_graph import ontology_state
    from databridge_graph.merge import name_prefix_join, reconcile_keyless, upsert_node
    from databridge_graph.store import GraphStore

    monkeypatch.setenv(ontology_state.STATE_ENV, str(tmp_path / "state.json"))
    clear_caches()
    with deployment("finance") as vocab:
        kind = next(k for k, r in vocab.identity_rules.items() if not r.similarity_merge)
        store = GraphStore(str(tmp_path / "g.db"))
        name = next(n for n in _org_names_finance() if len(n.split()) >= 3)
        upsert_node(store, _mention(kind, name), vocab)
        upsert_node(store, _mention(kind, name.split()[0], at=40), vocab)
        upsert_node(store, _row(kind, "CRM-ORG-1", name, "CRM-ORG-1", table="crm.account_org"),
                    vocab, id_only=True)
        assert reconcile_keyless(store, vocab).merged == []
        assert name_prefix_join(store, _mention(kind, name.split()[0], at=80), vocab) is None
    clear_caches()
