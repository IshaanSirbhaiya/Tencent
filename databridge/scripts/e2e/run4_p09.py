"""E2E4 P-09: leave the analyst's proposals pending (a scheduled firing), then read the approvals
inbox and run history as each principal; finally reject the proposals (nothing executes).

    python scripts/e2e/run4_p09.py [--tick ISO]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402
from run2_lib import save, step  # noqa: E402

TICK = sys.argv[sys.argv.index("--tick") + 1] if "--tick" in sys.argv else "2026-10-06T01:00:20Z"
out: dict = {}
with step("p4.p09.tick"):
    st, d, _ = call("POST", "/__e2e/tick", {"now": TICK, "drain": True}, name="p4_p09_tick", timeout=1800)
    out["tick"] = st
for who in ("primary", "second", "operator"):
    st, d, _ = call("GET", "/agents/approvals", name=f"p4_p09_approvals_{who}", token=tokens()[who])
    pend = d.get("pending") or []
    out[who] = {"status": st, "n": len(pend), "items": [{k: a.get(k) for k in ("run_id", "action_id", "agent", "status", "may_decide")} for a in pend],
                "carries_args": any(a.get("args") for a in pend)}
mine = [a for a in out["primary"]["items"] if a["status"] == "proposed"]
if mine:
    a = mine[0]
    st, d, _ = call("GET", f"/agents/{a['agent']}/runs/{a['run_id']}", name="p4_p09_run_second", token=tokens()["second"])
    st2, d2, _ = call("GET", f"/agents/{a['agent']}/runs/run-20990101T000000-deadbeef", name="p4_p09_run_missing_second", token=tokens()["second"])
    out["run_detail_second"] = {"status": st, "missing_status": st2, "same_body": d == d2}
for a in mine:
    st, _, _ = call("POST", f"/agents/approvals/{a['run_id']}/{a['action_id']}/reject",
                    {"reason": "e2e4: P-09 probe only"}, name="p4_p09_reject")
    out.setdefault("rejected", []).append([a["action_id"], st])
save("p4_p09.json", out)
print({k: (v["n"] if isinstance(v, dict) and "n" in v else v) for k, v in out.items()})
