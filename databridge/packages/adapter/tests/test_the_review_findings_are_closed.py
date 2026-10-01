"""Review **R-3A** on the Phase 3A contract: one test per finding, each failing without the fix.

The review is at `wiki/40-reviews/raw/phase2-3-b3-adapter.md`. Three Criticals and five Highs
landed on the contract this workstream builds its Core on top of, so they are fixed here rather
than carried. Findings are numbered as the review numbers them.

**One finding is recorded as a partial false positive** — finding 6's `mode` clause — and the
reasoning is in `test_finding_6_mode_stays_out_of_the_hash_and_the_branch_is_forbidden_instead`.
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
    ADAPTER_SINKS,
    TRUSTED_SINKS,
    CapabilityRequest,
    CursorDescription,
    DerivationError,
    Entitlement,
    NotAnAdapter,
    Principal,
    Projection,
    RegistryTaintMap,
    SourceDescription,
    UngroundedValue,
    UnorderedPointeredRead,
    UntrustedInControlFlow,
    Verdict,
    assert_conforms,
    assert_grounded,
    assert_provenance_present,
    canonical_scalar,
    combine_verdicts,
    exact_match,
    parse_derivation,
    pointer_digest,
    redact_pii,
    screen_sinks,
    verbatim_match,
)
from databridge_core import GroundedValue, SnapshotIdentity, SourcePointer, Trust

AS_OF = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def cell_pointer(column="legal_name") -> SourcePointer:
    return SourcePointer(source_id="core_banking", kind="table_cell", table="party",
                         column=column, row_id="P-1", trust=Trust.TRUSTED)


# =======================================================================================
# 1 (Critical) — `assert_grounded` could certify fabricated values.
# =======================================================================================

def test_finding_1_a_substring_no_longer_grounds_an_exact_cell():
    """The review's own example: emitted "eligible" was accepted against fetched "ineligible"."""
    cell = GroundedValue(value="eligible", pointer=cell_pointer("kyc_status"))
    with pytest.raises(UngroundedValue, match="fabricated"):
        assert_grounded(cell, fetched="ineligible")
    assert exact_match("eligible", "ineligible") is False


def test_finding_1_whitespace_collapsing_needs_a_derived_pointer():
    """The module said so and then did the opposite. "A  B" must not ground against "A B"."""
    with pytest.raises(UngroundedValue):
        assert_grounded(GroundedValue(value="A  B", pointer=cell_pointer()), fetched="A B")
    assert exact_match("A  B", "A B") is False


def test_finding_1_a_row_is_indexed_by_the_pointers_own_column():
    """A pointer citing `legal_name` may not be vouched for by the contents of `notes`."""
    row = {"party_id": "P-1", "legal_name": "Acme Pte Ltd", "notes": "Fabricated Holdings"}
    assert_grounded(GroundedValue(value="Acme Pte Ltd", pointer=cell_pointer()), fetched=row)
    with pytest.raises(UngroundedValue, match="not"):
        assert_grounded(GroundedValue(value="Fabricated Holdings", pointer=cell_pointer()),
                        fetched=row)


def test_finding_1_a_row_missing_the_cited_column_is_refused():
    with pytest.raises(UngroundedValue, match="does not contain"):
        assert_grounded(GroundedValue(value="x", pointer=cell_pointer("absent_column")),
                        fetched={"party_id": "P-1"})


def test_finding_1_a_span_is_sliced_to_the_offsets_it_cites():
    """The strongest available fix for "fetched need not come from the pointer": for a span, the
    pointer NAMES a range, so only that range may vouch for the value."""
    doc = "the borrower is ineligible under clause 4; Acme Pte Ltd is the guarantor"
    start = doc.index("Acme Pte Ltd")
    good = SourcePointer(source_id="filings", kind="doc_span", document_id="d1",
                         char_start=start, char_end=start + len("Acme Pte Ltd"),
                         trust=Trust.UNTRUSTED)
    assert_grounded(GroundedValue(value="Acme Pte Ltd", pointer=good), fetched=doc)

    lying = SourcePointer(source_id="filings", kind="doc_span", document_id="d1",
                          char_start=start, char_end=start + len("Acme Pte Ltd"),
                          trust=Trust.UNTRUSTED)
    with pytest.raises(UngroundedValue):
        # "eligible" IS in the document, at another offset. The cited range does not contain it.
        assert_grounded(GroundedValue(value="eligible", pointer=lying), fetched=doc)


