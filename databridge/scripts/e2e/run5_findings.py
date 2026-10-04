"""E2E5: append/update one finding in <run>/findings.json (incremental; nothing lost on interrupt).

    python scripts/e2e/run5_findings.py ID SEVERITY "title" --owner X --repro "..." --observed "..."
                                        [--expected "..."] [--evidence "..."] [--scenario N]
Also: python scripts/e2e/run5_findings.py --scenario-result N RESULT "evidence"
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main(argv):
    E = Path(os.environ["WALNUT_E2E_DIR"])
    p = E / "findings.json"
    data = json.loads(p.read_text("utf-8")) if p.is_file() else {
        "run": E.name, "estate": (E / "estate.txt").read_text().strip(), "by": "E2E5 (claude-opus-5-5)",
        "findings": [], "scenarios": {}}
    if argv[0] == "--scenario-result":
        data["scenarios"][argv[1]] = {"result": argv[2], "evidence": argv[3],
                                      "at": time.strftime("%H:%M:%S", time.gmtime())}
    else:
        fid, sev, title = argv[0], argv[1], argv[2]
        rec = {"id": fid, "severity": sev, "title": title}
        it = iter(argv[3:])
        for a in it:
            rec[a.lstrip("-")] = next(it)
        rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data["findings"] = [f for f in data["findings"] if f["id"] != fid] + [rec]
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("findings:", len(data["findings"]), "scenarios:", len(data["scenarios"]))


if __name__ == "__main__":
    main(sys.argv[1:])
