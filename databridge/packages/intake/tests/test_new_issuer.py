"""Phase 1 — the headline scalability claim, tested rather than asserted.

Parkway Shenton deals with many insurers, TPAs and employers, and the whole premise of this
design is that onboarding a new one is a configuration change. This module proves it by inventing
an insurer that has never been seen — a new code, a new package, a new test, a letter written in
a layout nobody tuned for — and running the real pipeline over it.

**Nothing in this file may require an edit to any `.py`.** If a future change breaks that, this
test is the thing that says so.
"""
from __future__ import annotations

import pytest
import yaml

from databridge_intake import DocumentType, load_rules
from databridge_intake.classify import classify
from databridge_intake.extract import IntakeDraft, extract_intake
from databridge_intake.paths import config_dir
from databridge_intake.rules import CoverageRules

# A referral letter from an insurer that does not exist, in nobody's template.
NEW_ISSUER_LETTER = """\
Zenith Assurance (Asia) Limited
Group Benefits Office, 12 Marina View, Singapore 018961

Code: ZZTEST1
11 August 2026
Policy Number: ZZ4410982

Medical Referral Letter

To        : The Medical Examiner
Policyholder : Northwind Logistics Pte Ltd
Examinee  : Tan Kai Xuan (S4744854C)

Please arrange the following for the above-named:
Chest X-Ray
Zenith Panel Test 7 - Bone Density Scan
Resting ECG

Kindly return findings to gb.medical@zenithassurance.example.
"""


@pytest.fixture()
def extended_rules(tmp_path) -> CoverageRules:
    """The shipped config plus one new issuer, one new package and one new test — as a clinic
    administrator would add them: by editing YAML."""
    data = yaml.safe_load((config_dir() / "coverage_rules.yaml").read_text(encoding="utf-8"))
    data["issuers"].append({
        "code": "ZZTEST1",
        "name": "Zenith Assurance (Asia) Limited",
        "kind": "insurer",
        "billing_party": "insurer",
        "tpa_claim_required": True,
        "default_visit_type": "general_health_screening",
        "default_questionnaire": "general_health",
    })
    data["test_catalogue"].append({
        "canonical": "bone_density_scan",
        "category": "imaging",
        "list_price_sgd": 120,
        "aliases": ["Bone Density Scan", "DEXA Scan",
                    "Zenith Panel Test 7 - Bone Density Scan"],
    })
    data["packages"].append({
        "id": "rule_zenith_referral",
        "issuer_code": "ZZTEST1",
        "package_code": None,
        "name": "Zenith medical referral (tests as listed)",
        "tests_from_document": True,
        "questionnaire": "general_health",
        "billing": {"covered_by": "insurer", "patient_payable_sgd": 0},
    })
    path = tmp_path / "extended.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_rules(path)


def test_a_never_seen_letter_still_classifies_correctly():
    """Classification keys on layout, not on a list of known insurers."""
    verdict = classify(NEW_ISSUER_LETTER)
    assert verdict.doc_type is DocumentType.INSURER_REFERRAL_LETTER
    assert verdict.confidence > 0.5


def test_the_new_issuer_resolves_from_config_alone(extended_rules):
    issuer = extended_rules.issuer("ZZTEST1")
    assert issuer is not None
    assert issuer.name == "Zenith Assurance (Asia) Limited"
    assert extended_rules.package("ZZTEST1", None).tests_from_document is True


def test_the_new_test_matches_including_its_vendor_prefixed_name(extended_rules):
    assert extended_rules.match_test("Bone Density Scan").canonical == "bone_density_scan"
    assert (extended_rules.match_test("Zenith Panel Test 7 - Bone Density Scan").canonical
            == "bone_density_scan")
    # and the existing catalogue is undisturbed
    assert extended_rules.match_test("Chest X-Ray").canonical == "chest_xray"


def test_extraction_grounds_the_new_issuers_fields():
    """Proves the ISSUER-AGNOSTIC half of the claim: one prompt, one schema, one mapping and one
    wall handle a layout nobody tuned for, with no code change.

    It does NOT prove a model can read this layout — the draft here is supplied directly, standing
    in for the model exactly as `gold.py` does for the shipped fixtures. Model accuracy on unseen
    layouts is what you measure by running the pipeline with a real API key; this test measures
    that nothing downstream of the model is issuer-specific.
    """
    from databridge_harness import FakeBackend, Harness

    draft = IntakeDraft(
        document_type="insurer_referral_letter",
        issuer_name="Zenith Assurance (Asia) Limited",
        issuer_kind="insurer",
        issuer_code="ZZTEST1",
        policy_number="ZZ4410982",
        claimed_name="Tan Kai Xuan",
        claimed_id_number="S4744854C",
        purpose_text="Medical Referral Letter",
        requested_tests=["Chest X-Ray", "Zenith Panel Test 7 - Bone Density Scan", "Resting ECG"],
        employer_name="Northwind Logistics Pte Ltd",
        billing_party="insurer",
        visit_type="general_health_screening",
    )
    payload = draft.model_dump_json()
    harness = Harness(FakeBackend(lambda *, model, messages, guided_json: payload),
                      profile="cloud")

    rec = extract_intake(NEW_ISSUER_LETTER, document_id="chit_zz_zenith.txt", harness=harness)

    assert rec.document_type.value == DocumentType.INSURER_REFERRAL_LETTER.value
    assert rec.issuer.issuer_code.value == "ZZTEST1"
    assert rec.issuer.policy_number.value == "ZZ4410982"
    assert rec.subject.claimed_name.value == "Tan Kai Xuan"
    assert rec.subject.claimed_id_number.value == "S4744854C"
    assert rec.employer.employer_name.value == "Northwind Logistics Pte Ltd"
    assert len(rec.authorisation.requested_tests) == 3
    assert not rec.refused

    # every pointer still resolves against this never-before-seen document
    for path, gv in rec.grounded_fields().items():
        p = gv.pointer
        if p.kind != "doc_span":
            continue
        sliced = NEW_ISSUER_LETTER[p.char_start:p.char_end]
        assert "".join(str(gv.value).split()).lower() in "".join(sliced.split()).lower(), path


def test_the_wall_still_applies_to_the_new_issuer():
    """A new insurer does not get a free pass: fabricated values are refused as usual."""
    from databridge_harness import FakeBackend, Harness

    liar = IntakeDraft(issuer_code="ZZTEST1", policy_number="ZZ9999999",
                       claimed_name="Someone Not On This Letter").model_dump_json()
    harness = Harness(FakeBackend(lambda *, model, messages, guided_json: liar), profile="cloud")
    rec = extract_intake(NEW_ISSUER_LETTER, document_id="chit_zz_zenith.txt", harness=harness)

    assert rec.issuer.issuer_code.value == "ZZTEST1"      # this one IS on the page
    assert rec.issuer.policy_number is None                # this one is not
    assert rec.subject.claimed_name is None
    assert len(rec.refused) >= 2
