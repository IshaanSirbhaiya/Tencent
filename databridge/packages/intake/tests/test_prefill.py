"""Questionnaire pre-fill tests.

The load-bearing one is `test_zero_clinical_fields_are_ever_prefilled`. Everything else here is
about coverage and correctness; that one is about the line the product must not cross.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib

import pytest

from databridge_intake.eligibility import determine_coverage
from databridge_intake.extract import extract_intake
from databridge_intake.gold import gold_harness
from databridge_intake.models import PrefillStatus, QuestionnaireKind
from databridge_intake.paths import fixtures_dir, source_data_dir
from databridge_intake.prefill import (QuestionnaireError, load_questionnaire, prefill,
                                       questionnaire)
from databridge_intake.registry import registry
from databridge_intake.resolve import resolve

STAFF = "staff:front_desk_01"
ATTESTED_AT = dt.datetime(2026, 8, 11, 9, 0, tzinfo=dt.timezone.utc)


def _confirmed(patient):
    """The gate a human passes through: an in-person attestation, then a selection.

    `ConfirmationState` refuses a `selected_patient_id` without an attestation, so building one is
    only possible if the check actually happened — which is what makes it a real gate rather than
    a parameter name."""
    from databridge_intake.models import ConfirmationState

    return (ConfirmationState(intake_id="t")
            .attest_identity_checked(STAFF, ATTESTED_AT)
            # A selection must name the reading it was made about (R-17). This unit test has no
            # document behind it — it exercises the questionnaire, not the pipeline — so there is
            # no real resolution to digest; derived per patient so two subjects never share one.
            .select_patient(patient.patient_id,
                            subject_fingerprint=hashlib.sha256(
                                patient.patient_id.encode("utf-8")).hexdigest()))


def _prefill(kind, patient=None, **kw):
    if patient is None:
        return prefill(kind, **kw)
    return prefill(kind, confirmation=_confirmed(patient), selected_patient=patient, **kw)

MOCK_CSV = {
    QuestionnaireKind.GENERAL_HEALTH: "general_health_questionnaire_mock_patients.csv",
    QuestionnaireKind.OCCUPATIONAL_HEALTH: "occupational_health_questionnaire_mock_patients.csv",
}
EXPECTED_FIELD_COUNT = {
    QuestionnaireKind.GENERAL_HEALTH: 41,
    QuestionnaireKind.OCCUPATIONAL_HEALTH: 27,
}


def _mock_columns(kind: QuestionnaireKind) -> list[str]:
    path = source_data_dir() / MOCK_CSV[kind]
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return next(csv.reader(fh))


@pytest.fixture(scope="module")
def built():
    """Extract → resolve → determine → prefill for the two fixtures PLAN.md names."""
    harness = gold_harness()
    out = {}
    for name in ("chit_02_everwell_voucher.txt", "chit_07_mol_authorisation_pee226.txt"):
        path = fixtures_dir() / "chits" / name
        record = extract_intake(path.read_text(encoding="utf-8"),
                                document_id=name, harness=harness)
        resolution = resolve(record.subject)
        coverage = determine_coverage(record, candidates=resolution.candidates)
        selected = resolution.candidates[0].record       # a human would click this
        out[name] = (record, coverage, selected,
                     _prefill(coverage.questionnaire, selected,
                              record=record, coverage=coverage))
    return out


# --- schema completeness (PLAN.md §5 Phase 3 "Done when") --------------------------------


@pytest.mark.parametrize("kind", list(EXPECTED_FIELD_COUNT))
def test_both_schemas_load_with_the_expected_field_count(kind):
    assert len(questionnaire(kind).fields) == EXPECTED_FIELD_COUNT[kind]


@pytest.mark.parametrize("kind", list(MOCK_CSV))
def test_every_mock_csv_column_maps_to_a_field(kind):
    """This is how we prove the schema is complete: a column in a real completed form with no
    field id means the questionnaire we render is missing a question the clinic actually asks."""
    unmapped = sorted(set(_mock_columns(kind)) - questionnaire(kind).csv_columns())
    assert unmapped == [], f"{kind.value}: unmapped mock-CSV column(s) {unmapped}"


@pytest.mark.parametrize("kind", list(MOCK_CSV))
def test_no_field_invents_a_column_the_form_does_not_have(kind):
    extra = sorted(questionnaire(kind).csv_columns() - set(_mock_columns(kind)))
    assert extra == [], f"{kind.value}: field(s) mapped to non-existent column(s) {extra}"


# --- THE SCOPE LINE ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(EXPECTED_FIELD_COUNT))
def test_zero_clinical_fields_are_ever_prefilled(kind):
    """For a fully resolved patient, every clinical field comes back `patient_must_answer`.

    Medical history, family history, lifestyle, pain, sexual history, pregnancy, immunisation, the
    declaration and the signature are clinical self-report and a consent act. Pre-filling any of
    them from another dataset would be fabricating clinical data about a real person — the exact
    failure this architecture exists to prevent (PLAN.md §6.4).
    """
    schema = questionnaire(kind)
    patient = registry()[0]
    result = _prefill(kind, patient)
    by_id = {f.field_id: f for f in result.fields}
    for field in schema.fields:
        if not field.clinical:
            continue
        assert by_id[field.id].status is not PrefillStatus.PREFILLED, field.id
        assert by_id[field.id].value is None, field.id


@pytest.mark.parametrize("kind", list(EXPECTED_FIELD_COUNT))
def test_a_clinical_field_with_a_prefill_source_is_refused_at_load(kind, tmp_path):
    """The scope line is enforced when the schema is READ, not only when it is used — so a typo in
    the YAML fails loudly instead of quietly leaking a clinical value into a form."""
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / f"{kind.value}.yaml").read_text(encoding="utf-8"))
    clinical = next(f for f in data["fields"] if f.get("clinical"))
    clinical["prefill_source"] = "registry.drug_allergy"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(Exception) as exc:
        load_questionnaire(kind, path)
    assert "clinical" in str(exc.value)


def test_nothing_is_prefilled_without_a_selected_patient():
    """Before a human has picked a candidate, the questionnaire must be blank — and the gate is
    GLOBAL, not per-source. Document-sourced fields must not sneak through either: a half-filled
    form before identity is settled is still a form filled for someone nobody has identified."""
    harness = gold_harness()
    path = fixtures_dir() / "chits" / "chit_02_everwell_voucher.txt"
    record = extract_intake(path.read_text(encoding="utf-8"),
                            document_id=path.name, harness=harness)
    result = prefill(QuestionnaireKind.GENERAL_HEALTH, record=record)
    assert result.prefilled_count == 0
    assert all(f.value is None for f in result.fields)


def test_a_patient_without_a_confirmation_is_refused():
    """The gate is a ConfirmationState, which cannot name a selected patient unless a staff member
    attested to an in-person check. Passing a bare candidate record is refused outright."""
    with pytest.raises(QuestionnaireError, match="requires a ConfirmationState"):
        prefill(QuestionnaireKind.GENERAL_HEALTH, selected_patient=registry()[0])


def test_a_confirmation_for_a_different_patient_is_refused():
    """The commonest way this would go wrong in a UI: the staff selection and the record handed to
    the renderer drift apart, and one patient's form fills from another's registry row."""
    a, b = registry()[0], registry()[1]
    with pytest.raises(QuestionnaireError, match="but the record supplied is"):
        prefill(QuestionnaireKind.GENERAL_HEALTH,
                confirmation=_confirmed(a), selected_patient=b)


def test_a_declaration_is_never_prefilled(built):
    for _, _, _, result in built.values():
        for field in result.fields:
            if "declaration" in field.field_id or field.field_id == "date_signed":
                assert field.status is not PrefillStatus.PREFILLED


# --- what IS pre-filled -----------------------------------------------------------------


def test_chit_2_routes_to_general_health_with_at_least_ten_step_one_fields(built):
    """PLAN.md's coverage metric for this phase."""
    _, coverage, _, result = built["chit_02_everwell_voucher.txt"]
    assert coverage.questionnaire is QuestionnaireKind.GENERAL_HEALTH
    assert result.kind is QuestionnaireKind.GENERAL_HEALTH
    assert result.prefilled_count >= 10


