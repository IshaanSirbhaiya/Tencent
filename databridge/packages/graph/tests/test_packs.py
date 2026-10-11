"""The three-layer schema (`00_SPINE.md` §4) and the D4b subject declaration.

Layer 0 is domain-free, Layer 1 is the pack YAML, Layer 2 is the deployment binding. What these
tests actually pin is the claim that makes the layering worth having: **the identity guarantee is
derived from a pack declaration, not written down in engine code**, and it does not weaken when
the domain changes.
"""
from __future__ import annotations

import textwrap

import pytest

from databridge_graph.kinds import EdgeKind, NodeKind, node_kinds_binding_to
from databridge_graph.meta import (
    SUBJECT_ELIGIBLE_META_KINDS,
    EdgeMetaKind,
    NodeMetaKind,
)
from databridge_graph.packs import (
    PackError,
    UnknownKind,
    active_vocabulary,
    available_packs,
    build_vocabulary,
    clear_caches,
    configured_packs,
    deployment,
    load_pack,
    never_fuzzy_merge_kinds,
    packs_root,
)

SHIPPED = ("finance", "government", "healthcare")


# --- Layer 1: every shipped pack loads and validates -------------------------------
def test_every_shipped_pack_is_on_disk():
    assert set(SHIPPED) <= set(available_packs())


@pytest.mark.parametrize("name", SHIPPED)
def test_pack_loads_and_binds_every_kind_to_a_meta_kind(name):
    pack = load_pack(name)
    assert pack.name == name
    assert pack.node_kinds and pack.edge_kinds
    for decl in pack.node_kinds.values():
        assert isinstance(decl.binds_to, NodeMetaKind)
        assert decl.pack == name
    for decl in pack.edge_kinds.values():
        assert isinstance(decl.binds_to, EdgeMetaKind)


@pytest.mark.parametrize("name", SHIPPED)
def test_pack_edge_endpoints_name_declared_kinds(name):
    """An edge pointing at a kind the pack never declared is a typo that would otherwise only
    surface as a silently-skipped extraction."""
    pack = load_pack(name)
    for e in pack.edge_kinds.values():
        for end in (*e.src, *e.dst):
            assert end == "*" or end in pack.node_kinds, f"{name}.{e.name} → {end}"


@pytest.mark.parametrize("name,expected", [
    ("healthcare", {"patient"}),
    ("finance", {"party"}),
])
def test_pack_declares_the_subject_kinds_the_spine_names(name, expected):
    assert set(load_pack(name).subject_kinds) >= expected


def test_government_declares_the_two_proceeding_roles_as_subjects():
    # Spine D4b's table names `applicant` and `claimant`. The pack declares `person` and
    # `organisation` alongside them; see the comment in its YAML for why that widening is
    # deliberate. `<=` rather than `==` on purpose: a pack may protect MORE than D4b names, and
    # the direction that must never happen silently is the other one — a subject dropping off the
    # list takes `never_fuzzy_merge` with it.
    assert {"applicant", "claimant"} <= set(load_pack("government").subject_kinds)


def test_a_subject_may_be_added_to_a_pack_but_the_spines_named_ones_may_not_be_dropped():
    """The asymmetry above, made explicit rather than left implied by a `<=`.

    Widening the protected set costs an extra human confirmation. Narrowing it silently removes
    the guardrail from a kind that had it, and nothing downstream announces the loss — the
    resolver simply starts merging. So the spine's table is a FLOOR, per pack.
    """
    floor = {"finance": {"party"}, "government": {"applicant", "claimant"},
             "healthcare": {"patient"}}
    for pack, named in floor.items():
        declared = set(load_pack(pack).subject_kinds)
        assert named <= declared, f"{pack} dropped a subject the spine names: {named - declared}"


