"""`python -m databridge_adapter_agents {list,create,sync,health,pause,resume,delete,refresh-plans}`

Prints JSON. Exit 0 on success, 1 when a sync did not fully complete (refused, busy, nothing to
run, a connection skipped or failed), 2 on a usage or registry error. Progress lines go to stderr, flushed (P9: silence means something).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from databridge_workflow_spec import SpecError, dump_spec

from .manager import AdapterAgentManager
from .registry import RegistryError


def _print(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")
    sys.stdout.flush()


def _progress(p: dict) -> None:
    sys.stderr.write(json.dumps(p, default=str) + "\n")
    sys.stderr.flush()


def main(argv: Optional[list[str]] = None, *, manager: Optional[AdapterAgentManager] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m databridge_adapter_agents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="list adapter agents")
    p_list.add_argument("--all", action="store_true", help="include deleted agents")
    p_create = sub.add_parser("create", help="register an adapter agent from a spec YAML")
    p_create.add_argument("yaml")
    p_create.add_argument("--by", default="")
    for name in ("sync", "health", "pause", "resume", "delete", "refresh-plans", "show"):
        p = sub.add_parser(name)
        p.add_argument("agent_id")
        p.add_argument("--by", default="")
        if name == "delete":
            p.add_argument("--reason", default="")
    args = ap.parse_args(argv)

    if manager is None:
        from .runner import default_manager

        manager = default_manager()
    if manager.progress_cb is None:
        manager.progress_cb = _progress

    try:
        if args.cmd == "list":
            specs = manager.list(include_deleted=args.all)
            _print({"agents": [{"id": s.id, "title": s.title, "status": s.status,
                                "connections": list(s.connections), "sync": s.sync.type,
                                "deleted": manager.registry.is_deleted(s.id)} for s in specs],
                    "problems": manager.registry.problems})
            return 0
        if args.cmd == "create":
            spec = manager.create(args.yaml, by=args.by)
            _print({"created": spec.id, "path": str(manager.registry.spec_path(spec.id))})
            return 0
        if args.cmd == "show":
            sys.stdout.write(dump_spec(manager.registry.get(args.agent_id, include_deleted=True)))
            return 0
        if args.cmd == "sync":
            summary = manager.sync(args.agent_id, trigger={"type": "manual", "via": "cli",
                                                           "by": args.by or None})
            _print(summary.as_dict())
            return 0 if summary.status == "completed" else 1
        if args.cmd == "health":
            _print(manager.health(args.agent_id))
            return 0
        if args.cmd in ("pause", "resume"):
            spec = getattr(manager, args.cmd)(args.agent_id, by=args.by)
            _print({"id": spec.id, "status": spec.status})
            return 0
        if args.cmd == "delete":
            spec = manager.delete(args.agent_id, by=args.by, reason=args.reason)
            _print({"id": spec.id, "status": spec.status, "deleted": True})
            return 0
        if args.cmd == "refresh-plans":
            spec = manager.refresh_plans(args.agent_id, by=args.by)
            _print({"id": spec.id, "plans": dict(spec.plans)})
            return 0
    except (RegistryError, SpecError) as exc:
        _print({"error": str(exc)})
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
