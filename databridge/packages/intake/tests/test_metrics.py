"""Time-saved metrics.

The load-bearing test is `test_identity_verification_can_never_contribute_a_saving`. Everything
else here defends the same property from a different angle: the headline number must be *earned*
by stages that actually completed, so that a judge who disbelieves it can check it.
"""
from __future__ import annotations

import datetime as dt

import pytest
import yaml

from databridge_intake.metrics import (BaselineError, aggregate, baseline, load_baseline, measure)
from databridge_intake.ledger import subject_fingerprint_for
from databridge_intake.models import ConfirmationState
from databridge_intake.paths import config_dir, fixtures_dir
from databridge_intake.pipeline import run_intake

STAFF = "staff:metrics"
NOW = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)


def _confirmed_draft(name: str):
    """An intake taken as far as a real counter would take it: patient selected, form pre-filled."""
    path = fixtures_dir() / "chits" / name
    text = path.read_text(encoding="utf-8")
    draft = run_intake(text, document_id=name)
    if not draft.resolution.candidates:
        return draft
    selected = draft.resolution.candidates[0].record
    state = (ConfirmationState(intake_id=draft.intake_id)
             .attest_identity_checked(STAFF, NOW)
             .select_patient(selected.patient_id,
                             subject_fingerprint=subject_fingerprint_for(
                                 draft.record, draft.resolution, selected.patient_id)))
    return run_intake(text, document_id=name, intake_id=draft.intake_id,
                      confirmation=state, selected_patient=selected)


@pytest.fixture(scope="module")
def measured():
    return {p.name: measure(_confirmed_draft(p.name))
            for p in sorted((fixtures_dir() / "chits").glob("*"))}


# --- THE CONSTRAINT ---------------------------------------------------------------------


def test_identity_verification_can_never_contribute_a_saving(measured):
    """`PLAN.md` §5 Phase 5, verbatim: step 2's contribution to minutes_saved is exactly 0."""
    for name, metrics in measured.items():
        step2 = next(s for s in metrics.steps if s.step == 2)
        assert step2.saved_min == 0.0, name
        assert step2.claimed is False, name
        assert step2.never_automatable is True, name
        assert step2.with_copilot_min == step2.baseline_min, name


def test_the_zero_is_structural_not_arithmetic():
    """Edit the YAML so step 2 *looks* automatable and cheap — the saving must still be zero.

    A number that comes out at zero because the inputs happen to be equal is a coincidence one
    edit away from being wrong. This asserts the guarantee survives a hostile config.
    """
    from databridge_intake.metrics import _saving_for
    from databridge_intake.metrics import BaselineStep

    hostile = BaselineStep(
        step=2, name="Identity verification", baseline_min_low=10.0, baseline_min_high=10.0,
        with_copilot_min=0.0, automatable=False, never_automatable=True, claim_condition="never")
    saving = _saving_for(hostile, {c: True for c in ("never", "document_understood")})
    assert saving.saved_min == 0.0
    assert saving.claimed is False


def test_a_never_automatable_step_that_claims_something_fails_at_load(tmp_path):
    """The two markers must agree, or the file is lying to itself."""
    data = yaml.safe_load((config_dir() / "time_baseline.yaml").read_text(encoding="utf-8"))
    step = next(s for s in data["steps"] if s.get("never_automatable"))
    step["claim_condition"] = "document_understood"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(Exception, match="never_automatable"):
        load_baseline(path)


def test_a_baseline_with_no_never_automatable_step_is_refused(tmp_path):
    """Losing the marker would make the guarantee silently vacuous."""
    data = yaml.safe_load((config_dir() / "time_baseline.yaml").read_text(encoding="utf-8"))
    for step in data["steps"]:
        step.pop("never_automatable", None)
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(BaselineError, match="never_automatable"):
        load_baseline(path)


