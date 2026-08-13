"""Deterministic extraction backend for CI and the offline demo.

**What is real and what is not.** The documents are real fixtures, the grounding wall is real, the
draft→record mapping is real, and every downstream stage (resolution, eligibility, billing) runs
exactly as it does in production. The only thing replaced is the *model call*: instead of asking an
LLM to read the letter, a handler keyed to a unique marker in each document returns the draft that
a competent reader would produce.

This is the same "plumbing proof" pattern the repo uses elsewhere (`gold_extraction_handler` in
`packages/graph`). It exists so the test suite and an offline demo never depend on a network call
or a model's mood. It is NOT an accuracy claim: extraction accuracy is what you get by running the
same pipeline with `ANTHROPIC_API_KEY` set, which the gateway does automatically.

Note the drafts below are deliberately *unpolished* — they contain the document's own wording,
including strings that will FAIL grounding or fail to match the test catalogue. That is the point:
a gold set that only contains clean, matchable values would test nothing.
"""
from __future__ import annotations

from databridge_harness import FakeBackend, Harness

from .extract import IntakeDraft

# filename → (unique dispatch marker, draft). The marker must appear in that document and in no
# other, so dispatch never depends on dict iteration order.
GOLD: dict[str, tuple[str, IntakeDraft]] = {
    "chit_01_meridian_referral.txt": ("MRD707314", IntakeDraft(
        document_type="insurer_referral_letter",
        issuer_name="Meridian Life Assurance Pte Ltd",
        issuer_kind="insurer",
        issuer_code="MRDEB",
        policy_number="MRD707314",
        issuer_contact="eb.uw@mail.meridianlife.com.sg",
        claimed_name="Loh Wei Ming",
        claimed_id_number="S8010946C",
        purpose_text="Medical Referral Letter",
        requested_tests=["Chest X-Ray", "Meridian Life# 10 - HIV Antibody Test", "Treadmill ECG"],
        valid_from="4 August 2026",
        employer_name="Coral Harbour Pte Ltd",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    # Phase 6 fixture: a SECOND document for a patient already in the corpus, from a different
    # insurer. It exists so the cross-document conflict flag has a real case — the pipeline reads
    # one document at a time and structurally cannot see this; only a graph traversal can.
    "chit_10_northstar_second_referral.txt": ("NST881204", IntakeDraft(
        document_type="underwriting_followup",
        issuer_name="Northstar Life Assurance",
        issuer_kind="insurer",
        issuer_code="NSTNBU",
        reference_number="NST881204",
        issuer_contact="nbu.medical@mail.northstarlife.com.sg",
        claimed_name="Loh Wei Ming",
        claimed_id_number="S8010946C",
        purpose_text="Further Medical Requirements",
        requested_tests=["Urine Examination and Microscopy"],
        valid_from="9 August 2026",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    "chit_02_everwell_voucher.txt": ("EVW563298", IntakeDraft(
        document_type="health_checkup_voucher",
        issuer_name="Everwell Insurance Group",
        issuer_kind="insurer",
        issuer_code="EVWPA",
        policy_number="EVW563298",
        claimed_name="Lim Arjun",
        claimed_id_number="T2221854H",
        purpose_text="Everwell Health Check-up Voucher",
        requested_tests=["Medical Examination", "Lipid Profile", "UFEME", "Resting ECG"],
        valid_from="02 Jun 2026",
        valid_until="30 Sep 2026",
        appointment_datetime="30 / 07 / 2026",
        appointment_location="Everwell Health Screening Centre",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    "chit_03_everwell_group_invitation.txt": ("GL-EVW975136", IntakeDraft(
        document_type="group_insurance_invitation",
        issuer_name="Everwell Insurance Group",
        issuer_kind="insurer",
        issuer_code="EVWME",
        policy_number="EVW975136",
        reference_number="C-58393",
        # No NRIC anywhere on this letter — left null on purpose. It must NOT be back-filled from
        # the registry here; that is the resolver's job, and only as a candidate.
        claimed_name="Devi Hui Min",
        purpose_text="INVITATION FOR MEDICAL TESTS AND/OR FURNISHING INFORMATION",
        requested_tests=["Adult Medical Examination"],
        employer_name="CORAL HARBOUR PTE LIMITED",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    "chit_04_bluepeak_underwriting.txt": ("BLP795612", IntakeDraft(
        document_type="underwriting_followup",
        issuer_name="Bluepeak Prosperity Life",
        issuer_kind="insurer",
        issuer_code="BLPDE",
        reference_number="BLP795612",
        claimed_name="Yeo Nur Aisyah",
        claimed_id_number="T1800429G",
        purpose_text="Underwriting Follow Up",
        # "Manager's Approval" and "Documentary proof of residential address" are administrative
        # requirements, not tests — the prompt tells the model to leave them out, and the
        # catalogue would surface them as unmatched if they slipped in.
        requested_tests=["Full Medical Examination", "Anti-HIV Test"],
        special_instructions=["No paramedics allowed"],
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    "chit_05_bluepeak_wellness_voucher.txt": ("BLP301158", IntakeDraft(
        document_type="health_checkup_voucher",
        issuer_name="Bluepeak Prosperity Life",
        issuer_kind="insurer",
        issuer_code="BLPHS",
        policy_number="BLP301158",
        # No NRIC on this voucher either — the second ambiguous-identity case.
        claimed_name="Loh Amir",
        # Only the TICKED tier. WELL1 and WELL3 are printed but unticked and must not appear.
        package_code="WELL2",
        package_name="Comprehensive Screen",
        purpose_text="Biennial Medical Check-up",
        requested_tests=[
            "Complete History Taking", "Complete Physical Examination", "BMI and Fat Composition",
            "Blood pressure measurement", "Full Blood Count", "Cholesterol screening",
            "Liver function screening", "Kidney function screening", "Thyroid function screening",
            "Resting ECG", "Medical Report Consultation"],
        valid_until="14 July 2028",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    "chit_06_northstar_further_req.txt": ("NST245095", IntakeDraft(
        document_type="underwriting_followup",
        issuer_name="Northstar Life Assurance",
        issuer_kind="insurer",
        issuer_code="NSTNBU",
        reference_number="NST245095",
        claimed_name="Wong Siti",
        claimed_id_number="S2915369H",
        purpose_text="Request for further requirement(s)",
        requested_tests=["Urine Examination and Microscopy"],
        valid_until="13 Aug 2026",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),

    "chit_07_mol_authorisation_pee226.txt": ("Assistant Learning Officer", IntakeDraft(
        document_type="tpa_authorisation_form",
        issuer_name="Ministry of Learning",
        issuer_kind="government_scheme",
        issuer_code="MOL0199VME",
        claimed_name="Kumar Sheng Yang",
        claimed_id_number="S8001691D",
        claimed_dob="20/05/77",
        claimed_sex="Male",
        # PEE226 is the only ticked tier; PEE224 and PEE225 are printed but unticked.
        package_code="PEE226",
        purpose_text="Pre-employment medical examination for appointment into the Public Service "
                     "as Assistant Learning Officer",
        requested_tests=["Electrocardiogram (ECG)"],
        add_ons=["MMR Dose 1 Vaccination"],
        agency="Ministry of Learning",
        scheme_of_service="MKL",
        billing_party="government_scheme",
        visit_type="occupational_health_screening",
    )),

    "chit_08_mol_authorisation_vital.txt": ("Learning Support Scheme", IntakeDraft(
        document_type="tpa_authorisation_form",
        issuer_name="Ministry of Learning",
        issuer_kind="government_scheme",
        issuer_code="MOL0199VME",
        claimed_name="Loh Hui Min",
        claimed_id_number="T7605473J",
        claimed_dob="23/02/79",
        claimed_sex="Female",
        # This form has NO Section B at all — no test code is ticked. package_code stays null and
        # coverage must come back UNDETERMINED rather than defaulting to a tier.
        purpose_text="Pre-employment medical examination for appointment into the Public Service "
                     "as Learning Support Officer",
        agency="Ministry of Learning",
        scheme_of_service="Learning Support Scheme",
        billing_address="Ministry of Learning (VITAL), MOL Complex Tower Block, Singapore 138675",
        billing_party="government_scheme",
        visit_type="occupational_health_screening",
    )),

    "chit_09_coralharbour_hr_email.eml": ("hr.admin@coralharbour.com.sg", IntakeDraft(
        document_type="employer_hr_email",
        issuer_name="Meridian Life Employee Benefits",
        issuer_kind="employer",
        issuer_code="MRDEB",
        # An HR email carries no NRIC — the third name-only resolution case.
        claimed_name="Ali Amir",
        purpose_text="Pre-Confirmation Medical Check-up Appointment",
        appointment_datetime="30 July 2026",
        appointment_location="Meridian Panel Clinic, Novena",
        special_instructions=["Fast for at least 9 hours prior to the medical check-up"],
        employer_name="CORAL HARBOUR PTE LTD",
        hr_contact="hr.admin@coralharbour.com.sg",
        billing_party="insurer",
        visit_type="general_health_screening",
    )),
}


def gold_harness() -> Harness:
    """A harness whose handler dispatches on document content.

    Returns an empty draft for an unrecognised document rather than the nearest known one — the
    honest answer for "I have never seen this" is "I extracted nothing", which then presents to
    staff as a document needing manual reading.
    """
    payloads = {marker: draft.model_dump_json() for marker, draft in GOLD.values()}

    def handler(*, model, messages, guided_json) -> str:
        # Every message, not just the last. This used to read `messages[-1]` alone, which quietly
        # encoded "the document is always the final message" — an assumption the harness broke the
        # moment it began inserting the JSON schema into the prompt (the L1 rung), and which an
        # L2 retry would have broken anyway by appending a correction. The failure was silent and
        # total: no marker matched, so every document extracted to an empty draft and the tests
        # reported candidate lists of length zero rather than a lookup miss.
        doc = "\n".join(m.get("content") or "" for m in messages)
        for marker, payload in payloads.items():
            if marker in doc:
                return payload
        return IntakeDraft().model_dump_json()

    return Harness(FakeBackend(handler), profile="cloud")


def markers_are_unique(texts: dict[str, str]) -> list[str]:
    """Return dispatch markers that appear in more than one document (should always be empty)."""
    clashes = []
    for filename, (marker, _) in GOLD.items():
        hits = [n for n, t in texts.items() if marker in t]
        if hits != [filename]:
            clashes.append(f"{marker!r} appears in {hits}, expected only {filename}")
    return clashes
