"""The dispatcher has no model in it — proved by scanning what is there, not by reading a docstring.

`GRAPH_AGENT_AND_MCP.md` §3 rule 1: *"A separate, dumb dispatcher — no model in the loop — sends
approved rows. An injected prompt can therefore cause a draft, which a human reads, and never a
send."* That sentence is only true if the dispatcher genuinely cannot reason. This file is what
makes it falsifiable.

Two layers, and the second is the one that catches what the first cannot:

1. **A source scan over every module in this directory.** Globbed rather than listed, so it covers
   a module nobody has written yet — the property `test_capability_boundary.py` calls out as the
   difference between a test that can fail and one that cannot. Paired with planted violations,
   because a scan whose patterns are wrong is a green suite asserting nothing.
2. **A `sys.modules` check after a real import, in a subprocess.** A transitive dependency is still
   a model in the process. This is why `databridge_outbox` re-exports its ledger-facing module
   lazily: without that, importing the outbox would drag `databridge_harness` in and this test
   would be red — correctly.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tomllib

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]      # apps/dispatcher
REPO_ROOT = APP_ROOT.parents[1]                              # databridge/

# A model, by any road: a vendor SDK, the harness that routes to one, the agent that calls it, or
# the eval suites that stand one up.
MODEL_MODULES = (
    "anthropic", "openai", "litellm", "cohere", "mistralai", "ollama", "google",
    "transformers", "llama_cpp", "sentence_transformers",
    "databridge_harness", "databridge_agent", "databridge_evals",
)

# Reasoning-shaped calls, for the case where somebody reaches a model without importing it by name
# — through a helper, a factory, or a string. Deliberately short and specific: if you land here,
# the dispatcher is doing something it must not, and the fix is to move that code, never to
# shorten this list.
REASONING_SHAPED = (
    ("harness_call", re.compile(r"\bcomplete_structured\s*\(|\bcomplete\s*\(")),
    ("chat_completion", re.compile(r"\bchat\.completions\b|\bmessages\.create\s*\(")),
    ("prompting", re.compile(r"\b(?:system_prompt|user_prompt|few_shot|temperature)\s*=")),
    ("agent_loop", re.compile(r"\bClinicAgent\b|\bToolRegistry\b|\brun_agent\s*\(")),
)


def _import_re(modules: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"^[ \t]*(?:import|from)[ \t]+(" + "|".join(modules) + r")\b", re.M)


def _sources() -> list[pathlib.Path]:
    """Every module in this app EXCEPT the tests. Globbed: a new module joins the scan by existing."""
    return [p for p in sorted(APP_ROOT.rglob("*.py"))
            if "__pycache__" not in p.parts and "tests" not in p.parts]


def test_there_are_sources_to_scan():
    """Guards every scan below. Known modules asserted present so a rename cannot empty the set,
    while the glob is what makes a NEW module covered without anybody remembering."""
    names = {p.name for p in _sources()}
    assert {"dispatcher.py", "smtp_transport.py"} <= names, names


def test_no_model_is_imported():
    hits = []
    pattern = _import_re(MODEL_MODULES)
    for path in _sources():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.match(line):
                hits.append(f"{path.relative_to(APP_ROOT)}:{line_no}: {line.strip()}")
    assert not hits, (
        "the dispatcher has no model in the loop — that is the whole reason an injected prompt "
        "can cause a draft and never a send: " + "; ".join(hits))


def test_nothing_here_reasons():
    hits = []
    for path in _sources():
        source = path.read_text(encoding="utf-8")
        for rule, pattern in REASONING_SHAPED:
            found = pattern.search(source)
            if found:
                hits.append(f"{path.relative_to(APP_ROOT)}: {rule}: {found.group(0)!r}")
    assert not hits, "; ".join(hits)


@pytest.mark.parametrize("line", [
    "import anthropic",
    "from openai import OpenAI",
    "from databridge_harness import Harness",
    "    import databridge_agent",
    "from databridge_evals.e_intake import gold_harness",
])
def test_the_import_scanner_catches_a_planted_import(line):
    """The scan is only worth its runtime if it fires on what it claims to catch."""
    assert _import_re(MODEL_MODULES).match(line), f"scanner missed {line!r}"


@pytest.mark.parametrize("line", [
    "        plan = harness.complete_structured(prompt, schema)",
    "    reply = client.messages.create(model=m)",
    "    answer = agent.chat.completions.create()",
    "    result = ClinicAgent(registry).answer(question)",
    "    out = call(temperature=0.2)",
])
def test_the_reasoning_scanner_catches_a_planted_call(line):
    assert any(p.search(line) for _, p in REASONING_SHAPED), f"scanner missed {line!r}"


def test_the_scanners_ignore_a_mention_in_prose():
    """Over-broadness would push people to stop writing about the rule in the file that keeps it."""
    prose = ('"""No model in the loop: we never import anthropic or call a harness."""\n'
             "# databridge_agent must never appear here\n")
    assert not _import_re(MODEL_MODULES).search(prose)


def test_the_dispatchers_own_docstring_states_the_rule():
    """The runbook's lesson: a description is what somebody reads before they change the file."""
    import dispatcher

    text = (dispatcher.__doc__ or "").lower()
    assert "no model" in text and "approved" in text


def test_importing_the_dispatcher_loads_no_model(tmp_path):
    """The layer the source scan cannot reach: a TRANSITIVE model dependency.

    Run in a subprocess because `sys.modules` in this one is polluted by every other test.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(REPO_ROOT / p) for p in config["tool"]["pytest"]["ini_options"]["pythonpath"])}
    code = (
        "import sys, dispatcher\n"
        "roots = {m.split('.')[0] for m in sys.modules}\n"
        "print(sorted(roots & {'anthropic','openai','litellm','cohere','mistralai','ollama',"
        "'databridge_harness','databridge_agent','databridge_evals'}))\n")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=str(REPO_ROOT), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing the dispatcher loaded {result.stdout.strip()}")


def test_importing_the_dispatcher_does_not_even_open_smtplib(tmp_path):
    """The transport is a dependency of RUNNING the dispatcher, not of reasoning about it.

    Not a safety property — it is the send path, it is allowed smtplib — but it keeps
    `build_dispatcher`'s lazy import honest, and a module that has not imported a transport cannot
    have one bound at import time by mistake.
    """
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        str(REPO_ROOT / p) for p in config["tool"]["pytest"]["ini_options"]["pythonpath"])}
    result = subprocess.run(
        [sys.executable, "-c", "import sys, dispatcher; print('smtplib' in sys.modules)"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
