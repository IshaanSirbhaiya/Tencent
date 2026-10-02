"""Completeness audit: did the graph receive what the source holds? (read-only)

    python scripts/e2e/completeness.py [--estate workspace|finance] [--dir <run dir>]
                                       [--answers <dir of captured HTTP responses>] [label]
        -> <run>/artefacts/completeness[_label].json, and exit 1 when a check fails

**Why this exists** (``wiki/40-reviews/LESSON-2026-10-02-completeness.md``). Four live E2E runs on
the workspace estate checked that every value the graph HOLDS cites a source that re-verifies.
None checked that the graph holds what the source HAS. The finance dry run then found declared
foreign keys that became no edges (1,000 interactions and 700 opportunities, all isolated), a
1,000-row cap per table (txn: 24,132 rows, 1,000 nodes), and cross-database links nobody proposed.
Each of those is a COUNT that disagrees with the source; none of them is a wrong value, so a
pointer audit cannot see them.

Every check compares two numbers the run itself produced, and every threshold is DERIVED from the
estate, never written down:

1. **rows**: per captured table, ``row_count_total`` (the source's own count) vs the rows captured
   vs the distinct source rows the graph's nodes cite. A table captured short is the row cap.
2. **declared FKs**: per declared foreign key, the rows whose FK value names a captured target row
   (an edge is EXPECTED) vs the rows whose node actually has an edge to that target's node.
3. **isolation**: the share of table-row nodes with no edge, against the share the declared FKs
   predict (a row that neither references nor is referenced by a captured row). Observed above
   predicted fails.
4. **unresolved references**: FK values whose target row was not captured (row cap, a table out
   of scope, or a dangling value in the source), counted per FK.
5. **cross-source links**: a column in one source whose values are (almost all) the key of a table
   in ANOTHER source, with no edge between the two tables' nodes. Reported as a proposal gap.
6. **foreign vocabulary** (optional, ``--answers``): the subject kinds of packs NOT bound to this
   deployment, found in captured responses (a "patient" in a finance answer).

When the ingest run report carries its own ``completeness`` block (the TAB6 track), it is read and
cross-checked against this independent count; this audit never depends on it.

The graph is opened ``mode=ro``: opening it read-write folds the WAL and changes the evidence.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: A cross-source candidate: at least this share of a column's distinct values are keys of a table
#: in another source, over at least `XREF_MIN_VALUES` distinct values. A rule over the data.
XREF_MIN_SHARE = 0.8
XREF_MIN_VALUES = 5


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _json_lines(path: Path) -> Iterable[dict]:
    if not path.is_file():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def captured_tables(ingest_dir: Path, capture_dir: Path) -> dict[tuple[str, str], dict]:
    """The LATEST captured snapshot of every (source, schema.table), from the runs' own ledgers."""
    latest: dict[tuple[str, str], tuple[str, dict]] = {}
    for source_dir in sorted(p for p in Path(ingest_dir).iterdir() if p.is_dir()):
        for run in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            for item in _json_lines(run / "captured.jsonl"):
                if item.get("kind") != "table" or not item.get("sha256"):
                    continue
                key = (source_dir.name, item.get("uri") or item.get("name") or "")
                if key in latest and latest[key][0] >= (item.get("at") or ""):
                    continue
                sha = item["sha256"]
                blob = Path(capture_dir) / sha[:2] / sha
                if not blob.is_file():
                    continue
                try:
                    snap = json.loads(blob.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(snap, dict) and "rows" in snap:
                    latest[key] = (item.get("at") or "", {**snap, "_run": run.name})
    return {k: v for k, (_, v) in latest.items()}


def _row_id(snap: dict, row: dict) -> Optional[str]:
    """The row id the ingest cites (`table_cell.row_id`), rendered by the ingest's OWN function so
    the two can never drift apart."""
    try:
        from databridge_mcp_ingest.build import _render_pk
    except ImportError:                                           # pragma: no cover - path setup
        _render_pk = None
    pk = list(snap.get("primary_key") or [])
    if _render_pk is not None:
        return _render_pk(row, pk)
    vals = [row.get(c) for c in pk]
    return None if not pk or any(v is None for v in vals) else ",".join(map(str, vals))


def is_association(snap: dict) -> bool:
    """A join table: two or more declared FKs, and its key is made of FK columns only. Its rows
    are expected to become EDGES between the referenced rows, not nodes of their own."""
    fks = snap.get("foreign_keys") or []
    pk = set(snap.get("primary_key") or [])
    return len(fks) >= 2 and bool(pk) and pk <= {fk.get("column") for fk in fks}


def _graph_index(db: Path) -> dict[str, Any]:
    """Believed nodes/edges, and which table rows each node cites (table_cell pointers)."""
    c = _ro(db)
    try:
        nodes = {r["id"]: r["kind"] for r in c.execute(
            "SELECT id, kind FROM nodes WHERE system_to IS NULL AND valid_to IS NULL")}
        adj: dict[str, set[str]] = collections.defaultdict(set)
        edges = 0
        for r in c.execute("SELECT src_id, dst_id FROM edges "
                           "WHERE system_to IS NULL AND valid_to IS NULL"):
            if r["src_id"] in nodes and r["dst_id"] in nodes:
                adj[r["src_id"]].add(r["dst_id"])
                adj[r["dst_id"]].add(r["src_id"])
                edges += 1
        row_nodes: dict[tuple[str, str, str], set[str]] = collections.defaultdict(set)
        for r in c.execute("SELECT entity_id, source_pointer_json FROM sources"):
            if r["entity_id"] not in nodes:
                continue
            try:
                p = json.loads(r["source_pointer_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if p.get("kind") == "table_cell" and p.get("row_id") is not None:
                row_nodes[(p.get("source_id") or "", p.get("table") or "",
                           str(p["row_id"]))].add(r["entity_id"])
    finally:
        c.close()
    return {"nodes": nodes, "adj": adj, "edges": edges, "row_nodes": row_nodes}


def _connected(g: dict, a: set[str], b: set[str]) -> bool:
    return any(g["adj"].get(x, set()) & b for x in a)


def audit(graph_db: Path, ingest_dir: Path, capture_dir: Path, *,
          answers_dir: Optional[Path] = None, bound_packs: Iterable[str] = (),
          packs_dir: Optional[Path] = None) -> dict:
    tables = captured_tables(ingest_dir, capture_dir)
    g = _graph_index(graph_db)
    out: dict[str, Any] = {"graph": str(graph_db), "nodes": len(g["nodes"]),
                           "edges": g["edges"], "tables": [], "foreign_keys": [],
                           "cross_source": [], "checks": {}}

    def nodes_of(source: str, table: str, rid: str) -> set[str]:
        return g["row_nodes"].get((source, table, rid), set())

    # --- 1. rows ------------------------------------------------------------------------------
    keys_of: dict[tuple[str, str], dict[str, dict]] = {}
    for (source, uri), snap in sorted(tables.items()):
        rows = snap.get("rows") or []
        by_id = {}
        for row in rows:
            rid = _row_id(snap, row)
            if rid is not None:
                by_id[rid] = row
        keys_of[(source, uri)] = by_id
        assoc = is_association(snap)
        if assoc:
            fks = snap.get("foreign_keys") or []
            a, b = fks[0], fks[1]

            def target(fk, val):
                ref = ".".join(x for x in (fk.get("ref_schema"), fk.get("ref_table")) if x)
                return nodes_of(source, ref, str(val))

            in_graph = sum(1 for row in by_id.values()
                           if _connected(g, target(a, row.get(a["column"])),
                                         target(b, row.get(b["column"]))))
        else:
            in_graph = sum(1 for rid in by_id if nodes_of(source, uri, rid))
        total = snap.get("row_count_total")
        out["tables"].append({
            "source": source, "table": uri, "source_rows": total, "captured_rows": len(rows),
            "capture_limit": snap.get("limit"), "graph_rows": in_graph,
            "association": assoc,
            "capped": isinstance(total, int) and len(rows) < total,
            "dropped_in_build": len(by_id) - in_graph,
        })

    # --- 2/3/4. declared FKs, isolation, unresolved ---------------------------------------------
    expected_connected: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for (source, uri), snap in sorted(tables.items()):
        if is_association(snap):
            continue                    # measured above, as edges between the referenced rows
        for fk in snap.get("foreign_keys") or []:
            col = fk.get("column")
            ref = ".".join(x for x in (fk.get("ref_schema"), fk.get("ref_table")) if x)
            target = keys_of.get((source, ref))
            expected = built = unresolved = 0
            missing: list[str] = []
            for rid, row in keys_of[(source, uri)].items():
                val = row.get(col)
                if val is None or val == "":
                    continue
                if target is None or str(val) not in target:
                    unresolved += 1
                    continue
                expected += 1
                expected_connected[(source, uri)].add(rid)
                expected_connected[(source, ref)].add(str(val))
                if _connected(g, nodes_of(source, uri, rid), nodes_of(source, ref, str(val))):
                    built += 1
                elif len(missing) < 5:
                    missing.append(rid)
            out["foreign_keys"].append({
                "source": source, "table": uri, "column": col, "references": ref,
                "target_captured": target is not None, "edges_expected": expected,
                "edges_built": built, "unresolved_references": unresolved,
                "missing_examples": missing})

    observed_isolated = predicted_isolated = table_nodes = 0
    for (source, uri), snap in sorted(tables.items()):
        if not is_association(snap):
            continue
        for fk in snap.get("foreign_keys") or []:
            ref = ".".join(x for x in (fk.get("ref_schema"), fk.get("ref_table")) if x)
            for row in keys_of[(source, uri)].values():
                if row.get(fk.get("column")) not in (None, ""):
                    expected_connected[(source, ref)].add(str(row[fk["column"]]))
    for t in out["tables"]:
        source, uri = t["source"], t["table"]
        if t["association"]:
            continue
        iso = pred = n = 0
        for rid in keys_of[(source, uri)]:
            ns = nodes_of(source, uri, rid)
            if not ns:
                continue
            n += 1
            if not any(g["adj"].get(x) for x in ns):
                iso += 1
            if rid not in expected_connected.get((source, uri), set()):
                pred += 1
        t.update(graph_nodes=n, isolated=iso, predicted_isolated=pred)
        observed_isolated += iso
        predicted_isolated += pred
        table_nodes += n

    # --- 5. cross-source links ----------------------------------------------------------------
    pk_values: dict[tuple[str, str], set[str]] = {k: set(v) for k, v in keys_of.items() if v}
    for (source, uri), snap in sorted(tables.items()):
        rows = list(keys_of[(source, uri)].items())
        if not rows:
            continue
        declared = {fk.get("column") for fk in snap.get("foreign_keys") or []}
        pk = set(snap.get("primary_key") or [])
        for col in sorted({c for _, r in rows for c in r} - declared - pk):
            vals = {str(r[col]) for _, r in rows if r.get(col) not in (None, "")}
            if len(vals) < XREF_MIN_VALUES:
                continue
            for (other, ouri), keys in sorted(pk_values.items()):
                if other == source:
                    continue
                share = len(vals & keys) / len(vals)
                if share < XREF_MIN_SHARE:
                    continue
                linked = sum(1 for rid, r in rows if r.get(col) not in (None, "")
                             and str(r[col]) in keys
                             and _connected(g, nodes_of(source, uri, rid),
                                            nodes_of(other, ouri, str(r[col]))))
                out["cross_source"].append({
                    "from": f"{source}:{uri}.{col}", "to": f"{other}:{ouri}",
                    "value_share": round(share, 3), "values": len(vals),
                    "rows_linked_in_graph": linked})

    # --- 6. foreign vocabulary ------------------------------------------------------------------
    vocab_hits: list[dict] = []
    if answers_dir is not None and packs_dir is not None:
        terms = foreign_terms(packs_dir, bound_packs)
        # A word the estate's OWN captured data uses is the estate's word, not another pack's
        # leaking in ("the party must hold insurance" in a workspace contract). Derived per run.
        own_words = _captured_words(capture_dir, terms)
        excused = sorted(terms & own_words)
        terms = terms - own_words
        if terms:
            pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(terms))) + r")s?\b", re.I)
            for f in sorted(Path(answers_dir).glob("*.json")):
                text = f.read_text("utf-8", errors="replace")
                body = text
                try:
                    body = json.dumps(json.loads(text).get("response"), ensure_ascii=False)
                except (json.JSONDecodeError, AttributeError):
                    pass
                for m in pat.finditer(body):
                    vocab_hits.append({"file": f.name, "term": m.group(1).lower(),
                                       "context": body[max(0, m.start() - 60):m.end() + 60]})
        out["foreign_vocabulary"] = {"terms": sorted(terms), "hits": vocab_hits[:50],
                                     "count": len(vocab_hits),
                                     "excused_used_by_the_estates_own_data": excused}

    # --- the ingest report's own completeness block (TAB6), when it exists ----------------------
    reported = []
    for source_dir in sorted(p for p in Path(ingest_dir).iterdir() if p.is_dir()):
        runs = sorted(p for p in source_dir.iterdir() if (p / "report.json").is_file())
        if not runs:
            continue
        try:
            rep = json.loads((runs[-1] / "report.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        block = rep.get("completeness") or (rep.get("counts") or {}).get("completeness")
        if block:
            reported.append({"source": source_dir.name, "run": runs[-1].name,
                             "completeness": block})
    out["ingest_reported_completeness"] = reported

    # --- verdicts -------------------------------------------------------------------------------
    capped = [t for t in out["tables"] if t["capped"]]
    dropped = [t for t in out["tables"] if t["dropped_in_build"]]
    fk_short = [f for f in out["foreign_keys"] if f["edges_built"] < f["edges_expected"]]
    unresolved = [f for f in out["foreign_keys"] if f["unresolved_references"]]
    unlinked = [x for x in out["cross_source"] if not x["rows_linked_in_graph"]]
    obs_share = observed_isolated / table_nodes if table_nodes else 0.0
    pred_share = predicted_isolated / table_nodes if table_nodes else 0.0
    out["isolation"] = {"table_nodes": table_nodes, "isolated": observed_isolated,
                        "isolated_share": round(obs_share, 4),
                        "predicted_isolated": predicted_isolated,
                        "threshold_share": round(pred_share, 4)}
    out["checks"] = {
        "rows_complete": {"passed": not capped and not dropped,
                          "capped_tables": [f"{t['source']}:{t['table']} "
                                            f"{t['captured_rows']}/{t['source_rows']}"
                                            for t in capped],
                          "dropped_in_build": [f"{t['source']}:{t['table']} "
                                               f"-{t['dropped_in_build']}" for t in dropped]},
        "declared_fks_built": {"passed": not fk_short,
                               "short": [f"{f['source']}:{f['table']}.{f['column']} -> "
                                         f"{f['references']}: {f['edges_built']}/"
                                         f"{f['edges_expected']}" for f in fk_short]},
        "isolation_within_prediction": {"passed": observed_isolated <= predicted_isolated,
                                        "observed": observed_isolated,
                                        "predicted": predicted_isolated},
        "references_resolved": {"passed": not unresolved,
                                "unresolved": [f"{f['source']}:{f['table']}.{f['column']}: "
                                               f"{f['unresolved_references']}"
                                               for f in unresolved]},
        "cross_source_links": {"passed": not unlinked,
                               "unlinked": [f"{x['from']} ~ {x['to']} "
                                            f"({x['value_share']:.0%} of {x['values']})"
                                            for x in unlinked]},
    }
    if "foreign_vocabulary" in out:
        out["checks"]["no_foreign_vocabulary"] = {"passed": not vocab_hits,
                                                  "count": len(vocab_hits)}
    out["passed"] = all(c["passed"] for c in out["checks"].values())
    return out


def _captured_words(capture_dir: Path, terms: set[str]) -> set[str]:
    """Which of ``terms`` occur (as words, singular or plural) in this run's captured text."""
    if not terms:
        return set()
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(terms))) + r")s?\b", re.I)
    found: set[str] = set()
    for f in sorted(Path(capture_dir).glob("*/*.txt")):
        if found == terms:
            break
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        found |= {m.group(1).lower() for m in pat.finditer(text)}
    return found