def test_moving_the_marker_off_the_identity_step_is_refused(tmp_path):
    """The guarantee has to be pinned to IDENTITY, not to "some step somewhere". Requiring only
    that *a* step carries the marker let someone move it to step 1, make step 2 automatable, and
    pass validation while identity quietly earned minutes."""
    data = yaml.safe_load((config_dir() / "time_baseline.yaml").read_text(encoding="utf-8"))
    identity = next(s for s in data["steps"] if s["step"] == data["identity_step"])
    other = next(s for s in data["steps"] if s["step"] != data["identity_step"]
                 and not s.get("automatable"))
    identity.pop("never_automatable")
    other["never_automatable"] = True
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(BaselineError, match="identity step"):
        load_baseline(path)


def test_a_table_that_does_not_reconstruct_its_own_band_is_refused(tmp_path):
    """The submission and /impact both say the baseline is the midpoint of the stated band. If the
    per-step rows sum to something else, that sentence is false — and it was, before review: the
    rows summed to 21.5-33.0 while claiming to decompose 23-32."""
    data = yaml.safe_load((config_dir() / "time_baseline.yaml").read_text(encoding="utf-8"))
    data["steps"][0]["baseline_min_low"] += 1.0
    data["steps"][0]["baseline_min_high"] += 1.0
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(BaselineError, match="claims to decompose"):
        load_baseline(path)


def test_the_shipped_table_reconstructs_the_stated_band():
    table = baseline()
    assert round(sum(s.baseline_min_low for s in table.steps), 2) == table.band_min_total
    assert round(sum(s.baseline_min_high for s in table.steps), 2) == table.band_max_total
    # And the quoted total really is the band's midpoint.
    assert table.baseline_total == (table.band_min_total + table.band_max_total) / 2


@pytest.mark.parametrize("with_copilot", [-1.0, 99.0])
def test_a_with_copilot_time_outside_the_baseline_is_refused(tmp_path, with_copilot):
    """A negative time inflates the claim silently; one above the baseline makes the saving
    negative, pushing the residual above the baseline and inverting `forfeited_min`."""
    data = yaml.safe_load((config_dir() / "time_baseline.yaml").read_text(encoding="utf-8"))
    next(s for s in data["steps"] if s["claim_condition"] == "document_understood")[
        "with_copilot_min"] = with_copilot
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(Exception):
        load_baseline(path)


def test_an_unknown_claim_condition_is_refused(tmp_path):
    """A typo would otherwise read as "never claimed" and quietly shrink the headline number."""
    data = yaml.safe_load((config_dir() / "time_baseline.yaml").read_text(encoding="utf-8"))
    next(s for s in data["steps"] if s["claim_condition"] == "coverage_determined")[
        "claim_condition"] = "coverage_determind"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(Exception, match="claim_condition"):
        load_baseline(path)


# --- savings are EARNED -----------------------------------------------------------------


def test_an_undetermined_coverage_forfeits_the_stages_it_could_not_complete(measured):
    """Chit 8 — no test code on the form. Staff still have to phone the issuer, so the eligibility,
    package, billing and TPA minutes are still spent and must not be claimed."""
    metrics = measured["chit_08_mol_authorisation_vital.txt"]
    unclaimed = {s.step for s in metrics.steps if not s.claimed}
    assert {5, 6, 7, 12} <= unclaimed
    assert metrics.forfeited_min > 10, "chit 8 should forfeit most of its potential"
    # It still earns the one stage it DID complete: the document was read.
    assert next(s for s in metrics.steps if s.step == 4).claimed is True


def test_a_clean_intake_claims_the_stages_it_completed(measured):
    metrics = measured["chit_02_everwell_voucher.txt"]
    claimed = {s.step for s in metrics.steps if s.claimed}
    assert {3, 4, 5, 6, 7, 11} <= claimed
    # NOT step 12. The TPA claim draft does not exist until a human confirms the intake —
    # `/intake/{id}/claim-draft` refuses before that — so its minutes are not claimable here.
    assert 12 not in claimed


