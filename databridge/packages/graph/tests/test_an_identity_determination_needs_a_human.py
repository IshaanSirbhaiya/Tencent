"""The strongest constraint in the system, tested as a validator rather than read as prose.

`meta.py` has always said `RESOLVED_TO` is "written only on the far side of a named human's
attestation". Review R-001/A3 found that `ATTESTED_META_KINDS` was **defined and consumed by
nothing**: any caller could construct an edge bound to `RESOLVED_TO` — an identity determination,
the one thing CLAUDE.md §Hard constraints 1 refuses to automate — with an ordinary
`SourcePointer`, no named human, no attestation record, no grant, and no check that the
destination was a declared subject at all.

Every test below fails if the corresponding clause is removed from
`attestation.enforce_attestation`. That was verified by removing each one in turn, not assumed.

**No test here names a domain.** The vocabulary comes from whichever pack the block binds, and the
subject kind, its required attestation mode, its grant type and its identifiers are all read back
out of the pack rather than written down — which is also a test that the enforcement is computed
from the packs and not from anything the engine knows.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from databridge_core import SourcePointer, Trust
from databridge_graph.attestation import (
    Attestation,
    AttestationError,
    enforce_attestation,
    is_determination_kind,
    record_determination,
    subject_kind_of_node_id,
)
from databridge_graph.kinds import EdgeKind, NodeKind
from databridge_graph.meta import ATTESTED_META_KINDS, CLAIM_META_KINDS, EdgeMetaKind
from databridge_graph.packs import (
    ATTESTATION_MODES,
    active_vocabulary,
    clear_caches,
    deployment,
)
from databridge_graph.schema import GraphEdge, node_id
from databridge_graph.store import GraphStore

#: A deployment whose pack declares BOTH a subject and an edge kind bound to `RESOLVED_TO`. Named
#: as a fixture parameter rather than inline so that the day a pack is renamed, one line changes.
PACK_WITH_DETERMINATIONS = "finance"
OTHER_PACK_WITH_DETERMINATIONS = "government"



def _a_pack_declaring_only_claims() -> str:
    """A minimal pack with a subject and a CLAIM edge, and deliberately no determination kind."""
    return """
pack: claimsonly
title: A pack that can claim but never determine
version: "1.0"
node_kinds:
  - name: doc
    binds_to: document
    description: A document.
  - name: person
    binds_to: entity
    description: A person.
edge_kinds:
  - name: mentions
    binds_to: about
    src: [doc]
    dst: [person]
    description: A claim that a document is about a person. Never a determination.
subject_kinds:
  - kind: person
    identifiers: [person_id]
    never_fuzzy_merge: true
    attestation: in_person
    grant_type: PersonReadGrant
