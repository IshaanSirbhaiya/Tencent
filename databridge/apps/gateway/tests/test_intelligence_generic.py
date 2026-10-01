"""`/intelligence` over a synthetic pack — the figures and the agent are generic, not clinical.

The clinical files (`test_intelligence_routes.py`, `test_intelligence_ask*.py`,
`test_intelligence_surface.py`) keep the healthcare behaviour pinned. This one binds
`generic_graph.deskpack` (a subject kind `person`, a claim edge `concerns`) or `plainpack` (no
subject at all) and asserts, with every expectation derived from the store the fixture built:

* the figures are computed over meta-kinds: kinds present, documents per source, cross-source
  entities, orphans, most-connected, recently-ingested, and the identity-work figures exactly when
  the vocabulary declares what they rest on (`None` — not zero — when it does not);
* the subject is withheld everywhere, a document that can claim one is a handle, and ordinary
  knowledge prints by its label;
* the entity tools resolve a quoted name, walk only structural edges, and cannot reach a subject;
* the coverage register reads the ingestion ledgers by pattern.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.intelligence_routes import _register_intelligence_routes

from conftest import AUTH_HEADERS
from generic_graph import bound, build_deskpack_graph, write_packs


@pytest.fixture()
def desk(tmp_path, monkeypatch):
    packs = write_packs(tmp_path)
    with bound(packs, "deskpack"):
        g = build_deskpack_graph(tmp_path)
        monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(g.capture_dir))
        monkeypatch.setenv("WALNUT_INGEST_DIR", str(g.ingest_dir))
        app = FastAPI()
        _register_intelligence_routes(app)
        app.state.graph_store = g.store
        with TestClient(app, headers=AUTH_HEADERS) as client:
            yield g, client


def _figures(client) -> dict:
    body = client.get("/intelligence").json()
    assert body["graph_unavailable"] is None, body["graph_unavailable"]
    return body


def test_kind_counts_come_from_the_store_and_the_packs_declarations(desk):
    g, client = desk
    graph = _figures(client)["graph"]
    rows = g.store.open_nodes()
    expected: dict[str, int] = {}
    for row in rows:
        expected[row["kind"]] = expected.get(row["kind"], 0) + 1
    assert {k["kind"]: k["n"] for k in graph["kinds"]} == expected
    assert {k["kind"] for k in graph["kinds"] if k["subject"]} == {"person"}
    metas = {k["kind"]: k["meta"] for k in graph["kinds"]}
    assert metas["memo"] == metas["wiki_page"] == "document"
    assert graph["documents"] == sum(1 for r in rows if metas[r["kind"]] == "document")


def test_the_subject_is_withheld_the_claiming_document_is_a_handle(desk):
    g, client = desk
    body = _figures(client)
    flat = json.dumps(body)
    assert "Quinn Vale" not in flat and "SN-0042" not in flat
    assert "person:sn-0042" not in flat
    # The memo can claim a subject, so it — and its filename-ish id — never print.
    assert "memos/rota.txt" not in flat and "Rota memo" not in flat
    graph = body["graph"]
    assert graph["subjects"]["n"] == 1 and graph["subjects"]["members"] == []
    assert graph["subjects"]["members_withheld"]
    # Ordinary knowledge prints by its label.
    labels = {e["label"] for e in graph["entities"].values()}
    assert "Platform Team" in labels


def test_the_identity_figures_follow_the_claim_edges(desk):
    g, client = desk
    graph = _figures(client)["graph"]
    claims = [e for e in g.store.open_edges() if e["kind"] == "concerns"]
    assert graph["subjects"]["n"] == len({e["dst_id"] for e in claims})
    assert graph["ambiguity"]["n"] == 0 and graph["ambiguity"]["of"] == len(
        {e["src_id"] for e in claims})
    # Only memos can claim a subject, so only memos count toward "unlinked".
    memos = [r for r in g.store.open_nodes() if r["kind"] == "memo"]
    assert graph["unlinked_documents"]["of"] == len(memos)


def test_documents_by_source_cross_source_and_orphans_are_derived_from_pointers(desk):
    g, client = desk
    graph = _figures(client)["graph"]
    by_source = {s["source"]: s for s in graph["documents_by_source"]}
    # The wiki names no subject, so its source prints; the memo drive's does not.
    assert by_source["wiki"]["documents"] == 1 and by_source["handbook"]["documents"] == 1
    assert "memos" not in by_source
    assert any(s["source"].startswith("src-") for s in graph["documents_by_source"])
    team = next(e for e in graph["entities"].values() if e["label"] == "Platform Team")
    assert team["id"] in graph["cross_source"]["members"]
    linked = {x for e in g.store.open_edges() for x in (e["src_id"], e["dst_id"])}
    orphans = {r["id"] for r in g.store.open_nodes()
               if r["id"] not in linked and r["kind"] != "person"}
    assert orphans and graph["orphans"]["n"] == len(orphans)
    # Members are PUBLIC ids: a subject-bearing orphan is listed by handle, an ordinary one by id.
    assert len(graph["orphans"]["members"]) == len(orphans)
    assert "topic:unlinked" in graph["orphans"]["members"]
    assert "topic:rota-change" not in graph["orphans"]["members"]


def test_rankings_are_computed_and_never_rank_a_subject(desk):
    g, client = desk
    graph = _figures(client)["graph"]
    ranked = [r["id"] for r in graph["most_connected"]]
    assert ranked, "nothing ranked"
    assert "person:sn-0042" not in ranked
    degree: dict[str, int] = {}
    for e in g.store.open_edges():
        for x in (e["src_id"], e["dst_id"]):
            degree[x] = degree.get(x, 0) + 1
    top = graph["most_connected"][0]
    assert top["degree"] == max(v for k, v in degree.items() if not k.startswith("person:"))
    assert graph["recently_ingested"]


def test_the_coverage_register_reads_every_ledger_by_pattern(desk):
    g, client = desk
    ingestion = _figures(client)["ingestion"]
    assert ingestion["excluded"] == sum(g.excluded_reasons.values())
    wiki = next(s for s in ingestion["sources"] if s["source"] == "wiki")
    assert any(name.startswith("excluded_") for name in wiki["latest"]["ledger_files"])


def test_the_drilldown_never_walks_a_claim_or_reaches_the_subject(desk):
    g, client = desk
    graph = _figures(client)["graph"]
    team = next(e["id"] for e in graph["entities"].values() if e["label"] == "Platform Team")
    body = client.get(f"/intelligence/subgraph/{team}", params={"depth": 3}).json()
    assert body["nodes"]
    assert all(e["kind"] != "concerns" for e in body["edges"])
    assert "Quinn Vale" not in json.dumps(body)
    refused = client.get("/intelligence/subgraph/person:sn-0042")
    assert refused.status_code == 403
    assert refused.json()["refusal"]["code"] == "NOT_ON_THIS_SURFACE"


def test_an_entity_tool_resolves_a_quoted_name_and_answers_with_citations(desk):
    g, client = desk
    r = client.post("/intelligence/ask",
                    json={"question": 'what are the neighbours of "Platform Team"?'})
    assert r.status_code == 200, r.text
    body = r.json()
    reads = {x["tool"]: x["status"] for x in body["reads"]}
    assert reads.get("neighbours") == "ok", reads
    values = [v for v in body["values"] if v["tool"] == "neighbours"]
    assert values, "the neighbour read reported nothing"
    relations = {v["text"] for v in values if v["field"] == "relationship"}
    expected = {e["kind"] for e in g.store.open_edges()
                if "team:platform" in (e["src_id"], e["dst_id"]) and e["kind"] != "concerns"}
    assert relations == expected
    for value in values:
        sub = client.get(f"/intelligence/subgraph/{value['source']['source_id']}")
        assert sub.status_code == 200


def test_path_between_two_quoted_names(desk):
    _g, client = desk
    r = client.post("/intelligence/ask", json={
        "question": 'how is "Harbour Lights runbook" connected to "release trains"? path'})
    assert r.status_code == 200, r.text
    steps = [v for v in r.json()["values"] if v["tool"] == "path_between"
             and v["field"] == "entity"]
    assert [s["text"] for s in steps][0] == "Harbour Lights runbook"
    assert [s["text"] for s in steps][-1] == "release trains"


def test_an_entity_tool_cannot_be_pointed_at_a_subject(desk):
    _g, client = desk
    r = client.post("/intelligence/ask",
                    json={"question": 'what are the neighbours of "Quinn Vale"?'})
    body = r.json()
    assert "Quinn Vale" not in json.dumps(body.get("values", []))
    assert not [v for v in body.get("values", []) if v["tool"] == "neighbours"]


def test_a_pack_with_no_subject_has_no_identity_figures_and_no_identity_tool(tmp_path,
                                                                            monkeypatch):
    from databridge_core import SourcePointer
    from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind
    from databridge_graph.store import GraphStore

    from databridge_gateway.agent_tools import build_structural_registry
    from generic_graph import WHEN

    monkeypatch.setenv("WALNUT_INGEST_DIR", str(tmp_path / "none"))
    packs = write_packs(tmp_path)
    with bound(packs, "plainpack"):
        store = GraphStore(str(tmp_path / "plain.db"))
        p = SourcePointer(source_id="lib", kind="doc_span", document_id="a.txt",
                          char_start=0, char_end=4)
        store.add_node(GraphNode(id="page:a.txt", kind=NodeKind("page"), label="Alpha page",
                                 sources=[p], valid_from=WHEN, system_from=WHEN))
        store.add_node(GraphNode(id="idea:x", kind=NodeKind("idea"), label="Idea X",
                                 sources=[p], valid_from=WHEN, system_from=WHEN))
        store.add_edge(GraphEdge(id="e1", src_id="page:a.txt", dst_id="idea:x",
                                 kind=EdgeKind("discusses"), sources=[p],
                                 valid_from=WHEN, system_from=WHEN))
        app = FastAPI()
        _register_intelligence_routes(app)
        app.state.graph_store = store
        with TestClient(app, headers=AUTH_HEADERS) as client:
            graph = client.get("/intelligence").json()["graph"]
        names = set(build_structural_registry(store).names())
    for figure in ("subjects", "ambiguity", "unlinked_documents", "conflicts"):
        assert graph[figure] is None, f"{figure} is not applicable, so it is None — never 0"
    assert "documents_needing_a_person" not in names
    assert graph["vocabulary"]["identity_figures"] is False
    labels = {e["label"] for e in graph["entities"].values()}
    assert "Alpha page" in labels, "with no subject declared, a document prints by its label"


# --- the generic surface names no kind ---------------------------------------------------


def _generic_modules() -> list[pathlib.Path]:
    root = pathlib.Path(__file__).resolve().parents[1] / "databridge_gateway"
    graph = pathlib.Path(__file__).resolve().parents[3] / "packages" / "graph" / "databridge_graph"
    return [root / "evidence_routes.py", root / "intelligence_surface.py",
            root / "agent_tools.py", root / "subject_exposure.py", root / "source_capture.py",
            graph / "analytics.py"]


def test_the_generic_modules_name_no_kind_of_any_pack_as_a_literal():
    """No per-domain branching: a vertical's kind name appearing as a string literal in these
    modules is how its vocabulary gets back into the engine. The words checked are every kind a
    VERTICAL pack declares, minus the words the engine owns itself — the shared `common` pack's
    kinds, the meta-kind values and the pointer kinds — all derived from what is on disk."""
    import typing

    from databridge_core.provenance import SourcePointerKind
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
    from databridge_graph.packs import available_packs, load_pack

    engine_words = {m.value for m in NodeMetaKind} | {m.value for m in EdgeMetaKind}
    engine_words |= set(typing.get_args(SourcePointerKind))
    shared = load_pack("common")
    engine_words |= set(shared.node_kinds) | set(shared.edge_kinds)
    kinds: set[str] = set()
    for name in available_packs():
        if name == "common":
            continue
        pack = load_pack(name)
        kinds |= set(pack.node_kinds) | set(pack.edge_kinds)
    kinds -= engine_words
    assert len(kinds) > 20, "the guard must have something to look for"
    offenders = []
    for path in _generic_modules():
        text = path.read_text(encoding="utf-8")
        # A literal used as a wire-contract KEY (`"claim": …`, `["message"]`) is a field name that
        # happens to share a word with some pack's kind; a kind literal is a VALUE being compared
        # or constructed (`== "x"`, `in {"x"}`, `Kind("x")`), and those are all still caught.
        for literal in re.findall(r"""(?<!\[)["']([a-z_]{3,40})["'](?!\s*[:\]])""", text):
            if literal in kinds:
                offenders.append(f"{path.name}: {literal!r}")
    assert not offenders, sorted(set(offenders))


# --- R-017 (Codex t08) ------------------------------------------------------------------------


def test_a_derived_pointer_over_subject_material_is_projected_not_passed_through(desk):
    """t08 #4. A `derived` pointer whose inputs reach subject-bearing material keeps its children
    projected — and must not carry its free-text `derivation` (or a raw non-node `source_id`)
    through verbatim, since that text can name the very person the children were masked for."""
    from databridge_core import SourcePointer
    from databridge_gateway.intelligence_surface import SurfaceNames

    g, _client = desk
    subject_label = g.store.open_node_row("person:sn-0042")["label"]
    memo_ptr = g.store.sources_for("memo:memos/rota.txt")[0]
    derived = SourcePointer(source_id=f"matcher for {subject_label}", kind="derived",
                            derivation=f"matched {subject_label} by staff number",
                            derived_from=[memo_ptr])
    names = SurfaceNames(g.store)
    projected = names.project(derived).model_dump_json()
    assert subject_label not in projected
    assert "memos/rota.txt" not in projected
    # Non-vacuous: ordinary derived provenance still passes through as written.
    wiki_ptr = g.store.sources_for("wiki_page:wiki/runbook.md")[0]
    plain = SourcePointer(source_id="summariser", kind="derived",
                          derivation="summarised the runbook", derived_from=[wiki_ptr])
    assert names.project(plain).derivation == "summarised the runbook"
    assert names.project(plain).source_id == "summariser"


def test_a_rewound_figure_counts_closed_versions_and_still_withholds_a_closed_subject(desk):
    """t08 #6 through the page. A fact closed with a recorded validity end is counted when the
    page is rewound to inside that validity — and a closed SUBJECT version it counts is withheld
    exactly as an open one would be (the naming policy must not read it as "not a node")."""
    import datetime as _dt

    from generic_graph import WHEN

    g, client = desk
    later = WHEN + _dt.timedelta(days=30)
    ended = WHEN + _dt.timedelta(days=20)
    inside = (WHEN + _dt.timedelta(days=10)).date().isoformat()
    g.store.close_node("topic:unlinked", at=later, valid_to=ended)
    g.store.close_node("person:sn-0042", at=later, valid_to=ended)

    now = client.get("/intelligence").json()["graph"]
    then = client.get(f"/intelligence?as_of={inside}").json()["graph"]
    assert then["nodes"] == now["nodes"] + 2, "both closed versions stood at that instant"
    flat = str(then)
    assert "person:sn-0042" not in flat
    assert g.store.all_node_versions("person:sn-0042")[-1]["label"] not in flat
    # The closed ordinary entity is counted (it is an orphan). R-019 t08b #3 widened what follows
    # deliberately: the naming policy is now built over the SNAPSHOT, so that version's label is
    # classified against THAT version's own pointers (before, it was never re-checked, and so
    # failed closed to `kind · handle`). An ordinary entity whose version's pointers name no
    # subject prints the label it carried at `as_of`.
    assert then["orphans"]["n"] == now["orphans"]["n"] + 1
    closed_label = g.store.all_node_versions("topic:unlinked")[-1]["label"]
    assert then["entities"]["topic:unlinked"]["label"] == closed_label


# --- R-019 (Codex batch 2, t08b) ----------------------------------------------------------------
def _rewind_topic(g):
    """Replace `topic:release-trains` later with a new label grounded in a DIFFERENT source, and
    close `topic:unlinked` with a validity end. Returns ``(inside_iso, old_label, new_label)``."""
    import datetime as _dt

    from databridge_graph.schema import GraphNode, NodeKind
    from generic_graph import WHEN, doc_pointer

    later = WHEN + _dt.timedelta(days=30)
    ended = WHEN + _dt.timedelta(days=20)
    old = g.store.open_node_row("topic:release-trains")["label"]
    new = "Release Trains, renamed"
    handbook_ptr = doc_pointer(g.digests["handbook"], g.texts["handbook"], "release trains",
                               source="handbook", document_id="handbook/team.md")
    g.store.replace_node(GraphNode(id="topic:release-trains", kind=NodeKind("topic"), label=new,
                                   sources=[handbook_ptr], valid_from=ended, system_from=later),
                         at=later)
    g.store.close_node("topic:unlinked", at=later, valid_to=ended)
    return (WHEN + _dt.timedelta(days=10)).date().isoformat(), old, new


def test_a_rewound_drilldown_names_and_cites_the_historical_version(desk):
    """t08b #1/#3. The rewound drill-down printed TODAY's label and cited TODAY's pointers for a
    version that was replaced since — evidence that did not support the value shown."""
    g, client = desk
    inside, old, new = _rewind_topic(g)
    body = client.get(f"/intelligence/subgraph/topic:release-trains?as_of={inside}").json()
    root = next(n for n in body["nodes"] if n["id"] == "topic:release-trains")
    assert root["label"] == old and new not in json.dumps(body)
    cited = {p.get("source_id") for p in body["sources"]["topic:release-trains"]}
    assert cited == {"wiki"}, f"historical version cited with {cited}"
    # Today the same route names and cites the open version.
    now = client.get("/intelligence/subgraph/topic:release-trains").json()
    assert next(n for n in now["nodes"] if n["id"] == "topic:release-trains")["label"] == new


def test_a_historical_only_entity_resolves_in_its_own_window(desk):
    """t08b #3. The route resolved the token against the LIVE graph before loading the snapshot,
    so an entity that stood at `as_of` and is closed now was a 404."""
    g, client = desk
    inside, _old, _new = _rewind_topic(g)
    r = client.get(f"/intelligence/subgraph/topic:unlinked?as_of={inside}")
    assert r.status_code == 200, r.text
    assert client.get("/intelligence/subgraph/topic:unlinked").status_code == 404


def test_every_subgraph_relationship_carries_its_own_pointers(desk):
    """t08b #2. An edge is a fact ("A wrote B"); the drill-down emitted its kind and endpoints
    with pointers for the NODES only."""
    g, client = desk
    body = client.get("/intelligence/subgraph/team:platform").json()
    assert body["edges"], "the fixture links the team; nothing to check otherwise"
    edge_sources = body.get("edge_sources")
    assert edge_sources is not None, "no per-edge provenance in the response"
    for edge in body["edges"]:
        assert edge_sources.get(edge["id"]), f"edge {edge['id']} emitted without a pointer"


def test_kind_counts_include_a_kind_that_existed_only_at_the_requested_instant(desk):
    """t08b #4. `kind_counts` looked for anchors in the LIVE node table, so a kind whose every
    member closed since was dropped from a rewound answer."""
    import datetime as _dt

    from databridge_gateway.agent_tools import KindCounts, _GraphSnapshot, _ReadOnlyGraphView
    from generic_graph import WHEN

    g, _client = desk
    later, ended = WHEN + _dt.timedelta(days=30), WHEN + _dt.timedelta(days=20)
    topics = [r["id"] for r in g.store.open_nodes() if r["kind"] == "topic"]
    assert topics
    for t in topics:
        g.store.close_node(t, at=later, valid_to=ended)
    snap = _GraphSnapshot(_ReadOnlyGraphView(g.store), as_of=WHEN + _dt.timedelta(days=10))
    rows = KindCounts(snap).read().rows
    counted = {r.values["kind"]: r.values["count"] for r in rows}
    assert counted.get("topic") == len(topics)


def test_one_answer_reads_one_snapshot(desk):
    """t08b #5. The figures were cached at registry build, but `structural_edges()` re-read the
    store — one answer could report the old edge count and the new relationship."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import EdgeKind, GraphEdge

    from databridge_gateway.agent_tools import _GraphSnapshot, _ReadOnlyGraphView
    from generic_graph import WHEN

    g, _client = desk
    snap = _GraphSnapshot(_ReadOnlyGraphView(g.store))
    before = snap.analytics().edges
    ptr = SourcePointer(source_id="wiki", kind="doc_span", document_id="wiki/runbook.md",
                        char_start=0, char_end=5, capture_digest=f"sha256:{g.digests['wiki']}")
    g.store.add_edge(GraphEdge(id="topic:unlinked|part_of_team|team:platform",
                               src_id="topic:unlinked", dst_id="team:platform",
                               kind=EdgeKind("part_of_team"), sources=[ptr], valid_from=WHEN,
                               system_from=WHEN))
    ids = {e["id"] for e in snap.structural_edges()}
    assert "topic:unlinked|part_of_team|team:platform" not in ids
    assert len(snap.structural_edges()) <= before


def test_an_undeclared_kind_is_not_named_on_the_page(tmp_path, monkeypatch):
    """t08b #8. A node of a kind no bound pack declares is withheld as an entity, but its KIND
    NAME and count were still published in `kinds`. It is named nowhere; the violation is said."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import GraphNode, NodeKind
    from generic_graph import WHEN

    packs = write_packs(tmp_path)
    with bound(packs, "deskpack", "plainpack"):
        g = build_deskpack_graph(tmp_path)
        g.store.add_node(GraphNode(
            id="idea:stray", kind=NodeKind("idea"), label="stray idea",
            sources=[SourcePointer(source_id="lib", kind="table_cell", table="t", column="c",
                                   row_id="1")],
            valid_from=WHEN, system_from=WHEN))
    with bound(packs, "deskpack"):
        monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(g.capture_dir))
        monkeypatch.setenv("WALNUT_INGEST_DIR", str(g.ingest_dir))
        app = FastAPI()
        _register_intelligence_routes(app)
        app.state.graph_store = g.store
        with TestClient(app, headers=AUTH_HEADERS) as client:
            graph = _figures(client)["graph"]
    assert "idea" not in {k["kind"] for k in graph["kinds"]}
    assert "stray idea" not in json.dumps(graph)
    violation = graph.get("closed_vocabulary_violation")
    assert violation and violation["nodes"] == 1 and violation["kinds"] == 1


def test_graph_bounds_are_checked_against_the_requested_snapshot(desk, monkeypatch):
    """t08b #9. The bound was checked against the LIVE graph: a rewind to an instant with more
    nodes than today passed, and a small historical snapshot could be refused for today's size."""
    import databridge_gateway.intelligence_surface as surface

    g, client = desk
    inside, _old, _new = _rewind_topic(g)
    live = len(g.store.open_nodes())
    monkeypatch.setattr(surface, "MAX_GRAPH_NODES", live)       # today fits exactly
    assert client.get("/intelligence").json()["graph_unavailable"] is None
    then = client.get(f"/intelligence?as_of={inside}").json()
    assert then["graph"] is None and then["graph_unavailable"], (
        "the rewound snapshot holds one node more than the bound and must be refused")


def test_a_dangling_edge_neither_crashes_the_ask_nor_hides_in_the_figures(desk):
    """E2E F-03. An edge whose endpoint node does not exist (a foreign key merged away at
    ingest) made `POST /intelligence/ask` a 503 (KeyError in `analytics.compute`). It is skipped
    and COUNTED on the page, and the agent still answers over the rest of the graph."""
    from databridge_core import SourcePointer
    from databridge_graph.schema import EdgeKind, GraphEdge
    from generic_graph import WHEN

    g, client = desk
    ptr = SourcePointer(source_id="wiki", kind="doc_span", document_id="wiki/runbook.md",
                        char_start=0, char_end=5, capture_digest=f"sha256:{g.digests['wiki']}")
    g.store.add_edge(GraphEdge(id="team:platform|part_of_team|team:merged-away",
                               src_id="team:platform", dst_id="team:merged-away",
                               kind=EdgeKind("part_of_team"), sources=[ptr], valid_from=WHEN,
                               system_from=WHEN))
    present = {r["id"] for r in g.store.open_nodes()}
    dangling = [e for e in g.store.open_edges()
                if e["src_id"] not in present or e["dst_id"] not in present]
    assert dangling, "the fixture wrote no dangling edge; nothing to test"
    graph = _figures(client)["graph"]
    assert graph["dangling_edges"] == len(dangling)
    assert graph["edges"] == len(g.store.open_edges()) - len(dangling)
    r = client.post("/intelligence/ask",
                    json={"question": 'what are the neighbours of "Platform Team"?'})
    assert r.status_code == 200, r.text
    assert "merged-away" not in json.dumps(r.json())
