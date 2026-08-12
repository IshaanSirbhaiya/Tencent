"""Questionnaire pre-fill — the administrative re-entry, and nothing else.

The problem statement complains that a patient re-keys the same ten administrative facts the clinic
already holds, at every visit. That is what this module removes. It does **not** remove the
questionnaire: medical history, family history, lifestyle, pain, sexual history, pregnancy,
immunisation, the declaration and the signature are all left for the patient, always.

**The scope line, stated once.** A field may be pre-filled only if it is
(a) administrative identity/contact data from the *selected* registry row,
(b) the appointment location or screening type, both grounded in the referring document or the
    coverage rule, or
(c) drug allergy — offered as a value the patient is asked to **confirm**, never as a settled
    clinical fact.

**Three layers enforce that, because a single flag is fail-open.** A field is clinical if it says
so *or* if it lives in a clinical SECTION (``CLINICAL_SECTIONS``) — so deleting ``clinical: true``
from a medical-history field does not quietly make it pre-fillable. ``drug_allergies`` is the one
named exception and the loader requires it to carry ``requires_confirmation``. And
``_resolve_source`` refuses a clinical field at resolve time regardless of what the schema says.

**Nothing is pre-filled until a human has selected a patient — globally, not per source.** The gate
is a ``ConfirmationState``, which cannot carry a ``selected_patient_id`` unless a named staff
member attested to an in-person identity check. That is a real constraint, not a naming convention:
there is no way to construct the argument this function requires without the attestation having
happened (CLAUDE.md §Hard constraints 1 and 4).

Every pre-filled value carries the ``SourcePointer`` it came from, so the patient (and staff) can
see *why* a box is already filled.
"""
from __future__ import annotations

import functools
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import GroundedValue, SourcePointer

from .models import (ConfirmationState, CoverageDetermination, IntakeRecord, PatientRecord,
                     PrefilledField, PrefilledQuestionnaire, PrefillStatus, QuestionnaireKind)
from .paths import config_dir
from .rules import CoverageRules, rules

PATIENT_MUST_ANSWER = "patient_must_answer"
DERIVED = "derived"

# Sections whose every field is clinical self-report or a consent act. Membership here is enough:
# it does not depend on the per-field `clinical` flag being remembered, which is what makes the
# protection fail-CLOSED. Adding a new section means deciding, once, which side of the line it is
# on — rather than re-deciding per field and eventually forgetting.
CLINICAL_SECTIONS = frozenset({
    "medical_history", "family_history", "lifestyle", "sexual_history", "gynaecological",
    "acknowledgement",
})

# The single permitted exception, named explicitly rather than expressed as a flag anyone can set.
# It is the "pharmacy re-asks about allergies" step in the problem statement: the registry holds
# the answer the patient gave last time, and it is offered back for confirmation, never asserted.
CLINICAL_PREFILL_EXCEPTIONS = frozenset({"drug_allergies"})

_VALID_SEXES = frozenset({"Male", "Female"})

# Field types whose value must be one of the declared options. A pre-filled value outside the set
# is not merely unusable by the widget — it is a WRONG ANSWER carrying a valid citation, which is
# the most dangerous shape a value can have here.
CHOICE_TYPES = frozenset({"dropdown", "radio", "checkbox_group", "boolean"})


class QuestionnaireError(RuntimeError):
    """The questionnaire schema is missing, malformed, or internally inconsistent."""


_STRICT = ConfigDict(frozen=True, extra="forbid")


