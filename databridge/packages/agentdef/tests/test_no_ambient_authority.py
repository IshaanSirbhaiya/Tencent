"""**No ambient authority** (AGENT_PLANE §3).

    "a capability not listed here cannot be requested at runtime, and there is no escalation
     path that adds one without a new pack version."

Two halves, and both are asserted structurally rather than by convention:

* the runtime handle refuses a capability the declaration does not carry, and refuses a purpose
  the declaration does not carry;
* there is no method anywhere that widens the set. Not "present but raising" - absent. The test
  scans the whole public surface, so a widening method added later fails even though nobody put
  its name in a list.
"""
from __future__ import annotations

import inspect
import re

import pytest

from databridge_agentdef import (
    AmbientAuthorityRefused,
    CapabilityGrantSet,
    CompilationRefused,
    CompiledAgent,
    GrantedAuthority,
    Outcome,
    compile_agent,
)
from databridge_agentdef.errors import AgentDefError

from agentdef_testing import now


@pytest.fixture
def authority(finance_declaration, catalogue, finance_purposes) -> GrantedAuthority:
    decision = compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes,
                             now=now)
    return GrantedAuthority.from_decision(decision)


def test_a_declared_capability_resolves_to_its_grant(authority):
    grant = authority.require("core_banking.resolve")
    assert grant.source_id == "core_banking" and grant.capability == "resolve"
    assert grant.purpose == "lending.underwriting"
    assert grant.max_trust == "untrusted"


def test_an_undeclared_capability_cannot_be_requested_at_runtime(authority):
    """The capability exists, the adapter advertises it, and this agent was not granted it."""
    assert "core_banking.snapshot_present" not in authority.capabilities
    with pytest.raises(AmbientAuthorityRefused) as excinfo:
        authority.require("core_banking.snapshot_present")
    message = str(excinfo.value)
    assert "no escalation path" in message
    assert "bumping `version`" in message


def test_an_adapter_the_agent_never_declared_cannot_be_reached(authority):
    with pytest.raises(AmbientAuthorityRefused):
        authority.require("crm.resolve")


def test_a_grant_is_not_transferable_between_purposes(authority):
    """A capability granted for underwriting is not granted for portfolio reporting: the purpose
    is what the ledger records as the lawful basis."""
    with pytest.raises(AmbientAuthorityRefused, match="field of the principal"):
        authority.require("core_banking.resolve", purpose="lending.portfolio_reporting")


def test_the_grant_set_itself_refuses_the_wrong_purpose(authority):
    with pytest.raises(AmbientAuthorityRefused):
        authority.compiled.grants.require("core_banking.resolve", "kyc.onboarding")


#: Anything that would widen an agent's authority in place.
_WIDENING = re.compile(
    r"^(add|grant|escalate|elevate|extend|widen|allow|permit|register|append|insert|update|set_|"
    r"with_capab|enable|attach|merge)")


def test_no_object_in_the_authority_chain_has_a_widening_method():
    """Absent, not present-and-raising. A method that exists and refuses is one somebody will
    eventually make succeed under a flag; a method that does not exist has to be written,
    reviewed and merged first."""
    offenders: list[str] = []
    for cls in (GrantedAuthority, CapabilityGrantSet, CompiledAgent):
        for attr in dir(cls):
            if attr.startswith("_"):
                continue
            member = inspect.getattr_static(cls, attr, None)
            if callable(member) and _WIDENING.match(attr):
                offenders.append(f"{cls.__name__}.{attr}")
    assert not offenders, (
        f"widening method(s) on the authority chain: {offenders}. Widening an agent's authority "
        f"is a new declaration version, reviewed and recompiled - never a call")


def test_the_authority_holds_no_mutable_state(authority):
    """`__slots__` and no setters. There is nothing to change: the authority IS the compiled
    artefact, and a different authority is a different compilation."""
    assert GrantedAuthority.__slots__ == ("_compiled",)
    with pytest.raises(AttributeError):
        authority.purpose = "kyc.onboarding"
    with pytest.raises(AttributeError):
        authority.something_new = 1


def test_the_grant_set_is_a_tuple_not_a_list(authority):
    assert isinstance(authority.compiled.grants.grants, tuple)
    with pytest.raises(TypeError):
        authority.compiled.grants.grants[0] = None                  # type: ignore[index]


