"""E2E2 phase 1: register sources, plan+approve both, ingest files and DB CONCURRENTLY, and compile
every English agent sentence concurrently with the ingest (compiles only need approved plans).

    python scripts/e2e/run2_phase1.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call  # noqa: E402
from run2_compile import compile_text  # noqa: E402
from run2_lib import note, parallel, poll, save, step  # noqa: E402

SENTENCES = {
    # BUILD_PLAN 5.6's own adapter-agent sentence (F-09/F-22: never compiled in run 1)
    "adapter_plain": "Keep the Larkspur files folder and CRM database in the graph; check hourly",
    "adapter_passages": "Keep the Larkspur files folder and CRM database in the graph, with passages "
                        "for search; check hourly",
    "briefing": "Account briefing: when I ask about a customer, give me a one-page briefing on that "
                "account from larkspur_db and larkspur-files - who the contacts are, the contract, "
                "the projects, and the invoices with their status. Read only; cite every fact.",
    "qa": "Answer my questions about Larkspur's customers, contacts, invoices, projects and "
          "contracts using larkspur_db and larkspur-files. Only when I ask. Read only; cite every "
          "fact and say so when something is not in the sources.",
    # run 1's FIRST sentence (it needed 7 rewordings then)
    "overdue": "Overdue invoice follow-up: every weekday at 9am, find open invoices in larkspur_db "
               "that are overdue or due within the next 30 days. For each one, save a follow-up "
               "email draft to larkspur-followups and tell me after. Also log a follow-up task in "
               "the CRM for each invoice, but ask me before doing that.",
    "newfile": "When a new file is added to larkspur-files, summarise it for me with citations. "
               "Read only.",
}


def run_source(sid: str) -> dict:
    out = {"source": sid, "runs": []}
    with step(f"ingest.{sid}.run1"):
        st, d, _ = call("POST", f"/sources/{sid}/runs", {}, name=f"p1_run_{sid}")
        out["start"] = {"status": st, "body": d}
        if st != 202:
            return out
        j = poll(f"/sources/{sid}/runs/{d['run_id']}", f"p1_run_{sid}_final",
                 done=lambda j: j.get("status") not in ("running", "queued", "pending", None))
        out["runs"].append(j)
    rep = (j.get("report") or {}).get("counts") or {}
    if sid == "larkspur_db" and not rep.get("built"):
        # a model-proposed table mapping must be approved by a person (by design)
        with step("ingest.larkspur_db.mapping_approve"):
            st, m, _ = call("POST", f"/sources/{sid}/mapping/approve", {}, name="p1_mapping_approve")
            out["mapping_approve"] = {"status": st, "body": m}
        with step("ingest.larkspur_db.run2"):
            st, d, _ = call("POST", f"/sources/{sid}/runs", {}, name=f"p1_run2_{sid}")
            if st == 202:
                j = poll(f"/sources/{sid}/runs/{d['run_id']}", f"p1_run2_{sid}_final",
                         done=lambda j: j.get("status") not in ("running", "queued", "pending", None))
                out["runs"].append(j)
            else:
                out["run2_start"] = {"status": st, "body": d}
    return out


def main() -> None:
    t0 = time.time()
    res: dict = {}
    with step("p1.import"):
        st, d, _ = call("POST", "/sources", {"mode": "import", "route": "direct",
                                             "only": ["larkspur-files", "larkspur_db",
                                                      "larkspur_db_writer", "larkspur-followups"]},
                        name="p1_import")
        res["import"] = {"status": st, "body": d}
        assert st == 200, d
    with step("p1.plan_both"):
        r = parallel(**{s: (lambda s=s: call("POST", f"/sources/{s}/plan", {}, name=f"p1_plan_{s}",
                                             timeout=600)[:2])
                        for s in ("larkspur-files", "larkspur_db")})
        res["plan"] = {k: {"status": v[0], "plan_status": (v[1].get("plan") or v[1]).get("status")}
                       for k, v in r.items()}
    with step("p1.approve_both"):
        r = parallel(**{s: (lambda s=s: call("POST", f"/sources/{s}/plan/approve", {},
                                             name=f"p1_approve_{s}")[:2])
                        for s in ("larkspur-files", "larkspur_db")})
        res["approve"] = {k: {"status": v[0], "body": str(v[1])[:600]} for k, v in r.items()}
        for k, v in r.items():
            if v[0] != 200:
                note(f"plan approve {k} -> {v[0]} {str(v[1])[:300]}")
                msg = str(((v[1] or {}).get("refusal") or {}).get("message") or "")
                tools = re.findall(r"tool '([^']+)' is unclassified", msg)
                if tools and "vouch" in msg:
                    # the product's own path: the approving PERSON vouches the tool reads
                    st2, d2, _ = call("POST", f"/sources/{k}/plan/approve",
                                      {"vouch_read_tools": tools}, name=f"p1_approve_vouch_{k}")
                    res["approve"][k]["vouched"] = {"tools": tools, "status": st2}
                    note(f"plan approve {k} with vouch_read_tools={tools} -> {st2}")
    st, cat, _ = call("GET", "/platform/catalogue", name="p1_catalogue")
    jobs = {s: (lambda s=s: run_source(s)) for s in ("larkspur-files", "larkspur_db")}

    def compile_one(key):
        with step(f"compile.{key}"):
            return compile_text(SENTENCES[key], f"p1_compile_{key}")
    jobs.update({f"compile_{k}": (lambda k=k: compile_one(k)) for k in SENTENCES})
    with step("p1.ingest_and_compile_parallel"):
        out = parallel(**jobs)
    res.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in out.items()})
    res["wall_s"] = round(time.time() - t0, 1)
    save("p1_result.json", res)
    for s in ("larkspur-files", "larkspur_db"):
        o = res.get(s) or {}
        for j in (o.get("runs") or []) if isinstance(o, dict) else []:
            print(s, j.get("run_id"), j.get("status"), (j.get("report") or {}).get("counts", {}).get("governance"),
                  "built", (j.get("report") or {}).get("counts", {}).get("built"))
    for k in SENTENCES:
        c = res.get(f"compile_{k}") or {}
        if isinstance(c, dict):
            print(k, "activatable", c.get("activatable"), "rounds", len(c.get("rounds") or []),
                  [q["id"] for r in c.get("rounds", []) for q in r["open_questions"]][:8])
    print("wall", res["wall_s"])


if __name__ == "__main__":
    main()
