"""`PLAN.md` §5 Phase 4 "Done when": no Acme branding survives outside the conversion-engine tests.

This test exists because I marked that criterion complete when it was not. Phase 4b retargeted the
sidebar and reported done, while `/brain` — the second item in the nav — still rendered a company
selector reading "Acme Corp", an ask placeholder saying "Ask anything about Acme Corp…", a toast
announcing "Acme Corp brain loaded", and a hardcoded suggested question about Sarah and BigClient.

Prose *about* the removal is allowed and useful; the check is on code, not comments.

**Why this file blanks spans rather than skipping lines.** The first version classified a whole
LINE as prose if it began with a comment or fell inside a docstring's line range. That is a
scanner with a blind spot in it: `\"\"\"doc\"\"\"; BRAND = "Acme"` and `/* note */ const B = "Acme"`
both put real, shipping code on a line the scanner had already decided was prose. A guard whose
failure mode is silence is worse than no guard, because it is reported as coverage. The comment
spans are now blanked out character by character and what remains is scanned — so code is only
ever exempt if it is genuinely inside a comment.
"""
from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize

import pytest

FORBIDDEN = re.compile(r"acme|bigclient|sarah chen|project atlas", re.I)

# `packages/convert` keeps Acme-shaped fixtures on purpose: they are unit fixtures for the
# conversion engine and have nothing to do with the clinic product (PLAN.md §5 Phase 4).
# Matched as a path PREFIX, and the rest as whole path COMPONENTS — a substring test would exempt
# `apps/frontend/lib/node_modules_helper.ts` and any directory that merely contains the word.
EXEMPT_PREFIXES = ("packages/convert",)
EXEMPT_COMPONENTS = ("node_modules", ".next", "__pycache__", ".git")


def _root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3]          # databridge/


def _is_exempt(rel: str) -> bool:
    if any(part in EXEMPT_COMPONENTS for part in rel.split("/")):
        return True
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in EXEMPT_PREFIXES)


def _blank(grid: list[list[str]], start: tuple[int, int], end: tuple[int, int]) -> None:
    """Replace the characters between ``start`` and ``end`` with spaces, in place.

    Both are ``(1-based row, 0-based col)``, ``end`` exclusive — the convention `ast` and
    `tokenize` both use. Line count and column positions are preserved so the reported line
    numbers still point at the real source.
    """
    (row0, col0), (row1, col1) = start, end
    for row in range(row0, row1 + 1):
        idx = row - 1
        if not 0 <= idx < len(grid):
            continue
        line = grid[idx]
        lo = col0 if row == row0 else 0
        hi = col1 if row == row1 else len(line)
        for col in range(max(lo, 0), min(hi, len(line))):
            line[col] = " "


def code_only_python(text: str) -> list[str]:
    """``text`` with every comment and docstring blanked out, one string per line.

    Docstrings come from `ast` (so only a *docstring* is prose — a string used as a value is code)
    and comments from `tokenize` (so a `#` inside a string literal is not mistaken for one).
    """
    grid = [list(line) for line in text.splitlines()]
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ["".join(line) for line in grid]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            doc = body[0].value
            _blank(grid, (doc.lineno, doc.col_offset),
                   (doc.end_lineno or doc.lineno, doc.end_col_offset or 0))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                _blank(grid, tok.start, tok.end)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass                                        # partial blanking beats none
    return ["".join(line) for line in grid]


