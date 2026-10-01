"""Run an agent through the e2e gateway (wait) and print the essentials; saves the response."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_client import call  # noqa: E402


def run(agent: str, question: str, name: str, quiet: bool = False) -> dict:
    st, d, ms = call("POST", f"/agents/{agent}/run", {"question": question, "wait": True},
                     name=name, timeout=3600)
    r = d.get("record") or {}
    a = r.get("answer") or {}
    if not quiet:
        print(st, round(ms / 1000, 1), "s", r.get("run_id"), r.get("status"))
        print("models:", json.dumps({k: r.get(k) for k in r if "model" in k or "produced" in k},
                                    default=str)[:600])
        print("coverage:", json.dumps(r.get("coverage"), default=str)[:800])
        print("STEPS", [(s.get("tool"), s.get("policy_decision"), s.get("ms")) for s in r.get("steps") or []])
        print("ANSWER:\n", (a.get("markdown") or "")[:3500])
        print("CITES", len(a.get("citations") or []), "UNSUPPORTED", a.get("unsupported_claims"))
        print("ACTIONS", json.dumps(r.get("actions"), default=str)[:1500])
        if st != 200:
            print(json.dumps(d)[:800])
    return {"status": st, "ms": ms, "record": r, "raw": d}


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "agent_run")