def test_chit_7_routes_to_occupational_health_with_the_screening_type(built):
    _, coverage, _, result = built["chit_07_mol_authorisation_pee226.txt"]
    assert coverage.questionnaire is QuestionnaireKind.OCCUPATIONAL_HEALTH
    screening = next(f for f in result.fields if f.field_id == "screening_types")
    assert screening.status is PrefillStatus.PREFILLED
    # A LIST, because the field is a multi-select checkbox group. A bare string is either rejected
    # by the widget or iterated character by character.
    assert screening.value.value == ["Pre/Re Employment"]
    # …and it comes from the rule's own `screening_type` cell, not from an assumption in code, so
    # the citation resolves to text that actually contains the value.
    pointer = screening.value.pointer
    assert pointer.column == "screening_type" and pointer.row_id == "rule_mol_pee226"


def test_a_package_that_states_no_screening_type_leaves_the_box_for_the_patient(built):
    """The guess this replaced: an earlier revision returned "Pre/Re Employment" for ANY
    occupational rule, which would tick the wrong box for a hazard-specific referral."""
    import yaml

    from databridge_intake.paths import config_dir
    from databridge_intake.rules import load_rules

    record, coverage, selected, _ = built["chit_07_mol_authorisation_pee226.txt"]
    data = yaml.safe_load((config_dir() / "coverage_rules.yaml").read_text(encoding="utf-8"))
    for package in data["packages"]:
        package.pop("screening_type", None)
    path = fixtures_dir().parent / "_no_screening_type.yaml"
    try:
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        result = _prefill(QuestionnaireKind.OCCUPATIONAL_HEALTH, selected,
                          record=record, coverage=coverage, table=load_rules(path))
        screening = next(f for f in result.fields if f.field_id == "screening_types")
        assert screening.status is PrefillStatus.PATIENT_MUST_ANSWER
    finally:
        path.unlink(missing_ok=True)