"""


def _a_kind_absent_from(other_pack: str) -> EdgeKind:
    """A kind declared by the bound pack but NOT by `other_pack` — computed, never named."""
    with deployment(other_pack):
        theirs = set(active_vocabulary().edge_kinds)
    mine = [k for k in active_vocabulary().edge_kinds if k not in theirs]
    assert mine, f"the two packs declare identical edge vocabularies; pick different fixtures"
    return EdgeKind(sorted(mine)[0])

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pointer(source: str = "s") -> SourcePointer:
    # `trust=Trust.TRUSTED`: since review R-012 (2026-08-16, Critical, finding 4) an attestation's
    # OWN grounding must itself be trusted (spine D3) — an untrusted or unlabelled capture pointer
    # must not be able to authorise an identity-bearing edge. This helper is also used to ground
    # ordinary (non-attestation) edges in this file, where a trusted pointer is a strict superset
    # of what those calls needed anyway.
    return SourcePointer(source_id=source, kind="table_cell", table="t", column="c", row_id="1",
                         trust=Trust.TRUSTED)


def _kinds_binding_to(meta: EdgeMetaKind) -> list[EdgeKind]:
    vocab = active_vocabulary()
    return [EdgeKind(n) for n in vocab.edges_binding_to(meta)]


def _a_determination_kind() -> EdgeKind:
    kinds = [k for m in ATTESTED_META_KINDS for k in _kinds_binding_to(m)]
    assert kinds, "this deployment declares no determination edge kind"
    return kinds[0]


def _a_claim_kind() -> EdgeKind:
    kinds = [k for m in CLAIM_META_KINDS for k in _kinds_binding_to(m)]
    assert kinds, "this deployment declares no claim edge kind"
    return kinds[0]


def _a_subject_kind() -> str:
    return sorted(active_vocabulary().subject_kinds)[0]


def _a_subject_id(slug: str = "one") -> str:
    return node_id(NodeKind(_a_subject_kind()), slug)


def _valid_attestation(**overrides) -> Attestation:
    """An attestation that satisfies the *currently bound* pack — every value read from it.

    `candidate_id` / `subject_id` / `edge_kind` default to the same triple `_determination` uses,
    so the two helpers agree by construction. They are REQUIRED fields since the review of
    2026-08-16: without them one attestation authorised every determination pointing at any node
    of its subject kind.
    """
    vocab = active_vocabulary()
    kind = _a_subject_kind()
    fields = dict(
        attestor="Ada Verifier",
        attestor_role="front desk",
        mode=vocab.attestation_for(kind),
        attested_at=_now(),
        subject_kind=kind,
        identifier_checked=vocab.identifiers_for(kind)[0],
        grant_type=vocab.identity_grant_for(kind),
        sources=(_pointer("attestation-capture"),),
        candidate_id="doc:1",
        subject_id=_a_subject_id(),
        edge_kind=str(_a_determination_kind()),
        statement="Candidate selected against the identifier presented.")
    fields.update(overrides)
    return Attestation(**fields)


def _determination(**overrides) -> GraphEdge:
    fields = dict(
        id="e:determination", src_id="doc:1", dst_id=_a_subject_id(),
        kind=_a_determination_kind(), sources=(_pointer(),),
        valid_from=_now(), system_from=_now())
    fields.update(overrides)
    if "attestation" not in overrides:
        # Bound to THIS edge. A caller that overrides an endpoint gets an attestation that follows
        # it, so the tests that vary a destination keep exercising the guard they were written for
        # rather than tripping over the binding check first.
        fields["attestation"] = _valid_attestation(
            candidate_id=fields["src_id"], subject_id=fields["dst_id"],
            edge_kind=str(fields["kind"]))
    return GraphEdge(**fields)


def _store_holding(store, node_id_: str, *, kind=None):
    """Put the destination subject in the graph, because the store now insists it exists.

    `subject_kind_of_node_id` reads a kind out of an id STRING. Only the store can check that
    there is a node behind it, and it does (attestation finding 8) — so a test that writes a
    determination has to seed the subject first, which is also what any real caller does.
    """
    from databridge_graph.schema import GraphNode

    prefix = node_id_.split(":", 1)[0]
    store.add_node(GraphNode(
        id=node_id_, kind=NodeKind(kind or prefix), label=node_id_,
        sources=(_pointer("registry-row"),), valid_from=_now(), system_from=_now()))
    return store


# --- the finding itself ------------------------------------------------------------------

def test_a_determination_cannot_be_constructed_without_an_attestation():
    """A2/A3, the whole point: the generic constructor was the unguarded route."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError) as exc:
            _determination(attestation=None)
        assert "Attestation" in str(exc.value)


def test_a_source_pointer_is_not_an_attestation():
    """A pointer says where a value was read. It does not say a human selected this candidate."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            GraphEdge(id="e:1", src_id="doc:1", dst_id=_a_subject_id(),
                      kind=_a_determination_kind(),
                      sources=(_pointer(), _pointer("second"), _pointer("third")),
                      valid_from=_now(), system_from=_now())


def test_a_valid_determination_is_accepted():
    """The wall must refuse the wrong thing, not everything."""
    with deployment(PACK_WITH_DETERMINATIONS):
        edge = _determination()
        assert edge.attestation is not None
        assert edge.attestation.attestor == "Ada Verifier"
        assert is_determination_kind(edge.kind)


# --- who, when, what act, what was checked, and its own grounding -------------------------

@pytest.mark.parametrize("attestor", ["", "   ", "A"])
def test_an_attestation_must_name_a_human(attestor):
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _valid_attestation(attestor=attestor)


def test_an_attestation_time_must_carry_an_offset():
    """An audit record whose timestamp has no timezone is not answerable across a border."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _valid_attestation(attested_at=datetime(2026, 8, 16, 9, 0, 0))


