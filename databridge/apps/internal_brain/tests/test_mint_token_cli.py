"""Tests for the ib_mint_token CLI script.

Uses a temporary env file and a dev key — never the real .env.ib.local. Asserts:
  (1) --print gives a token that auth.verify accepts with the right sub,
  (2) --out FILE writes the token to the file (not stdout),
  (3) with neither --print nor --out, exits non-zero with a usage message,
  (4) an unknown persona exits non-zero,
  (5) --hours 24 is capped to 8 (token expiry ~8 h, not 24 h),
  (6) mint_for() returns a verifiable token.
"""
from __future__ import annotations

import json
import os
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


def test_print_mints_verifiable_token(env_and_world, capsys):
    rc, out, err = _run_cli(["jdoe", "--print"], capsys)
    assert rc == 0, f"expected rc=0, got {rc}; stderr={err}"
    token = out.strip()
    assert token, "expected a token on stdout"
    claims = auth.verify(DEV_KEY, token)
    assert claims["sub"] == "jdoe"
    assert claims["roles"] == ["employee"]


def test_out_writes_token_to_file(env_and_world, tmp_path, capsys):
    out_file = tmp_path / "token.txt"
    rc, out, err = _run_cli(["jdoe", "--out", str(out_file)], capsys)
    assert rc == 0, f"expected rc=0, got {rc}; stderr={err}"
    # stdout should NOT contain the token, only the confirmation message
    assert "token written to" in out
    assert out.strip() != cli.mint_for.__code__  # sanity
    # The token must not have been printed to stdout
    lines = out.strip().split("\n")
    for line in lines:
        assert "." in line or "token" in line.lower()  # the message, not a token
    # The file should contain the verifiable token
    token = out_file.read_text(encoding="utf-8").strip()
    assert token, "expected a token in the file"
    claims = auth.verify(DEV_KEY, token)
    assert claims["sub"] == "jdoe"


def test_no_flag_exits_nonzero(env_and_world, capsys):
    rc, out, err = _run_cli(["jdoe"], capsys)
    assert rc != 0, f"expected non-zero exit, got {rc}"
    # Should have a usage message mentioning --print or --out
    combined = out + err
    assert "--print" in combined or "--out" in combined, (
        f"expected usage message mentioning --print/--out, got: {combined}")
    # No token should be printed
    assert out.strip() == "" or "token written" not in out


def test_unknown_persona_exits_nonzero(env_and_world, capsys):
    rc, out, err = _run_cli(["nobody.nope", "--print"], capsys)
    assert rc != 0
    assert not out.strip()  # no token printed for an error


def test_inactive_persona_exits_nonzero(env_and_world, capsys):
    rc, out, err = _run_cli(["sam.old", "--print"], capsys)
    assert rc != 0
    assert not out.strip()


def test_hours_capped_to_eight(env_and_world, capsys):
    before = time.time()
    rc, out, err = _run_cli(["jdoe", "--hours", "24", "--print"], capsys)
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


def test_print_and_out_mutually_exclusive(env_and_world, tmp_path, capsys):
    out_file = tmp_path / "token.txt"
    rc, out, err = _run_cli(["jdoe", "--print", "--out", str(out_file)], capsys)
    assert rc != 0, "expected non-zero exit for mutually exclusive flags"


def test_mint_for_returns_verifiable_token(env_and_world):
    env_file, world_file = env_and_world
    token = cli.mint_for("jdoe", hours=4, env_path=env_file, world_path=world_file)
    claims = auth.verify(DEV_KEY, token)
    assert claims["sub"] == "jdoe"
    assert claims["roles"] == ["employee"]


def test_mint_for_unknown_persona_exits_nonzero(env_and_world):
    env_file, world_file = env_and_world
    with pytest.raises(SystemExit):
        cli.mint_for("nobody.nope", env_path=env_file, world_path=world_file)
