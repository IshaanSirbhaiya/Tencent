"""Revocations from several PROCESSES at once are all kept (Codex sec r1 track 4 #8)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from databridge_ib import auth

APP = Path(__file__).resolve().parents[1]


def test_parallel_processes_do_not_lose_revocations(tmp_path):
    path = tmp_path / "revoked.json"
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from databridge_ib import auth; "
            "[auth.revoke(sys.argv[2], f'{sys.argv[3]}-{i}') for i in range(25)]")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(APP), str(path), f"p{n}"])
             for n in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    got = auth.load_revoked(path)
    assert got == {f"p{n}-{i}" for n in range(4) for i in range(25)}
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob("*.lock"))


def test_revoked_token_is_rejected(tmp_path):
    path = tmp_path / "revoked.json"
    tok = auth.mint("k" * 40, "jdoe", ["employee"])
    jti = auth.verify("k" * 40, tok)["jti"]
    auth.revoke(path, jti)
    try:
        auth.verify("k" * 40, tok, revoked=auth.load_revoked(path))
    except auth.TokenError as e:
        assert "revoked" in str(e)
    else:
        raise AssertionError("revoked token accepted")
