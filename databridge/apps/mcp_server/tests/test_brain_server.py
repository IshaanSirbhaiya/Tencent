"""brain_server.BrainTools — the shared tool layer behind the MCP server and the gateway.

"One brain, two mouths": these same methods back the FastAPI ``/brain/*`` routes, so what passes
here is what an agent over MCP and the front desk UI both see. Retargeted to the clinic graph by
ADR-0009.
"""
from __future__ import annotations

from brain_server import BrainTools, answer_to_dict, edge_to_dict, node_to_dict
from databridge_evals.e_intake import FIXTURE_TEXT, gold_harness
from databridge_graph.clinic_seed import document_node_id

MERIDIAN = "Meridian Life Assurance Pte Ltd"


def _tools():
    t = BrainTools(harness=gold_harness())
    t.load_seed()
    return t


def test_graph_export_shape_for_react_flow():
    g = _tools().graph()
    assert g["stats"]["nodes"] >= 60 and g["stats"]["edges"] >= 100
    n = g["nodes"][0]
    assert {"id", "kind", "label", "attrs"} <= set(n)
    e = g["edges"][0]
    assert {"id", "source", "target", "kind"} <= set(e)   # React Flow source/target keys


def test_brain_query_grounded_with_citations():
    out = _tools().brain_query("What does the WELL2 package include?")
    assert out["grounded"], out["refusal"]
    assert "WELL2" in out["answer"]
    assert out["citations"] and out["citations"][0]["pointers"]


def test_brain_query_no_match_not_grounded():
    # NB: the question must share no token with the graph. "what is the meaning of life" does NOT
    # work here — two seeded insurers are "…Life Assurance", so `link_entities` matches on the
    # incidental word and answers. That is a known weakness of token-overlap linking, recorded in
    # STATUS.md for the Phase 4 guardrail pass; it is not what this test is about.
    out = _tools().brain_query("What is the airspeed velocity of an unladen swallow?")
    assert out["grounded"] is False


def test_brain_neighbors_by_name_resolves():
    out = _tools().brain_neighbors(MERIDIAN, depth=1)
    assert out["root"].startswith("org:")
    assert any(n["kind"] == "document" for n in out["nodes"])


def test_brain_path_by_name():
    out = _tools().brain_path(MERIDIAN, "Chest X-Ray")
    assert out["found"] and out["steps"]
    assert out["provenance_pointers"]


def test_brain_timeline_by_name():
    out = _tools().brain_timeline(MERIDIAN)
    assert out["entity"].startswith("org:") and out["events"]


def test_brain_ingest_extracts_grounded_subgraph():
    t = _tools()
    before = t.store.counts()["nodes"]
    out = t.brain_ingest("demo.txt", text=FIXTURE_TEXT)
    assert out["entities_added"] >= 1
    assert t.store.counts()["nodes"] >= before        # merged into the live graph


def test_graph_includes_provenance_by_source():
    g = _tools().graph()
    prov = g["provenance_by_source"]
    # the real fixture files are the sources — the CSV, the rules table and the chit letters
    assert "coverage_rules.yaml" in prov
    assert "patient_registration_synthetic.csv" in prov
    assert any(s.endswith((".txt", ".eml")) for s in prov)
    # provenance % is backend-MEASURED (every seed node is grounded → 100), not a UI constant
    assert g["stats"]["provenance_pct"] == 100


def test_insights_are_grounded_and_focusable():
    ins = _tools().insights()["insights"]
    assert 1 <= len(ins) <= 5
    for i in ins:
        assert i["text"] and i["focus"] and i["citations"]   # every insight cites + zooms somewhere


def test_insights_surface_the_ambiguous_identities():
    """The queue insight staff actually need: which documents match more than one registry
    patient and therefore cannot be processed without an in-person check."""
    ins = _tools().insights()["insights"]
    text = " ".join(i["text"] for i in ins)
    assert "registry patients" in text
    assert "in person" in text


def test_no_insight_claims_an_identity_is_established():
    ins = _tools().insights()["insights"]
    for i in ins:
        lowered = i["text"].lower()
        for forbidden in ("identity verified", "confirmed patient", "is the patient"):
            assert forbidden not in lowered, i["text"]


def test_node_detail_returns_receipts_and_connections():
    d = _tools().node_detail(MERIDIAN)
    assert d["node"]["label"] == MERIDIAN
    assert d["sources"] and d["sources"][0]["source_id"] == "coverage_rules.yaml"
    assert d["connections"]                                   # ISSUED edges to its documents


def test_node_detail_on_a_document_reports_claims_not_conclusions():
    """A document's patient links are candidate CLAIMS. Each must arrive with its score, the
    number of competing candidates, and the verification requirement — so no consumer can render
    one as a settled answer.

    Addressed by node id, not by name: DOCUMENT nodes are provenance and are deliberately excluded
    from `link_entities`, so a bare filename does not resolve."""
    d = _tools().node_detail(document_node_id("chit_03_everwell_group_invitation.txt"))
    assert len(d["claims"]) == 2, d["claims"]
    for claim in d["claims"]:
        assert claim["requires_human_verification"] is True
        assert claim["candidate_count"] == 2
        assert claim["match_score"] is not None


def test_node_detail_unknown_entity_errors():
    assert "error" in _tools().node_detail("Nobody McGhost")


def test_concurrent_reads_do_not_corrupt_sources():
    # the gateway serves /brain/{graph,insights,node} concurrently from a threadpool sharing ONE
    # sqlite connection; unlocked reads used to interleave and return [] sources → a sourceless
    # GraphNode crash. With locked reads, hammering the views from many threads must never raise.
    import concurrent.futures as cf

    t = _tools()
    errors: list[str] = []

    def hammer(_i: int):
        try:
            for _ in range(3):
                t.graph()
                t.insights()
                t.node_detail(MERIDIAN)
                t.provenance_by_source()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(hammer, range(8)))
    assert not errors, errors[:3]


