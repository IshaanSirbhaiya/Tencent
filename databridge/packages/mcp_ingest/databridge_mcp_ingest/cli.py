"""`python -m databridge_mcp_ingest {connect,plan,approve,run,status,list}`.

    connect  --id files --command npx --arg=-y --arg @modelcontextprotocol/server-filesystem \\
             --arg C:/data [--via mcpjungle] [--auto-approve] [--include "**/*.md"] ...
    connect  --from-infra [infra/mcp_servers.yaml] [--route direct|mcpjungle] [--only workspace_files]
    plan     <id>                 connect, list tools, propose a plan (rules first, model second)
    approve  <id> --dev-os-principal [--vouch TOOL ...] [--mapping]
                                  approve the plan as the signed-in OS account (a DEV-ONLY
                                  authenticated principal, recorded as such); a typed --by name
                                  is refused (FXI3, Codex f1 #1)
    run      <id> [--store PATH] [--resume RUN_ID] [--no-model]
    status   <id> [--run RUN_ID]  the report of the latest (or a given) run
    list                          connections, plan status, last run

An --arg that itself starts with "-" is written --arg=-y (argparse would read it as a flag).
Progress is printed with an explicit flush (set PYTHONUNBUFFERED=1 for detached runs, P9).
Failures print one plain sentence, not a stack trace (P19).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _harness(no_model: bool):
    if no_model:
        return None
    from .llm import default_harness

    return default_harness()


def _incomplete(exc) -> int:
    """Codex f2 #11: a WRITE connection whose live tool advert failed is an INCOMPLETE
    registration (the package raises `RegistrationIncomplete`); it is reported, exit 1."""
    for c in exc.connections:
        print(f"registered {c.id} (via {c.via})", flush=True)
    for f in exc.failures:
        print(f"registered {f['id']} but its tool advert FAILED: {f['error']} (finish it with "
              f"`connect --from-infra --only {f['id']}` once the server is reachable)", flush=True)
    return 1


def cmd_connect(a) -> int:
    """Registration authority comes from the DEPLOYMENT (`WALNUT_SOURCE_REGISTRATION=operator`,
    Codex f2 #10), not from this command line: without it the remote policy applies."""
    from .connections import RegistrationIncomplete, import_declarations, register_connection

    if a.from_infra is not None:
        path = Path(a.from_infra) if a.from_infra else None
        try:
            conns = import_declarations(path, route=a.route, only=a.only or None,
                                        auto_approve=a.auto_approve)
        except RegistrationIncomplete as exc:
            return _incomplete(exc)
        for c in conns:
            print(f"registered {c.id} (via {c.via})", flush=True)
        if not conns:
            print("no server declarations matched", flush=True)
            return 1
        return 0
    if not a.id:
        print("connect needs --id (or --from-infra)", flush=True)
        return 2
    if a.url:
        transport = {"type": "http", "url": a.url,
                     "headers_from": dict(h.split("=", 1) for h in a.header_from or [])}
    elif a.command:
        transport = {"type": "stdio", "command": a.command, "args": list(a.arg or []),
                     "env_from": dict(e.split("=", 1) for e in a.env_from or []),
                     "cwd": a.cwd}
    else:
        print("connect needs --command (stdio) or --url (streamable HTTP)", flush=True)
        return 2
    scope = {k: v for k, v in {"roots": a.root, "include": a.include, "exclude": a.exclude,
                               "blocked": a.blocked, "file_types": a.file_type}.items() if v}
    if a.max_bytes:
        scope["max_bytes"] = a.max_bytes
    try:
        conn = register_connection({"id": a.id, "title": a.title or a.id,
                                    "transport": transport, "via": a.via, "scope": scope,
                                    "auto_approve": a.auto_approve})
    except RegistrationIncomplete as exc:
        return _incomplete(exc)
    print(f"registered {conn.id} (via {conn.via})", flush=True)
    return 0


def cmd_plan(a) -> int:
    from .plans import propose_plan

    plan = propose_plan(a.id, harness=_harness(a.no_model))
    print(f"plan for {a.id}: status={plan.status} origin={plan.origin} "
          f"profiles={plan.profiles} steps={[s.id for s in plan.steps]}", flush=True)
    for n in plan.notes:
        print(f"  note: {n}", flush=True)
    for p in plan.problems:
        print(f"  PROBLEM: {p}", flush=True)
    return 0 if not plan.problems else 1


def cmd_approve(a) -> int:
    """Approve as an AUTHENTICATED principal. The CLI's only authenticator is the agent runtime's
    DEV-ONLY OS-token login (`--dev-os-principal`); the principal it issues is sealed, marked
    `dev_only`, and recorded as such on the approval. A typed `--by NAME` is refused."""
    import inspect

    from .plans import approve_plan

    if a.by is not None:
        print("--by is not accepted: a typed name is not an authenticated principal. Approve as "
              "the signed-in OS account with --dev-os-principal (recorded as dev-only), or "
              "through the gateway as the signed-in user.", flush=True)
        return 2
    if not a.dev_os_principal:
        print("approve needs an authenticated principal: pass --dev-os-principal (dev only), or "
              "approve through the gateway.", flush=True)
        return 2
    from databridge_agent_runtime.cli import os_principal

    principal = os_principal()
    plan = approve_plan(a.id, principal=principal, vouch_read_tools=list(a.vouch or []) or None)
    print(f"plan for {a.id} approved by {plan.approved_by} "
          f"({plan.approval.get('authenticated_by')}, dev_only="
          f"{plan.approval.get('dev_only')})", flush=True)
    if a.mapping:
        from .mapping import approve_mapping

        if "principal" in inspect.signature(approve_mapping).parameters:
            m = approve_mapping(a.id, principal=principal)
        else:                                               # pragma: no cover - older mapping
            m = approve_mapping(a.id, approved_by=principal)
        print(f"mapping approved: "
              f"{sorted(t for t, tm in m.tables.items() if tm.status == 'approved')}",
              flush=True)
    return 0


def cmd_run(a) -> int:
    from databridge_graph.store import GraphStore

    from . import paths
    from .runner import run_ingest

    db = Path(a.store) if a.store else paths.graph_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(str(db))
    harness = _harness(a.no_model)
    if harness is None:
        print("no model configured: documents will be captured, screened and anchored, but no "
              "entities extracted", flush=True)
    report = run_ingest(a.id, store=store, harness=harness, run_id=a.resume, verbose=True)
    print(json.dumps({"status": report.status, "message": report.message,
                      "run_dir": report.run_dir, "counts": {k: v for k, v in report.counts.items()
                                                            if k != "ledgers_found"}},
                     indent=1, default=str), flush=True)
    return 0 if report.status == "ok" else 1


def cmd_status(a) -> int:
    from .runner import list_runs

    runs = list_runs(a.id)
    if not runs:
        print(f"no runs for {a.id}", flush=True)
        return 1
    run = next((r for r in runs if r["run_id"] == a.run), None) if a.run else runs[-1]
    if run is None:
        print(f"no run {a.run} for {a.id}", flush=True)
        return 1
    rep = Path(run["run_dir"]) / "report.json"
    print(rep.read_text(encoding="utf-8") if rep.is_file() else json.dumps(run), flush=True)
    return 0


def cmd_list(a) -> int:
    from .connections import list_connections
    from .plans import PlanError, load_plan
    from .runner import list_runs

    conns = list_connections()
    if not conns:
        print("no connections registered", flush=True)
    for c in conns:
        try:
            plan = load_plan(c.id).status
        except PlanError:
            plan = "none"
        runs = list_runs(c.id)
        last = f"{runs[-1]['run_id']} {runs[-1]['status']}" if runs else "never run"
        print(f"{c.id:24s} via={c.via:9s} plan={plan:9s} last={last}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="databridge_mcp_ingest")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("connect")
    c.add_argument("--id")
    c.add_argument("--title")
    c.add_argument("--command")
    c.add_argument("--arg", action="append")
    c.add_argument("--env-from", action="append", help="CHILD_VAR=HOST_VAR")
    c.add_argument("--cwd")
    c.add_argument("--url")
    c.add_argument("--header-from", action="append", help="Header=HOST_VAR")
    c.add_argument("--via", choices=["direct", "mcpjungle"], default="direct")
    c.add_argument("--root", action="append")
    c.add_argument("--include", action="append")
    c.add_argument("--exclude", action="append")
    c.add_argument("--blocked", action="append")
    c.add_argument("--file-type", action="append")
    c.add_argument("--max-bytes", type=int)
    c.add_argument("--auto-approve", action="store_true")
    c.add_argument("--from-infra", nargs="?", const="", default=None)
    c.add_argument("--route", choices=["direct", "mcpjungle"], default="direct")
    c.add_argument("--only", action="append")
    c.set_defaults(fn=cmd_connect)
    pl = sub.add_parser("plan")
    pl.add_argument("id")
    pl.add_argument("--no-model", action="store_true")
    pl.set_defaults(fn=cmd_plan)
    ap = sub.add_parser("approve")
    ap.add_argument("id")
    ap.add_argument("--by", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--dev-os-principal", action="store_true",
                    help="approve as the signed-in OS account (dev-only principal)")
    ap.add_argument("--vouch", action="append",
                    help="vouch, by name, that an unclassified tool only reads")
    ap.add_argument("--mapping", action="store_true")
    ap.set_defaults(fn=cmd_approve)
    r = sub.add_parser("run")
    r.add_argument("id")
    r.add_argument("--store")
    r.add_argument("--resume")
    r.add_argument("--no-model", action="store_true")
    r.set_defaults(fn=cmd_run)
    st = sub.add_parser("status")
    st.add_argument("id")
    st.add_argument("--run")
    st.set_defaults(fn=cmd_status)
    ls = sub.add_parser("list")
    ls.set_defaults(fn=cmd_list)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except Exception as exc:                                         # noqa: BLE001
        # FXI3 (Codex f1 #6): a boundary message never repeats a secret it may quote
        from .secret_scan import redact

        print(f"error: {redact(str(exc))}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":                                           # pragma: no cover
    raise SystemExit(main())
