"""Scenario 8: collect stage timings from run artefacts + HTTP call durations -> artefacts/timings.json"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402

E = e2e_dir()
out: dict = {"ingest_runs": [], "agent_runs": [], "adapter_syncs": [], "http": []}
for rep in sorted(glob.glob(str(E / "ingest" / "*" / "*" / "report.json"))):
    d = json.loads(Path(rep).read_text(encoding="utf-8"))
    st = Path(rep).parent / "stages.json"
    stages = json.loads(st.read_text(encoding="utf-8")) if st.is_file() else {}
    t = d.get("timings") or {}
    out["ingest_runs"].append({
        "source": d.get("source_id"), "run_id": d.get("run_id"), "status": d.get("status"),
        "started": d.get("started_at"), "finished": d.get("finished_at"),
        "stages_s": {k: (v or {}).get("seconds") for k, v in stages.items()},
        "buckets_ms": {k: v.get("ms") for k, v in (t.get("buckets") or {}).items()},
        "model": t.get("model"), "tabular": t.get("tabular")})
for p in sorted(glob.glob(str(E / "runs" / "agent" / "*" / "*.json"))):
    r = json.loads(Path(p).read_text(encoding="utf-8"))
    md = (r.get("answer") or {}).get("markdown") or ""
    m = re.search(r"_Measured: (\d+) model call\(s\), (\d+) tool call\(s\).*?(\d+) token\(s\), (\d+) ms", md)
    out["agent_runs"].append({"agent": Path(p).parent.name, "run_id": r.get("run_id"),
                              "status": r.get("status"), "trigger": (r.get("trigger") or {}).get("type"),
                              "started": r.get("started"), "ended": r.get("ended"),
                              "models": r.get("models_used"), "steps": len(r.get("steps") or []),
                              "measured": ({"model_calls": int(m.group(1)), "tool_calls": int(m.group(2)),
                                            "tokens": int(m.group(3)), "ms": int(m.group(4))} if m else None)})
for p in sorted(glob.glob(str(E / "runs" / "adapter_agent" / "*" / "*.json"))):
    r = json.loads(Path(p).read_text(encoding="utf-8"))
    out["adapter_syncs"].append({"run_id": r.get("run_id"), "status": r.get("status"),
                                 "trigger": (r.get("trigger") or {}).get("type"),
                                 "started": r.get("started"), "ended": r.get("ended"),
                                 "connections": [(c.get("connection"), c.get("ms")) for c in r.get("connections") or []]})
for p in sorted(glob.glob(str(E / "artefacts" / "http" / "*.json"))):
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    if d.get("ms", 0) > 1500:
        out["http"].append({"file": Path(p).name, "path": d["path"], "status": d["status"], "ms": d["ms"]})
# run 2+: per-step timings with wall-clock start/end (overlap = parallelism)
steps_f = E / "artefacts" / "timings_steps.jsonl"
if steps_f.is_file():
    steps = [json.loads(x) for x in steps_f.read_text(encoding="utf-8").splitlines() if x.strip()]
    out["steps"] = [{"step": s["step"], "secs": s["secs"], "ok": s.get("ok"),
                     "start": s["start"], "end": s["end"]} for s in steps]
    top = [s for s in steps if s["step"].split(".")[0] in ("p1", "p1b", "p2", "p3", "p3c", "p3d")
           and s["step"].count(".") == 1 and ("parallel" in s["step"] or s["step"] in (
               "p1.import", "p1.plan_both", "p1.approve_both", "p1b.approve_db_vouch",
               "p2.compile.delegate", "p3d.fed.single_source_table"))]
    out["phases"] = [{"step": s["step"], "secs": s["secs"]} for s in top]
    serial = sum(s["secs"] for s in steps if s["step"].count(".") >= 1 and "parallel" not in s["step"])
    out["summary"] = {"sum_of_phase_walls_s": round(sum(s["secs"] for s in top), 1),
                      "sum_of_step_durations_s": round(serial, 1),
                      "note": "sum_of_step_durations is what the same steps would take run one after "
                              "another; sum_of_phase_walls is the measured wall clock of the "
                              "parallel phases"}
boot = E / "logs" / "boot_t0"
if boot.is_file():
    out["boot_note"] = "gateway boot measured from logs/boot_t0 to first /health 200 (see notes.md)"
(E / "artefacts" / "timings.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
for k, v in out.items():
    print(k, len(v))
for r in out["ingest_runs"][:3]:
    print(json.dumps(r)[:900])
for r in out["agent_runs"]:
    print(r["agent"], r["status"], r["trigger"], r["measured"], r["started"], r["ended"])
print(json.dumps(out.get("phases"), indent=0))
print(json.dumps(out.get("summary")))
