"""E2E3 M: the withheld path on a published dataset (MAT3's test hook, the brief's own command).

    python scripts/e2e/run3_withhold.py <plan_hash> [pointer|leak|missing]

Before: every file 200. Then `python -m databridge_materialise.testing withhold <dir> <hash> <check>`.
After: the dataset and its .pointers.jsonl must be 409 DATASET_WITHHELD; receipt.json and plan.yaml 200.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import ROOT, setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, raw  # noqa: E402
from run2_lib import save, step  # noqa: E402

ph = sys.argv[1]
check = sys.argv[2] if len(sys.argv) > 2 else "pointer"
st, rc, _ = call("GET", f"/materialise/receipts/{ph}", name="p3d_withhold_receipt_before")
names = [f if isinstance(f, str) else f.get("name") for f in rc.get("files") or []]
out = {"plan_hash": ph, "check": check, "before": {n: raw("GET", f"/materialise/receipts/{ph}/files/{n}").status_code
                                                   for n in names}}
with step("p3d.withhold"):
    cp = subprocess.run([sys.executable, "-m", "databridge_materialise.testing", "withhold",
                         str(E / "materialise"), ph, check], capture_output=True, text=True, timeout=120,
                        cwd=str(ROOT), env={**__import__("os").environ,
                                            "PYTHONPATH": __import__("os").pathsep.join(sys.path)})
out["cli"] = {"rc": cp.returncode, "out": (cp.stdout + cp.stderr)[-800:]}
out["after"] = {}
for n in names:
    r = raw("GET", f"/materialise/receipts/{ph}/files/{n}")
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = None
    out["after"][n] = {"status": r.status_code, "code": ((body or {}).get("refusal") or {}).get("code")
                       if isinstance(body, dict) else None}
save("p3d_withhold.json", out)
print(json.dumps(out, indent=1))
