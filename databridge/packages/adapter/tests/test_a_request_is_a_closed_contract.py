"""The request is closed: what it refuses is the point (spine D2, §2, §6, §7).

`CapabilityRequest` is the ONLY orchestrator-to-adapter message. Natural language, raw SQL and
per-source RPC are all rejected for the same three reasons at once - unhashable, unverifiable, no
cache key - so everything that could reintroduce them by the back door has to fail here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from databridge_adapter import (
    CapabilityRequest,
    Entitlement,
    Principal,
    Projection,
    UnorderedPointeredRead,
)
from databridge_core import SourcePointer, Trust


# --- the projection ------------------------------------------------------------------

def test_a_projection_must_name_at_least_one_field():
    """An empty projection has nothing to hash, so nothing it returned could be verified."""
    with pytest.raises(ValidationError):
        Projection(entity="party", fields=())


def test_a_projection_may_not_repeat_a_field():
    with pytest.raises(ValidationError, match="duplicate"):
        Projection(entity="party", fields=("party_id", "party_id"))


@pytest.mark.parametrize("bad", ["Party", "party id", "1party", "party;drop", ""])
def test_a_projection_entity_must_be_an_identifier(bad):
    with pytest.raises(ValidationError):
        Projection(entity=bad, fields=("a",))


def test_a_projection_field_path_names_the_entity():
    assert Projection(entity="party", fields=("notes",)).path("notes") == "party.notes"


# --- order_by (§7) -------------------------------------------------------------------

def test_a_discovery_read_without_order_by_is_refused(request_factory):
    """`row_ordinal` over an unordered result is not a stable citation."""
    with pytest.raises(UnorderedPointeredRead):
        request_factory(order_by=())


def test_the_refusal_keeps_its_type_through_pydantic(request_factory):
    """It is raised inside a validator, and Pydantic must not flatten it into ValidationError -
    the caller's response to 'add order_by' is specific and needs the type to dispatch on."""
    with pytest.raises(UnorderedPointeredRead) as exc:
        request_factory(order_by=())
    assert not isinstance(exc.value, ValidationError)


def test_a_resolve_only_request_needs_no_order_by(crawler):
    """Re-fetching identified pointers mints no ordinals, so the requirement does not apply."""
    ptr = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                        column="notes", row_id="P-1", trust=Trust.UNTRUSTED)
    req = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                            projection=Projection(entity="party", fields=("notes",)),
                            resolve=(ptr,))
    assert req.order_by == ()


def test_order_by_must_name_a_projected_field(request_factory):
    """You cannot order by what you did not read: a projection-only schema hash cannot see the
    drift of a column that was never projected (D6)."""
    with pytest.raises(ValidationError, match="not in the projection"):
        request_factory(projection=Projection(entity="party", fields=("party_id",)),
                        order_by=("legal_name",))


def test_descending_order_is_expressed_with_a_leading_minus(request_factory):
    req = request_factory(projection=Projection(entity="party", fields=("party_id",)),
                          order_by=("-party_id",))
    assert req.order_by == ("-party_id",)


# --- params --------------------------------------------------------------------------

def test_params_accept_a_mapping_and_read_back_as_one(request_factory):
    req = request_factory(params={"b": 2, "a": "x"})
    assert dict(req.param_map) == {"a": "x", "b": 2}
    # Stored sorted, so two spellings of one binding set cannot fork the hash.
    assert req.params == (("a", "x"), ("b", 2))


def test_the_param_map_is_a_view_over_a_copy(request_factory):
    req = request_factory(params={"a": 1})
    with pytest.raises(TypeError):
        req.param_map["a"] = 2                                # type: ignore[index]


@pytest.mark.parametrize("bad", ["A", "1a", "a b", "a;b"])
def test_a_parameter_name_must_be_an_identifier(request_factory, bad):
    with pytest.raises(ValidationError):
        request_factory(params={bad: 1})


def test_a_container_parameter_is_refused(request_factory):
    """A nested structure has no single canonical form, which is where a 'deterministic' hash
    stops being deterministic."""
    with pytest.raises(Exception):
        request_factory(params={"ids": [1, 2, 3]}).read_hash


# --- the rest of the surface ---------------------------------------------------------

def test_limit_is_bounded(request_factory):
    with pytest.raises(ValidationError):
        request_factory(limit=0)
    with pytest.raises(ValidationError):
        request_factory(limit=100_001)


def test_an_unknown_field_is_refused(request_factory):
    """`extra="forbid"`: a proposed-but-absent field must fail at the call site, not vanish."""
    with pytest.raises(ValidationError):
        request_factory(sql="SELECT 1")


def test_a_request_is_frozen(req):
    with pytest.raises(ValidationError):
        req.limit = 1


def test_resolve_may_not_carry_another_sources_pointer(crawler):
    """One request reaches one adapter; a pointer for another source has no path to be
    re-resolved by this one."""
    other = SourcePointer(source_id="crm", kind="table_cell", table="contact",
                          column="email", row_id="C-1", trust=Trust.UNTRUSTED)
    with pytest.raises(ValidationError, match="targets"):
        CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                          projection=Projection(entity="party", fields=("notes",)),
                          resolve=(other,))


@pytest.mark.parametrize("bad", ["Row_Read", "row read", "row;read", ""])
def test_a_capability_must_be_an_identifier(request_factory, bad):
    with pytest.raises(ValidationError):
        request_factory(capability=bad)


def test_mode_is_a_closed_pair(request_factory):
    with pytest.raises(ValidationError):
        request_factory(mode="admin")


# --- entitlement ---------------------------------------------------------------------

def test_an_entitlement_needs_a_policy_bundle_version():
    with pytest.raises(ValidationError):
        Entitlement(policy_bundle_version="   ")


def test_entitlement_roles_are_order_and_duplicate_insensitive():
    a = Entitlement(roles=("b", "a", "a"), policy_bundle_version="v1")
    b = Entitlement(roles=("a", "b"), policy_bundle_version="v1")
    assert a.hash == b.hash


def test_an_actor_must_be_named(packs_purposes):
    with pytest.raises(ValidationError):
        Principal(actor="  ", on_behalf_of=None, purpose="finance.graph_build")


def test_as_of_is_normalised_to_utc(request_factory):
    from datetime import timedelta
    sgt = timezone(timedelta(hours=8))
    req = request_factory(as_of=datetime(2026, 8, 15, 20, 0, tzinfo=sgt))
    assert req.as_of == datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