def test_an_attestation_is_itself_grounded():
    """P4 does not exempt records about humans."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _valid_attestation(sources=())


def test_an_invented_attestation_mode_is_refused():
    with deployment(PACK_WITH_DETERMINATIONS):
        assert "declared_by_the_caller" not in ATTESTATION_MODES
        with pytest.raises(ValidationError):
            _valid_attestation(mode="declared_by_the_caller")


def test_a_weaker_mode_does_not_satisfy_a_stronger_declaration():
    """The mode IS the guarantee. A pack requiring one act is not satisfied by another.

    Both packs bound here declare a subject; the test picks a mode the pack did NOT declare, so it
    holds whichever way the two packs' declarations fall.
    """
    for pack in (PACK_WITH_DETERMINATIONS, OTHER_PACK_WITH_DETERMINATIONS):
        with deployment(pack):
            declared = active_vocabulary().attestation_for(_a_subject_kind())
            other = sorted(ATTESTATION_MODES - {declared})[0]
            with pytest.raises(AttestationError):
                _valid_attestation(mode=other).check_against()


def test_an_undeclared_identifier_is_refused():
    """"I confirmed them" is not checkable; the identifier the human held up is."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _determination(attestation=_valid_attestation(
                identifier_checked="whatever_was_to_hand"))


def test_an_attestation_cannot_name_a_grant_its_subject_does_not_confer():
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _determination(attestation=_valid_attestation(grant_type="RootGrant"))


# --- the destination must be a declared subject -------------------------------------------

def test_a_determination_must_point_at_a_declared_subject():
    """Three guards refuse this, independently — the destination check here, the
    attestation/destination agreement check below, and `Attestation.check_against`. The refusal
    REASON is asserted because that is what distinguishes them: an operator reading "not a
    subject" is told something different from one reading "the attestation is for another kind",
    and a test that only checked "something raised" would pass with this guard deleted.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        vocab = active_vocabulary()
        non_subject = sorted(set(vocab.node_kinds) - set(vocab.subject_kinds))[0]
        assert subject_kind_of_node_id(f"{non_subject}:x") is None
        with pytest.raises(ValidationError) as exc:
            _determination(dst_id=node_id(NodeKind(non_subject), "x"))
        assert "SUBJECT" in str(exc.value)

        # Direct, past the model, so the clause is exercised on its own terms too.
        with pytest.raises(AttestationError, match="SUBJECT"):
            enforce_attestation(_a_determination_kind(), f"{non_subject}:x",
                                _valid_attestation(), edge_id="e:direct")


def test_a_destination_with_no_kind_prefix_is_refused_rather_than_assumed():
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _determination(dst_id="an-opaque-identifier")


def test_an_attestation_for_one_subject_kind_does_not_carry_to_another():
    """A pack with more than one subject: attesting to one kind must not licence the other."""
    with deployment(OTHER_PACK_WITH_DETERMINATIONS):
        subjects = sorted(active_vocabulary().subject_kinds)
        assert len(subjects) > 1, "this test needs a pack with two subject kinds"
        first, second = subjects[0], subjects[1]
        attestation = _valid_attestation(
            subject_kind=second,
            mode=active_vocabulary().attestation_for(second),
            identifier_checked=active_vocabulary().identifiers_for(second)[0],
            grant_type=active_vocabulary().identity_grant_for(second))
        with pytest.raises(ValidationError):
            _determination(dst_id=node_id(NodeKind(first), "x"), attestation=attestation)


# --- a claim stays a claim -----------------------------------------------------------------

def test_a_claim_edge_cannot_be_promoted_by_attaching_an_attestation():
    """`ABOUT` records that an artefact CLAIMS to concern an entity. Two candidates yield two
    such edges and neither is an answer. Letting one carry an attestation would make it read as a
    determination to everything downstream, through a kind that was never checked as one."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError) as exc:
            GraphEdge(id="e:claim", src_id="doc:1", dst_id=_a_subject_id(),
                      kind=_a_claim_kind(), sources=(_pointer(),),
                      attestation=_valid_attestation(),
                      valid_from=_now(), system_from=_now())
        assert "CLAIM" in str(exc.value)


def test_two_candidates_yield_two_claims_and_neither_is_a_determination():
    with deployment(PACK_WITH_DETERMINATIONS):
        claim = _a_claim_kind()
        edges = [GraphEdge(id=f"e:claim{i}", src_id="doc:1", dst_id=_a_subject_id(f"cand{i}"),
                           kind=claim, sources=(_pointer(),),
                           valid_from=_now(), system_from=_now())
                 for i in (1, 2)]
        assert all(e.attestation is None for e in edges)
        assert not any(is_determination_kind(e.kind) for e in edges)


# --- fail closed ----------------------------------------------------------------------------

