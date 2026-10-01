"""E2E3 phase 3: every scenario that needs only the ingested graph + saved agents, CONCURRENTLY.

  golden   ac-001..010 through the English-compiled Q&A agent (structural outcomes, INT5 s5_golden)
  briefing the account briefing (run 2 FAIL: N-10/N-11)
  overdue  tick -> drafts + CRM proposal -> approve ONCE (typed 'by' ignored) -> row in
           public.follow_up_tasks; second principal's reject -> 403 (N-18)
  chain    adapter syncs: new file (event agent) + ADDED column (delegated auto-apply under the seal)
           -> rename -> drop -> .csv (all held); then decide held changes via the routes:
           reject rename, approve-after-reject -> 409, approve drop, reject csv
  fed      cross-source with an explicit date window (+ answers round), second principal 403
  sec      401s, plain-string approval, literal secret, SSRF private IP, command source
  brain    /brain/query: run-1 questions + cross-source (routed_to federated)
  rq       random requests: 6 questions (/brain/query) + 5 agents (/agents/compile)

    python scripts/e2e/run3_phase3.py [only...] [--tick ISO]
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
from e2e_client import call, tokens  # noqa: E402
from run2_compile import compile_text  # noqa: E402
from run2_lib import load, parallel, poll, save, step  # noqa: E402

RUN1 = E.parent / "e2e-20261001T0356" / "corpus" / "files"
P2 = load("p2_result.json") or {}
AA = (P2.get("adapter") or {}).get("id")
OVERDUE = ((P2.get("overdue") or {}).get("save") or {}).get("id")
QA = (P2.get("qa") or {}).get("id")
BRIEFING = (P2.get("briefing") or {}).get("id")
NEWFILE = (P2.get("newfile") or {}).get("id")
SCRATCH = f"public.{__import__('os').environ.get('WALNUT_E2E_PREFIX', 'e2e3')}_scratch"  # E2E4: prefix
TICK = sys.argv[sys.argv.index("--tick") + 1] if "--tick" in sys.argv else "2026-10-02T01:00:20Z"


def psql(sql: str) -> str:
    out = subprocess.run(["docker", "exec", "walnut_demo_pg", "psql", "-U", "walnut", "-d",
                          "larkspur", "-Atc", sql], capture_output=True, text=True, timeout=60)
    return (out.stdout + out.stderr).strip()


def golden():
    import s5_golden
    with step("p3.golden_all_parallel"):
        return s5_golden.main(QA, [], parallel_=True)


def briefing():
    with step("p3.briefing"):
        st, d, ms = call("POST", f"/agents/{BRIEFING}/run",
                         {"question": "Brief me on the Pellucid Freightways account.", "wait": True},
                         name="p3_briefing", timeout=1800)
    rec = d.get("record") or {}
    return {"status": st, "ms": ms, "run_id": rec.get("run_id"), "run_status": rec.get("status"),
            "tokens": rec.get("tokens"), "answer": ((rec.get("answer") or {}).get("markdown") or "")[:3000],
            "citations": (rec.get("answer") or {}).get("citations"), "body_keys": sorted(d)}


def overdue():
    out: dict = {"rows_before": psql("select task_id from public.follow_up_tasks order by 1")}
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
        # N-18: a second principal may not decide the owner's proposal
        st, d, _ = call("POST", f"/agents/approvals/{a1['run_id']}/{a1['action_id']}/reject",
                        {"by": "mallory", "reason": "e2e3 second principal"},
                        name="p3_overdue_reject_by_second", token=tokens()["second"])
        out["reject_by_second"] = {"status": st, "body": d}
        with step("p3.overdue.approve_first"):
            st, d, _ = call("POST", f"/agents/approvals/{a1['run_id']}/{a1['action_id']}/approve",
                            {"by": "mallory", "note": "e2e3"}, name="p3_overdue_approve_a1")
        out["approve_first"] = {"status": st, "body": d}
        st, d, _ = call("POST", f"/agents/approvals/{a1['run_id']}/{a1['action_id']}/approve",
                        {}, name="p3_overdue_approve_a1_again")
        out["approve_again"] = {"status": st, "body": d}
        for a in proposed[1:]:
            st, d, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/reject",
                            {"reason": "e2e3: one approval proves the loop"},
                            name=f"p3_overdue_reject_{a['action_id']}")
            out.setdefault("rejected_rest", []).append({"action": a["action_id"], "status": st})
    out["db_rows"] = psql("select task_id, customer_id, invoice_no, created_by, "
                          "coalesce(agent_run_id,'<null>'), left(source_pointer,120) "
                          "from public.follow_up_tasks order by task_id")
    out["drafts"] = sorted(str(p.relative_to(followups_dir(E))) for p in followups_dir(E).rglob("*")
                           if p.is_file()) if followups_dir(E).is_dir() else []
    rd = E / "runs" / "agent" / str(OVERDUE)
    runs = sorted(rd.glob("run-*.json"), key=lambda p: p.stat().st_mtime) if rd.is_dir() else []
    if runs:
        r = json.loads(runs[-1].read_text(encoding="utf-8"))
        out["run"] = {"file": str(runs[-1]), "status": r.get("status"),
                      "answer": ((r.get("answer") or {}).get("markdown") or "")[:2500],
                      "actions": [{k: a.get(k) for k in ("action_id", "status", "mode", "tool",
                                                         "refusal", "reason")}
                                  for a in r.get("actions") or []]}
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


def wait_event_run(agent: str, since: float, limit: float = 180.0) -> list:
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


def adaptations() -> list:
    st, d, _ = call("GET", f"/adapter-agents/{AA}/adaptations", name="p3_chain_adaptations")
    return d.get("adaptations") or d.get("items") or (d if isinstance(d, list) else [])


def chain():
    out: dict = {}
    corpus = corpus_dir(E)
    with step("p3.chain.a_newfile_addcol"):
        t_a = time.time()
        dst = corpus / "contracts" / "Tamsinbay"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RUN1 / "contracts" / "Tamsinbay" / "LOI-2026-003_Tamsinbay_Water.md", dst)
        out["a_sql"] = psql(f"ALTER TABLE {SCRATCH} ADD COLUMN IF NOT EXISTS notes text; "
                            f"UPDATE {SCRATCH} SET notes = 'n-' || ref;")
        out["a_sync"] = aa_sync("p3_chain_a_sync")
    with step("p3.chain.a_event_agent_wait"):
        out["a_event_runs"] = wait_event_run(NEWFILE, t_a)
    with step("p3.chain.b_rename"):
        out["b_sql"] = psql(f"ALTER TABLE {SCRATCH} RENAME COLUMN region TO area;")
        out["b_sync"] = aa_sync("p3_chain_b_sync")
    with step("p3.chain.c_drop"):
        out["c_sql"] = psql(f"ALTER TABLE {SCRATCH} DROP COLUMN amount;")
        out["c_sync"] = aa_sync("p3_chain_c_sync")
    with step("p3.chain.d_csv"):
        shutil.copy2(RUN1 / "sales" / "store_units_2026.csv", corpus / "sales")
        out["d_sync1"] = aa_sync("p3_chain_d_sync1")
        out["d_sync2"] = aa_sync("p3_chain_d_sync2")
    out["adaptations"] = adaptations()
    log = E / "sources" / "larkspur_db.mapping.approvals.jsonl"
    out["mapping_approvals"] = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()
                                if x.strip()] if log.is_file() else []
    out["csv_built"] = any("store_units" in p.read_text(encoding="utf-8", errors="ignore")
                           for p in (E / "ingest" / "larkspur-files").glob("*/build.jsonl"))
    # decide the held changes through the routes (a person)
    with step("p3.chain.decide"):
        dec: dict = {}
        pend = [a for a in out["adaptations"] if a.get("status") in ("pending", "reported")]
        for a in pend:
            kinds = set(a.get("kinds") or [])
            aid = a.get("adaptation_id")
            base = f"/adapter-agents/{AA}/adaptations/{aid}"
            if "renamed" in kinds or "new_file_type" in kinds:
                st, d, _ = call("POST", base + "/reject", {"reason": "e2e3: not wanted",
                                                            "by": "mallory"},
                                name=f"p3_chain_reject_{aid}")
                dec[aid] = {"kinds": sorted(kinds), "reject": [st, d]}
                st2, d2, _ = call("POST", base + "/approve", {}, name=f"p3_chain_approve_rejected_{aid}")
                dec[aid]["approve_after_reject"] = [st2, d2]
            else:
                st, d, _ = call("POST", base + "/approve", {"reason": "e2e3: accepted"},
                                name=f"p3_chain_approve_{aid}")
                dec[aid] = {"kinds": sorted(kinds), "approve": [st, d]}
            # the second principal may not decide this (owner-only)
        out["decisions"] = dec
        out["adaptations_after"] = adaptations()
    return out


# --------------------------------------------------------------------------------------------- D
FED_Q = ("What is the total open invoice amount by account owner, for customers mentioned in "
         "meeting notes, for invoices due between 1 October 2026 and 31 December 2026 compared "
         "with invoices due between 1 July 2026 and 30 September 2026?")


def answer_for(q: str) -> str:
    """What this user means, typed back once (only from the sentence they wrote)."""
    t = q.lower()
    if "owner" in t:
        return "The account owner is customers.account_owner, joined on customer_id."
    if "open" in t:
        return "Open means invoices.status = 'open'."
    if "due" in t or "date" in t or "period" in t or "window" in t:
        return "Use invoices.due_on for the two windows."
    if "meeting" in t or "mention" in t:
        return "Customers named in any document under the meetings folder."
    return "As stated in the question."


def fed():
    out: dict = {}

    def ask(name, answers=None, token=None):
        body = {"question": FED_Q, "wait": True}
        if answers:
            body["answers"] = answers
        st, d, ms = call("POST", "/federated/ask", body, name=name, timeout=1800, token=token)
        if st == 202 and d.get("job_id") and d.get("status") in ("running", "queued"):
            d = poll(f"/federated/jobs/{d['job_id']}", name + "_job")
        return {"status": st, "ms": ms, "body": d}

    def primary():
        with step("p3.fed.ask"):
            r0 = ask("p3_fed_r0")
        res = {"r0": r0}
        items = (r0["body"].get("question_items") or [])
        if items and not ((r0["body"].get("table") or {}).get("rows")):
            with step("p3.fed.ask_answered"):
                res["r1"] = ask("p3_fed_r1", {q["id"]: answer_for(q["text"]) for q in items})
        return res

    r = parallel(primary=primary,
                 second=lambda: ask("p3_fed_second_principal", token=tokens()["second"]))
    out.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()})
    return out


# --------------------------------------------------------------------------------------------- S
DISCLOSING = [("GET", "/graph/search?q=pellucid"), ("POST", "/brain/query"), ("GET", "/brain/graph"),
              ("GET", "/activity"), ("GET", "/agents"), ("GET", "/agents/approvals"),
              ("GET", "/sources"), ("GET", "/adapter-agents"), ("GET", "/materialise/jobs"),
              ("POST", "/federated/ask"), ("GET", "/evidence/sources"), ("GET", "/platform/catalogue"),
              ("GET", "/automation/status"), ("GET", "/materialise/receipts"), ("GET", "/trace"),
              ("GET", "/activity/automations")]


def sec():
    import httpx
    from e2e_client import BASE
    out: dict = {"no_token": {}}
    for m, p in DISCLOSING:
        body = {"question": "x"} if m == "POST" else None
        out["no_token"][f"{m} {p}"] = call(m, p, body, name="p3_sec_notoken", token="")[0]
    out["bad_token"] = call("GET", "/agents", name="p3_sec_badtoken", token="not-a-real-token")[0]
    # an approval sent as a plain string (not a decision object)
    with httpx.Client(timeout=60) as c:
        r = c.post(BASE + "/agents/approvals/run-x/a1/approve", content=json.dumps("approve"),
                   headers={"Authorization": f"Bearer {tokens()['primary']}",
                            "Content-Type": "application/json"})
        out["approval_plain_string_http"] = {"status": r.status_code, "body": r.text[:400]}
    # package level: a typed name is not an approver
    pk: dict = {}
    try:
        from databridge_adapter_agents.adapt import principal_of
        principal_of("alice", "e2e3 check")
        pk["adapter_agents.principal_of('alice')"] = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        pk["adapter_agents.principal_of('alice')"] = f"refused: {type(exc).__name__}: {str(exc)[:160]}"
    try:
        from databridge_mcp_ingest.plans import require_authenticated_approver
        require_authenticated_approver("alice", what="e2e3 check")
        pk["mcp_ingest.require_authenticated_approver('alice')"] = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        pk["mcp_ingest.require_authenticated_approver('alice')"] = f"refused: {type(exc).__name__}: {str(exc)[:160]}"
    out["approval_plain_string_package"] = pk
    st, d, _ = call("POST", "/sources", {"mode": "url", "id": "e2e3_leaky_url",
                                         "url": "https://example.com/mcp?api_key=SUPERSECRETVALUE1234567"},
                    name="p3_sec_literal_secret_url")
    out["literal_secret"] = {"status": st, "body": d, "echoed": "SUPERSECRETVALUE1234567" in json.dumps(d)}
    for host in ("http://10.0.0.5/mcp", "http://127.0.0.1:8080/mcp", "http://169.254.169.254/latest"):
        st, d, _ = call("POST", "/sources", {"mode": "url", "id": "e2e3_ssrf", "url": host},
                        name="p3_sec_ssrf")
        out.setdefault("ssrf", {})[host] = {"status": st, "code": (d.get("refusal") or {}).get("code"),
                                           "message": str((d.get("refusal") or d))[:300]}
    st, d, _ = call("POST", "/sources", {"mode": "connection", "connection": {
        "id": "e2e3_cmd", "title": "cmd", "transport": {
            "type": "stdio", "command": "cmd.exe", "args": ["/c", "whoami"]}}},
        name="p3_sec_command_source")
    out["command_source"] = {"status": st, "code": (d.get("refusal") or {}).get("code"),
                             "message": str((d.get("refusal") or d))[:300]}
    out["import_path"] = call("POST", "/sources", {"mode": "import", "path": "C:/Windows/win.ini"},
                              name="p3_sec_import_path")[0]
    return out


# --------------------------------------------------------------------------------------------- brain
def brain_q(qid, text, label="p3_brain"):
    st, d, ms = call("POST", "/brain/query", {"question": text}, name=f"{label}_{qid}", timeout=900)
    return {"id": qid, "q": text, "status": st, "ms": round(ms),
            "answer": (d.get("answer") or d.get("answer_markdown") or "")[:2500],
            "refusal": d.get("refusal"), "routed_to": d.get("routed_to"), "route": d.get("route"),
            "table": d.get("table"), "coverage": d.get("coverage"), "questions": d.get("questions"),
            "citations": (d.get("citations") or [])[:40], "run_id": d.get("run_id"),
            "activity_url": d.get("activity_url"), "keys": sorted(d) if isinstance(d, dict) else None}


def brain():
    from s5_brain import QS
    qs = list(QS) + [("x-4", FED_Q)]
    with step("p3.brain_all_parallel"):
        r = parallel(**{qid: (lambda qid=qid, t=t: brain_q(qid, t)) for qid, t in qs})
    out = {"query": r, "graph": call("GET", "/brain/graph", name="p3_brain_graph")[0]}
    st, d, _ = call("POST", "/intelligence/ask", {"question": "what is the status of invoice INV-2026-0057?"},
                    name="p3_intel_ask")
    out["intelligence_ask"] = {"status": st, "body": str(d)[:800]}
    return out


# --------------------------------------------------------------------------------------------- random
def rq():
    R = json.loads((E / "random_requests.json").read_text(encoding="utf-8"))
    jobs = {k: (lambda k=k, t=t: brain_q(k, t, "p3_rq")) for k, t in R["questions"].items()}

    def comp(k, t):
        with step(f"p3.rq.{k}"):
            c = compile_text(t, f"p3_rq_{k}", max_rounds=1)
        f = c.get("final") or {}
        return {"text": t, "kind": c.get("kind"), "activatable": c.get("activatable"),
                "open_questions": [{"id": q.get("id"), "text": q.get("text"), "options": q.get("options")}
                                   for q in f.get("open_questions") or []],
                "understood": f.get("understood"), "refusal": f.get("refusal"),
                "status": c["rounds"][0]["status"] if c.get("rounds") else None,
                "spec_yaml": (f.get("spec_yaml") or "")[:3000], "keys": sorted(f)}
    jobs.update({k: (lambda k=k, t=t: comp(k, t)) for k, t in R["agents"].items()})
    with step("p3.rq_parallel"):
        r = parallel(**jobs)
    return {k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()}


def deleg_agent():
    aid = (P2.get("delegate_save") or {}).get("id")
    if not aid:
        return {"error": "no delegating agent saved"}
    with step("p3.deleg_agent"):
        st, d, ms = call("POST", f"/agents/{aid}/run", {"question": FED_Q, "wait": True},
                         name="p3_deleg_agent", timeout=1800)
    rec = d.get("record") or {}
    return {"agent": aid, "status": st, "ms": ms, "run_id": rec.get("run_id"),
            "run_status": rec.get("status"), "steps": [{k: s_.get(k) for k in ("tool", "ok", "error")}
                                                        for s_ in rec.get("steps") or []],
            "answer": ((rec.get("answer") or {}).get("markdown") or "")[:3000],
            "outcome": rec.get("outcome"), "coverage": rec.get("coverage")}


ALL = {"deleg_agent": deleg_agent, "golden": golden, "briefing": briefing, "overdue": overdue, "chain": chain, "fed": fed,
       "sec": sec, "brain": brain, "rq": rq}

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != TICK]
    only = args or list(ALL)
    with step("p3.parallel_all", scenarios=only):
        res = parallel(**{k: ALL[k] for k in only})
    for k, v in res.items():
        save(f"p3_{k}.json", v if not isinstance(v, Exception) else {"error": repr(v)})
    print("done", {k: ("ERR " + repr(v)[:200]) if isinstance(v, Exception) else "ok" for k, v in res.items()})
