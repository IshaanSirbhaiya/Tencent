"""E2E5 scenario 6 (finance): check the delegated watchlist-limit answer against Postgres.

    python scripts/e2e/run5_fedcheck_fin.py [r0|r1] -> <run>/artefacts/s6_fed_check.json

Independent: SUM(limit_amount) of core_banking.facility rows whose lower(status) = 'watchlist',
grouped by the party's primary_rm_code and the facility's currency as stored (live psql). Every
leaf of every returned cell: its query_cell is re-executed on the facts store (read-only) and the
same cell is re-read LIVE by the row's own key.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402
from run5_ptrcheck import facts_rows, psql, same  # noqa: E402

E = e2e_dir()
KEYS = {"facility_id": "core_banking.facility", "party_id": "core_banking.party",
        "rm_code": "crm.relationship_manager"}


def leaves(p):
    if p.get("kind") == "derived":
        return [x for d in p.get("derived_from") or [] for x in leaves(d)]
    return [p]


def main(rk="r0"):
    body = json.loads((E / "artefacts" / "s6_fed.json").read_text("utf-8"))[rk]["body"]
    rows, _ = psql("finance_demo", "select p.primary_rm_code, f.currency, sum(f.limit_amount)::text "
                   "from core_banking.facility f join core_banking.party p using(party_id) "
                   "join crm.relationship_manager rm on rm.rm_code = p.primary_rm_code "
                   "where lower(f.status) = 'watchlist' group by 1, 2")
    # the question scopes to "each relationship manager in the CRM": a party whose RM code is not
    # a CRM key (an orphan reference) is out of scope; it is counted here so the report says so
    orphan, _ = psql("finance_demo", "select count(*) from core_banking.facility f join "
                     "core_banking.party p using(party_id) where lower(f.status) = 'watchlist' "
                     "and p.primary_rm_code not in (select rm_code from crm.relationship_manager)")
    expected = {(r[0], r[1]): Decimal(r[2]) for r in rows}
    got, cells, reread_ok = {}, 0, 0
    fails = []
    for row in (body.get("table") or {}).get("rows") or []:
        c = row["cells"]
        got[(c["primary_rm_code"]["value"], c["currency"]["value"])] = Decimal(str(c["sum_limit_amount"]["value"]))
        for col, cell in c.items():
            for lf in leaves(cell.get("pointer") or {}):
                cells += 1
                if lf.get("kind") != "query_cell":
                    fails.append({"col": col, "why": f"leaf kind {lf.get('kind')}"})
                    continue
                fr = facts_rows(lf["source_id"], lf["query_hash"])[lf["row_ordinal"]]
                key = next(((k, t) for k, t in KEYS.items() if k in fr), None)
                if key is None:
                    fails.append({"col": col, "why": "no key in facts row", "row": list(fr)[:6]})
                    continue
                live, err = psql("finance_demo", f"select {lf['column']}::text from {key[1]} "
                                                 f"where {key[0]} = '{fr[key[0]]}'")
                if live and same(fr[lf["column"]], live[0][0]):
                    reread_ok += 1
                else:
                    fails.append({"col": col, "key": fr.get(key[0]), "facts": fr[lf["column"]],
                                  "live": live[0][0] if live else None, "err": err[:120]})
    missing = sorted(f"{k[0]}/{k[1]}" for k in set(expected) - set(got))
    extra = sorted(f"{k[0]}/{k[1]}" for k in set(got) - set(expected))
    wrong = sorted(f"{k[0]}/{k[1]}: {got[k]} vs {expected[k]}" for k in set(got) & set(expected)
                   if got[k] != expected[k])
    out = {"expected_groups": len(expected), "returned_groups": len(got), "missing": missing,
           "extra": extra, "wrong": wrong, "matches_independent": not (missing or extra or wrong),
           "cells": cells, "reread_ok": reread_ok, "failures": fails[:10],
           "coverage_status": (body.get("coverage") or {}).get("status"),
           "out_of_scope_watchlist_facilities_with_non_crm_rm": int(orphan[0][0]) if orphan else None}
    (E / "artefacts" / "s6_fed_check.json").write_text(json.dumps(out, indent=2), "utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(*(sys.argv[1:2] or ["r0"]))