def test_the_tpa_saving_requires_a_confirmed_intake():
    """The largest single line in the as-is table (4.5 min) is also the easiest to overclaim: it is
    tempting to count it as soon as coverage resolves. But the artifact only exists after
    confirmation, so an unconfirmed intake claims nothing for it."""
    draft = _confirmed_draft("chit_02_everwell_voucher.txt")
    before = measure(draft, confirmed=False)
    after = measure(draft, confirmed=True)
    assert next(s for s in before.steps if s.step == 12).claimed is False
    assert next(s for s in after.steps if s.step == 12).claimed is True
    assert after.saved_min > before.saved_min


def test_an_occupational_form_does_not_claim_the_allergy_step(measured):
    """Chit 7 routes to the Occupational Health questionnaire, which has no drug-allergy field —
    so the pharmacy re-ask is not avoided and its minutes are not claimed."""
    metrics = measured["chit_07_mol_authorisation_pee226.txt"]
    step11 = next(s for s in metrics.steps if s.step == 11)
    assert step11.claimed is False
    assert step11.saved_min == 0.0


def test_nothing_is_claimed_before_a_patient_is_selected():
    """An unresolved intake has pre-filled nothing, so the registration minutes are still spent."""
    path = fixtures_dir() / "chits" / "chit_02_everwell_voucher.txt"
    unresolved = run_intake(path.read_text(encoding="utf-8"), document_id=path.name)
    metrics = measure(unresolved)
    assert next(s for s in metrics.steps if s.step == 3).claimed is False
    assert next(s for s in metrics.steps if s.step == 11).claimed is False


def test_one_prefilled_field_does_not_earn_the_whole_registration_saving():
    """The registration saving is for replacing administrative re-entry. A single pre-filled field
    — especially the allergy field that step 11 claims separately — must not earn all 2.25 minutes
    and then 1.5 more for the pharmacy."""
    from databridge_intake.metrics import _conditions_met
    from databridge_intake.models import (PrefilledField, PrefilledQuestionnaire, PrefillStatus,
                                          QuestionnaireKind)
    from databridge_core import GroundedValue, SourcePointer

    draft = _confirmed_draft("chit_02_everwell_voucher.txt")
    ptr = SourcePointer(source_id="r.csv", kind="table_cell", table="t", column="c", row_id="1")
    allergy_only = PrefilledQuestionnaire(
        kind=QuestionnaireKind.GENERAL_HEALTH,
        fields=(PrefilledField(field_id="drug_allergies", label="Drug Allergies",
                               value=GroundedValue(value="Latex", pointer=ptr, confidence=1.0),
                               status=PrefillStatus.PREFILLED, requires_confirmation=True),))
    thin = draft.model_copy(update={"questionnaire": allergy_only})
    conditions = _conditions_met(thin)
    assert conditions["allergy_carried_forward"] is True
    assert conditions["questionnaire_prefilled"] is False


def test_document_understood_is_not_trivially_true():
    """`grounded_fields()` always contains `document_type`, so a truthiness check on it held for
    every intake — including one where the grounding wall refused everything else."""
    from databridge_intake.metrics import _conditions_met

    draft = _confirmed_draft("chit_02_everwell_voucher.txt")
    stripped = draft.record.model_copy(update={
        "issuer": draft.record.issuer.model_copy(update={
            "issuer_name": None, "issuer_code": None, "policy_number": None,
            "reference_number": None, "contact": None}),
        "subject": draft.record.subject.model_copy(update={
            "claimed_name": None, "claimed_id_number": None, "claimed_id_type": None}),
        "authorisation": draft.record.authorisation.model_copy(update={
            "requested_tests": [], "add_ons": [], "package_code": None, "package_name": None,
            "appointment_datetime": None, "appointment_location": None, "purpose_text": None,
            "valid_from": None, "valid_until": None, "billing_party": None,
            "billing_address": None, "special_instructions": []}),
        "employer": draft.record.employer.model_copy(update={
            "employer_name": None, "agency": None, "scheme_of_service": None, "hr_contact": None}),
        "visit_type": None,
        "unresolved": (), "refused": ()})
    empty = draft.model_copy(update={"record": stripped})
    assert _conditions_met(empty)["document_understood"] is False


