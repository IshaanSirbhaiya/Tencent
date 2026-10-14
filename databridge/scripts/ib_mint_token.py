"""Mint a short-lived Internal Brain token for a persona (plan §4.9, P8).

    python databridge/scripts/ib_mint_token.py <persona> [--hours N]

Loads IB_TOKEN_KEY from databridge/.env.ib.local (parsed in code; the key is never
printed), looks the persona up in databridge/fixtures/enterprise/world.json, and
prints ONLY the token minted with databridge_ib.auth.mint(key, sub, roles, ttl_s=...).

Unknown or inactive personas exit non-zero. --hours is capped at 8 (default 8).
"""
from __future__ import annotations

import argparse
import json
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mint a short-lived Internal Brain token for a persona.")
    parser.add_argument("persona", help="Persona id from world.json (e.g. jdoe)")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS,
                        help=f"Token lifetime in hours (default {DEFAULT_HOURS}, max {MAX_HOURS})")
    args = parser.parse_args(argv)

    hours = max(1, min(args.hours, MAX_HOURS))
    ttl_s = hours * 3600

    key = _load_env_value(ENV_FILE, "IB_TOKEN_KEY")
    user = _load_persona(WORLD_FILE, args.persona)
    sub = user["id"]
    roles = list(user.get("roles", ["employee"]))

    token = mint(key, sub, roles, ttl_s=ttl_s)
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
