"""E2E2 phase 1b (this run only: phase 1 was first run before the vouch step existed in it):
vouch+approve the DB plan, ingest the DB, and re-compile the three sentences with the scripted
user's corrected answers — concurrently."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call  # noqa: E402
from run2_compile import compile_text  # noqa: E402
from run2_lib import load, parallel, save, step  # noqa: E402
from run2_phase1 import SENTENCES, run_source  # noqa: E402

res = load("p1_result.json")
with step("p1b.approve_db_vouch"):
    st, d, _ = call("POST", "/sources/larkspur_db/plan/approve", {"vouch_read_tools": ["execute_sql"]},
                    name="p1_approve_vouch_larkspur_db")
    res["approve"]["larkspur_db"]["vouched"] = {"tools": ["execute_sql"], "status": st,
                                                "body": str(d)[:400]}
    print("vouch approve", st, str(d)[:300])


def comp(k):
    with step(f"compile2.{k}"):
        return compile_text(SENTENCES[k], f"p1b_compile_{k}")


with step("p1b.db_ingest_and_recompile_parallel"):
    out = parallel(larkspur_db=lambda: run_source("larkspur_db"),
                   **{f"compile_{k}": (lambda k=k: comp(k))
                      for k in ("adapter_plain", "adapter_passages", "overdue")})
res.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in out.items()})
save("p1_result.json", res)
for j in res["larkspur_db"].get("runs") or []:
    print("db", j.get("run_id"), j.get("status"), (j.get("report") or {}).get("counts", {}).get("built"))
print("mapping", str(res["larkspur_db"].get("mapping_approve"))[:300])
for k in ("adapter_plain", "adapter_passages", "overdue"):
    c = res[f"compile_{k}"]
    print(k, c.get("activatable"), [(r["round"], [q["id"] for q in r["open_questions"]], r["answers_sent"]) for r in c["rounds"]])
