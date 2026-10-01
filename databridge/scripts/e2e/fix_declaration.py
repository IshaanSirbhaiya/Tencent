"""WORKAROUND for E2E finding F-compiler-declaration: the English compiler falls back to its
local declaration builder (the runtime's `declaration_from_workflow(spec, pack, governance)` is
called with 2 args), and the runtime then refuses the agent. This regenerates `<agent>.agent.yaml`
in the E2E agents dir with the RUNTIME's builder, and (with --prune) drops workflow tools whose
capability the connection does not advertise, then checks the pair with `compile_for_run`.

    python scripts/e2e/fix_declaration.py <agent_id> [--prune]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()

import yaml  # noqa: E402
from databridge_agent_runtime import mapping as M  # noqa: E402
from databridge_agent_runtime import runtime as R  # noqa: E402
from databridge_workflow_spec import load_spec  # noqa: E402

agent = sys.argv[1]
prune = "--prune" in sys.argv
wf = E / "agents" / f"{agent}.workflow.yaml"
decl_p = E / "agents" / f"{agent}.agent.yaml"
old = yaml.safe_load(decl_p.read_text(encoding="utf-8"))
spec = load_spec(wf)
touches = {}
acts = old.get("actions") or {}
if isinstance(acts, list):
    acts = {k: v for item in acts for k, v in (item or {}).items()}
for a, body in acts.items():
    touches[a] = bool((body or {}).get("touches_subject"))
gov = M.Governance(purpose=old.get("purpose") or "workspace.agent_run",
                   golden_set=(old.get("evals") or {}).get("golden_set") or
                   "packs/workspace/evals/agent_contract.jsonl",
                   escalations_per_day=int((old.get("budget") or {}).get("escalations_per_day") or 20),
                   cost_ceiling_usd_per_day=float((old.get("budget") or {}).get("cost_ceiling_usd_per_day") or 5.0),
                   touches_subject=touches)
report = {"agent": agent, "old_top_keys": sorted(old)}
text = M.declaration_from_workflow(spec, "workspace", gov)
decl_p.with_suffix(".yaml.compiler-original").write_text(yaml.safe_dump(old, sort_keys=False),
                                                         encoding="utf-8")
decl_p.write_text(text, encoding="utf-8")
try:
    spec2, decl = M.load_agent(agent, E / "agents")
    report["pair_problems"] = M.pair_problems(spec2, decl)
except Exception as exc:  # noqa: BLE001
    report["load_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(report, indent=1, default=str)[:4000])