def test_finding_1_offsets_that_do_not_fit_the_block_ground_nothing():
    ptr = SourcePointer(source_id="filings", kind="doc_span", document_id="d1",
                        char_start=5000, char_end=5010, trust=Trust.UNTRUSTED)
    with pytest.raises(UngroundedValue):
        assert_grounded(GroundedValue(value="Acme", pointer=ptr), fetched="Acme")


def test_finding_1_a_number_rendered_as_text_is_a_transformation_not_a_citation():
    with pytest.raises(UngroundedValue):
        assert_grounded(GroundedValue(value="1000.50", pointer=cell_pointer("limit_amount")),
                        fetched=Decimal("1000.50"))


def test_finding_1_span_containment_is_still_available_where_it_is_honest():
    """Not a blanket tightening: a span with no offsets genuinely cites "somewhere in this block",
    and refusing containment there would make every OCR and email pointer ungroundable."""
    ptr = SourcePointer(source_id="mailbox", kind="email_part", message_id="m1", part="body",
                        trust=Trust.UNTRUSTED)
    assert_grounded(GroundedValue(value="Acme Pte Ltd", pointer=ptr),
                    fetched="Dear all, Acme Pte Ltd has drawn on the facility.")
    assert verbatim_match("Acme", "  Acme  ") is True


# =======================================================================================
# 2 (Critical) — derived values had no value-level proof.
# =======================================================================================

def test_finding_2_a_prose_derivation_is_no_longer_accepted():
    """`derivation="redact"` beside any value at all used to pass."""
    raw = cell_pointer("email")
    ptr = SourcePointer(source_id="core_banking", kind="derived",
                        derivation="redact(email) - RFC5322 local-part+domain replaced",
                        derived_from=[raw], trust=raw.trust)
    with pytest.raises(DerivationError, match="not a replayable call"):
        assert_grounded(GroundedValue(value="<REDACTED:email>", pointer=ptr),
                        fetched="ops@acme.example")


def test_finding_2_a_registered_derivation_is_replayed_and_must_produce_the_value():
    raw = cell_pointer("email")
    ptr = SourcePointer(source_id="core_banking", kind="derived",
                        derivation="redact_pii@v1(column=email) - RFC5322 local-part+domain "
                                   "replaced",
                        derived_from=[raw], trust=raw.trust)
    assert_grounded(GroundedValue(value="<REDACTED:email>", pointer=ptr),
                    fetched="ops@acme.example")

    lying = GroundedValue(value="<REDACTED:national_id>", pointer=ptr)
    with pytest.raises(DerivationError, match="replayed over the cited parent"):
        assert_grounded(lying, fetched="ops@acme.example")


def test_finding_2_an_unregistered_rule_fails_closed():
    raw = cell_pointer("email")
    ptr = SourcePointer(source_id="core_banking", kind="derived",
                        derivation="invent_a_value@v9(column=email)", derived_from=[raw],
                        trust=raw.trust)
    with pytest.raises(DerivationError, match="not registered"):
        assert_grounded(GroundedValue(value="anything", pointer=ptr), fetched="x")


def test_finding_2_the_weaker_check_is_named_so_it_cannot_be_mistaken_for_the_wall():
    """The review asked for the API to distinguish "provenance structurally present" from
    "derivation verified". It does, and the weaker one is a mouthful on purpose."""
    raw = cell_pointer("email")
    ptr = SourcePointer(source_id="core_banking", kind="derived",
                        derivation="redact_pii@v1(column=email)", derived_from=[raw],
                        trust=raw.trust)
    assert_provenance_present(GroundedValue(value="anything at all", pointer=ptr))
    with pytest.raises(DerivationError):
        assert_grounded(GroundedValue(value="anything at all", pointer=ptr),
                        fetched="ops@acme.example")


def test_finding_2_the_derivation_grammar_is_parsed_not_guessed():
    call = parse_derivation("redact_pii@v1(column=email) — prose a human wrote")
    assert (call.name, call.version, dict(call.args)) == ("redact_pii", 1, {"column": "email"})
    assert call.note
    with pytest.raises(DerivationError):
        parse_derivation("   ")


def test_finding_2_a_registered_rule_cannot_be_swapped_under_a_stored_pointer():
    """Replaying a five-year-old pointer must run the rule that produced it."""
    from databridge_adapter import register_derivation

    with pytest.raises(DerivationError, match="NEW VERSION"):
        register_derivation("redact_pii", 1, lambda parents, args: "something else")