class FieldOption(BaseModel):
    """One option of a checkbox group. ``sex`` and ``requires`` are the gating rules."""

    model_config = _STRICT

    value: str
    sex: Optional[str] = None                    # Male | Female — shown only for that gender
    requires: Optional[dict] = None              # {"option": "<another option's value>"}

    @model_validator(mode="after")
    def _gates_are_well_formed(self) -> "FieldOption":
        # A typo in either gate fails silently and clinically: `sex: Femlae` hides the option from
        # everyone, and `optoin:` shows a dependent option unconditionally.
        if self.sex is not None and self.sex not in _VALID_SEXES:
            raise ValueError(
                f"option {self.value!r}: sex={self.sex!r} is not one of {sorted(_VALID_SEXES)}")
        if self.requires is not None:
            unknown = set(self.requires) - {"option"}
            if unknown or "option" not in self.requires:
                raise ValueError(
                    f"option {self.value!r}: `requires` must be exactly {{option: <value>}}, got "
                    f"{self.requires!r}")
        return self


class Condition(BaseModel):
    model_config = _STRICT

    field: str
    equals: Optional[str] = None
    not_equals: Optional[str] = None
    contains: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_comparator(self) -> "Condition":
        set_ones = [n for n in ("equals", "not_equals", "contains") if getattr(self, n) is not None]
        if len(set_ones) != 1:
            raise ValueError(
                f"condition on {self.field!r} must set exactly one comparator, got {set_ones} — "
                f"none means the gate never fires, several have no defined meaning")
        return self

    def holds_for(self, value: Any) -> bool:
        if self.equals is not None:
            return str(value) == self.equals
        if self.not_equals is not None:
            return str(value) != self.not_equals
        if isinstance(value, (list, tuple, set)):
            return self.contains in {str(v) for v in value}
        return self.contains in str(value)


class QuestionnaireField(BaseModel):
    model_config = _STRICT

    id: str
    csv_column: str
    label: str
    section: str
    type: str
    required: bool = False
    clinical: bool = False
    requires_confirmation: bool = False
    # Tuples, not lists: `questionnaire()` is an lru_cache, so a mutable list is a process-wide
    # shared object anyone can edit — `questionnaire(GENERAL_HEALTH).fields.pop()` would silently
    # shrink the form for the rest of the run. `registry()` returns a tuple for the same reason.
    options: tuple[Any, ...] = ()
    conditional_on: Optional[Condition] = None
    prefill_source: str = PATIENT_MUST_ANSWER

    def accepts(self, value: Any) -> bool:
        """Whether ``value`` is a legal answer to this field.

        Free-text fields accept anything; choice fields accept only their own options. This is what
        stops a document's appointment location — the insurer's own facility, on chit 2 — being
        pre-filled into a dropdown of Parkway Shenton clinics.
        """
        if self.type not in CHOICE_TYPES or not self.options:
            return True
        allowed = set(self.option_values())
        candidates = value if isinstance(value, (list, tuple, set)) else [value]
        return all(str(v) in allowed for v in candidates)

    @property
    def is_clinical(self) -> bool:
        """Clinical by declaration OR by section. The section half is what makes this fail-closed:
        removing ``clinical: true`` from a medical-history field changes nothing."""
        return self.clinical or self.section in CLINICAL_SECTIONS

    @model_validator(mode="after")
    def _clinical_fields_are_never_prefilled(self) -> "QuestionnaireField":
        """The scope line, enforced at load rather than trusted.

        A clinical field with a ``prefill_source`` is not a preference to be honoured later — it is
        a schema bug that would fabricate clinical data about a real person, and it must fail
        loudly the moment the file is read.
        """
        prefilled = self.prefill_source != PATIENT_MUST_ANSWER
        if self.id in CLINICAL_PREFILL_EXCEPTIONS:
            if prefilled and not self.requires_confirmation:
                raise ValueError(
                    f"questionnaire field {self.id!r} is the one permitted clinical pre-fill and "
                    f"must set requires_confirmation: it is offered for the patient to confirm, "
                    f"never asserted as current")
            return self
        if self.is_clinical and prefilled:
            raise ValueError(
                f"questionnaire field {self.id!r} is clinical (section={self.section!r}) but has "
                f"prefill_source={self.prefill_source!r} — clinical self-report is never pre-filled "
                f"(PLAN.md §6.4)")
        if self.requires_confirmation and self.id not in CLINICAL_PREFILL_EXCEPTIONS:
            raise ValueError(
                f"questionnaire field {self.id!r} sets requires_confirmation but is not one of the "
                f"named clinical pre-fill exceptions {sorted(CLINICAL_PREFILL_EXCEPTIONS)}")
        return self

    def option_values(self) -> list[str]:
        return [o["value"] if isinstance(o, dict) else str(o) for o in self.options]

    def options_for(self, sex: Optional[str], selected: tuple[str, ...] = ()) -> list[str]:
        """The options actually shown, after gender and dependency gating.

        A male patient is never shown "Gestational Diabetes"; "Hysterectomy" appears only once
        "Prior Surgery / Endoscopy" is ticked. Presenting an inapplicable option is not merely
        untidy — on a clinical form it invites a wrong answer.

        With an UNKNOWN sex every sex-specific option is hidden, not shown. Showing both sets means
        a form offering Prostate Disease and Gestational Diabetes side by side, which is worse than
        asking for the missing answer first.
        """
        shown: list[str] = []
        for raw in self.options:
            if not isinstance(raw, dict):
                shown.append(str(raw))
                continue
            option = FieldOption.model_validate(raw)
            if option.sex is not None and option.sex != sex:
                continue
            if option.requires is not None and option.requires["option"] not in selected:
                continue
            shown.append(option.value)
        return shown


