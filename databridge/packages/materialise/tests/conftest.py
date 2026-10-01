"""Fixtures for the materialisation tests: the real finance estate, the real adapter, real policy.

**Nothing here is a mock.** The rows come from the estate's own PostgreSQL seed loaded into
SQLite, the reads go through `PostgresCore` and the five-verb port, and the column decisions come
from `fixtures/packs/finance/roles.yaml` evaluated by the real `PolicyEngine`. A materialisation
test against a stub adapter would prove that the materialiser agrees with the stub.

**Import bootstrap.** `packages/materialise` is not on the root `pythonpath` and this package's
tests are not in the root `testpaths`, so `python -m pytest` from `databridge/` does not collect
them and `python -m pytest packages/materialise` does. That is deliberate while three other agents
are editing `packages/adapter/`: adding this package to the root `pyproject.toml` also requires
editing `apps/gateway/serve.py` in the same change (there is a test asserting the two agree), and
neither file is in this workstream's scope. Promote both together when something outside this
package first imports it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_DATABRIDGE = _PACKAGE_ROOT.parents[1]
for _path in (_PACKAGE_ROOT,
              _DATABRIDGE / "packages" / "core",
              _DATABRIDGE / "packages" / "harness",
              _DATABRIDGE / "packages" / "graph",
              _DATABRIDGE / "packages" / "adapter",
              Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from databridge_adapter import (                # noqa: E402  (after the bootstrap, necessarily)
    Budget,
    PolicyEngine,
    Principal,
    PrincipalBinding,
    load_purpose_bindings,
    load_role_model,
    purpose_vocabulary,
    use_purposes,
)
from databridge_materialise import (            # noqa: E402
    AgentCatalogue,
    EntityCatalogue,
    EstateReader,
    SourceSnapshot,
)
from databridge_adapter.testing.estate import ( # noqa: E402
    ROLES_YAML,
    SOURCES_YAML,
    load_finance_estate,
)

PACKS_ROOT = _DATABRIDGE / "packs"
ALL_PACKS = ("finance", "government", "healthcare")

#: A pinned build instant. Every temporal cut in these tests is relative to it, so a test that
#: passed today passes in a year — the alternative is a suite whose maturity gate quietly stops
#: rejecting anything as the estate ages into the past.
BUILD_TIME = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)

#: The seed shards these tests need. `covenant_test` is the worked example (SYSTEM_DESIGN §6.6(a));
#: `facility` carries the free text the chunk corpus reads and the only `updated_at` in the join.
SEED = ("01_party_01.sql", "04_account_01.sql", "05_facility_01.sql",
        "06_facility_account_01.sql", "09_collateral_01.sql", "10_covenant_01.sql",
        "11_covenant_test_01.sql")

#: The deployment's principal directory. Explicit rather than derived from `roles.yaml`: that file
#: says what a ROLE may do, and who holds which role is a different system's statement.
DIRECTORY = {
    "analyst-7": PrincipalBinding(roles=("credit_analyst",)),
    "fcc-3": PrincipalBinding(roles=("compliance_officer",)),
    "rm-0042": PrincipalBinding(roles=("relationship_manager",),
                                attributes=(("rm_code", "RM-007"), ("team", "SG-CORP"),
                                            ("email", "rm0042@walnut.example"))),
    "orchestrator@walnut": PrincipalBinding(roles=("credit_analyst",)),
}


@pytest.fixture(scope="session")
def finance_engine(tmp_path_factory):
    engine = load_finance_estate(tmp_path_factory.mktemp("finance-estate"), tables=SEED)
    yield engine
    engine.dispose()


@pytest.fixture
def packs_purposes():
    vocab = purpose_vocabulary(ALL_PACKS, PACKS_ROOT)
    with use_purposes(vocab):
        yield vocab


@pytest.fixture(scope="session")
def role_model():
    return load_role_model(ROLES_YAML)


@pytest.fixture
def policy(role_model):
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    # A budget large enough for the whole cohort: 2096 covenant tests, and a materialisation that
    # silently truncated at 1000 would produce a plausible dataset from half an estate.
    return PolicyEngine(role_model=role_model, purpose_binding=binding, directory=DIRECTORY,
                        default_budget=Budget(max_rows=200_000))


@pytest.fixture
def core(finance_engine, policy):
    from databridge_adapter.sources.postgres import PostgresCore

    adapter = PostgresCore(source_id="core_banking", engine=finance_engine, policy=policy,
                           sources_yaml=SOURCES_YAML)
    yield adapter


@pytest.fixture
def analyst(packs_purposes) -> Principal:
    """A credit analyst monitoring covenants — the principal a covenant-breach training set is
    assembled for. Subject-scoped, so it carries a grant."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                     purpose="lending.covenant_monitoring", grant_id="grant-analyst-7")


