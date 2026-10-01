"""`read_hash` is pointer identity, so it is pinned by golden digests (spine D2, §2a).

``read_hash`` BECOMES ``SnapshotIdentity.query_hash``. That is what makes a citation verifiable by
re-execution rather than by trust: to check a cited value, replay this exact request against the
pinned snapshot and compare digests. The consequence is unusual and it is the reason this file
exists - **if the canonicalisation moves, every pointer already written to the ledger silently
stops verifying.** Not loudly. Silently: the replay produces a different hash, the pointer no
longer matches anything, and the only symptom is an auditor in 2029 being told a determination
cites something unfindable.

So the hashes below are **hard-coded expected digests**. This is the one place in the repo where a
hard-coded value is correct, and the distinction is worth stating precisely: a hardcode is
forbidden when it *replaces* a computation (an input->answer lookup table). Here it *pins* one -
the test still runs the full canonicalisation and compares. A Pydantic upgrade, a dict-ordering
change, a float-formatting difference or a well-meaning refactor of `canonical.py` fails HERE,
where the failure is legible, instead of everywhere at once.

**If one of these fails, do not update the constant.** Either revert the change, or bump
`SCHEME_VERSION` and add the new digest beside the old one, so that pointers written under the old
scheme keep verifying under it.

## Scheme history - and the schema-evolution trap these digests exposed

The digests below are the **v3** values. They moved twice, and the second move is the interesting
one because it was not a bug in this package at all:

* **v1 -> v2** - four canonicalisation defects found by review R-3A: `Decimal.normalize()` under
  the ambient decimal context, scale collapsing, `pointer_digest` going through
  `model_dump(mode="json")`, and the pointer set not being frozen at construction.
* **v2 -> v3** - `packages/core` added `trust` and `capture_digest` to `SourcePointer` while
  closing the trust-laundering paths. Correct change, entirely unrelated to hashing, and it moved
  every digest here - because v2 rendered every non-`None` field of the pointer.

**That is the trap worth writing down.** Under v2, *adding any field to `SourcePointer`
invalidated every pointer identity ever issued*, silently, with a golden test as the only signal -
and the natural reaction to a failing golden test is to re-pin it, which is exactly the action
that loses every stored citation. So v3 does not merely re-pin: `canonical_pointer` now renders
an **allowlist of locator fields**, and
`test_a_new_pointer_field_does_not_move_an_issued_identity` below guards the rule, so the next
field addition is a non-event rather than a silent break.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from databridge_adapter import (
    CapabilityRequest,
    Entitlement,
    PredicateGrammarNotDefined,
    Principal,
    Projection,
    pointer_digest,
    pointer_set_digest,
)
from databridge_core import SnapshotIdentity, SourcePointer, Trust

AS_OF = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
PARAMS = {"since": date(2026, 1, 1), "min_amount": Decimal("1000.50"),
          "active": True, "n": 7, "label": "acme"}

# --- THE GOLDEN DIGESTS. Read the module docstring before touching one. ---------------
#: **Re-pinned 2026-08-16 for SCHEME_VERSION 3 -> 4.**
#:
#: These moved because `_LOCATOR_EXTRAS["email_part"]` gained `char_start`/`char_end`, closing a
#: real collision: two different spans of one email part rendered to an IDENTICAL canonical form,
#: so two distinct citations shared one pointer identity and one cache entry. Reproduced before
#: the change and asserted afterwards in
#: `test_two_spans_of_one_email_part_are_not_one_identity`.
#:
#: The rule for anyone who finds these failing again: **establish WHY the digest moved before
#: touching the constant.** A golden hash exists to make an unintended change loud, and re-pinning
#: it reflexively converts the one alarm that would have caught a silent identity change into a
#: chore. If the locator set changed on purpose, bump `SCHEME_VERSION` and re-pin. If it did not,
#: the bug is upstream and the constant is telling the truth.
GOLDEN_READ_HASH = "439fe83dcb0229dd3f3540e885c6287f5d6c606ad9ef4635593ac30715577b7d"
GOLDEN_RESOLVE_READ_HASH = "9f9406620978f416b755b65b8a71ec073c48fd9b0233c8c64b66a5cecf06ec2b"
GOLDEN_CACHE_KEY = "7d7b0e028b6da89e72c12fe0fe9cd238a6e930597f0f7f56fc5cb3a0cad5f6ff"
GOLDEN_POINTER_SET_DIGEST = "99414207a406bd78ec14c99569e39f8b0988fd4de5c5eaa493fd63842d4802c7"

PROJECTION = Projection(entity="party", fields=("party_id", "legal_name", "notes"),
                        key=("party_id",))


def _pointers() -> tuple[SourcePointer, SourcePointer]:
    return (
        SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                      column="legal_name", row_id="P-1", trust=Trust.TRUSTED),
        SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                      column="notes", row_id="P-2", trust=Trust.UNTRUSTED),
    )


@pytest.fixture
def golden_request(crawler) -> CapabilityRequest:
    return CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=crawler,
        projection=PROJECTION, order_by=("legal_name", "party_id"), as_of=AS_OF, limit=250,
        params=PARAMS,
    )


# --- the pinned values --------------------------------------------------------------

def test_read_hash_matches_the_golden_digest(golden_request):
    assert golden_request.read_hash == GOLDEN_READ_HASH


def test_a_resolve_request_matches_its_golden_digest(crawler):
    """Two resolution requests for different pointers are different reads and must not collide."""
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=crawler,
        projection=PROJECTION, as_of=AS_OF, limit=250, params=PARAMS, resolve=_pointers(),
    )
    assert req.read_hash == GOLDEN_RESOLVE_READ_HASH


def test_pointer_set_digest_matches_the_golden_digest():
    assert pointer_set_digest(_pointers()) == GOLDEN_POINTER_SET_DIGEST


def test_cache_key_matches_the_golden_digest(golden_request):
    # The entitlement's grant must be the PRINCIPAL's grant (review R-3A finding 3), and the
    # golden request is a build-time crawl, which carries none.
    ent = Entitlement(grant_id=None, roles=("underwriter", "reader"),
                      policy_bundle_version="pb-9")
    snap = SnapshotIdentity(source_id="core_banking", as_of=AS_OF, content_hash="ch",
                            schema_hash="sh", query_hash=golden_request.read_hash)
    assert golden_request.cache_key(entitlement=ent, snapshot=snap,
                                    pack_version="finance@1.0") == GOLDEN_CACHE_KEY


def test_the_hash_is_stable_across_processes(tmp_path: Path):
    """A fresh interpreter, a different PYTHONHASHSEED, the same digest.

    Set randomisation explicitly rather than trusting the ambient value: dict ordering and set
    iteration are exactly the two things that would make this hash environment-dependent, and the
    bug would only appear on someone else's machine.
    """
    script = textwrap.dedent(f"""
        import sys, pathlib
        sys.path[:0] = {[str(Path(p).resolve()) for p in
                         ("packages/core", "packages/graph", "packages/harness",
                          "packages/convert", "packages/intake", "packages/adapter")]!r}
        from datetime import date, datetime, timezone
        from decimal import Decimal
        from databridge_adapter import (CapabilityRequest, Principal, Projection,
                                        purpose_vocabulary, use_purposes)
        with use_purposes(purpose_vocabulary(("finance", "government", "healthcare"),
                                             pathlib.Path({str(Path("packs").resolve())!r}))):
            p = Principal(actor="orchestrator@walnut", on_behalf_of=None,
                          purpose="finance.graph_build")
            r = CapabilityRequest(
                source_id="core_banking", capability="row_read", principal=p,
                projection=Projection(entity="party",
                                      fields=("party_id", "legal_name", "notes"),
                                      key=("party_id",)),
                order_by=("legal_name", "party_id"),
                as_of=datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc), limit=250,
                params={{"since": date(2026, 1, 1), "min_amount": Decimal("1000.50"),
                         "active": True, "n": 7, "label": "acme"}})
            print(r.read_hash)
    """)
    path = tmp_path / "hash_probe.py"
    path.write_text(script, encoding="utf-8")
    seen = set()
    for seed in ("0", "1", "12345"):
        out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                             env={**_env(), "PYTHONHASHSEED": seed})
        assert out.returncode == 0, out.stderr
        seen.add(out.stdout.strip())
    assert seen == {GOLDEN_READ_HASH}


def _env() -> dict:
    import os
    return dict(os.environ)


# --- what the hash covers, and what it must not -------------------------------------

def test_read_hash_is_principal_independent(request_factory, crawler, underwriter):
    """A citation must mean the same thing to every reader, or an auditor cannot verify it."""
    a = request_factory(principal=crawler)
    b = request_factory(principal=underwriter)
    assert a.principal != b.principal
    assert a.read_hash == b.read_hash


def test_a_new_pointer_field_does_not_move_an_issued_identity():
    """**The guard on the trap.** Adding a non-locator field to `SourcePointer` must be a
    non-event for every pointer identity already in the ledger.

    Constructed explicitly rather than asserted in prose: two pointers to the SAME cell that
    differ only in metadata *about* the value must digest identically. `trust` is the case that
    actually happened - it is a statement about who could write the bytes, declared in the source
    contract and re-derivable at read time, and a deployment reclassifying a column must not
    invalidate every citation into it.
    """
    a = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                      column="notes", row_id="P-1", trust=Trust.TRUSTED)
    b = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                      column="notes", row_id="P-1", trust=Trust.UNTRUSTED)
    assert pointer_digest(a) == pointer_digest(b), (
        "trust moved a locator identity: a column reclassified by a data owner would silently "
        "invalidate every stored pointer into it")

    # And the converse, so the allowlist has not simply hashed nothing: a different LOCATION is a
    # different identity, and every locator field is live.
    for field, value in (("row_id", "P-2"), ("column", "legal_name"), ("table", "facility"),
                         ("source_id", "crm")):
        moved = a.model_copy(update={field: value})
        assert pointer_digest(moved) != pointer_digest(a), f"{field} is not covered by the digest"

    # `capture_digest` IS a locator - it names WHICH BYTES were captured - so it does move it.
    assert (pointer_digest(a.model_copy(update={"capture_digest": "sha256:abc"}))
            != pointer_digest(a))


def test_a_cited_value_changing_does_not_move_the_pointers_identity():
    """The other half of the same rule, and the reason `snapshot.content_hash` is excluded.

    A pointer's identity is WHERE the value came from. If the digest of the value itself were in
    it, re-resolving a cell whose value had changed would produce a different pointer identity -
    and `verify()` would have nothing to compare, because the pointer it was handed could no
    longer address anything.
    """
    def at(content_hash: str) -> SourcePointer:
        return SourcePointer(
            source_id="core_banking", kind="query_cell", column="legal_name", row_id="P-1",
            row_ordinal=0, table="core_banking.party", query_hash="qh",
            snapshot=SnapshotIdentity(source_id="core_banking", as_of=AS_OF,
                                      content_hash=content_hash, schema_hash="sh",
                                      query_hash="qh"),
            trust=Trust.TRUSTED)

    assert pointer_digest(at("before")) == pointer_digest(at("after"))
    # `row_ordinal` is a hint, not identity (SS7), so it must not move it either.
    assert pointer_digest(at("x").model_copy(update={"row_ordinal": 7})) == pointer_digest(at("x"))


def test_read_hash_ignores_mode(request_factory):
    """build vs query is scheduling, not identity."""
    assert request_factory(mode="build").read_hash == request_factory(mode="query").read_hash


@pytest.mark.parametrize("field,value", [
    ("source_id", "crm"),
    ("capability", "structural_read"),
    ("limit", 501),
    ("as_of", AS_OF),
])
def test_read_hash_changes_with_the_read(request_factory, field, value):
    assert request_factory().read_hash != request_factory(**{field: value}).read_hash


def test_read_hash_changes_with_the_projection(request_factory):
    base = request_factory()
    wider = request_factory(projection=Projection(entity="party",
                                                  fields=("party_id", "legal_name", "notes")))
    reordered = request_factory(projection=Projection(entity="party",
                                                      fields=("legal_name", "party_id")))
    assert base.read_hash != wider.read_hash
    # Field ORDER participates: the projection is ordered, and cells come back positionally.
    assert base.read_hash != reordered.read_hash


def test_read_hash_changes_with_order_by(request_factory):
    a = request_factory(projection=Projection(entity="party", fields=("party_id", "legal_name")),
                        order_by=("party_id",))
    b = request_factory(projection=Projection(entity="party", fields=("party_id", "legal_name")),
                        order_by=("legal_name",))
    assert a.read_hash != b.read_hash


def test_typed_params_do_not_collide(request_factory):
    """``1`` and ``"1"`` are different reads. JSON renders them the same, so scalars are tagged."""
    a = request_factory(params={"n": 1})
    b = request_factory(params={"n": "1"})
    c = request_factory(params={"n": True})
    assert len({a.read_hash, b.read_hash, c.read_hash}) == 3


def test_param_order_does_not_change_the_hash(request_factory):
    a = request_factory(params={"a": 1, "b": 2})
    b = request_factory(params={"b": 2, "a": 1})
    assert a.read_hash == b.read_hash


def test_resolve_pointer_order_does_not_change_the_hash(crawler):
    p1, p2 = _pointers()
    a = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                          projection=PROJECTION, resolve=(p1, p2))
    b = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                          projection=PROJECTION, resolve=(p2, p1))
    assert a.read_hash == b.read_hash


def test_different_resolve_sets_do_not_collide(crawler):
    p1, p2 = _pointers()
    one = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                            projection=PROJECTION, resolve=(p1,))
    both = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                             projection=PROJECTION, resolve=(p1, p2))
    twice = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                              projection=PROJECTION, resolve=(p1, p1))
    assert len({one.read_hash, both.read_hash, twice.read_hash}) == 3


def test_a_naive_as_of_is_refused(request_factory):
    """A pointer identity that depends on the reader's timezone is not an identity."""
    with pytest.raises(Exception):
        request_factory(as_of=datetime(2026, 8, 15, 12, 0, 0))