def test_widening_requires_a_new_declaration_version(finance_declaration, catalogue,
                                                     finance_purposes):
    """The only escalation path there is: edit the file, bump `version`, recompile.

    The wider agent is a DIFFERENT compiled artefact with a different `compiled_id` and different
    grant ids, so a ledger entry citing the narrow one still means what it meant.
    """
    from dataclasses import replace

    narrow = GrantedAuthority.from_decision(
        compile_agent(finance_declaration, catalogue=catalogue, purposes=finance_purposes,
                      now=now))
    assert "core_banking.snapshot_present" not in narrow.capabilities

    v4 = replace(finance_declaration, version=4, supersedes=3,
                 capabilities=finance_declaration.capabilities + ("core_banking.snapshot_present",))
    wide = GrantedAuthority.from_decision(
        compile_agent(v4, catalogue=catalogue, purposes=finance_purposes, now=now))

    assert "core_banking.snapshot_present" in wide.capabilities
    assert wide.version == 4 and narrow.version == 3
    assert wide.compiled.compiled_id != narrow.compiled.compiled_id
    assert narrow.compiled.grants.capabilities < wide.compiled.grants.capabilities
    # And the old handle is untouched by the new compilation.
    with pytest.raises(AmbientAuthorityRefused):
        narrow.require("core_banking.snapshot_present")


def test_a_refused_declaration_yields_no_authority(mutate, catalogue, finance_purposes):
    decl = mutate("finance", "credit_memo_drafter", lambda raw: raw.pop("citation"))
    decision = compile_agent(decl, catalogue=catalogue, purposes=finance_purposes, now=now)
    assert decision.outcome is Outcome.REFUSED
    with pytest.raises(CompilationRefused):
        GrantedAuthority.from_decision(decision)


def test_authority_cannot_be_built_from_a_declaration(finance_declaration):
    """There is no path from a declaration to authority that skips the compiler."""
    with pytest.raises(AgentDefError, match="built from a CompiledAgent"):
        GrantedAuthority(finance_declaration)                       # type: ignore[arg-type]


# --- the word that must not be conflated ---------------------------------------------------
def test_a_capability_grant_cannot_stand_in_for_a_human_attestation(authority):
    """`CapabilityGrant.grant_id` and `Principal.grant_id` are different things. Passing the first
    where the second belongs would let a compiled artefact replace an in-person attestation."""
    own = next(iter(authority.compiled.grants)).grant_id
    with pytest.raises(AgentDefError, match="attests"):
        authority.principal(actor="orchestrator@walnut", on_behalf_of="staff:ps-0142",
                            subject_grant_id=own)


def test_the_principal_carries_the_declared_purpose(authority, finance_purposes):
    """`lending.underwriting` is declared subject_scoped, so a Principal without a subject-read
    grant is refused by the CONTRACT, one layer down - which is where that rule lives.

    The vocabulary is bound explicitly here because `packs/deployment.yaml` enables `healthcare`
    alone. That is not a workaround: it is the runtime half of D5. A compiled finance grant is
    only USABLE where the finance pack is enabled, and a `Principal` built from one in a
    healthcare-only deployment is refused by `purposes.py` rather than by this package.
    """
    from databridge_adapter.purposes import use_purposes

    with use_purposes(finance_purposes):
        principal = authority.principal(actor="orchestrator@walnut",
                                        on_behalf_of="staff:ps-0142",
                                        subject_grant_id="sgr-2026-08-16-0007")
        assert principal.purpose == "lending.underwriting"
        assert principal.is_subject_scoped
        with pytest.raises(ValueError, match="subject_scoped"):
            authority.principal(actor="orchestrator@walnut", on_behalf_of="staff:ps-0142")


def test_a_compiled_grant_is_not_usable_where_the_pack_is_not_enabled(authority):
    """The other half of the same fact, stated as a test so nobody reads the grant as authority
    on its own: compilation is per pack, and the deployment binding still governs the read."""
    from databridge_adapter.errors import PurposeNotDeclared
    from databridge_adapter.purposes import purpose_vocabulary, use_purposes

    healthcare_only = purpose_vocabulary(["healthcare"], __import__(
        "databridge_agentdef").packs_root())
    with use_purposes(healthcare_only):
        with pytest.raises(PurposeNotDeclared):
            authority.principal(actor="orchestrator@walnut", on_behalf_of=None,
                                subject_grant_id="sgr-2026-08-16-0007")
