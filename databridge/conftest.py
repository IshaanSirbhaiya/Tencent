"""Repo-wide pytest configuration.

**No test may reach a live model.** `CLAUDE.md` states it as a rule and the reason is practical:
a suite whose answers depend on a hosted model is non-deterministic, costs money per run, and
fails differently on the machine of whoever is holding the API key that day.

Until now that held by accident. The gateway extracts through whatever `detect_llm()` finds in the
environment, so the suite used the deterministic gold backend only because the shell running it
happened to have no `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` exported. Anyone with a key in their
profile — which is the normal state for someone developing against this repo — would have run the
whole suite against a live API without being told.

`DATABRIDGE_FORCE_GOLD` makes it explicit and unconditional, set before any test imports the
gateway. The live path is exercised by running the server, which is what `STATUS.md` already says
a green suite does not prove.
"""
from __future__ import annotations

import os


def pytest_configure(config):                                        # noqa: ARG001
    os.environ["DATABRIDGE_FORCE_GOLD"] = "1"
    # The gateway and the MCP servers open ONE shared, FILE-backed graph store
    # (`WALNUT_GRAPH_DB_PATH`, default `var/graph/workspace.db`). A test must never write into the
    # developer's real store, so the suite runs on a private in-memory store per app — the same
    # isolation it had when the store was always in-memory. A test that needs a file sets its own
    # (tmp_path) path with monkeypatch.
    os.environ["WALNUT_GRAPH_DB_PATH"] = ":memory:"
    # The clinic demo seed is OPT-IN in production (`WALNUT_LOAD_CLINIC_SEED`, default off). The
    # existing gateway/MCP suites are written against it, so the suite turns it on; a test of the
    # empty-store behaviour turns it off with monkeypatch.
    os.environ["WALNUT_LOAD_CLINIC_SEED"] = "1"
    # The automation TriggerEngine must never fire real schedules or spawn MCP servers because some
    # unrelated test booted the gateway. Explicit OFF; a test of the engine turns it on itself.
    os.environ["WALNUT_AUTOMATION_ENGINE"] = "0"
