"""Scenario 5b: hybrid retrieval with REAL embeddings; check every passage pointer resolves."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
import databridge_retrieval as retrieval  # noqa: E402

from s5_brain import QS  # noqa: E402  (same 8 questions)


def resolves(ptr: dict, text: str) -> str:
    sha = ptr.get("capture_digest")
    if not sha:
        return "text_unbound"
    f = E / "capture" / sha[:2] / sha
    if not f.is_file():
        return "capture_missing"
    if hashlib.sha256(f.read_bytes()).hexdigest() != sha:
        return "hash_mismatch"
    t = (E / "capture" / sha[:2] / f"{sha}.txt")
    raw = t.read_text(encoding="utf-8") if t.is_file() else ""
    s, e = ptr.get("char_start"), ptr.get("char_end")
    if not isinstance(s, int) or not isinstance(e, int) or e > len(raw):
        return "out_of_bounds"
    span = " ".join(raw[s:e].split())
    probe = " ".join(text.split())[:60]
    red = E / "capture" / sha[:2] / f"{sha}.redacted.txt"
    rspan = " ".join(red.read_text(encoding="utf-8")[s:e].split()) if red.is_file() else ""
    return "verified" if (probe in span or probe in rspan) else "value_not_in_span"


out = []
for qid, q in QS:
    try:
        ps = retrieval.search(q, k=5, mode="hybrid")
    except Exception as exc:  # noqa: BLE001
        out.append({"id": qid, "error": f"{type(exc).__name__}: {exc}"})
        print(qid, "ERROR", exc)
        continue
    rows = []
    for p in ps:
        d = p.model_dump(mode="json") if hasattr(p, "model_dump") else dict(p)
        ptr = d.get("pointer") or {}
        rows.append({"doc": str(d.get("locator") or d.get("doc_id"))[-60:],
                     "source": d.get("source_id"), "score": d.get("score"),
                     "resolves": resolves(ptr, d.get("text") or ""),
                     "text": (d.get("text") or "")[:120],
                     "embedding": d.get("embedding_model") or d.get("embed_model")})
    out.append({"id": qid, "q": q, "passages": rows})
    print(qid, [(r["doc"], round(r["score"] or 0, 3), r["resolves"]) for r in rows])
(E / "artefacts" / "s5_retrieval.json").write_text(json.dumps(out, indent=2, default=str),
                                                 encoding="utf-8")
