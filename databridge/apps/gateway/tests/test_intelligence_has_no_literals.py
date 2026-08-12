"""`/intelligence` must render the backend's figures, never its own (T-42 "done when").

Two claims the page makes about itself, made checkable:

1. **Nothing is a literal.** A dashboard whose headline number is hardcoded is worth nothing to
   anyone assessing it, and the difference is invisible in a screenshot. So no JSX text node in the
   intelligence surface may be a bare number.
2. **No issuer, package or test name appears anywhere in the surface.** Adding an insurer is a
   `coverage_rules.yaml` change with zero code changes (CLAUDE.md §Hard constraints 3). A page that
   named one — even in a comment, even as a "helpful" example — is a page someone will later
   special-case.

**These scan the RAW file, comments included, on purpose.** `test_no_acme_branding` learned the
hard way that a scanner which exempts comments is a scanner with a blind spot in it; here the
strictest possible rule is also the cheapest, because no legitimate line in these files needs to
name an insurer or hardcode a count.
"""
from __future__ import annotations

import pathlib
import re

import pytest


def _repo() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3]          # databridge/


def _surface() -> list[pathlib.Path]:
    """Every file that makes up the intelligence surface, front and back."""
    frontend = _repo() / "apps" / "frontend"
    files = [
        frontend / "lib" / "intelligence-api.ts",
        _repo() / "apps" / "gateway" / "databridge_gateway" / "intelligence_routes.py",
        _repo() / "packages" / "graph" / "databridge_graph" / "analytics.py",
    ]
    files += sorted((frontend / "app" / "intelligence").rglob("*.tsx"))
    files += sorted((frontend / "components" / "intelligence").rglob("*.tsx"))
    return [f for f in files if f.exists()]


def _tokens() -> set[str]:
    """Every issuer code, issuer name and package code the config defines.

    Read through `databridge_intake.rules` — the same accessor `test_no_issuer_branching` uses —
    rather than re-parsing the YAML, so adding an insurer widens this guard automatically.
    """
    from databridge_intake import rules

    table = rules()
    tokens = {i.code for i in table.issuers} | {i.name for i in table.issuers}
    tokens |= {p.package_code for p in table.packages if p.package_code}
    return {t for t in tokens if t}


def test_the_surface_files_are_all_present():
    """A guard that silently scans nothing is worse than no guard."""
    found = {f.name for f in _surface()}
    assert {"page.tsx", "intelligence-api.ts", "intelligence_routes.py", "analytics.py"} <= found
    assert len(_surface()) >= 6, sorted(found)


def test_no_issuer_or_package_name_appears_in_the_intelligence_surface():
    tokens = _tokens()
    assert tokens, "the guard must have something to look for"
    offenders = []
    for path in _surface():
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token and token in text:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "issuers and packages are config, not code — the intelligence surface must render "
        "whatever the graph returns: " + "; ".join(sorted(offenders)))


@pytest.mark.parametrize("path", [p for p in _surface() if p.suffix == ".tsx"],
                         ids=lambda p: p.name)
def test_no_jsx_text_node_is_a_bare_number(path: pathlib.Path):
    """`<div>10</div>` — the shape of a hardcoded figure."""
    text = path.read_text(encoding="utf-8")
    literals = re.findall(r">\s*\d[\d,.%]*\s*<", text)
    assert not literals, (
        f"{path.name} renders a hardcoded number ({literals}); every figure on this page must "
        "come from the traversal that computed it")
