"""Sync the e2e adapter agent once through the gateway and print drift/adaptation per connection."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_client import call  # noqa: E402

AID = "larkspur_files_db_graph_sync"
name = sys.argv[1] if len(sys.argv) > 1 else "aa_sync"
t0 = time.time()
st, d, ms = call("POST", f"/adapter-agents/{AID}/sync", {}, name=name)
job = d["job_id"]
while True:
    st, j, ms = call("GET", f"/adapter-agents/{AID}/jobs/{job}", name=name + "_poll")
    if j.get("status") != "running":
        break
    time.sleep(3)
r = j.get("result") or {}
print("status", j.get("status"), r.get("status"), "secs", round(time.time() - t0), j.get("error"))
for c in r.get("connections", []):
    ad = c.get("adaptation") or {}
    print(c["connection"], c["status"], c.get("reason"), c["ingest_run_id"], c["counts"])
    print("   adaptation:", json.dumps({k: ad.get(k) for k in ("drifts", "drift_kinds", "held_connection", "problems", "applied", "pending", "decisions")}, default=str)[:1500])
st, h, ms = call("GET", f"/adapter-agents/{AID}/health", name=name + "_health")
print("health", h.get("overall"), json.dumps((h.get("adaptation") or {}).get("pending"), default=str)[:2000])
print("held", (h.get("adaptation") or {}).get("held_connections"), "counts", (h.get("adaptation") or {}).get("counts"))