def test_every_prefilled_value_carries_a_resolvable_pointer(built):
    """The anti-fabrication proof, applied to pre-fill: a box that arrives already filled must be
    able to say where its content came from — a registry cell or a document span."""
    for name, (_, _, _, result) in built.items():
        for field in result.fields:
            if field.status is not PrefillStatus.PREFILLED:
                continue
            pointer = field.value.pointer
            assert pointer is not None, f"{name}:{field.field_id}"
            assert pointer.kind in {"table_cell", "doc_span", "derived"}, field.field_id
            assert pointer.source_id


def test_identity_fields_come_from_the_selected_registry_row(built):
    _, _, selected, result = built["chit_02_everwell_voucher.txt"]
    by_id = {f.field_id: f for f in result.fields}
    assert by_id["name"].value.value == selected.full_name.value
    assert by_id["id_number"].value.value == selected.id_number.value
    assert by_id["date_of_birth"].value.value == selected.date_of_birth.value


def test_a_location_outside_the_clinics_option_set_is_refused(built):
    """Chit 2 names "Everwell Health Screening Centre" — the INSURER's own facility, not a Parkway
    Shenton site. Pre-filling it would put a venue the clinic does not operate into the form with a
    perfectly valid document citation behind it: a wrong answer that looks sourced."""
    record, _, _, result = built["chit_02_everwell_voucher.txt"]
    printed = record.authorisation.appointment_location.value
    schema_field = questionnaire(QuestionnaireKind.GENERAL_HEALTH).field("health_screening_location")
    assert printed not in schema_field.option_values()
    location = next(f for f in result.fields if f.field_id == "health_screening_location")
    assert location.status is PrefillStatus.PATIENT_MUST_ANSWER


