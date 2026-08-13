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


def test_the_mcp_node_payload_cites_each_attribute_and_names_the_rest():
    """This payload is consumed by a model. `attrs` beside `n_sources` is the pooled-citation
    shape at its purest: a COUNT names nothing, so "3 citations" sitting next to four values reads
    as backing all four and cannot be checked."""
    from databridge_graph.clinic_seed import fresh_clinic_store
    from databridge_graph.store import row_to_node

    from brain_server import node_to_dict

    store = fresh_clinic_store()
    patients = [r for r in store.open_nodes() if r["id"].startswith("patient:")]
    assert patients, "no patient nodes in the seed"
    payload = node_to_dict(row_to_node(store, patients[0]))

    for key, value in payload["attrs"].items():
        if value is None:
            assert key in payload["unresolved"]
        else:
            assert key in payload["attribute_sources"], f"{key} is emitted with no pointer"
            assert payload["attribute_sources"][key]["kind"]
    assert "not the values" in payload["sources_ground"]


def test_every_serialised_patient_node_carries_the_caveat_in_every_tool():
    """`node_detail` is not the only place a registry row reaches a model. `brain_neighbors`,
    `brain_path` and `graph()` all hand back patient nodes with a name and a date of birth on
    them, and a patient reached through someone ELSE's 1-hop neighbourhood never passes through
    `node_detail` at all. The caveat is therefore attached in `node_to_dict`, which is the one
    place every one of those paths goes through."""
    from brain_server import PATIENT_NODE_CAVEAT

    tools = _tools()
    patients = 0
    for node in tools.graph()["nodes"]:
        if node["kind"] == "patient":
            assert node["identity_caveat"] == PATIENT_NODE_CAVEAT, node["id"]
            assert node["identity_verified"] is False
            patients += 1
        else:
            assert "identity_caveat" not in node, node["id"]
    assert patients > 100

    # the same node reached sideways, through a DOCUMENT's neighbourhood
    doc = document_node_id("chit_02_everwell_voucher.txt")
    sideways = [n for n in tools.brain_neighbors(doc)["nodes"] if n["kind"] == "patient"]
    assert sideways
    for node in sideways:
        assert node["identity_caveat"] == PATIENT_NODE_CAVEAT


def test_the_claimed_by_a_document_flag_names_the_documents_it_was_computed_from():
    """A boolean nobody can check is the same problem as a count with no members. And only a
    DOCUMENT's claim counts — `FOR_PATIENT` also runs visit → patient, and counting one of those
    would pick the LESS cautious caveat."""
    from databridge_graph.clinic_seed import claimed_patient_node_ids
    from databridge_graph.store import row_to_edge, row_to_node

    tools = _tools()
    nodes = [row_to_node(tools.store, r) for r in tools.store.open_nodes()]
    edges = [row_to_edge(tools.store, r) for r in tools.store.open_edges()]
    claimed = sorted(claimed_patient_node_ids(nodes, edges))
    assert claimed

    detail = tools.node_detail(claimed[0])
    assert detail["identity"]["claimed_by_a_document"] is True
    assert detail["identity"]["claimed_by"]
    for document_id in detail["identity"]["claimed_by"]:
        assert tools.store.open_node_row(document_id)["kind"] == "document"
        # …and it really is one of this node's own connections, not an assertion out of nowhere
        assert any(c["other_id"] == document_id and c["direction"] == "in"
                   and c["edge_kind"] == "for_patient" for c in detail["connections"])


# --- a name is not an identifier (CLAUDE.md §Hard constraints 1) ----------------------
#
# The registry holds three people called "Loh Wei Ming", four "Rahman Zhi Hao" and four
# "Tan Mei Ling" (`merge.py` refuses to fuzzy-merge patients for exactly this reason). A tool that
# answered about ONE of them would have made an identity determination — silently, by SQLite rowid
# order or by whichever scored first among BM25 equals, with nothing in the response saying a
# choice had been made.


def _ambiguous_name(tools):
    """A patient label the seeded registry holds more than once, and its node ids.

    Derived, never hardcoded: the point is the property, and a literal name would stop testing it
    the moment the synthetic registry was regenerated.
    """
    import collections

    from databridge_graph import NodeKind

    by_label = collections.defaultdict(list)
    for row in tools.store.open_nodes():
        if row["kind"] == NodeKind.PATIENT.value:
            by_label[row["label"]].append(row["id"])
    label, ids = max(by_label.items(), key=lambda kv: (len(kv[1]), kv[0]))
    assert len(ids) > 1, "the registry no longer holds a repeated name — this test is now vacuous"
    return label, sorted(ids)


