"""Scenario 5a: /brain/query over the shared graph — 5 golden asks + 3 cross-source asks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_client import call  # noqa: E402
from e2e_env import e2e_dir  # noqa: E402

QS = [("ac-001", "what is the status of invoice INV-2026-0057?"),
      ("ac-002", "what is the total of invoice INV-2026-0041 including VAT?"),
      ("ac-004", "which city is Corriedale Grocers based in?"),
      ("ac-005", "who is Pellucid Freightways' head of procurement?"),
      ("ac-008", "how many open invoices does Pellucid Freightways have?"),
      ("x-1", "Which contract governs our work with Pellucid Freightways, and what liability cap does it set?"),
      ("x-2", "Who at Larkspur owns the Pellucid Freightways account, and which of their invoices are still open?"),
      ("x-3", "Which project do we run for Tamsinbay Water Authority and who is their contact?")]

def main():
  label = sys.argv[1] if len(sys.argv) > 1 else "brain"
  out = []
  for qid, q in QS:
      st, d, ms = call("POST", "/brain/query", {"question": q}, name=f"s5_{label}_{qid}", timeout=300)
      out.append({"id": qid, "q": q, "status": st, "ms": round(ms), "answer": d.get("answer"),
                  "refusal": d.get("refusal"), "citations": d.get("citations"),
                  "nodes_used": d.get("nodes_used"), "passages": len(d.get("passages") or []),
                  "is_real_llm": d.get("is_real_llm")})
      print(qid, st, round(ms), "|", (d.get("answer") or json.dumps(d.get("refusal")))[:400].replace("\n", " "))
      print("   cites:", json.dumps(d.get("citations"))[:300], "passages:", len(d.get("passages") or []))
  (e2e_dir() / "artefacts" / f"s5_{label}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
