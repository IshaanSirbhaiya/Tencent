"""`/brain/query` — E2E run 4 findings Q-03 and Q-04 (wiki/40-reviews/E2E-2026-10-01-run4.md).

* **Q-03** "Who is the account owner for Pellucid Freightways?" got "The context does not provide
  information about an account owner". The organisation's row held ``account_owner = LA-007``, but
  ingestion turned that column into a ``manages`` edge from the employee, so the record had no
  ``account_owner`` attribute. The context listed "Tobias Reinholt --[MANAGES]--> Pellucid…" deep in
  RELATIONSHIPS, with nothing tying it to the field the question named.
* **Q-04** "Anything I should worry about?" got 422: only the PLURAL first person counted as the
  asker's own affairs.

Every store is synthetic and built from the bound pack's declarations (kinds chosen by meta-kind
and edge permission, never named); every column and table name is invented here and is in no
pack. Expectations are read off the store the test built.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

from conftest import AUTH_HEADERS

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
SRC = "ledger_db"
#: Invented names: in no pack, no fixture and no prompt.
REC_TABLE, REC_ROW, FIELD = "public.vendors", "V-0042", "stewardship_lead"
OTHER_TABLE, OTHER_FIELD = "public.liaisons", "vendor_ref"
REC_LABEL, SUBJ_LABEL, OTHER_LABEL = "Quillmarsh Haulage Ltd", "Ottilie Faraday-Brook", "Bram Ostlund"


@pytest.fixture
def vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as v:
        yield v


def _cell(table: str, column: str, row: str):
    from databridge_core import SourcePointer
    return SourcePointer(source_id=SRC, kind="table_cell", table=table, column=column, row_id=row)


def _kinds(v):
    """(organisation-like non-subject kind, a subject kind, a subject->org edge kind)."""
    from databridge_graph.meta import NodeMetaKind

    org = next(k for k in sorted(v.nodes_binding_to(NodeMetaKind.ORGANISATION))
               if not v.is_subject(k))
    subj = sorted(v.subject_kinds)[0]
    edges = [e for e, d in sorted(v.edge_kinds.items())
             if d.allows("src", subj) and d.allows("dst", org)]
    assert edges, "the pack cannot link a subject to an organisation"
    return org, subj, edges


def _node(store, nid, kind, label, table, row, attrs):
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind

    store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, canonical_id=row,
                             sources=[_cell(table, "id", row)], attrs=attrs,
                             attr_sources={k: _cell(table, k, row) for k in attrs},
                             valid_from=T0, system_from=T0))


def _edge(store, eid, kind, src, dst, ptr):
    from databridge_graph import GraphEdge
    from databridge_graph.kinds import EdgeKind

    store.add_edge(GraphEdge(id=eid, src_id=src, dst_id=dst, kind=EdgeKind(kind), sources=[ptr],
                             valid_from=T0, system_from=T0))


def _world(v, before_edges=None):
    """A record whose OWN row's ``FIELD`` column became an edge from a subject (run 4's shape),
    and a second subject linked by a column on ANOTHER table's row (a contact's foreign key).
    ``before_edges(store)`` runs before those two edges are written (so they are read last)."""
    from databridge_graph import GraphStore

    org, subj, edges = _kinds(v)
    store = GraphStore(clock=lambda: T0)
    _node(store, "org:q", org, REC_LABEL, REC_TABLE, REC_ROW,
          {"name": REC_LABEL, "city": "Pendlecombe", "sector": "Haulage"})
    _node(store, "subj:own", subj, SUBJ_LABEL, "public.staff", "ST-9",
          {"full_name": SUBJ_LABEL, "title": "Head of Delivery"})
    _node(store, "subj:other", subj, OTHER_LABEL, OTHER_TABLE, "7",
          {"full_name": OTHER_LABEL, "role": "Buyer"})
    if before_edges is not None:
        before_edges(store)
    _edge(store, "e:own", edges[0], "subj:own", "org:q", _cell(REC_TABLE, FIELD, REC_ROW))
    _edge(store, "e:other", edges[-1], "subj:other", "org:q", _cell(OTHER_TABLE, OTHER_FIELD, "7"))
    return store


def _tools(store):
    from brain_server import BrainTools

    t = BrainTools(store=store)
    t.passage_search = lambda *a, **k: []
    t.ingest_root = None
    return t


def _ctx(store, q):
    from databridge_gateway.llm import build_context
    return build_context(_tools(store), q)


def _entity_block(ctx, label) -> list[str]:
    lines = ctx["context"].splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(rf"- (\[C\d+\])+ {re.escape(label)} \[", ln))
    out = []
    for ln in lines[start + 1:]:
        if not ln.startswith("    "):
            break
        out.append(ln)
    return out


QUESTION = f"Who is the {FIELD.replace('_', ' ')} for {REC_LABEL.rsplit(' ', 1)[0]}?"


# ------------------------------------------------------------------------------------------------
# Q-03 — a field held as a relationship reaches the record's block, with its pointer
# ------------------------------------------------------------------------------------------------
def test_the_field_is_not_an_attribute_so_this_test_is_not_vacuous(vocab):
    """Run 4 as it was: the field the question names exists only as an edge's pointer column."""
    from databridge_graph import neighbors, row_to_node

    store = _world(vocab)
    node = row_to_node(store, store.open_node_row("org:q"))
    assert FIELD not in node.attrs and FIELD not in node.attr_sources
    edge_cols = {p.column for e in neighbors(store, "org:q", depth=1).edges for p in e.sources}
    assert FIELD in edge_cols


