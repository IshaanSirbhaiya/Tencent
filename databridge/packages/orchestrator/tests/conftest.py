"""Fixtures for the orchestrator's own tests: a real two-source fleet over the finance estate.

Reuses exactly what `apps/gateway/databridge_gateway/verify_fleet.py` reuses —
`databridge_adapter.testing.estate.load_finance_estate` for `core_banking`, the estate's own
`roles.yaml`/`purposes.yaml`, and a `PolicyEngine` built from both — plus a `MailboxCore` over the
same estate's `mailbox/` drop, which nothing in `apps/gateway` wires yet. Two real adapters under
one real `PolicyEngine` is what makes the fan-out genuinely multi-agent rather than multi-branch:
`core_banking.party`/`facility` grants to the credit analyst under `lending.underwriting`, and
`mailbox.message` is compliance-only (`roles.yaml`: "email bodies are readable HERE AND NOWHERE
ELSE") — so the same question asked as the analyst answers from one source and is refused by the
other, which is the orchestrator's refusal path exercised for real rather than mocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "adapter"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from databridge_adapter import (                        # noqa: E402
    Budget,
    PolicyEngine,
    Principal,
    PrincipalBinding,
    load_purpose_bindings,
    load_role_model,
    purpose_vocabulary,
    use_purposes,
)
from databridge_adapter.capture import CaptureStore                       # noqa: E402
from databridge_adapter.sources.mailbox import MailboxCore                 # noqa: E402
from databridge_adapter.sources.postgres import PostgresCore               # noqa: E402
from databridge_adapter.testing.estate import (                            # noqa: E402
    ROLES_YAML,
    SOURCES_YAML,
    load_finance_estate,
)

from databridge_orchestrator import AdapterHandle, Orchestrator            # noqa: E402

#: packages/orchestrator/tests/ -> packages/orchestrator -> packages -> databridge/ -> packs/
DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
PACKS_ROOT = DATABRIDGE_ROOT / "packs"

DIRECTORY = {
    "analyst-7": PrincipalBinding(roles=("credit_analyst",)),
    "fcc-3": PrincipalBinding(roles=("compliance_officer",)),
    "orchestrator@walnut": PrincipalBinding(roles=("credit_analyst",)),
}


@pytest.fixture(scope="session")
def packs_purposes():
    vocab = purpose_vocabulary(("finance",), PACKS_ROOT)
    with use_purposes(vocab):
        yield vocab


@pytest.fixture(scope="session")
def finance_engine(tmp_path_factory, packs_purposes):
    engine = load_finance_estate(tmp_path_factory.mktemp("orchestrator-finance-estate"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def policy(packs_purposes) -> PolicyEngine:
    role_model = load_role_model(ROLES_YAML)
    binding = load_purpose_bindings(role_model.estate, known_estate_purposes=role_model.estate_purposes)
    return PolicyEngine(role_model=role_model, purpose_binding=binding, directory=DIRECTORY,
                        default_budget=Budget(max_rows=5000))


@pytest.fixture(scope="session")
def core_banking(finance_engine, policy) -> PostgresCore:
    return PostgresCore(source_id="core_banking", engine=finance_engine, policy=policy,
                        sources_yaml=SOURCES_YAML)


@pytest.fixture(scope="session")
def mailbox(policy, tmp_path_factory) -> MailboxCore:
    store = CaptureStore(root=tmp_path_factory.mktemp("orchestrator-mailbox-capture"))
    return MailboxCore(source_id="mailbox", policy=policy, capture=store,
                       sources_yaml=SOURCES_YAML, register_taint=False)


@pytest.fixture
def handles(core_banking, mailbox) -> list[AdapterHandle]:
    return [
        AdapterHandle(source_id="core_banking", adapter=core_banking,
                      description=core_banking.describe()),
        AdapterHandle(source_id="mailbox", adapter=mailbox, description=mailbox.describe()),
    ]


@pytest.fixture
def analyst(packs_purposes) -> Principal:
    """A credit analyst under `lending.underwriting` — reaches `core_banking`, refused on
    `mailbox` (compliance-only)."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                     purpose="lending.underwriting", grant_id="grant-analyst-7")


@pytest.fixture
def compliance(packs_purposes) -> Principal:
    """The one role/purpose pair that may read the mailbox at all."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="fcc-3",
                     purpose="kyc.screening", grant_id="grant-fcc-3")


def make_orchestrator(handles, harness) -> Orchestrator:
    return Orchestrator(handles, harness=harness)