def code_only_ts(text: str) -> list[str]:
    """``text`` with `//` and `/* */` comments blanked, one string per line.

    String and template literals are tracked, so a `//` inside `"https://acme.example"` is NOT
    read as the start of a comment — which would otherwise blank the rest of a real line and hide
    exactly the kind of URL this scanner exists to find.

    Known limit: a regex literal containing `//` or `/*` would be mis-read as a comment. None
    exists in this codebase, and distinguishing division from a regex needs a real parser.
    """
    grid = [list(line) for line in text.splitlines()]
    row = col = 0
    i, n = 0, len(text)
    state = "code"
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\n":
            if state == "line_comment":
                state = "code"
            row += 1
            col = 0
            i += 1
            continue
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
            elif ch == "/" and nxt == "*":
                state = "block_comment"
            elif ch in "'\"`":
                state = {"'": "single", '"': "double", "`": "template"}[ch]
        elif state == "block_comment" and ch == "*" and nxt == "/":
            # Blank the closing `*/` itself, then resume scanning the SAME line — the code after
            # it is code, and treating it as prose is the defect this function was rewritten for.
            for offset in (0, 1):
                if 0 <= row < len(grid) and col + offset < len(grid[row]):
                    grid[row][col + offset] = " "
            i += 2
            col += 2
            state = "code"
            continue
        elif state in ("single", "double", "template"):
            if ch == "\\":
                if nxt == "\n":
                    # A line continuation inside a string literal. Skipping two characters without
                    # advancing `row` desynchronised the grid: every subsequent blank landed on the
                    # wrong line, which could erase real code on a preceding line and hide an
                    # offender. Found in review.
                    row += 1
                    col = 0
                    i += 2
                    continue
                i += 2
                col += 2
                continue
            closing = {"single": "'", "double": '"', "template": "`"}[state]
            if ch == closing:
                state = "code"
        if state in ("line_comment", "block_comment"):
            if 0 <= row < len(grid) and col < len(grid[row]):
                grid[row][col] = " "
        i += 1
        col += 1
    return ["".join(line) for line in grid]


def _code_lines(path: pathlib.Path, text: str) -> list[str]:
    return code_only_python(text) if path.suffix == ".py" else code_only_ts(text)


