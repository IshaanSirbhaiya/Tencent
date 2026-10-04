"""Tabular build benchmark (E2E run 5 F-02: measure before and after).

Loads a fixture estate (`tests/estates.py`: `finance` = core_banking + crm, `workspace` = Larkspur)
into sqlite, registers it as a source read through the REAL `crawl.fetch_table`, has a named test
person approve the plan and the proposed mapping, and runs the real `run_ingest` twice:

  run 1   proposes the mapping (nothing built until a person approves it)
  run 2   the full build of every approved table: the run the E2E measured at 27 min

Prints the stage seconds, the build's own breakdown and the graph file's size. Nothing touches the
network or a model; everything is written under a throw-away directory.

    python packages/mcp_ingest/bench/bench_tables.py --estate finance
    python packages/mcp_ingest/bench/bench_tables.py --estate finance --include core_banking.* \
        --profile prof.out --json out.json
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
DATABRIDGE = PKG.parents[1]
for _p in (PKG, PKG / "tests", *(DATABRIDGE / "packages" / n for n in
                                 ("core", "harness", "convert", "graph", "intake", "adapter",
                                  "ingest", "retrieval", "agent_runtime", "agentdef",
                                  "workflow_spec", "semantic", "outbox"))):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _profile_dir(base: Path, name: str, schemas) -> Path:
    import yaml

    from databridge_mcp_ingest.plans import sql_dialect

    d = base / "profiles"
    d.mkdir(exist_ok=True)
    dialect = dict(sql_dialect("sqlite"))
    dialect["name"] = f"estate_{name}"
    dialect["list_tables"] = " UNION ALL ".join(
        f"SELECT '{s}' AS table_schema, name AS table_name FROM {s}.sqlite_master "
        f"WHERE type = 'table'" for s in schemas) + " ORDER BY 1, 2"
    (d / f"estate_{name}.sql.yaml").write_text(yaml.safe_dump(dialect), encoding="utf-8")
    (d / f"estate_{name}.profile.yaml").write_text(yaml.safe_dump({
        "id": f"estate-{name}", "title": "bench estate", "priority": 100,
        "verified": "bench fixture", "match": {"all_tools": ["query"]},
        "step": {"strategy": "tabular", "sql": {"tool": "query", "arg": "sql",
                                                "dialect": f"estate_{name}",
                                                "parse": {"mode": "json_rows"}}}}),
        encoding="utf-8")
    return d


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--estate", default="finance", choices=("finance", "workspace"))
    ap.add_argument("--include", action="append", default=None,
                    help="tables_include glob(s); default every table of the estate")
    ap.add_argument("--profile", default=None, help="write a cProfile of the build run here")
    ap.add_argument("--json", default=None)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--mapping-from", default=None,
                    help="a mapping YAML (e.g. an E2E run's); every table the rules leave "
                         "UNMAPPED takes its node/edges/split from it, as a person's edit")
    ap.add_argument("--lift-holds", action="store_true",
                    help="before the build, a person approves an override for every item or "
                         "row the screen sent to review in the propose run")
    args = ap.parse_args(argv)

    import estates

    base = Path(tempfile.mkdtemp(prefix="bench_tables_"))
    env = {"WALNUT_SOURCES_DIR": base / "sources", "WALNUT_INGEST_DIR": base / "ingest",
           "WALNUT_CAPTURE_DIR": base / "capture", "WALNUT_CACHE_DIR": base / "cache",
           "WALNUT_EVENTS_PATH": base / "events" / "events.jsonl",
           "WALNUT_GRAPH_DB_PATH": base / "graph" / "bench.db",
           "WALNUT_PASSAGES_DIR": base / "passages", "WALNUT_FACTS_DIR": base / "facts",
           "WALNUT_SOURCE_REGISTRATION": "operator"}
    key = base / "keys" / "approval.key"
    key.parent.mkdir(parents=True)
    key.write_text(os.urandom(32).hex(), encoding="utf-8")
    env["WALNUT_APPROVAL_KEY_FILE"] = key
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ.pop("WALNUT_APPROVAL_KEY", None)
    os.environ["WALNUT_CRAWL_PROFILES_DIR"] = str(_profile_dir(
        base, args.estate, estates.ESTATES[args.estate]["schemas"]))

    from databridge_agent_runtime import register_authenticator
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore

    import databridge_mcp_ingest as mi
    from databridge_mcp_ingest import mapping, plans

    person = register_authenticator("bench-tables").issue("Bench Approver")
    db = estates.build_estate(args.estate, base / "e.sqlite")
    client = estates.engine_client(db)
    sid = f"bench_{args.estate}"
    include = args.include or [f"{s}.*" for s in estates.ESTATES[args.estate]["schemas"]]
    env["WALNUT_GRAPH_DB_PATH"].parent.mkdir(parents=True, exist_ok=True)
    out: dict = {"estate": args.estate, "include": include, "runs": []}
    with deployment(estates.ESTATES[args.estate]["pack"]) as vocab:
        mi.register_connection({"id": sid, "transport": {"type": "stdio", "command": "uvx"},
                                "scope": {"tables_include": include}})
        plans.propose_plan(sid, client=client)
        plans.approve_plan(sid, principal=person, client=client)
        store = GraphStore(str(env["WALNUT_GRAPH_DB_PATH"]))
        for label in ("propose", "build"):
            if label == "build":
                if args.mapping_from:
                    import yaml

                    given = (yaml.safe_load(Path(args.mapping_from).read_text(
                        encoding="utf-8")) or {}).get("tables") or {}
                    m = mapping.load_mapping(sid)
                    upd = {}
                    for t, tm in m.tables.items():
                        g = dict(given.get(t) or {})
                        if tm.status == "unmapped" and g.get("node"):
                            g.pop("approval", None)
                            g.update(status="proposed", origin="manual",
                                     links=[lk for lk in g.get("links") or []
                                            if lk.get("source") == sid])
                            upd[t] = mapping.TableMapping.model_validate(g)
                    mapping.save_mapping(m.model_copy(update={"tables": {**m.tables, **upd}}))
                mapping.approve_mapping(sid, principal=person, vocab=vocab)
                if args.lift_holds:
                    from databridge_mcp_ingest import screen
                    from databridge_mcp_ingest.runner import read_jsonl

                    prev = Path(out["runs"][-1]["run_dir"])
                    for g in read_jsonl(prev / "governance.jsonl"):
                        if g.get("decision") == "review":
                            screen.approve_override(sid, g["item_id"], digest=g["sha256"],
                                                    principal=person, reason="bench")
                    for b in read_jsonl(prev / "governance_chunks.jsonl"):
                        if b.get("decision") == "review" and b.get("row_ref"):
                            screen.approve_override(sid, b["row_ref"], digest=b["row_digest"],
                                                    principal=person, reason="bench")
            progress: list = []
            prof = cProfile.Profile() if (args.profile and label == "build") else None
            t0 = time.perf_counter()
            if prof:
                prof.enable()
            rep = mi.run_ingest(sid, store=store, client=client, vocab=vocab,
                                progress_cb=progress.append)
            if prof:
                prof.disable()
                prof.dump_stats(args.profile)
            wall = time.perf_counter() - t0
            stages = json.loads((Path(rep.run_dir) / "stages.json").read_text(encoding="utf-8"))
            gdb = env["WALNUT_GRAPH_DB_PATH"]
            size = sum(p.stat().st_size for p in gdb.parent.glob(gdb.name + "*"))
            run = {"label": label, "status": rep.status, "run_dir": str(rep.run_dir), "wall_s": round(wall, 2),
                   "stages_s": {k: v.get("seconds") for k, v in stages.items()},
                   "build_breakdown": (stages.get("build") or {}).get("breakdown"),
                   "graph_bytes": size,
                   "nodes": store.fetchall("SELECT COUNT(*) AS n FROM nodes "
                                           "WHERE system_to IS NULL")[0]["n"],
                   "edges": store.fetchall("SELECT COUNT(*) AS n FROM edges "
                                           "WHERE system_to IS NULL")[0]["n"],
                   "progress_stages": sorted({p.get("stage") for p in progress
                                              if isinstance(p, dict)}),
                   "progress_events": len(progress)}
            out["runs"].append(run)
            print(json.dumps(run, indent=1, default=str))
        store.close()
    if args.profile:
        pstats.Stats(args.profile).sort_stats("cumulative").print_stats(40)
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    if not args.keep:
        import shutil

        db.close()
        shutil.rmtree(base, ignore_errors=True)
    return out


if __name__ == "__main__":
    main()
