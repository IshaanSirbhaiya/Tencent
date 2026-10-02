"""C-4 / C-5 (finance dry run): the /intelligence figures are EXACT at any graph size, and the
queue card exists only where a queue does.

C-4. The count cards withheld every figure once the graph passed 5,000 nodes ("Graph figures
unavailable"); the finance run's graph held 10,128. The figures are now counted in the store with
SQL aggregates (`intelligence_counts.store_figures`) past the detail envelope, and must agree,
number for number, with `analytics.compute` — on TWO estates built from the repo's fixtures:

* **workspace** — the Larkspur estate (`fixtures/workspace_demo/db`), under the workspace pack;
* **finance** — `fixtures/packs/finance`, through the pack's own seed map.

Every expectation is DERIVED: from `analytics.compute` over the same store, or from the store's
own rows. The envelope used to force the large-graph path is set from the graph's size, never
written down.

C-5. A deployment that mounts no intake queue gets no queue card and no failure reason for it.
"""
from __future__ import annotations

import datetime as dt
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_core import SourcePointer
from databridge_graph.analytics import compute
from databridge_graph.packs import deployment
from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind
from databridge_graph.store import GraphStore

from databridge_gateway.intelligence_counts import MEMBERS_NOT_LISTED, store_figures
from databridge_gateway.intelligence_routes import _register_intelligence_routes
from databridge_gateway.intelligence_surface import COUNT_FIGURES

from conftest import AUTH_HEADERS

ROOT = Path(__file__).resolve().parents[3]                          # databridge/
LARKSPUR_SQL = ROOT / "fixtures" / "workspace_demo" / "db" / "01_schema_and_data.sql"
WHEN = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
#: Rows per table / dataset. Small enough to keep the suite fast; the figures are compared, not
#: fixed, so the number is not an expectation.
SAMPLE = 30


# --- the two estates ------------------------------------------------------------------------
def _larkspur_tables() -> dict[str, list[dict]]:
    """Every table of the Larkspur dump, read with the engine's own SQL-dump reader."""
    from databridge_graph.estate_seed import read_sql_insert
    import tempfile

    text = LARKSPUR_SQL.read_text(encoding="utf-8")
    out: dict[str, list[dict]] = {}
    for chunk in re.split(r"(?=INSERT INTO )", text)[1:]:
        table = re.match(r"INSERT INTO\s+(\w+)", chunk).group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(chunk)
        try:
            out[table] = list(read_sql_insert(Path(fh.name), max_rows=SAMPLE))
        finally:
            Path(fh.name).unlink()
    return out


def _cell(table: str, column: str, row_id, source: str = "larkspur-db") -> SourcePointer:
    return SourcePointer(source_id=source, kind="table_cell", table=table, column=column,
                         row_id=str(row_id))


def _node(kind: str, key, label: str, *ptrs: SourcePointer) -> GraphNode:
    return GraphNode(id=f"{kind}:{key}", kind=NodeKind(kind), label=str(label), sources=list(ptrs),
                     valid_from=WHEN, system_from=WHEN)


def _edge(kind: str, src: str, dst: str, ptr: SourcePointer) -> GraphEdge:
    return GraphEdge(id=f"{src}|{kind}|{dst}", src_id=src, dst_id=dst, kind=EdgeKind(kind),
                     sources=[ptr], valid_from=WHEN, system_from=WHEN)


