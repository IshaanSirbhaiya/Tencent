"""WORKAROUND for E2E finding (passages never indexed through the gateway / adapter agent):
index every document BUILT by the listed ingest runs into databridge_retrieval, exactly as
mcp_ingest.runner._index_passages would (same BuildSession, same metadata), with the gateway's
own harness so embeddings are REAL (task_class embed -> openai/text-embedding-3-small).

    python scripts/e2e/s5_index_passages.py <source_id>:<run_id> [...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()

from databridge_gateway.llm import build_extraction_harness, detect_llm  # noqa: E402
from databridge_mcp_ingest import capture  # noqa: E402
from databridge_mcp_ingest.ledger import read_jsonl  # noqa: E402
import databridge_retrieval as retrieval  # noqa: E402

harness = build_extraction_harness(detect_llm())
out = {}
for arg in sys.argv[1:]:
    sid, rid = arg.split(":", 1)
    run_dir = E / "ingest" / sid / rid
    built = [r for r in read_jsonl(run_dir / "build.jsonl") if r.get("kind") in ("document", None)]
    res_all = []
    t0 = time.monotonic()
    session = retrieval.BuildSession(sid, harness=harness)
    with session:
        for r in built:
            meta = capture.get_meta(r["sha256"]) or {}
            res = session.index_capture(r["sha256"], {"source_id": sid, "item_id": r["item_id"],
                                                      "uri": r["uri"], "title": meta.get("title"),
                                                      "mime": meta.get("mime")})
            res_all.append({"uri": r["uri"], "status": res.status, "chunks": res.chunks,
                            "detail": getattr(res, "detail", "")})
    out[arg] = {"built": len(built), "results": res_all, "s": round(time.monotonic() - t0, 2)}
    print(arg, len(built), [(Path(x["uri"]).name, x["status"], x["chunks"]) for x in res_all])
(E / "artefacts" / "s5_index_passages.json").write_text(json.dumps(out, indent=2, default=str),
                                                      encoding="utf-8")
try:
    print(json.dumps(retrieval.stats(), default=str)[:1500])
except Exception as exc:  # noqa: BLE001
    print("stats failed", exc)
