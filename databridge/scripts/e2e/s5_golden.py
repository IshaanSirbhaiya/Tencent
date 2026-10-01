"""Scenario 5c: golden set ac-001..ac-010 through a read-only agent compiled from English; score
exact expected values, citations (resolve + reference the expected row) and abstentions.

    python scripts/e2e/s5_golden.py <agent_id> [ids...]
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from run_agent import run  # noqa: E402

GOLD = [json.loads(line) for line in
        (Path(__file__).resolve().parents[2] / "packs/workspace/evals/agent_contract.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def check_pointer(p: dict) -> str:
    kind = p.get("kind")
    if kind == "derived":
        rs = [check_pointer(b) for b in p.get("derived_from") or []]
        return "verified" if rs and all(r == "verified" for r in rs) else (rs[0] if rs else "other")
    sha = p.get("capture_digest")
    if not sha:
        return "text_unbound"
    f = E / "capture" / sha[:2] / sha
    if not f.is_file():
        return "capture_missing"
    if hashlib.sha256(f.read_bytes()).hexdigest() != sha:
        return "hash_mismatch"
    if kind == "doc_span":
        t = E / "capture" / sha[:2] / f"{sha}.txt"
        raw = t.read_text(encoding="utf-8") if t.is_file() else f.read_bytes().decode("utf-8", "replace")
        s, e = p.get("char_start"), p.get("char_end")
        return "verified" if isinstance(s, int) and isinstance(e, int) and 0 <= s <= e <= len(raw) else "out_of_bounds"
    if kind == "table_cell":
        try:
            d = json.loads(f.read_bytes())
            pk = d.get("primary_key") or []
            rows = {",".join(str(r.get(c)) for c in pk): r for r in d.get("rows") or []}
            r = rows.get(str(p.get("row_id")))
            return "verified" if r is not None and p.get("column") in r else "cell_missing"
        except Exception:  # noqa: BLE001
            return "other"
    return "other"


def cited_text(p: dict) -> str:
    sha = p.get("capture_digest")
    if not sha:
        return ""
    t = E / "capture" / sha[:2] / f"{sha}.txt"
    raw = t.read_text(encoding="utf-8") if t.is_file() else \
        (E / "capture" / sha[:2] / sha).read_bytes().decode("utf-8", "replace")
    s, e = p.get("char_start"), p.get("char_end")
    return raw[s:e] if isinstance(s, int) and isinstance(e, int) else raw


def norm(s: str) -> str:
    return re.sub(r"[\s,£]", "", str(s)).lower()


ABSTAIN = re.compile(r"(not (in|found|recorded|available|connected)|no (source|record|salesforce|"
                     r"connection|discount|information)|cannot|can't|withheld|blocked|unable|"
                     r"does not (record|contain|say|exist)|candidate|human|confirm|abstain|"
                     r"no evidence|isn't|is not)", re.I)


def _same_item(a: str, b: str) -> bool:
    """A withheld item is named by its path; the runtime may record it relative to a different root
    than the golden line, so the trailing path components are compared."""
    pa, pb = Path(str(a)).parts, Path(str(b)).parts
    n = min(len(pa), len(pb))
    return n > 0 and [x.lower() for x in pa[-n:]] == [x.lower() for x in pb[-n:]]


def outcome_check(g: dict, rec: dict) -> dict:
    """Assert the golden case's STRUCTURED `outcome` (R-021) against the run record's OWN
    `outcome` (agent_runtime `RunOutcome`, RTF2), field by field. INT5 (RTF2 cross-track): nothing
    is inferred from `skipped` codes or from the markdown any more — the runtime computes the
    outcome from structure, and a record without one FAILS here rather than being guessed at.
    Every expectation is read from the golden line; nothing is written down here."""
    want = g.get("outcome") or {}
    out = rec.get("outcome")
    if not isinstance(out, dict):
        return {"want": want, "got": None, "checks": {"outcome_recorded": False}, "pass": False}
    got = {k: out.get(k) for k in want}
    res: dict = {}
    for key, w in want.items():
        have = out.get(key)
        if key == "candidates":
            vals = {(str(c.get("subject_kind")), str(c.get("value"))) for c in have or []}
            res[key] = all((str(c.get("subject_kind")), str(c.get("value"))) in vals
                           for c in w or [])
            res["candidates_cited"] = all(
                any(str(c.get("value")) == str(h.get("value")) and h.get("cites")
                    for h in have or []) for c in w or [])
        elif key == "withheld":
            res[key] = all(any(_same_item(x.get("item", ""), h.get("item", ""))
                               and set(x.get("rules") or []) <= set(h.get("rules") or [])
                               and (not x.get("decision") or x["decision"] == h.get("decision"))
                               for h in have or []) for x in w or [])
        elif key == "not_connected":
            res[key] = {str(n).lower() for n in w or []} <= {str(n).lower() for n in have or []}
        else:
            res[key] = have == w
    return {"want": want, "got": got, "checks": res, "pass": bool(res) and all(res.values())}


def evaluate(g: dict, r: dict) -> dict:
    rec = r["record"]
    ans = (rec.get("answer") or {})
    md = ans.get("markdown") or ""
    cites = ans.get("citations") or []
    res = [check_pointer(c) for c in cites]
    row = {"id": g["id"], "ask": g["ask"], "status": rec.get("status"), "run_id": rec.get("run_id"),
           "secs": round(r["ms"] / 1000, 1), "n_citations": len(cites),
           "citations_resolve": sum(1 for x in res if x == "verified"),
           "citation_failures": [x for x in res if x != "verified"],
           "unsupported": len(ans.get("unsupported_claims") or []),
           "unsupported_claims": ans.get("unsupported_claims") or [],
           "tokens": rec.get("tokens"), "cost_usd": rec.get("cost_usd"),
           "tool_calls": len(rec.get("steps") or []), "answer": md[:1500]}
    if g["expect_abstain"]:
        row["expect"] = "abstain"
        row["abstained_heuristic"] = bool(ABSTAIN.search(md))
        row["outcome"] = outcome_check(g, rec)
    else:
        ok_vals, ok_cites = [], []
        for field, spec in g["expect"].items():
            ok_vals.append(norm(spec["value"]) in norm(md))
            refs = [spec["cite"]] if spec.get("cite") else list(spec.get("cites") or [])
            keys = {x.split("/", 1)[1].split("#")[0].split(":")[-1] for x in refs if "/" in x}
            texts = " ".join(cited_text(c) for c in cites)
            ok_cites.append(all(k in texts for k in keys) if keys else True)
        row["expect"] = g["expect"]
        row["value_ok"] = all(ok_vals)
        row["cite_references_row"] = all(ok_cites)
    return row


def main(agent: str, only: list[str], parallel_: bool = False) -> list:
    todo = [g for g in GOLD if not only or g["id"] in only]
    if parallel_:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=len(todo) or 1) as ex:
            runs = list(ex.map(lambda g: run(agent, g["ask"], f"s5_golden_{agent}_{g['id']}",
                                             quiet=True), todo))
    else:
        runs = [run(agent, g["ask"], f"s5_golden_{agent}_{g['id']}", quiet=True) for g in todo]
    results = [evaluate(g, r) for g, r in zip(todo, runs)]
    for row in results:
        print(json.dumps({k: v for k, v in row.items() if k not in ("answer", "unsupported_claims")},
                         default=str)[:900])
    out = E / "artefacts" / f"s5_golden_{agent}.json"
    prev = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    keep = [p for p in prev if p["id"] not in {r["id"] for r in results}]
    out.write_text(json.dumps(keep + results, indent=2, default=str), encoding="utf-8")
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[2:] if a != "--parallel"]
    main(sys.argv[1], args, parallel_="--parallel" in sys.argv)
