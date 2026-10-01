"""Hand-written plans for the compiler tests.

The compiler must be provable against plans that are WRONG in exactly one way, and an agent that
authors correct plans cannot supply those. So these are written out longhand: `covenant_plan()`
is the reference, and every refusal test takes it and breaks one thing.

Nothing here is a fixture of expected output. Each plan is an input, and what the tests assert is
the refusal it provokes and the reason attached to it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from databridge_materialise import (
    EXECUTOR_VERSION,
    AuthoredBy,
    Cohort,
    Column,
    ColumnSource,
    Consumer,
    Determinism,
    Executor,
    Label,
    LabelSource,
    LabelWindow,
    MaterialisationPlan,
    MaterialisationPolicy,
    Output,
    PolicyPrincipal,
    Provenance,
    Selection,
    Shape,
    SourceSnapshot,
    Split,
    TemporalContract,
)

BUILD_TIME = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)
AUTHORED_AT = datetime(2026, 8, 16, 9, 12, tzinfo=timezone.utc)

SNAPSHOT = SourceSnapshot(source_id="core_banking", tier="T1", as_of=BUILD_TIME,
                          content_hash="sha256:synthetic-fixture", version_id="seed-v2")

#: `fixtures/packs/finance/roles.yaml` declares `estate: walnut-finance-synthetic` and `version: 2`;
#: `RoleModel.bundle_version` renders that as `f"{estate}@v{version}"`. Pinned here rather than
#: read live off the fixture file so a plan's `policy.bundle_version` (R-014 Finding 3) is a
#: constant a test can assert against, the same way `SNAPSHOT.content_hash` is a fixed string
#: rather than a hash computed at import time.
POLICY_BUNDLE_VERSION = "walnut-finance-synthetic@v2"


def covenant_plan(**overrides: Any) -> MaterialisationPlan:
    """The reference plan: covenant-breach prediction over the synthetic finance estate.

    SYSTEM_DESIGN §6.6(a) in miniature. One row per covenant test; T_d is the test date; features
    are the previously *recorded* test for the same covenant, cut on `recorded_at`; the label is
    the next test's outcome, cut on `test_date` and matured against `recorded_at`.
    """
    base: dict[str, Any] = dict(
        label="covenant-breach 365d, synthetic finance estate",
        authored_by=AuthoredBy(agent="data_agent", on_behalf_of="analyst-7",
                               purpose="lending.covenant_monitoring", at=AUTHORED_AT),
        selection=Selection(entity="covenant_test",
                            cohort=Cohort(decision_time_field="test_date"),
                            traverse=("covenant_test -[of]-> covenant",)),
        temporal=TemporalContract(
            feature_axis="system_time",
            feature_as_of="per_row",
            label_axis="valid_time",
            label_window=LabelWindow(within="P365D"),
            label_read_at_system_time="build_time",
            label_maturity_lag="P14D",
            build_time=BUILD_TIME,
            source_snapshots=(SNAPSHOT,),
            pin_strength="observational"),
        shape=Shape(
            kind="tabular",
            consumer=Consumer(framework="xgboost", version="3.4.0"),
            columns=(
                Column(path="covenant_test.test_id", type="string",
                       source=ColumnSource(entity="covenant_test", field="test_id",
                                           relation="cohort_key")),
                Column(path="covenant_test.test_date", type="date",
                       source=ColumnSource(entity="covenant_test", field="test_date",
                                           relation="decision_time")),
                Column(path="covenant_test.prior_observed_value", type="float64",
                       source=ColumnSource(entity="covenant_test", field="observed_value",
                                           relation="as_of_prior", join_key="covenant_id",
                                           system_time_field="recorded_at",
                                           valid_time_field="test_date")),
                Column(path="covenant_test.prior_breached", type="bool",
                       source=ColumnSource(entity="covenant_test", field="breached",
                                           relation="as_of_prior", join_key="covenant_id",
                                           system_time_field="recorded_at",
                                           valid_time_field="test_date")),
                Column(path="covenant_test.prior_test_count", type="int32",
                       pointer_mode="derived_compressed",
                       source=ColumnSource(entity="covenant_test", field="test_id",
                                           relation="aggregate_count", join_key="covenant_id",
                                           system_time_field="recorded_at",
                                           valid_time_field="test_date")),
            ),
            label=Label(path="covenant_test.breached_next", type="bool",
                        source=LabelSource(entity="covenant_test", field="breached",
                                           join_key="covenant_id",
                                           valid_time_field="test_date",
                                           system_time_field="recorded_at")),
            emit_missing_reason=True,
            semantic_names=True),
        policy=MaterialisationPolicy(
            purpose="lending.covenant_monitoring",
            principal=PolicyPrincipal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                                      grant_id="grant-analyst-7"),
            lawful_basis="contract - performance of the facility agreement",
            egress="internal",
            bundle_version=POLICY_BUNDLE_VERSION),
        determinism=Determinism(seed=20260816,
                                row_order=("covenant_test.test_date", "covenant_test.test_id"),
                                executor=Executor(backend="sqlite 3.45 / databridge-adapter",
                                                  threads=1,
                                                  materialiser_version=EXECUTOR_VERSION),
                                split=Split(strategy="temporal",
                                            train_before=datetime(2026, 1, 1).date(),
                                            val="2026-01", test="2026-02"),
                                float_scale=6),
        provenance=Provenance(mode="compressed", require_pointer_for_every_cell=True),
        output=Output(format="jsonl", metadata="croissant/1.1"),
        packs=("finance@2",),
    )
    base.update(overrides)
    return MaterialisationPlan(**base)


def facility_notes_corpus(**overrides: Any) -> MaterialisationPlan:
    """A RAG corpus over `facility.notes`, membership cut on `facility.updated_at`."""
    from databridge_materialise import Chunking

    base: dict[str, Any] = dict(
        label="facility notes corpus",
        authored_by=AuthoredBy(agent="data_agent", on_behalf_of="fcc-3",
                               purpose="kyc.screening", at=AUTHORED_AT),
        selection=Selection(entity="facility", cohort=Cohort()),
        temporal=TemporalContract(
            feature_axis="system_time",
            feature_as_of="fixed",
            feature_as_of_fixed=BUILD_TIME,
            label_axis="none",
            corpus_as_of=BUILD_TIME,
            source_snapshots=(SNAPSHOT,),
            pin_strength="observational"),
        shape=Shape(
            kind="chunk_corpus",
            consumer=Consumer(framework="retriever", version="1.0"),
            columns=(),
            text_field=ColumnSource(entity="facility", field="notes", relation="cohort_row",
                                    system_time_field="updated_at",
                                    valid_time_field="start_date"),
            chunking=Chunking(max_chars=40, overlap_chars=0),
            emit_missing_reason=False),
        policy=MaterialisationPolicy(
            purpose="kyc.screening",
            principal=PolicyPrincipal(actor="orchestrator@walnut", on_behalf_of="fcc-3",
                                      grant_id="grant-fcc-3"),
            lawful_basis="legal obligation - AML/CFT sanctions screening",
            egress="restricted_internal",
            bundle_version=POLICY_BUNDLE_VERSION),
        determinism=Determinism(seed=20260816, row_order=("chunk_id",),
                                executor=Executor(backend="sqlite 3.45 / databridge-adapter",
                                                  materialiser_version=EXECUTOR_VERSION),
                                split=Split(strategy="none")),
        provenance=Provenance(mode="full", require_pointer_for_every_cell=True),
        output=Output(format="jsonl", metadata="none"),
        packs=("finance@2",),
    )
    base.update(overrides)
    return MaterialisationPlan(**base)


def party_dob_plan(**overrides: Any) -> MaterialisationPlan:
    """A plan whose projection reaches a column policy would MASK — the O1b case.

    `party.date_of_birth` is declared sensitive by the estate's source contract, so for a principal
    permitted to read it the Policy layer compiles `transform: redact_pii@v1(...)` and the Core
    emits a *partially disclosed* value behind a `derived` pointer. That cell is neither
    GroundedValue, Withheld nor Absent, and the receipt cannot describe it honestly.
    """
    base: dict[str, Any] = dict(
        label="party dob — refused",
        authored_by=AuthoredBy(agent="data_agent", on_behalf_of="fcc-3",
                               purpose="kyc.screening", at=AUTHORED_AT),
        selection=Selection(entity="party", cohort=Cohort(decision_time_field="updated_at")),
        temporal=TemporalContract(
            feature_axis="system_time", feature_as_of="per_row",
            label_axis="none", corpus_as_of=BUILD_TIME,
            source_snapshots=(SNAPSHOT,), pin_strength="observational"),
        shape=Shape(
            kind="olap",
            consumer=Consumer(framework="duckdb", version="1.4.2"),
            columns=(
                Column(path="party.party_id", type="string",
                       source=ColumnSource(entity="party", field="party_id",
                                           relation="cohort_key")),
                Column(path="party.updated_at", type="timestamp",
                       source=ColumnSource(entity="party", field="updated_at",
                                           relation="decision_time")),
                Column(path="party.date_of_birth", type="date",
                       source=ColumnSource(entity="party", field="date_of_birth",
                                           relation="cohort_row",
                                           system_time_field="updated_at")),
            ),
            emit_missing_reason=True),
        policy=MaterialisationPolicy(
            purpose="kyc.screening",
            principal=PolicyPrincipal(actor="orchestrator@walnut", on_behalf_of="fcc-3",
                                      grant_id="grant-fcc-3"),
            lawful_basis="legal obligation - AML/CFT sanctions screening",
            egress="restricted_internal",
            bundle_version=POLICY_BUNDLE_VERSION),
        determinism=Determinism(seed=20260816, row_order=("party.party_id",),
                                executor=Executor(backend="sqlite 3.45 / databridge-adapter",
                                                  materialiser_version=EXECUTOR_VERSION),
                                split=Split(strategy="none")),
        provenance=Provenance(mode="full", require_pointer_for_every_cell=True),
        output=Output(format="jsonl", metadata="none"),
        packs=("finance@2",),
    )
    base.update(overrides)
    return MaterialisationPlan(**base)