def build_workspace_estate(store: GraphStore) -> None:
    """The Larkspur tables as workspace-pack entities. Invoices cite the DB row AND a derived
    pointer whose leaf is a second source (the ledger export), so the provenance figures have a
    multi-source entity to count; edges to projects outside the sample are kept, so the dangling
    count is exercised too."""
    t = _larkspur_tables()
    for r in t["employees"]:
        store.add_node(_node("person", r["employee_id"], r["full_name"],
                             _cell("employees", "employee_id", r["employee_id"])))
    for r in t["customers"]:
        store.add_node(_node("organisation", r["customer_id"], r["name"],
                             _cell("customers", "customer_id", r["customer_id"])))
    for r in t["projects"]:
        pid = f"project:{r['project_id']}"
        store.add_node(_node("project", r["project_id"], r["name"],
                             _cell("projects", "project_id", r["project_id"])))
        ptr = _cell("projects", "customer_id", r["project_id"])
        store.add_edge(_edge("party_to", f"organisation:{r['customer_id']}", pid, ptr))
        store.add_edge(_edge("manages", f"person:{r['manager_id']}", pid,
                             _cell("projects", "manager_id", r["project_id"])))
    for r in t["invoices"]:
        inv = r["invoice_no"]
        row = _cell("invoices", "invoice_no", inv)
        ledger = SourcePointer(source_id="larkspur-ledger", kind="derived",
                               derivation="ledger export of the invoice row",
                               derived_from=[_cell("ledger", "invoice_no", inv,
                                                   source="larkspur-ledger")])
        store.add_node(_node("invoice", inv, inv, row, ledger))
        store.add_edge(_edge("billed_to", f"invoice:{inv}", f"organisation:{r['customer_id']}",
                             _cell("invoices", "customer_id", inv)))
        store.add_edge(_edge("part_of", f"project:{r['project_id']}", f"invoice:{inv}",
                             _cell("invoices", "project_id", inv)))
        store.add_edge(_edge("mentions", f"invoice:{inv}", f"project:{r['project_id']}",
                             _cell("invoices", "project_id", inv)))
    # Contact notes: documents an employee wrote (an ISSUED edge, so an issuer mix), each naming
    # a contact row this estate does not load as a node — a reference to an unloaded row, which
    # the figures must count as dangling rather than crash on or drop silently.
    # `note_id` is SERIAL: the dump does not carry it, so a note's id is its ordinal (what the
    # serial assigns on load).
    for note_id, r in enumerate(t["contact_notes"], start=1):
        doc = f"document:note-{note_id}"
        store.add_node(_node("document", f"note-{note_id}", f"note {note_id}",
                             _cell("contact_notes", "note_id", note_id)))
        store.add_edge(_edge("authored", f"person:{r['written_by']}", doc,
                             _cell("contact_notes", "written_by", note_id)))
        store.add_edge(_edge("mentions", doc, f"contact:{r['contact_id']}",
                             _cell("contact_notes", "contact_id", note_id)))


def build_finance_estate(store: GraphStore) -> None:
    from databridge_graph.finance_seed import load_finance_seed, non_watchlist_dataset_ids

    load_finance_seed(store, max_rows_per_dataset=SAMPLE, only=non_watchlist_dataset_ids())


ESTATES = {"workspace": ("workspace", build_workspace_estate),
           "finance": ("finance", build_finance_estate)}


@contextmanager
def estate(name: str):
    pack, build = ESTATES[name]
    with deployment(pack):
        store = GraphStore()
        build(store)
        yield store


