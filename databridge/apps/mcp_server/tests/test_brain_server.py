"""brain_server.BrainTools — the shared tool layer behind the MCP server and the gateway (T08 §9)."""
from __future__ import annotations

from brain_server import BrainTools, answer_to_dict, edge_to_dict, node_to_dict
from databridge_evals.e_brain import FIXTURE_TEXT, gold_harness


def _tools():
    t = BrainTools(harness=gold_harness())
    t.load_seed()
    return t


def test_graph_export_shape_for_react_flow():
    g = _tools().graph()
    assert g["stats"]["nodes"] >= 100 and g["stats"]["edges"] >= 150   # dense investor seed
    n = g["nodes"][0]
    assert {"id", "kind", "label", "attrs"} <= set(n)
    e = g["edges"][0]
    assert {"id", "source", "target", "kind"} <= set(e)   # React Flow source/target keys


def test_brain_query_grounded_with_citations():
    out = _tools().brain_query("What is the BigClient contract value?")
    assert out["grounded"] and "$2,400,000" in out["answer"]
    assert out["citations"] and out["citations"][0]["pointers"]


def test_brain_query_no_match_not_grounded():
    out = _tools().brain_query("what is the meaning of life")
    assert out["grounded"] is False


def test_brain_neighbors_by_name_resolves():
    out = _tools().brain_neighbors("Sarah Chen", depth=1)
    assert out["root"].startswith("person:")
    assert any(n["label"] == "Acme Corp" for n in out["nodes"])


def test_brain_path_by_name():
    out = _tools().brain_path("Sarah Chen", "Data Migration")
    assert out["found"] and out["steps"]
    assert out["provenance_pointers"]


def test_brain_timeline_by_name():
    out = _tools().brain_timeline("Sarah Chen")
    assert out["entity"].startswith("person:") and out["events"]


def test_brain_ingest_extracts_grounded_subgraph():
    t = _tools()
    before = t.store.counts()["nodes"]
    out = t.brain_ingest("demo.txt", text=FIXTURE_TEXT)
    assert out["entities_added"] >= 1
    assert t.store.counts()["nodes"] >= before        # merged into the live graph


def test_graph_includes_provenance_by_source():
    g = _tools().graph()
    prov = g["provenance_by_source"]
    # the 3 original docs are present (now among 15 source documents)
    assert {"email_001.eml", "meeting_001.txt", "contract_001.pdf"} <= set(prov)
    assert sum(prov.values()) >= g["stats"]["nodes"] - 15      # ~every non-doc node attributed
    # provenance % is backend-MEASURED (every seed node is grounded → 100), not a UI constant
    assert g["stats"]["provenance_pct"] == 100


def test_insights_are_grounded_and_focusable():
    ins = _tools().insights()["insights"]
    assert 1 <= len(ins) <= 5
    for i in ins:
        assert i["text"] and i["focus"] and i["citations"]   # every insight cites + zooms somewhere


def test_node_detail_returns_receipts_and_connections():
    d = _tools().node_detail("Sarah Chen")
    assert d["node"]["label"] == "Sarah Chen"
    assert d["sources"] and d["sources"][0]["source_id"].endswith((".eml", ".txt", ".pdf"))
    assert d["connections"]                                   # Sarah has WORKS_AT/DECIDED/MENTIONED
    assert any(dec["label"] for dec in d["decisions"])        # Sarah made decisions


def test_node_detail_unknown_entity_errors():
    assert "error" in _tools().node_detail("Nobody McGhost")


def test_demo_fixtures_ingest_new_grounded_entities():
    # Part 2: each of the 3 drag-drop fixtures deterministically extracts its OWN entities through
    # the real grounding + upsert path, adding genuinely NEW nodes (0 refused = nothing fabricated).
    from brain_demo import demo_ingest_harness, registered_sources

    t = BrainTools(harness=demo_ingest_harness(), sources=registered_sources())
    t.load_seed()
    before = t.store.counts()["nodes"]
    expected = {
        "email_001.eml": "Alice Wong",
        "meeting_001.txt": "Project Phoenix",
        "contract_001.pdf": "Robert Vance",
    }
    for fname, must in expected.items():
        r = t.brain_ingest(fname)
        assert not r["refused"], (fname, r["refused"])
        assert must in r["new_entities"], (fname, r["new_entities"])
    assert t.store.counts()["nodes"] > before          # the graph grew
    # idempotent: re-dropping the same fixture adds nothing new
    assert t.brain_ingest("email_001.eml")["new_entities"] == []


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
                t.node_detail("Sarah Chen")
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