# =======================================================================================
# 3 (Critical) — cache isolation rested on an unverified caller assertion.
# =======================================================================================

def _snapshot(req, content_hash="ch") -> SnapshotIdentity:
    return SnapshotIdentity(source_id=req.source_id, as_of=AS_OF, content_hash=content_hash,
                            schema_hash="sh", query_hash=req.read_hash)


def test_finding_3_an_entitlement_that_is_not_the_principals_is_refused(party_request, analyst):
    req = party_request(principal=analyst)
    with pytest.raises(ValueError, match="two principals can share"):
        req.cache_key(entitlement=Entitlement(grant_id="someone-elses", roles=("credit_analyst",),
                                              policy_bundle_version="pb-1"),
                      snapshot=_snapshot(req), pack_version="finance@2")


def test_finding_3_a_subject_scoped_principal_cannot_use_a_grantless_entitlement(party_request,
                                                                                analyst):
    req = party_request(principal=analyst)
    assert analyst.grant_id is not None
    with pytest.raises(ValueError):
        req.cache_key(entitlement=Entitlement(policy_bundle_version="pb-1"),
                      snapshot=_snapshot(req), pack_version="finance@2")


def test_finding_3_an_unrelated_snapshot_is_refused(party_request, analyst):
    req = party_request(principal=analyst)
    ent = Entitlement(grant_id=analyst.grant_id, policy_bundle_version="pb-1")
    other_source = SnapshotIdentity(source_id="crm", as_of=AS_OF, content_hash="ch",
                                    schema_hash="sh", query_hash=req.read_hash)
    with pytest.raises(ValueError, match="guarded by nothing"):
        req.cache_key(entitlement=ent, snapshot=other_source, pack_version="finance@2")
    other_read = SnapshotIdentity(source_id="core_banking", as_of=AS_OF, content_hash="ch",
                                  schema_hash="sh", query_hash="a-different-read")
    with pytest.raises(ValueError, match="read_hash"):
        req.cache_key(entitlement=ent, snapshot=other_read, pack_version="finance@2")


def test_finding_3_two_principals_with_the_same_entitlement_do_not_share_a_key(party_request,
                                                                              packs_purposes):
    """`actor` and `on_behalf_of` are in the payload now. Sharing was previously safe only if
    policy output could be PROVEN to depend on nothing else, and nothing proved it."""
    a = Principal(actor="orchestrator@walnut", on_behalf_of="rm-0042",
                  purpose="lending.underwriting", grant_id="shared-grant")
    b = Principal(actor="orchestrator@walnut", on_behalf_of="rm-0099",
                  purpose="lending.underwriting", grant_id="shared-grant")
    ra, rb = party_request(principal=a), party_request(principal=b)
    ent = Entitlement(grant_id="shared-grant", roles=("relationship_manager",),
                      policy_bundle_version="pb-1")
    assert ra.read_hash == rb.read_hash, "the READ is the same read; only the reader differs"
    assert (ra.cache_key(entitlement=ent, snapshot=_snapshot(ra), pack_version="p")
            != rb.cache_key(entitlement=ent, snapshot=_snapshot(rb), pack_version="p"))


# =======================================================================================
# 4 (High) — canonicalisation was not deterministic across execution contexts.
# =======================================================================================

def test_finding_4_decimal_canonicalisation_ignores_the_ambient_context():
    """`Decimal.normalize()` rounds under the ACTIVE context, which is per-thread and settable by
    any library in the process."""
    import decimal

    value = Decimal("1000.500")
    baseline = canonical_scalar(value)
    with decimal.localcontext() as ctx:
        ctx.prec = 3
        assert canonical_scalar(value) == baseline
    with decimal.localcontext() as ctx:
        ctx.prec = 60
        assert canonical_scalar(value) == baseline


def test_finding_4_non_finite_decimals_are_refused_rather_than_rendered_as_text():
    for bad in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(ValueError, match="no canonical form"):
            canonical_scalar(bad)


def test_finding_4_pointer_digests_do_not_go_through_pydantic_serialisation():
    """The module's first rule is that the payload is built by hand; `pointer_digest` went
    straight through `model_dump(mode="json")` and undid it."""
    import inspect

    from databridge_adapter import canonical

    assert "model_dump" not in inspect.getsource(canonical.canonical_pointer)
    assert "model_dump" not in inspect.getsource(canonical.pointer_digest)
    assert pointer_digest(cell_pointer()) == pointer_digest(cell_pointer())


