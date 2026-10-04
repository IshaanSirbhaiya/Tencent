"""`/brain/query` — E2E run 5 findings F-05, F-06 and W-04 (wiki/40-reviews/E2E-2026-10-02-run5.md).

* **F-05** On the finance estate (50k nodes, 49k edges) a question took 18-140 s. Profiled on a
  copy of that graph: the context and the deterministic answer each read the WHOLE graph — entity
  linking typed every open node, and the 1-hop neighbourhood typed every open edge. And a vague
  question ("Is everything okay with our lending book?") got a dump of loosely matching passages,
  because a passage answer was tried before the question back.
* **F-06** "What does the Yolande Mining interim review 2024 say?" — the filing was HELD FOR REVIEW
  by the ingestion screen, but the company's records matched the question's words, so the
  withheld check never ran and the answer said the review was not there.
* **W-04** "What did we agree with Pellucid … in our most recent meeting?" — the meeting note is a
  document one hop from the company, no passage of it is published, and the context said nothing
  of what the graph extracted from it.

Every store is synthetic and built from the bound pack's declarations (kinds chosen by meta-kind
and edge permission, never named). Expectations are read off the store or record the test wrote.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from conftest import AUTH_HEADERS

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
SRC = "notes-src"
#: Invented names: in no pack, no fixture and no prompt.
ORG_LABEL, MEET_LABEL, AGR_LABEL, SUBJ_LABEL = ("Quellmore Freight Ltd", "Kickoff",
                                                "Harbour Route Pilot", "Ottoline Brask")
DOC_LABEL = "2026-07-14_kickoff_quellmore.txt"


@pytest.fixture
def vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as v:
        yield v


def _meta(v, meta: str) -> str:
    from databridge_graph.meta import NodeMetaKind
    return next(k for k in sorted(v.nodes_binding_to(NodeMetaKind(meta))) if not v.is_subject(k))


def _edge(v, src: str, dst: str) -> str:
    edges = [e for e, d in sorted(v.edge_kinds.items())
             if d.allows("src", src) and d.allows("dst", dst)]
    assert edges, f"the pack cannot link {src} to {dst}"
    return edges[0]


def _span(doc: str, a: int, b: int):
    from databridge_core import SourcePointer
    return SourcePointer(source_id=SRC, kind="doc_span", document_id=doc, char_start=a, char_end=b)


def _add_node(store, nid, kind, label, ptr, attrs=None, canonical=None):
    from databridge_graph import GraphNode
    from databridge_graph.kinds import NodeKind

    attrs = attrs or {}
    store.add_node(GraphNode(id=nid, kind=NodeKind(kind), label=label, canonical_id=canonical,
                             sources=[ptr], attrs=attrs,
                             attr_sources={k: _span(f"{nid}#{k}", 0, 1) for k in attrs},
                             valid_from=T0, system_from=T0))


def _add_edge(store, eid, src, dst, kind, ptr):
    from databridge_graph import GraphEdge
    from databridge_graph.kinds import EdgeKind
    store.add_edge(GraphEdge(id=eid, src_id=src, dst_id=dst, kind=EdgeKind(kind), sources=[ptr],
                             valid_from=T0, system_from=T0))


def _world(v, *, filler: int = 0):
    """An organisation; a meeting-note DOCUMENT that mentions it and the meeting it records; the
    meeting (extracted from the same document) is about an agreement and has a subject party to
    it. ``filler`` unrelated records joined by edges make "read the whole graph" measurable."""
    from databridge_graph import GraphStore

    org, doc, event = _meta(v, "organisation"), _meta(v, "document"), _meta(v, "event")
    agr, subj = _meta(v, "agreement"), sorted(v.subject_kinds)[0]
    store = GraphStore(clock=lambda: T0)
    _add_node(store, "org:q", org, ORG_LABEL, _span("crm-row", 0, 5),
              attrs={"sector": "Logistics"})
    _add_node(store, "doc:k", doc, DOC_LABEL, _span("D1", 0, 400))
    _add_node(store, "evt:k", event, MEET_LABEL, _span("D1", 10, 17))
    _add_node(store, "agr:k", agr, AGR_LABEL, _span("D1", 40, 58))
    _add_node(store, "subj:k", subj, SUBJ_LABEL, _span("D1", 70, 84))
    _add_edge(store, "e1", "doc:k", "org:q", _edge(v, doc, org), _span("D1", 20, 35))
    _add_edge(store, "e2", "doc:k", "evt:k", _edge(v, doc, event), _span("D1", 10, 17))
    _add_edge(store, "e3", "evt:k", "agr:k", _edge(v, event, agr), _span("D1", 30, 58))
    _add_edge(store, "e4", "subj:k", "evt:k", _edge(v, subj, event), _span("D1", 60, 84))
    for i in range(filler):
        _add_node(store, f"fill:{i}", agr, f"Filler Agreement {i}", _span(f"F{i}", 0, 5))
        if i:
            _add_edge(store, f"ef{i}", f"fill:{i}", f"fill:{i - 1}", _edge(v, agr, agr),
                      _span(f"F{i}", 0, 5))
    return store


def _tools(store, *, passages=None, ingest_root=None):
    from brain_server import BrainTools

    t = BrainTools(store=store)
    t.passage_search = (lambda *a, **k: list(passages)) if passages else (lambda *a, **k: [])
    t.ingest_root = ingest_root
    return t


class _Passage:
    def __init__(self, text, doc="P1", source=SRC):
        self.text, self.chunk_id, self.doc_id, self.source_id = text, f"{doc}#0", doc, source
        self.locator, self.approx_locator, self.score = "p1", False, 1.0
        from databridge_core import SourcePointer
        self.pointer = SourcePointer(source_id=source, kind="doc_span", document_id=doc,
                                     char_start=0, char_end=len(text))


class _ModelMustNotBeCalled:
    def complete_text(self, *a, **k):                       # noqa: D401
        raise AssertionError("the model was called")


class _Counter:
    """Counts the typed conversions a read performs (the whole-graph signature)."""

    def __init__(self, monkeypatch):
        import databridge_graph.store as st
        self.nodes = self.edges = 0
        rn, re_ = st.row_to_node, st.row_to_edge

        def node(*a, **k):
            self.nodes += 1
            return rn(*a, **k)

        def edge(*a, **k):
            self.edges += 1
            return re_(*a, **k)
        import brain_server
        import databridge_graph.query as gq
        for mod in (st, gq):
            monkeypatch.setattr(mod, "row_to_node", node)
            monkeypatch.setattr(mod, "row_to_edge", edge)
        monkeypatch.setattr(brain_server, "row_to_node", node)


# ================================================================================================
# F-05 — the graph is read by index, and the answers are the same
# ================================================================================================
def test_f05_the_neighbourhood_read_by_index_is_neighbors_depth_1(vocab):
    from databridge_graph import neighbors
    from databridge_gateway.llm import bounded_neighbourhood

    store = _world(vocab, filler=12)
    for row in store.open_nodes():
        want = neighbors(store, row["id"], depth=1)
        nodes, edges, total = bounded_neighbourhood(store, row["id"], cap=10 ** 6)
        assert {e.id for e in edges} == {e.id for e in want.edges}, row["id"]
        assert {n.id for n in nodes} == {n.id for n in want.nodes}, row["id"]
        assert total == len(want.edges)


def test_f05_a_large_neighbourhood_is_cut_by_relevance_and_the_context_says_so(vocab,
                                                                              monkeypatch):
    import databridge_gateway.llm as llm

    store = _world(vocab)
    monkeypatch.setattr(llm, "MAX_NEIGHBOUR_EDGES", 2)
    nodes, edges, total = llm.bounded_neighbourhood(store, "evt:k", cap=2)
    assert total == 3 and len(edges) == 2                    # e2, e3, e4 touch the meeting
    ctx = llm.build_context(_tools(store), f"What happened at the {MEET_LABEL}?")
    assert "evt:k" in ctx["nodes_used"], ctx
    assert re.search(rf"NOTE: '{MEET_LABEL}' has 3 relationships; the 2 most relevant",
                     ctx["context"])


def test_f05_the_context_reads_only_the_matched_neighbourhood(vocab, monkeypatch):
    """Non-vacuous: the store holds many more edges than the matched record touches; the context
    must type only the record's own (it typed EVERY open edge before)."""
    from databridge_gateway.llm import build_context

    store = _world(vocab, filler=60)
    all_edges = len(store.open_edges())
    count = _Counter(monkeypatch)
    ctx = build_context(_tools(store), f"Which sector is {ORG_LABEL} in?")
    assert ctx["nodes_used"][0] == "org:q", ctx
    assert all_edges > 50 and count.edges < 15, (count.edges, all_edges)
    assert count.nodes < 20, count.nodes


