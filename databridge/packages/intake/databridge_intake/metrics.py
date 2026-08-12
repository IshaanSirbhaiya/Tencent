"""Minutes-of-admin-saved — computed per intake, from stages that actually completed.

**The number has to be honest or it is worthless to the judges**, so this module is built to make
overclaiming difficult rather than merely discouraged:

* **A step's saving is earned, not assumed.** Each step in ``config/time_baseline.yaml`` names a
  ``claim_condition``, and the condition is evaluated against the real ``IntakeDraft``. Chit 8's
  coverage is undetermined, so it claims nothing for eligibility, package routing, billing or the
  TPA draft — staff still have to phone the issuer, so the minutes are still spent.
* **Identity verification can never contribute.** Step 2 carries ``never_automatable: true``, and
  ``_saving_for`` returns zero for it *before* looking at any other field. Editing
  ``with_copilot_min`` down to zero in the YAML would still produce a zero saving. That is the
  point: the constraint is structural, not arithmetic that happens to come out right.
* **The baseline is a band, and the midpoint is what we quote.** ``baseline_min_low``/``_high``
  come from the problem statement's 23-32 minute range; quoting the high end everywhere would
  inflate every claim by ~20%.

Nothing here is a projection. ``per_morning`` multiplies a measured per-intake figure by the
dataset's stated patient count and says so.
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import CoverageStatus, DocumentType, PrefillStatus, QuestionnaireKind
from .paths import config_dir

if TYPE_CHECKING:                                   # pragma: no cover
    from .pipeline import IntakeDraft

# Claim conditions a step may name. A step whose condition is not in this set fails at load —
# a typo would otherwise silently mean "never claimed" and quietly shrink the headline number,
# or worse, be read as "always".
CLAIM_CONDITIONS = frozenset({
    "never",
    "document_understood",
    "questionnaire_prefilled",
    "coverage_determined",
    "questionnaire_routed",
    "billing_drafted",
    "allergy_carried_forward",
    "claim_draft_available",
})

# The registration saving is about ADMINISTRATIVE re-entry. Drug allergy is pre-filled too, but
# step 11 claims it separately — counting it here would let one field earn both savings.
_NOT_REGISTRATION_FIELDS = frozenset({"drug_allergies"})

# "The document was understood" must mean more than "a record exists". `grounded_fields()` always
# contains `document_type`, so a truthiness check on it is trivially true for every intake —
# including one where the grounding wall refused everything else.
MIN_GROUNDED_FIELDS_FOR_UNDERSTOOD = 3
MIN_PREFILLED_FOR_REGISTRATION = 5


class BaselineError(RuntimeError):
    """The time baseline is missing, malformed, or internally inconsistent."""


class BaselineStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int
    name: str
    baseline_min_low: float
    baseline_min_high: float
    with_copilot_min: float
    automatable: bool
    never_automatable: bool = False
    claim_condition: str
    note: str = ""

    @model_validator(mode="after")
    def _coherent(self) -> "BaselineStep":
        if self.baseline_min_low > self.baseline_min_high:
            raise ValueError(f"step {self.step}: baseline_min_low > baseline_min_high")
        if min(self.baseline_min_low, self.with_copilot_min) < 0:
            raise ValueError(f"step {self.step}: negative minutes")
        # The one arithmetic door otherwise left open: a `with_copilot_min` below zero inflates the
        # claim silently, and one above the baseline makes `saved_min` NEGATIVE — which pushes
        # `actual_total_min` above the baseline and inverts `forfeited_min`.
        midpoint = (self.baseline_min_low + self.baseline_min_high) / 2
        if self.with_copilot_min > midpoint:
            raise ValueError(
                f"step {self.step}: with_copilot_min ({self.with_copilot_min}) exceeds the "
                f"baseline midpoint ({midpoint}) — that is a cost, not a saving")
        if self.claim_condition not in CLAIM_CONDITIONS:
            raise ValueError(
                f"step {self.step}: unknown claim_condition {self.claim_condition!r} "
                f"(known: {sorted(CLAIM_CONDITIONS)})")
        if self.never_automatable and self.claim_condition != "never":
            raise ValueError(
                f"step {self.step} is never_automatable but names claim_condition "
                f"{self.claim_condition!r} — it must be 'never'")
        if self.never_automatable and self.automatable:
            raise ValueError(f"step {self.step} is both automatable and never_automatable")
        return self

    @property
    def baseline_midpoint(self) -> float:
        """What we quote. The high end everywhere would inflate every claim by ~20%."""
        return (self.baseline_min_low + self.baseline_min_high) / 2

    @property
    def max_saving(self) -> float:
        """The most this step could ever save — used for the 'potential' column, never claimed."""
        if self.never_automatable or not self.automatable:
            return 0.0
        return max(0.0, self.baseline_midpoint - self.with_copilot_min)


class TimeBaseline(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    patients_per_morning: int
    # The step that IS identity verification, named rather than inferred — see `load_baseline`.
    identity_step: int
    band_min_total: float
    band_max_total: float
    steps: list[BaselineStep]

    @property
    def baseline_total(self) -> float:
        return sum(s.baseline_midpoint for s in self.steps)

    def step(self, number: int) -> Optional[BaselineStep]:
        return next((s for s in self.steps if s.step == number), None)


def load_baseline(path=None) -> TimeBaseline:
    baseline_path = path or (config_dir() / "time_baseline.yaml")
    if not baseline_path.exists():
        raise BaselineError(f"time baseline not found at {baseline_path}")
    data = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BaselineError(f"{baseline_path} did not parse to a mapping")
    baseline = TimeBaseline.model_validate(data)
    numbers = [s.step for s in baseline.steps]
    if len(numbers) != len(set(numbers)):
        raise BaselineError(f"{baseline_path}: duplicate step number(s)")
    # The guarantee must be pinned to IDENTITY, not to "some step somewhere". Requiring only that
    # *a* step carries the marker lets someone move it to step 1, make step 2 automatable with a
    # satisfiable condition, and pass validation while identity quietly earns minutes.
    identity = baseline.step(baseline.identity_step)
    if identity is None:
        raise BaselineError(
            f"{baseline_path}: identity_step={baseline.identity_step} names no step in the table")
    if not identity.never_automatable:
        raise BaselineError(
            f"{baseline_path}: step {identity.step} is the identity step and must carry "
            f"never_automatable (CLAUDE.md §Hard constraints 1)")

    # The table must reconstruct the band it claims to decompose, or "the midpoint of the 23-32
    # band" on /impact and in the submission is simply a false statement about this file.
    low = round(sum(s.baseline_min_low for s in baseline.steps), 2)
    high = round(sum(s.baseline_min_high for s in baseline.steps), 2)
    if (low, high) != (baseline.band_min_total, baseline.band_max_total):
        raise BaselineError(
            f"{baseline_path}: per-step bands sum to {low}-{high}, but the file claims to "
            f"decompose {baseline.band_min_total}-{baseline.band_max_total}")
    return baseline


@functools.lru_cache(maxsize=1)
def baseline() -> TimeBaseline:
    return load_baseline()


# --- did this stage actually complete? --------------------------------------------


def _conditions_met(draft: "IntakeDraft", *, confirmed: bool = False) -> dict[str, bool]:
    """Evaluate every claim condition against what the pipeline really produced.

    Each is a fact about the draft, not a judgement. "The coverage came back determined" is
    checkable; "the system probably helped" is not.
    """
    record = draft.record
    coverage = draft.coverage
    questionnaire = draft.questionnaire

    document_understood = (
        record.document_type.value not in {DocumentType.UNRECOGNISED.value,
                                           DocumentType.NO_DOCUMENT.value}
        # More than `document_type` alone — see MIN_GROUNDED_FIELDS_FOR_UNDERSTOOD.
        and len(record.grounded_fields()) >= MIN_GROUNDED_FIELDS_FOR_UNDERSTOOD
    )
    coverage_determined = coverage.status is not CoverageStatus.UNDETERMINED
    # DETERMINED only. A PARTIAL carries a flag or an unmatched test — precisely the case where
    # staff still verify the package by hand — so step 6's saving is not earned even though a rule
    # fired.
    coverage_clean = coverage.status is CoverageStatus.DETERMINED
    allergy_carried = any(
        f.field_id == "drug_allergies" and f.status is PrefillStatus.PREFILLED
        for f in questionnaire.fields)
    registration_fields = sum(
        1 for f in questionnaire.fields
        if f.status is PrefillStatus.PREFILLED and f.field_id not in _NOT_REGISTRATION_FIELDS)
    has_payer = draft.billing.billed_to not in {"unknown", ""}

    return {
        "never": False,
        "document_understood": document_understood,
        # Enough administrative fields to have genuinely replaced the re-keying, and NOT counting
        # the allergy field that step 11 claims separately.
        "questionnaire_prefilled": registration_fields >= MIN_PREFILLED_FOR_REGISTRATION,
        "coverage_determined": coverage_determined,
        "questionnaire_routed": (coverage_clean
                                 and coverage.questionnaire is not QuestionnaireKind.NONE),
        # A payer alone is not enough — an undetermined coverage carrying a payer string would
        # still claim the minutes. Enforced HERE rather than relying on `billing.py` happening to
        # return "unknown", which was an accidental cross-module invariant.
        "billing_drafted": coverage_determined and has_payer,
        "allergy_carried_forward": allergy_carried,
        # The TPA draft is a real artifact behind a real gate: `/intake/{id}/claim-draft` refuses
        # until the intake is CONFIRMED. Claiming its minutes for an unconfirmed intake claims work
        # that has not happened and cannot yet happen.
        "claim_draft_available": (confirmed and coverage_determined and has_payer
                                  and draft.billing.billed_to != "patient"),
    }


class StepSaving(BaseModel):
    model_config = ConfigDict(frozen=True)

    step: int
    name: str
    baseline_min: float
    with_copilot_min: float
    saved_min: float
    claimed: bool
    reason: str
    never_automatable: bool = False


class IntakeMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    intake_id: str
    steps: list[StepSaving] = Field(default_factory=list)
    baseline_total_min: float
    actual_total_min: float
    saved_min: float
    saved_pct: float
    # What this intake COULD have saved if every automatable stage had completed. The gap between
    # this and `saved_min` is the honest measure of where the system fell short.
    potential_saved_min: float

    @property
    def forfeited_min(self) -> float:
        return round(self.potential_saved_min - self.saved_min, 2)


def _saving_for(step: BaselineStep, conditions: dict[str, bool]) -> StepSaving:
    # The structural guarantee, checked FIRST and before any arithmetic: a never-automatable step
    # returns zero whatever the YAML says about its with_copilot time.
    if step.never_automatable:
        return StepSaving(
            step=step.step, name=step.name, baseline_min=step.baseline_midpoint,
            with_copilot_min=step.baseline_midpoint, saved_min=0.0, claimed=False,
            never_automatable=True,
            reason="never automatable — identity is verified in person by staff")

    if not step.automatable:
        return StepSaving(
            step=step.step, name=step.name, baseline_min=step.baseline_midpoint,
            with_copilot_min=step.baseline_midpoint, saved_min=0.0, claimed=False,
            reason="not automatable by this system")

    met = conditions.get(step.claim_condition, False)
    if not met:
        return StepSaving(
            step=step.step, name=step.name, baseline_min=step.baseline_midpoint,
            with_copilot_min=step.baseline_midpoint, saved_min=0.0, claimed=False,
            reason=f"not claimed: {step.claim_condition} did not hold for this intake")

    return StepSaving(
        step=step.step, name=step.name, baseline_min=step.baseline_midpoint,
        with_copilot_min=step.with_copilot_min,
        saved_min=round(step.baseline_midpoint - step.with_copilot_min, 2),
        claimed=True, reason=f"claimed: {step.claim_condition}")


def measure(draft: "IntakeDraft", *, confirmed: bool = False,
            table: Optional[TimeBaseline] = None) -> IntakeMetrics:
    """Minutes saved on ONE intake, counting only stages that actually completed.

    ``confirmed`` is whether a human has committed this intake. It gates the TPA claim-draft
    saving, because that artifact does not exist until they have.
    """
    table = table or baseline()
    conditions = _conditions_met(draft, confirmed=confirmed)
    steps = [_saving_for(s, conditions) for s in table.steps]

    baseline_total = round(sum(s.baseline_min for s in steps), 2)
    saved = round(sum(s.saved_min for s in steps), 2)
    potential = round(sum(s.max_saving for s in table.steps), 2)
    return IntakeMetrics(
        intake_id=draft.intake_id,
        steps=steps,
        baseline_total_min=baseline_total,
        actual_total_min=round(baseline_total - saved, 2),
        saved_min=saved,
        saved_pct=round(100 * saved / baseline_total, 1) if baseline_total else 0.0,
        potential_saved_min=potential,
    )


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    intakes: int
    baseline_total_min: float
    saved_min: float
    saved_pct: float
    mean_saved_min: float
    patients_per_morning: int
    # mean × patients_per_morning. A projection from a measured mean, labelled as one.
    projected_morning_hours_saved: float
    per_step: list[dict] = Field(default_factory=list)


def aggregate(metrics: list[IntakeMetrics],
              *, table: Optional[TimeBaseline] = None) -> AggregateMetrics:
    """Roll up measured intakes. The morning figure is a projection and is named as one."""
    table = table or baseline()
    if not metrics:
        return AggregateMetrics(
            intakes=0, baseline_total_min=0.0, saved_min=0.0, saved_pct=0.0, mean_saved_min=0.0,
            patients_per_morning=table.patients_per_morning,
            projected_morning_hours_saved=0.0)

    baseline_total = round(sum(m.baseline_total_min for m in metrics), 2)
    saved = round(sum(m.saved_min for m in metrics), 2)
    mean = round(saved / len(metrics), 2)

    by_step: dict[int, dict] = {}
    for m in metrics:
        for s in m.steps:
            row = by_step.setdefault(s.step, {
                "step": s.step, "name": s.name, "baseline_min": s.baseline_min,
                "saved_min": 0.0, "claimed_count": 0,
                "never_automatable": s.never_automatable})
            row["saved_min"] = round(row["saved_min"] + s.saved_min, 2)
            row["claimed_count"] += int(s.claimed)

    return AggregateMetrics(
        intakes=len(metrics),
        baseline_total_min=baseline_total,
        saved_min=saved,
        saved_pct=round(100 * saved / baseline_total, 1) if baseline_total else 0.0,
        mean_saved_min=mean,
        patients_per_morning=table.patients_per_morning,
        projected_morning_hours_saved=round(mean * table.patients_per_morning / 60, 1),
        per_step=[by_step[k] for k in sorted(by_step)],
    )