def test_a_location_inside_the_option_set_is_prefilled_from_the_document():
    """The other half: when the document DOES name one of the clinic's own locations, it is
    pre-filled and cites the document span it was read from."""
    from databridge_core import GroundedValue, SourcePointer

    harness = gold_harness()
    path = fixtures_dir() / "chits" / "chit_02_everwell_voucher.txt"
    record = extract_intake(path.read_text(encoding="utf-8"),
                            document_id=path.name, harness=harness)
    ptr = SourcePointer(source_id=path.name, kind="doc_span", document_id=path.name,
                        char_start=0, char_end=5)
    real_clinic = "Shenton Medical Group (Toa Payoh)"
    patched = record.model_copy(update={
        "authorisation": record.authorisation.model_copy(update={
            "appointment_location": GroundedValue(value=real_clinic, pointer=ptr, confidence=1.0)})})
    patient = registry()[0]
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, patient, record=patched)
    location = next(f for f in result.fields if f.field_id == "health_screening_location")
    assert location.status is PrefillStatus.PREFILLED
    assert location.value.value == real_clinic
    assert location.value.pointer.kind == "doc_span"


def test_drug_allergy_is_prefilled_but_flagged_for_confirmation(built):
    """The one clinical-adjacent pre-fill, and it is explicitly a value to confirm — it removes the
    "pharmacy re-asks about allergies" step without asserting the answer is still current."""
    _, _, selected, result = built["chit_02_everwell_voucher.txt"]
    field = next(f for f in result.fields if f.field_id == "drug_allergies")
    assert field.status is PrefillStatus.PREFILLED
    assert field.requires_confirmation is True
    assert field.value.value == selected.drug_allergy.value


def test_no_other_field_is_marked_requires_confirmation(built):
    for _, _, _, result in built.values():
        flagged = [f.field_id for f in result.fields if f.requires_confirmation]
        assert flagged in ([], ["drug_allergies"]), flagged


# --- derivations ------------------------------------------------------------------------


def test_gender_is_derived_from_registry_sex_with_the_derivation_recorded(built):
    _, _, selected, result = built["chit_02_everwell_voucher.txt"]
    gender = next(f for f in result.fields if f.field_id == "gender")
    assert gender.value.value in {"Male", "Female"}
    assert gender.value.pointer.kind == "derived"
    assert gender.value.pointer.derived_from[0] == selected.sex.pointer


def test_id_type_is_derived_to_the_forms_own_option_wording(built):
    """The registry stores `nric_fin`; the radio reads "NRIC/FIN". Emitting the raw enum pre-fills
    a value matching no option, so the field silently renders unanswered."""
    _, _, _, result = built["chit_02_everwell_voucher.txt"]
    id_type = next(f for f in result.fields if f.field_id == "id_type")
    schema_field = questionnaire(QuestionnaireKind.GENERAL_HEALTH).field("id_type")
    assert id_type.value.value in schema_field.option_values()


def test_an_unclassifiable_id_type_is_left_for_the_patient():
    """`IdType.UNKNOWN` exists so a malformed identifier is not asserted as a passport. Pre-ticking
    either radio for such a patient would assert an ID type nobody established."""
    from databridge_intake.models import IdType

    patient = registry()[0]
    unknown = patient.model_copy(update={
        "id_type": patient.id_type.model_copy(update={"value": IdType.UNKNOWN.value})})
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, unknown)
    id_type = next(f for f in result.fields if f.field_id == "id_type")
    assert id_type.status is PrefillStatus.PATIENT_MUST_ANSWER


# --- conditional logic ------------------------------------------------------------------


def _patient_of_sex(sex: str):
    return next(r for r in registry() if str(r.sex.value).upper() == sex)


def test_female_only_fields_are_not_applicable_for_a_male_patient():
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, _patient_of_sex("M"))
    by_id = {f.field_id: f for f in result.fields}
    for field_id in ("pregnant", "menstrual_cycle", "contraception_use"):
        assert by_id[field_id].status is PrefillStatus.NOT_APPLICABLE, field_id


def test_female_only_fields_are_askable_for_a_female_patient():
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, _patient_of_sex("F"))
    by_id = {f.field_id: f for f in result.fields}
    for field_id in ("pregnant", "menstrual_cycle", "contraception_use"):
        assert by_id[field_id].status is PrefillStatus.PATIENT_MUST_ANSWER, field_id


def test_weeks_pregnant_is_gated_on_pregnancy_not_on_gender():
    schema = questionnaire(QuestionnaireKind.GENERAL_HEALTH)
    assert schema.field("weeks_pregnant").conditional_on.field == "pregnant"
    assert schema.field("pregnant").conditional_on.field == "gender"


