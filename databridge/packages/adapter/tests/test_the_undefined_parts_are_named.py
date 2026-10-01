"""Appendix A, honestly: what is stubbed says so, and the contract package stays a contract.

`ADAPTER_AGENT_SPEC` Appendix A is a list of holes "so nobody discovers them by walking into one".
Three of them land in this package's scope, and each is handled the same way: define it minimally
where the shape is genuinely settled, and otherwise leave a NAMED stub that refuses - never a
permissive default that would pass every happy-path test ever written against it.

| Appendix A item | What Phase 3A did |
|---|---|
| predicate grammar + canonicalisation | refused at construction (`PredicateGrammarNotDefined`) |
| `TaintMap` value-level lifetime | partial, with the gap asserted in the taint tests |
| Policy->Core authorisation + compile-down | `NotYetSpecifiedDecisionTable`, raises |

The last test is the one that keeps the rest true: **no I/O in this package.** The layering that
makes an adapter's output mechanically verifiable depends on the contract being pure; the moment a
driver import appears here, "the Shell has no handle to a driver" becomes a sentence rather than a
structure.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import databridge_adapter
from databridge_adapter import (
    DecisionTable,
    NotYetSpecifiedDecisionTable,
    PolicyDecision,
    PredicateGrammarNotDefined,
    canonical_ast,
)

PACKAGE_DIR = Path(databridge_adapter.__file__).resolve().parent


# --- the predicate grammar (§6) ---------------------------------------------------------

def test_a_predicate_is_refused_and_the_refusal_says_what_is_missing():
    with pytest.raises(PredicateGrammarNotDefined) as exc:
        canonical_ast("party.party_id == :pid", ())
    message = str(exc.value)
    for expected in ("closed grammar", "allowlist", "conformance", "named query"):
        assert expected in message


def test_params_alone_are_canonicalised_because_named_queries_need_them():
    """§6: 'an adapter accepts predicate=None and a fixed set of pack-declared named queries'."""
    ast_form = canonical_ast(None, (("since", "2026-01-01"),))
    assert ast_form["predicate"] is None
    assert ast_form["params"] == {"since": {"t": "str", "v": "2026-01-01"}}


# --- the Policy->Core protocol (Appendix A) -----------------------------------------------

def test_the_decision_table_stub_refuses_rather_than_permitting(crawler):
    """No permissive default: a table that answered 'not withheld' would be a policy engine that
    permits everything wearing the name of one that does not."""
    with pytest.raises(NotImplementedError) as exc:
        NotYetSpecifiedDecisionTable().decide("party", "notes", {"notes": "x"}, crawler)
    assert "Appendix A" in str(exc.value)
    assert "3C" in str(exc.value)


def test_the_stub_conforms_to_the_protocol_it_stubs():
    """So that replacing it in 3C is a substitution, not a redesign."""
    assert isinstance(NotYetSpecifiedDecisionTable(), DecisionTable)


def test_the_stub_is_not_load_bearing():
    """Nothing in the package imports or instantiates it. If that changes, this package has
    quietly acquired a policy engine that refuses everything."""
    users = []
    for path in PACKAGE_DIR.glob("*.py"):
        if path.name in ("policy.py", "__init__.py"):
            continue
        if "NotYetSpecifiedDecisionTable" in path.read_text(encoding="utf-8"):
            users.append(path.name)
    assert users == []


def test_a_denial_must_be_attributable():
    with pytest.raises(ValueError, match="policy that made it"):
        PolicyDecision(withheld=True)


def test_a_permit_carries_no_denial_reason():
    with pytest.raises(ValueError, match="attribute a DENIAL"):
        PolicyDecision(withheld=False, policy_id="pol-7", reason_code="RESTRICTED")


def test_a_denial_becomes_a_withholding_record_that_carries_the_purpose(crawler):
    decision = PolicyDecision(withheld=True, policy_id="pol-7", reason_code="RESTRICTED",
                              appealable=True)
    record = decision.to_record(field_path="party.notes", principal=crawler,
                               policy_version="pb-1")
    assert record.purpose == crawler.purpose        # D5: purpose reaches the ledger entry
    assert record.principal == crawler.actor
    assert record.appealable is True


# --- the package stays a contract ----------------------------------------------------------

#: Anything that would make this package do I/O, hold a credential, or call a model. `yaml` is
#: permitted and `pathlib` is used, but only to read the pack's purpose vocabulary - which is
#: configuration, not a source.
FORBIDDEN_IMPORTS = frozenset({
    "socket", "http", "urllib", "requests", "httpx", "aiohttp",
    "psycopg", "psycopg2", "sqlite3", "sqlalchemy", "pymysql", "duckdb",
    "boto3", "googleapiclient", "google",
    "anthropic", "openai", "litellm", "transformers",
    "subprocess", "asyncio", "threading",
})

#: `threading` is used for the purpose-vocabulary cache lock, which is not I/O.
ALLOWED_EXCEPTIONS = {("purposes.py", "threading")}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_contract_package_does_no_io():
    offenders = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        for name in _top_level_imports(path) & FORBIDDEN_IMPORTS:
            if (path.name, name) in ALLOWED_EXCEPTIONS:
                continue
            offenders.append(f"{path.name}: {name}")
    assert offenders == [], (
        "packages/adapter is the CONTRACT. A driver, a socket or a model client here breaks the "
        "layering that makes an adapter's output mechanically verifiable - the real Core is 3B")


def test_the_contract_package_imports_no_vendor_sdk():
    """Repo invariant (P2): LLM access only through packages/harness."""
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import anthropic" not in text
        assert "import openai" not in text
