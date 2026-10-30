"""Write the three WorkBuddy demo workspaces, each with a project-level .workbuddy/mcp.json.

    python databridge/scripts/workbuddy_setup.py [--out DIR] [--hours N] [--ib-url URL]
                                                 [--user-config | --compliance-user-config]

Each workspace acts as ONE persona: its MCP server runs databridge_ib.mcp_server (or the audit
server for compliance) with that persona's short-lived bearer token, minted by ib_mint_token.py
from IB_TOKEN_KEY in databridge/.env.ib.local. Tokens are secrets, so the default output folder
is OUTSIDE the repository (<repo>/../workbuddy-demo). Re-run it to refresh tokens (max 8 h).

``--user-config`` also writes the two EMPLOYEE connectors as WorkBuddy's own connector list
(``~/.workbuddy-ai/mcp.json``: ib-as-jdoe, ib-as-ken), so one WorkBuddy chat can use them without
opening a folder. It never writes the compliance connector there, and it removes an ``ib-compliance``
entry an older run left behind (round 2b finding 2: source text an employee connector returns must
not be able to steer the same agent into the audit tools). The compliance seat uses its own
workspace folder ("IB compliance"), or ``--compliance-user-config`` on a separate machine or user
account: that writes ONLY ib-compliance and removes the employee connectors from that list. The two
flags cannot be combined. Restart WorkBuddy, then press Trust on each in
Experts · Skills · Connectors > Custom connectors. Any other servers already in that file are kept.

Every file holding a token is private from its creation (round 4 Codex B7, B10; round 5 #4, #5):
a new folder with an unpredictable name is made next to the target and restricted to this account
(Windows: icacls turns inheritance off and leaves only this account with full control, then a
second icacls listing must show exactly that; POSIX: 0700). Only then is the token file created
inside it with an exclusive create, so it is born with that one-account ACL (POSIX: 0600). It is
written, fsynced and closed through the same descriptor (never reopened by name), its ACL and file
id are checked, and it is atomically renamed onto the target; the private folder is then removed.
A path through a symlink or junction, or a target that is not a regular file under the expected
folder, is refused. Any refusal stops the script with exit code 1; nothing is written for that file.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_WINDOWS = os.name == "nt"
DATABRIDGE = Path(__file__).resolve().parents[1]
REPO = DATABRIDGE.parent
sys.path.insert(0, str(DATABRIDGE / "scripts"))
from ib_mint_token import mint_for  # noqa: E402  (tokens never pass through stdout)

WORKSPACES = (
    # folder, persona, server module, what to ask in the demo
    ("IB as jdoe", "jdoe", "databridge_ib.mcp_server",
     "What's the status of the database migration project and were there any blockers raised in "
     "Slack last week?"),
    ("IB as ken.ext", "ken.ext", "databridge_ib.mcp_server",
     "What's the status of the database migration project and were there any blockers raised in "
     "Slack last week?"),
    ("IB compliance", "maya.compliance", "databridge_ib.audit_mcp_server",
     "Show me everything jdoe accessed in the payment-gateway space in the last 30 days, then "
     "verify the audit chain."),
)


#: names of the connectors in WorkBuddy's own connector list (--user-config)
USER_NAMES = {"jdoe": "ib-as-jdoe", "ken.ext": "ib-as-ken", "maya.compliance": "ib-compliance"}
#: the compliance connector is never written next to the employee connectors
COMPLIANCE_NAMES = frozenset({"ib-compliance"})
EMPLOYEE_NAMES = frozenset(USER_NAMES.values()) - COMPLIANCE_NAMES


def merge_user_servers(current: dict, user_servers: dict, *, compliance: bool) -> dict:
    """Return the new ``mcpServers`` map for WorkBuddy's own connector list.

    Employee mode (``compliance=False``) adds only the employee connectors and drops any compliance
    connector; compliance mode adds only the compliance connector and drops the employee ones. So one
    WorkBuddy connector list never holds both an employee identity and the audit identity. Servers
    that are not ours are kept untouched.
    """
    keep, drop = (COMPLIANCE_NAMES, EMPLOYEE_NAMES) if compliance else (EMPLOYEE_NAMES, COMPLIANCE_NAMES)
    servers = {k: v for k, v in (current.get("mcpServers") or {}).items() if k not in drop}
    servers.update({k: v for k, v in user_servers.items() if k in keep})
    return servers


def _python() -> Path:
    for p in (DATABRIDGE / ".venv" / "Scripts" / "python.exe", DATABRIDGE / ".venv" / "bin" / "python"):
        if p.exists():
            return p
    return Path(sys.executable)


class Refused(Exception):
    """A token file could not be written privately (round 4 Codex B7, B10). Nothing is published
    for it; ``main`` stops and exits non-zero."""


def _account() -> tuple[str, str]:
    """``(DOMAIN\\user, user)`` of the account running this script (from the environment)."""
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return (f"{domain}\\{user}" if domain and user else user), user


def _decodings(raw: bytes) -> list[str]:
    out = []
    for enc in ("utf-8", "oem", "mbcs"):
        try:
            out.append(raw.decode(enc))
        except (LookupError, UnicodeDecodeError):
            pass
    return out


def _only_owner(listing: bytes, path: Path, who: str | None = None, user: str | None = None) -> bool:
    """Whether an ``icacls <path>`` listing shows exactly ONE entry, explicit (no ``(I)``), full
    control, for this account. Anything else, or a listing that cannot be parsed, is False."""
    if who is None or user is None:
        who, user = _account()
    p = str(path)
    for text in _decodings(listing):
        lines = text.splitlines()
        if not lines or not lines[0].casefold().startswith(p.casefold()):
            continue
        aces = [lines[0][len(p):].strip()]
        for ln in lines[1:]:
            if not ln.strip():
                break
            aces.append(ln.strip())
        if len(aces) != 1 or ":(" not in aces[0]:
            return False
        principal, perms = aces[0].split(":(", 1)
        if "(I)" in f"({perms}" or "(F)" not in f"({perms}":
            return False
        return bool(user) and principal.strip().casefold() in {who.casefold(), user.casefold()}
    return False


def _icacls(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["icacls", *args], capture_output=True, check=False)
    except OSError as exc:
        raise Refused(f"icacls could not run ({type(exc).__name__})") from None


def _verify_only_owner(path: Path, who: str, user: str) -> None:
    """A fresh ``icacls <path>`` listing must show one explicit full-control entry for this account
    and nothing else, or ``Refused``."""
    v = _icacls(str(path))
    if v.returncode != 0 or not _only_owner(v.stdout, path, who, user):
        raise Refused(f"the ACL on {path} is not limited to {who}; not writing a token there")


#: explicit entries Python 3.13's ``mkdtemp`` (``os.mkdir`` mode 0o700) gives a new folder on
#: Windows: SYSTEM, Administrators, OWNER RIGHTS. They are removed so only this account remains.
_MKDIR_SIDS = ("*S-1-5-18", "*S-1-5-32-544", "*S-1-3-4")


def _private_dir(parent: Path) -> Path:
    """A NEW folder with an unpredictable name inside ``parent`` that only this account can open
    (round 5 Codex #4, #5). Windows: icacls turns inheritance off and leaves one entry, this
    account with full control inherited by everything created inside, then a second icacls
    listing must show exactly that. POSIX: mode 0700 and owned by this user. The folder must also
    be empty and not a link. Any failure removes it and raises ``Refused``."""
    try:
        d = Path(tempfile.mkdtemp(dir=parent, prefix=".wb-private.", suffix=".tmp"))
    except OSError as exc:
        raise Refused(f"could not create a private folder in {parent} ({type(exc).__name__})") from None
    try:
        if _WINDOWS:
            who, user = _account()
            if not who:
                raise Refused("cannot tell which account runs this script (USERNAME is not set)")
            r = _icacls(str(d), "/inheritance:r", "/grant:r", f"{who}:(OI)(CI)F", "/remove:g", *_MKDIR_SIDS)
            if r.returncode != 0:
                raise Refused(f"icacls could not restrict {d} to {who} (exit {r.returncode})")
            _verify_only_owner(d, who, user)
        else:
            os.chmod(d, 0o700)
            st = os.lstat(d)
            if stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.geteuid():
                raise Refused(f"could not make {d} private (mode 0700, owned by this user)")
        if _is_link(d) or any(os.scandir(d)):
            raise Refused(f"{d} is not a new, empty folder")
    except BaseException:
        _remove_private(d)
        raise
    return d


def _remove_private(d: Path) -> None:
    """Best-effort removal of a private folder made by ``_private_dir`` (never follows a link)."""
    try:
        for e in os.scandir(d):
            try:
                os.unlink(e.path)
            except OSError:
                pass
        os.rmdir(d)
    except OSError:
        pass


def _owner_only(path: Path) -> None:
    """Check a token file created inside a ``_private_dir`` folder. Windows: icacls turns the one
    inherited entry (this account, full control) into an explicit one and switches inheritance
    off, so the file stays private after it is moved out of the folder; then a fresh listing must
    show that one entry and nothing else. POSIX: mode 0600, checked. Any failure raises
    ``Refused`` (never a warning). The file is never opened here."""
    if not _WINDOWS:
        os.chmod(path, 0o600)
        if stat.S_IMODE(os.lstat(path).st_mode) & 0o077:
            raise Refused(f"could not make {path} private (mode 0600)")
        return
    who, user = _account()
    if not who:
        raise Refused("cannot tell which account runs this script (USERNAME is not set)")
    r = _icacls(str(path), "/inheritance:d")
    if r.returncode != 0:
        raise Refused(f"icacls could not protect {path} (exit {r.returncode})")
    _verify_only_owner(path, who, user)


def _is_link(p: Path) -> bool:
    """A symlink, a junction or any other reparse point (Windows)."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(st, "st_file_attributes", 0) & reparse)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _safe_target(path: Path, root: Path) -> Path:
    """``path`` made absolute, refused (``Refused``) unless it lies under ``root``, no component from
    ``root`` down to it is a symlink or junction, its folder resolves to where it says, and, if it
    exists, it is a regular file (round 4 Codex B10)."""
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise Refused(f"{path} is not inside {root}") from None
    if not rel.parts:
        raise Refused(f"{path} is a folder, not a file")
    cur = root
    if _is_link(cur):
        raise Refused(f"{cur} is a link or junction")
    for part in rel.parts:
        cur = cur / part
        if _is_link(cur):
            raise Refused(f"{cur} is a link or junction; not writing through it")
    if not _same(path.parent.resolve(), root.resolve() / rel.parent):
        raise Refused(f"{path.parent} resolves somewhere else")
    if os.path.lexists(path) and not stat.S_ISREG(os.lstat(path).st_mode):
        raise Refused(f"{path} exists and is not a regular file")
    return path


def _write_private(path: Path, text: str, *, root: Path) -> None:
    """Write a token file privately from the first byte (round 4 Codex B7, B10; round 5 #4, #5).

    1. Check the target (``_safe_target``: under ``root``, no link or junction, a regular file).
    2. Make a NEW private folder next to it (``_private_dir``: restricted and VERIFIED before
       anything is created inside it).
    3. Create the file inside that folder with an exclusive create, so it is born with the
       folder's one-account ACL; write, fsync and close through that SAME descriptor (the file is
       never opened again by name).
    4. Verify the file's ACL (``_owner_only``) and that the name still points at the file that was
       written (same file id), check the target again, and atomically ``os.replace`` it in.
    Any failure raises ``Refused`` (or the original error) and nothing is published; the private
    folder and everything in it are removed either way."""
    path = _safe_target(path, root)
    data = text.encode("utf-8")
    priv = _private_dir(path.parent)
    tmp = priv / path.name
    fd = None
    try:
        flags = (os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        try:
            fd = os.open(tmp, flags, 0o600)
        except OSError as exc:
            raise Refused(f"could not create {tmp} ({type(exc).__name__})") from None
        born = os.fstat(fd)
        if not stat.S_ISREG(born.st_mode):
            raise Refused(f"{tmp} is not a regular file")
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        os.close(fd)
        fd = None
        _owner_only(tmp)
        now = os.lstat(tmp)
        if not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (born.st_dev, born.st_ino):
            raise Refused(f"{tmp} was replaced after it was written; not publishing it")
        _safe_target(path, root)
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        _remove_private(priv)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO.parent / "workbuddy-demo")
    ap.add_argument("--hours", type=int, default=8)
    ap.add_argument("--ib-url", default="http://127.0.0.1:8100")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--user-config", action="store_true",
                      help="also write the two employee connectors (ib-as-jdoe, ib-as-ken) to "
                           "~/.workbuddy-ai/mcp.json; never the compliance connector")
    mode.add_argument("--compliance-user-config", action="store_true",
                      help="compliance seat only (a separate machine or account): write ONLY "
                           "ib-compliance to ~/.workbuddy-ai/mcp.json and remove the employee connectors")
    args = ap.parse_args(argv)
    out = args.out.resolve()
    if out == REPO or REPO in out.parents:          # tokens are secrets: never inside the repo
        ap.error(f"--out must be outside the repository ({REPO})")
    try:
        return _setup(ap, args, out)
    except Refused as exc:
        print(f"REFUSED: {exc}. Nothing more was written; fix it and re-run.", file=sys.stderr)
        return 1


