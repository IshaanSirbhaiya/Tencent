"""Shared fixtures for the adapter contract tests.

The purpose vocabulary is bound EXPLICITLY rather than left to the deployment binding, for the
same reason `databridge_graph`'s tests bind a deployment: a contract test that depended on which
packs happen to be enabled in ``packs/deployment.yaml`` would start failing the day someone
enables another pack, and the failure would say nothing about the contract.

One test module deliberately does NOT use these fixtures - `test_purpose_is_pack_declared.py`
checks the default resolution path itself.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# --- import bootstrap, and why it is here rather than in the root pyproject -------------
#
# Every other package is on pytest's `pythonpath` in `databridge/pyproject.toml`. This one is not,
# for a reason that is about sequencing rather than taste: `apps/gateway/tests/test_serve_path.py`
# asserts that everything on the pytest path is ALSO on `serve.py`'s `_PACKAGES` - a guard added
# after a green suite shipped a server that would not boot. Adding `packages/adapter` to the root
# path therefore requires editing `apps/gateway/serve.py` in the same change, and workstream 3A's
# scope is the new package only.
#
# So: `packages/adapter/tests` is on `testpaths` (the suite collects these tests) and the package
# is put on `sys.path` here. **Workstream 3B should promote it** - add "packages/adapter" to the
# root `pythonpath` and to `serve.py::_PACKAGES` together - at the point something outside this
# package first imports it. Until then nothing does, and this is the smaller footprint.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from databridge_adapter import (            # noqa: E402  (after the bootstrap, necessarily)
    CapabilityRequest,
    Principal,
    Projection,
    purpose_vocabulary,
    use_purposes,
)

#: packages/adapter/tests/ -> packages/adapter -> packages -> databridge/ -> packs/
PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"

#: Every pack on disk. The tests draw purposes from whichever pack fits the example; binding all
#: three also exercises the cross-pack uniqueness rule on real files.
ALL_PACKS = ("finance", "government", "healthcare")


@pytest.fixture
def packs_root() -> Path:
    """The repo's packs directory. A fixture rather than an import, because a test module that
    did ``from conftest import PACKS_ROOT`` would race every other package's conftest.py for the
    module name ``conftest`` on sys.path."""
    return PACKS_ROOT


@pytest.fixture
def all_packs() -> tuple[str, ...]:
    return ALL_PACKS


@pytest.fixture
def packs_purposes():
    vocab = purpose_vocabulary(ALL_PACKS, PACKS_ROOT)
    with use_purposes(vocab):
        yield vocab


@pytest.fixture
def crawler(packs_purposes) -> Principal:
    """A scheduled build-time crawl: no human, not subject-scoped, therefore no grant."""
    return Principal(actor="orchestrator@walnut", on_behalf_of=None,
                     purpose="finance.graph_build")


@pytest.fixture
def underwriter(packs_purposes) -> Principal:
    """A subject-scoped read on behalf of a named human, carrying a grant."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="rm-0042",
                     purpose="lending.underwriting", grant_id="grant-abc")


@pytest.fixture
def projection() -> Projection:
    return Projection(entity="party", fields=("party_id", "legal_name", "notes"))


@pytest.fixture
def request_factory(packs_purposes):
    """Build a valid `CapabilityRequest` with any field overridden."""
    def build(**overrides) -> CapabilityRequest:
        base = dict(
            source_id="core_banking",
            capability="row_read",
            principal=Principal(actor="orchestrator@walnut", on_behalf_of=None,
                                purpose="finance.graph_build"),
            projection=Projection(entity="party", fields=("party_id", "legal_name")),
            order_by=("party_id",),
            limit=500,
        )
        base.update(overrides)
        return CapabilityRequest(**base)
    return build


