"""CI rule: no vendor SDK imports outside packages/harness (P2 / ADR-0001, ADR-0002).

`import anthropic` / `from openai import ...` (and submodule forms) are FORBIDDEN
anywhere except packages/harness — that is the single place a vendor SDK may appear.
This makes the model-agnostic principle structurally enforceable: the rule passes
vacuously today (harness doesn't exist yet) and fails LOUDLY the moment a vendor import
leaks into any other package.

Usage:
    python scripts/check_vendor_imports.py        # exits 1 on any violation
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Vendor SDK modules that may ONLY be imported under this allowed prefix.
FORBIDDEN = ("anthropic", "openai")
ALLOWED_PREFIX = "packages/harness/"

# Temporary exceptions (DECISIONS D-41): the stand-in model client, used only while Tencent Hy3 is
# not live. Each entry allows exactly ONE lazy `import anthropic` in that one file; a second vendor
# import there, or one anywhere else, still fails. Removed together with the stand-in once Hy3 is live.
EXCEPTIONS = {
    # the stand-in answer backend (a harness Backend), imported only when IB_CLAUDE=1
    "apps/internal_brain/databridge_ib/claude.py": "import anthropic",
    # the stand-in page composer, imported only when IB_CLAUDE=1
    "apps/internal_brain/databridge_ib/views/composer.py": "import anthropic",
}

# Directories never scanned (not first-party source).
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", ".uv", "build", "dist",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "docs", ".claude",
}

_IMPORT_RE = re.compile(
    r"^[ \t]*(?:import|from)[ \t]+(" + "|".join(FORBIDDEN) + r")\b"
)


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line_no, line) for every forbidden vendor import statement in ``text``."""
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if _IMPORT_RE.match(line):
            hits.append((i, line.strip()))
    return hits


def find_violations(root: Path) -> list[tuple[str, int, str]]:
    """Scan every first-party .py file under ``root`` (except the harness) for violations."""
    violations: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if rel.startswith(ALLOWED_PREFIX):
            continue
        hits = scan_text(path.read_text(encoding="utf-8", errors="replace"))
        allowed = EXCEPTIONS.get(rel)
        if allowed is not None and [h[1] for h in hits] == [allowed]:
            continue                                   # exactly the one recorded exception
        for line_no, line in hits:
            violations.append((rel, line_no, line))
    return violations


def main() -> int:
    violations = find_violations(REPO_ROOT)
    if violations:
        print(f"[FAIL] vendor-import rule — {len(violations)} forbidden import(s) "
              f"outside {ALLOWED_PREFIX} (P2):")
        for rel, line_no, line in violations:
            print(f"   - {rel}:{line_no}: {line}")
        return 1
    print(f"[ok] vendor-import rule — no {'/'.join(FORBIDDEN)} imports outside "
          f"{ALLOWED_PREFIX} (temporary exceptions: {len(EXCEPTIONS)}, DECISIONS D-41)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