def test_an_edge_kind_this_deployment_does_not_declare_is_refused():
    """Kinds are interned and survive a rebinding (review finding A7). An edge whose kind the
    enabled packs no longer declare cannot be shown NOT to be a determination, so it is refused
    rather than assumed harmless.

    TWO independent guards catch this, and the second was a surprise worth recording: pydantic's
    `str` schema normalises a `str` SUBCLASS to plain `str` before the after-validator runs, so
    `_Kind._validate`'s `type(v) is cls` short-circuit does not actually fire on the model path
    and the kind IS re-validated against the current vocabulary. A7 therefore does not reach
    `GraphEdge`. It is still asserted directly below, because that behaviour is pydantic's and
    not ours to rely on.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        stale = _a_kind_absent_from(OTHER_PACK_WITH_DETERMINATIONS)
    with deployment(OTHER_PACK_WITH_DETERMINATIONS):
        assert str(stale) not in active_vocabulary().edge_kinds
        with pytest.raises(ValidationError):
            GraphEdge(id="e:stale", src_id="a:1", dst_id="b:2", kind=stale,
                      sources=(_pointer(),), valid_from=_now(), system_from=_now())
        with pytest.raises(AttestationError, match="not declared by any enabled pack"):
            enforce_attestation(stale, "b:2", None, edge_id="e:stale")


def test_a_pack_that_declares_no_determination_kind_simply_cannot_record_one(tmp_path):
    """Fail-closed by construction: with no edge kind bound to an attested meta-kind, there is no
    kind to put on the edge, so the determination cannot be expressed at all.

    This originally bound `healthcare` as its example of such a pack. That was borrowing a real
    pack's GAP as a fixture, and it broke the day the gap was fixed: healthcare is the pack whose
    entire product *is* an identity determination, and it had no edge kind to write one on — the
    determination lived only in `intake.ConfirmationState`, never in the graph. Pinning that state
    would have made the test an argument for leaving the product broken.

    The property is real and worth keeping, so it is now tested against a pack SYNTHESISED to
    declare no determination kind. The property no longer depends on any shipped pack's contents.
    """
    pack_dir = tmp_path / "packs" / "claimsonly"
    pack_dir.mkdir(parents=True)
    (pack_dir / "ontology.yaml").write_text(_a_pack_declaring_only_claims(), encoding="utf-8")
    (tmp_path / "packs" / "deployment.yaml").write_text(
        "enabled:\n  - claimsonly\n", encoding="utf-8")

    # `DATABRIDGE_PACKS_ROOT` is the documented test override for the packs directory.
    previous = os.environ.get("DATABRIDGE_PACKS_ROOT")
    os.environ["DATABRIDGE_PACKS_ROOT"] = str(tmp_path / "packs")
    try:
        with deployment("claimsonly"):
            assert not [k for m in ATTESTED_META_KINDS
                        for k in active_vocabulary().edges_binding_to(m)], (
                "a pack declaring only claim edges must expose no determination kind")
    finally:
        if previous is None:
            os.environ.pop("DATABRIDGE_PACKS_ROOT", None)
        else:
            os.environ["DATABRIDGE_PACKS_ROOT"] = previous


# --- the store re-checks, because construction is not the only path -------------------------

def test_the_store_refuses_a_determination_that_skipped_the_constructor():
    """`model_construct` bypasses validation by design — the same honest limit recorded in
    `test_grounding_wall_cannot_be_walked_around.py`. The persistence boundary is why that limit
    is survivable."""
    with deployment(PACK_WITH_DETERMINATIONS):
        store = GraphStore()
        forged = GraphEdge.model_construct(
            id="e:forged", src_id="doc:1", dst_id=_a_subject_id(),
            kind=_a_determination_kind(), weight=1.0, attrs={}, attr_sources={},
            sources=(_pointer(),), attestation=None,
            valid_from=_now(), valid_to=None, system_from=_now(), system_to=None)
        assert forged.attestation is None            # the constructor was skipped
        with pytest.raises(AttestationError):
            store.add_edge(forged)


def test_a_determination_survives_a_round_trip_through_the_store():
    """If the attestation did not persist, the edge would be unreadable: reading it back rebuilds
    it through `GraphEdge(...)`, whose validator refuses a determination without one."""
    from databridge_graph.store import row_to_edge

    with deployment(PACK_WITH_DETERMINATIONS):
        store = _store_holding(GraphStore(), _a_subject_id())
        edge = _determination()
        store.add_edge(edge)
        rows = list(store.open_edges())
        assert len(rows) == 1
        back = row_to_edge(store, rows[0])
        assert back.attestation == edge.attestation
        assert back.attestation.attestor == "Ada Verifier"


def test_record_determination_is_a_front_door_not_the_only_door():
    with deployment(PACK_WITH_DETERMINATIONS):
        store = _store_holding(GraphStore(), _a_subject_id())
        outcome = record_determination(
            store, edge_id="e:front", src_id="doc:1", dst_id=_a_subject_id(),
            kind=_a_determination_kind(),
            attestation=_valid_attestation(edge_kind=str(_a_determination_kind())),
            sources=(_pointer(),), valid_from=_now())
        assert outcome.action == "inserted"
        assert len(list(store.open_edges())) == 1

        with pytest.raises(ValidationError):
            record_determination(
                store, edge_id="e:front2", src_id="doc:1", dst_id=_a_subject_id(),
                kind=_a_determination_kind(), attestation=None,          # type: ignore[arg-type]
                sources=(_pointer(),), valid_from=_now())


# --- the honest limit -----------------------------------------------------------------------

def test_the_attestor_name_is_not_verified_and_that_is_recorded_here():
    """This module cannot check that a name belongs to a human who was actually present. It
    refuses the empty string and nothing more, and the control that makes the name mean something
    is organisational — the staff login that produced it and the ledger row it lands in.

    Asserted rather than fixed so the boundary is visible in the test suite instead of only in a
    docstring, exactly as `model_construct` is.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        plausible_but_unverified = _valid_attestation(attestor="Someone Who Was Not There")
        assert plausible_but_unverified.attestor == "Someone Who Was Not There"
        edge = _determination(attestation=plausible_but_unverified)
        # `==`, not `is`. The model now revalidates instances (`revalidate_instances="always"`),
        # so a nested Attestation is REBUILT rather than taken on trust — which is the fix for
        # finding 2, and it means identity is no longer preserved across the boundary. The value
        # is; the object is not, and that is the intended trade.
        assert edge.attestation == plausible_but_unverified


