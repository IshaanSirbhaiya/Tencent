"""``python -m databridge_automation`` — compile English, serve triggers, fire one by hand.

    compile "every weekday at 9am, …"   [--json] [--offline] [--answer ID=VALUE …] [--save] [--activate]
    serve                               [--poll SECONDS] [--runner module:function] [--once]
    fire <agent-or-adapter-id>          [--runner module:function]
    status

The model is used only when a key is configured (ANTHROPIC_API_KEY / OPENAI_API_KEY) and
``--offline`` is not given; otherwise the draft comes from the deterministic pre-pass alone.
Without ``--runner`` the engine records firings but runs nothing (status ``no_runner``) — the
gateway attaches the real runners (agent runtime / adapter-agent manager).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    """Make the sibling packages importable when run from a bare checkout (no install)."""
    pkgs = Path(__file__).resolve().parents[2]
    for name in ("automation", "workflow_spec", "agentdef", "adapter", "harness", "core"):
        p = str(pkgs / name)
        if p not in sys.path:
            sys.path.append(p)


_bootstrap()

from . import paths  # noqa: E402
from .catalogue import load_catalogue  # noqa: E402
from .compiler import ActivationRefused, compile_english, save_draft  # noqa: E402
from .engine import TriggerEngine, load_specs_from_dirs  # noqa: E402


def _harness(offline: bool):
    if offline or os.environ.get("DATABRIDGE_FORCE_GOLD"):
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        return None
    from databridge_harness import Harness
    return Harness()


def _runner(spec: str | None):
    if not spec:
        return None
    mod, _, attr = spec.partition(":")
    fn = getattr(importlib.import_module(mod), attr or "run")
    if not callable(fn):
        raise SystemExit(f"--runner {spec!r} is not callable")
    return fn


def _existing_ids() -> list[str]:
    specs, _ = load_specs_from_dirs()
    return [getattr(s, "agent", None) or getattr(s, "id") for s in specs]


def cmd_compile(args) -> int:
    cat = load_catalogue(existing_ids=_existing_ids())
    answers = dict(a.split("=", 1) for a in args.answer or [] if "=" in a)
    res = compile_english(args.text, cat, harness=_harness(args.offline), answers=answers)
    if args.json:
        print(json.dumps(res.as_record(), indent=2, default=str))
    else:
        print(res.understood)
        if res.open_questions:
            print("\nQuestions:")
            for q in res.open_questions:
                opts = f"  [{', '.join(q.options)}]" if q.options else ""
                print(f"  - ({q.id}) {q.text}{opts}")
        if res.spec_yaml:
            print("\n--- spec ---\n" + res.spec_yaml)
        if res.declaration_yaml:
            print("--- declaration ---\n" + res.declaration_yaml)
        if res.compilation:
            print(f"compile_agent: {res.compilation.outcome}")
            for r in res.compilation.refusals:
                print(f"  - {r['code']}: {r['detail'][:200]}")
            if res.compilation.reason:
                print(f"  - {res.compilation.reason}")
    if args.save or args.activate:
        try:
            for p in save_draft(res, activate=args.activate):
                print(f"wrote {p}")
        except ActivationRefused as exc:
            print(f"not saved: {exc}", file=sys.stderr)
            return 2
    return 0 if not res.open_questions else 1


def _engine(args) -> TriggerEngine:
    def loader():
        specs, problems = load_specs_from_dirs()
        for p in problems:
            print(f"spec problem: {p}", file=sys.stderr)
        return specs

    return TriggerEngine(_runner(getattr(args, "runner", None)), spec_loader=loader,
                         poll_interval=getattr(args, "poll", 5.0))


_NO_RUNNER = ("no --runner: nothing would run, and every trigger would be recorded as no_runner "
              "and never run later. Pass --runner module:function, or --record-only to record "
              "firings without running them.")


def _runner_required(args) -> bool:
    if getattr(args, "runner", None) or getattr(args, "record_only", False):
        return False
    print(_NO_RUNNER, file=sys.stderr)
    return True


def cmd_serve(args) -> int:
    if _runner_required(args):
        return 2
    engine = _engine(args)

    async def main() -> None:
        engine.reload()
        if args.once:
            for rec in await engine.run_once():
                print(json.dumps(rec, default=str))
            await engine.drain()
            return
        print(f"trigger engine: {len(engine.specs)} spec(s); events {engine.events_path}; "
              f"firings {engine.firings.path}", flush=True)
        await engine.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await engine.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_fire(args) -> int:
    if _runner_required(args):
        return 2
    engine = _engine(args)

    async def main() -> dict:
        engine.reload()
        rec = await engine.fire(args.id, by="cli")
        await engine.drain()
        return rec

    try:
        rec = asyncio.run(main())
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(rec, indent=2, default=str))
    return 0 if rec.get("status") == "succeeded" else 1        # no_runner: nothing ran


def cmd_status(args) -> int:
    engine = _engine(args)
    engine.reload()
    print(json.dumps(engine.status(), indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m databridge_automation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="compile plain English into a draft")
    c.add_argument("text")
    c.add_argument("--json", action="store_true")
    c.add_argument("--offline", action="store_true", help="rules only, no model")
    c.add_argument("--answer", action="append", help="ID=VALUE answer to an open question")
    c.add_argument("--save", action="store_true", help=f"write the draft under {paths.agents_dir()}")
    c.add_argument("--activate", action="store_true", help="save as active (refused unless compiled)")
    s = sub.add_parser("serve", help="run the trigger engine")
    s.add_argument("--poll", type=float, default=5.0)
    s.add_argument("--runner")
    s.add_argument("--once", action="store_true", help="one poll + one tick, then exit")
    s.add_argument("--record-only", action="store_true",
                   help="no runner: record firings as no_runner (they never run later)")
    f = sub.add_parser("fire", help="fire one agent / adapter agent now")
    f.add_argument("id")
    f.add_argument("--runner")
    f.add_argument("--record-only", action="store_true",
                   help="no runner: record the firing as no_runner (exit 1: nothing ran)")
    sub.add_parser("status", help="registered specs and their next fire")
    args = ap.parse_args(argv)
    return {"compile": cmd_compile, "serve": cmd_serve, "fire": cmd_fire,
            "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
