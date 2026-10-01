"""`brain_server` under a NON-clinic binding — the identity safeguard, generalised (E2E F-05).

`node_to_dict` and `node_detail` used to branch on `NodeKind.PATIENT` / `EdgeKind.FOR_PATIENT`.
Under `DATABRIDGE_PACKS=workspace` those members do not exist, so `/brain/graph` was a 500
(AttributeError). The fix must NOT drop the caveat — it is hard constraint 1 (a record is not an
identification). It is keyed on what the bound packs DECLARE: a node whose kind is a declared
subject carries the caveat; an edge whose kind binds to the `about` meta-kind is a claim.

Every expectation here is derived from the bound vocabulary, never from a kind name.
"""
from __future__ import annotations

import datetime as dt

import pytest

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
SRC = "larkspur-files"


def _ptr(start=0, end=4):
    from databridge_core import SourcePointer

    return SourcePointer(source_id=SRC, kind="doc_span", document_id="d1",
                         char_start=start, char_end=end, capture_digest="cd" * 32)


#: Two non-clinic bindings: `workspace` (no claim edge reaches its subject) and `finance` (one
#: does). Between them both branches of the claim logic run with no clinic kind bound.
PACKS = ("workspace", "finance")


@pytest.fixture(params=PACKS)
def world(request):
    from databridge_graph import GraphEdge, GraphNode, GraphStore
    from databridge_graph.kinds import EdgeKind, NodeKind
    from databridge_graph.meta import EdgeMetaKind, NodeMetaKind
    from databridge_graph.packs import deployment

    with deployment(request.param) as vocab:
        pairs = [(d, sk, e) for sk in sorted(vocab.subject_kinds)
                 for d in vocab.nodes_binding_to(NodeMetaKind.DOCUMENT)
                 for e in vocab.edges_binding_to(EdgeMetaKind.ABOUT)
                 if vocab.edge_kinds[e].allows("src", d) and vocab.edge_kinds[e].allows("dst", sk)]
        subject = pairs[0][1] if pairs else next(iter(sorted(vocab.subject_kinds)))
        doc = pairs[0][0] if pairs else next(
            k for k in vocab.nodes_binding_to(NodeMetaKind.DOCUMENT) if not vocab.is_subject(k))
        s = GraphStore()
        for nid, label in (("subj:a", "Imogen Farrow"), ("subj:b", "Nobody Claims")):
            s.add_node(GraphNode(id=nid, kind=NodeKind(subject), label=label,
                                 sources=[_ptr()], valid_from=T0, system_from=T0))
        s.add_node(GraphNode(id="doc:1", kind=NodeKind(doc), label="msa-2026.pdf",
                             sources=[_ptr()], valid_from=T0, system_from=T0))
        if pairs:
            s.add_edge(GraphEdge(id="e1", src_id="doc:1", dst_id="subj:a",
                                 kind=EdgeKind(pairs[0][2]), sources=[_ptr()],
                                 valid_from=T0, system_from=T0))
        yield s, vocab, subject, bool(pairs)


def test_at_least_one_binding_exercises_a_claim_edge():
    """Guards the parametrisation: if no listed pack can express doc --about--> subject, the
    claim assertions below would only ever run their empty branch."""
    from databridge_graph.meta import EdgeMetaKind
    from databridge_graph.packs import deployment

    found = False
    for name in PACKS:
        with deployment(name) as v:
            found |= any(v.edge_kinds[e].allows("dst", sk)
                         for e in v.edges_binding_to(EdgeMetaKind.ABOUT) for sk in v.subject_kinds)
    assert found


def test_graph_serialises_under_a_binding_with_no_clinic_kinds(world):
    """F-05 reproduction: this raised AttributeError (NodeKind has no PATIENT)."""
    from brain_server import BrainTools, subject_node_caveat

    store, vocab, _, _ = world
    nodes = BrainTools(store).graph()["nodes"]
    subjects = [n for n in nodes if vocab.is_subject(n["kind"])]
    assert subjects, "no subject node in the fixture — the caveat check below is vacuous"
    for n in nodes:
        if vocab.is_subject(n["kind"]):
            assert n["identity_caveat"] == subject_node_caveat(n["kind"])
            assert n["identity_verified"] is False
        else:
            assert "identity_caveat" not in n


def test_the_caveat_names_the_attestation_the_pack_declares(world):
    """A pack whose subject is attested over a verified channel must not be told "in person"."""
    from brain_server import subject_node_caveat

    _, vocab, subject, _ = world
    text = subject_node_caveat(subject)
    assert "not an identification" in text
    assert ("in person" in text) is (vocab.attestation_for(subject) == "in_person")


def test_node_detail_of_a_subject_carries_the_identity_block_with_its_claimants(world):
    from brain_server import BrainTools

    store, _, _, has_claim = world
    tools = BrainTools(store)
    claimed = tools.node_detail("subj:a")
    assert claimed["identity"]["verified"] is False
    assert claimed["identity"]["requires_human_verification"] is True
    assert claimed["identity"]["claimed_by"] == (["doc:1"] if has_claim else [])
    assert claimed["identity"]["claimed_by_a_document"] is has_claim
    unclaimed = tools.node_detail("subj:b")
    assert unclaimed["identity"]["claimed_by"] == []
    assert unclaimed["identity"]["claimed_by_a_document"] is False
    assert (unclaimed["identity"]["caveat"] != claimed["identity"]["caveat"]) is has_claim


def test_node_detail_of_a_document_lists_its_claims_as_unverified_candidates(world):
    from brain_server import BrainTools

    store, _, _, has_claim = world
    detail = BrainTools(store).node_detail("doc:1")
    assert [c["id"] for c in detail["claims"]] == (["subj:a"] if has_claim else [])
    assert all(c["requires_human_verification"] is True for c in detail["claims"])
    assert "identity" not in detail, "a document is not a subject"


# --- Codex f1 #6: identity wording follows the pack's declared attestation -------------------------
def test_node_detail_identity_caveat_follows_the_declared_attestation(world):
    """`node_detail` used two fixed strings that named one pack's subject and said "in person"
    whatever the pack declared."""
    from brain_server import BrainTools, attestation_clause

    store, vocab, subject, _ = world
    caveat = BrainTools(store).node_detail("subj:a")["identity"]["caveat"]
    assert attestation_clause(subject) in caveat
    assert ("in person" in caveat) is (vocab.attestation_for(subject) == "in_person")
    assert subject.replace("_", " ") in caveat


def test_insight_identity_wording_follows_the_declared_attestation(world):
    from brain_server import BrainTools, attestation_clause

    store, vocab, subject, has_claim = world
    texts = [i["text"] for i in BrainTools(store).insights()["insights"]]
    queue = [t for t in texts if "awaiting" in t]
    if not has_claim:
        assert not queue
        return
    assert queue, texts
    assert attestation_clause(subject) in queue[0]
    assert ("in person" in queue[0]) is (vocab.attestation_for(subject) == "in_person")


def test_every_attestation_mode_has_its_own_clause():
    """The clause table must cover the engine's closed attestation vocabulary — a mode it does not
    know falls back to the generic clause, which is safe but says less."""
    from brain_server import _ATTESTATION_CLAUSE
    from databridge_graph.packs import ATTESTATION_MODES

    assert set(ATTESTATION_MODES) <= set(_ATTESTATION_CLAUSE), set(ATTESTATION_MODES)