def test_an_attestation_cannot_be_edited_after_the_fact():
    with deployment(PACK_WITH_DETERMINATIONS):
        a = _valid_attestation()
        with pytest.raises(ValidationError):
            a.attestor = "Someone Else"                 # type: ignore[misc]
        with pytest.raises(AttributeError):
            a.sources.clear()                           # type: ignore[attr-defined]


def test_an_attestation_refuses_a_field_it_does_not_declare():
    """`extra="forbid"`: an attestation that silently absorbs an unknown field is one nobody can
    read back the same way twice."""
    with deployment(PACK_WITH_DETERMINATIONS):
        with pytest.raises(ValidationError):
            _valid_attestation(automated=True)


def test_backdating_an_attestation_is_visible_rather_than_prevented():
    """The type records WHEN, and does not police it — a clock is not a control. What it does
    guarantee is that the time is unambiguous and immutable once recorded."""
    with deployment(PACK_WITH_DETERMINATIONS):
        old = _valid_attestation(attested_at=_now() - timedelta(days=400))
        assert old.attested_at < _now()


# --- and a model may never propose one -----------------------------------------------------

def test_extraction_refuses_a_model_proposed_determination():
    """The gap that only appeared once the constraint was real.

    `extractor.py` turns the model's `rel_type` into an `EdgeKind` and constructs the edge. Under
    a deployment whose pack declares a determination kind, the model can name it — and the model
    is exactly the party that may not make that call. Before this guard the edge's own validator
    caught it, which is right but arrives as an unhandled `ValidationError` that fails the WHOLE
    extraction. Now it lands in `refused`, where a reviewer can see what was proposed.
    """
    import datetime as dt

    from databridge_convert.ir.models import Block, DocumentRecord
    from databridge_core import SnapshotIdentity
    from databridge_harness import FakeBackend, Harness

    from databridge_graph.extractor import extract_graph, gold_extraction_handler

    with deployment(PACK_WITH_DETERMINATIONS):
        determination = str(_a_determination_kind())
        vocab = active_vocabulary()
        # An entity kind this pack declares for each end, read from the pack rather than named.
        src_kind = sorted(vocab.edge_kinds[determination].src)[0]
        subject = _a_subject_kind()
        text = "Northwind Holdings memo concerning Alexis Rivera, filed this quarter."
        snap = SnapshotIdentity(source_id="d1", as_of=dt.datetime(2026, 3, 1, tzinfo=timezone.utc),
                                content_hash="h", schema_hash="s")
        doc = DocumentRecord(document_id="d1", sha256="x", snapshot=snap,
                             blocks=[Block(kind="Paragraph", text=text)])

        def at(needle):
            i = text.index(needle)
            return {"char_start": i, "char_end": i + len(needle)}

        ents = [{"name": "Northwind Holdings", "kind": src_kind, **at("Northwind Holdings")},
                {"name": "Alexis Rivera", "kind": subject, **at("Alexis Rivera")}]
        rels = [{"src_name": "Northwind Holdings", "rel_type": determination,
                 "dst_name": "Alexis Rivera",
                 "evidence_char_start": 0, "evidence_char_end": len(text)}]
        harness = Harness(FakeBackend(gold_extraction_handler(ents, rels)), profile="cloud")

        result = extract_graph(doc, harness)
        assert result.relations == []
        assert any("DETERMINATION" in r for r in result.refused), result.refused


