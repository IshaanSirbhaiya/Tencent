"""E2E5: one-line status of every source's latest run (no artefact saved)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import setup_env  # noqa: E402

setup_env()
from e2e_client import call  # noqa: E402

st, d, _ = call("GET", "/sources", save=False)
for s in d.get("sources", []):
    sid = s.get("id")
    st, j, _ = call("GET", f"/sources/{sid}", save=False)
    lr = j.get("last_run") or {}
    c = lr.get("counts") or {}
    print(f"{sid:24} run={lr.get('run_id')} status={lr.get('status')} running={j.get('running')} "
          f"captured={c.get('captured')} built={c.get('built')} failed={c.get('failed')} "
          f"skipped={c.get('build_skipped')} msg={(lr.get('message') or '')[:90]}")
