"""C0 contract tests: ACL evaluation is fail-closed, the grounding wall loads without the legacy
gateway, and no request shape carries an identity (P8)."""
from __future__ import annotations

import sys

from databridge_ib import acl
from databridge_ib.models import (AclNobody, AskRequest, DirectoryUser, UserKind)


def _u(**kw) -> DirectoryUser:
    base = dict(id="jdoe", name="J", email="jdoe@companya.example", employment="employee",
                groups=("grp-eng",))
    base.update(kw)
    return DirectoryUser(**base)


def test_all_requires_every_branch():
    e = acl.all_of(acl.groups("grp-eng"), acl.users("sara.sec"))
    assert not acl.allows(e, _u())
    assert acl.allows(e, _u(id="sara.sec"))


def test_any_and_kind():
    public_channel = acl.any_of(acl.users("ken.ext"),
                                acl.all_of(acl.AclKind(kinds=(UserKind.MEMBER,)), acl.AclEveryone()))
    assert acl.allows(public_channel, _u())
    assert not acl.allows(public_channel, _u(id="guest1", kind=UserKind.GUEST))
    assert acl.allows(public_channel, _u(id="ken.ext", kind=UserKind.CONNECT))


def test_fail_closed():
    assert not acl.allows(acl.parse("not json"), _u())
    assert isinstance(acl.parse({"t": "mystery"}), AclNobody)
    assert not acl.allows(acl.all_of(), _u())
    assert not acl.allows(acl.any_of(), _u())
    assert not acl.allows(acl.groups("grp-eng"), _u(active=False))


def test_roundtrip():
    e = acl.all_of(acl.groups("b", "a"), acl.any_of(acl.users("x"), acl.AclDomain(domain="companya.example")))
    assert acl.dump(acl.parse(acl.dump(e))) == acl.dump(e)


def test_grounding_loads_without_legacy_gateway():
    from databridge_ib import grounding
    assert grounding.GroundedNarrator.TASK == "brain_query"
    assert "databridge_gateway.main" not in sys.modules


def test_no_identity_in_request_shapes():
    banned = {"user", "principal", "sub", "persona", "user_id", "as_user", "identity"}
    assert not banned & set(AskRequest.model_fields)
