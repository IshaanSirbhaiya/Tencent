"""E2E3 phase 3c (after phase 3: no adapter sync in flight, since a sync voids a materialisation's pins):

  mat     /materialise: labelled tabular naming the outcome field, chunk_corpus, the four random
          dataset requests; downloads incl. the <stem>.pointers.jsonl sidecar; every sidecar pointer
          re-read to its cell (capture + live Postgres / capture text); then the withheld path
          (python -m databridge_materialise.testing withhold) -> dataset + pointers 409, receipt +
          plan 200; second principal 404 on everything
  loop    the CRM write E2E3 approved: next adapter sync re-ingests it (item_changed); the row is
          then deleted (only that row)
  act     /activity as analyst / second / operator; run detail incl. delegation tree; refusals

    python scripts/e2e/run3_phase3c.py [only...]
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import setup_env  # noqa: E402

E = setup_env()
from e2e_client import call, raw, tokens  # noqa: E402
from run2_lib import load, parallel, poll, save, step  # noqa: E402
import run3_phase3 as p3  # noqa: E402

R = json.loads((E / "random_requests.json").read_text(encoding="utf-8"))
MAT_REQ = {
    "labelled": "One row per invoice at its issue date, with the customer, project, net and VAT "
                "amounts as features; the label is the invoice status 30 days after it was issued.",
    "chunks": "A chunk corpus of every passage indexed for search, for retrieval evaluation.",
    **R["datasets"],
}
DL = E / "materialise_downloads"


def _num(a, b) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return str(a).strip() == str(b).strip()


def _span_texts(p: dict) -> list:
    sha = p.get("capture_digest") or ""
    f = E / "capture" / sha[:2] / sha
    texts = [q.read_text(encoding="utf-8") for q in (f.with_name(sha + ".txt"), f.with_name(sha + ".redacted.txt"))
             if q.is_file()] or ([f.read_bytes().decode("utf-8", "replace")] if f.is_file() else [])
    s, e = p.get("char_start"), p.get("char_end")
    return [t[s:e] for t in texts if isinstance(s, int) and isinstance(e, int) and e <= len(t)]


def reread(ptr: dict, value) -> dict:
    """Re-read one SourcePointer to the cell it names; compare with the dataset value."""
    kind = ptr.get("kind")
    if kind == "derived":
        subs = [reread(b, None) for b in ptr.get("derived_from") or []]
        ok = bool(subs) and all(s["ok"] for s in subs)
        m = re.match(r"text_slice@v1\(start=(\d+),end=(\d+)\)", str(ptr.get("derivation") or ""))
        if ok and m and value is not None and len(ptr.get("derived_from") or []) == 1:
            # replay the slice over the base span's captured text: it must give the cell's value
            base = ptr["derived_from"][0]
            spans = _span_texts(base)
            a, b = int(m.group(1)), int(m.group(2))
            ok = any(sp[a:b] == str(value) for sp in spans)
            return {"kind": kind, "ok": ok, "replayed": "text_slice", "subs": len(subs)}
        return {"kind": kind, "ok": ok, "subs": len(subs)}
    src = str(ptr.get("source_id") or "")
    if kind == "table_cell" and src.startswith("passages:"):
        import sqlite3
        db = E / "passages" / (src.split(":", 1)[1] + ".sqlite")
        if not db.is_file():
            return {"kind": kind, "ok": False, "why": "passage store missing"}
        col, tbl = ptr.get("column"), ptr.get("table")
        row = sqlite3.connect(str(db)).execute(
            f"select {col} from {tbl} where {col} = ?", (ptr.get("row_id"),)).fetchone()             if col and tbl and re.fullmatch(r"\w+", col) and re.fullmatch(r"\w+", tbl) else None
        ok = row is not None and (value is None or str(row[0]) == str(value))
        return {"kind": kind, "ok": ok, "store": db.name}
    if kind == "table_cell" and src.startswith("graph:") and ptr.get("table") == "nodes":
        # E2E4: a cohort key cites the graph store's own node row (rowid_pk); re-read it there
        import sqlite3
        col = ptr.get("column")
        if not (col and re.fullmatch(r"\w+", col)):
            return {"kind": kind, "ok": False, "why": "bad column"}
        row = sqlite3.connect(str(E / "graph" / "workspace.db")).execute(
            f"select {col} from nodes where rowid_pk = ?", (ptr.get("row_id"),)).fetchone()
        ok = row is not None and (value is None or str(row[0]) == str(value))
        return {"kind": kind, "ok": ok, "store": "graph nodes", "read": row[0] if row else None}
    sha = ptr.get("capture_digest")
    if not sha:
        return {"kind": kind, "ok": False, "why": "no capture_digest"}
    f = E / "capture" / sha[:2] / sha
    if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != sha:
        return {"kind": kind, "ok": False, "why": "capture missing or hash mismatch"}
    if kind == "table_cell":
        d = json.loads(f.read_bytes())
        pk = d.get("primary_key") or []
        rows = {",".join(str(r.get(c)) for c in pk): r for r in d.get("rows") or []}
        row = rows.get(str(ptr.get("row_id")))
        col = ptr.get("column")
        if row is None or col not in row:
            return {"kind": kind, "ok": False, "why": "cell missing in snapshot"}
        snap = row[col]
        table = ptr.get("table") or d.get("table") or ""
        live = None
        if table and pk:
            where = " and ".join(f"{c}::text = '{v}'" for c, v in zip(pk, str(ptr["row_id"]).split(",")))
            live = p3.psql(f"select {col}::text from {table} where {where}")
        ok = (value is None or _num(snap, value)) and (live is None or _num(live, snap))
        return {"kind": kind, "ok": bool(ok), "snapshot": snap, "live": live, "value": value}
    if kind == "doc_span":
        texts = [p.read_text(encoding="utf-8") for p in (f.with_name(sha + ".txt"),
                                                          f.with_name(sha + ".redacted.txt"))
                 if p.is_file()]
        if not texts:
            texts = [f.read_bytes().decode("utf-8", "replace")]
        s, e = ptr.get("char_start"), ptr.get("char_end")
        spans = [t[s:e] for t in texts if isinstance(s, int) and isinstance(e, int) and e <= len(t)]
        ok = bool(spans) and (value is None or any(str(value) in sp or sp in str(value)
                                                   for sp in spans))
        return {"kind": kind, "ok": ok}
    return {"kind": kind, "ok": False, "why": "unknown pointer kind"}


def check_sidecar(ph: str, files: dict) -> dict:
    name = next((n for n in files if n.endswith(".pointers.jsonl")), None)
    data = next((n for n in files if n.startswith("mp_") and n.endswith(".jsonl")
                 and not n.endswith(".pointers.jsonl")), None)
    if not name or not data or files[name]["status"] != 200:
        return {"sidecar": name, "ok": False, "why": "no downloadable pointers file"}
    rows = [json.loads(x) for x in (DL / ph / data).read_text(encoding="utf-8").splitlines() if x.strip()]
    out = {"sidecar": name, "lines": 0, "grounded": 0, "reread_ok": 0, "failures": []}
    for line in (DL / ph / name).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out["lines"] += 1
        ptr = rec.get("pointer")
        if not ptr:
            continue
        out["grounded"] += 1
        row = rows[rec["row"]] if isinstance(rec.get("row"), int) and rec["row"] < len(rows) else {}
        val = row.get(rec.get("column")) if isinstance(row, dict) else None
        if ptr.get("kind") == "derived" and rec.get("column") != "text":
            val = None   # a span's offsets/ids share the text's pointer: verify its base only
        r = reread(ptr, val)
        if r["ok"]:
            out["reread_ok"] += 1
        elif len(out["failures"]) < 10:
            out["failures"].append({"row": rec.get("row"), "column": rec.get("column"), **r})
    out["ok"] = out["grounded"] > 0 and out["reread_ok"] == out["grounded"]
    return out


def mat():
    out: dict = {"catalogue_status": call("GET", "/materialise/catalogue", name="p3c_mat_catalogue")[0]}

    def build(k):
        with step(f"p3c.mat.{k}"):
            st, d, _ = call("POST", "/materialise", {"request": MAT_REQ[k]}, name=f"p3c_mat_{k}")
            if st != 202:
                return {"request": MAT_REQ[k], "status": st, "body": d}
            j = poll(f"/materialise/jobs/{d['job_id']}", f"p3c_mat_{k}_job")
            res = {"request": MAT_REQ[k], "job": j}
            ph = (j.get("result") or {}).get("plan_hash")
            if ph and j.get("status") == "succeeded":
                st, rc, _ = call("GET", f"/materialise/receipts/{ph}", name=f"p3c_mat_{k}_receipt")
                res["receipt"] = rc
                res["downloads"] = {}
                for f in rc.get("files") or []:
                    name = f if isinstance(f, str) else (f.get("name") or "")
                    r = raw("GET", f"/materialise/receipts/{ph}/files/{name}")
                    res["downloads"][name] = {"status": r.status_code, "bytes": len(r.content)}
                    if r.status_code == 200:
                        (DL / ph).mkdir(parents=True, exist_ok=True)
                        (DL / ph / name).write_bytes(r.content)
                res["sidecar_check"] = check_sidecar(ph, res["downloads"])
                sec = tokens()["second"]
                res["isolation"] = {
                    "job": call("GET", f"/materialise/jobs/{d['job_id']}", name=f"p3c_mat_{k}_iso_job", token=sec)[0],
                    "receipt": call("GET", f"/materialise/receipts/{ph}", name=f"p3c_mat_{k}_iso_rc", token=sec)[0],
                    "download": [raw("GET", f"/materialise/receipts/{ph}/files/{n}", token=sec).status_code
                                 for n in res["downloads"]]}
            return res

    r = parallel(**{k: (lambda k=k: build(k)) for k in MAT_REQ})
    out.update({k: (v if not isinstance(v, Exception) else repr(v)) for k, v in r.items()})
    # the withheld path, on one published dataset (prefer the chunk corpus)
    pick = next((k for k in ("chunks", "labelled", "d2_rag_meeting_notes") if isinstance(out.get(k), dict)
                 and out[k].get("downloads")), None)
    if pick:
        ph = out[pick]["job"]["result"]["plan_hash"]
        with step("p3c.mat.withhold"):
            cp = subprocess.run([sys.executable, "-m", "databridge_materialise.testing", "withhold",
                                 str(E / "materialise"), ph, "pointer"], capture_output=True, text=True,
                                timeout=120, cwd=str(Path(__file__).resolve().parents[2]))
            w = {"dataset": pick, "plan_hash": ph, "cli_rc": cp.returncode,
                 "cli_out": (cp.stdout + cp.stderr)[-600:], "after": {}}
            for name in out[pick]["downloads"]:
                rr = raw("GET", f"/materialise/receipts/{ph}/files/{name}")
                w["after"][name] = {"status": rr.status_code, "body": rr.text[:300] if rr.status_code != 200 else None}
        out["withhold"] = w
    return out


def loop():
    """The CRM row E2E3's approval wrote: re-ingested on the next sync, then removed."""
    ov = load("p3_overdue.json") or {}
    before = set((ov.get("rows_before") or "").split())
    rows = [r for r in (ov.get("db_rows") or "").splitlines() if r and r.split("|")[0] not in before]
    out: dict = {"new_rows": rows}
    with step("p3c.loop.sync"):
        out["sync"] = p3.aa_sync("p3c_loop_sync")
    # what the files/db runs recorded for follow_up_tasks
    runs = sorted((E / "ingest" / "larkspur_db").glob("*/report.json"), key=lambda p: p.stat().st_mtime)
    if runs:
        rep = json.loads(runs[-1].read_text(encoding="utf-8"))
        out["last_db_run"] = {"run": runs[-1].parent.name, "status": rep.get("status"),
                              "counts": rep.get("counts")}
        ev = runs[-1].parent / "changes.jsonl"
        out["changes_file"] = ev.read_text(encoding="utf-8")[:4000] if ev.is_file() else None
    evf = E / "events" / "events.jsonl"
    out["events_follow_up"] = [json.loads(x) for x in evf.read_text(encoding="utf-8").splitlines()
                               if "follow_up_tasks" in x][-10:] if evf.is_file() else []
    st, m, _ = call("GET", "/sources/larkspur_db", name="p3c_loop_source")
    out["mapping_tables"] = {t: (v or {}).get("status") for t, v in
                             (((m.get("mapping") or {}).get("tables")) or {}).items()}
    ids = [r.split("|")[0] for r in rows]
    if ids:
        out["deleted"] = p3.psql(f"delete from public.follow_up_tasks where task_id in ({','.join(ids)}) "
                                 f"returning task_id")
    out["rows_after"] = p3.psql("select task_id from public.follow_up_tasks order by 1")
    return out


