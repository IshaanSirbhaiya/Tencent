"""E2E3 phase 3d — the follow-ups phase 3/3c showed were needed, sequentially where they must be:

  mat2    /materialise has no `answers` field, so the user re-words each request to answer the
          planner's own question (the entity / maturity lag it named) -> downloads, sidecar re-read,
          withheld path, isolation (run3_phase3c.mat)
  crm     a second scheduled firing of the overdue agent (phase 3's harness approved the DRAFT and
          rejected the CRM write, a harness error); this time the CRM write (execute_sql) is the one
          approved, the DB is queried, the next sync re-ingests the row (item_changed), and only
          that row is deleted; one draft approval is retried to record its outcome
    python scripts/e2e/run3_phase3d.py [mat2] [crm] [--tick ISO]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402
from run2_lib import parallel, save, step  # noqa: E402
import run3_phase3 as p3  # noqa: E402
import run3_phase3c as p3c  # noqa: E402

TICK2 = sys.argv[sys.argv.index("--tick") + 1] if "--tick" in sys.argv else "2026-10-05T01:00:20Z"
MAT2 = {
    "chunks_files": "A chunk corpus of every passage in passages.larkspur-files.default, for retrieval "
                    "evaluation, with no max_chars limit.",
    "labelled_p0d": "One row per invoice at its issue date, with the customer, project, net and VAT "
                    "amounts as features; the label is the invoice status 30 days after it was "
                    "issued, with a maturity lag of P0D.",
    "rag_meetings_passages": "Build a RAG corpus from passages.larkspur-files.default, the meeting "
                             "notes and other documents, with no max_chars limit.",
}


def mat2():
    p3c.MAT_REQ.clear()
    p3c.MAT_REQ.update(MAT2)
    return p3c.mat()


def crm():
    out: dict = {"rows_before": p3.psql("select task_id from public.follow_up_tasks order by 1")}
    st, ap0, _ = call("GET", "/agents/approvals", name="p3d_crm_approvals_before")
    before = {a.get("run_id") for a in ap0.get("pending") or []}
    with step("p3d.crm.tick"):
        st, d, ms = call("POST", "/__e2e/tick", {"now": TICK2, "drain": True}, name="p3d_crm_tick",
                         timeout=1800)
        out["tick"] = {"status": st, "ms": ms, "n_records": len(d.get("records") or [])}
    st, ap, _ = call("GET", "/agents/approvals", name="p3d_crm_approvals")
    mine = [a for a in ap.get("pending") or [] if a.get("agent") == p3.OVERDUE
            and a.get("run_id") not in before and a.get("status") == "proposed"]
    out["pending"] = mine
    crm_ = [a for a in mine if "execute_sql" in str(a.get("tool"))]
    drafts = [a for a in mine if "write_file" in str(a.get("tool"))]
    if crm_:
        a = crm_[0]
        with step("p3d.crm.approve"):
            st, d, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/approve",
                            {"by": "mallory", "note": "e2e3: approve the CRM write once"},
                            name="p3d_crm_approve")
        out["approve_crm"] = {"status": st, "body": d}
        st, d, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/approve", {},
                        name="p3d_crm_approve_again")
        out["approve_crm_again"] = {"status": st, "body": d}
    if drafts:
        a = drafts[0]
        st, d, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/approve",
                        {"note": "e2e3: approve one draft"}, name="p3d_draft_approve")
        out["approve_draft"] = {"status": st, "body": d, "args": a.get("args")}
    for a in [x for x in mine if x not in crm_[:1] + drafts[:1]]:
        st, _, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/reject",
                        {"reason": "e2e3: one of each proves the loop"}, name="p3d_crm_reject_rest")
        out.setdefault("rejected_rest", []).append([a["action_id"], st])
    out["db_rows"] = p3.psql("select task_id, customer_id, invoice_no, created_by, "
                             "coalesce(agent_run_id,'<null>'), left(source_pointer,160) "
                             "from public.follow_up_tasks order by task_id")
    fd = p3.followups_dir(E)
    out["drafts_on_disk"] = sorted(str(p.relative_to(fd)) for p in fd.rglob("*") if p.is_file()) \
        if fd.is_dir() else []
    # the next sync must re-ingest the new row
    p3.json  # noqa: B018
    save("p3_overdue2.json", out)
    with step("p3d.crm.loop"):
        import run3_phase3c
        # loop() reads p3_overdue.json; point it at this firing's file
        orig = run3_phase3c.load
        run3_phase3c.load = lambda name, default=None: orig("p3_overdue2.json", default) \
            if name == "p3_overdue.json" else orig(name, default)
        out["loop"] = run3_phase3c.loop()
    return out


if __name__ == "__main__":
    only = [a for a in sys.argv[1:] if not a.startswith("--") and a != TICK2] or ["mat2", "crm"]
    for k in only:   # sequential: a tick fires the adapter's schedule, which voids mat pins
        with step(f"p3d.{k}"):
            try:
                v = {"mat2": mat2, "crm": crm}[k]()
            except Exception as exc:  # noqa: BLE001
                v = {"error": repr(exc)}
        save(f"p3d_{k}.json", v)
        print("done", k)
