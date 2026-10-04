"""E2E5: how many graph pointer rows cite a source (read-only). -> prints JSON; with a label also
writes <run>/artefacts/srccount_<label>.json"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir, graph_db  # noqa: E402

src = sys.argv[1]
c = sqlite3.connect(f"{graph_db(e2e_dir()).resolve().as_uri()}?mode=ro", uri=True)
n = c.execute("select count(*) from sources where source_pointer_json like ?",
              (f'%"source_id": "{src}"%',)).fetchone()[0]
n2 = c.execute("select count(*) from sources where source_pointer_json like ?",
               (f'%"source_id":"{src}"%',)).fetchone()[0]
out = {"source": src, "pointer_rows": n + n2, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
if len(sys.argv) > 2:
    (e2e_dir() / "artefacts" / f"srccount_{sys.argv[2]}.json").write_text(json.dumps(out), "utf-8")
print(json.dumps(out))
