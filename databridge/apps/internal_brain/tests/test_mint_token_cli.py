"""Tests for the ib_mint_token CLI script.

Uses a temporary env file and a dev key — never the real .env.ib.local. Asserts:
  (1) a known persona gives a token that auth.verify accepts with the right sub,
  (2) an unknown persona exits non-zero,
  (3) --hours 24 is capped to 8 (token expiry ~8 h, not 24 h).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# databridge/ is the root that conftest already puts on sys.path for the IB tests,
# but ib_mint_token.py lives under scripts/ and manipulates its own path at import.
# We import it as a module by adding scripts/ to sys.path here.
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ib_mint_token as cli  # noqa: E402
from databridge_ib import auth  # noqa: E402

DEV_KEY = "dev-key-for-tests-only-0123456789abcdef0123456789abcdef"

# A minimal world.json with one active persona and one inactive persona.
_WORLD = {
    "directory": {
        "groups": [],
        "users": [
            {
                "id": "jdoe",
                "name": "Jordan Doe",
                "email": "jdoe@companya.example",
                "employment": "employee",
                "kind": "member",
                "active": True,
                "roles": ["employee"],
                "groups": [],
                "platform_ids": {},
            },
            {
                "id": "sam.old",
                "name": "Sam Old",
                "email": "sam.old@companya.example",
                "employment": "employee",
                "kind": "member",
                "active": False,
                "roles": ["employee"],
                "groups": [],
                "platform_ids": {},
            },
        ],
    }
}


@pytest.fixture
def env_and_world(tmp_path: Path, monkeypatch):
    """Create a temp env file and world.json, and point the CLI at them."""
    env_file = tmp_path / ".env.ib.local"
    env_file.write_text(f"IB_TOKEN_KEY={DEV_KEY}\n", encoding="utf-8")
    world_file = tmp_path / "world.json"
    world_file.write_text(json.dumps(_WORLD), encoding="utf-8")
    monkeypatch.setattr(cli, "ENV_FILE", env_file)
    monkeypatch.setattr(cli, "WORLD_FILE", world_file)
    return env_file, world_file


def _run_cli(argv, capsys):
    """Call cli.main() and return (rc, stdout, stderr)."""
    try:
        rc = cli.main(argv)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_known_persona_mints_verifiable_token(env_and_world, capsys):
    rc, out, err = _run_cli(["jdoe"], capsys)
    assert rc == 0, f"expected rc=0, got {rc}; stderr={err}"
    token = out.strip()
    assert token, "expected a token on stdout"
    claims = auth.verify(DEV_KEY, token)
    assert claims["sub"] == "jdoe"
    assert claims["roles"] == ["employee"]


def test_unknown_persona_exits_nonzero(env_and_world, capsys):
    rc, out, err = _run_cli(["nobody.nope"], capsys)
    assert rc != 0
    assert not out.strip()  # no token printed for an error


def test_inactive_persona_exits_nonzero(env_and_world, capsys):
    rc, out, err = _run_cli(["sam.old"], capsys)
    assert rc != 0
    assert not out.strip()


def test_hours_capped_to_eight(env_and_world, capsys):
    before = time.time()
    rc, out, err = _run_cli(["jdoe", "--hours", "24"], capsys)
    assert rc == 0, f"expected rc=0, got {rc}; stderr={err}"
    token = out.strip()
    claims = auth.verify(DEV_KEY, token)
    # Capped to 8 h => expiry is ~8 h from now, NOT ~24 h.
    exp = claims["exp"]
    eight_hours = 8 * 3600
    twenty_four_hours = 24 * 3600
    delta = exp - before
    assert delta <= eight_hours + 120, f"token lifetime exceeds 8 h cap: {delta}s"
    assert delta > 7 * 3600, f"token lifetime too short (expected ~8 h): {delta}s"
    assert delta < twenty_four_hours, f"token was NOT capped to 8 h: {delta}s"