def test_a_partial_coverage_does_not_earn_the_package_verification_saving():
    """PARTIAL means a rule fired but something still needs a human — an age flag or an unmatched
    test. That is exactly the case where staff verify the package by hand."""
    from databridge_intake.metrics import _conditions_met

    partial = _confirmed_draft("chit_05_bluepeak_wellness_voucher.txt")
    assert partial.coverage.status.value == "partial"
    conditions = _conditions_met(partial)
    assert conditions["coverage_determined"] is True       # the lookup DID run
    assert conditions["questionnaire_routed"] is False     # but the package is not settled


def test_billing_minutes_need_a_determined_coverage_not_just_a_payer():
    """Enforced in metrics rather than relying on billing.py happening to return "unknown" — that
    was an accidental cross-module invariant, not a guarantee."""
    from databridge_intake.metrics import _conditions_met

    draft = _confirmed_draft("chit_08_mol_authorisation_vital.txt")
    forged = draft.model_copy(update={
        "billing": draft.billing.model_copy(update={"billed_to": "insurer"})})
    assert forged.coverage.status.value == "undetermined"
    assert _conditions_met(forged)["billing_drafted"] is False


def test_every_unclaimed_step_says_why(measured):
    """A zero with no reason is indistinguishable from a bug."""
    for name, metrics in measured.items():
        for step in metrics.steps:
            if not step.claimed:
                assert step.reason, f"{name} step {step.step}"


# --- the totals -------------------------------------------------------------------------


def test_the_baseline_sits_inside_the_problem_statements_band():
    """The Technical Problem Statement says 23-32 minutes. A baseline outside that is not the
    workflow we claim to be improving."""
    assert 23.0 <= baseline().baseline_total <= 32.0


def test_saved_never_exceeds_the_baseline(measured):
    for name, metrics in measured.items():
        assert 0.0 <= metrics.saved_min <= metrics.baseline_total_min, name
        assert 0.0 <= metrics.saved_pct <= 100.0, name


def test_saved_plus_residual_reconstructs_the_baseline(measured):
    for name, metrics in measured.items():
        assert metrics.saved_min + metrics.actual_total_min == pytest.approx(
            metrics.baseline_total_min), name


def test_the_aggregate_is_measured_not_asserted(measured):
    """The morning figure is mean × patients_per_morning — a projection from a MEASURED mean, and
    the field name says so."""
    agg = aggregate(list(measured.values()))
    assert agg.intakes == len(measured)
    expected = round(agg.mean_saved_min * agg.patients_per_morning / 60, 1)
    assert agg.projected_morning_hours_saved == expected


def test_the_aggregate_mean_is_dragged_down_by_the_failures(measured):
    """The honest consequence of earning savings: chit 8 pulls the mean below the per-intake best
    case. A mean equal to the best case would mean nothing was being measured."""
    agg = aggregate(list(measured.values()))
    best = max(m.saved_min for m in measured.values())
    assert agg.mean_saved_min < best


def test_the_identity_step_contributes_zero_to_the_aggregate(measured):
    agg = aggregate(list(measured.values()))
    row = next(r for r in agg.per_step if r["never_automatable"])
    assert row["saved_min"] == 0.0
    assert row["claimed_count"] == 0


def test_an_empty_aggregate_is_zero_not_a_crash():
    agg = aggregate([])
    assert agg.intakes == 0 and agg.saved_min == 0.0
    assert agg.projected_morning_hours_saved == 0.0
