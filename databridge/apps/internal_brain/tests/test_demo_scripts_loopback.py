"""Round 2b finding 6: check_absent.py and demo_preflight.py send the demo sign-in (and
IB_DEMO_PASSCODE) only to a loopback origin unless --allow-remote, and never over plain http to a
remote host. Both scripts are imported by path; main() must refuse before any request is made."""
from __future__ import annotations

import importlib.util
import sys
import urllib.request
from pathlib import Path

import pytest

_DATABRIDGE = Path(__file__).resolve().parents[3]
_SCRIPTS = {
    "check_absent": _DATABRIDGE.parent / "docs" / "finals" / "live_kit" / "check_absent.py",
    "demo_preflight": _DATABRIDGE / "scripts" / "demo_preflight.py",
}


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", _SCRIPTS[name])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", sorted(_SCRIPTS))
def test_origin_rule(name):
    mod = _load(name)
    for ok in ("http://127.0.0.1:8100", "http://localhost:3100", "http://[::1]:3100"):
        assert mod.origin_error(ok, False) is None, ok
    assert mod.origin_error("http://192.168.1.20:8100", False)          # remote, no flag
    assert mod.origin_error("https://stage.example:8100", False)        # remote, no flag
    assert mod.origin_error("http://192.168.1.20:8100", True)           # remote over plain http
    assert mod.origin_error("https://stage.example:8100", True) is None  # explicit, https
    assert mod.origin_error("http://u:p@127.0.0.1:8100", False)         # credentials
    assert mod.origin_error("ftp://127.0.0.1", False)


@pytest.mark.parametrize("name,flag", [("check_absent", "--base"), ("demo_preflight", "--web")])
def test_main_refuses_remote_before_any_request(name, flag, monkeypatch, capsys):
    mod = _load(name)
    calls = []
    monkeypatch.setattr(urllib.request.OpenerDirector, "open",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("request made")))
    monkeypatch.setenv("IB_DEMO_PASSCODE", "secret-passcode")
    argv = [name, flag, "http://10.0.0.5:8100"]
    if name == "check_absent":
        argv.append("--all")
    monkeypatch.setattr(sys, "argv", argv)
    assert mod.main() == 2
    assert not calls
    err = capsys.readouterr().err
    assert "--allow-remote" in err and "secret-passcode" not in err
