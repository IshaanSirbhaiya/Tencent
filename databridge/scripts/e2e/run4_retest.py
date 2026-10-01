"""E2E4: re-test run-3 findings that need their own probe (P-06, P-07, P-09, P-13 with run 3's exact
sentences; the approvals inbox and run history as the second principal).

    python scripts/e2e/run4_retest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402
from run2_compile import compile_text  # noqa: E402
from run2_lib import parallel, save, step  # noqa: E402
import run3_phase3 as p3  # noqa: E402

R3 = json.loads((E.parent / "e2e-20261001T0836" / "random_requests.json").read_text(encoding="utf-8"))


def approvals():
    out = {}
    for who in ("primary", "second", "operator"):
        st, d, _ = call("GET", "/agents/approvals", name=f"p4_approvals_{who}", token=tokens()[who])
        out[who] = {"status": st, "n": len(d.get("pending") or []),
                    "owners": sorted({str(a.get("owner") or a.get("run_owner")) for a in d.get("pending") or []}),
                    "agents": sorted({str(a.get("agent")) for a in d.get("pending") or []})}
    runs = {}
    for ag in ("overdue_invoice_follow_up", "account_briefing"):
        for who in ("primary", "second"):
            st, d, _ = call("GET", f"/agents/{ag}/runs", name=f"p4_runs_{ag}_{who}", token=tokens()[who])
            runs[f"{ag}/{who}"] = {"status": st, "n": len((d or {}).get("runs") or [])}
    out["runs"] = runs
    return out


def brain():
    qs = {"r3_q1": R3["questions"]["q1_single_fact"], "r3_q4": R3["questions"]["q4_vague"],
          "r3_q5": R3["questions"]["q5_blocked_document"]}
    with step("p4.retest.brain"):
        r = parallel(**{k: (lambda k=k, t=t: p3.brain_q(k, t, "p4_retest")) for k, t in qs.items()})
    return {k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()}


def paraphrase():
    with step("p4.retest.p07"):
        c = compile_text(R3["agents"]["a4_delegate"], "p4_retest_p07", max_rounds=1)
    f = c.get("final") or {}
    return {"text": c["text"], "kind": c.get("kind"),
            "open_questions": [{"id": q.get("id"), "text": q.get("text"), "options": q.get("options")}
                               for q in f.get("open_questions") or []]}


if __name__ == "__main__":
    r = parallel(approvals=approvals, brain=brain, paraphrase=paraphrase)
    save("p4_retest.json", {k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()})
    print("done")
