"""E2E3 A: a column TYPE change is held, then APPROVED by a person through the route (phase 3's
harness rejected both held changes, so the approve-200 path still needed proving).

    python scripts/e2e/run3_typechange.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402
from run2_lib import save, step  # noqa: E402
import run3_phase3 as p3  # noqa: E402

out: dict = {}
with step("p3d.type_change"):
    out["sql"] = p3.psql(f"ALTER TABLE {p3.SCRATCH} ALTER COLUMN notes TYPE varchar(40);")
    out["sync"] = p3.aa_sync("p3d_type_sync")
    ads = p3.adaptations()
    pend = [a for a in ads if a.get("status") in ("pending", "reported")]
    out["pending"] = pend
    for a in pend:
        base = f"/adapter-agents/{p3.AA}/adaptations/{a['adaptation_id']}"
        out.setdefault("second_principal_approve", []).append(
            call("POST", base + "/approve", {}, name="p3d_type_approve_second", token=tokens()["second"])[:2])
        st, d, _ = call("POST", base + "/approve", {"reason": "e2e3: the wider type is fine"},
                        name="p3d_type_approve")
        out.setdefault("approve", []).append({"id": a["adaptation_id"], "kinds": a.get("kinds"),
                                              "status": st, "body": d})
    out["after"] = [(a.get("adaptation_id"), a.get("status"), a.get("kinds")) for a in p3.adaptations()]
    out["sync_after"] = p3.aa_sync("p3d_type_sync_after")
save("p3d_typechange.json", out)
print(out["sync"]["connections"][1]["adaptation"] if len(out["sync"]["connections"]) > 1 else out["sync"])
print("pending", [(a["adaptation_id"], a.get("kinds"), a.get("held_because")) for a in out["pending"]])
print("second", [x[0] for x in out.get("second_principal_approve", [])])
print("approve", [(x["id"], x["status"], str(x["body"])[:200]) for x in out.get("approve", [])])
print("after", out["after"])
