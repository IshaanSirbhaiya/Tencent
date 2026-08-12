"""The launcher's sys.path must cover everything the gateway imports.

This exists because the suite structurally cannot catch the failure it guards. pytest builds its
own path from the root `pyproject.toml`, so every test passed while `serve.py` — the thing that
actually starts the server — was missing `packages/intake`. The first sign was the browser
walkthrough failing to boot with `No module named 'databridge_intake'`.

A green suite and a server that will not start is the worst combination available, so the drift is
now asserted rather than remembered.
"""
from __future__ import annotations

import pathlib
import re
import tomllib


def _root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3]          # databridge/


def test_serve_path_matches_the_pytest_pythonpath():
    serve = (_root() / "apps" / "gateway" / "serve.py").read_text(encoding="utf-8")
    block = re.search(r"_PACKAGES = \((.*?)\)", serve, re.S)
    assert block, "serve.py no longer declares _PACKAGES — update this test with it"
    on_serve_path = set(re.findall(r'"([^"]+)"', block.group(1)))

    config = tomllib.loads((_root() / "pyproject.toml").read_text(encoding="utf-8"))
    on_pytest_path = set(config["tool"]["pytest"]["ini_options"]["pythonpath"])

    # `scripts` and `apps/cli` are test-only; the server does not import them.
    missing = on_pytest_path - on_serve_path - {"scripts", "apps/cli"}
    assert not missing, (
        f"serve.py is missing {sorted(missing)} — the suite will pass and the server will not "
        f"boot")


def test_every_declared_path_exists():
    serve = (_root() / "apps" / "gateway" / "serve.py").read_text(encoding="utf-8")
    block = re.search(r"_PACKAGES = \((.*?)\)", serve, re.S)
    for entry in re.findall(r'"([^"]+)"', block.group(1)):
        assert (_root() / entry).exists(), entry
