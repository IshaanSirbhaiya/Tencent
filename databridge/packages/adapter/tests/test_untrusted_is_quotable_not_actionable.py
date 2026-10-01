"""D3, both halves: an untrusted value may not parameterise, but it CAN be re-resolved (§4, §11.5).

The spec is emphatic that an earlier draft got this exactly backwards by screening ``req.resolve``:

* screening `resolve` **over-blocks** - it makes every source document and every email impossible
  to re-resolve or verify through the advertised contract, and *using a locator to retrieve bytes
  is not the same as letting the bytes decide something*;
* screening only `resolve` **under-protects** - a caller can read an untrusted value, stringify
  it, and drop it into a filter, where no pointer accompanies it and a pointer-list check sees
  nothing.

Both halves are the test, per §11.5.

The last section is the **honest gap**: taint does not survive string operations, so `screen_sinks`
is a second line and not the control. That is asserted rather than hidden, because a security
property everybody believes in and nobody has is worse than one nobody claims.
"""
from __future__ import annotations

import pytest

from databridge_adapter import (
    ADAPTER_SINKS,
    TRUSTED_SINKS,
    CapabilityRequest,
    Projection,
    RegistryTaintMap,
    UntrustedInControlFlow,
    screen_sinks,
)
from databridge_core import GroundedValue, SourcePointer, Trust

INJECTED = "party'; DROP TABLE party--"


def untrusted_pointer(column="notes") -> SourcePointer:
    """A free-text column any counterparty can write. Trust is per FIELD, not per source."""
    return SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                         column=column, row_id="P-1", trust=Trust.UNTRUSTED)


def tainted(value) -> RegistryTaintMap:
    taint = RegistryTaintMap()
    taint.register_value(GroundedValue(value=value, pointer=untrusted_pointer()))
    return taint


# --- half one: untrusted values may not parameterise ----------------------------------

def test_an_untrusted_value_may_not_become_a_filter_parameter(request_factory):
    req = request_factory(params={"name": INJECTED})
    with pytest.raises(UntrustedInControlFlow, match="predicate.params"):
        screen_sinks(req, tainted(INJECTED))


def test_an_untrusted_value_may_not_select_the_capability(request_factory):
    req = request_factory(capability="row_read")
    with pytest.raises(UntrustedInControlFlow, match="capability"):
        screen_sinks(req, tainted("row_read"))


def test_an_untrusted_value_may_not_choose_what_is_read(request_factory):
    req = request_factory(projection=Projection(entity="party", fields=("party_id",)),
                          order_by=("party_id",))
    with pytest.raises(UntrustedInControlFlow, match="projection.entity"):
        screen_sinks(req, tainted("party"))


def test_an_untrusted_value_may_not_choose_the_columns(request_factory):
    req = request_factory(projection=Projection(entity="party", fields=("party_id", "notes")),
                          order_by=("party_id",))
    with pytest.raises(UntrustedInControlFlow, match="projection.fields"):
        screen_sinks(req, tainted("notes"))


def test_a_clean_request_passes_the_screen(req):
    assert screen_sinks(req, tainted("something else")) is req


def test_the_screen_returns_the_request_it_screened(req):
    """So a caller cannot screen one object and pass another onward."""
    assert screen_sinks(req, RegistryTaintMap()) is req


# --- half two: the SAME untrusted pointer may be re-resolved --------------------------

def test_an_untrusted_pointer_may_still_be_re_resolved(crawler):
    """Refusing this would make every document and every email unverifiable through the
    advertised contract. Retrieval is not action."""
    ptr = untrusted_pointer()
    req = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                            projection=Projection(entity="party", fields=("notes",)),
                            resolve=(ptr,))
    taint = RegistryTaintMap()
    taint.register(ptr, ptr.trust)              # the pointer itself
    taint.register(ptr.row_id, ptr.trust)       # and the row identity it carries
    assert screen_sinks(req, taint) is req


def test_resolve_is_not_a_sink():
    assert "resolve" not in TRUSTED_SINKS
    assert "resolve" not in ADAPTER_SINKS


def test_the_business_plane_sinks_are_listed_but_not_screened_here():
    """Appendix A calls the trusted-sink inventory for the business plane an open item. Dropping
    those entries from the list would hide the gap rather than name it."""
    assert "action.arguments" in TRUSTED_SINKS
    assert "prompt.instruction" in TRUSTED_SINKS
    assert not {"action.arguments", "prompt.instruction"} & ADAPTER_SINKS


# --- trust labelling ------------------------------------------------------------------

def test_an_unlabelled_pointer_registers_as_tainted():
    """'Never considered' is not evidence of safety - matching `databridge_core.trust_lub`."""
    unlabelled = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                               column="notes", row_id="P-1")
    assert unlabelled.trust is None
    taint = RegistryTaintMap()
    taint.register_value(GroundedValue(value="mid-migration", pointer=unlabelled))
    assert taint.of("mid-migration") is Trust.UNTRUSTED


def test_a_trusted_value_is_not_registered():
    taint = RegistryTaintMap()
    taint.register("Acme", Trust.TRUSTED)
    assert len(taint) == 0
    assert taint.of("Acme") is Trust.TRUSTED


def test_registering_an_unhashable_value_fails_loudly():
    """A taint that fails to record is worse than one never attempted: the caller believes it
    recorded."""
    with pytest.raises(TypeError, match="unhashable"):
        RegistryTaintMap().register(["a"], Trust.UNTRUSTED)


# --- the honest gap --------------------------------------------------------------------

def test_taint_does_not_survive_string_operations(request_factory):
    """**A known, named gap.** `ADAPTER_AGENT_SPEC` Appendix A lists `TaintMap` as undefined and
    says plainly that without value-level taint lifetime, D3 is a naming convention rather than a
    control.

    This test asserts the gap so it cannot be quietly believed away: concatenation produces an
    object the registry has never seen, and `screen_sinks` passes it. Closing this needs a
    tainted-string wrapper threaded through every unwrap site, or a quarantined interpreter in
    CaMeL's sense. Neither is Phase 3A. If a future change makes this test FAIL, that is good
    news - delete it and say so in the phase log.
    """
    taint = tainted(INJECTED)
    assert taint.of(INJECTED) is Trust.UNTRUSTED, "the value itself IS caught"

    # ...but anything DERIVED from it is a value the registry has never seen.
    laundered = f"%{INJECTED}%"
    assert taint.of(laundered) is Trust.TRUSTED, "the gap this test documents"
    screen_sinks(request_factory(params={"name": laundered}), taint)   # passes; it should not


def test_an_unregistered_value_reads_as_trusted():
    """Fail-OPEN, deliberately and visibly: this is a registry of observations, not a lattice over
    the value space. Every literal an orchestrator writes is unregistered, so the alternative
    default would refuse every request ever made."""
    assert RegistryTaintMap().of("anything at all") is Trust.TRUSTED