@pytest.mark.parametrize("name", SHIPPED)
def test_subject_kinds_are_entity_shaped_and_carry_a_grant(name):
    pack = load_pack(name)
    for s in pack.subject_kinds.values():
        assert pack.node_kinds[s.kind].binds_to in SUBJECT_ELIGIBLE_META_KINDS
        assert s.never_fuzzy_merge is True
        assert s.identifiers
        assert s.grant_type


# --- Layer 2: the deployment binding closes the vocabulary -------------------------
def test_default_binding_is_declared_not_guessed():
    """DERIVED from the declaration, not a literal.

    This used to assert `("healthcare",)`. That made the test a second, silent declaration of the
    binding: enabling `finance` and `government` in `packs/deployment.yaml` on 2026-08-16 — which
    the file itself said to do once their estates landed — turned a correct configuration change
    into four red tests. A test for "declared, not guessed" must read the declaration.
    """
    import yaml

    declared = tuple(yaml.safe_load(
        (packs_root() / "deployment.yaml").read_text(encoding="utf-8"))["enabled_packs"])
    assert declared, "deployment.yaml enables nothing - the loader should have refused it"
    assert configured_packs() == declared
    assert active_vocabulary().packs == declared


def test_binding_more_packs_widens_the_vocabulary_and_nothing_else():
    with deployment("finance", "government") as vocab:
        assert vocab.packs == ("finance", "government")
        assert "party" in vocab.node_kinds and "permit" in vocab.node_kinds
        assert "patient" not in vocab.node_kinds       # not enabled ⇒ not in the vocabulary
        assert NodeKind("party").pack == "finance"
        with pytest.raises(UnknownKind):
            NodeKind("patient")
    # restored - to whatever the deployment declares, not to a pack named here
    assert active_vocabulary().packs == configured_packs()


def test_vocabulary_is_closed():
    with pytest.raises(UnknownKind):
        NodeKind("a_kind_no_pack_declares")
    with pytest.raises(UnknownKind):
        EdgeKind("a_kind_no_pack_declares")


def test_kinds_keep_the_enum_surface_the_tree_depends_on():
    k = NodeKind("document")
    assert k.value == "document" and k.name == "DOCUMENT"
    assert k == "document" and isinstance(k, str)
    assert NodeKind.DOCUMENT is k                       # interned: `is` comparison still works
    assert k in set(NodeKind)
    assert len(set(NodeKind)) == len(active_vocabulary().node_kinds)
    with pytest.raises(AttributeError):
        NodeKind.NOT_A_MEMBER


def test_kind_answers_its_meta_kind_so_engine_code_need_not_know_the_domain():
    with deployment("finance"):
        assert NodeKind("credit_memo").meta is NodeMetaKind.DOCUMENT
        assert NodeKind("transaction").meta is NodeMetaKind.EVENT
        assert EdgeKind("memo_about").meta is EdgeMetaKind.ABOUT
        docs = set(node_kinds_binding_to(NodeMetaKind.DOCUMENT))
        # A FLOOR, not an equality. The predefined library is meant to grow — pinning the exact
        # membership here would make every honest ontology addition look like a regression, which
        # is how a test stops being read and starts being edited.
        assert {"credit_memo", "filing"} <= docs
        assert all(NodeKind(d).meta is NodeMetaKind.DOCUMENT for d in docs)


def test_two_packs_may_not_bind_one_kind_name_to_two_shapes(tmp_path):
    """Node ids are ``f"{kind}:{slug}"``, so a name meaning two shapes makes the id space
    ambiguous — and a time-travel answer wrong."""
    for name, meta in (("alpha", "entity"), ("beta", "document")):
        d = tmp_path / name
        d.mkdir()
        (d / "ontology.yaml").write_text(textwrap.dedent(f"""
            pack: {name}
            version: "1.0"
            node_kinds:
              - name: thing
                binds_to: {meta}
            edge_kinds:
              - name: references
                binds_to: references
        """), encoding="utf-8")
    clear_caches()
    try:
        with pytest.raises(PackError, match="bind it to"):
            build_vocabulary(["alpha", "beta"], tmp_path)
    finally:
        clear_caches()