def test_equivalent_instants_in_different_zones_hash_alike(request_factory):
    from datetime import timedelta
    sgt = timezone(timedelta(hours=8))
    a = request_factory(as_of=datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc))
    b = request_factory(as_of=datetime(2026, 8, 15, 20, 0, 0, tzinfo=sgt))
    assert a.read_hash == b.read_hash


def test_a_predicate_is_refused_until_the_grammar_exists(request_factory):
    """§6 / Appendix A: the highest-risk undefined component fails closed at the contract."""
    with pytest.raises(PredicateGrammarNotDefined):
        request_factory(predicate="party.party_id == :pid")


def test_an_unknown_scheme_version_fails_closed(request_factory):
    with pytest.raises(Exception, match="scheme_version"):
        request_factory(scheme_version=2)


def test_a_request_carrying_pointer_lists_is_still_hashable(crawler):
    """Appendix A: `SourcePointer` holds `facts`/`derived_from` LISTS, so a field-tuple hash of a
    request carrying one would raise. Identity here is the digest instead."""
    raw = SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                        column="notes", row_id="P-2", trust=Trust.UNTRUSTED)
    derived = SourcePointer(source_id="core_banking", kind="derived",
                            derivation="redact(notes)", derived_from=[raw],
                            trust=Trust.UNTRUSTED)
    req = CapabilityRequest(source_id="core_banking", capability="row_read", principal=crawler,
                            projection=PROJECTION, resolve=(derived,))
    assert isinstance(hash(req), int)
    assert hash(req) == hash(req)


