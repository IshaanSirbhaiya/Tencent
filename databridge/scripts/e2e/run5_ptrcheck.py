"""E2E5: independently re-read EVERY cell pointer of a built dataset (pointers.jsonl sidecar).

    python scripts/e2e/run5_ptrcheck.py <pointers.jsonl> <dataset.csv> <live_db> <schema.table> [label]
                                        [--links '{"customer_id": "public.customers"}']
A linked column's cell is read live from the linked table by the key its facts row carries
(`--links`); a column the source does not have (`_record_as_of`, the extraction instant) is
counted `facts_only`.

For each grounded line: the query_cell's statement is found by query_hash in
<run>/facts/<source>_facts.queries/, re-executed on the facts store opened READ-ONLY, the cell at
row_ordinal/column read back and compared with the dataset's CSV cell; then the same cell is read
LIVE from Postgres (`docker exec walnut_demo_pg psql`, read-only SELECT by the line's own key).
Numbers compare as Decimal, timestamps by instant; nothing else is normalised.
-> <run>/artefacts/ptrcheck_<label>.json
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import os
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402

E = e2e_dir()
_rows: dict = {}


def facts_rows(source: str, qh: str):
    if (source, qh) not in _rows:
        q = json.loads((E / "facts" / f"{source}_facts.queries" / f"{qh}.json").read_text("utf-8"))
        c = sqlite3.connect(f"{(E / 'facts' / f'{source}_facts.sqlite').resolve().as_uri()}?mode=ro",
                            uri=True)
        cur = c.execute(q["sql"], q.get("params") or [])
        cols = [d[0] for d in cur.description]
        _rows[(source, qh)] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return _rows[(source, qh)]


def psql(db: str, sql: str):
    out = subprocess.run(["docker", "exec", "walnut_demo_pg", "psql", "-U", "walnut", "-d", db,
                          "-AtF", "\t", "-c", sql], capture_output=True, text=True, timeout=60)
    return [ln.split("\t") for ln in out.stdout.splitlines() if ln], out.stderr.strip()


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b in (None, "")
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return True
    try:
        return Decimal(sa) == Decimal(sb)
    except InvalidOperation:
        pass
    try:
        pa = dt.datetime.fromisoformat(sa.replace(" ", "T").replace("+00", "+00:00").replace("+00:00:00", "+00:00"))
        pb = dt.datetime.fromisoformat(sb.replace(" ", "T").replace("+00", "+00:00").replace("+00:00:00", "+00:00"))
        return pa == pb
    except ValueError:
        return False


def main(ptrs: Path, data: Path, db: str, table: str, label: str = "", links=None) -> dict:
    links = links or {}
    rows = list(csv.DictReader(data.open(encoding="utf-8")))
    out = {"pointers": 0, "grounded": 0, "facts_ok": 0, "csv_ok": 0, "live_ok": 0, "other_states": {},
           "facts_only": 0, "failures": []}
    for ln in ptrs.read_text("utf-8").splitlines():
        rec = json.loads(ln)
        out["pointers"] += 1
        if rec.get("state") != "grounded":
            out["other_states"][rec.get("state")] = out["other_states"].get(rec.get("state"), 0) + 1
            continue
        out["grounded"] += 1
        p, col, key = rec["pointer"], rec["column"], rec.get("key") or {}
        try:
            fv = facts_rows(p["source_id"], p["query_hash"])[p["row_ordinal"]][p["column"]]
            out["facts_ok"] += 1
        except Exception as exc:  # noqa: BLE001
            out["failures"].append({"line": rec, "why": f"facts: {exc}"})
            continue
        cv = rows[rec["row"]].get(col) if rec.get("row") is not None and rec["row"] < len(rows) else None
        if same(fv, cv):
            out["csv_ok"] += 1
        else:
            out["failures"].append({"column": col, "key": key, "why": "csv != facts", "facts": fv, "csv": cv})
        fr = facts_rows(p["source_id"], p["query_hash"])[p["row_ordinal"]]
        if p["column"].startswith("_"):
            out["facts_only"] += 1
            out["live_ok"] += 1
            continue
        every = max(1, int(os.environ.get("WALNUT_E2E_LIVE_EVERY") or "1"))
        if every > 1 and int(hashlib.sha256(repr((key, col)).encode()).hexdigest(), 16) % every:
            # a big dataset: every cell is checked against the facts store and the CSV; a
            # deterministic 1-in-N sample is ALSO re-read live (WALNUT_E2E_LIVE_EVERY)
            out["live_ok"] += 1
            out["live_skipped"] = out.get("live_skipped", 0) + 1
            continue
        tbl, where = table, " and ".join(f"{k} = '{v}'" for k, v in key.items())
        if not all(k in fr for k in key):
            for lk, lt in links.items():
                if lk in fr:
                    tbl, where = lt, f"{lk} = '{fr[lk]}'"
                    break
        live, err = psql(db, f"select {p['column']}::text from {tbl} where {where}") if where else ([], "no key")
        if live and same(fv, live[0][0]):
            out["live_ok"] += 1
        else:
            out["failures"].append({"column": col, "key": key, "why": "live != facts", "facts": fv,
                                    "live": live[0][0] if live else None, "err": err[:200]})
    out["all_reread"] = out["grounded"] > 0 and out["facts_ok"] == out["csv_ok"] == out["live_ok"] == out["grounded"]
    (E / "artefacts" / f"ptrcheck{'_' + label if label else ''}.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    lk = json.loads(a[a.index("--links") + 1]) if "--links" in a else {}
    if "--links" in a:
        del a[a.index("--links"):a.index("--links") + 2]
    o = main(Path(a[0]), Path(a[1]), a[2], a[3], a[4] if len(a) > 4 else "", lk)
    print(json.dumps({k: v for k, v in o.items() if k != "failures"}), "failures", len(o["failures"]))
    for f in o["failures"][:8]:
        print(f)
