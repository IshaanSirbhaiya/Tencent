"""E2E3 scenario D check for an explicit-window cross-source answer.

For every numeric cell of the answer table: expand its derived pointer to query_cell leaves, re-run
each leaf's statement on the facts store, re-read the same cell LIVE in Postgres, and compare.
Independently (no product code): the customers "mentioned in meeting notes" are those whose
customers.name (or its first two words) appears in the text of any file under meetings/ in the
run's corpus (docx read as XML text); the expected table is SUM(total_gbp) of their OPEN invoices
by account_owner over the plan's own compare windows (read from the answer's plan).

    python scripts/e2e/fed_check3.py <artefact json> <key path, e.g. primary.r1> [label]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import corpus_dir, e2e_dir  # noqa: E402
import fed_check  # noqa: E402

E = e2e_dir()


def meeting_text() -> str:
    out = []
    for p in (corpus_dir(E) / "meetings").rglob("*"):
        if p.suffix.lower() == ".docx":
            with zipfile.ZipFile(p) as z:
                out.append(re.sub(r"<[^>]+>", " ", z.read("word/document.xml").decode("utf-8", "replace")))
        elif p.is_file():
            out.append(p.read_text(encoding="utf-8", errors="replace"))
    return re.sub(r"\s+", " ", " ".join(out)).lower()


def doc_span_ok(p: dict) -> dict:
    """Link evidence from a document: capture bytes hash to the digest, the span is in bounds of
    the captured text and (when the pointer carries an excerpt) the text there is that excerpt."""
    import hashlib
    sha = p.get("capture_digest") or ""
    f = E / "capture" / sha[:2] / sha
    if not sha or not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != sha:
        return {"ok": False, "why": "capture missing or hash mismatch"}
    texts = [q.read_text(encoding="utf-8") for q in (f.with_name(sha + ".txt"), f.with_name(sha + ".redacted.txt"))
             if q.is_file()] or [f.read_bytes().decode("utf-8", "replace")]
    s, e = p.get("char_start"), p.get("char_end")
    if not (isinstance(s, int) and isinstance(e, int)):
        return {"ok": False, "why": "no span"}
    spans = [t[s:e] for t in texts if e <= len(t)]
    ex = p.get("excerpt") or p.get("quote")
    ok = bool(spans) and (not ex or any(" ".join(str(ex).split()) in " ".join(sp.split()) for sp in spans))
    return {"ok": ok, "span": (spans[0][:80] if spans else None)}


def windows(plan: dict) -> dict:
    """{period: (start, end)} from the plan's own compare periods + clock (this/last quarter of
    2026-10-01, as the plan's assumption states)."""
    per = ((plan.get("combine") or {}).get("compare") or {}).get("periods") or []
    return {p: fed_check.quarter(dt.date(2026, 10, 1), p) for p in per}


def main(path: Path, keypath: str, label: str = "") -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    for k in keypath.split("."):
        body = body[k]
    body = body.get("body", body)
    plan = body.get("plan") or {}
    field = ((plan.get("combine") or {}).get("aggregate") or {}).get("field") or "total_gbp"
    axis = ((plan.get("combine") or {}).get("compare") or {}).get("field") or "due_on"
    text = meeting_text()
    custs = fed_check.psql("select customer_id, name, account_owner from public.customers")
    mentioned = {c for c, n, _o in custs if n.lower() in text or " ".join(n.lower().split()[:2]) in text}
    owner = {c: o for c, _n, o in custs}
    expected: dict = {}
    for p, (s, e) in windows(plan).items():
        for inv, c, v in fed_check.psql(
                f"select invoice_no, customer_id, {field}::text from public.invoices where status='open' "
                f"and {axis} >= '{s}' and {axis} < '{e}'"):
            if c in mentioned:
                expected.setdefault(owner[c], {}).setdefault(p, Decimal(0))
                expected[owner[c]][p] += Decimal(v)
    got: dict = {}
    cells = []
    for row in (body.get("table") or {}).get("rows") or []:
        g = row["cells"].get("account_owner", {}).get("value")
        for col, cell in row["cells"].items():
            if ":" not in col or col.startswith("change:"):
                continue
            period = col.split(":", 1)[1]
            got.setdefault(g, {})[period] = Decimal(str(cell["value"]))
            leaves = fed_check.leaves(cell.get("pointer") or {})
            rb, total = [], Decimal(0)
            for lf in leaves:
                if lf.get("kind") == "doc_span":
                    rb.append({"kind": "doc_span", **doc_span_ok(lf)})
                    continue
                if lf.get("kind") != "query_cell":
                    rb.append({"ok": False, "kind": lf.get("kind")})
                    continue
                fr = fed_check.facts_rows(lf["query_hash"])[lf["row_ordinal"]]
                v = fr[lf["column"]]
                # the row's own table: invoices rows carry invoice_no, customers rows customer_id
                tbl, key = (("invoices", "invoice_no") if "invoice_no" in fr else ("customers", "customer_id"))
                live = fed_check.psql(f"select {lf['column']}::text from public.{tbl} where {key} = "
                                      f"'{fr.get(key)}'")
                lv = live[0][0] if live else None
                same = lv is not None and (str(lv) == str(v) or (lf["column"] == field and Decimal(str(lv)) == Decimal(str(v))))
                rb.append({"row": fr.get(key), "column": lf["column"], "facts": v, "live": lv, "ok": same})
                if lf["column"] == field:
                    total += Decimal(str(v))
            cells.append({"group": g, "column": col, "value": cell["value"], "leaves": rb,
                          "leaf_sum": str(total), "value_matches_leaves": Decimal(str(cell["value"])) == total,
                          "all_leaves_reread": all(x["ok"] for x in rb)})
    exp_nonzero = {g: {p: str(v) for p, v in ps.items()} for g, ps in expected.items()}
    got_s = {g: {p: str(v) for p, v in ps.items() if v != 0} for g, ps in got.items()}
    out = {"mentioned_customers": sorted(mentioned), "expected": exp_nonzero, "got_nonzero": got_s,
           "rows_returned": len(got), "cells": cells,
           "every_returned_value_correct": all(
               expected.get(g, {}).get(p, Decimal(0)) == v for g, ps in got.items() for p, v in ps.items()),
           "complete": got_s == exp_nonzero,
           "missing_groups": sorted(set(exp_nonzero) - set(got_s)),
           "all_leaves_reread": all(c["all_leaves_reread"] for c in cells),
           "values_match_leaves": all(c["value_matches_leaves"] for c in cells),
           "coverage_status": (body.get("coverage") or {}).get("status")}
    (E / "artefacts" / f"fed_check3{'_' + label if label else ''}.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = main(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    print(json.dumps({k: v for k, v in o.items() if k != "cells"}, indent=1, default=str))
    for c in o["cells"]:
        print(c["group"], c["column"], c["value"], c["leaf_sum"], c["all_leaves_reread"], [x.get("row") for x in c["leaves"]])