# ==========================================================================================
# Review findings, 2026-08-16 (codex, `wiki/40-reviews/raw/phase2-3-b2-attestation.md`).
# One test per finding, each written so that removing the fix makes it fail.
# ==========================================================================================

def test_an_attestation_cannot_be_replayed_onto_another_subject():
    """FINDING 1 (Critical) — an attestation was a bearer token for a whole subject kind.

    Nothing bound the record to the act. A valid attestation for one person could be attached to a
    determination about a different person, and the wall compared only the SUBJECT KIND — which
    both share by definition. Each of those edges is a separate human decision that nobody made.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        alice, bob = _a_subject_id("alice"), _a_subject_id("bob")
        for_alice = _valid_attestation(candidate_id="doc:1", subject_id=alice,
                                       edge_kind=str(_a_determination_kind()))
        # It works for the act it was made for.
        assert _determination(id="e:alice", dst_id=alice, attestation=for_alice) is not None
        # And for no other.
        with pytest.raises(ValidationError, match="not made for this edge"):
            _determination(id="e:bob", dst_id=bob, attestation=for_alice)
        with pytest.raises(ValidationError, match="not made for this edge"):
            _determination(id="e:other-doc", src_id="doc:2", dst_id=alice,
                           attestation=for_alice)


def test_an_attestation_cannot_be_replayed_onto_another_determination_kind():
    """FINDING 1, the third leg: the kind of determination is part of the act attested to."""
    with deployment(OTHER_PACK_WITH_DETERMINATIONS):
        kinds = [k for m in ATTESTED_META_KINDS
                 for k in active_vocabulary().edges_binding_to(m)]
        if len(kinds) < 2:
            pytest.skip("this pack declares one determination kind; nothing to swap it with")
        subject = _a_subject_id()
        att = _valid_attestation(candidate_id="doc:1", subject_id=subject, edge_kind=kinds[0])
        with pytest.raises(ValidationError, match="not made for this edge"):
            _determination(dst_id=subject, kind=EdgeKind(kinds[1]), attestation=att)


@pytest.mark.parametrize("corruption", [
    {"attestor": ""},
    {"attested_at": datetime(2026, 8, 16, 9, 0, 0)},      # naive
    {"sources": ()},
])
def test_an_attestation_built_past_the_validators_does_not_survive_the_wall(corruption):
    """FINDING 2 (Critical) — `model_construct` produced attestations the wall accepted.

    The validators for `attestor`, `attested_at` and `sources` never ran, and `check_against()`
    only ever examined the pack-dependent half, so an empty attestor with a naive timestamp and no
    grounding satisfied enforcement — including at the store, which was supposed to be the
    independent second check.

    `enforce_attestation` now REBUILDS the record through the ordinary validators and checks the
    rebuild. Remove `revalidated()` from it and every case here passes silently.
    """
    from databridge_graph.attestation import revalidated

    with deployment(PACK_WITH_DETERMINATIONS):
        good = _valid_attestation()
        forged = Attestation.model_construct(
            **{**{f: getattr(good, f) for f in Attestation.model_fields}, **corruption})
        with pytest.raises(AttestationError):
            revalidated(forged)
        with pytest.raises(AttestationError):
            enforce_attestation(_a_determination_kind(), _a_subject_id(), forged,
                                src_id="doc:1", edge_id="e:forged")


def test_a_forged_determination_cannot_be_persisted_even_though_it_can_be_built():
    """FINDING 3 (Critical) — `model_copy(update=...)` promoted a claim into a determination.

    `model_construct` and `model_copy` bypass every validator in Pydantic; that is a limit of the
    library and not something this codebase can close. What it CAN do is make the store the point
    where it stops mattering, which the coordinator asked for explicitly: the store re-derives the
    meaning of the edge from its own fields and refuses the write.

    Both directions are exercised — a claim promoted to a determination, and a determination
    stripped of its attestation.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        store = _store_holding(GraphStore(), _a_subject_id())
        claim = GraphEdge(id="e:claim", src_id="doc:1", dst_id=_a_subject_id(),
                          kind=_a_claim_kind(), sources=(_pointer(),),
                          valid_from=_now(), system_from=_now())
        promoted = claim.model_copy(update={"kind": _a_determination_kind()})
        assert promoted.kind == _a_determination_kind()      # the object exists
        with pytest.raises(AttestationError):
            store.add_edge(promoted)                          # and cannot become durable

        real = _determination()
        stripped = real.model_copy(update={"attestation": None})
        assert stripped.attestation is None
        with pytest.raises(AttestationError):
            store.add_edge(stripped)


