"""E2E5 scenario 6: one delegated (federated) cross-source question, answered back once if the
planner asks, plus the ungranted second principal. -> <run>/artefacts/<label>.json

    python scripts/e2e/run5_fed.py LABEL "question" [answers-module-function]
Answers come from run3_phase3.answer_for (workspace) or run5_fed_answers.answer_for(estate).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import estate_name, setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402


def answers_for(items):
    if estate_name() == "workspace":
        from run3_phase3 import answer_for  # noqa: PLC0415
        return {q["id"]: answer_for(q["text"]) for q in items}
    return {q["id"]: "As stated in the question; use the source's own column for it." for q in items}


def ask(label, q, answers=None, who="primary"):
    body = {"question": q, "wait": True}
    if answers:
        body["answers"] = answers
    st, d, ms = call("POST", "/federated/ask", body, name=label, timeout=1800, token=tokens()[who])
    t0 = time.time()
    while st in (200, 202) and d.get("job_id") and d.get("status") in ("running", "queued") \
            and time.time() - t0 < 900:
        time.sleep(3)
        st, d, _ = call("GET", f"/federated/jobs/{d['job_id']}", save=False, token=tokens()[who])
    return {"status": st, "ms": round(ms), "body": d}


def main(label, q):
    out = {"question": q, "second": ask(label + "_second", q, who="second")}
    r0 = ask(label + "_r0", q)
    out["r0"] = r0
    items = r0["body"].get("question_items") or []
    if items and not ((r0["body"].get("table") or {}).get("rows")):
        out["r1"] = ask(label + "_r1", q, answers_for(items))
    (E / "artefacts" / f"{label}.json").write_text(json.dumps(out, indent=2, default=str), "utf-8")
    for k in ("second", "r0", "r1"):
        if k in out:
            b = out[k]["body"]
            print(k, out[k]["status"], out[k]["ms"], "coverage", (b.get("coverage") or {}).get("status"),
                  "rows", len((b.get("table") or {}).get("rows") or []),
                  "questions", [x.get("text", "")[:120] for x in b.get("question_items") or []],
                  "refusal", json.dumps(b.get("refusal"))[:200])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
