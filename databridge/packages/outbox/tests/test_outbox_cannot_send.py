"""The outbox holds no transport and no model — asserted against the source, not against a promise.

An outbox that could send is not a gate, it is a queue with extra steps. The whole value of the
design is that the thing which decides *what to say* and the thing which can *say it* are in
different processes, so this package must be unable to acquire the second capability by accident.

Paired with a planted-violation test, as `packages/agent/tests/test_no_send_capability.py` is:
without one, a typo in a pattern leaves a green suite asserting nothing — the failure mode this
repo's house style calls out by name.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

import databridge_outbox

PKG_ROOT = pathlib.Path(databridge_outbox.__file__).resolve().parent

# Anything that can open a connection, send mail, spawn a process, or speak a third-party API.
TRANSPORT_MODULES = (
    "smtplib", "imaplib", "poplib", "socket", "ssl", "http", "httpx", "requests", "urllib",
    "aiohttp", "websockets", "ftplib", "telnetlib", "slack_sdk", "slack_bolt", "slackclient",
    "googleapiclient", "google_auth_oauthlib", "boto3", "botocore", "paramiko", "smtpd",
    "subprocess", "webbrowser", "xmlrpc",
)

# The vendor rule (P2 / ADR-0002): every model call goes through packages/harness, and the outbox
# does not make model calls at all.
VENDOR_MODULES = ("anthropic", "openai", "litellm", "cohere", "mistralai", "ollama",
                  "databridge_harness", "databridge_agent")


def _import_re(modules: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"^[ \t]*(?:import|from)[ \t]+(" + "|".join(modules) + r")\b", re.M)


def _sources() -> list[pathlib.Path]:
    return [p for p in sorted(PKG_ROOT.rglob("*.py")) if "__pycache__" not in p.parts]


def _violations(modules: tuple[str, ...]) -> list[str]:
    pattern = _import_re(modules)
    hits: list[str] = []
    for path in _sources():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.match(line):
                hits.append(f"{path.relative_to(PKG_ROOT)}:{line_no}: {line.strip()}")
    return hits


def test_there_are_sources_to_scan():
    """Guards every scan below: an empty file list would make them vacuously green.

    Globbed, not listed, so a module added tomorrow joins the scan without anybody remembering to
    add it — but the known modules are asserted present, so a rename cannot empty the set.
    """
    names = {p.name for p in _sources()}
    assert {"drafting.py", "models.py", "monitors.py", "recipients.py",
            "store.py"} <= names, names


def test_no_transport_of_any_kind_is_imported():
    assert not _violations(TRANSPORT_MODULES), (
        "the outbox is the gate, not the sender; the dispatcher is a different process: "
        + "; ".join(_violations(TRANSPORT_MODULES)))


def test_no_model_is_imported():
    assert not _violations(VENDOR_MODULES), (
        "composition here is template interpolation over committed values, which is why every "
        "sentence has a pointer: " + "; ".join(_violations(VENDOR_MODULES)))


@pytest.mark.parametrize("line,modules", [
    ("import smtplib", TRANSPORT_MODULES),
    ("from smtplib import SMTP", TRANSPORT_MODULES),
    ("    import httpx", TRANSPORT_MODULES),
    ("from slack_sdk import WebClient", TRANSPORT_MODULES),
    ("import anthropic", VENDOR_MODULES),
    ("from databridge_harness import Harness", VENDOR_MODULES),
    ("from databridge_agent.agent import ClinicAgent", VENDOR_MODULES),
])
def test_the_scanner_catches_a_planted_import(line, modules):
    """The scans above are only worth their runtime if they fail on what they claim to catch."""
    assert _import_re(modules).match(line), f"scanner missed {line!r}"


def test_the_scanner_ignores_a_mention_in_prose():
    """Over-broadness would push people to stop writing about the rule in the file that keeps it."""
    prose = '"""We never import smtplib here."""\n# import anthropic would be a bug\n'
    assert not _import_re(TRANSPORT_MODULES).search(prose)
    assert not _import_re(VENDOR_MODULES).search(prose)


def test_the_package_declares_no_transport_dependency():
    """The scan reads code; this reads the manifest. A dependency nobody imports today is a
    dependency somebody imports tomorrow."""
    import tomllib

    manifest = tomllib.loads((PKG_ROOT.parent / "pyproject.toml").read_text(encoding="utf-8"))
    declared = " ".join(manifest["project"].get("dependencies", [])).lower()
    for forbidden in ("httpx", "requests", "aiosmtplib", "slack", "google-api", "anthropic",
                      "openai", "databridge-harness", "databridge-agent"):
        assert forbidden not in declared, f"packages/outbox depends on {forbidden}"


def test_importing_the_store_pulls_in_no_model_routing_layer():
    """The strongest version of the claim: not "we do not import it" but "it is not loaded".

    Run in a subprocess because `sys.modules` in this one is polluted by every other test in the
    suite. `databridge_outbox` re-exports its ledger-facing module lazily precisely so this holds
    — see the package docstring — and this is the test that keeps that arrangement honest.
    """
    import os
    import tomllib

    root = PKG_ROOT.parents[2]                       # databridge/
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    # Built from the pytest path rather than hardcoded, so the child sees exactly what the suite
    # sees and this test cannot pass because the child failed to import in a different way.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(root / p) for p in config["tool"]["pytest"]["ini_options"]["pythonpath"])}
    code = (
        "import sys, databridge_outbox\n"
        "from databridge_outbox.store import OutboxStore\n"
        "roots = {m.split('.')[0] for m in sys.modules}\n"
        "bad = roots & {'anthropic','openai','litellm','cohere','mistralai','ollama',"
        "'databridge_harness','databridge_agent','smtplib'}\n"
        "print(sorted(bad))\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=str(root), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing the outbox loaded {result.stdout.strip()} — the dispatcher imports this "
        f"package, and 'the dispatcher has no model in the loop' must be provable from the "
        f"module graph")
