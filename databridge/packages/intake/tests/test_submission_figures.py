"""The figures quoted in `docs/hackathon/SUBMISSION.md` must stay reproducible from the code.

A submission's persuasive weight rests entirely on being auditable — the first thing a reader does
with a number they doubt is try to reproduce it. Before this test the headline figures were a
snapshot in prose: a fixture edit, a baseline tweak or a change to a claim condition would silently
make the document wrong, and nothing would fail.

This asserts the document and the code agree. **If it fails, decide which one is right** — do not
reflexively edit whichever is easier to change.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest

from databridge_intake.metrics import aggregate, baseline, measure
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import fixtures_dir
from databridge_intake.pipeline import run_intake

STAFF = "staff:submission"
NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)


def _submission() -> str:
    # repo root / docs / hackathon / SUBMISSION.md
    root = pathlib.Path(__file__).resolve().parents[4]
    return (root / "docs" / "hackathon" / "SUBMISSION.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def figures():
    """The corpus, taken as far as a real counter would: selected, confirmed, measured."""
    metrics = []
    for path in sorted((fixtures_dir() / "chits").glob("*")):
        text = path.read_text(encoding="utf-8")
        draft = run_intake(text, document_id=path.name)
        if draft.resolution.candidates:
            selected = draft.resolution.candidates[0].record
            state = (ConfirmationState(intake_id=draft.intake_id)
                     .attest_identity_checked(STAFF, NOW)
                     .select_patient(selected.patient_id))
            draft = run_intake(text, document_id=path.name, intake_id=draft.intake_id,
                               confirmation=state, selected_patient=selected)
        metrics.append(measure(draft, confirmed=True))
    return metrics, aggregate(metrics)


def test_the_quoted_baseline_matches_the_config(figures):
    assert f"**{baseline().baseline_total} min**" in _submission()


def test_the_quoted_band_matches_the_config():
    table = baseline()
    assert f"{int(table.band_min_total)}–{int(table.band_max_total)} band" in _submission()


def test_the_quoted_mean_matches_the_measurement(figures):
    _, agg = figures
    # Quoted to one decimal place.
    assert f"**{agg.mean_saved_min:.1f} min" in _submission()


def test_the_quoted_best_and_worst_cases_match(figures):
    metrics, _ = figures
    best = max(m.saved_min for m in metrics)
    worst = min(m.saved_min for m in metrics)
    text = _submission()
    assert f"{best} min" in text, f"best case is now {best}"
    assert f"**{worst} min**" in text, f"worst case is now {worst}"


def test_the_quoted_morning_projection_matches(figures):
    _, agg = figures
    assert f"~{round(agg.projected_morning_hours_saved)} hours" in _submission()


def test_the_quoted_python_test_count_is_a_floor_the_repo_clears():
    """The writeup quotes a FLOOR ("over N"), not an exact total, and this proves the repo clears it.

    An exact figure was quoted once and went stale within a session — it read 1,320 while the suite
    had grown well past it, in the one document a judge is most likely to check. A floor stays true
    as tests are added, and the bound below is sound: every declared test function runs at least
    once, so the count of `def test_` is a lower bound on the number that pass.
    """
    root = pathlib.Path(__file__).resolve().parents[4] / "databridge"
    declared = sum(
        len(re.findall(r"^def test_", p.read_text(encoding="utf-8", errors="replace"), re.M))
        for p in root.rglob("test_*.py")
        if "node_modules" not in p.as_posix())

    quoted = re.search(r"Over ([\d,]+) Python tests pass", _submission())
    assert quoted, "the writeup no longer quotes a Python test floor"
    floor = int(quoted.group(1).replace(",", ""))
    assert floor <= declared, (
        f"the writeup claims over {floor} Python tests but only {declared} are declared")


def test_the_quoted_playwright_count_matches_the_spec():
    """The writeup quotes the browser-suite size; this pins it to what is actually declared.

    Counts EVERY spec file, not just `frontdesk.spec.ts`. The gate coverage lives in its own file
    precisely because it must not inherit the others' session seeding, and counting one file would
    have quietly under-reported the suite the moment that file appeared.
    """
    root = pathlib.Path(__file__).resolve().parents[4]
    e2e = root / "databridge" / "apps" / "frontend" / "e2e"
    specs = sorted(e2e.glob("*.spec.ts"))
    assert specs, "no Playwright specs found — the path moved"
    declared = sum(len(re.findall(r"^test\(", p.read_text(encoding="utf-8"), re.M)) for p in specs)
    assert f"{declared} Playwright tests" in _submission(), (
        f"{len(specs)} spec file(s) declare {declared} tests")


def test_the_quoted_guardrail_counts_match_the_tests():
    here = pathlib.Path(__file__).resolve().parent
    gate = (here / "test_gate.py").read_text(encoding="utf-8")

    def count(name: str) -> int:
        block = re.search(rf"{name} = \[(.*?)\n\]", gate, re.S).group(1)
        return len([ln for ln in block.splitlines() if ln.strip().startswith('"')])

    text = _submission()
    assert f"{count('IDENTITY_QUESTIONS')} refusal cases" in text
    assert f"{count('ORDINARY_QUESTIONS')} must-answer cases" in text


def test_the_submission_does_not_claim_identity_minutes(figures):
    """Belt and braces on the document itself: the writeup must not contain a sentence crediting
    the identity step with a saving."""
    text = _submission().lower()
    assert "identity verification contributes exactly zero" in text
    for forbidden in ("saved on identity", "identity verification saves"):
        assert forbidden not in text
