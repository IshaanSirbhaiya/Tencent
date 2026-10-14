"""Mint a short-lived Internal Brain token for a persona (plan §4.9, P8).

    python databridge/scripts/ib_mint_token.py <persona> [--hours N] (--print | --out FILE)

Loads IB_TOKEN_KEY from databridge/.env.ib.local (parsed in code; the key is never
printed), looks the persona up in databridge/fixtures/enterprise/world.json, and
mints a token with databridge_ib.auth.mint(key, sub, roles, ttl_s=...).

The token is NEVER printed by default. Use --print to write it to stdout (for piping),
or --out FILE to write it to a file with owner-only permissions. With neither flag,
exits non-zero with a usage message.

Unknown or inactive personas exit non-zero. --hours is capped at 8 (default 8).
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]                      # databridge/
for rel in ("apps/internal_brain",):
    if str(ROOT / rel) not in sys.path:
        sys.path.insert(0, str(ROOT / rel))

from databridge_ib.auth import mint  # noqa: E402

MAX_HOURS = 8
DEFAULT_HOURS = 8
ENV_FILE = ROOT / ".env.ib.local"
WORLD_FILE = ROOT / "fixtures" / "enterprise" / "world.json"


def _load_env_value(env_path: Path, key: str) -> str:
    """Parse a .env file in code and return the value for *key* (never prints the file)."""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            return v
    raise SystemExit(f"error: {key} not found in {env_path.name}")


def _load_persona(world_path: Path, persona: str) -> dict:
    """Return the directory user dict for *persona* from world.json, or raise SystemExit."""
    world = json.loads(world_path.read_text(encoding="utf-8"))
    users = world.get("directory", {}).get("users", [])
    for u in users:
        if u.get("id") == persona:
            if not u.get("active", True):
                raise SystemExit(f"error: persona '{persona}' is inactive")
            return u
    raise SystemExit(f"error: unknown persona '{persona}'")


def mint_for(persona: str, hours: int = DEFAULT_HOURS, *,
             env_path: Path | None = None, world_path: Path | None = None) -> str:
    """Mint and return a token for *persona*.

    Other scripts can import this function instead of invoking the CLI.
    """
    if env_path is None:
        env_path = ENV_FILE
    if world_path is None:
        world_path = WORLD_FILE
    hours = max(1, min(hours, MAX_HOURS))
    ttl_s = hours * 3600
    if env_path.exists():
        key = _load_env_value(env_path, "IB_TOKEN_KEY")
    else:                       # no secrets file: match the local Brain, which then runs on its dev key
        import os
        from databridge_ib.config import DEV_TOKEN_KEY
        key = os.environ.get("IB_TOKEN_KEY") or DEV_TOKEN_KEY
        print(f"note: {env_path.name} not found; using IB_TOKEN_KEY from the environment or the "
              "local development key", file=sys.stderr)
    user = _load_persona(world_path, persona)
    sub = user["id"]
    roles = list(user.get("roles", ["employee"]))
    return mint(key, sub, roles, ttl_s=ttl_s)


def _write_token_file(path: Path, token: str) -> None:
    """Write *token* to *path* with owner-only permissions where the OS supports chmod."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # If the file already exists, remove it first so we control permissions on create.
    if path.exists():
        path.unlink()
    path.write_text(token, encoding="utf-8")
    # Set owner-only permissions (0o600) on POSIX.
    if os.name == "posix":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    # On Windows, the file is created with default ACLs; we rely on the user's profile dir.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mint a short-lived Internal Brain token for a persona.")
    parser.add_argument("persona", help="Persona id from world.json (e.g. jdoe)")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                        help=f"Token lifetime in hours (default {DEFAULT_HOURS}, max {MAX_HOURS})")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--out", metavar="FILE",
                        help="Write the token to FILE (owner-only permissions).")
    output.add_argument("--print", action="store_true",
                        help="Print the token to stdout (for piping).")
    args = parser.parse_args(argv)

    if not args.print and not args.out:
        parser.error("one of --print or --out FILE is required (the token is not printed by "
                     "default).")

    token = mint_for(args.persona, args.hours)

    if args.print:
        print(token)
    elif args.out:
        out_path = Path(args.out)
        _write_token_file(out_path, token)
        print(f"token written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