def test_a_name_shared_by_several_patients_is_refused_not_silently_picked():
    tools = _tools()
    label, ids = _ambiguous_name(tools)

    ref = tools._resolve(label)
    assert ref.node_id is None, f"{label!r} resolved to one of {len(ids)} people"
    assert list(ref.candidates) == ids
    assert "will not choose" in ref.error


def test_the_refusal_reaches_every_tool_in_that_tools_own_payload_shape():
    """A caller that reads `["nodes"]` without checking `error` must get nothing, not a KeyError —
    and must not have to make a second round trip to learn which ids it could have meant."""
    tools = _tools()
    label, ids = _ambiguous_name(tools)

    neighbors = tools.brain_neighbors(label)
    assert neighbors["error"] and neighbors["nodes"] == [] and neighbors["edges"] == []
    assert neighbors["candidates"] == ids

    timeline = tools.brain_timeline(label)
    assert timeline["error"] and timeline["events"] == []
    assert timeline["candidates"] == ids

    detail = tools.node_detail(label)
    assert detail["error"] and detail["candidates"] == ids

    # both endpoints of a path are reported independently, so the caller is never left guessing
    # which of two arguments was the problem
    path = tools.brain_path(label, MERIDIAN)
    assert path["error"] and path["found"] is False and path["candidates"] == ids


def test_the_refusal_is_a_fork_in_the_road_not_a_dead_end():
    """Asking again with one of the ids the refusal handed back resolves cleanly. **A node id is
    an identifier**, and a caller who has one has already made the choice this layer will not make
    for them. Without this the refusal would just be a wall."""
    tools = _tools()
    label, ids = _ambiguous_name(tools)

    for node_id in ids:
        ref = tools._resolve(node_id)
        assert ref.node_id == node_id, ref.error
        assert ref.candidates == ()
        detail = tools.node_detail(node_id)
        assert "error" not in detail
        assert detail["node"]["label"] == label
        assert detail["node"]["id"] == node_id


def test_an_ambiguous_question_is_refused_by_brain_query_and_carries_the_candidates():
    """The same rule on the NL surface. `nl_query` and `_resolve` are one implementation
    (`databridge_graph.identity`) precisely because this was hardened here first and nowhere
    else."""
    tools = _tools()
    label, ids = _ambiguous_name(tools)

    out = tools.brain_query(f"Who is {label}?")
    assert out["grounded"] is False
    assert out["answer"] == ""
    assert out["candidates"] == ids

    resolved = tools.brain_query(ids[0])
    assert resolved["grounded"], resolved["refusal"]
    assert resolved["candidates"] == []
    assert "not a verified identity" in resolved["answer"]


# --- registry presence is not identification -----------------------------------------


def test_a_patient_node_detail_carries_the_identity_caveat_with_or_without_a_claim():
    """`claims` is empty for the ~289 registry rows no document names, and an empty list carries
    no caveat at all — the drawer would have shown a name, a date of birth and a nationality with
    nothing saying "not verified". The caveat is keyed on the KIND."""
    from databridge_graph import NodeKind
    from databridge_graph.clinic_seed import claimed_patient_node_ids
    from databridge_graph.identity import (
        IDENTITY_CLAIMED_ON_DOCUMENT,
        IDENTITY_REGISTRY_ONLY,
    )
    from databridge_graph.store import row_to_edge, row_to_node

    tools = _tools()
    nodes = [row_to_node(tools.store, r) for r in tools.store.open_nodes()]
    edges = [row_to_edge(tools.store, r) for r in tools.store.open_edges()]
    claimed = set(claimed_patient_node_ids(nodes, edges))
    patients = [n for n in nodes if n.kind is NodeKind.PATIENT]
    on_file_only = [n for n in patients if n.id not in claimed]
    assert claimed and on_file_only

    # `claims` is OUTGOING only on purpose (a PATIENT node's incoming FOR_PATIENT edges are
    # DOCUMENTS, and listing them as candidate patients was a real bug), so it is empty on both of
    # these. That is exactly why the caveat cannot be keyed on it.
    named = tools.node_detail(sorted(claimed)[0])
    assert named["claims"] == []
    assert named["identity"] == {"verified": False, "requires_human_verification": True,
                                 "claimed_by_a_document": True,
                                 "claimed_by": named["identity"]["claimed_by"],
                                 "caveat": IDENTITY_CLAIMED_ON_DOCUMENT}
    assert named["identity"]["claimed_by"], "the boolean must name what produced it"

    unnamed = tools.node_detail(on_file_only[0].id)
    assert unnamed["claims"] == []
    assert unnamed["identity"] == {"verified": False, "requires_human_verification": True,
                                   "claimed_by_a_document": False, "claimed_by": [],
                                   "caveat": IDENTITY_REGISTRY_ONLY}

    # nothing that is not a patient acquires an identity verdict it has no business carrying
    org = tools.node_detail(MERIDIAN)
    assert "identity" not in org