def test_finding_4_a_pointers_nested_snapshot_and_parents_are_rendered_deeply(party_request):
    parent = cell_pointer("email")
    a = SourcePointer(source_id="core_banking", kind="derived",
                      derivation="redact_pii@v1(column=email)", derived_from=[parent],
                      trust=Trust.TRUSTED)
    b = SourcePointer(source_id="core_banking", kind="derived",
                      derivation="redact_pii@v1(column=email)",
                      derived_from=[cell_pointer("phone")], trust=Trust.TRUSTED)
    assert pointer_digest(a) != pointer_digest(b), (
        "a shallow rendering would hash two derivations over different parents identically")


# =======================================================================================
# 5 (High) — the request was not actually frozen.
# =======================================================================================

def test_finding_5_mutating_a_resolve_pointer_cannot_change_an_issued_identity(analyst):
    ptr = cell_pointer()
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        resolve=(ptr,), limit=10)
    before, before_hash = req.read_hash, hash(req)
    object.__setattr__(ptr, "derived_from", [cell_pointer("notes")])
    assert req.read_hash == before
    assert hash(req) == before_hash


# =======================================================================================
# 6 (High) — semantically different reads shared a `read_hash`.
# =======================================================================================

def test_finding_6_decimal_scale_is_no_longer_collapsed(party_request):
    a = party_request(params={"floor": Decimal("1.0")})
    b = party_request(params={"floor": Decimal("1")})
    assert a.read_hash != b.read_hash


def test_finding_6_a_named_querys_version_is_part_of_the_identity(party_request):
    """"Changing that query under the same capability name changes what is read without changing
    pointer identity." Now it does change it."""
    assert party_request().read_hash != party_request(capability_version="v2").read_hash
    assert (party_request(capability_version="v2").read_hash
            != party_request(capability_version="v3").read_hash)


def test_finding_6_mode_stays_out_of_the_hash_and_the_branch_is_forbidden_instead(core,
                                                                                 party_request):
    """**Recorded as a partial false positive, with the reason.**

    The review is right that an adapter *could* branch on `mode`, and wrong that hashing it is the
    fix. `read_hash` is pointer identity: two reads of the same rows, one scheduled as a build-time
    crawl and one served to a query, cite the SAME cells, and forking their pointer identities by
    schedule would mean a citation minted at build time never matches one minted at query time —
    which breaks verification-by-re-execution for exactly the reads a graph is built from.

    So the hole is closed at the other end: no adapter may branch on `mode`, and this asserts it
    for the adapter that exists — the plan and the emitted pointers are identical under both.
    """
    build, query = party_request(mode="build", limit=5), party_request(mode="query", limit=5)
    assert build.read_hash == query.read_hash
    assert core.plan(build).operations == core.plan(query).operations
    left = [c.pointer.row_id for row in core.resolve(build).rows.rows for c in row
            if hasattr(c, "pointer")]
    right = [c.pointer.row_id for row in core.resolve(query).rows.rows for c in row
             if hasattr(c, "pointer")]
    assert left == right


def test_finding_6_resolve_order_is_erased_and_the_result_order_is_contractual(core, compliance):
    """`pointer_set_digest` is order-independent, which is only sound if the RESULT ordering is
    independent of request order too. The Core orders a resolution by the key, and this is that
    contract made true rather than assumed."""
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=compliance,
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=4)
    rows = core.resolve(req).rows.rows
    pointers = tuple(row[0].pointer for row in rows)

    forward = CapabilityRequest(source_id="core_banking", capability="row_read",
                                principal=compliance, projection=req.projection,
                                resolve=pointers, limit=10)
    backward = CapabilityRequest(source_id="core_banking", capability="row_read",
                                 principal=compliance, projection=req.projection,
                                 resolve=tuple(reversed(pointers)), limit=10)
    assert forward.read_hash == backward.read_hash
    assert ([c.value for row in core.resolve(forward).rows.rows for c in row]
            == [c.value for row in core.resolve(backward).rows.rows for c in row])


# =======================================================================================
# 7 (High) — the sink inventory omitted direct control-flow inputs.
# =======================================================================================

@pytest.mark.parametrize("sink", ["source_id", "principal.purpose", "principal.grant_id",
                                  "principal.actor", "principal.on_behalf_of", "mode", "as_of",
                                  "limit", "capability_version", "projection.key"])