def test_sex_specific_medical_history_options_gate_correctly():
    schema = questionnaire(QuestionnaireKind.GENERAL_HEALTH)
    conditions = schema.field("medical_history_conditions")
    male = conditions.options_for("Male")
    female = conditions.options_for("Female")
    assert "Prostate Disease" in male and "Prostate Disease" not in female
    assert "Gestational Diabetes" in female and "Gestational Diabetes" not in male
    assert "Polycystic Ovary Syndrome (PCOS)" in female
    assert "Asthma" in male and "Asthma" in female        # ungated items appear for both


def test_hysterectomy_appears_only_after_prior_surgery_is_ticked():
    conditions = questionnaire(QuestionnaireKind.GENERAL_HEALTH).field(
        "medical_history_conditions")
    hysterectomy = "Hysterectomy (Womb Removal Surgery)"
    assert hysterectomy not in conditions.options_for("Female")
    assert hysterectomy in conditions.options_for(
        "Female", selected=("Prior Surgery / Endoscopy",))
    # …and never for a male patient, whatever else is ticked
    assert hysterectomy not in conditions.options_for(
        "Male", selected=("Prior Surgery / Endoscopy",))


def test_prior_surgery_details_is_gated_on_the_condition_being_ticked():
    schema = questionnaire(QuestionnaireKind.GENERAL_HEALTH)
    condition = schema.field("prior_surgery_details").conditional_on
    assert condition.field == "medical_history_conditions"
    assert condition.contains == "Prior Surgery / Endoscopy"


# --- schema hardening -------------------------------------------------------------------


def test_a_conditional_on_an_unknown_field_is_refused(tmp_path):
    """A conditional naming a field that does not exist never fires, so the dependent field would
    render unconditionally — the opposite of what the author meant."""
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    data["fields"][-1]["conditional_on"] = {"field": "no_such_field", "equals": "Yes"}
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(QuestionnaireError, match="conditional on unknown field"):
        load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)


def test_a_duplicate_field_id_is_refused(tmp_path):
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    data["fields"].append(dict(data["fields"][0]))
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(QuestionnaireError, match="duplicate field id"):
        load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)


def test_an_unknown_prefill_source_fails_loudly_rather_than_silently_blank(tmp_path):
    """A typo'd source must not degrade to "the patient answers this" — that would look exactly
    like a working form while quietly dropping the field's automation."""
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    field = next(f for f in data["fields"] if f["id"] == "name")
    field["prefill_source"] = "regsitry.full_name"          # typo
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    schema = load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)
    from databridge_intake.prefill import _resolve_source

    with pytest.raises(QuestionnaireError, match="unknown prefill_source"):
        _resolve_source(schema.field("name"), registry()[0], None, None)


def test_the_none_questionnaire_is_empty_not_an_error():
    result = prefill(QuestionnaireKind.NONE)
    assert result.kind is QuestionnaireKind.NONE and result.fields == ()


# --- regressions found by review ---------------------------------------------------------


def test_removing_the_clinical_flag_does_not_make_a_field_prefillable(tmp_path):
    """`clinical` alone is fail-OPEN: omitting it is not a typo the strict model can catch. A
    field is clinical by its SECTION too, so deleting the flag from a medical-history field
    changes nothing."""
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    field = next(f for f in data["fields"] if f["id"] == "current_medications")
    field.pop("clinical", None)
    field["prefill_source"] = "registry.drug_allergy"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(Exception, match="clinical"):
        load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)


def test_an_empty_registry_cell_is_absent_not_an_answer():
    """A blank cell rendered as PREFILLED shows an empty box the patient reads as answered, asks
    them to confirm a blank allergy record, and inflates the count feeding the time-saved metric."""
    patient = registry()[0]
    blanked = patient.model_copy(update={
        "email": patient.email.model_copy(update={"value": ""}),
        "drug_allergy": patient.drug_allergy.model_copy(update={"value": "   "})})
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, blanked)
    by_id = {f.field_id: f for f in result.fields}
    assert by_id["email_address"].status is PrefillStatus.PATIENT_MUST_ANSWER
    assert by_id["drug_allergies"].status is PrefillStatus.PATIENT_MUST_ANSWER