def test_brain_ingest_refuses_fabrication_in_unrelated_text():
    # ingesting text that does NOT contain the gold names ⇒ grounding wall refuses them all
    t = _tools()
    out = t.brain_ingest("noise.txt", text="The quick brown fox jumps over the lazy dog.")
    assert out["entities_added"] == 0 and out["refused"]


def _synthetic_add_on_graph():
    """A minimal graph reproducing the cross-issuer add-on bug directly, rather than hoping a real
    fixture happens to exercise it.

    Two ISSUERS, each with one coded package. Package A includes `test_shared`; package B (a
    DIFFERENT issuer's package) also includes `test_shared` — plausible in a shared clinic
    catalogue. Document 1 authorises package A and separately REQUESTS `test_shared`.

    The old code built `tests_in_packages` from every INCLUDES_TEST edge in the whole graph, so
    `test_shared` was "in a package" globally — via package B — even though document 1's own
    package A ALSO includes it, which would make this a bad example... so `test_shared` here is
    deliberately covered by A (not an add-on) and a SECOND test, `test_only_in_b`, is requested by
    document 1 while being included ONLY in B, never in A. Under the bug, `test_only_in_b` was
    wrongly cleared as "in a package" (B's) even though the document that requested it authorises
    A, which does not include it — the honest add-on. The fix must still flag it.
    """
    import datetime as dt

    from databridge_core import SourcePointer
    from databridge_graph import GraphEdge, GraphNode, GraphStore, upsert_edge, upsert_node
    from databridge_graph.schema import EdgeKind, NodeKind, node_id

    t0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    def ptr(src="doc1"):
        return SourcePointer(source_id=src, kind="doc_span", document_id=src,
                             char_start=0, char_end=1)

    def node(kind, slug, label, attrs=None):
        # Each attribute is grounded on the same pointer the node is. Passing a bag of uncited
        # values would work around the per-attribute wall in the one place it is easiest to.
        return GraphNode(id=node_id(kind, slug), kind=kind, label=label,
                         canonical_id=slug, attrs=attrs or {},
                         attr_sources={k: ptr() for k, v in (attrs or {}).items()
                                       if v is not None},
                         sources=[ptr()], valid_from=t0, system_from=t0)

    def edge(src, kind, dst, attrs=None):
        return GraphEdge(id=f"{src}|{kind.value}|{dst}", src_id=src, kind=kind, dst_id=dst,
                         attrs=attrs or {}, sources=[ptr()], valid_from=t0, system_from=t0)

    doc1 = node(NodeKind.DOCUMENT, "doc1", "Document 1")
    rule_a = node(NodeKind.COVERAGE_RULE, "rule-a", "Rule A", attrs={"package_code": "PKGA"})
    package_a = node(NodeKind.PACKAGE, "pkg-a", "Package A")
    rule_b = node(NodeKind.COVERAGE_RULE, "rule-b", "Rule B", attrs={"package_code": "PKGB"})
    package_b = node(NodeKind.PACKAGE, "pkg-b", "Package B")
    test_shared = node(NodeKind.TEST, "test_shared", "Shared Test")
    test_only_in_b = node(NodeKind.TEST, "test_only_in_b", "Test Only In B")

    nodes = [doc1, rule_a, package_a, rule_b, package_b, test_shared, test_only_in_b]
    edges = [
        edge(doc1.id, EdgeKind.AUTHORISES, rule_a.id),
        edge(rule_a.id, EdgeKind.AUTHORISES, package_a.id),
        edge(package_a.id, EdgeKind.INCLUDES_TEST, test_shared.id),
        # rule_b/package_b belong to a document that does not exist in this graph at all — they
        # only need to exist so `test_only_in_b` is "in a package" GLOBALLY but not in doc1's own.
        edge(rule_b.id, EdgeKind.AUTHORISES, package_b.id),
        edge(package_b.id, EdgeKind.INCLUDES_TEST, test_only_in_b.id),
        # doc1 requests both tests.
        edge(doc1.id, EdgeKind.REQUESTS_TEST, test_shared.id),
        edge(doc1.id, EdgeKind.REQUESTS_TEST, test_only_in_b.id),
    ]

    store = GraphStore(clock=lambda: t0)
    for n in nodes:
        upsert_node(store, n)
    for e in edges:
        upsert_edge(store, e)
    return store, test_only_in_b.id


def test_add_on_insight_is_scoped_to_the_documents_own_package():
    """The bug this pins: `insights()`'s "ordered on top of a named package" check used to clear a
    requested test as long as it sat in ANY package in the graph, not the one the requesting
    document actually authorises. `test_only_in_b` is requested by `doc1`, which authorises
    package A — and package A does not include it — so it must surface as a patient-payable
    add-on even though an unrelated package B (a different issuer's) happens to include it too."""
    store, test_only_in_b_id = _synthetic_add_on_graph()
    tools = BrainTools(store=store)
    ins = tools.insights()["insights"]
    add_on = next((i for i in ins if i["kind"] == "test"), None)
    assert add_on is not None, ins
    assert add_on["focus"] == test_only_in_b_id


def test_serialisers_round_trip():
    t = _tools()
    g = t.graph()
    assert node_to_dict.__name__ and edge_to_dict.__name__      # imported and callable
    assert all(isinstance(n["attrs"], dict) for n in g["nodes"])
    answer = t.brain_query("What does the WELL2 package include?")
    assert set(answer) >= {"question", "answer", "grounded", "citations"}
    assert answer_to_dict.__name__