# --- D4b: the identity guarantee, derived --------------------------------------------
def test_never_fuzzy_merge_is_derived_from_the_enabled_packs():
    """The set the resolver enforces is COMPUTED from `subject_kinds`. If this were a literal in
    engine code, changing the domain would silently drop the protection."""
    # IDN (E2E run 5 F-04): derived from the packs' own declarations, never written down here,
    # so a deliberate widening of a pack's subjects (finance `contact`, government `officer`)
    # moves the expectation with it. Non-vacuous: every pack named declares subjects.
    def declared(*packs):
        out = frozenset().union(*(frozenset(load_pack(p).subject_kinds) for p in packs))
        assert out
        return out

    with deployment("healthcare"):
        assert never_fuzzy_merge_kinds() == declared("healthcare")
        assert "patient" in never_fuzzy_merge_kinds()
    with deployment("finance"):
        assert never_fuzzy_merge_kinds() == declared("finance")
        assert {"party", "counterparty", "beneficial_owner"} <= never_fuzzy_merge_kinds()
    with deployment("finance", "government"):
        assert never_fuzzy_merge_kinds() == declared("finance", "government")


def test_the_old_constant_name_is_gone_rather_than_made_dynamic():
    """`merge.NEVER_FUZZY_MERGE` used to resolve through a module `__getattr__`, on the reasoning
    that the name is what the ADRs and reviews call this set.

    Review (2026-08-16, A5) showed the reasoning was wrong: a module `__getattr__` cannot make a
    *from-import* dynamic. `from databridge_graph.merge import NEVER_FUZZY_MERGE` evaluates once
    and binds the frozenset into the importing module for the life of the process, so an importer
    that loaded under one pack keeps protecting that pack's subject after a rebinding — while
    believing it is protected. The name is therefore removed, not fixed.

    `test_never_fuzzy_merge_is_not_importable.py` is the general form of this assertion.
    """
    import databridge_graph.merge as merge

    with pytest.raises(AttributeError):
        merge.NEVER_FUZZY_MERGE
    with deployment("healthcare"):
        assert merge.never_fuzzy_merge_kinds() == frozenset(load_pack("healthcare").subject_kinds)
    with deployment("government"):
        assert merge.never_fuzzy_merge_kinds() == frozenset(load_pack("government").subject_kinds)
        assert {"applicant", "claimant", "person", "organisation"} <=             merge.never_fuzzy_merge_kinds()


def test_never_fuzzy_merge_is_not_written_down_in_engine_code():
    """Grep-level proof that the D4b set has no literal twin. `merge.py` may name the *function*;
    it may not name a kind."""
    import databridge_graph.merge as merge

    src = (merge.__file__ and open(merge.__file__, encoding="utf-8").read()) or ""
    for kind in active_vocabulary().node_kinds:
        assert f'"{kind}"' not in src and f"'{kind}'" not in src, (
            f"merge.py names the kind {kind!r} as a literal — the subject set must come from the "
            f"pack declaration, never from engine code")


def test_a_pack_with_no_subject_kind_gets_no_identity_resolution(tmp_path):
    """Spine D4b: it does not silently get an unguarded one."""
    d = tmp_path / "quiet"
    d.mkdir()
    (d / "ontology.yaml").write_text(textwrap.dedent("""
        pack: quiet
        version: "1.0"
        node_kinds:
          - name: thing
            binds_to: entity
        edge_kinds:
          - name: references
            binds_to: references
    """), encoding="utf-8")
    clear_caches()
    try:
        vocab = build_vocabulary(["quiet"], tmp_path)
        assert vocab.subject_kinds == {}
        # R-029 Codex round 1 (IDN): person protection is fail-closed — an entity kind its pack
        # did not declare `person: false` is never merged by name, subject or not
        assert vocab.never_fuzzy_merge == frozenset({"thing"})
        assert vocab.is_subject("thing") is False
        assert vocab.identity_grant_for("thing") is None      # no fallback grant, by design
        assert vocab.attestation_for("thing") is None
    finally:
        clear_caches()