# --- two spans of one email part are two locations ----------------------------------------

def test_two_spans_of_one_email_part_are_not_one_identity():
    """A live collision, reproduced before it was fixed (scheme v3 → v4).

    `_LOCATOR_EXTRAS["email_part"]` listed only `child`, so two pointers naming different
    `char_start`/`char_end` within one part rendered to an **identical** canonical form. Two
    distinct citations therefore shared one pointer identity — and, because `read_hash` is built
    from the locator set, one cache entry.

    The trap was that `char_start`/`char_end` are *optional* on `email_part` (unlike `doc_span`,
    where `_REQUIRED_FIELDS` already carries them), so nothing forced them into the locator set.
    Optional is not the same as non-locating: when a pointer carries them, they are exactly what
    distinguishes one span of a body from another.

    Found by the mailbox adapter, which worked around it by encoding the span inside `part`
    (`body#s:e`). That encoding is still valid; it is no longer load-bearing.
    """
    from databridge_core import SourcePointer, Trust
    from databridge_adapter.canonical import canonical_pointer

    first = SourcePointer(source_id="mb", kind="email_part", message_id="M1", part="body",
                          char_start=0, char_end=10, trust=Trust.UNTRUSTED)
    second = SourcePointer(source_id="mb", kind="email_part", message_id="M1", part="body",
                           char_start=50, char_end=60, trust=Trust.UNTRUSTED)

    assert canonical_pointer(first) != canonical_pointer(second), (
        "two different spans of one email part must be two locations; sharing an identity means "
        "two citations become indistinguishable and share a cache entry")


def test_trust_still_does_not_change_an_email_part_identity():
    """The other half of the boundary: `trust` is a writability declaration about the field, not a
    statement about WHERE the value is. Two pointers to one span denote one location however the
    deployment labels its trust — otherwise re-labelling a source would orphan every citation
    into it."""
    from databridge_core import SourcePointer, Trust
    from databridge_adapter.canonical import canonical_pointer

    untrusted = SourcePointer(source_id="mb", kind="email_part", message_id="M1", part="body",
                              char_start=0, char_end=10, trust=Trust.UNTRUSTED)
    trusted = SourcePointer(source_id="mb", kind="email_part", message_id="M1", part="body",
                            char_start=0, char_end=10, trust=Trust.TRUSTED)

    assert canonical_pointer(untrusted) == canonical_pointer(trusted)