def test_the_store_refuses_a_determination_whose_attestation_was_forged():
    """FINDING 2 + 3 together, at the boundary the coordinator named as the real one."""
    with deployment(PACK_WITH_DETERMINATIONS):
        store = _store_holding(GraphStore(), _a_subject_id())
        good = _valid_attestation()
        forged = Attestation.model_construct(
            **{**{f: getattr(good, f) for f in Attestation.model_fields}, "attestor": ""})
        edge = GraphEdge.model_construct(
            id="e:forged", src_id="doc:1", dst_id=_a_subject_id(),
            kind=_a_determination_kind(), weight=1.0, attrs={}, attr_sources={},
            sources=(_pointer(),), attestation=forged,
            valid_from=_now(), valid_to=None, system_from=_now(), system_to=None)
        with pytest.raises(AttestationError):
            store.add_edge(edge)


def _a_pack_with_its_own_determination_kind() -> str:
    """A pack whose determination edge kind NO shipped pack declares — so a vocabulary that does
    not include it genuinely cannot say what the kind means."""
    return """
pack: ownkind
title: A pack with a determination kind nobody else declares
version: "1.0"
node_kinds:
  - name: doc
    binds_to: document
    description: A document.
  - name: individual
    binds_to: entity
    description: An individual.
edge_kinds:
  - name: attested_link
    binds_to: resolved_to
    src: [doc]
    dst: [individual]
    description: A determination, declared by this pack alone.
subject_kinds:
  - kind: individual
    identifiers: [individual_id]
    never_fuzzy_merge: true
    attestation: in_person
    grant_type: IndividualReadGrant
"""


def test_the_meta_kind_is_resolved_through_the_vocabulary_it_was_given(tmp_path):
    """FINDING 4 (High) — a mixed-snapshot defect.

    `_meta_of` read `kind.meta`, which resolves through the CURRENT GLOBAL deployment, while the
    subject and the attestation were validated against a DIFFERENT, explicitly supplied
    vocabulary. One question answered from snapshot A and the next from snapshot B produces a
    verdict that is right about neither, and the mix is invisible in the failure it produces.

    Set up so the two snapshots genuinely disagree: a pack declares a determination kind that no
    shipped pack knows, it is bound globally, and a shipped pack's vocabulary is passed explicitly.
    With the fix, every question is answered from the supplied snapshot and the honest answer is
    "cannot establish what this kind means" — which fails CLOSED. Revert `_meta_of` to `kind.meta`
    and the classification comes from the global binding instead, so the refusal changes to one
    about the subject: a different verdict, from a snapshot nobody asked about.
    """
    from databridge_graph.packs import build_vocabulary

    pack_dir = tmp_path / "packs" / "ownkind"
    pack_dir.mkdir(parents=True)
    (pack_dir / "ontology.yaml").write_text(
        _a_pack_with_its_own_determination_kind(), encoding="utf-8")

    previous = os.environ.get("DATABRIDGE_PACKS_ROOT")
    os.environ["DATABRIDGE_PACKS_ROOT"] = str(tmp_path / "packs")
    try:
        with deployment("ownkind"):
            kind = EdgeKind("attested_link")
            assert is_determination_kind(kind) is True          # the global binding says so
    finally:
        if previous is None:
            os.environ.pop("DATABRIDGE_PACKS_ROOT", None)
        else:
            os.environ["DATABRIDGE_PACKS_ROOT"] = previous
        clear_caches()

    shipped = build_vocabulary([PACK_WITH_DETERMINATIONS])
    assert "attested_link" not in shipped.edge_kinds
    assert is_determination_kind(kind, shipped) is False
    with pytest.raises(AttestationError, match="not declared by any enabled pack"):
        enforce_attestation(kind, f"{sorted(shipped.subject_kinds)[0]}:x", None,
                            src_id="doc:1", edge_id="e:mixed", vocab=shipped)


