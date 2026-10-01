"""E2E3 phase 2: activate the adapter agent compiled from English WITH passages (N-01 back-fill) as the
signed-in analyst (a sealed delegation record must be minted, AA3/INT5), sync it, and concurrently
save the workflow agents, compile the overdue sentence and then the delegating agent (N-04).

    python scripts/e2e/run3_phase2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call  # noqa: E402
from run2_compile import compile_text, save_draft  # noqa: E402
from run2_lib import load, parallel, save, step  # noqa: E402
from run2_phase1 import SENTENCES  # noqa: E402
from run2_phase2 import DELEGATE_TEXT, aa_sync  # noqa: E402


def main() -> None:
    p1 = load("p1_result.json")
    res: dict = {}
    aa = p1["compile_adapter_passages"] if (p1.get("compile_adapter_passages") or {}).get(
        "activatable") else p1["compile_adapter_plain"]
    res["adapter_sentence"] = aa.get("text")

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
        c = p1.get("compile_overdue") or {}
        if not c.get("activatable"):
            with step("p2.compile.overdue_final"):
                c = compile_text(SENTENCES["overdue"], "p2_compile_overdue", max_rounds=4)
        with step("p2.save.overdue"):
            return {"compile": c, "save": save_draft(c, "p2_save_overdue")}

    with step("p2.parallel"):
        out = parallel(adapter=adapter, overdue=overdue,
                       **{k: (lambda k=k: save_agent(k)) for k in ("briefing", "qa", "newfile")})
    res.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in out.items()})
    aid = (res.get("adapter") or {}).get("id")
    # the sealed delegation record (AA3): read from disk, never trusted from the response
    dl = E / "adapter_agents" / "_delegations" / f"{aid}.delegations.jsonl"
    res["delegation_log"] = [json.loads(x) for x in dl.read_text(encoding="utf-8").splitlines()
                             if x.strip()] if dl.is_file() else None
    with step("p2.compile.delegate"):
        c = compile_text(DELEGATE_TEXT.format(aa=aid), "p2_compile_delegate")
    res["delegate_compile"] = c
    if c.get("activatable"):
        res["delegate_save"] = save_draft(c, "p2_save_delegate")
    save("p2_result.json", res)
    sync = (res.get("adapter") or {}).get("sync") or {}
    r = ((sync.get("job") or {}).get("result") or {})
    print("adapter", aid, str((res.get("adapter") or {}).get("save"))[:300], r.get("status"))
    for cn in r.get("connections") or []:
        print("  ", cn.get("connection"), cn.get("status"), cn.get("reason"), cn.get("counts"))
    print("delegation_log", str(res["delegation_log"])[:400])
    for k in ("briefing", "qa", "newfile"):
        print(k, str(res.get(k))[:200])
    print("overdue", str((res.get("overdue") or {}).get("save"))[:300])
    print("delegate", c.get("kind"), c.get("activatable"),
          [(x["round"], [q["id"] for q in x["open_questions"]]) for x in c["rounds"]])


if __name__ == "__main__":
    main()