def test_the_records_own_field_is_written_under_it_with_the_edge_and_related_pointers(vocab):
    store = _world(vocab)
    ctx = _ctx(store, QUESTION)
    assert ctx["refusal"] == "" and ctx["nodes_used"][0] == "org:q", ctx
    block = _entity_block(ctx, REC_LABEL)
    line = next((ln for ln in block if f" {FIELD}: " in ln), None)
    assert line is not None, ctx["context"]
    assert SUBJ_LABEL in line
    by_tag = {c["fact_tag"]: c for c in ctx["citations"]}
    cites = [by_tag[t] for t in re.findall(r"\[(C\d+)\]", line)]
    # one citation is the relationship, whose pointer is the record's own cell for FIELD …
    rel = [c for c in cites if c["kind"] == "relationship"]
    assert rel and rel[0]["pointers"][0] == {"source_id": SRC, "kind": "table_cell",
                                             "table": REC_TABLE, "column": FIELD,
                                             "row_id": REC_ROW}
    # … and one is the related record itself
    assert any(c["entity_id"] == "subj:own" for c in cites)
    # naming a related SUBJECT is a citation, never an identification: the caveat is on the line
    assert "IDENTITY" in line


def test_a_column_on_another_rows_record_is_not_a_field_of_this_record(vocab):
    store = _world(vocab)
    ctx = _ctx(store, QUESTION)
    block = "\n".join(_entity_block(ctx, REC_LABEL))
    assert OTHER_FIELD not in block and OTHER_LABEL not in block
    # it is still a relationship, and says which field it was read from
    rel = next(ln for ln in ctx["context"].splitlines()
               if OTHER_LABEL in ln and "--[" in ln)
    assert f"{OTHER_TABLE}.{OTHER_FIELD}" in rel


