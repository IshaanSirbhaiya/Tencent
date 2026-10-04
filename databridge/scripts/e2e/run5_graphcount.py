"""E2E5: read-only graph counts (+ rows written since an instant) for the run's estate graph."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir, graph_db  # noqa: E402

c = sqlite3.connect(f"{graph_db(e2e_dir()).resolve().as_uri()}?mode=ro", uri=True)
out = {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("nodes", "edges", "sources")}
cols = {t: [r[1] for r in c.execute(f"pragma table_info({t})")] for t in ("nodes", "edges", "sources")}
since = sys.argv[1] if len(sys.argv) > 1 else None
if since:
    for t in ("nodes", "edges", "sources"):
        col = next((x for x in ("system_from", "recorded_at", "created_at") if x in cols[t]), None)
        if col:
            out[f"{t}_since"] = c.execute(f"select count(*) from {t} where {col} >= ?", (since,)).fetchone()[0]
print(json.dumps(out))
