"""C-5 (finance dry run, 2026-10-02): engine code does not import the healthcare pack's package.

``databridge_intake`` is the healthcare vertical's product package. Pack discovery, the deployment
clock and the identity-refusal guardrail used to live there, so the agent, ``/brain/query`` and the
MCP surfaces imported a vertical to find out which pack they were running under — and spoke that
vertical's words under every pack. Those helpers now live in ``databridge_core`` (``pack_binding``,
``clock``, ``identity_guardrail``).

This test keeps it that way. The set of engine modules is DERIVED from the filesystem — every
non-test ``.py`` under ``packages/*`` and ``apps/*`` except the vertical's own package — so a new
module is scanned the day it lands. Imports are read from the AST (``import``, ``from … import``,
``importlib.import_module("…")`` and ``__import__("…")``), so a lazy import inside a function is
caught too.

The exceptions are listed explicitly, each with its reason, in two groups:

* ``HEALTHCARE_ROUTES`` — modules that ARE the healthcare pack's surface and legitimately drive its
  pipeline, ledger or registry;
* ``KNOWN_DEBT`` — engine modules still reaching into the vertical for something generic, owned by
  another track. Recorded as CROSS-TRACK in ``wiki/40-reviews/R-026-PACK6.md``. This group may only
  shrink.

The allowlist must match the scan EXACTLY: a new importer fails, and so does a stale entry (a module
that stopped importing the vertical must leave the list, or the list would rot into a loophole).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]                      # databridge/
VERTICAL = "databridge_intake"
VERTICAL_DIR = Path("packages") / "intake"

HEALTHCARE_ROUTES: dict[str, str] = {
    "packages/graph/databridge_graph/clinic_seed.py":
        "builds the healthcare demo graph from the intake fixtures (quarantined in "
        "test_no_domain_branching)",
    "packages/graph/databridge_graph/clinic_history.py":
        "the graph's implementation of the intake pipeline's ClinicHistory port",
    "packages/outbox/databridge_outbox/recipients.py":
        "resolves message recipients from the intake ledger and patient registry",
    "apps/gateway/databridge_gateway/intake_routes.py":
        "the healthcare pack's /intake/* routes — the pipeline, ledger and attestation gate",
    "apps/gateway/databridge_gateway/outbox_routes.py":
        "reads intake prefill/questionnaire models for the healthcare outbox playbooks",
}

KNOWN_DEBT: dict[str, str] = {
    "packages/outbox/databridge_outbox/monitors.py":
        "reads the active pack's config dir via databridge_intake.paths; should use "
        "databridge_core.pack_binding.pack_config_dir (CROSS-TRACK)",
    "apps/gateway/databridge_gateway/intelligence_routes.py":
        "checks attestations through databridge_intake.ledger for the 'waiting on a human' card "
        "(C-5 card wording is CROSS-TRACK)",
    "apps/gateway/databridge_gateway/llm.py":
        "loads a pack's config/assistant.yaml through databridge_intake.assistant; the "
        "AssistantConfig loader should move to the engine (CROSS-TRACK)",
}

#: Literal ceiling — lower it when a debt is paid, never raise it.
MAX_KNOWN_DEBT = 3


def _imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module)
        elif isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            name = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if name in ("import_module", "__import__"):
                out.add(n.args[0].value)
    return out


def _imports_vertical(source: str) -> bool:
    return any(m.split(".")[0] == VERTICAL for m in _imports(ast.parse(source)))


def _engine_modules() -> list[Path]:
    out: list[Path] = []
    for top in ("packages", "apps"):
        for p in sorted((REPO / top).rglob("*.py")):
            rel = p.relative_to(REPO)
            if {"tests", "__pycache__", "node_modules"} & set(rel.parts):
                continue
            if rel.parts[:2] == VERTICAL_DIR.parts:
                continue
            out.append(p)
    return out


def _offenders() -> set[str]:
    return {p.relative_to(REPO).as_posix() for p in _engine_modules()
            if _imports_vertical(p.read_text(encoding="utf-8"))}


def test_the_detector_is_not_vacuous():
    positives = [f"import {VERTICAL}", f"from {VERTICAL}.pack import packs_root",
                 f"def f():\n    from {VERTICAL} import guardrails\n",
                 f"import importlib\nimportlib.import_module('{VERTICAL}.clock')",
                 f"__import__('{VERTICAL}')"]
    negatives = ["from databridge_core.pack_binding import packs_root",
                 f"# from {VERTICAL} import nothing", f"X = '{VERTICAL} is mentioned in a string'",
                 "from . import pack"]
    assert all(_imports_vertical(s) for s in positives)
    assert not any(_imports_vertical(s) for s in negatives)


def test_the_scan_covers_the_engine():
    modules = _engine_modules()
    packages = {p.relative_to(REPO).parts[1] for p in modules if p.relative_to(REPO).parts[0] == "packages"}
    assert len(modules) >= 100 and len(packages) >= 10, (len(modules), sorted(packages))
    assert not any(p.relative_to(REPO).parts[:2] == VERTICAL_DIR.parts for p in modules)


def test_no_engine_module_imports_the_vertical_outside_the_allowlist():
    allowed = set(HEALTHCARE_ROUTES) | set(KNOWN_DEBT)
    assert not set(HEALTHCARE_ROUTES) & set(KNOWN_DEBT)
    assert all(reason.strip() for reason in {**HEALTHCARE_ROUTES, **KNOWN_DEBT}.values())
    found = _offenders()
    new = sorted(found - allowed)
    stale = sorted(allowed - found)
    assert not new, ("engine module(s) import the healthcare package — import the engine helper "
                     f"from databridge_core instead:\n" + "\n".join(new))
    assert not stale, f"allowlisted module(s) no longer import {VERTICAL}; remove them: {stale}"


def test_known_debt_may_only_shrink():
    assert len(KNOWN_DEBT) <= MAX_KNOWN_DEBT


def test_the_moved_helpers_are_engine_modules_and_the_old_names_still_resolve():
    """The re-export contract: every old name is importable from the vertical, and IS the engine's
    object (not a copy that could drift)."""
    from databridge_core import clock as core_clock
    from databridge_core import identity_guardrail as core_ig
    from databridge_core import pack_binding as core_pb
    from databridge_intake import clock, guardrails, pack

    for name in ("enabled_packs", "packs_root", "pinned_pack", "pack_dir", "active_pack",
                 "bind_pack", "PackConfigError"):
        assert getattr(pack, name) is getattr(core_pb, name), name
    for name in ("deployment_today", "deployment_timezone", "ClockError", "TIMEZONE_ENV"):
        assert getattr(clock, name) is getattr(core_clock, name), name
    assert guardrails.REFUSAL_CODE == core_ig.REFUSAL_CODE
    assert guardrails.GuardrailResult is core_ig.GuardrailResult
    assert isinstance(guardrails.REFUSAL_TEXT, str) and guardrails.REFUSAL_TEXT
