"""Scenario D check: a /federated/ask table against an INDEPENDENT computation, and every number's
derived pointer expanded to its leaves, each leaf re-read from the source.

For each numeric cell:
  * leaves = the query_cell pointers under its `derived` pointer (recursively);
  * each leaf: the statement is found by its query_hash in the facts query log, re-executed on the
    facts store (sqlite), the cell at row_ordinal/column read back and compared with the source
    row in LIVE Postgres (docker exec psql, read-only SELECT) by the row's natural key;
  * EXACTLY its inputs: the set of rows the leaves read == the set of rows the independent
    computation (live Postgres) puts in that group+period; and SUM(leaf values) == the cell.
The independent computation reads the plan (field, group, period axis) from the answer's own
pointers — the expectation is derived, never written down (rule 6).

    python scripts/e2e/fed_check.py <artefact json with {body}> [label]
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402

E = e2e_dir()
FACTS = E / "facts" / "larkspur_db_facts.sqlite"
QLOG = E / "facts" / "larkspur_db_facts.queries"


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(["docker", "exec", "walnut_demo_pg", "psql", "-U", "walnut", "-d", "larkspur",
                          "-AtF", "\t", "-c", sql], capture_output=True, text=True, timeout=60)
    return [ln.split("\t") for ln in out.stdout.splitlines() if ln]


def leaves(p: dict) -> list[dict]:
    if p.get("kind") == "derived":
        return [x for d in p.get("derived_from") or [] for x in leaves(d)]
    return [p]


_rows: dict = {}


def facts_rows(qh: str):
    if qh not in _rows:
        q = json.loads((QLOG / f"{qh}.json").read_text(encoding="utf-8"))
        c = sqlite3.connect(str(FACTS))
        cur = c.execute(q["sql"], q.get("params") or [])
        cols = [d[0] for d in cur.description]
        _rows[qh] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return _rows[qh]


def quarter(d: dt.date, which: str) -> tuple[dt.date, dt.date]:
    q0 = dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    if which == "this_quarter":
        s = q0
    else:
        m = q0.month - 3
        s = dt.date(q0.year - (m <= 0), (m - 1) % 12 + 1, 1)
    m2 = s.month + 3
    return s, dt.date(s.year + (m2 > 12), (m2 - 1) % 12 + 1, 1)


def main(path: Path, label: str = "") -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    body = body.get("body", body)
    table = body.get("table") or {}
    out = {"run_id": body.get("run_id"), "status": body.get("status"), "cells": [], "problems": []}
    rows = table.get("rows") or []
    if not rows:
        out["problems"].append("empty table")
    for row in rows:
        cells = row["cells"]
        gkey = [k for k in cells if ":" not in k]
        for col, cell in cells.items():
            p = cell.get("pointer") or {}
            if ":" not in col:
                continue
            rec = {"group": {k: cells[k]["value"] for k in gkey}, "column": col, "value": cell["value"],
                   "derivation": p.get("derivation"), "agg_window": p.get("agg_window")}
            lv = leaves(p)
            if col.startswith("change:"):
                rec["leaf_count"] = len(lv)
                out["cells"].append(rec)
                continue
            readback, keys, total = [], set(), Decimal(0)
            for leaf in lv:
                if leaf.get("kind") != "query_cell":
                    readback.append({"kind": leaf.get("kind"), "ok": False})
                    continue
                fr = facts_rows(leaf["query_hash"])[leaf["row_ordinal"]]
                v = fr[leaf["column"]]
                key = fr.get("invoice_no")
                live = psql(f"select {leaf['column']}::text from public.invoices where invoice_no = '{key}'")
                ok = live and Decimal(str(live[0][0])) == Decimal(str(v))
                readback.append({"row": key, "facts": v, "live": live[0][0] if live else None, "ok": bool(ok)})
                keys.add(key)
                total += Decimal(str(v))
            # independent computation from LIVE Postgres
            field = (p.get("derivation") or "SUM(x)").split("(")[1].rstrip(")")
            win = (p.get("agg_window") or "")
            period, rest = win.split(" ", 1) if " " in win else (win, "")
            axis_field = rest.rsplit(":", 1)[-1] if ":" in rest else "due_on"
            s, e = quarter(dt.date(2026, 10, 1), period)
            where = " and ".join(f"{k} = '{v}'" for k, v in rec["group"].items())
            ind = psql(f"select invoice_no, {field}::text from public.invoices where {where} and "
                       f"{axis_field} >= '{s}' and {axis_field} < '{e}'")
            ind_keys = {r[0] for r in ind}
            ind_sum = sum((Decimal(r[1]) for r in ind), Decimal(0))
            rec.update({"leaves": readback, "leaf_rows": sorted(keys), "independent_rows": sorted(ind_keys),
                        "independent_value": str(ind_sum), "leaf_sum": str(total),
                        "exactly_inputs": keys == ind_keys,
                        "value_matches_independent": Decimal(str(cell["value"])) == ind_sum,
                        "value_matches_leaves": Decimal(str(cell["value"])) == total,
                        "all_leaves_reread": all(x["ok"] for x in readback)})
            out["cells"].append(rec)
    num = [c for c in out["cells"] if "exactly_inputs" in c]
    out["summary"] = {"numeric_cells": len(num),
                      "exactly_inputs": sum(c["exactly_inputs"] for c in num),
                      "value_matches_independent": sum(c["value_matches_independent"] for c in num),
                      "all_leaves_reread": sum(c["all_leaves_reread"] for c in num)}
    (E / "artefacts" / f"fed_check{'_' + label if label else ''}.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "")
    print(json.dumps(o["summary"]), o["problems"])
    for c in o["cells"]:
        print(c["group"], c["column"], c["value"], c.get("independent_value"), c.get("leaf_rows"),
              c.get("independent_rows"), c.get("exactly_inputs"), c.get("all_leaves_reread"))