def foreign_terms(packs_dir: Path, bound: Iterable[str]) -> set[str]:
    """The subject kinds of packs NOT bound here, minus every kind a bound (or common) pack
    declares. Read from the packs' own ontology.yaml — the audit names no domain."""
    import yaml

    bound = set(bound) | {"common"}
    own: set[str] = set()
    foreign: set[str] = set()
    for onto in sorted(Path(packs_dir).glob("*/ontology.yaml")):
        doc = yaml.safe_load(onto.read_text("utf-8")) or {}
        kinds = {(k.get("name") if isinstance(k, dict) else k) for k in doc.get("node_kinds") or []}
        subjects = {(s.get("kind") if isinstance(s, dict) else s)
                    for s in doc.get("subject_kinds") or []}
        if onto.parent.name in bound:
            own |= {k for k in kinds | subjects if k}
        else:
            foreign |= {s for s in subjects if s}
    return {t.replace("_", " ") for t in foreign - own}


def main(argv: Optional[list[str]] = None) -> int:
    from e2e_env import ESTATES, ROOT, estate_name, e2e_dir, graph_db

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("label", nargs="?", default="")
    ap.add_argument("--estate", default=None, choices=sorted(ESTATES))
    ap.add_argument("--dir", default=None, help="the run directory (default: WALNUT_E2E_DIR)")
    ap.add_argument("--out", default=None,
                    help="where to write the JSON (default <run>/artefacts); pass one when the run "
                         "directory is evidence that must not change")
    ap.add_argument("--answers", default=None,
                    help="a directory of captured HTTP responses to scan for foreign vocabulary")
    a = ap.parse_args(argv)
    estate = a.estate or estate_name()
    E = Path(a.dir).resolve() if a.dir else e2e_dir()
    out = audit(graph_db(E, estate), E / "ingest", E / "capture",
                answers_dir=Path(a.answers) if a.answers else None,
                bound_packs=ESTATES[estate]["packs"].split(","), packs_dir=ROOT / "packs")
    out["estate"] = estate
    art = Path(a.out) if a.out else E / "artefacts"
    art.mkdir(parents=True, exist_ok=True)
    p = art / f"completeness{'_' + a.label if a.label else ''}.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"estate": estate, "nodes": out["nodes"], "edges": out["edges"],
                      "isolation": out["isolation"], "checks": out["checks"],
                      "passed": out["passed"], "written": str(p)}, indent=1)[:8000])
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