def _offenders(patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for path in _root().rglob(pattern):
            rel = path.relative_to(_root()).as_posix()
            if _is_exempt(rel):
                continue
            text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            for n, line in enumerate(_code_lines(path, text), 1):
                if FORBIDDEN.search(line):
                    found.append(f"{rel}:{n}: {line.strip()[:100]}")
    return found


# --- the scanner itself, which has to be un-foolable before its results mean anything ---------


@pytest.mark.parametrize("source,should_flag", [
    # The defect this was rewritten for: shipping code sharing a line with prose.
    ('"""A module about Acme."""\nBRAND = "Acme Corp"\n', True),
    ('"""doc"""; BRAND = "Acme Corp"\n', True),
    ('x = 1  # we removed Acme here\n', False),
    # A string that merely CONTAINS a hash is not a comment.
    ('URL = "https://acme.example/#top"\n', True),
    # Prose alone still passes — describing the removal is the point.
    ('"""Replaces the Acme Corp demo seed."""\nX = 1\n', False),
])
def test_the_python_scanner_cannot_be_fooled(source, should_flag):
    flagged = any(FORBIDDEN.search(line) for line in code_only_python(source))
    assert flagged is should_flag, f"{source!r} -> flagged={flagged}"


@pytest.mark.parametrize("source,should_flag", [
    ('/* note */ const BRAND = "Acme";\n', True),
    ('/* multi\n   line */ const BRAND = "Acme";\n', True),
    ('// we removed Acme\nconst x = 1;\n', False),
    # A `//` inside a string is not a comment. Reading it as one blanks the rest of the line and
    # hides the very thing being searched for.
    ('const url = "https://acme.example/logo.png";\n', True),
    ('const tpl = `see https://acme.example`;\n', True),
    ('/* Acme is gone */\nconst x = 1;\n', False),
    # A line continuation inside a string. The scanner used to lose track of which row it was on
    # after this, so a later comment blanked the WRONG line and could erase a real offender.
    ('const s = "a\\\n b";\nconst BRAND = "Acme"; // gone\n', True),
])
def test_the_typescript_scanner_cannot_be_fooled(source, should_flag):
    flagged = any(FORBIDDEN.search(line) for line in code_only_ts(source))
    assert flagged is should_flag, f"{source!r} -> flagged={flagged}"


@pytest.mark.parametrize("rel,exempt", [
    ("packages/convert/tests/fixtures/x.py", True),
    ("apps/frontend/node_modules/pkg/index.ts", True),
    # Substring matching used to exempt both of these, which is how a real offender hides.
    ("apps/frontend/lib/node_modules_helper.ts", False),
    ("packages/convert_helpers/x.py", False),
    ("packages/intake/databridge_intake/claim.py", False),
])
def test_the_exemption_matches_paths_not_substrings(rel, exempt):
    assert _is_exempt(rel) is exempt


# --- and then the scan itself ------------------------------------------------------------------


def test_no_acme_branding_in_the_frontend():
    """The one that actually got missed. `/brain` is linked from the sidebar, so anything here is
    on screen for anyone clicking through the demo."""
    offenders = [o for o in _offenders(("apps/frontend/**/*.tsx", "apps/frontend/**/*.ts"))]
    assert offenders == [], "Acme branding is still rendered:\n" + "\n".join(offenders)


def test_no_acme_branding_in_the_python_product_code():
    """Product code only. Tests are deliberately out of scope: the guard tests have to *name* the
    strings they forbid (this file does, and `test_intake_routes.py` asserts no suggested question
    mentions Acme). Scanning them would mean a test that fails because another test guards the same
    thing — so the scan covers what ships, and `test_no_acme_branding_in_the_frontend` covers what
    renders."""
    offenders = [o for o in _offenders(("packages/intake/**/*.py", "packages/graph/**/*.py",
                                        "packages/evals/**/*.py", "apps/gateway/**/*.py",
                                        "apps/mcp_server/**/*.py"))
                 if "/tests/" not in o]
    assert offenders == [], "Acme references in product code:\n" + "\n".join(offenders)


@pytest.mark.parametrize("path", [
    "apps/frontend/app/layout.tsx",
    "apps/frontend/components/brain/brain-header.tsx",
])
def test_the_visible_branding_comes_from_the_active_pack(path):
    """Phase 1 replaces this check's subject without weakening it.

    The original asserted two literals — "Parkway Shenton" and "Clinic brain" — were *present*,
    because the failure it was written for was Acme branding deleted from the sidebar and left
    everywhere else. That was the right check while one vertical was the product.

    It is the wrong check now, and keeping it would be worse than deleting it: it would pin the
    platform to one pack's vocabulary and fail the moment a deployment enabled finance. The
    engine must know no domain by name (CLAUDE.md), and a *test* asserting a domain literal is
    the same violation as code doing it.

    So the intent is preserved and generalised: branding must still be *deliberate* rather than
    deleted — it now comes from the active pack, and never from a literal in the component.
    """
    source = (_root() / path).read_text(encoding="utf-8")

    # A file participates in pack vocabulary either by MOUNTING the provider (layouts) or by
    # CONSUMING it (components). Requiring the consumer form only would fail the layout that makes
    # every consumer possible.
    participates = re.search(r"PackProvider|usePack|useNouns|pack\.", source)
    assert participates is not None, (
        f"{path} neither provides nor consumes pack vocabulary. Branding must come from the "
        f"active pack — not hardcoded, and not simply deleted.")

    # The forbidden set is what the platform must never name: any single pack's identity.
    # `FORBIDDEN` already covers the Acme fixtures; these are the vertical's own literals.
    # Scanned on CODE ONLY — prose explaining the removal is allowed, same rule as `_offenders`.
    domain_literals = re.compile(r"parkway|shenton|hack4health|\bclinic\b", re.I)
    offenders = [f"line {n}: {line.strip()[:100]}"
                 for n, line in enumerate(_code_lines(_root() / path, source), 1)
                 if domain_literals.search(line)]
    assert offenders == [], (
        f"{path} hardcodes one pack's vocabulary:\n" + "\n".join(offenders) +
        "\nUse the pack nouns so a finance or government deployment reads correctly.")


def test_the_ask_panel_takes_its_questions_from_config():
    """`/brain/suggested` serves `config/assistant.yaml`, built in Phase 4a precisely so the demo's
    prompts are config. The panel was still ignoring it and rendering a hardcoded list."""
    panel = (_root() / "apps/frontend/components/brain/ask-panel.tsx").read_text(encoding="utf-8")
    assert "/brain/suggested" in panel
    assert "suggested.map" in panel


def test_the_scripted_tour_only_visits_live_routes():
    """The tour used to route to `/connectors`, retired from the nav in Phase 4b — a scripted demo
    that navigates to a retired screen is worse than no tour."""
    tour = (_root() / "apps/frontend/components/demo/demo-overlay.tsx").read_text(encoding="utf-8")
    live = {"/frontdesk", "/intake", "/brain", "/impact", "/evals"}
    routed = set(re.findall(r'route:\s*"([^"]+)"', tour))
    assert routed <= live, f"the tour visits retired routes: {sorted(routed - live)}"
