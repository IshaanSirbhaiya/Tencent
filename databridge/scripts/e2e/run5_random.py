"""E2E5 scenario 12: the random requests (written before any fixer test was read), one round each.

    python scripts/e2e/run5_random.py <run>/random_requests.json [ids...]
question -> /brain/query; dataset -> /materialise {request}; agent -> /agents/compile;
one_time -> /intelligence/one-time/compile (+ /run when runnable). -> artefacts/s12_random.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import estate_name, setup_env  # noqa: E402

E = setup_env()
from e2e_client import call  # noqa: E402


def main(path, only):
    reqs = json.loads(Path(path).read_text("utf-8"))[estate_name()]
    outp = E / "artefacts" / "s12_random.json"
    out = json.loads(outp.read_text("utf-8")) if outp.is_file() else {}
    for r in reqs:
        if only and r["id"] not in only:
            continue
        cat, t, n = r["cat"], r["text"], f"s12_{r['id']}"
        if cat == "question":
            st, d, ms = call("POST", "/brain/query", {"question": t}, name=n, timeout=900)
            summary = d.get("answer") or json.dumps(d.get("refusal") or d.get("questions") or d)[:800]
        elif cat == "dataset":
            st, d, ms = call("POST", "/materialise", {"request": t}, name=n, timeout=900)
            summary = json.dumps({k: d.get(k) for k in ("status", "refusal", "questions",
                                                       "question_items", "job_id", "plan")
                                  if d.get(k) is not None}, default=str)[:1200]
        elif cat == "agent":
            st, d, ms = call("POST", "/agents/compile", {"text": t}, name=n, timeout=900)
            summary = json.dumps({"activatable": d.get("activatable"),
                                  "understood": (d.get("understood") or "")[:500],
                                  "open_questions": [q.get("text") for q in d.get("open_questions") or []],
                                  "refusal": d.get("refusal")}, default=str)[:1500]
        else:
            st, d, ms = call("POST", "/intelligence/one-time/compile", {"text": t}, name=n, timeout=900)
            summary = {"runnable": d.get("runnable"),
                       "open_questions": [q.get("text") for q in d.get("open_questions") or []]}
            if d.get("runnable"):
                st2, d2, _ = call("POST", "/intelligence/one-time/run", {"draft_id": d["draft_id"]},
                                  name=n + "_run", timeout=900)
                summary["run"] = {"status": st2, "state": d2.get("state"),
                                  "answer": (((d2.get("run") or {}).get("answer") or {})
                                             .get("markdown") or "")[:1500]}
            summary = json.dumps(summary, default=str)[:2500]
        out[r["id"]] = {"cat": cat, "text": t, "status": st, "ms": round(ms), "response": summary}
        print(r["id"], st, round(ms), str(summary)[:500].replace("\n", " "), flush=True)
        outp.write_text(json.dumps(out, indent=2, default=str), "utf-8")


if __name__ == "__main__":
    main(sys.argv[1], set(sys.argv[2:]))
