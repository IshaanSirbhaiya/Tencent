"""`/impact` — the numbers must be computed, not literal.

`PLAN.md` §5 Phase 5 "Done when": /impact renders real computed numbers from the fixtures, not
literals in the JSX. The route test is where that is actually provable — the page can only render
what this returns.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from databridge_gateway import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(scope="module")
def chit():
    from databridge_intake.paths import fixtures_dir

    return (fixtures_dir() / "chits" / "chit_02_everwell_voucher.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def undetermined_chit():
    from databridge_intake.paths import fixtures_dir

    return (fixtures_dir() / "chits" / "chit_08_mol_authorisation_vital.txt").read_text(
        encoding="utf-8")


def test_an_empty_queue_reports_zeros_not_a_projection(client):
    """A dashboard showing a headline figure with no intakes behind it is exactly what makes a
    reader stop believing the rest of the screen."""
    body = client.get("/impact").json()
    if body["measured"]["intakes"] == 0:
        assert body["measured"]["saved_min"] == 0.0
        assert body["measured"]["projected_morning_hours_saved"] == 0.0
    # The BASELINE is always present — it is config, not a measurement.
    assert body["baseline"]["total_min"] > 0
    assert body["baseline"]["steps"]


def test_the_baseline_sits_in_the_problem_statements_band(client):
    body = client.get("/impact").json()
    assert 23.0 <= body["baseline"]["total_min"] <= 32.0


def test_the_identity_step_is_marked_never_automatable_and_saves_nothing(client, chit):
    client.post("/intake/upload", json={"document_id": "impact__chit_02.txt", "text": chit})
    body = client.get("/impact").json()

    step = next(s for s in body["baseline"]["steps"] if s["never_automatable"])
    assert step["max_saving_min"] == 0.0
    assert step["with_copilot_min"] == step["baseline_min"]

    measured = next(s for s in body["measured"]["per_step"] if s["step"] == step["step"])
    assert measured["saved_min"] == 0.0
    assert measured["claimed_count"] == 0


def test_the_numbers_move_when_a_real_intake_is_added(client, chit):
    """The proof that nothing here is a literal: upload a document and the aggregate changes."""
    before = client.get("/impact").json()["measured"]
    client.post("/intake/upload",
                json={"document_id": "impact__moves__chit_02.txt", "text": chit})
    after = client.get("/impact").json()["measured"]
    assert after["intakes"] == before["intakes"] + 1
    assert after["saved_min"] > before["saved_min"]


def test_an_undetermined_intake_drags_the_mean_down(client, chit, undetermined_chit):
    """Chit 8 claims almost nothing. If the mean did not fall, savings were not being earned."""
    client.post("/intake/upload",
                json={"document_id": "impact__mean__chit_02.txt", "text": chit})
    clean_mean = client.get("/impact").json()["measured"]["mean_saved_min"]

    client.post("/intake/upload",
                json={"document_id": "impact__mean__chit_08.txt", "text": undetermined_chit})
    body = client.get("/impact").json()
    assert body["measured"]["mean_saved_min"] < clean_mean

    worst = min(body["per_intake"], key=lambda m: m["saved_min"])
    unclaimed = {s["step"] for s in worst["steps"] if not s["claimed"]}
    assert {5, 6, 7, 12} <= unclaimed, "an undetermined coverage must forfeit those stages"


def test_every_per_step_row_reconciles_with_its_baseline(client, chit):
    client.post("/intake/upload",
                json={"document_id": "impact__reconcile__chit_02.txt", "text": chit})
    body = client.get("/impact").json()
    baseline_by_step = {s["step"]: s for s in body["baseline"]["steps"]}
    for row in body["measured"]["per_step"]:
        b = baseline_by_step[row["step"]]
        # A step can never save more, in total, than its per-intake ceiling × the intakes measured.
        assert row["saved_min"] <= b["max_saving_min"] * body["measured"]["intakes"] + 1e-6


def test_the_morning_figure_is_derived_from_the_measured_mean(client, chit):
    client.post("/intake/upload",
                json={"document_id": "impact__morning__chit_02.txt", "text": chit})
    m = client.get("/impact").json()["measured"]
    expected = round(m["mean_saved_min"] * m["patients_per_morning"] / 60, 1)
    assert m["projected_morning_hours_saved"] == expected
