"""Coverage determination tests.

The cases that matter are the ones where the honest answer is less impressive than the confident
one: chit 8 (an authorisation form with no test code ticked → *undetermined*, never a guessed
package) and chit 5 (a package ticked for ages 40-59 against two possible patients aged 21 and 30
→ a conflict that is certain even though the identity is not).
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_intake.eligibility import (FLAG_AGE_OUTSIDE_BAND, FLAG_AGE_PENDING_IDENTITY,
                                           FLAG_AGE_UNKNOWN, FLAG_AMBIGUOUS_TESTS,
                                           FLAG_NO_TESTS_ON_DOCUMENT, FLAG_UNMATCHED_TESTS,
                                           determine_coverage)
from databridge_intake.extract import extract_intake
from databridge_intake.gold import gold_harness
from databridge_intake.models import CoverageStatus, IntakeRecord, QuestionnaireKind
from databridge_intake.paths import fixtures_dir
from databridge_intake.registry import DATASET_TODAY, registry
from databridge_intake.resolve import resolve
from databridge_intake.rules import rules


@pytest.fixture(scope="module")
def intakes() -> dict[str, IntakeRecord]:
    harness = gold_harness()
    return {p.name: extract_intake(p.read_text(encoding="utf-8"),
                                   document_id=p.name, harness=harness)
            for p in sorted((fixtures_dir() / "chits").glob("*"))}


@pytest.fixture(scope="module")
def coverage(intakes) -> dict[str, "CoverageDetermination"]:
    """Coverage for every chit, with the resolver's candidates supplied but nobody selected —
    the state the summary card is built in, before staff have verified anyone."""
    out = {}
    for name, record in intakes.items():
        out[name] = determine_coverage(record, candidates=resolve(record.subject).candidates, today=DATASET_TODAY)
    return out


def _patient(id_number: str):
    return next(r for r in registry(DATASET_TODAY) if r.id_number.value == id_number)


# --- the two headline cases from PLAN.md §5 Phase 2 ---------------------------------------


def test_an_authorisation_form_with_no_test_code_is_undetermined(coverage):
    """Chit 8. Section B carries no ticked package code.

    Guessing this issuer's most common package would be right most of the time and catastrophic
    the rest — the patient would arrive for the wrong examination, billed to the wrong scheme.
    """
    cov = coverage["chit_08_mol_authorisation_vital.txt"]
    assert cov.status is CoverageStatus.UNDETERMINED
    assert cov.package_code is None
    assert cov.matched_rule_id is None
    assert cov.authorised_tests == []
    assert any("states no package code" in r for r in cov.reasoning)
    # The issuer WAS recognised — the failure is specific to the package, and the trace says so.
    assert cov.issuer_code == "MOL0199VME"


def test_an_age_band_conflict_is_certain_even_when_identity_is_not(coverage):
    """Chit 5. Two candidates aged 21 and 30; the voucher ticks a package for ages 40-59.

    Neither possible patient could be eligible, so the conflict holds whichever one staff select.
    This is the only kind of conclusion the system may draw before identity is settled: one that
    is true for every candidate.
    """
    cov = coverage["chit_05_bluepeak_wellness_voucher.txt"]
    assert cov.package_code == "WELL2"
    assert FLAG_AGE_OUTSIDE_BAND in cov.eligibility_flags
    assert cov.status is CoverageStatus.PARTIAL
    assert any("every candidate patient" in r and "outside that band" in r for r in cov.reasoning)


def test_the_upper_age_boundary_is_inclusive(coverage):
    """Chit 7. PEE226 is ages 25-49 and the matched patient is exactly 49 in the dataset's
    'today'. An off-by-one here would deny a covered patient their package."""
    patient = _patient("S8001691D")
    assert patient.age_at(DATASET_TODAY) == 49
    cov = coverage["chit_07_mol_authorisation_pee226.txt"]
    assert cov.package_code == "PEE226"
    assert FLAG_AGE_OUTSIDE_BAND not in cov.eligibility_flags
    assert cov.status is CoverageStatus.DETERMINED


@pytest.mark.parametrize("age,eligible", [(24, False), (25, True), (49, True), (50, False)])
def test_both_age_band_boundaries(age, eligible):
    """Directly against the rule, so the boundary is pinned even if the fixtures change."""
    band = next(p for p in rules().packages if p.package_code == "PEE226").eligibility
    assert band.covers_age(age) is eligible


# --- status is honest about what was and was not established -------------------------------


def test_a_document_defined_rule_with_no_tests_does_not_report_determined(coverage):
    """Chit 9, an HR email that asks the clinic to arrange a medical without naming one.

    The matched rule's definition is "the tests are whatever the document lists", so a document
    listing none has established no scope. Reporting `determined` would put a confident, empty
    test list on the summary card.
    """
    cov = coverage["chit_09_coralharbour_hr_email.eml"]
    assert cov.matched_rule_id is not None
    assert cov.authorised_tests == []
    assert FLAG_NO_TESTS_ON_DOCUMENT in cov.eligibility_flags
    assert cov.status is CoverageStatus.PARTIAL


@pytest.mark.parametrize("fixture", [
    "chit_01_meridian_referral.txt", "chit_02_everwell_voucher.txt",
    "chit_03_everwell_group_invitation.txt", "chit_04_bluepeak_underwriting.txt",
    "chit_06_northstar_further_req.txt", "chit_07_mol_authorisation_pee226.txt",
])
def test_the_documents_that_state_a_full_scope_are_determined(coverage, fixture):
    cov = coverage[fixture]
    assert cov.status is CoverageStatus.DETERMINED
    assert cov.eligibility_flags == []
    assert cov.authorised_tests


def test_an_unknown_issuer_is_undetermined_not_defaulted(intakes):
    """An issuer code we have no rule for must not fall through to some default payer. The
    reasoning names the file to edit, because the fix is a config change by design."""
    record = intakes["chit_01_meridian_referral.txt"]
    stripped = record.model_copy(update={
        "issuer": record.issuer.model_copy(update={
            "issuer_code": record.issuer.issuer_code.model_copy(update={"value": "NOSUCH1"})})})
    cov = determine_coverage(stripped, today=DATASET_TODAY)
    assert cov.status is CoverageStatus.UNDETERMINED
    assert cov.matched_rule_id is None
    assert any("coverage_rules.yaml" in r for r in cov.reasoning)


def test_a_document_with_no_issuer_code_is_undetermined(intakes):
    record = intakes["chit_01_meridian_referral.txt"]
    stripped = record.model_copy(update={
        "issuer": record.issuer.model_copy(update={"issuer_code": None})})
    cov = determine_coverage(stripped, today=DATASET_TODAY)
    assert cov.status is CoverageStatus.UNDETERMINED
    assert any("does not carry an issuer code" in r for r in cov.reasoning)


def test_a_package_code_absent_from_the_table_is_undetermined(intakes):
    """Distinct from "no package code at all" — the document DID state one and we do not have it.
    The reasoning must not blur the two, because they need different follow-up from staff."""
    record = intakes["chit_05_bluepeak_wellness_voucher.txt"]
    stripped = record.model_copy(update={
        "authorisation": record.authorisation.model_copy(update={
            "package_code": record.authorisation.package_code.model_copy(
                update={"value": "WELL99"})})})
    cov = determine_coverage(stripped, today=DATASET_TODAY)
    assert cov.status is CoverageStatus.UNDETERMINED
    assert cov.package_code is None
    assert any("has no such package" in r for r in cov.reasoning)


# --- authorised tests -----------------------------------------------------------------


def test_package_contents_are_included_even_when_the_document_lists_them(coverage):
    """Chit 5 prints all 11 WELL2 tests. They must appear once each, not twice — a duplicated
    line is a double-billed test."""
    cov = coverage["chit_05_bluepeak_wellness_voucher.txt"]
    canonicals = [t.canonical for t in cov.authorised_tests]
    assert len(canonicals) == len(set(canonicals)), f"duplicate authorised tests: {canonicals}"
    # Derived from the rule table (HC1, rule 6): the package's own test list, not a count.
    from databridge_intake.rules import rules

    package = rules().package(cov.issuer_code, cov.package_code)
    assert package is not None and len(package.includes_tests) >= 1
    assert len(canonicals) == len(package.includes_tests)
    assert all(t.in_package for t in cov.authorised_tests)


def test_a_printed_test_carries_a_pointer_back_to_the_document(coverage):
    """The click-through evidence path: a test on the card resolves to the line that ordered it."""
    cov = coverage["chit_01_meridian_referral.txt"]
    assert cov.authorised_tests
    for test in cov.authorised_tests:
        assert test.pointer is not None, f"{test.as_printed} has no source pointer"
        assert test.source_field and test.source_field.startswith("authorisation.")


def test_package_derived_tests_cite_the_yaml_rule_not_a_document_span(coverage):
    """A test that comes from the package definition is not quoted from the document, so it must
    NOT carry a document span — but it must still carry a real, resolvable pointer. It gets a
    `table_cell` into the coverage rules, so a billable line on the summary card always has
    provenance a human can open (CLAUDE.md §Hard constraints 2)."""
    cov = coverage["chit_07_mol_authorisation_pee226.txt"]
    assert cov.matched_rule_id == "rule_mol_pee226"
    from_package_only = [t for t in cov.authorised_tests
                         if t.in_package and t.source_field is None]
    assert from_package_only, "chit 7's package contributes tests beyond those printed"
    for test in from_package_only:
        assert test.pointer is not None, f"{test.as_printed} has no provenance at all"
        assert test.pointer.kind == "table_cell"
        assert test.pointer.source_id == "coverage_rules.yaml"
        assert test.pointer.row_id == "rule_mol_pee226"


def test_no_authorised_test_is_left_without_any_pointer(coverage):
    """Every emitted value carries a source. Applies to both flavours: printed tests cite the
    document span, package contents cite the YAML row."""
    for name, cov in coverage.items():
        for test in cov.authorised_tests:
            assert test.pointer is not None, f"{name}: {test.as_printed!r} has no pointer"


def test_a_test_ordered_beyond_the_package_is_not_folded_into_covered(coverage):
    """Chit 7 prints an ECG on top of PEE226's contents. It must stay `in_package=False` — that
    distinction is what stops an add-on being silently billed to the scheme."""
    cov = coverage["chit_07_mol_authorisation_pee226.txt"]
    extras = [t for t in cov.authorised_tests if not t.in_package]
    assert extras, "the ECG add-on should be carried as outside the package"
    assert all(t.pointer is not None for t in extras), "an extra must be quoted from the document"


def test_an_unrecognised_test_is_surfaced_not_dropped_or_invented(intakes):
    """A printed test matching nothing in the catalogue is information for staff ("this insurer
    asked for something we have no code for"), never silently discarded."""
    from databridge_core import GroundedValue, SourcePointer

    record = intakes["chit_01_meridian_referral.txt"]
    ptr = SourcePointer(source_id=record.document.document_id, kind="doc_span",
                        document_id=record.document.document_id, char_start=0, char_end=1)
    invented = GroundedValue(value="Cranial Aura Scan", pointer=ptr, confidence=1.0)
    tweaked = record.model_copy(update={
        "authorisation": record.authorisation.model_copy(update={
            "requested_tests": [*record.authorisation.requested_tests, invented]})})
    cov = determine_coverage(tweaked, today=DATASET_TODAY)
    assert "Cranial Aura Scan" in cov.unmatched_tests
    assert FLAG_UNMATCHED_TESTS in cov.eligibility_flags
    assert cov.status is CoverageStatus.PARTIAL
    # It is still listed, with no canonical — shown to staff rather than deleted.
    entry = next(t for t in cov.authorised_tests if t.as_printed == "Cranial Aura Scan")
    assert entry.canonical is None and entry.in_package is False


# --- age handling when nobody is selected -------------------------------------------------


def test_a_selected_patient_takes_precedence_over_candidates(intakes):
    """Once staff have picked someone, the band is checked against that person alone."""
    record = intakes["chit_05_bluepeak_wellness_voucher.txt"]
    cov = determine_coverage(record, selected_patient=_patient("S8536477Z"), today=DATASET_TODAY)  # Loh Amir, aged 21
    assert FLAG_AGE_OUTSIDE_BAND in cov.eligibility_flags
    assert any("selected patient is 21" in r for r in cov.reasoning)


def test_a_band_is_not_silently_skipped_when_there_is_no_patient(intakes):
    """With no patient and no candidates, the age band is unchecked — and says so. Silence here
    would read as "eligibility confirmed"."""
    record = intakes["chit_05_bluepeak_wellness_voucher.txt"]
    cov = determine_coverage(record, today=DATASET_TODAY)
    assert FLAG_AGE_UNKNOWN in cov.eligibility_flags
    assert cov.status is CoverageStatus.PARTIAL


def test_candidates_disagreeing_on_eligibility_is_reported_as_such(intakes):
    """When one candidate is in the band and another is not, the system must not pick a side —
    that would be deciding identity by way of an eligibility answer."""
    from databridge_intake.models import ResolutionCandidate

    record = intakes["chit_05_bluepeak_wellness_voucher.txt"]
    candidates = resolve(record.subject, today=DATASET_TODAY).candidates      # both outside the band (21 and 30)
    in_band = _patient("S7501626C")                      # aged 52 — inside WELL2's 40-59
    assert in_band.age_at(DATASET_TODAY) == 52
    mixed = (*candidates, ResolutionCandidate(patient_id=in_band.patient_id, score=0.1,
                                              matched_on=["name"], record=in_band))
    cov = determine_coverage(record, candidates=mixed, today=DATASET_TODAY)
    assert FLAG_AGE_PENDING_IDENTITY in cov.eligibility_flags
    assert FLAG_AGE_OUTSIDE_BAND not in cov.eligibility_flags
    assert any("depends on which patient staff select" in r for r in cov.reasoning)


# --- the walk-in / self-pay path ----------------------------------------------------


def test_the_walk_in_path_is_a_rule_in_the_table_not_a_branch_in_code(intakes):
    """A walk-in with no referring document resolves through `self_pay` in the YAML."""
    harness = gold_harness()
    walkin = fixtures_dir() / "walkin"
    files = sorted(walkin.glob("*"))
    assert files, "the walk-in fixture is missing"
    record = extract_intake("", document_id=files[0].name, harness=harness,
                            channel="walk_in")
    cov = determine_coverage(record, today=DATASET_TODAY)
    assert cov.matched_rule_id == "rule_self_pay"
    assert cov.status is CoverageStatus.DETERMINED
    assert cov.package_code is None
    assert any("billed to patient" in r for r in cov.reasoning)


# --- the anti-hardcoding constraint ---------------------------------------------------


def test_a_brand_new_issuer_resolves_with_no_source_change(intakes, tmp_path):
    """CLAUDE.md §Hard constraints 3. Adding an insurer must be a YAML change only.

    Phase 1 proved this for extraction; this proves it for *coverage*, which is where the
    temptation to branch on an issuer code actually lives.
    """
    import yaml

    from databridge_intake.paths import config_dir
    from databridge_intake.rules import load_rules

    data = yaml.safe_load((config_dir() / "coverage_rules.yaml").read_text(encoding="utf-8"))
    data["issuers"].append({
        "code": "ZZTEST1", "name": "Ziggurat Assurance", "kind": "insurer",
        "billing_party": "insurer", "default_questionnaire": "general_health"})
    data["packages"].append({
        "id": "rule_zz_referral", "issuer_code": "ZZTEST1", "package_code": None,
        "name": "Ziggurat referral (tests as listed)", "tests_from_document": True,
        "questionnaire": "general_health",
        "billing": {"covered_by": "insurer", "patient_payable_sgd": 0}})
    path = tmp_path / "coverage_rules.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    record = intakes["chit_01_meridian_referral.txt"]
    swapped = record.model_copy(update={
        "issuer": record.issuer.model_copy(update={
            "issuer_code": record.issuer.issuer_code.model_copy(update={"value": "ZZTEST1"})})})
    cov = determine_coverage(swapped, table=load_rules(path), today=DATASET_TODAY)
    assert cov.status is CoverageStatus.DETERMINED
    assert cov.matched_rule_id == "rule_zz_referral"
    assert cov.questionnaire is QuestionnaireKind.GENERAL_HEALTH


def test_the_reasoning_trace_always_names_the_rule_that_fired(coverage):
    """The determination has to be arguable, not oracular: every non-undetermined result names
    the YAML rule id a human can go read."""
    for name, cov in coverage.items():
        assert cov.reasoning, f"{name} produced no reasoning trace"
        if cov.status is not CoverageStatus.UNDETERMINED:
            assert cov.matched_rule_id
            assert any(cov.matched_rule_id in r for r in cov.reasoning), name
