"""databridge_intake — Walnut, the Hack4Health front-desk copilot.

An AI intake layer that sits *before* the registration counter. A patient's referring document is
read, classified, and turned into a normalized ``IntakeRecord`` in which every field points back to
the source span it came from; the patient is matched to *candidates* in the clinic registry
(never confirmed — identity verification is an in-person human act); coverage and package are
resolved from a config table; the right Parkway Shenton questionnaire is pre-filled; and billing is
drafted for staff approval.

Pipeline: classify → extract → ground → resolve → eligibility → prefill → billing → ledger → metrics.

Import the public surface from this package, not from submodules.
"""
from __future__ import annotations

from .models import (
    PATIENT_MUST_ANSWER,
    AuthorisationBlock,
    BillingParty,
    AuthorisedTest,
    BillingDraft,
    ConfirmationState,
    CoverageDetermination,
    CoverageStatus,
    DocumentRef,
    DocumentType,
    EmployerBlock,
    EvidenceEntry,
    IdType,
    IntakeRecord,
    IssuerBlock,
    IssuerKind,
    PatientRecord,
    PrefilledField,
    PrefilledQuestionnaire,
    PrefillStatus,
    QuestionnaireKind,
    ResolutionCandidate,
    ResolutionResult,
    ResolutionStatus,
    SubjectBlock,
    VisitType,
)
from .classify import Classification, classify
from .extract import IntakeDraft, extract_intake
from .gold import gold_harness
from .ground import GroundingResult, ground, locate
from .pack import active_pack, pack_config_dir, pack_dir
from .paths import (
    chits_dir,
    config_dir,
    fixtures_dir,
    general_health_csv,
    occupational_health_csv,
    package_root,
    patient_registry_csv,
    repo_root,
    source_data_dir,
)
from .clock import deployment_today
from .registry import (
    DATASET_TODAY,
    DOB_PIVOT_YEAR,
    RegistryError,
    by_patient_id,
    classify_id_type,
    load_registry,
    parse_dob_two_digit_year,
    patient_id_for,
    registry,
)
from .rules import (
    Billing,
    CatalogueTest,
    CoverageRules,
    Eligibility,
    Issuer,
    Package,
    RulesError,
    SelfPay,
    TestMatch,
    load_rules,
    rules,
)

__all__ = [
    # models — closed vocabularies
    "DocumentType", "IssuerKind", "VisitType", "IdType", "QuestionnaireKind",
    "CoverageStatus", "ResolutionStatus", "PATIENT_MUST_ANSWER", "PrefillStatus",
    "BillingParty",
    # models — records
    "PatientRecord", "IntakeRecord", "DocumentRef", "IssuerBlock", "SubjectBlock",
    "AuthorisationBlock", "EmployerBlock",
    # models — downstream
    "ResolutionCandidate", "ResolutionResult", "CoverageDetermination", "AuthorisedTest",
    "BillingDraft", "PrefilledField", "PrefilledQuestionnaire", "ConfirmationState",
    "EvidenceEntry",
    # pipeline
    "classify", "Classification", "extract_intake", "IntakeDraft",
    "ground", "locate", "GroundingResult", "gold_harness",
    # registry
    "load_registry", "registry", "by_patient_id", "patient_id_for", "classify_id_type",
    "parse_dob_two_digit_year", "RegistryError", "DOB_PIVOT_YEAR", "DATASET_TODAY",
    "deployment_today",
    # rules
    "load_rules", "rules", "CoverageRules", "Issuer", "Package", "Eligibility", "Billing",
    "CatalogueTest", "TestMatch", "SelfPay", "RulesError",
    # paths
    "repo_root", "source_data_dir", "fixtures_dir", "chits_dir", "config_dir",
    "package_root", "patient_registry_csv", "general_health_csv", "occupational_health_csv",
    # pack (the vertical the rules come from)
    "active_pack", "pack_dir", "pack_config_dir",
]