def act():
    out: dict = {}
    for who in ("primary", "second", "operator"):
        st, d, _ = call("GET", "/activity", name=f"p3c_activity_{who}", params={"limit": 200},
                        token=tokens()[who])
        rows = d.get("items") or []
        out[f"list_{who}"] = {"status": st, "n": len(rows), "total_visible": d.get("total_visible"),
                              "kinds": dict(__import__("collections").Counter(str(r.get("kind")) for r in rows)),
                              "run_owners": sorted({str(r.get("run_owner")) for r in rows}),
                              "ids": [r.get("id") for r in rows],
                              "outcomes": sorted({str(r.get("outcome") or r.get("status")) for r in rows})}
        st, d, _ = call("GET", "/activity/automations", name=f"p3c_activity_auto_{who}", token=tokens()[who])
        out[f"automations_{who}"] = {"status": st, "n": len((d or {}).get("automations") or [])}
    # run details: every visible run of the analyst, opened by each principal
    prim = [r for r in (call("GET", "/activity", params={"limit": 200}, name="p3c_act_prim2")[1].get("items") or [])
            if r.get("run_kind") and r.get("run_id")]
    det = {}
    for r in prim[:40]:
        rid, kind, owner = r["run_id"], r["run_kind"], r.get("run_owner")
        for who in ("primary", "second", "operator"):
            st, d, _ = call("GET", f"/activity/runs/{rid}", params={"kind": kind, "owner": owner} if owner else {"kind": kind},
                            name=f"p3c_act_run_{who}", token=tokens()[who])
            det[f"{kind}:{rid}/{who}"] = {"status": st, "body_hash": hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16] if st == 404 else None,
                                          "keys": sorted(d) if isinstance(d, dict) else None,
                                          "tree": (d.get("delegation_tree") if isinstance(d, dict) and kind == "federated_run" else None)}
    out["runs"] = det
    # a missing run, for the identical-404 comparison
    st, d, _ = call("GET", "/activity/runs/run-20990101T000000-deadbeef", params={"kind": "agent_run"},
                    name="p3c_act_missing", token=tokens()["second"])
    out["missing_404"] = {"status": st, "body_hash": hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]}
    return out


ALL = {"mat": mat, "loop": loop, "act": act}

if __name__ == "__main__":
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or list(ALL)
    # mat and loop both need a quiet platform: the loop's sync must not void mat's pins
    for group in (["mat"], ["loop", "act"]):
        g = [k for k in group if k in only]
        if not g:
            continue
        with step("p3c.parallel_" + "_".join(g)):
            res = parallel(**{k: ALL[k] for k in g})
        for k, v in res.items():
            save(f"p3c_{k}.json", v if not isinstance(v, Exception) else {"error": repr(v)})
        print("done", {k: ("ERR " + repr(v)[:300]) if isinstance(v, Exception) else "ok" for k, v in res.items()})