def test_the_asked_relationship_survives_the_relationship_cap(vocab):
    """Twenty-plus relationships from other rows, written FIRST, must not push the asked one
    out of the capped RELATIONSHIPS list."""
    from databridge_graph.meta import NodeMetaKind

    org, subj, edges = _kinds(vocab)
    doc = sorted(vocab.nodes_binding_to(NodeMetaKind.DOCUMENT))
    src_kind = next(k for k in sorted(vocab.node_kinds) if not vocab.is_subject(k)
                    and k not in doc and k != org
                    and any(d.allows("src", k) and d.allows("dst", org)
                            for d in vocab.edge_kinds.values()))
    ek = next(e for e, d in sorted(vocab.edge_kinds.items())
              if d.allows("src", src_kind) and d.allows("dst", org))
    def fillers(store):
        for i in range(25):
            _node(store, f"r:{i}", src_kind, f"Filler {i:02d}", "public.fillers", f"F{i}",
                  {"note": f"n{i}"})
            _edge(store, f"e:f{i}", ek, f"r:{i}", "org:q",
                  _cell("public.fillers", "vendor_ref", f"F{i}"))

    store = _world(vocab, before_edges=fillers)
    ctx = _ctx(store, QUESTION)
    rels = ctx["context"].split("RELATIONSHIPS:")[1].splitlines()
    assert any(SUBJ_LABEL in ln and FIELD in ln for ln in rels)


def test_a_name_still_never_selects_a_subject(vocab):
    """Hard constraint 1 is untouched: asking BY the person's name is refused with candidates."""
    store = _world(vocab)
    ctx = _ctx(store, f"What does {SUBJ_LABEL} look after?")
    assert ctx["refusal"] and "subj:own" in ctx["candidates"]


class _Res:
    def __init__(self, text):
        self.text = text


class _ReadsTheContext:
    """A stand-in model that answers FROM the context it is sent: it finds the line under the
    record that names the asked field and repeats it. Nothing about the answer is written here."""

    def complete_text(self, task, messages, *a, **k):
        ctx = messages[-1]["content"]
        m = re.search(rf"^\s+((?:\[C\d+\])+) {FIELD}: (.+?) \[", ctx, re.M)
        if not m:
            return _Res("The context does not provide that.")
        return _Res(f"The {FIELD.replace('_', ' ')} of {REC_LABEL} is {m.group(2)} {m.group(1)}.")


def test_the_route_serves_an_answer_that_names_the_related_record(vocab):
    from fastapi.testclient import TestClient

    from databridge_gateway import create_app

    store = _world(vocab)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = _tools(store)
        c.app.state.brain_llm = _ReadsTheContext()
        r = c.post("/brain/query", json={"question": QUESTION})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_real_llm"] is True and body["grounded"] is True, body   # the model branch
    assert SUBJ_LABEL in body["answer"] and not body.get("dropped_sentences"), body
    assert "subj:own" in {c["entity_id"] for c in body["citations"]}


# ------------------------------------------------------------------------------------------------
# Q-04 — the first person, singular or plural, is the asker's own affairs
# ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("q, vague", [
    ("Anything I should worry about?", True),                # run 4's exact sentence
    ("Is there anything I'm missing?", True),                # a contraction is not a name
    ("What should we chase this week?", True),
    ("Let's see what is overdue.", True),
    ("Should I worry about Brackenfold?", False),            # names a thing
    ("I wonder what the capital of Australia is", False),    # names a thing
    ("Anything to worry about?", False),                     # not in the first person
    ("How many bones are in the adult human body?", False),
])
def test_vagueness_rule_counts_the_singular_first_person(q, vague):
    from brain_server import is_vague_question
    assert is_vague_question(q) is vague


class _ModelMustNotBeCalled:
    def complete_text(self, *a, **k):
        raise AssertionError("the model was called for a question with no context")


@pytest.mark.parametrize("with_llm", [False, True])
def test_run4_vague_sentence_gets_a_question_back_not_422(vocab, with_llm):
    from fastapi.testclient import TestClient

    from databridge_gateway import create_app

    store = _world(vocab)
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = _tools(store)
        c.app.state.brain_llm = _ModelMustNotBeCalled() if with_llm else None
        r = c.post("/brain/query", json={"question": "Anything I should worry about?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer_kind"] == "needs_clarification" and body["question_items"]
    # options are record KINDS, never a person's name
    assert not any(SUBJ_LABEL in o or OTHER_LABEL in o
                   for o in body["question_items"][0]["options"])