class Questionnaire(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    kind: QuestionnaireKind
    label: str
    fields: tuple[QuestionnaireField, ...]

    def field(self, field_id: str) -> Optional[QuestionnaireField]:
        return next((f for f in self.fields if f.id == field_id), None)

    def csv_columns(self) -> set[str]:
        return {f.csv_column for f in self.fields}


def load_questionnaire(kind: QuestionnaireKind, path=None) -> Questionnaire:
    """Parse and validate one questionnaire schema."""
    schema_path = path or (config_dir() / "questionnaires" / f"{kind.value}.yaml")
    if not schema_path.exists():
        raise QuestionnaireError(f"questionnaire schema not found at {schema_path}")
    data = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise QuestionnaireError(f"{schema_path} did not parse to a mapping")
    questionnaire_ = Questionnaire.model_validate(data)
    if questionnaire_.kind is not kind:
        # A copy-paste when adding a third form would otherwise render the WRONG questionnaire
        # under the right name — `prefill` labels its result with the requested kind.
        raise QuestionnaireError(
            f"{schema_path}: declares kind={questionnaire_.kind.value!r} but was loaded as "
            f"{kind.value!r}")

    ids = [f.id for f in questionnaire_.fields]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise QuestionnaireError(f"{schema_path}: duplicate field id(s) {dupes}")
    columns = [f.csv_column for f in questionnaire_.fields]
    if len(columns) != len(set(columns)):
        dupes = sorted({c for c in columns if columns.count(c) > 1})
        raise QuestionnaireError(f"{schema_path}: duplicate csv_column(s) {dupes}")

    known = set(ids)
    from .models import PatientRecord as _PatientRecord, _known_field_paths

    registry_fields = {n for n, f in _PatientRecord.model_fields.items() if n != "patient_id"}
    intake_paths = _known_field_paths()
    for f in questionnaire_.fields:
        # A choice field with no options renders an empty required control the patient cannot
        # answer, and no CSV-column test can see it.
        if f.type in CHOICE_TYPES and f.required and not f.options:
            raise QuestionnaireError(
                f"{schema_path}: field {f.id!r} is a required {f.type} with no options — it would "
                f"render as a control nobody can answer")
        # A typo AFTER a valid prefix is the dangerous case: it degrades to "the patient answers
        # this", which looks exactly like a working form while silently dropping the automation.
        source = f.prefill_source
        if source.startswith("registry.") and source.split(".", 1)[1] not in registry_fields:
            raise QuestionnaireError(
                f"{schema_path}: field {f.id!r} sources {source!r}, but PatientRecord has no such "
                f"grounded field (known: {sorted(registry_fields)})")
        if source.startswith("intake.") and source.split(".", 1)[1] not in intake_paths:
            raise QuestionnaireError(
                f"{schema_path}: field {f.id!r} sources {source!r}, which is not a field path of "
                f"IntakeRecord")
        # A conditional referring to a field that does not exist would silently never fire, so the
        # dependent field would render unconditionally — the opposite of the intent.
        if f.conditional_on is not None:
            if f.conditional_on.field not in known:
                raise QuestionnaireError(
                    f"{schema_path}: field {f.id!r} is conditional on unknown field "
                    f"{f.conditional_on.field!r}")
            parent = questionnaire_.field(f.conditional_on.field)
            wanted = (f.conditional_on.equals or f.conditional_on.not_equals
                      or f.conditional_on.contains)
            parent_options = parent.option_values()
            # A comparator value that is not one of the parent's options can never match, so the
            # dependent field would be hidden (or shown) forever with nothing to indicate why.
            if parent_options and wanted not in parent_options:
                raise QuestionnaireError(
                    f"{schema_path}: field {f.id!r} is conditional on {parent.id!r}={wanted!r}, "
                    f"which is not one of its options {parent_options}")
        # Validate every option eagerly rather than lazily at render time.
        for raw in f.options:
            if isinstance(raw, dict):
                option = FieldOption.model_validate(raw)
                if option.requires is not None:
                    if option.requires["option"] not in f.option_values():
                        raise QuestionnaireError(
                            f"{schema_path}: option {option.value!r} requires "
                            f"{option.requires['option']!r}, which is not an option of {f.id!r}")
    return questionnaire_


@functools.lru_cache(maxsize=4)
def questionnaire(kind: QuestionnaireKind) -> Questionnaire:
    """Process-wide cached schema."""
    return load_questionnaire(kind)


# --- derivations ------------------------------------------------------------------

_SEX_TO_GENDER = {"M": "Male", "F": "Female"}

# The registry stores an internal IdType (`nric_fin`); the form's radio reads "NRIC/FIN". Emitting
# the raw enum pre-fills a value that matches no option, so the radio silently renders unselected
# and the patient has to answer a question we already knew the answer to.
_ID_TYPE_TO_OPTION = {"nric_fin": "NRIC/FIN", "passport": "Passport"}


def _derived_pointer(base: SourcePointer, derivation: str) -> SourcePointer:
    return SourcePointer(source_id=base.source_id, kind="derived", derivation=derivation,
                         derived_from=[base])


def _gender_from(patient: PatientRecord) -> Optional[GroundedValue]:
    """Registry ``M``/``F`` → the form's ``Male``/``Female``.

    An unrecognised code returns ``None`` rather than defaulting: the questionnaire gates
    female-only and male-only items on this answer, so a guess here hides or shows clinical
    questions for the wrong person.
    """
    raw = str(patient.sex.value).strip().upper()
    gender = _SEX_TO_GENDER.get(raw)
    if gender is None:
        return None
    return GroundedValue(
        value=gender,
        pointer=_derived_pointer(patient.sex.pointer, f"registry sex {raw!r} -> {gender!r}"),
        confidence=patient.sex.confidence)


def _id_type_from(patient: PatientRecord) -> Optional[GroundedValue]:
    """Registry ``IdType`` → the form's radio option.

    ``UNKNOWN`` returns ``None``: the registry loader uses it precisely when an identifier is not
    classifiable, and pre-ticking either radio for such a patient would assert an ID type nobody
    established.
    """
    raw = str(patient.id_type.value).strip().lower()
    option = _ID_TYPE_TO_OPTION.get(raw)
    if option is None:
        return None
    return GroundedValue(
        value=option,
        pointer=_derived_pointer(patient.id_type.pointer, f"id type {raw!r} -> {option!r}"),
        confidence=patient.id_type.confidence)


def _screening_types_from(coverage: Optional[CoverageDetermination],
                          table: CoverageRules) -> Optional[GroundedValue]:
    """The occupational screening type, read from the coverage rule that fired.

    The value comes from the rule's ``screening_type`` column, not from an assumption in code. An
    earlier revision returned "Pre/Re Employment" for *any* occupational rule, which would tick the
    wrong box for a hazard-specific referral. A rule that does not state one leaves the checkbox
    for the patient.

    Returned as a LIST: the field is a checkbox group, and a bare string would either be rejected
    by the widget or iterated character by character.
    """
    if coverage is None or not coverage.matched_rule_id:
        return None
    if coverage.questionnaire is not QuestionnaireKind.OCCUPATIONAL_HEALTH:
        return None
    rule = next((p for p in table.packages if p.id == coverage.matched_rule_id), None)
    if rule is None or not rule.screening_type:
        return None
    return GroundedValue(
        value=[rule.screening_type],
        pointer=SourcePointer(
            source_id="coverage_rules.yaml", kind="table_cell", table="packages",
            column="screening_type", row_id=rule.id),
        confidence=1.0)


def _blank(value: Any) -> bool:
    """An empty registry cell is ABSENT, not an answer.

    ``registry.py`` normalises a missing cell to ``""``. Emitting that as pre-filled puts an empty
    box on the form that the patient reads as already answered, asks them to *confirm* a blank
    allergy record, and inflates the pre-filled count that feeds the time-saved metric.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _intake_value(record: Optional[IntakeRecord], path: str) -> Optional[GroundedValue]:
    if record is None:
        return None
    found = record.grounded_fields().get(path)
    return None if found is None or _blank(found.value) else found


def _registry_value(patient: Optional[PatientRecord], field: str) -> Optional[GroundedValue]:
    if patient is None:
        return None
    found = getattr(patient, field, None)
    if not isinstance(found, GroundedValue) or _blank(found.value):
        return None
    return found


def _resolve_source(field: QuestionnaireField,
                    patient: Optional[PatientRecord],
                    record: Optional[IntakeRecord],
                    coverage: Optional[CoverageDetermination],
                    table: Optional[CoverageRules] = None) -> Optional[GroundedValue]:
    """The value for one field, or ``None`` if the patient must answer it."""
    # Belt and braces with the load-time validator: a clinical field is never pre-filled, whatever
    # its declared source says. The named exception still has to come from the registry.
    if field.prefill_source == PATIENT_MUST_ANSWER:
        return None
    if field.is_clinical and field.id not in CLINICAL_PREFILL_EXCEPTIONS:
        return None

    source = field.prefill_source
    if source.startswith("registry."):
        return _registry_value(patient, source.split(".", 1)[1])
    if source.startswith("intake."):
        return _intake_value(record, source.split(".", 1)[1])
    if source == DERIVED:
        if field.id == "gender":
            return _gender_from(patient) if patient is not None else None
        if field.id == "id_type":
            return _id_type_from(patient) if patient is not None else None
        if field.id == "screening_types":
            return _screening_types_from(coverage, table or rules())
        raise QuestionnaireError(
            f"field {field.id!r} declares prefill_source 'derived' but no derivation is "
            f"implemented for it — a silent None here would look like 'not on file'")
    raise QuestionnaireError(f"field {field.id!r} has unknown prefill_source {source!r}")


def _applicability(field: QuestionnaireField,
                   answers: dict[str, Any],
                   inapplicable: set[str]) -> bool:
    """Whether this field applies at all, given what is already known.

    Inapplicability CASCADES: if `pregnant` does not apply to this patient then neither does
    `weeks_pregnant`. Without that, a male patient's form shows "No. of weeks" as a question he
    must answer — which is not merely untidy, it is a clinical form asking a question that has no
    meaning for the person in front of it.

    A condition whose parent answer is simply not known yet leaves the field applicable: the
    patient will answer the parent, and the form re-evaluates.
    """
    condition = field.conditional_on
    if condition is None:
        return True
    if condition.field in inapplicable:
        return False
    if condition.field not in answers:
        return True
    return condition.holds_for(answers[condition.field])


def prefill(kind: QuestionnaireKind,
            *,
            confirmation: Optional[ConfirmationState] = None,
            selected_patient: Optional[PatientRecord] = None,
            record: Optional[IntakeRecord] = None,
            coverage: Optional[CoverageDetermination] = None,
            table: Optional[CoverageRules] = None) -> PrefilledQuestionnaire:
    """Build the pre-filled questionnaire for a patient a human has selected.

    ``confirmation`` is the gate and it is not decorative: a ``ConfirmationState`` cannot hold a
    ``selected_patient_id`` unless a named staff member recorded an in-person identity check, so
    requiring one here means the attestation provably happened. ``selected_patient`` must be that
    same patient.

    With no confirmation, **every** field comes back ``patient_must_answer`` — including the ones
    sourced from the document rather than the registry. A half-filled form before identity is
    settled is still a form filled on behalf of someone nobody has identified.
    """
    if kind is QuestionnaireKind.NONE:
        return PrefilledQuestionnaire(kind=kind, fields=())

    schema = questionnaire(kind)

    gated_patient: Optional[PatientRecord] = None
    if selected_patient is not None or confirmation is not None:
        if confirmation is None or not confirmation.selected_patient_id:
            raise QuestionnaireError(
                "prefill REFUSED: a selected patient requires a ConfirmationState carrying the "
                "staff selection — identity is verified in person before anything is pre-filled "
                "(CLAUDE.md §Hard constraints 1)")
        if selected_patient is None:
            raise QuestionnaireError(
                "prefill REFUSED: confirmation names a selected patient but no PatientRecord was "
                "supplied to fill from")
        if confirmation.selected_patient_id != selected_patient.patient_id:
            raise QuestionnaireError(
                f"prefill REFUSED: staff selected {confirmation.selected_patient_id!r} but the "
                f"record supplied is {selected_patient.patient_id!r} — pre-filling one patient's "
                f"form from another's registry row")
        gated_patient = selected_patient

    # No confirmed selection ⇒ nothing is pre-filled at all, from any source.
    gated_record = record if gated_patient is not None else None
    gated_coverage = coverage if gated_patient is not None else None

    gender = _gender_from(gated_patient) if gated_patient is not None else None
    sex = str(gender.value) if gender is not None else None

    answers: dict[str, Any] = {}
    if sex is not None:
        answers["gender"] = sex

    out: list[PrefilledField] = []
    inapplicable: set[str] = set()
    for field in schema.fields:
        if not _applicability(field, answers, inapplicable):
            # NOT_APPLICABLE is a different state from "you must answer this", and the form must
            # not conflate them: one is a question that does not exist for this patient.
            inapplicable.add(field.id)
            out.append(PrefilledField(field_id=field.id, label=field.label,
                                      status=PrefillStatus.NOT_APPLICABLE))
            continue

        value = _resolve_source(field, gated_patient, gated_record, gated_coverage, table)
        # A value the field cannot legally hold is refused rather than emitted. On chit 2 this is
        # what stops the insurer's own "Everwell Health Screening Centre" being pre-filled into a
        # dropdown of Parkway Shenton clinics — a wrong answer with a valid citation behind it,
        # which is worse than an empty box.
        if value is not None and not field.accepts(value.value):
            value = None
        if value is None:
            out.append(PrefilledField(field_id=field.id, label=field.label,
                                      status=PrefillStatus.PATIENT_MUST_ANSWER))
            continue
        answers[field.id] = value.value
        out.append(PrefilledField(
            field_id=field.id, label=field.label, value=value,
            status=PrefillStatus.PREFILLED,
            requires_confirmation=field.requires_confirmation))

    return PrefilledQuestionnaire(kind=kind, fields=tuple(out))
