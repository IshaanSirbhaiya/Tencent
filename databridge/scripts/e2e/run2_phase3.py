"""E2E2 phase 3: every scenario that only needs the ingested graph + saved agents, CONCURRENTLY.

  golden   ac-001..010 at once through the English-compiled read-only Q&A agent (s5_golden)
  briefing the account briefing (6a)
  overdue  schedule fast-forward -> drafts + CRM task proposal -> approve once / reject / re-approve (6b)
  chain    sequential adapter syncs: new file (event agent 6c) + added column (delegated auto-apply)
           -> renamed column (held) -> dropped column (held) -> new file type .csv (held?)   (7, A)
  fed      /federated/ask x2 + grant refusal for a second principal (D)
  mat      /materialise English tabular + chunk_corpus + leakage case + isolation (M)
  sec      401 without a token, approval by a typed name, literal-secret source (S)
  brain    /brain/query (8 asks) + /brain/graph + /intelligence/ask (F-04/F-05/F-03)

    python scripts/e2e/run2_phase3.py [only...]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import corpus_dir, followups_dir, setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, raw, tokens  # noqa: E402
from run2_lib import load, parallel, poll, save, step  # noqa: E402

RUN1 = E.parent / "e2e-20261001T0356" / "corpus" / "files"
AA = (load("p2_result.json") or {}).get("adapter", {}).get("id") or "larkspur_graph_sync"
P2 = load("p2_result.json") or {}
OVERDUE = ((P2.get("overdue") or {}).get("save") or {}).get("id") or "overdue_invoice_follow_up"


def psql(sql: str) -> str:
    out = subprocess.run(["docker", "exec", "walnut_demo_pg", "psql", "-U", "walnut", "-d",
                          "larkspur", "-Atc", sql], capture_output=True, text=True, timeout=60)
    return (out.stdout + out.stderr).strip()


# --------------------------------------------------------------------------------------------- golden
def golden():
    import s5_golden
    with step("p3.golden_all_parallel"):
        return s5_golden.main("larkspur_qa", [], parallel_=True)


def briefing():
    with step("p3.briefing"):
        st, d, ms = call("POST", "/agents/account_briefing/run",
                         {"question": "Brief me on the Pellucid Freightways account.", "wait": True},
                         name="p3_briefing", timeout=1800)
    return {"status": st, "ms": ms, "run_id": (d.get("record") or {}).get("run_id"),
            "run_status": (d.get("record") or {}).get("status")}


# --------------------------------------------------------------------------------------------- 6b
TICK = sys.argv[sys.argv.index("--tick") + 1] if "--tick" in sys.argv else "2026-10-02T01:00:20Z"


def overdue():
    out: dict = {}
    st, ap0, _ = call("GET", "/agents/approvals", name="p3_overdue_approvals_before")
    before = {a.get("run_id") for a in ap0.get("pending") or []}
    with step("p3.overdue.tick"):
        st, d, ms = call("POST", "/__e2e/tick", {"now": TICK, "drain": True},
                         name="p3_overdue_tick", timeout=1800)
        out["tick"] = {"status": st, "ms": ms, "records": d.get("records")}
    st, ap, _ = call("GET", "/agents/approvals", name="p3_overdue_approvals")
    mine = [a for a in ap.get("pending") or [] if a.get("agent") == OVERDUE
            and a.get("run_id") not in before]
    out["pending"] = mine
    proposed = [a for a in mine if a.get("status") == "proposed"]
    if proposed:
        a1 = proposed[0]
        # S: an approval whose body names someone else — the name must not be believed
        with step("p3.overdue.approve_first"):
            st, d, _ = call("POST", f"/agents/approvals/{a1['run_id']}/{a1['action_id']}/approve",
                            {"by": "mallory", "note": "e2e2"}, name="p3_overdue_approve_a1")
        out["approve_first"] = {"status": st, "body": d}
        st, d, _ = call("POST", f"/agents/approvals/{a1['run_id']}/{a1['action_id']}/approve",
                        {}, name="p3_overdue_approve_a1_again")
        out["approve_again"] = {"status": st, "body": d}
        for a in proposed[1:]:
            st, d, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/reject",
                            {"reason": "e2e2: one approval is enough to prove the loop"},
                            name=f"p3_overdue_reject_{a['action_id']}", token=tokens()["second"])
            out.setdefault("reject_by_second_principal", []).append({"action": a["action_id"],
                                                                     "status": st, "body": d})
    out["db_rows"] = psql("select task_id, invoice_no, created_by, coalesce(agent_run_id,'<null>'), "
                          "left(source_pointer,80) from public.follow_up_tasks order by task_id")
    out["drafts"] = sorted(str(p.relative_to(followups_dir(E))) for p in followups_dir(E).rglob("*")
                           if p.is_file())
    return out


# --------------------------------------------------------------------------------------------- chain
def aa_sync(name: str) -> dict:
    st, d, _ = call("POST", f"/adapter-agents/{AA}/sync", {}, name=name)
    if st != 202:
        return {"status": st, "body": d}
    j = poll(f"/adapter-agents/{AA}/jobs/{d['job_id']}", name + "_job")
    st, h, _ = call("GET", f"/adapter-agents/{AA}/health", name=name + "_health")
    r = j.get("result") or {}
    return {"status": j.get("status"), "result_status": r.get("status"),
            "connections": [{"connection": c.get("connection"), "status": c.get("status"),
                             "reason": c.get("reason"), "counts": c.get("counts"),
                             "adaptation": c.get("adaptation")} for c in r.get("connections") or []],
            "health_adaptation": h.get("adaptation"), "overall": h.get("overall")}


def wait_event_run(agent: str, since: float, limit: float = 120.0) -> list:
    d = E / "runs" / "agent" / agent
    t0 = time.time()
    while time.time() - t0 < limit:
        runs = [p for p in d.glob("run-*.json") if p.stat().st_mtime >= since] if d.is_dir() else []
        done = []
        for p in runs:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if r.get("status") and r.get("ended"):
                done.append({"file": p.name, "status": r.get("status"), "trigger": r.get("trigger"),
                             "answer": (r.get("answer") or {}).get("markdown", "")[:1500]})
        if done:
            return done
        time.sleep(3)
    return []


def chain():
    out: dict = {}
    corpus = corpus_dir(E)
    # (a) a new in-scope file (event trigger) + an ADDED column (additive drift)
    with step("p3.chain.a_newfile_addcol"):
        t_a = time.time()
        dst = corpus / "contracts" / "Tamsinbay"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RUN1 / "contracts" / "Tamsinbay" / "LOI-2026-003_Tamsinbay_Water.md", dst)
        out["a_sql"] = psql("ALTER TABLE public.e2e2_scratch ADD COLUMN IF NOT EXISTS notes text; "
                            "UPDATE public.e2e2_scratch SET notes = 'n-' || ref;")
        out["a_sync"] = aa_sync("p3_chain_a_sync")
    with step("p3.chain.a_event_agent_wait"):
        out["a_event_runs"] = wait_event_run("larkspur_new_file_summariser", t_a)
    # (b) a RENAMED column
    with step("p3.chain.b_rename"):
        out["b_sql"] = psql("ALTER TABLE public.e2e2_scratch RENAME COLUMN region TO area;")
        out["b_sync"] = aa_sync("p3_chain_b_sync")
    # (c) a DROPPED column
    with step("p3.chain.c_drop"):
        out["c_sql"] = psql("ALTER TABLE public.e2e2_scratch DROP COLUMN amount;")
        out["c_sync"] = aa_sync("p3_chain_c_sync")
    # (d) a NEW FILE TYPE outside the declared extension scope (.csv) — twice (F-24)
    with step("p3.chain.d_csv"):
        shutil.copy2(RUN1 / "sales" / "store_units_2026.csv", corpus / "sales")
        out["d_sync1"] = aa_sync("p3_chain_d_sync1")
        out["d_sync2"] = aa_sync("p3_chain_d_sync2")
    st, pr, _ = call("GET", "/adapter-agents/proposals", name="p3_chain_proposals")
    out["proposals"] = pr
    log = E / "sources" / "larkspur_db.mapping.approvals.jsonl"
    out["mapping_approvals"] = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()
                                if x.strip()] if log.is_file() else []
    out["csv_built"] = any("store_units" in p.read_text(encoding="utf-8", errors="ignore")
                           for p in (E / "ingest" / "larkspur-files").glob("*/build.jsonl"))
    return out


# --------------------------------------------------------------------------------------------- D
FED_QS = {
    "overdue": "What is the total overdue invoice amount by account owner, for customers mentioned "
               "in meeting notes, this quarter compared with last quarter?",
    "open": "What is the total open invoice amount by account owner, for customers mentioned in "
            "meeting notes, this quarter compared with last quarter?",
}


def fed():
    out: dict = {}

    def ask(k):
        with step(f"p3.fed.{k}"):
            st, d, ms = call("POST", "/federated/ask", {"question": FED_QS[k], "wait": True},
                             name=f"p3_fed_{k}", timeout=1800)
            return {"status": st, "ms": ms, "body": d}

    def second():
        return call("POST", "/federated/ask", {"question": FED_QS["open"], "wait": True},
                    name="p3_fed_second_principal", token=tokens()["second"])[:2]

    r = parallel(overdue=lambda: ask("overdue"), open=lambda: ask("open"), second=second)
    out.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()})
    return out


# --------------------------------------------------------------------------------------------- M
MAT_REQ = {
    "tabular": "One row per invoice with its customer, project, issue date, due date, net, VAT, "
               "total and status, as the graph and facts hold them now.",
    "chunks": "A chunk corpus of every passage indexed for search, for retrieval evaluation.",
    "leaky": "One row per invoice at its issue date; the label is whether the invoice is paid; "
             "include the invoice status as a feature.",
}


def mat():
    out: dict = {}
    st, d, _ = call("GET", "/materialise/plans", name="p3_mat_plans")
    out["plans"] = d
    st, d, _ = call("GET", "/materialise/catalogue", name="p3_mat_catalogue")
    out["catalogue_status"] = st

    def build(k):
        with step(f"p3.mat.{k}"):
            st, d, _ = call("POST", "/materialise", {"request": MAT_REQ[k]}, name=f"p3_mat_{k}")
            if st != 202:
                return {"status": st, "body": d}
            j = poll(f"/materialise/jobs/{d['job_id']}", f"p3_mat_{k}_job")
            res = {"job": j}
            ph = j.get("plan_hash") or (j.get("result") or {}).get("plan_hash")
            if ph:
                st, rc, _ = call("GET", f"/materialise/receipts/{ph}", name=f"p3_mat_{k}_receipt")
                res["receipt"] = rc
                res["downloads"] = {}
                for f in (rc.get("files") or []):
                    name = f if isinstance(f, str) else (f.get("name") or "")
                    r = raw("GET", f"/materialise/receipts/{ph}/files/{name}")
                    res["downloads"][name] = {"status": r.status_code, "bytes": len(r.content)}
                    if r.status_code == 200:
                        (E / "materialise_downloads" / ph).mkdir(parents=True, exist_ok=True)
                        (E / "materialise_downloads" / ph / name).write_bytes(r.content)
                    else:
                        res["downloads"][name]["body"] = r.text[:400]
                # principal isolation: the second principal sees nothing of it
                sec = tokens()["second"]
                res["isolation"] = {
                    "job": call("GET", f"/materialise/jobs/{d['job_id']}", name=f"p3_mat_{k}_iso_job",
                                token=sec)[0],
                    "receipt": call("GET", f"/materialise/receipts/{ph}", name=f"p3_mat_{k}_iso_rc",
                                    token=sec)[0],
                    "jobs_list": len((call("GET", "/materialise/jobs", name=f"p3_mat_{k}_iso_jobs",
                                           token=sec)[1] or {}).get("jobs") or []),
                    "download": [raw("GET", f"/materialise/receipts/{ph}/files/{n}", token=sec)
                                 .status_code for n in list(res["downloads"])[:3]]}
            return res

    r = parallel(**{k: (lambda k=k: build(k)) for k in MAT_REQ})
    out.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()})
    return out


# --------------------------------------------------------------------------------------------- S
DISCLOSING = [("GET", "/graph/search?q=pellucid"), ("POST", "/brain/query"), ("GET", "/brain/graph"),
              ("GET", "/activity"), ("GET", "/agents"), ("GET", "/agents/approvals"),
              ("GET", "/sources"), ("GET", "/adapter-agents"), ("GET", "/materialise/jobs"),
              ("POST", "/federated/ask"), ("GET", "/evidence/sources"), ("GET", "/platform/catalogue"),
              ("GET", "/automation/status"), ("GET", "/materialise/receipts")]


def sec():
    out: dict = {"no_token": {}}
    for m, p in DISCLOSING:
        body = {"question": "x"} if m == "POST" else None
        st, d, _ = call(m, p, body, name="p3_sec_notoken", token="")
        out["no_token"][f"{m} {p}"] = st
    st, d, _ = call("GET", "/agents", name="p3_sec_badtoken", token="not-a-real-token")
    out["bad_token"] = st
    # a source whose declaration carries a literal secret (a credential in the URL query)
    st, d, _ = call("POST", "/sources", {"mode": "url", "id": "e2e2_leaky_source",
                                         "url": "https://mcp.example.com/mcp?api_key=SUPERSECRETVALUE1234567"},
                    name="p3_sec_literal_secret")
    out["literal_secret"] = {"status": st, "body": d,
                             "echoed": "SUPERSECRETVALUE1234567" in json.dumps(d)}
    st, d, _ = call("POST", "/sources", {"mode": "connection", "connection": {
        "id": "e2e2_leaky_pg", "title": "leaky", "transport": {
            "type": "stdio", "command": "uvx", "args": ["postgres-mcp", "--password=hunter2hunter2"]}}},
        name="p3_sec_literal_secret_arg")
    out["literal_secret_arg"] = {"status": st, "body": d, "echoed": "hunter2hunter2" in json.dumps(d)}
    # import with a caller-chosen declarations path
    st, d, _ = call("POST", "/sources", {"mode": "import", "path": "C:/Windows/win.ini"},
                    name="p3_sec_import_path")
    out["import_path"] = st
    # package level: a typed name is not an approver (adapter agents)
    try:
        from databridge_adapter_agents.adapt import principal_of
        principal_of("alice", "e2e2 check")
        out["adapter_principal_of_name"] = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        out["adapter_principal_of_name"] = f"refused: {type(exc).__name__}: {str(exc)[:200]}"
    return out


# --------------------------------------------------------------------------------------------- brain
def brain():
    from s5_brain import QS
    out: dict = {}

    def q(qid, text):
        st, d, ms = call("POST", "/brain/query", {"question": text}, name=f"p3_brain_{qid}", timeout=600)
        return {"id": qid, "status": st, "ms": round(ms), "answer": (d.get("answer") or "")[:800],
                "refusal": d.get("refusal"), "citations": d.get("citations"),
                "route": d.get("route") or d.get("routed_to") or d.get("mode"),
                "keys": sorted(d.keys()) if isinstance(d, dict) else None}
    with step("p3.brain_all_parallel"):
        r = parallel(**{qid: (lambda qid=qid, t=t: q(qid, t)) for qid, t in QS})
    out["query"] = r
    out["graph"] = call("GET", "/brain/graph", name="p3_brain_graph")[0]
    st, d, _ = call("POST", "/intelligence/ask", {"question": "what is the status of invoice INV-2026-0057?"},
                    name="p3_intel_ask")
    out["intelligence_ask"] = {"status": st, "body": str(d)[:600]}
    return out


ALL = {"golden": golden, "briefing": briefing, "overdue": overdue, "chain": chain, "fed": fed,
       "mat": mat, "sec": sec, "brain": brain}

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != TICK]
    only = args or list(ALL)
    with step("p3.parallel_all", scenarios=only):
        res = parallel(**{k: ALL[k] for k in only})
    for k, v in res.items():
        save(f"p3_{k}.json", v if not isinstance(v, Exception) else {"error": repr(v)})
    print("done", {k: ("ERR " + repr(v)[:200]) if isinstance(v, Exception) else "ok" for k, v in res.items()})
