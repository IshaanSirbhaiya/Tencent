"""One-off: build the government pack graph into a persistent sqlite file for the /graph surface demo.
Not part of the app; run manually. Safe to re-run (idempotent upsert)."""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "core"))
sys.path.insert(0, str(REPO / "packages" / "graph"))

from databridge_graph.packs import deployment
from databridge_graph.government_seed import load_government_seed
from databridge_graph.store import GraphStore

out = REPO / "var" / "graph" / "government.db"
out.parent.mkdir(parents=True, exist_ok=True)
if out.exists():
    out.unlink()

t0 = time.perf_counter()
with deployment("government"):
    store = GraphStore(str(out))
    report = load_government_seed(store)
    counts = store.counts()
print("counts:", counts)
print("report:", report)
print("elapsed:", time.perf_counter() - t0, "s")