def _numbers(payload: dict) -> dict:
    """Everything but member lists and the entity table: the numbers, labels and rankings."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items()
                    if k not in ("members", "entities", "members_withheld")}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip({k: v for k, v in payload.items()
                  if k not in ("withholding", "members_listed", "members_note",
                               "closed_vocabulary_violation")})


def _client(store: GraphStore) -> TestClient:
    app = FastAPI()
    _register_intelligence_routes(app)
    app.state.graph_store = store
    return TestClient(app, headers=AUTH_HEADERS)


# --- C-4: the store's counts ARE compute's counts ---------------------------------------------
@pytest.mark.parametrize("name", sorted(ESTATES))
def test_store_counts_equal_the_in_memory_figures(name):
    with estate(name) as store:
        want = compute(store).to_dict()
        got = store_figures(store)
    # Non-vacuous: a real graph, with relationships and at least one non-zero figure.
    assert want["nodes"] > 0 and want["edges"] > 0
    assert any((want.get(k) or {}).get("n") for k in COUNT_FIGURES)
    for key in want:
        if key in ("entities",):
            continue
        assert _numbers({key: got.get(key)}) == _numbers({key: want[key]}), key


def test_the_two_estates_exercise_different_figures():
    """The comparison above is only as good as what the estates contain. Each estate has a
    multi-source entity and a dangling reference, one has an issuer mix; between them at least three figures
    are non-zero, and their kind tables differ."""
    seen = {}
    for name in ESTATES:
        with estate(name) as store:
            seen[name] = compute(store).to_dict()
    for name, p in seen.items():
        assert p["nodes"] > 0 and p["edges"] > 0, name
        assert p["cross_source"]["n"] > 0, name          # a multi-source entity in each
        assert p["dangling_edges"] > 0, name             # a reference to an unloaded row in each
    assert any(p["issuer_mix"] for p in seen.values())   # an issued relationship somewhere
    nonzero = {k for p in seen.values() for k in COUNT_FIGURES if (p.get(k) or {}).get("n")}
    assert len(nonzero) >= 3, nonzero
    # The estates differ: their kind tables have nothing in common but the shape.
    kinds = [{k["kind"] for k in p["kinds"]} for p in seen.values()]
    assert kinds[0] != kinds[1]


# --- C-4: past the envelope the page still shows every figure, exact ----------------------------
@pytest.mark.parametrize("name", sorted(ESTATES))
def test_figures_past_the_envelope_are_exact_not_withheld(name, monkeypatch):
    import databridge_gateway.intelligence_surface as surface

    with estate(name) as store:
        want = compute(store).to_dict()
        n_nodes = len(store.open_nodes())
        # The envelope is set from THIS graph: one node smaller than it.
        monkeypatch.setattr(surface, "MAX_GRAPH_NODES", n_nodes - 1)
        with _client(store) as c:
            body = c.get("/intelligence").json()
    assert body["graph_unavailable"] is None, body["graph_unavailable"]
    graph = body["graph"]
    assert graph["members_listed"] is False
    assert graph["nodes"] == want["nodes"] and graph["edges"] == want["edges"]
    assert graph["dangling_edges"] == want["dangling_edges"]
    for key in COUNT_FIGURES:
        if want.get(key) is None:
            assert graph.get(key) is None, key
            continue
        assert (graph[key]["n"], graph[key]["of"]) == (want[key]["n"], want[key]["of"]), key
        assert graph[key]["members"] == []
        if want[key]["n"]:
            assert graph[key]["members_withheld"], key
    assert ({k["kind"]: k["n"] for k in graph["kinds"]}
            == {k["kind"]: k["n"] for k in want["kinds"] if k["declared"]})
    assert MEMBERS_NOT_LISTED in {graph[k].get("members_withheld") for k in COUNT_FIGURES
                                  if graph.get(k)}


@pytest.mark.parametrize("name", sorted(ESTATES))
def test_within_the_envelope_the_members_are_still_listed(name, monkeypatch):
    import databridge_gateway.intelligence_surface as surface

    with estate(name) as store:
        n_nodes = len(store.open_nodes())
        n_edges = len(store.open_edges())
        monkeypatch.setattr(surface, "MAX_GRAPH_NODES", n_nodes)
        monkeypatch.setattr(surface, "MAX_GRAPH_EDGES", n_edges)
        with _client(store) as c:
            graph = c.get("/intelligence").json()["graph"]
    assert graph["members_listed"] is True
    assert any(graph[k]["members"] for k in COUNT_FIGURES if graph.get(k))


@pytest.mark.parametrize("name", sorted(ESTATES))
def test_a_rewound_snapshot_past_the_envelope_is_counted_exactly(name, monkeypatch):
    """The store's SQL counts the OPEN graph; a rewind past the envelope is counted in memory —
    exactly — and loses only its member lists."""
    import databridge_gateway.intelligence_surface as surface

    as_of = WHEN + dt.timedelta(days=400)
    with estate(name) as store:
        want = compute(store, as_of=as_of).to_dict()
        monkeypatch.setattr(surface, "MAX_GRAPH_NODES", 0)
        with _client(store) as c:
            body = c.get("/intelligence", params={"as_of": as_of.date().isoformat()}).json()
    assert body["graph_unavailable"] is None
    assert body["graph"]["members_listed"] is False
    assert want["nodes"] > 0
    assert body["graph"]["nodes"] == want["nodes"] and body["graph"]["edges"] == want["edges"]


# --- C-5: no queue mounted, no queue card -------------------------------------------------------
@pytest.mark.parametrize("name", sorted(ESTATES))
def test_a_deployment_without_an_intake_queue_reports_no_queue_and_no_failure(name):
    with estate(name) as store:
        with _client(store) as c:
            body = c.get("/intelligence").json()
    assert body["queue_mounted"] is False
    assert body["queue"] is None and body["queue_unavailable"] is None
    assert body["graph"] is not None                       # the rest of the page is unaffected


def test_a_mounted_queue_is_still_reported_and_a_broken_one_still_says_why():
    with estate("workspace") as store:
        app = FastAPI()
        _register_intelligence_routes(app)
        app.state.graph_store = store
        app.state.intakes = {}
        with TestClient(app, headers=AUTH_HEADERS) as c:
            body = c.get("/intelligence").json()
        assert body["queue_mounted"] is True and body["queue"] is not None

        class Exploding(dict):
            def items(self):
                raise RuntimeError("queue exploded")

        app.state.intakes = Exploding()
        with TestClient(app, headers=AUTH_HEADERS) as c:
            body = c.get("/intelligence").json()
        assert body["queue_mounted"] is True
        assert body["queue"] is None and body["queue_unavailable"]