def test_f05_entity_linking_by_index_ranks_exactly_as_link_entities(vocab, monkeypatch):
    from brain_server import link_entities_indexed
    from databridge_graph.query import link_entities

    store = _world(vocab, filler=20)
    questions = [f"Which sector is {ORG_LABEL} in?", f"Tell me about the {MEET_LABEL}",
                 f"{AGR_LABEL} status", "Filler Agreement 7", "capital of Australia",
                 f"Who is {SUBJ_LABEL}?"]
    for q in questions:
        assert [n.id for n in link_entities_indexed(store, q, top_k=8)] == \
            [n.id for n in link_entities(store, q, top_k=8)], q
    assert any(link_entities(store, q) for q in questions)          # non-vacuous
    count = _Counter(monkeypatch)
    got = link_entities_indexed(store, f"Which sector is {ORG_LABEL} in?", top_k=3)
    assert got                                                            # non-vacuous
    assert count.nodes <= len(got), count.nodes           # only what it returns is typed


def test_f05_the_deterministic_answer_by_index_is_nl_query(vocab, monkeypatch):
    """`nl_query_indexed` returns what `nl_query` returns, field for field — including a node id
    holding a SPACE (appending it to the question would have been split into another node's id)
    and the same-name refusal — while typing only the primary record's neighbourhood."""
    from brain_server import answer_to_dict, nl_query_indexed
    from databridge_graph import nl_query

    store = _world(vocab, filler=40)
    _add_node(store, "org:q quellmore", _meta(vocab, "organisation"), "Quellmore",
              _span("crm-row-2", 0, 5))
    twin = _meta(vocab, "agreement")
    _add_node(store, "agr:twin", twin, AGR_LABEL, _span("D9", 0, 5))
    questions = [f"Which sector is {ORG_LABEL} in?", f"Tell me about {MEET_LABEL}",
                 f"What is {AGR_LABEL}?", "Quellmore", "capital of Australia",
                 f"Who is {SUBJ_LABEL}?", "Filler Agreement 9"]
    for q in questions:
        want = json.dumps(answer_to_dict(nl_query(store, q), store), sort_keys=True, default=str)
        got = json.dumps(answer_to_dict(nl_query_indexed(store, q), store), sort_keys=True,
                         default=str)
        assert got == want, q
    assert answer_to_dict(nl_query(store, f"What is {AGR_LABEL}?"), store)["candidates"]
    assert answer_to_dict(nl_query(store, f"Which sector is {ORG_LABEL} in?"),
                          store)["grounded"]
    count = _Counter(monkeypatch)
    nl_query_indexed(store, "Filler Agreement 9")
    assert count.edges <= 4 and count.nodes < 15, (count.edges, count.nodes)


