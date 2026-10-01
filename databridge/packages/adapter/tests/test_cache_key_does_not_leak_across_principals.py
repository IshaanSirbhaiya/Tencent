"""One hash leaks; that is why there are two (§2a).

An earlier draft of the spec used a single `request_hash` that excluded the principal but included
`purpose`, and used it as BOTH the pointer identity and the cache key. Two principals with the
same purpose but different grants are entitled to different rows and columns, and they would share
a cache entry. The withheld set is principal-shaped, so the cache must be too.

The other half of the guard tuple is R10's: a cached answer whose underlying pointer has moved is
not a cache hit, it is **a wrong answer with a citation** - which is the single failure mode this
system exists to prevent. So the snapshot and the domain-pack version are in the key.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from databridge_adapter import Entitlement, Principal
from databridge_core import SnapshotIdentity

AS_OF = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _snapshot(req, content_hash="ch") -> SnapshotIdentity:
    return SnapshotIdentity(source_id=req.source_id, as_of=AS_OF, content_hash=content_hash,
                            schema_hash="sh", query_hash=req.read_hash)


def _key(req, *, entitlement=None, snapshot=None, pack_version="finance@1.0") -> str:
    return req.cache_key(
        entitlement=entitlement or Entitlement(policy_bundle_version="pb-1"),
        snapshot=snapshot or _snapshot(req),
        pack_version=pack_version)


def test_same_purpose_different_grant_do_not_share_a_cache_entry(request_factory, underwriter):
    """The data-leak path the two-hash split exists to close."""
    a = request_factory(principal=underwriter)
    b = request_factory(principal=Principal(actor=underwriter.actor,
                                            on_behalf_of=underwriter.on_behalf_of,
                                            purpose=underwriter.purpose,
                                            grant_id="grant-someone-else"))
    assert a.read_hash == b.read_hash, "the READ is the same; only entitlement differs"
    assert _key(a, entitlement=Entitlement(grant_id="grant-abc", policy_bundle_version="pb-1")) \
        != _key(b, entitlement=Entitlement(grant_id="grant-someone-else",
                                           policy_bundle_version="pb-1"))


def test_different_purposes_do_not_share_a_cache_entry(request_factory, crawler, packs_purposes):
    a = request_factory(principal=crawler)
    b = request_factory(principal=Principal(actor=crawler.actor, on_behalf_of=None,
                                            purpose="finance.pointer_verification"))
    assert a.read_hash == b.read_hash
    assert _key(a) != _key(b)


def test_a_redeployed_policy_bundle_invalidates_the_cache(req):
    assert _key(req, entitlement=Entitlement(policy_bundle_version="pb-1")) \
        != _key(req, entitlement=Entitlement(policy_bundle_version="pb-2"))


def test_a_moved_snapshot_is_not_a_cache_hit(req):
    assert _key(req, snapshot=_snapshot(req, "ch-1")) != _key(req, snapshot=_snapshot(req, "ch-2"))


def test_a_new_pack_version_invalidates_the_cache(req):
    """The domain pack decides what a field MEANS, so two pack versions are two answers."""
    assert _key(req, pack_version="finance@1.0") != _key(req, pack_version="finance@1.1")


def test_a_blank_pack_version_is_refused(req):
    with pytest.raises(ValueError, match="pack_version"):
        _key(req, pack_version="  ")


def test_the_cache_key_is_not_the_read_hash(req):
    assert _key(req) != req.read_hash


def test_roles_participate_in_the_cache_key(req):
    a = Entitlement(roles=("reader",), policy_bundle_version="pb-1")
    b = Entitlement(roles=("reader", "auditor"), policy_bundle_version="pb-1")
    assert _key(req, entitlement=a) != _key(req, entitlement=b)