def test_finding_7_every_named_sink_is_on_the_list_and_readable(sink, party_request):
    assert sink in TRUSTED_SINKS and sink in ADAPTER_SINKS
    from databridge_adapter.taint import _sink_values

    assert list(_sink_values(party_request(as_of=AS_OF, capability_version="v1"), sink))


def test_finding_7_an_untrusted_value_in_any_of_them_raises(party_request):
    taint = RegistryTaintMap()
    req = party_request(as_of=AS_OF, capability_version="v1")
    for value in ("core_banking", "lending.underwriting", "grant-analyst-7", "analyst-7",
                  "query", AS_OF, 25, "v1", "party_id"):
        fresh = RegistryTaintMap()
        fresh.register(value, Trust.UNTRUSTED)
        with pytest.raises(UntrustedInControlFlow):
            screen_sinks(req, fresh)
    screen_sinks(req, taint)                # nothing registered: nothing refused


# =======================================================================================
# 8 (High) — a non-empty `order_by` is not a total order.
# =======================================================================================

def test_finding_8_a_partial_order_is_refused_by_the_contract(analyst):
    with pytest.raises(ValueError, match="not a TOTAL order"):
        CapabilityRequest(
            source_id="core_banking", capability="row_read", principal=analyst,
            projection=Projection(entity="party", fields=("party_id", "kyc_status"),
                                  key=("party_id",)),
            order_by=("kyc_status",), limit=10)


def test_finding_8_a_repeated_or_contradictory_term_is_refused(analyst):
    with pytest.raises(ValueError, match="twice"):
        CapabilityRequest(
            source_id="core_banking", capability="row_read", principal=analyst,
            projection=Projection(entity="party", fields=("party_id", "kyc_status"),
                                  key=("party_id",)),
            order_by=("party_id", "-party_id"), limit=10)


def test_finding_8_the_core_checks_the_key_against_reflection_not_against_the_claim(core,
                                                                                   analyst):
    """The contract can only check the ordering against the key the caller DECLARED. The Core
    checks that key against the database's own primary key, which is where the claim stops being
    paperwork."""
    lying = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=Projection(entity="party", fields=("party_id", "kyc_status"),
                              key=("kyc_status",)),
        order_by=("kyc_status",), limit=10)
    with pytest.raises(UnorderedPointeredRead, match="primary key"):
        core.resolve(lying)


def test_finding_8_an_undeclared_key_is_refused_by_the_core(core, analyst):
    undeclared = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=Projection(entity="party", fields=("party_id", "kyc_status")),
        order_by=("kyc_status",), limit=10)
    with pytest.raises(UnorderedPointeredRead):
        core.resolve(undeclared)


# =======================================================================================
# 9 (Medium) — `DRIFT_BENIGN` had contradictory dual advertisements.
# =======================================================================================

def _description(**overrides) -> SourceDescription:
    base = dict(source_id="s", capabilities=frozenset({"row_read"}), pinning_tier="T2",
                cursor=CursorDescription(kind="timestamp", field="updated_at", lossy=True),
                deletes_detectable="none", effective_retention={"default": "P1Y"},
                capture_required=True, egress_class="INTERNAL",
                reachable_verdicts=frozenset({Verdict.VERIFIED}))
    base.update(overrides)
    return SourceDescription(**base)


def test_finding_9_an_internally_inconsistent_description_cannot_be_constructed():
    with pytest.raises(ValueError, match="does not advertise detects_benign_drift"):
        _description(reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.DRIFT_BENIGN}))
    with pytest.raises(ValueError, match="contradicts itself"):
        _description(detects_benign_drift=True, benign_drift_signal="xmin")


def test_finding_9_the_core_screens_its_own_verdicts_against_the_matrix(core):
    """`assert_verdict_advertised` is convention unless something calls it. The Core does, on the
    way out of every `verify()`."""
    import inspect

    source = inspect.getsource(type(core).verify)
    assert "assert_verdict_advertised" in source


# =======================================================================================
# 10 (Medium) — the "lattice" was only a closed enum.
# =======================================================================================