def _setup(ap: argparse.ArgumentParser, args: argparse.Namespace, out: Path) -> int:
    py = _python()
    user_servers = {}
    for folder, persona, module, question in WORKSPACES:
        token = mint_for(persona, args.hours)
        ws = out / folder
        (ws / ".workbuddy").mkdir(parents=True, exist_ok=True)
        name = "internal-brain-audit" if module.endswith("audit_mcp_server") else "internal-brain"
        cfg = {"mcpServers": {name: {
            "command": py.as_posix(),
            "args": ["-m", module],
            "cwd": DATABRIDGE.as_posix(),
            "env": {"PYTHONPATH": (DATABRIDGE / "apps" / "internal_brain").as_posix(),
                    "IB_URL": args.ib_url, "IB_TOKEN": token},
        }}}
        user_servers[USER_NAMES[persona]] = cfg["mcpServers"][name]
        cfg_path = ws / ".workbuddy" / "mcp.json"
        _write_private(cfg_path, json.dumps(cfg, indent=2), root=out)   # under the resolved --out
        _safe_target(ws / "DEMO_QUESTION.txt", out).write_text(
            f"This WorkBuddy workspace talks to the Walnut Internal Brain as '{persona}'.\n"
            f"Ask:\n{question}\n", encoding="utf-8")
        print(f"wrote {ws / '.workbuddy' / 'mcp.json'}  (persona {persona}, {module})")
    if args.user_config or args.compliance_user_config:
        compliance = bool(args.compliance_user_config)
        user_cfg = Path.home() / ".workbuddy-ai" / "mcp.json"
        user_cfg.parent.mkdir(parents=True, exist_ok=True)
        user_cfg = _safe_target(user_cfg, user_cfg.parent)          # no link or junction (B10)
        current = json.loads(user_cfg.read_text(encoding="utf-8")) if user_cfg.exists() else {}
        if not isinstance(current, dict) or not isinstance(current.get("mcpServers", {}), dict):
            ap.error(f"{user_cfg} is not a WorkBuddy MCP config; fix or move it first")
        current["mcpServers"] = merge_user_servers(current, user_servers, compliance=compliance)
        _write_private(user_cfg, json.dumps(current, indent=2), root=user_cfg.parent)
        written = sorted(COMPLIANCE_NAMES if compliance else EMPLOYEE_NAMES)
        print(f"wrote {user_cfg}  ({', '.join(written)}); restart WorkBuddy and press Trust on each")
        if compliance:
            print("note: this connector list now holds only the compliance connector; keep it on the "
                  "compliance seat, never on an employee's WorkBuddy.")
        else:
            print("note: the compliance connector is NOT in this list (use the 'IB compliance' workspace "
                  "folder, or --compliance-user-config on the compliance seat). These connectors are "
                  "visible in every WorkBuddy chat for up to 8 h; each only ever answers as its own "
                  "person. Remove them after the demo.")
    print(f"tokens expire in {min(args.hours, 8)} h; re-run this script to refresh them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