def test_f05_a_vague_question_gets_a_question_back_even_when_a_passage_shares_a_word(vocab):
    """Run 5's finance answer to "Anything I should worry about?" was a passage dump: some
    passage always shares a word ("should") with a vague question on a large estate."""
    from brain_server import _lexically_relevant

    q = "Anything I should worry about?"
    text = "You should review the covenant pack before the committee."
    assert _lexically_relevant(q, text)                                   # non-vacuous
    out = _tools(_world(vocab), passages=[_Passage(text)]).brain_query(q)
    assert out["answer_kind"] == "needs_clarification", out
    assert out["questions"] and "covenant pack" not in json.dumps(out)


@pytest.mark.parametrize("with_llm", [False, True])
def test_f05_the_route_asks_back_for_the_finance_wordings(vocab, with_llm):
    from fastapi.testclient import TestClient

    from databridge_gateway import create_app

    text = "Is everything in the lending book okay? The book should be reviewed."
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = _tools(_world(vocab), passages=[_Passage(text)])
        c.app.state.brain_llm = _ModelMustNotBeCalled() if with_llm else None
        for q in ("Anything I should worry about?", "Is everything okay with our lending book?"):
            r = c.post("/brain/query", json={"question": q})
            assert r.status_code == 200, r.text
            assert r.json()["answer_kind"] == "needs_clarification", r.json()


# ================================================================================================
# F-06 — an item held for review is named even when records share its words
# ================================================================================================
HELD = "quellmore-freight_interim_review_2024_FINAL.md"


