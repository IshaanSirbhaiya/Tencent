"""How much of today's code is the original Walnut base?

    python databridge/scripts/measure_original_share.py [base-commit]     (default 078572e)

Counts code lines (py, ts/tsx/js, css, sh/ps1, yaml, sql, toml; not lockfiles, node_modules, build
output or generated JSON) at the base commit and at HEAD, and reports the share of HEAD's code lines
that are unchanged original lines: (HEAD lines - lines added since the base) / HEAD lines.
"""
from __future__ import annotations

import re
import subprocess
import sys

CODE = re.compile(r"\.(py|ts|tsx|js|jsx|cjs|mjs|css|sh|ps1|yaml|yml|sql|toml)$")
SKIP = re.compile(r"(^|/)(node_modules|\.next)(/|$)|package-lock\.json$|fixtures/.*\.json$|results\.json$|\.lock$")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def is_code(path: str) -> bool:
    return bool(CODE.search(path)) and not SKIP.search(path)


def lines(rev: str, path: str) -> int:
    return subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True).stdout.count(b"\n")


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "078572e"
    head_files = [f for f in git("ls-tree", "-r", "--name-only", "HEAD").splitlines() if is_code(f)]
    head_total = sum(lines("HEAD", f) for f in head_files)
    added = 0
    for row in git("diff", "--numstat", base, "HEAD").splitlines():
        a, _d, path = (row.split("\t") + ["", "", ""])[:3]
        path = path.split(" => ")[-1].rstrip("}")
        if a != "-" and is_code(path):
            added += int(a)
    share = (head_total - added) / head_total
    print(f"code lines at HEAD: {head_total:,}; added since {base}: {added:,}")
    print(f"original Walnut share of today's code: {share:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
