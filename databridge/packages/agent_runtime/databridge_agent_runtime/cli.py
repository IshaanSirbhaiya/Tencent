"""``python -m databridge_agent_runtime`` — run an agent, list what awaits approval, approve it.

    run <agent> [--question TEXT] [--by NAME] [--agents-dir DIR]
    pending [--agent AGENT]
    approve <run_id> <action_id> --dev-os-principal
    reject  <run_id> <action_id> --dev-os-principal [--reason TEXT]
    show    <run_id>

``approve`` / ``reject`` are a DEVELOPMENT path (R-021 #5). The CLI has no authentication of its
own, so it decides only when ``--dev-os-principal`` is passed, as the account the operating system's
security token names (the Windows access token's SID-backed account / the POSIX effective uid) —
never ``%USERNAME%`` / ``$USER`` / ``$LOGNAME``, which any caller can set, and never a typed name.
The principal is minted by a ``dev_only`` authenticator and every event it produces says so. In
production, approvals go through the gateway, which authenticates the request.

Failures are said in plain words with a non-zero exit, never as a stack trace (P19). ``run`` needs a
model: with no ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` it says so and stops rather than
pretending with a canned backend.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import paths


def _say(msg: str) -> None:
    print(msg, file=sys.stderr)


def _open_store():
    p = paths.graph_db_path()
    if not p.is_file():
        return None
    from databridge_graph.store import GraphStore           # noqa: PLC0415
    return GraphStore(str(p))


def _cmd_run(ns: argparse.Namespace) -> int:
    from .mapping import MappingError, load_agent           # noqa: PLC0415
    from .runtime import run_agent                          # noqa: PLC0415
    from .tools import default_gateway, default_retriever   # noqa: PLC0415
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        _say("No model is configured (set ANTHROPIC_API_KEY or OPENAI_API_KEY). The agent was not "
             "run.")
        return 2
    try:
        spec, decl = load_agent(ns.agent, Path(ns.agents_dir) if ns.agents_dir else None)
    except (MappingError, ValueError, OSError) as e:
        _say(f"The agent could not be loaded: {e}")
        return 2
    from databridge_harness import Harness                  # noqa: PLC0415
    gateway = default_gateway()
    try:
        record = run_agent(spec, decl, store=_open_store(), harness=Harness(), gateway=gateway,
                           retriever=default_retriever(),
                           trigger={"type": "manual", "by": ns.by, "question": ns.question})
    finally:
        close = getattr(gateway, "close", None)
        if callable(close):
            close()
    print(record.answer.markdown)
    print(f"\nrun {record.run_id}: {record.status}", file=sys.stderr)
    return 0 if record.status == "completed" else 1


def _cmd_pending(ns: argparse.Namespace) -> int:
    from .runtime import pending_actions                    # noqa: PLC0415
    print(json.dumps(pending_actions(agent=ns.agent), indent=2, default=str))
    return 0


#: The CLI's authentication mechanism. DEV-ONLY: it is marked so on every event it produces.
DEV_OS_MECHANISM = "dev:os_token"


def os_account() -> str:
    """The account the OS SECURITY TOKEN of this process names — not an environment variable.

    Windows: ``GetUserNameExW(NameSamCompatible)`` / ``GetUserNameW``, which read the calling
    thread's access token. POSIX: the password-database entry of the EFFECTIVE uid, falling back
    to ``uid:<n>``. `getpass.getuser()` is not used: it reads ``LOGNAME``/``USER``/``USERNAME``
    first, which any caller can set (R-021 #5)."""
    if sys.platform == "win32":
        import ctypes                                        # noqa: PLC0415
        from ctypes import wintypes                          # noqa: PLC0415
        size = wintypes.DWORD(0)
        secur32 = ctypes.WinDLL("secur32")
        fn = secur32.GetUserNameExW
        fn.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        fn.restype = wintypes.BOOLEAN
        fn(2, None, ctypes.byref(size))                      # 2 = NameSamCompatible (DOMAIN\user)
        if size.value:
            buf = ctypes.create_unicode_buffer(size.value + 1)
            if fn(2, buf, ctypes.byref(size)) and buf.value:
                return buf.value
        adv = ctypes.WinDLL("advapi32")
        size2 = wintypes.DWORD(257)
        buf2 = ctypes.create_unicode_buffer(257)
        if adv.GetUserNameW(buf2, ctypes.byref(size2)) and buf2.value:
            return buf2.value
        raise OSError("the process token names no account")
    uid = os.geteuid()                                       # type: ignore[attr-defined]
    try:
        import pwd                                           # noqa: PLC0415
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return f"uid:{uid}"


def os_principal():
    """The CLI's principal: the OS token's account, minted by the DEV-ONLY authenticator. Only
    reached behind ``--dev-os-principal``; a ``--by NAME`` typed on the command line is refused."""
    from .runtime import register_authenticator             # noqa: PLC0415
    return register_authenticator(DEV_OS_MECHANISM, dev_only=True).issue(os_account())


def _cmd_decide(ns: argparse.Namespace) -> int:
    from .runtime import ActionRefused, approve, reject     # noqa: PLC0415
    from .tools import default_gateway                      # noqa: PLC0415
    if ns.by is not None:
        _say(f"--by is not accepted: a typed name is not an authenticated principal. This "
             f"decision would be recorded as the signed-in OS user; run it without --by.")
        return 1
    if not ns.dev_os_principal:
        _say("The CLI cannot authenticate an approver. Approve through the gateway, which "
             "authenticates the request; or, on a DEVELOPMENT machine only, pass "
             "--dev-os-principal to decide as the operating-system account (recorded as a "
             "dev-only principal). Nothing was decided.")
        return 1
    try:
        who = os_principal()
    except Exception as e:                                  # noqa: BLE001 - plain words
        _say(f"The signed-in user could not be determined ({e}); nothing was decided.")
        return 1
    try:
        if ns.cmd == "approve":
            gateway = default_gateway()
            try:
                rec = approve(ns.run_id, ns.action_id, who, gateway=gateway)
            finally:
                close = getattr(gateway, "close", None)
                if callable(close):
                    close()
        else:
            rec = reject(ns.run_id, ns.action_id, who, reason=ns.reason or "")
    except (ActionRefused, FileNotFoundError) as e:
        _say(str(e))
        return 1
    from .runs import index_for                             # noqa: PLC0415
    print(json.dumps(rec.actions[index_for(ns.action_id)].model_dump(mode="json"), indent=2))
    return 0


def _cmd_show(ns: argparse.Namespace) -> int:
    from .runs import find_run, read_run                    # noqa: PLC0415
    try:
        print(json.dumps(read_run(find_run(ns.run_id)).to_dict(), indent=2, ensure_ascii=False))
    except FileNotFoundError as e:
        _say(str(e))
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m databridge_agent_runtime")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run an agent once")
    r.add_argument("agent")
    r.add_argument("--question", default=None)
    r.add_argument("--by", default=None, help="the person asking (recorded as on_behalf_of)")
    r.add_argument("--agents-dir", default=None)
    p = sub.add_parser("pending", help="actions awaiting a person")
    p.add_argument("--agent", default=None)
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("run_id")
        d.add_argument("action_id")
        d.add_argument("--by", default=None, help=argparse.SUPPRESS)   # refused: see os_principal
        d.add_argument("--dev-os-principal", action="store_true",
                       help="DEVELOPMENT ONLY: decide as the OS account this process runs as")
        if name == "reject":
            d.add_argument("--reason", default="")
    s = sub.add_parser("show", help="print a run record")
    s.add_argument("run_id")
    ns = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):              # a Windows console is not UTF-8 by default
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    return {"run": _cmd_run, "pending": _cmd_pending, "approve": _cmd_decide,
            "reject": _cmd_decide, "show": _cmd_show}[ns.cmd](ns)


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
