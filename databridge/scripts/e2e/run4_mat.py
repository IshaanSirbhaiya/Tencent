"""E2E4 M: /materialise from English, answering the planner's questions through `answers` (MAT4's
P-10 fix) instead of re-wording. Reuses run3_phase3c's downloads, sidecar re-read, isolation and
withheld path.

    python scripts/e2e/run4_mat.py [core] [random]

core   the labelled tabular request (run 3's verbatim sentence) + a chunk corpus; when the planner
       asks, the scripted user answers ONCE from the sentence's own intent (see `user_answer`),
       then the withheld path on one published dataset.
random the four random dataset requests, ONE round only (graded on the first response).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, raw, tokens  # noqa: E402
from run2_lib import parallel, poll, save, step  # noqa: E402
import run3_phase3c as p3c  # noqa: E402

CORE = {
    "labelled": "One row per invoice at its issue date, with the customer, project, net and VAT "
                "amounts as features; the label is the invoice status 30 days after it was issued.",
    "chunks": "A chunk corpus of every passage indexed for search, for retrieval evaluation.",
}


def user_answer(q: dict) -> str:
    """What the person who typed the request would answer (never reads code or specs)."""
    t = (q.get("text") or "").lower()
    opts = q.get("options") or []
    if "lag" in t or "maturity" in t or "mature" in t:
        return "P0D: the label is read exactly 30 days after the issue date."
    if opts and len(opts) == 1:
        return opts[0]
    pas = [o for o in opts if "passage" in str(o).lower()]
    if pas:
        return pas[0]
    if "passage" in t or "entity" in t or "store" in t or "which" in t and "text" in t:
        import re
        m = re.findall(r"passages\.[\w.-]+", q.get("text") or "")
        if m:
            return m[0]
    if "max_chars" in t or "length" in t or "limit" in t:
        return "No limit."
    return "As stated in the request."


def _job(k: str, request: str, answers=None, label=""):
    body = {"request": request}
    if answers:
        body["answers"] = answers
    st, d, _ = call("POST", "/materialise", body, name=f"p4_mat_{k}{label}")
    if st != 202:
        return {"status": st, "body": d}
    j = poll(f"/materialise/jobs/{d['job_id']}", f"p4_mat_{k}{label}_job")
    return {"status": st, "job_id": d["job_id"], "job": j}


def _finish(k: str, res: dict) -> dict:
    j = res.get("job") or {}
    ph = (j.get("result") or {}).get("plan_hash")
    if not (ph and j.get("status") == "succeeded"):
        return res
    st, rc, _ = call("GET", f"/materialise/receipts/{ph}", name=f"p4_mat_{k}_receipt")
    res["receipt"] = rc
    res["downloads"] = {}
    for f in rc.get("files") or []:
        name = f if isinstance(f, str) else (f.get("name") or "")
        r = raw("GET", f"/materialise/receipts/{ph}/files/{name}")
        res["downloads"][name] = {"status": r.status_code, "bytes": len(r.content)}
        if r.status_code == 200:
            (p3c.DL / ph).mkdir(parents=True, exist_ok=True)
            (p3c.DL / ph / name).write_bytes(r.content)
    res["sidecar_check"] = p3c.check_sidecar(ph, res["downloads"])
    sec = tokens()["second"]
    res["isolation"] = {
        "job": call("GET", f"/materialise/jobs/{res['job_id']}", name=f"p4_mat_{k}_iso_job", token=sec)[0],
        "receipt": call("GET", f"/materialise/receipts/{ph}", name=f"p4_mat_{k}_iso_rc", token=sec)[0],
        "download": [raw("GET", f"/materialise/receipts/{ph}/files/{n}", token=sec).status_code
                     for n in res["downloads"]]}
    return res


def core_one(k: str) -> dict:
    with step(f"p4.mat.{k}.r0"):
        r0 = _job(k, CORE[k])
    out = {"request": CORE[k], "r0": r0}
    ref = ((r0.get("job") or {}).get("refusal") or (r0.get("job") or {}).get("result") or {})
    items = ref.get("question_items") if isinstance(ref, dict) else None
    final = r0
    if items:
        answers = {q["id"]: user_answer(q) for q in items}
        out["answers"] = answers
        with step(f"p4.mat.{k}.r1"):
            final = _job(k, CORE[k], answers, "_r1")
        out["r1"] = final
        ref = ((final.get("job") or {}).get("refusal") or {})
        items2 = ref.get("question_items") if isinstance(ref, dict) else None
        if items2:   # a second, different question: answer once more
            answers.update({q["id"]: user_answer(q) for q in items2 if q["id"] not in answers})
            with step(f"p4.mat.{k}.r2"):
                final = _job(k, CORE[k], answers, "_r2")
            out["r2"] = final
    out["final"] = _finish(k, dict(final))
    return out


def core() -> dict:
    out = parallel(**{k: (lambda k=k: core_one(k)) for k in CORE})
    out = {k: (v if not isinstance(v, Exception) else {"error": repr(v)}) for k, v in out.items()}
    pick = next((k for k in ("labelled", "chunks") if (out.get(k) or {}).get("final", {}).get("downloads")), None)
    if pick:
        f = out[pick]["final"]
        ph = f["job"]["result"]["plan_hash"]
        names = list(f["downloads"])
        w = {"dataset": pick, "plan_hash": ph,
             "before": {n: raw("GET", f"/materialise/receipts/{ph}/files/{n}").status_code for n in names}}
        with step("p4.mat.withhold"):
            import os
            cp = subprocess.run([sys.executable, "-m", "databridge_materialise.testing", "withhold",
                                 str(E / "materialise"), ph, "pointer"], capture_output=True, text=True,
                                timeout=120, cwd=str(Path(__file__).resolve().parents[2]),
                                env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        w["cli"] = {"rc": cp.returncode, "out": (cp.stdout + cp.stderr)[-600:]}
        w["after"] = {}
        for n in names:
            r = raw("GET", f"/materialise/receipts/{ph}/files/{n}")
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = None
            w["after"][n] = {"status": r.status_code,
                             "code": ((body or {}).get("refusal") or {}).get("code") if isinstance(body, dict) else None}
        out["withhold"] = w
    return out


def random_() -> dict:
    R = json.loads((E / "random_requests.json").read_text(encoding="utf-8"))
    jobs = {k: (lambda k=k, t=t: {"request": t, **_job(k, t)}) for k, t in R["datasets"].items()}
    with step("p4.mat.random"):
        r = parallel(**jobs)
    return {k: (v if not isinstance(v, Exception) else {"error": repr(v)}) for k, v in r.items()}


if __name__ == "__main__":
    only = sys.argv[1:] or ["core", "random"]
    for k in only:
        with step(f"p4.mat.{k}"):
            v = {"core": core, "random": random_}[k]()
        save(f"p4_mat_{k}.json", v)
        print("done", k, flush=True)