def test_weeks_pregnant_is_not_applicable_for_a_male_patient():
    """Inapplicability cascades. Without it a male patient's form asks how many weeks pregnant he
    is — and it counts toward `patient_must_answer_count`."""
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, _patient_of_sex("M"))
    by_id = {f.field_id: f for f in result.fields}
    assert by_id["pregnant"].status is PrefillStatus.NOT_APPLICABLE
    assert by_id["weeks_pregnant"].status is PrefillStatus.NOT_APPLICABLE


def test_drug_allergy_details_is_not_applicable_when_there_are_no_allergies():
    """`drug_allergies` resolves to "None" for many registry rows; the details box is then a
    question that does not apply, not one the patient must answer."""
    patient = next(r for r in registry() if str(r.drug_allergy.value).strip().lower() == "none")
    result = _prefill(QuestionnaireKind.GENERAL_HEALTH, patient)
    by_id = {f.field_id: f for f in result.fields}
    assert by_id["drug_allergies"].status is PrefillStatus.PREFILLED
    assert by_id["drug_allergy_details"].status is PrefillStatus.NOT_APPLICABLE


def test_an_unknown_sex_hides_every_sex_specific_option():
    """Fail closed. Showing both sets means a form offering Prostate Disease and Gestational
    Diabetes side by side, which is worse than asking for the missing answer first."""
    conditions = questionnaire(QuestionnaireKind.GENERAL_HEALTH).field(
        "medical_history_conditions")
    shown = conditions.options_for(None)
    for gated in ("Prostate Disease", "Gestational Diabetes",
                  "Polycystic Ovary Syndrome (PCOS)", "Decreased Libido"):
        assert gated not in shown
    assert "Asthma" in shown           # ungated items still appear


@pytest.mark.parametrize("kind", list(EXPECTED_FIELD_COUNT))
def test_every_required_choice_field_declares_options(kind):
    """A required dropdown/radio with no options renders a control nobody can answer, and no
    CSV-column test can see it."""
    from databridge_intake.prefill import CHOICE_TYPES

    empty = [f.id for f in questionnaire(kind).fields
             if f.type in CHOICE_TYPES and f.required and not f.options]
    assert empty == []


def test_a_typo_after_a_valid_source_prefix_is_refused_at_load(tmp_path):
    """The dangerous typo is the one AFTER the prefix: it degrades to "the patient answers this",
    which looks exactly like a working form while silently dropping the automation."""
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    next(f for f in data["fields"] if f["id"] == "phone_number")["prefill_source"] = (
        "registry.phone_number")        # the registry field is `contact_mobile`
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(QuestionnaireError, match="no such grounded field"):
        load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)


def test_a_schema_declaring_the_wrong_kind_is_refused(tmp_path):
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    data["kind"] = "occupational_health"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(QuestionnaireError, match="was loaded as"):
        load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)


def test_a_typo_in_an_option_gate_is_refused_at_load(tmp_path):
    """`requires: {optoin: ...}` used to fail OPEN — Hysterectomy shown with no prior surgery."""
    import yaml

    from databridge_intake.paths import config_dir

    data = yaml.safe_load(
        (config_dir() / "questionnaires" / "general_health.yaml").read_text(encoding="utf-8"))
    field = next(f for f in data["fields"] if f["id"] == "medical_history_conditions")
    option = next(o for o in field["options"] if isinstance(o, dict) and o.get("requires"))
    option["requires"] = {"optoin": "Prior Surgery / Endoscopy"}
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(Exception, match="requires"):
        load_questionnaire(QuestionnaireKind.GENERAL_HEALTH, path)


def test_the_cached_schema_cannot_be_mutated_by_a_caller():
    """`questionnaire()` is an lru_cache, so a mutable list would be a process-wide shared object
    anyone could shrink."""
    schema = questionnaire(QuestionnaireKind.GENERAL_HEALTH)
    assert isinstance(schema.fields, tuple)
    with pytest.raises((AttributeError, TypeError)):
        schema.fields.pop()             # type: ignore[attr-defined]
