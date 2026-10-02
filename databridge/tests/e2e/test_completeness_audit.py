"""The completeness audit (`scripts/e2e/completeness.py`), offline, on BOTH estates.

`test_phase_b_e2e.py` runs only against a completed run directory, so a normal test run never
exercises the audit. This file does: it builds a small graph from each estate's own fixture rows
(the finance seed through the production loader; the workspace/Larkspur invoices of E2E run 4),
writes the captured snapshots and the run ledger exactly where an ingest run writes them, and
checks that the audit

* passes a complete build,
* names a declared FK whose edges were not built — the finance dry run's C-1,
* names a table captured short of the source's own row count — C-2 — and the references that
  the short capture leaves unresolved,
* names a column whose values are another source's keys with no edge between them — C-3.

Every expected number is derived from the fixture rows; none is written down.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "e2e"))

AT = datetime(2026, 10, 1, 17, 8, 19, tzinfo=timezone.utc)
_ROWS: dict[str, tuple[list[dict], list[dict]]] = {}


@dataclass(frozen=True)
class Estate:
    name: str
    pack: str
    source: str
    parent: tuple[str, str, str]           # (schema.table, pk, node kind)
    child: tuple[str, str, str]
    fk: str                                # child column -> parent pk
    edge: tuple[str, bool]                 # (edge kind, child is the edge's src)
    rows: Callable[[Path], tuple[list[dict], list[dict]]]


def _finance(tmp: Path):
    from databridge_adapter.testing.estate import load_finance_estate
    from sqlalchemy import text

    engine = load_finance_estate(tmp / "estate", tables=("01_party_01.sql", "05_facility_01.sql"))
    with engine.connect() as conn:
        facilities = [dict(r._mapping) for r in conn.execute(text(
            "SELECT facility_id, party_id, facility_type, status FROM core_banking.facility "
            "ORDER BY facility_id LIMIT 20"))]
        wanted = sorted({f["party_id"] for f in facilities})
        parties = [dict(r._mapping) for r in conn.execute(text(
            "SELECT party_id, legal_name FROM core_banking.party ORDER BY party_id"))
            if r._mapping["party_id"] in wanted]
    engine.dispose()
    return parties, facilities


def _workspace(_tmp: Path):
    """The Larkspur database (`fixtures/workspace_demo/db`), loaded into SQLite up to its role
    grants (Postgres-only); the tables and rows are the fixture's own."""
    import re
    import sqlite3

    sql = (ROOT / "fixtures" / "workspace_demo" / "db" / "01_schema_and_data.sql").read_text("utf-8")
    cut = re.search(r"(?m)^\s*(CREATE ROLE|GRANT|ALTER|REVOKE)\b", sql)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(re.sub(r"\bSERIAL\b", "INTEGER", sql[:cut.start()] if cut else sql))
    customers = [dict(r) for r in conn.execute("SELECT customer_id, name FROM customers "
                                               "ORDER BY customer_id")]
    invoices = [dict(r) for r in conn.execute("SELECT invoice_no, customer_id, status "
                                              "FROM invoices ORDER BY invoice_no")]
    conn.close()
    return customers, invoices


ESTATES = [
    Estate("finance", "finance", "finance_core_banking",
           ("core_banking.party", "party_id", "party"),
           ("core_banking.facility", "facility_id", "facility"), "party_id",
           ("borrower_of", False), _finance),
    Estate("workspace", "workspace", "larkspur_db",
           ("public.customers", "customer_id", "organisation"),
           ("public.invoices", "invoice_no", "invoice"), "customer_id",
           ("billed_to", True), _workspace),
]


def _put(cap: Path, ingest: Path, source: str, table: str, pk: str, rows: list[dict],
         *, total: int, fks: list[dict]) -> str:
    schema, name = table.split(".")
    snap = {"schema": schema, "table": name, "primary_key": [pk], "foreign_keys": fks,
            "row_count_total": total, "limit": len(rows),
            "columns": [{"name": c} for c in rows[0]], "rows": rows}
    data = json.dumps(snap, sort_keys=True, default=str).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    (cap / sha[:2]).mkdir(parents=True, exist_ok=True)
    (cap / sha[:2] / sha).write_bytes(data)
    run = ingest / source / "20261001T170819Z-test"
    run.mkdir(parents=True, exist_ok=True)
    with (run / "captured.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "table", "sha256": sha, "uri": table, "name": name,
                            "at": AT.isoformat()}) + "\n")
    return sha


