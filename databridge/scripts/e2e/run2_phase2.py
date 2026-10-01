"""E2E2 phase 2: activate the adapter agent (BUILD_PLAN 5.6 sentence) and sync it (passages, F-11),
while saving the workflow agents and compiling the overdue + delegating agents concurrently.

    python scripts/e2e/run2_phase2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call  # noqa: E402
from run2_compile import compile_text, save_draft  # noqa: E402
from run2_lib import load, parallel, poll, save, step  # noqa: E402
from run2_phase1 import SENTENCES  # noqa: E402

DELEGATE_TEXT = ("When I ask, answer my questions that need both larkspur_db and larkspur-files by "
                 "delegating them to the {aa} adapter agent. Read only; cite every number.")


def aa_sync(aid: str, name: str) -> dict:
    st, d, _ = call("POST", f"/adapter-agents/{aid}/sync", {}, name=name)
    if st != 202:
        return {"status": st, "body": d}
    j = poll(f"/adapter-agents/{aid}/jobs/{d['job_id']}", name + "_job")
    st, h, _ = call("GET", f"/adapter-agents/{aid}/health", name=name + "_health")
    return {"job": j, "health": h}


def main() -> None:
    p1 = load("p1_result.json")
    res: dict = {}
    aa = p1["compile_adapter_plain"]

    def adapter():
        with step("p2.adapter_save"):
            s = save_draft(aa, "p2_adapter")
        aid = s.get("id")
        with step("p2.adapter_sync1"):
            out = aa_sync(aid, "p2_aa_sync1")
        return {"save": s, "sync": out, "id": aid}

    def save_agent(k):
        with step(f"p2.save.{k}"):
            return save_draft(p1[f"compile_{k}"], f"p2_save_{k}")

    def overdue():
        with step("p2.compile.overdue_final"):
            c = compile_text(SENTENCES["overdue"], "p2_compile_overdue", max_rounds=4)
        with step("p2.save.overdue"):
            return {"compile": c, "save": save_draft(c, "p2_save_overdue")}

    with step("p2.parallel"):
        out = parallel(adapter=adapter, overdue=overdue,
                       **{k: (lambda k=k: save_agent(k)) for k in ("briefing", "qa", "newfile")})
    res.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in out.items()})
    aid = (res.get("adapter") or {}).get("id")
    with step("p2.compile.delegate"):
        c = compile_text(DELEGATE_TEXT.format(aa=aid), "p2_compile_delegate")
    res["delegate_compile"] = c
    if c.get("activatable"):
        res["delegate_save"] = save_draft(c, "p2_save_delegate")
    st, cat, _ = call("GET", "/platform/catalogue", name="p2_catalogue")
    save("p2_result.json", res)
    sync = (res.get("adapter") or {}).get("sync") or {}
    r = ((sync.get("job") or {}).get("result") or {})
    print("adapter", aid, (res.get("adapter") or {}).get("save"), r.get("status"), r.get("totals"))
    for cn in r.get("connections") or []:
        print("  ", cn.get("connection"), cn.get("status"), cn.get("reason"), cn.get("counts"))
    print("health", str((sync.get("health") or {}))[:1500])
    for k in ("briefing", "qa", "newfile"):
        print(k, res.get(k))
    print("overdue", (res.get("overdue") or {}).get("save"),
          [(x["round"], [q["id"] for q in x["open_questions"]]) for x in ((res.get("overdue") or {}).get("compile") or {}).get("rounds", [])])
    print("delegate", c.get("activatable"), [(x["round"], x["open_questions"], x["compilation"]) for x in c["rounds"]][:3])
    print("delegate understood", (c.get("final") or {}).get("understood", "")[:800])


if __name__ == "__main__":
    main()