def _ingest(tmp_path, decision: str) -> tuple:
    from databridge_mcp_ingest.records import GovernanceRecord, line

    run = tmp_path / "ingest" / SRC / "20261002T162617Z-651098"
    run.mkdir(parents=True)
    (run / "report.json").write_text("{}", encoding="utf-8")
    rec = line(GovernanceRecord, {
        "item_id": "c" * 64, "uri": f"C:\\filings\\2024\\{HELD}", "sha256": "d" * 64,
        "rules_source": "screen.yaml", "at": "2026-10-02T16:26:19Z", "decision": decision,
        "fired": [{"rule": "automated_reader_directive", "kind": "quarantine", "count": 1,
                   "weight": None}],
        "rationale": "held for a human", "reverse": "An authenticated owner approves it.",
        "score": 0.0})
    (run / "governance.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return tmp_path / "ingest", rec


@pytest.mark.parametrize("decision, status", [("review", "held_for_review"),
                                              ("block", "withheld")])
def test_f06_a_held_or_blocked_item_the_question_names_is_named_though_records_match(
        vocab, tmp_path, decision, status):
    from databridge_graph.query import link_entities

    root, rec = _ingest(tmp_path, decision)
    store = _world(vocab)
    q = "What does the Quellmore Freight interim review 2024 say?"
    assert link_entities(store, q), "no record matches: the test would not reproduce F-06"
    out = _tools(store, ingest_root=root).brain_query(q)
    assert out["answer_kind"] == "withheld", out
    [item] = out["withheld_items"]
    assert item["name"] == HELD and item["status"] == status
    assert item["rules"] == [f["rule"] for f in rec["fired"]]
    assert HELD in out["answer"] and item["rules"][0] in out["answer"]
    assert rec["reverse"] in out["answer"]
    assert not out["citations"] and not out["values"]                     # nothing quoted


def test_f06_a_question_sharing_only_the_company_name_is_not_about_the_held_item(vocab,
                                                                                   tmp_path):
    root, _rec = _ingest(tmp_path, "review")
    out = _tools(_world(vocab), ingest_root=root).brain_query(
        f"Which sector is {ORG_LABEL} in?")
    assert out.get("withheld_items") is None and out["grounded"], out


@pytest.mark.parametrize("with_llm", [False, True])
def test_f06_the_route_names_the_held_item_without_calling_a_model(vocab, tmp_path, with_llm):
    from fastapi.testclient import TestClient

    from databridge_gateway import create_app
    from databridge_gateway.llm import build_context

    root, _rec = _ingest(tmp_path, "review")
    tools = _tools(_world(vocab), ingest_root=root)
    q = "What does the Quellmore Freight interim review 2024 say?"
    ctx = build_context(tools, q)
    assert ctx["deterministic_reason"] == "withheld_or_needs_clarification" and not ctx["context"]
    with TestClient(create_app(), headers=AUTH_HEADERS) as c:
        c.app.state.brain = tools
        c.app.state.brain_llm = _ModelMustNotBeCalled() if with_llm else None
        r = c.post("/brain/query", json={"question": q})
    assert r.status_code == 200, r.text
    assert r.json()["answer_kind"] == "withheld" and HELD in r.json()["answer"]


# ================================================================================================
# W-04 — a document with no published passage is described by what the graph extracted from it
# ================================================================================================
Q_MEETING = f"What did we agree with {ORG_LABEL} in our most recent meeting?"


def test_w04_the_context_holds_what_was_extracted_from_an_unquoted_document(vocab):
    from databridge_gateway.llm import build_context

    store = _world(vocab)
    ctx = build_context(_tools(store), Q_MEETING)
    assert ctx["nodes_used"][0] == "org:q", ctx
    text = ctx["context"]
    assert "EXTRACTED FROM DOCUMENTS" in text, text
    block = text.split("EXTRACTED FROM DOCUMENTS", 1)[1]
    assert DOC_LABEL in block
    # the meeting it records, what the meeting was about, and who was party to it
    want = {f"--> {MEET_LABEL}", f"{MEET_LABEL} --[", f"--> {AGR_LABEL}", f"{SUBJ_LABEL} --["}
    for w in want:
        assert w in block, (w, block)
    tags = {c["fact_tag"]: c for c in ctx["citations"]}
    for ln in block.splitlines():
        if "--[" not in ln:
            continue
        found = re.findall(r"\[(C\d+)\]", ln)
        assert len(found) == 3 and all(tags[t]["pointers"] for t in found), ln
        if SUBJ_LABEL in ln:
            assert "IDENTITY" in ln                                   # a subject keeps its caveat


def test_w04_a_document_whose_source_published_passages_is_quoted_instead(vocab):
    from databridge_gateway.llm import build_context

    store = _world(vocab)
    ctx = build_context(_tools(store, passages=[_Passage(f"{ORG_LABEL} agreed a pilot.",
                                                         doc="D1")]), Q_MEETING)
    assert "EXTRACTED FROM DOCUMENTS" not in ctx["context"]
    assert "PASSAGES" in ctx["context"]