def _build(tmp: Path, est: Estate, *, cap_short: int = 0, drop_edges: int = 0,
           second_source: bool = False):
    """Capture + graph for the estate. `cap_short` captures that many child rows fewer (the
    source still reports its full count); `drop_edges` leaves that many FK edges unbuilt;
    `second_source` adds a table in ANOTHER source whose column holds the parent keys."""
    from databridge_core import SourcePointer
    from databridge_graph import GraphStore
    from databridge_graph.packs import deployment
    from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

    if est.name not in _ROWS:                  # the fixture rows never change within a session
        _ROWS[est.name] = est.rows(tmp)
    parents, children = (list(x) for x in _ROWS[est.name])
    cap, ingest = tmp / "capture", tmp / "ingest"
    (ptable, ppk, pkind), (ctable, cpk, ckind) = est.parent, est.child
    captured_children = children[:len(children) - cap_short]
    psha = _put(cap, ingest, est.source, ptable, ppk, parents, total=len(parents), fks=[])
    ref_schema, ref_table = ptable.split(".")
    csha = _put(cap, ingest, est.source, ctable, cpk, captured_children, total=len(children),
                fks=[{"column": est.fk, "ref_schema": ref_schema, "ref_table": ref_table,
                      "ref_column": ppk}])
    mirror = []
    if second_source:
        mirror = [{"ext_id": f"X-{i:04d}", "external_ref": p[ppk]} for i, p in enumerate(parents)]
        _put(cap, ingest, "other_system", "ext.mirror", "ext_id", mirror, total=len(mirror),
             fks=[])

    def ptr(source, table, col, rid, sha):
        return SourcePointer(source_id=source, kind="table_cell", table=table, column=col,
                             row_id=str(rid), capture_digest=sha)

    stamp = {"valid_from": AT, "system_from": AT}
    with deployment(est.pack):
        store = GraphStore(str(tmp / "graph.db"))
        for p in parents:
            store.add_node(GraphNode(id=f"{pkind}:{p[ppk].lower()}", kind=NodeKind(pkind),
                                     label=str(p[ppk]),
                                     sources=(ptr(est.source, ptable, ppk, p[ppk], psha),),
                                     **stamp))
        keys = {p[ppk] for p in parents}
        built = 0
        for c in captured_children:
            cid = f"{ckind}:{c[cpk].lower()}"
            store.add_node(GraphNode(id=cid, kind=NodeKind(ckind), label=str(c[cpk]),
                                     sources=(ptr(est.source, ctable, cpk, c[cpk], csha),),
                                     **stamp))
            if c[est.fk] in keys and built >= drop_edges:
                pid = f"{pkind}:{c[est.fk].lower()}"
                src, dst = (cid, pid) if est.edge[1] else (pid, cid)
                store.add_edge(GraphEdge(
                    id=f"{src}|{est.edge[0]}|{dst}", src_id=src, dst_id=dst,
                    kind=EdgeKind(est.edge[0]),
                    sources=(ptr(est.source, ctable, est.fk, c[cpk], csha),), **stamp))
            built += 1
        store.close()
    return parents, children, captured_children, mirror


def _audit(tmp: Path, est: Estate) -> dict:
    import completeness

    return completeness.audit(tmp / "graph.db", tmp / "ingest", tmp / "capture",
                              bound_packs=[est.pack], packs_dir=ROOT / "packs")


@pytest.fixture(params=ESTATES, ids=lambda e: e.name)
def estate(request):
    return request.param


def test_a_complete_build_passes(tmp_path, estate):
    parents, children, _, _ = _build(tmp_path, estate)
    out = _audit(tmp_path, estate)
    assert out["passed"], out["checks"]
    fk = out["foreign_keys"][0]
    keys = {p[estate.parent[1]] for p in parents}
    assert fk["edges_expected"] == fk["edges_built"] == sum(1 for c in children
                                                            if c[estate.fk] in keys) > 0


def test_unbuilt_declared_fk_edges_are_named_with_their_count(tmp_path, estate):
    """C-1: the declared FK exists, the edges do not."""
    parents, children, _, _ = _build(tmp_path, estate, drop_edges=2)
    out = _audit(tmp_path, estate)
    keys = {p[estate.parent[1]] for p in parents}
    expected = sum(1 for c in children if c[estate.fk] in keys)
    fk = out["foreign_keys"][0]
    assert (fk["edges_expected"], fk["edges_built"]) == (expected, expected - 2)
    assert not out["checks"]["declared_fks_built"]["passed"]
    # the two children without their edge are isolated, and the declared keys predicted otherwise
    assert out["isolation"]["isolated"] > out["isolation"]["predicted_isolated"]
    assert not out["checks"]["isolation_within_prediction"]["passed"]


def test_a_capped_capture_is_named_against_the_sources_own_count(tmp_path, estate):
    """C-2: the source reports N rows; the capture holds fewer."""
    parents, children, captured, _ = _build(tmp_path, estate, cap_short=1)
    out = _audit(tmp_path, estate)
    row = next(t for t in out["tables"] if t["table"] == estate.child[0])
    assert (row["source_rows"], row["captured_rows"]) == (len(children), len(captured))
    assert row["capped"] and not out["checks"]["rows_complete"]["passed"]


def test_values_that_are_another_sources_keys_are_named_when_unlinked(tmp_path, estate):
    """C-3: no FK is declared across databases, but the values say there is a link."""
    _, _, _, mirror = _build(tmp_path, estate, second_source=True)
    out = _audit(tmp_path, estate)
    import completeness

    if len({m["external_ref"] for m in mirror}) < completeness.XREF_MIN_VALUES:
        assert out["checks"]["cross_source_links"]["passed"]      # too few values to infer
        return
    assert any(x["from"].endswith("ext.mirror.external_ref") and
               x["to"] == f"{estate.source}:{estate.parent[0]}" and not x["rows_linked_in_graph"]
               for x in out["cross_source"]), out["cross_source"]
    assert not out["checks"]["cross_source_links"]["passed"]


def test_the_foreign_vocabulary_terms_come_from_the_unbound_packs():
    """The audit names no domain: the terms are the subject kinds the OTHER packs declare."""
    import completeness
    import yaml

    for bound in ("finance", "workspace"):
        terms = completeness.foreign_terms(ROOT / "packs", [bound])
        assert terms, bound
        own = yaml.safe_load((ROOT / "packs" / bound / "ontology.yaml").read_text("utf-8"))
        own_kinds = {k["name"] if isinstance(k, dict) else k for k in own["node_kinds"]}
        assert not terms & own_kinds
