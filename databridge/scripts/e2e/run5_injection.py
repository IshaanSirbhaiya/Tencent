"""E2E5: what the screen decided for the planted injection file, from the run's own ledgers.

    python scripts/e2e/run5_injection.py <source_id> <run_id> <relative file path>
-> <run>/artefacts/s11_injection.json {decision, rules, built}
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402

E = e2e_dir()
src, run, rel = sys.argv[1:4]
R = E / "ingest" / src / run


def lines(name):
    p = R / name
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()] if p.is_file() else []


cap = [c for c in lines("captured.jsonl") if rel.replace("\\", "/") in json.dumps(c).replace("\\\\", "/")]
ids = {c.get("item_id") for c in cap}
gov = [g for g in lines("governance.jsonl") if g.get("item_id") in ids]
built = [b for b in lines("build.jsonl") if b.get("item_id") in ids]
out = {"source": src, "run": run, "file": rel, "item_ids": sorted(i for i in ids if i),
       "decision": gov[-1]["decision"] if gov else None,
       "rules": [f["rule"] for f in (gov[-1].get("fired") or [])] if gov else [],
       "built": bool(built)}
(E / "artefacts" / "s11_injection.json").write_text(json.dumps(out, indent=2), "utf-8")
print(json.dumps(out))
