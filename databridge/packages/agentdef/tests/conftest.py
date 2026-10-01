"""Shared fixtures for the agent-plane tests.

**Import bootstrap.** This package and `packages/adapter` are now BOTH on the root
`pythonpath` - promoted (with `apps/gateway/serve.py::_PACKAGES`, which
`apps/gateway/tests/test_serve_path.py` asserts agrees) at the point the gateway's `/builder`
first called the compiler. The `sys.path` insert below is therefore redundant in a normal run and
kept anyway: it is what makes `python -m pytest packages/agentdef` work from inside the package
directory, and it costs nothing when the entry is already there.

**The vocabulary and the catalogue are bound EXPLICITLY**, for the reason the adapter's conftest
gives: a test that depended on which packs happen to be enabled in ``packs/deployment.yaml`` would
start failing the day someone enables another pack, and the failure would say nothing about the
compiler. `packs/deployment.yaml` currently enables `healthcare` alone, and both declarations
under test are finance and government - which is itself the point of `compile_agent` defaulting to
the DECLARING pack's vocabulary rather than to the deployment binding.
"""
from __future__ import annotations

import sys
from copy import deepcopy

from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_PACKAGES = _PACKAGE_ROOT.parent
for _p in (_PACKAGE_ROOT, _PACKAGES / "adapter"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from databridge_adapter.purposes import purpose_vocabulary          # noqa: E402
from databridge_agentdef import (                                   # noqa: E402
    AdapterCatalogue,
    AgentDeclaration,
    databridge_root,
    load_catalogue,
    load_declaration,
    packs_root,
    parse_declaration,
)
from databridge_agentdef.declaration import normalised_text, text_digest   # noqa: E402

# Constants live in `agentdef_testing` rather than here: the repo runs ONE pytest session over
# many packages, several of which have a `conftest.py`, so `from conftest import ...` resolves to
# whichever one landed on sys.path first. That is not a hypothetical - it is how this suite first
# broke.
from agentdef_testing import (                                      # noqa: E402
    FINANCE_AGENT,
    FROZEN_NOW,
    GOVERNMENT_AGENT,
    declaration_path,
    now,
)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return databridge_root()


@pytest.fixture(scope="session")
def packs_dir() -> Path:
    return packs_root()


@pytest.fixture(scope="session")
def catalogue() -> AdapterCatalogue:
    """What the finance and government estates advertise. Loaded once; sealed."""
    return load_catalogue(["finance", "government"])


@pytest.fixture(scope="session")
def finance_purposes():
    return purpose_vocabulary(["finance"], packs_root())


@pytest.fixture(scope="session")
def government_purposes():
    return purpose_vocabulary(["government"], packs_root())


@pytest.fixture(scope="session")
def finance_declaration() -> AgentDeclaration:
    return load_declaration(declaration_path("finance", FINANCE_AGENT))


@pytest.fixture(scope="session")
def government_declaration() -> AgentDeclaration:
    return load_declaration(declaration_path("government", GOVERNMENT_AGENT))


def _raw(pack: str, agent: str) -> dict:
    return yaml.safe_load(normalised_text(declaration_path(pack, agent)))


@pytest.fixture
def mutate() -> Callable[..., AgentDeclaration]:
    """Load a real declaration, mutate its parsed YAML, and re-parse it.

    Every negative test in this package starts from a declaration that **actually compiles** and
    breaks exactly one thing. A hand-written broken fixture proves only that a hand-written broken
    fixture is refused; mutating the real one proves the check is what stands between this file
    and a grant.
    """

    def _mutate(pack: str, agent: str, change: Callable[[dict], Any]) -> AgentDeclaration:
        raw = deepcopy(_raw(pack, agent))
        change(raw)
        path = declaration_path(pack, agent)
        return parse_declaration(
            raw, where=f"<mutated {path.name}>",
            declared_in=f"packs/{pack}/agents/{agent}.yaml",
            declaration_digest=text_digest(str(raw)))

    return _mutate