@pytest.fixture
def compliance(packs_purposes) -> Principal:
    """The only role permitted to read free text in this estate, and therefore the only principal
    for whom a chunk corpus over `facility.notes` is readable at all."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="fcc-3",
                     purpose="kyc.screening", grant_id="grant-fcc-3")


@pytest.fixture
def reader(core, policy, analyst) -> EstateReader:
    return EstateReader(core, analyst, policy=policy)


@pytest.fixture
def compliance_reader(core, policy, compliance) -> EstateReader:
    return EstateReader(core, compliance, policy=policy)


@pytest.fixture
def snapshots() -> tuple[SourceSnapshot, ...]:
    """The source-side pins. `T1` because that is what the estate's own `sources.yaml` declares
    for `core_banking` — a monotonic cursor, not a native snapshot."""
    return (SourceSnapshot(source_id="core_banking", tier="T1", as_of=BUILD_TIME,
                           content_hash="sha256:synthetic-fixture", version_id="seed-v2"),)


@pytest.fixture
def catalogue(snapshots) -> AgentCatalogue:
    """What the data agent is shown.

    The two axis declarations are the load-bearing part, and they are stated per entity:

    * `covenant_test` records both — `test_date` is when the test was true in the world and
      `recorded_at` is when the bank learned it. It is bi-temporal in the source.
    * `covenant` records NEITHER. It is a dimension table that is overwritten in place, so it can
      answer "what does the covenant say now" and cannot answer "what did we know then". The
      catalogue says so, and the compiler refuses any feature drawn from it.
    """
    return AgentCatalogue(
        source_id="core_banking",
        packs=("finance@2",),
        purpose="lending.covenant_monitoring",
        lawful_basis="contract - performance of the facility agreement",
        principal_actor="orchestrator@walnut",
        principal_on_behalf_of="analyst-7",
        principal_grant_id="grant-analyst-7",
        entities=(
            EntityCatalogue(
                name="covenant_test",
                fields=("test_id", "covenant_id", "test_date", "observed_value", "breached",
                        "waiver_ref", "recorded_by", "recorded_at"),
                key=("test_id",),
                valid_time_field="test_date",
                system_time_field="recorded_at",
                label_candidates=("breached",),
                feature_candidates=("observed_value", "breached"),
            ),
            EntityCatalogue(
                name="covenant",
                fields=("covenant_id", "facility_id", "covenant_type", "comparison_op",
                        "threshold_value", "unit", "test_frequency", "next_test_date", "status"),
                key=("covenant_id",),
                valid_time_field=None,
                system_time_field=None,       # overwritten in place: no recorded learning instant
                feature_candidates=("threshold_value",),
            ),
        ),
    )


@pytest.fixture
def text_catalogue(snapshots) -> AgentCatalogue:
    """A catalogue for the retrieval shape: `facility.notes`, membership on `facility.updated_at`."""
    return AgentCatalogue(
        source_id="core_banking",
        packs=("finance@2",),
        purpose="kyc.screening",
        lawful_basis="legal obligation - AML/CFT sanctions screening",
        principal_actor="orchestrator@walnut",
        principal_on_behalf_of="fcc-3",
        principal_grant_id="grant-fcc-3",
        entities=(
            EntityCatalogue(
                name="facility",
                fields=("facility_id", "party_id", "facility_type", "status", "updated_at",
                        "notes"),
                key=("facility_id",),
                valid_time_field="start_date",
                system_time_field="updated_at",
                text_field="notes",
            ),
        ),
    )
