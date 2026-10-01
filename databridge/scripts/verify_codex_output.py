#!/usr/bin/env python3
"""Detect codex's silent-failure mode so a parallel review batch cannot fake a clean result.

A failed codex run prints its banner and exits -1. The output then reads exactly like a successful
review that found nothing, which is the most dangerous possible outcome for a review gate: it
manufactures confidence. This script classifies each output file so a batch can be re-run
selectively instead of trusted blindly.

The checks are STRUCTURAL — they assert the shape of a review (it has a body, it reaches a
conclusion, it is proportionate to its input). They never assert what the review should say.

Usage:
    python scripts/verify_codex_output.py <file> [<file> ...]

Exit 0 = every file looks like a genuine review.
Exit 1 = at least one must be re-run.
"""
from __future__ import annotations

import pathlib
import sys

# A real review reaches a conclusion of some kind. Absence of ALL of these means the model never
# got as far as saying anything about the code.
CONCLUSION_MARKERS = (
    "finding", "closed", "verdict", "severity", "issue",
    "recommend", "no issues", "looks correct", "concern",
)

# The failure mode this script exists to catch has a THIRD form, found by testing against a real
# failed run: codex reads nothing, then writes a long, articulate explanation of why it cannot
# review. That text is full of the word "review" and trips every conclusion marker above, so the
# structural checks pass it. An honest refusal is the best possible behaviour from codex and the
# worst possible input to a review gate — it looks like a result and contains none.
REFUSAL_MARKERS = (
    "can't complete", "cannot complete", "unable to complete",
    "unable to review", "can't review", "cannot review",
    "restore read access", "please either", "attach/paste",
    "won't speculate", "will not speculate",
    "return 404", "returns 404 on every branch",
)

MIN_BODY_CHARS = 400          # shorter than this is not a review of anything
SANDBOX_FAILURE = "CreateProcessAsUserW failed"
BANNER_ONLY_EXIT = "exited -1"


def classify(path: pathlib.Path) -> tuple[bool, str]:
    """Return (looks_genuine, reason)."""
    if not path.exists():
        return False, "missing — the run produced no output at all"

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return False, "empty file"

    # Everything before </stdin> is our own echoed input; the review is what follows.
    stdin_echo, marker, response = text.partition("</stdin>")
    body = response if marker else text

    if not body.strip():
        return False, "banner/input only, no response body — THE silent failure"

    if len(body) < MIN_BODY_CHARS:
        return False, f"response is {len(body)} chars — too short to be a review"

    if (BANNER_ONLY_EXIT in text or SANDBOX_FAILURE in text) and len(body) < 2000:
        return False, "sandbox spawn failure with no substantive response"

    lowered = body.lower()

    # Check refusal BEFORE conclusion: a refusal is articulate and would otherwise pass.
    hit = next((m for m in REFUSAL_MARKERS if m in lowered), None)
    if hit is not None:
        return False, f"codex REFUSED to review (matched {hit!r}) — it read nothing"

    if not any(m in lowered for m in CONCLUSION_MARKERS):
        return False, "no findings/verdict language — never reached a conclusion"

    # A review that is negligible next to what it was asked to read is a truncation, not a result.
    if marker and stdin_echo and len(body) < len(stdin_echo) * 0.01:
        return False, (f"response ({len(body)} chars) is negligible against input "
                       f"({len(stdin_echo)} chars) — likely truncated")

    return True, f"ok — {len(body):,} chars of review"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    failures: list[str] = []
    for arg in argv[1:]:
        path = pathlib.Path(arg)
        ok, why = classify(path)
        print(f"[{'PASS ' if ok else 'RERUN'}] {path.name}: {why}")
        if not ok:
            failures.append(path.name)

    if failures:
        print(f"\n{len(failures)} review(s) must be re-run: {', '.join(failures)}")
        return 1

    print(f"\nAll {len(argv) - 1} review(s) look genuine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
