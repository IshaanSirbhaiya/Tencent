"""Scenario 4: re-verify EVERY pointer in the e2e graph store, independently of verify_run.

For each row of `sources` (every version, every entity/edge/attribute pointer):
  * capture binding: capture_digest present, bytes present, sha256(bytes) == digest
  * doc_span: span in bounds of the captured text; the value (attribute value, or the entity label
    for a non-anchor node, or the endpoint label(s) for an edge) appears in the span (raw or
    redacted text)
  * table_cell: row_id resolves in the snapshot by primary key, column present, value equal;
    then the same cell in the facts store and LIVE in Postgres (docker exec psql, read-only SELECT)
  * derived: every base in derived_from checked recursively (binding + bounds)
Categories: verified | hash_mismatch | capture_missing | text_unbound | out_of_bounds |
value_not_in_span | cell_missing | cell_value_mismatch | other.

    python scripts/e2e/pointer_audit.py [label]
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402

E = e2e_dir()
CAP = E / "capture"
QUOTE = "'"


def _cap(sha: str, suffix: str = "") -> Path:
    return CAP / sha[:2] / f"{sha}{suffix}"


_hash: dict = {}
_text: dict = {}
_snap: dict = {}
_snapmeta: dict = {}
LIVE: dict = {}


def binding(p: dict):
    sha = p.get("capture_digest")
    if not sha:
        return "text_unbound"
    if sha not in _hash:
        f = _cap(sha)
        if not f.is_file():
            _hash[sha] = None
        else:
            _hash[sha] = hashlib.sha256(f.read_bytes()).hexdigest() == sha
    if _hash[sha] is None:
        return "capture_missing"
    return None if _hash[sha] else "hash_mismatch"


def texts(sha: str):
    if sha not in _text:
        t, r = _cap(sha, ".txt"), _cap(sha, ".redacted.txt")
        _text[sha] = (t.read_text(encoding="utf-8") if t.is_file() else None,
                      r.read_text(encoding="utf-8") if r.is_file() else None)
    return _text[sha]


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def num_eq(a, b) -> bool:
    if norm(a) == norm(b):
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def snapshot(sha: str):
    if sha not in _snap:
        try:
            d = json.loads(_cap(sha).read_bytes())
            pk = d.get("primary_key") or []
            _snapmeta[sha] = d
            _snap[sha] = {",".join(str(r.get(c)) for c in pk): r for r in d.get("rows") or []}
        except Exception:                                           # noqa: BLE001
            _snapmeta[sha] = {}
            _snap[sha] = {}
    return _snap[sha], _snapmeta[sha]


def live_cell(table: str, pk: list, row_id: str, column: str) -> str:
    key = (table, row_id, column)
    if key in LIVE:
        return LIVE[key]
    if not pk or len(pk) != 1:
        LIVE[key] = "n/a"
        return "n/a"
    rid = row_id.replace(QUOTE, "")
    sql = f"select {column}::text from {table} where {pk[0]}::text = {QUOTE}{rid}{QUOTE}"
    try:
        out = subprocess.run(["docker", "exec", "walnut_demo_pg", "psql", "-U", "walnut", "-d",
                              "larkspur", "-At", "-c", sql], capture_output=True, text=True,
                             timeout=30)
        LIVE[key] = out.stdout.rstrip("\n") if out.returncode == 0 else "error:" + out.stderr[:100]
    except Exception as exc:                                        # noqa: BLE001
        LIVE[key] = f"error:{exc}"
    return LIVE[key]


def main(label: str = "") -> dict:
    c = sqlite3.connect(str(E / "graph" / "workspace.db"))
    node_rows = {r[0]: r for r in c.execute(
        "select rowid_pk,id,kind,label,canonical_id,attrs_json,system_to from nodes")}
    edge_rows = {r[0]: r for r in c.execute(
        "select rowid_pk,id,src_id,dst_id,kind,attrs_json,system_to from edges")}
    node_ids = {r[1] for r in node_rows.values()}
    edge_ids = {r[1] for r in edge_rows.values()}
    latest_label: dict = {}
    for k in sorted(node_rows):
        latest_label[node_rows[k][1]] = node_rows[k][3]
    fpath = E / "facts" / "larkspur_db_facts.sqlite"
    facts = sqlite3.connect(str(fpath)) if fpath.is_file() else None
    counts = collections.Counter()
    by_kind: dict = collections.defaultdict(collections.Counter)
    fails: dict = collections.defaultdict(list)
    cells = collections.Counter()

    def value_of(entity, field, vpk, is_edge):
        row = (edge_rows if is_edge else node_rows).get(vpk) if vpk else None
        if row is None:
            return None, row
        attrs = json.loads(row[5] or "{}")
        if field in attrs:
            return attrs[field], row
        if not is_edge and field == "label":
            return row[3], row
        if not is_edge and field == "canonical_id":
            return row[4], row
        return None, row

    def check(p: dict, entity: str, field, vpk, top: bool = True) -> str:
        kind = p.get("kind")
        if kind == "derived":
            bases = p.get("derived_from") or []
            if not bases:
                return "other"
            for b in bases:
                r = check(b, entity, None, vpk, top=False)
                if r != "verified":
                    return r
            return "verified"
        b = binding(p)
        if b:
            return b
        sha = p["capture_digest"]
        is_edge = entity in edge_ids and entity not in node_ids
        if kind == "doc_span":
            raw, red = texts(sha)
            s, e = p.get("char_start"), p.get("char_end")
            if raw is None or not isinstance(s, int) or not isinstance(e, int) \
                    or not (0 <= s <= e <= len(raw)):
                return "out_of_bounds"
            if not top:
                return "verified"
            spans = [norm(raw[s:e])] + ([norm(red[s:e])] if red and len(red) >= e else [])
            values: list = []
            if field:
                v, _ = value_of(entity, field, vpk, is_edge)
                values = [v]
            elif is_edge:
                er = edge_rows.get(vpk) or next((r for r in edge_rows.values() if r[1] == entity),
                                                 None)
                if er:
                    values = [latest_label.get(er[3], "")] if er[4] == "mentions" else \
                        [latest_label.get(er[2], ""), latest_label.get(er[3], "")]
            elif entity.startswith("document:"):
                return "verified"                 # an item anchor: the span is the whole item
            else:
                row = node_rows.get(vpk)
                values = [row[3] if row else latest_label.get(entity, "")]
            values = [v for v in values if v not in (None, "")]
            if not values:
                return "verified"
            if all(any(norm(v) in sp for sp in spans) for v in values):
                return "verified"
            return "value_not_in_span"
        if kind == "table_cell":
            rows, meta = snapshot(sha)
            row = rows.get(str(p.get("row_id")))
            col = p.get("column")
            if row is None or col not in row:
                return "cell_missing"
            if top and field:
                v, _ = value_of(entity, field, vpk, is_edge)
                if v is not None and not num_eq(v, row[col]):
                    fails["cell_value_detail"].append({"entity": entity, "field": field,
                                                       "graph": v, "snapshot": row[col]})
                    return "cell_value_mismatch"
            table = p.get("table", "")
            pk = meta.get("primary_key") or []
            lv = live_cell(table, pk, str(p.get("row_id")), col)
            if lv != "n/a" and not lv.startswith("error"):
                cells["live_checked"] += 1
                if not num_eq(lv, row[col]) and not (row[col] is None and lv == ""):
                    cells["live_differs"] += 1
                    if len(fails["live_differs"]) < 15:
                        fails["live_differs"].append({"entity": entity, "table": table,
                                                      "row": p.get("row_id"), "column": col,
                                                      "snapshot": row[col], "live": lv})
            elif lv.startswith("error"):
                cells["live_error"] += 1
            if facts is not None and len(pk) == 1:
                tname = table.split(".")[-1]
                try:
                    fr = facts.execute(f'select "{col}" from "{tname}" where cast("{pk[0]}" as text)=?',
                                       (str(p.get("row_id")),)).fetchone()
                    cells["facts_checked"] += 1
                    if fr is None or (not num_eq(fr[0], row[col])
                                      and not (fr[0] is None and row[col] is None)):
                        cells["facts_differs"] += 1
                        if len(fails["facts_differs"]) < 15:
                            fails["facts_differs"].append({"entity": entity, "table": table,
                                                           "row": p.get("row_id"), "column": col,
                                                           "snapshot": row[col],
                                                           "facts": None if fr is None else fr[0]})
                except Exception as exc:                            # noqa: BLE001
                    cells["facts_error"] += 1
                    if len(fails["facts_error"]) < 5:
                        fails["facts_error"].append(str(exc))
            return "verified"
        return "other"

    for eid, field, vpk, pj in c.execute(
            "select entity_id, field, version_pk, source_pointer_json from sources"):
        p = json.loads(pj)
        res = check(p, eid, field, vpk)
        counts[res] += 1
        by_kind[f"{p.get('kind')}/{p.get('source_id')}"][res] += 1
        if res != "verified" and len(fails[res]) < 15:
            sha = p.get("capture_digest") or ""
            ex = {"entity": eid, "field": field, "version_pk": vpk, "kind": p.get("kind"),
                  "capture": sha[:12], "span": [p.get("char_start"), p.get("char_end")]}
            if p.get("kind") == "doc_span" and sha:
                raw, _ = texts(sha)
                if raw and isinstance(p.get("char_start"), int):
                    ex["span_text"] = raw[p["char_start"]:p["char_end"]][:160]
            fails[res].append(ex)
    total = sum(counts.values())
    out = {"total_pointers": total, "counts": dict(counts),
           "by_kind": {k: dict(v) for k, v in by_kind.items()},
           "verified_share": round(counts["verified"] / max(1, total), 4),
           "cells": dict(cells), "failure_samples": fails}
    name = f"pointer_audit{'_' + label if label else ''}.json"
    (E / "artefacts" / name).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = main(sys.argv[1] if len(sys.argv) > 1 else "")
    print(json.dumps({k: v for k, v in o.items() if k != "failure_samples"}, indent=1))
    for k, v in o["failure_samples"].items():
        print("==", k, len(v))
        for x in v[:6]:
            print("  ", json.dumps(x, default=str)[:400])
