"""E2E2 phase 3c: the follow-ups phase 3 showed were needed, CONCURRENTLY and with no source sync
running (a sync in flight correctly voids a materialisation's source pins):

  fed2   the founder question rephrased the way the planner's own questions ask (per customer,
         due date, 'overdue' = unpaid and past due) + the open-amount variant -> real tables
  mat2   chunk_corpus again (quiet platform) + a LABELLED tabular request (MAT's own example) +
         the plain extract again
  sec2   literal secret on a resolvable host; reject by a second principal with a typed `by`
  act    /activity, /activity/runs/{id} for a federated run + an agent run, as both principals

    python scripts/e2e/run2_phase3c.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402
from run2_lib import load, parallel, save, step  # noqa: E402
import run2_phase3 as p3  # noqa: E402

FED2 = {
    "overdue_by_customer": "What is the total overdue invoice amount (unpaid invoices whose due date "
                           "has passed) by customer, for customers mentioned in meeting notes, by "
                           "due date this quarter compared with last quarter?",
    "open_by_customer": "What is the total open invoice amount by customer, for customers mentioned "
                        "in meeting notes, by due date this quarter compared with last quarter?",
}
p3.FED_QS.update(FED2)
p3.MAT_REQ.update({
    "labelled": "One row per invoice at its issue date, with the customer, project, net and VAT "
                "amounts as features; the label is whether the invoice is still unpaid 30 days "
                "after it was issued.",
})


def fed2():
    def ask(k):
        with step(f"p3c.fed.{k}"):
            st, d, ms = call("POST", "/federated/ask", {"question": FED2[k], "wait": True},
                             name=f"p3c_fed_{k}", timeout=1800)
            return {"status": st, "ms": ms, "body": d}
    r = parallel(**{k: (lambda k=k: ask(k)) for k in FED2})
    return {k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()}


def mat2():
    keep = dict(p3.MAT_REQ)
    p3.MAT_REQ.clear()
    p3.MAT_REQ.update({k: keep[k] for k in ("chunks", "labelled", "tabular")})
    return p3.mat()


def sec2():
    out = {}
    st, d, _ = call("POST", "/sources", {"mode": "url", "id": "e2e2_leaky_url",
                                         "url": "https://example.com/mcp?api_key=SUPERSECRETVALUE1234567"},
                    name="p3c_sec_literal_secret_url")
    out["literal_secret_url"] = {"status": st, "body": d,
                                 "echoed": "SUPERSECRETVALUE1234567" in str(d)}
    ov = load("p3_overdue.json") or {}
    pend = [a for a in ov.get("pending") or [] if a.get("status") == "proposed"]
    if pend:
        a = pend[0]
        st, d, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/reject",
                        {"by": "mallory", "reason": "e2e2: ungrounded 'p1' cannot be approved"},
                        name="p3c_reject_by_second_with_typed_name", token=tokens()["second"])
        out["reject_second_typed_by"] = {"status": st, "decided_by": (d or {}).get("decided_by"),
                                         "body": str(d)[:500]}
    return out


def act():
    out = {}
    for who in ("primary", "second"):
        st, d, _ = call("GET", "/activity", name=f"p3c_activity_{who}", params={"limit": 200},
                        token=tokens()[who])
        rows = d.get("items") or d.get("rows") or d.get("activity") or []
        out[f"list_{who}"] = {"status": st, "keys": sorted(d) if isinstance(d, dict) else None,
                              "n": len(rows),
                              "kinds": sorted({str(r.get("kind")) for r in rows}),
                              "started_by": sorted({str(r.get("started_by") or r.get("by"))
                                                    for r in rows})}
        st, d, _ = call("GET", "/activity/automations", name=f"p3c_activity_auto_{who}",
                        token=tokens()[who])
        out[f"automations_{who}"] = {"status": st, "n": len((d or {}).get("automations") or [])}
    return out


if __name__ == "__main__":
    with step("p3c.parallel_all"):
        res = parallel(fed2=fed2, mat2=mat2, sec2=sec2)
    for k, v in res.items():
        save(f"p3c_{k}.json", v if not isinstance(v, Exception) else {"error": repr(v)})
    with step("p3c.activity"):
        a = act()
    save("p3c_act.json", a)
    print("done", {k: "ok" if not isinstance(v, Exception) else repr(v)[:200] for k, v in res.items()})
