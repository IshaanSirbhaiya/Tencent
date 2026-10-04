"""E2E5: ask /brain/query a list of questions, sequentially; summary -> <run>/artefacts/<label>.json.

    python scripts/e2e/run5_brain.py LABEL questions.json [--as who]
questions.json: [{"id": "...", "q": "..."}, ...]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WALNUT_E2E_BASE", "http://127.0.0.1:8015")
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, tokens  # noqa: E402


def main(argv):
    label, qf = argv[0], argv[1]
    who = argv[argv.index("--as") + 1] if "--as" in argv else "primary"
    out = []
    for q in json.loads(Path(qf).read_text(encoding="utf-8")):
        body = {"question": q["q"], **(q.get("extra") or {})}
        st, d, ms = call("POST", "/brain/query", body, name=f"{label}_{q['id']}", timeout=900,
                         token=tokens()[who])
        rec = {"id": q["id"], "q": q["q"], "status": st, "ms": round(ms),
               "routed_to": d.get("routed_to"), "answer": d.get("answer"),
               "refusal": d.get("refusal"), "question_back": d.get("question") or d.get("clarify"),
               "citations": d.get("citations"), "keys": sorted(d)[:40]}
        out.append(rec)
        print(q["id"], st, round(ms), d.get("routed_to"), "|",
              (d.get("answer") or json.dumps(d.get("refusal") or d)[:600] or "")[:600].replace("\n", " "),
              flush=True)
        (E / "artefacts" / f"{label}.json").write_text(json.dumps(out, indent=2, default=str),
                                                      encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