@pytest.fixture
def req(request_factory) -> CapabilityRequest:
    return request_factory()


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# =======================================================================================
# The finance estate (workstream 3B/3C/3D).
#
# One SQLite database per SESSION, built from the estate's own PostgreSQL DDL and seed, with
# `core_banking` ATTACHed as a real schema — see `estate.py` on why the schema is attached rather
# than stripped. Tests that MUTATE the estate (drift, vanished rows) take `mutable_estate`, which
# builds its own copy: a session-scoped database that one test rewrites is a test suite whose
# results depend on collection order.
# =======================================================================================

from databridge_adapter import (           # noqa: E402  (after the sys.path bootstrap)
    Budget,
    PolicyEngine,
    PrincipalBinding,
    load_purpose_bindings,
    load_role_model,
)
from databridge_adapter.testing.estate import (   # noqa: E402
    ROLES_YAML,
    SOURCES_YAML,
    load_finance_estate,
)


@pytest.fixture(scope="session")
def finance_engine(tmp_path_factory):
    engine = load_finance_estate(tmp_path_factory.mktemp("finance-estate"))
    yield engine
    engine.dispose()


@pytest.fixture
def mutable_estate(tmp_path):
    """A private copy of the estate, for tests that change a value on purpose."""
    engine = load_finance_estate(tmp_path / "mutable")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def role_model():
    return load_role_model(ROLES_YAML)


@pytest.fixture(scope="session")
def sources_yaml():
    return SOURCES_YAML


#: The deployment's principal directory: what the identity provider says about each principal.
#: Deliberately explicit rather than derived from `roles.yaml` — roles.yaml declares what a ROLE
#: may do, and who holds which role is a different system's statement.
DIRECTORY = {
    "rm-0042": PrincipalBinding(roles=("relationship_manager",),
                                attributes=(("rm_code", "RM-007"), ("team", "SG-CORP"),
                                            ("email", "rm0042@walnut.example"))),
    "rm-0099": PrincipalBinding(roles=("relationship_manager",),
                                attributes=(("rm_code", "RM-013"),)),
    "analyst-7": PrincipalBinding(roles=("credit_analyst",)),
    "fcc-3": PrincipalBinding(roles=("compliance_officer",)),
    "quant-2": PrincipalBinding(roles=("portfolio_analyst",)),
    "audit-1": PrincipalBinding(roles=("auditor",)),
    "ops-1": PrincipalBinding(roles=("platform_operator",)),
    "orchestrator@walnut": PrincipalBinding(roles=("credit_analyst",)),
}


@pytest.fixture
def policy(role_model):
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    return PolicyEngine(role_model=role_model, purpose_binding=binding, directory=DIRECTORY,
                        default_budget=Budget(max_rows=5000))


@pytest.fixture
def core(finance_engine, policy, sources_yaml):
    from databridge_adapter.sources.postgres import PostgresCore

    return PostgresCore(source_id="core_banking", engine=finance_engine, policy=policy,
                        sources_yaml=sources_yaml)


@pytest.fixture
def analyst(packs_purposes) -> Principal:
    """A credit analyst under `lending.underwriting` -> the estate's `credit_assessment`."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                     purpose="lending.underwriting", grant_id="grant-analyst-7")


@pytest.fixture
def rm(packs_purposes) -> Principal:
    """RM-007, whose row filter restricts `party` to their own book."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="rm-0042",
                     purpose="lending.underwriting", grant_id="grant-rm-0042")


@pytest.fixture
def compliance(packs_purposes) -> Principal:
    """The only role that may read free text — and therefore the only one that sees an injection."""
    return Principal(actor="orchestrator@walnut", on_behalf_of="fcc-3",
                     purpose="kyc.screening", grant_id="grant-fcc-3")


@pytest.fixture
def party_request(analyst):
    """The workhorse read: three columns of `party`, ordered totally by its primary key."""
    def build(principal=None, **overrides):
        base = dict(
            source_id="core_banking", capability="row_read", principal=principal or analyst,
            projection=Projection(entity="party",
                                  fields=("party_id", "legal_name", "kyc_status"),
                                  key=("party_id",)),
            order_by=("legal_name", "party_id"), limit=25)
        base.update(overrides)
        return CapabilityRequest(**base)
    return build