def test_an_attestation_dated_in_the_future_cannot_become_durable():
    """FINDING 5 (High) — `attested_at` was unbounded in both directions.

    Backdating stays *visible rather than prevented* (a clock is not a control, and a genuinely
    late-entered attestation is a real thing). Future-dating is different: it is either a wrong
    clock or a pre-signed record, and the store has a clock to compare against, so it does.
    """
    from databridge_graph.attestation import FUTURE_ATTESTATION_SKEW

    with deployment(PACK_WITH_DETERMINATIONS):
        store = _store_holding(GraphStore(), _a_subject_id())
        ahead = _now() + FUTURE_ATTESTATION_SKEW + timedelta(minutes=1)
        edge = _determination(attestation=_valid_attestation(attested_at=ahead))
        with pytest.raises(AttestationError, match="ahead of the store clock"):
            store.add_edge(edge)


def test_system_from_is_the_stores_and_not_the_callers():
    """FINDING 5, second half: `record_determination` used to accept `system_from`.

    System time is when the store LEARNED the fact. A caller that can set it can backdate the
    audit boundary, which makes "what did we know when we decided this?" answerable only by
    whoever wrote the row.
    """
    import inspect

    assert "system_from" not in inspect.signature(record_determination).parameters

    pinned = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with deployment(PACK_WITH_DETERMINATIONS):
        store = _store_holding(GraphStore(clock=lambda: pinned), _a_subject_id())
        record_determination(
            store, edge_id="e:clock", src_id="doc:1", dst_id=_a_subject_id(),
            kind=_a_determination_kind(), attestation=_valid_attestation(),
            sources=(_pointer(),), valid_from=_now())
        row = list(store.open_edges())[0]
        assert row["system_from"].startswith("2030-01-01")


def test_a_determination_must_point_at_a_node_that_exists():
    """FINDING 8 (Medium) — subjecthood was inferred from a STRING.

    `subject_kind_of_node_id` reads a kind prefix out of an id, so `"<subject-kind>:anything"`
    passed the subject test with no such node in the graph. Only the store can check referential
    integrity, so it does — a determination about a subject that does not exist is a determination
    about nobody.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        store = GraphStore()                       # deliberately empty
        edge = _determination(dst_id=_a_subject_id("never-registered"))
        with pytest.raises(AttestationError, match="not an open node"):
            store.add_edge(edge)


def test_one_attestation_cannot_back_two_stored_determinations():
    """FINDING 1, at the store: replay refused by content address, not by trusting the fields.

    The binding check depends on the attestation being well-formed. This one does not: two edges
    carrying the same `attestation_id` is one human act being spent twice, whatever the record
    says about itself.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        subject = _a_subject_id()
        store = _store_holding(GraphStore(), subject)
        att = _valid_attestation(candidate_id="doc:1", subject_id=subject,
                                 edge_kind=str(_a_determination_kind()))
        store.add_edge(_determination(id="e:first", dst_id=subject, attestation=att))
        with pytest.raises(AttestationError, match="already bound to edge"):
            store.add_edge(_determination(id="e:second", dst_id=subject, attestation=att))


def test_the_attestor_principal_is_recorded_but_still_not_verified_here():
    """FINDING 6 (High) — acknowledged, not closed, and the acknowledgement is the test.

    A caller-supplied string cannot establish that a human was present; that requires an
    authenticated session, which this layer does not have and should not invent. What changed is
    that there is now a field for the authenticated principal, so an attestation WITHOUT one is
    visibly weaker rather than indistinguishable from one with it. The control remains
    organisational.
    """
    with deployment(PACK_WITH_DETERMINATIONS):
        anonymous = _valid_attestation()
        assert anonymous.attestor_principal == ""        # nothing vouches for the name
        authenticated = _valid_attestation(attestor_principal="staff-session:9f3c")
        assert authenticated.attestor_principal == "staff-session:9f3c"
        # ...and this module still cannot tell the two apart on the question of who was present.
        assert anonymous.attestor == authenticated.attestor