def test_finding_10_verdicts_have_a_deterministic_join():
    assert combine_verdicts([Verdict.FORBIDDEN, Verdict.VANISHED]) is Verdict.FORBIDDEN
    assert combine_verdicts([Verdict.VERIFIED_AS_OF_CAPTURE,
                             Verdict.DRIFT_MATERIAL]) is Verdict.DRIFT_MATERIAL
    assert combine_verdicts([Verdict.VERIFIED, Verdict.VERIFIED]) is Verdict.VERIFIED
    assert combine_verdicts([Verdict.UNVERIFIABLE, Verdict.VERIFIED]) is Verdict.UNVERIFIABLE


def test_finding_10_drift_material_dominates_everything_so_escalation_survives_the_join():
    """The one rule that must not be lost in an aggregate: a changed cited value always pages."""
    for other in Verdict:
        assert combine_verdicts([Verdict.DRIFT_MATERIAL, other]).must_escalate


def test_finding_10_the_join_of_nothing_is_not_a_verdict():
    with pytest.raises(ValueError, match="examined no pointers"):
        combine_verdicts([])


# =======================================================================================
# 11 (Medium) — registration-time conformance accepted unusable adapters.
# =======================================================================================

class _Base:
    source_id = "s"
    capabilities = frozenset({"row_read"})
    pinning_tier = "T2"

    def describe(self): ...
    def snapshot(self, req): ...
    def plan(self, req): ...
    def resolve(self, req): ...
    def verify(self, pointer): ...


def test_finding_11_a_verb_with_an_extra_required_positional_is_refused():
    class Bad(_Base):
        def resolve(self, req, another_required_argument): ...

    with pytest.raises(NotAnAdapter, match="not callable through the port"):
        assert_conforms(Bad())


def test_finding_11_describe_with_a_required_argument_is_refused():
    class Bad(_Base):
        def describe(self, required_argument): ...

    with pytest.raises(NotAnAdapter):
        assert_conforms(Bad())


def test_finding_11_a_required_keyword_only_argument_is_refused():
    class Bad(_Base):
        def resolve(self, req, *, budget): ...

    with pytest.raises(NotAnAdapter, match="keyword-only"):
        assert_conforms(Bad())


def test_finding_11_an_optional_keyword_only_argument_is_allowed():
    """`PostgresCore.verify(pointer, *, principal=None)` has to stay conformant: the orchestrator
    can still call it with exactly the port's arguments."""
    class Fine(_Base):
        def verify(self, pointer, *, principal=None): ...

    assert_conforms(Fine())


def test_finding_11_capabilities_must_be_identifiers():
    class Bad(_Base):
        capabilities = frozenset({object()})

    with pytest.raises(NotAnAdapter, match="lowercase snake_case"):
        assert_conforms(Bad())


def test_finding_11_the_real_core_conforms(core):
    assert_conforms(core)


# =======================================================================================
# 12 (Medium) — invalid canonical values survived construction.
# =======================================================================================

def test_finding_12_a_nan_parameter_is_refused_at_construction(analyst):
    with pytest.raises(Exception, match="canonical form"):
        CapabilityRequest(
            source_id="core_banking", capability="row_read", principal=analyst,
            projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
            order_by=("party_id",), params={"x": float("nan")})


def test_finding_12_a_non_finite_decimal_parameter_is_refused_at_construction(analyst):
    # Pydantic's `Scalar` union coerces `Decimal("Infinity")` to a float before the validator
    # sees it, so the refusal arrives from the float branch. Either way it is refused AT
    # CONSTRUCTION, which is the finding; the branch that catches it is an implementation detail.
    with pytest.raises(Exception, match="no canonical form"):
        CapabilityRequest(
            source_id="core_banking", capability="row_read", principal=analyst,
            projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
            order_by=("party_id",), params={"x": Decimal("Infinity")})


# =======================================================================================
# 13 (Medium) — principal identity fields had permissive representations.
# =======================================================================================

def test_finding_13_a_blank_human_identifier_is_refused(packs_purposes):
    with pytest.raises(ValueError, match="nobody wrote down who"):
        Principal(actor="orchestrator@walnut", on_behalf_of="   ",
                  purpose="finance.graph_build")
    with pytest.raises(ValueError):
        Principal(actor="orchestrator@walnut", on_behalf_of=None, purpose="lending.underwriting",
                  grant_id="  ")


def test_finding_13_none_still_means_no_human_asked(packs_purposes):
    """The legitimate case stays legitimate: a scheduled build-time crawl has no human behind it,
    and that is a decision the caller states rather than an oversight."""
    p = Principal(actor="orchestrator@walnut", on_behalf_of=None, purpose="finance.graph_build")
    assert p.on_behalf_of is None