@pytest.mark.parametrize("body,match", [
    ("subject_kinds:\n  - kind: nope\n    identifiers: [x]\n    attestation: in_person\n"
     "    grant_type: G", "not a node kind"),
    ("subject_kinds:\n  - kind: rulebook\n    identifiers: [x]\n    attestation: in_person\n"
     "    grant_type: G", "no identity to attest to"),
    ("subject_kinds:\n  - kind: thing\n    identifiers: [x]\n    never_fuzzy_merge: false\n"
     "    attestation: in_person\n    grant_type: G", "never_fuzzy_merge"),
    ("subject_kinds:\n  - kind: thing\n    identifiers: []\n    attestation: in_person\n"
     "    grant_type: G", "no identifiers"),
    ("subject_kinds:\n  - kind: thing\n    identifiers: [x]\n    attestation: vibes\n"
     "    grant_type: G", "attestation"),
])
def test_loader_refuses_a_subject_declaration_that_would_weaken_the_guarantee(
        tmp_path, body, match):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "ontology.yaml").write_text(
        'pack: bad\nversion: "1.0"\n'
        'node_kinds:\n  - name: thing\n    binds_to: entity\n'
        '  - name: rulebook\n    binds_to: rule\n'
        'edge_kinds:\n  - name: references\n    binds_to: references\n' + body,
        encoding="utf-8")
    clear_caches()
    try:
        with pytest.raises(PackError, match=match):
            load_pack("bad", tmp_path)
    finally:
        clear_caches()


@pytest.mark.parametrize("yaml_text,match", [
    ('pack: other\nversion: "1"\nnode_kinds: []\nedge_kinds: []', "directory"),
    ('pack: bad\nnode_kinds: []\nedge_kinds: []', "version"),
    ('pack: bad\nversion: "1"\nedge_kinds: []', "node_kinds"),
    ('pack: bad\nversion: "1"\nnode_kinds:\n  - name: Thing\n    binds_to: entity\n'
     'edge_kinds: []', "snake_case"),
    ('pack: bad\nversion: "1"\nnode_kinds:\n  - name: thing\n    binds_to: sideways\n'
     'edge_kinds: []', "not a node meta-kind"),
    ('pack: bad\nversion: "1"\nnode_kinds:\n  - name: thing\n    binds_to: entity\n'
     'edge_kinds:\n  - name: rel\n    binds_to: sideways', "not an edge meta-kind"),
    ('pack: bad\nversion: "1"\nnode_kinds:\n  - name: thing\n    binds_to: entity\n'
     'edge_kinds:\n  - name: rel\n    binds_to: references\n    dst: [ghost]', "does not declare"),
])
def test_loader_refuses_a_malformed_pack(tmp_path, yaml_text, match):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "ontology.yaml").write_text(yaml_text, encoding="utf-8")
    clear_caches()
    try:
        with pytest.raises(PackError, match=match):
            load_pack("bad", tmp_path)
    finally:
        clear_caches()


def test_a_deployment_with_no_binding_is_refused_not_guessed(tmp_path):
    """Binding "every pack on disk" would make the vocabulary open, which is exactly the property
    the closure exists to deny."""
    clear_caches()
    try:
        with pytest.raises(PackError, match="no deployment binding"):
            configured_packs(tmp_path)
    finally:
        clear_caches()


def test_shipped_packs_all_build_into_one_vocabulary():
    vocab = build_vocabulary(SHIPPED, packs_root())
    assert len(vocab.node_kinds) >= 30
    assert set(vocab.packs) == set(SHIPPED)
