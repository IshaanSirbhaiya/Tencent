"""The pack loader as a **trust boundary** — review findings, 2026-08-16.

Source: `wiki/40-reviews/raw/phase2-3-b5-packs.md` (codex, independent second opinion).

`test_packs.py` already pins what the three-layer schema is *for*. This file pins the separate
claim that the layering only means something if it holds: **the enabled vocabulary is exactly the
packs in this tree, decided once, under one binding, and not widenable by a name.**

One test per finding, each written so that removing the fix makes it fail.
"""
from __future__ import annotations

import os
import textwrap
import threading
from pathlib import Path

import pytest

from databridge_graph.packs import (
    PackError,
    active_vocabulary,
    bind_deployment,
    binding_generation,
    build_vocabulary,
    check_pack_name,
    clear_caches,
    configured_packs,
    deployment,
    load_pack,
    packs_root,
)


def _write_pack(root: Path, name: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "ontology.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return d


_MINIMAL = """
    pack: {name}
    version: "1.0"
    node_kinds:
      - name: thing
        binds_to: entity
        description: A thing.
    edge_kinds:
      - name: references
        binds_to: references
        description: a cites b.
"""


@pytest.fixture(autouse=True)
def _isolated_caches():
    """Every test here writes pack files or rebinds; a shared cache would make them order-dependent
    in exactly the way finding 6 is about."""
    clear_caches()
    yield
    clear_caches()


# --- FINDING 1 (Critical): a name may not escape the packs root ---------------------------
@pytest.mark.parametrize("name", [
    "../external",
    "..",
    "a/b",
    "a\\b",
    "/etc/walnut",
    ".hidden",
    "Government",          # case matters: the id space is lowercase
    "",
    "pack name",
])
def test_a_pack_name_that_is_a_path_is_refused(name):
    """A pack name is a DIRECTORY under the root, never a path.

    `load_pack` used to build `root / name / "ontology.yaml"` and resolve it without ever checking
    the result stayed under `root`. `DATABRIDGE_PACKS=../../somewhere`, an absolute path, or a
    symlinked pack directory therefore loaded an ontology from anywhere on disk — which does not
    merely widen the vocabulary, it makes "the vocabulary is closed to the enabled packs" a
    sentence about the filesystem rather than about the deployment.
    """
    with pytest.raises(PackError):
        check_pack_name(name)


def test_a_traversal_name_cannot_load_an_ontology_from_outside_the_root(tmp_path):
    """The end-to-end version of the finding, proving the escape is really closed.

    A perfectly valid pack is planted OUTSIDE the packs root. Reaching it needs only a name that
    walks up one directory — which is what an attacker-supplied or misconfigured `DATABRIDGE_PACKS`
    can be.
    """
    outside = tmp_path / "outside"
    _write_pack(outside, "smuggled", _MINIMAL.format(name="smuggled"))
    root = tmp_path / "packs"
    root.mkdir()
    _write_pack(root, "legit", _MINIMAL.format(name="legit"))

    assert (outside / "smuggled" / "ontology.yaml").is_file()      # it is genuinely reachable
    with pytest.raises(PackError):
        load_pack("../outside/smuggled", root)
    # ...and via the deployment binding, which is the route that matters.
    with pytest.raises(PackError):
        bind_deployment(["../outside/smuggled"])


def test_an_absolute_name_cannot_load_an_ontology_from_outside_the_root(tmp_path):
    outside = tmp_path / "outside"
    _write_pack(outside, "smuggled", _MINIMAL.format(name="smuggled"))
    root = tmp_path / "packs"
    root.mkdir()
    with pytest.raises(PackError):
        load_pack(str(outside / "smuggled"), root)


def test_the_env_override_is_validated_too(tmp_path, monkeypatch):
    """`DATABRIDGE_PACKS` is the most externally-controlled of the three binding sources."""
    root = tmp_path / "packs"
    root.mkdir()
    _write_pack(root, "legit", _MINIMAL.format(name="legit"))
    monkeypatch.setenv("DATABRIDGE_PACKS", "../outside/smuggled")
    bind_deployment(None)
    with pytest.raises(PackError):
        configured_packs(root)


@pytest.mark.skipif(os.name == "nt" and not os.environ.get("DATABRIDGE_TEST_SYMLINKS"),
                    reason="creating a symlink on Windows needs privilege; set "
                           "DATABRIDGE_TEST_SYMLINKS=1 in an elevated shell to run it")
def test_a_symlinked_pack_directory_is_refused(tmp_path):
    """Containment alone is not enough: a link INSIDE the root can serve bytes from outside it."""
    outside = tmp_path / "outside"
    _write_pack(outside, "smuggled", _MINIMAL.format(name="smuggled"))
    root = tmp_path / "packs"
    root.mkdir()
    (root / "aliased").symlink_to(outside / "smuggled", target_is_directory=True)
    with pytest.raises(PackError, match="symbolic link"):
        load_pack("aliased", root)


# --- FINDING 10 (Low): the cache may not be reached before the name is checked -------------
def test_the_cache_is_not_reachable_before_the_name_and_containment_are_checked(tmp_path):
    """`_PACK_CACHE` was keyed on the RESOLVED path and consulted before `declared == name`.

    So a second, differently-spelled route to an already-cached ontology returned that Pack
    immediately, and `Vocabulary.packs` ended up naming the alias while the declarations named the
    original — two names for one vocabulary, only one of which was ever validated.
    """
    root = tmp_path / "packs"
    root.mkdir()
    _write_pack(root, "real", _MINIMAL.format(name="real"))
    first = load_pack("real", root)
    assert first.name == "real"
    with pytest.raises(PackError):
        load_pack("./real", root)
    with pytest.raises(PackError):
        load_pack("../packs/real", root)


# --- FINDING 8 (Medium): the pack schema is closed -----------------------------------------
def test_a_misspelled_subject_kinds_key_is_refused_rather_than_ignored(tmp_path):
    """The failure this prevents is the worst kind: silent and security-relevant.

    `subject_kind:` instead of `subject_kinds:` used to load a pack with NO subject protections at
    all — no `never_fuzzy_merge`, no attestation requirement, no identity grant — and say nothing.
    The guardrail was absent exactly where somebody had tried to write it.
    """
    root = tmp_path / "packs"
    _write_pack(root, "typo", _MINIMAL.format(name="typo") + textwrap.dedent("""
        subject_kind:
          - kind: thing
            identifiers: [thing_id]
            never_fuzzy_merge: true
            attestation: in_person
            grant_type: ThingReadGrant
    """))
    with pytest.raises(PackError, match="subject_kind"):
        load_pack("typo", root)


_TYPO_IN_A_NODE_ENTRY = """
    pack: strict
    version: "1.0"
    node_kinds:
      - name: thing
        binds_to: entity
        descripton: the description key, misspelled
    edge_kinds:
      - name: references
        binds_to: references
        description: a cites b.
"""

_TYPO_IN_AN_EDGE_ENTRY = """
    pack: strict
    version: "1.0"
    node_kinds:
      - name: thing
        binds_to: entity
        description: A thing.
    edge_kinds:
      - name: references
        binds_to: references
        scr: [thing]
        description: a cites b.
"""


@pytest.mark.parametrize("body", [_TYPO_IN_A_NODE_ENTRY, _TYPO_IN_AN_EDGE_ENTRY])
def test_an_unknown_key_inside_an_entry_is_refused(tmp_path, body):
    """`scr:` for `src:` would silently give an edge no endpoints at all, and an edge whose
    endpoints are empty documents nothing and constrains nothing."""
    root = tmp_path / "packs"
    _write_pack(root, "strict", body)
    with pytest.raises(PackError, match="no pack schema declares"):
        load_pack("strict", root)


@pytest.mark.parametrize("identifiers", ["[null]", '[""]', '["  "]', "[thing_id, thing_id]"])
def test_a_blank_or_duplicated_subject_identifier_is_refused(tmp_path, identifiers):
    """`[null]` used to become `("None",)` and satisfy the non-empty check — an identifier that
    names nothing, presented to a human as the thing they should hold up and compare."""
    root = tmp_path / "packs"
    _write_pack(root, "blank", _MINIMAL.format(name="blank") + textwrap.dedent(f"""
        subject_kinds:
          - kind: thing
            identifiers: {identifiers}
            never_fuzzy_merge: true
            attestation: in_person
            grant_type: ThingReadGrant
    """))
    with pytest.raises(PackError):
        load_pack("blank", root)


def test_a_blank_grant_type_is_refused(tmp_path):
    root = tmp_path / "packs"
    _write_pack(root, "nogrant", _MINIMAL.format(name="nogrant") + textwrap.dedent("""
        subject_kinds:
          - kind: thing
            identifiers: [thing_id]
            never_fuzzy_merge: true
            attestation: in_person
            grant_type: "   "
    """))
    with pytest.raises(PackError):
        load_pack("nogrant", root)


# --- FINDING 9 (Medium): the deployment file is validated, not duck-typed -------------------
def test_a_non_mapping_deployment_file_is_refused_by_name(tmp_path):
    """It used to raise a bare `AttributeError` from a module whose contract is "fail loud, name
    the file"."""
    root = tmp_path / "packs"
    root.mkdir()
    _write_pack(root, "legit", _MINIMAL.format(name="legit"))
    (root / "deployment.yaml").write_text("- legit\n", encoding="utf-8")
    bind_deployment(None)
    with pytest.raises(PackError, match="must be a mapping"):
        configured_packs(root)


# --- FINDING 5 (High): identity declarations may not be settled by list order ---------------
def _pack_with_subject(name: str, *, identifiers: str, attestation: str, grant: str) -> str:
    return textwrap.dedent(f"""
        pack: {name}
        version: "1.0"
        node_kinds:
          - name: subject_a
            binds_to: entity
            description: A subject.
        edge_kinds:
          - name: references
            binds_to: references
            description: a cites b.
        subject_kinds:
          - kind: subject_a
            identifiers: {identifiers}
            never_fuzzy_merge: true
            attestation: {attestation}
            grant_type: {grant}
    """)


def test_two_packs_may_not_disagree_about_one_subjects_identity_guarantee(tmp_path):
    """`setdefault` kept whichever pack was listed FIRST and discarded the second in silence.

    So which identifiers a human may check, and which act satisfies the attestation, depended on
    the order of `enabled_packs`. An identity guarantee settled by list position is not a guarantee
    — and the direction of the failure is unbounded: the weaker declaration wins whenever it
    happens to be first.
    """
    root = tmp_path / "packs"
    _write_pack(root, "alpha", _pack_with_subject(
        "alpha", identifiers="[national_id]", attestation="in_person", grant="SubjectReadGrant"))
    _write_pack(root, "beta", _pack_with_subject(
        "beta", identifiers="[email]", attestation="verified_channel", grant="SubjectReadGrant"))
    with pytest.raises(PackError, match="disagree about its identity guarantee"):
        build_vocabulary(["alpha", "beta"], root)
    # ...and the reverse order is refused identically, which is the point.
    with pytest.raises(PackError, match="disagree about its identity guarantee"):
        build_vocabulary(["beta", "alpha"], root)


def test_identical_subject_declarations_agree_and_keep_both_pointers(tmp_path):
    """Agreement is permitted, and both origins are kept: two packs asserting the same guarantee
    is two pieces of evidence for it, and dropping one loses the fact that it was checked."""
    root = tmp_path / "packs"
    same = dict(identifiers="[national_id]", attestation="in_person", grant="SubjectReadGrant")
    _write_pack(root, "alpha", _pack_with_subject("alpha", **same))
    _write_pack(root, "beta", _pack_with_subject("beta", **same))
    vocab = build_vocabulary(["alpha", "beta"], root)
    subject = vocab.subject_kinds["subject_a"]
    assert len(subject.sources) == 2
    assert {Path(s.source_id).parent.name for s in subject.sources} == {"alpha", "beta"}


# --- FINDING 4 (High): a declaration says where it came from --------------------------------
def test_every_declaration_carries_a_pointer_at_the_entry_it_came_from():
    """A declaration carrying only a pack NAME cannot be re-executed: "which file, which entry?"
    has no answer, and a value derived from it is grounded in a string."""
    vocab = active_vocabulary()
    for decl in (*vocab.node_kinds.values(), *vocab.edge_kinds.values(),
                 *vocab.subject_kinds.values()):
        assert decl.sources, f"{decl} carries no source pointer"
        for pointer in decl.sources:
            assert pointer.kind == "table_cell"
            assert Path(pointer.source_id).name == "ontology.yaml"
            assert Path(pointer.source_id).is_file()


def test_a_merged_edge_declaration_names_every_pack_that_contributed(tmp_path):
    """`references` and `supersedes` are shared across packs on purpose, and the merged endpoint
    union is genuinely in neither file alone. Attributing the composite to whichever pack came
    first makes it unre-executable — a reader following `pack` back to one `ontology.yaml` finds a
    declaration that does not match what the vocabulary holds.
    """
    root = tmp_path / "packs"
    _write_pack(root, "alpha", textwrap.dedent("""
        pack: alpha
        version: "1.0"
        node_kinds:
          - name: a_thing
            binds_to: entity
            description: A thing.
        edge_kinds:
          - name: references
            binds_to: references
            src: [a_thing]
            dst: [a_thing]
            description: a cites b.
    """))
    _write_pack(root, "beta", textwrap.dedent("""
        pack: beta
        version: "1.0"
        node_kinds:
          - name: b_thing
            binds_to: document
            description: A document.
        edge_kinds:
          - name: references
            binds_to: references
            src: [b_thing]
            dst: [b_thing]
            description: a cites b.
    """))
    vocab = build_vocabulary(["alpha", "beta"], root)
    merged = vocab.edge_kinds["references"]
    assert set(merged.src) == {"a_thing", "b_thing"}
    assert merged.packs == ("alpha", "beta")
    assert len(merged.sources) == 2


# --- FINDING 2 (High): a rebinding race may not poison the cache -----------------------------
def test_a_concurrent_rebinding_cannot_publish_under_the_wrong_key():
    """The key used to be assembled from `_BINDING` read WITHOUT the lock, so a rebind between
    reading the key and building the vocabulary published binding B's vocabulary under binding A's
    key — and it STAYED there, so every later request for A got B.

    Driven hard rather than mocked: two threads flip the binding while a third reads, and the
    invariant is checked on the *returned* object rather than on the cache, because a caller only
    ever sees what it was handed.
    """
    errors: list[str] = []

    def flip(names):
        for _ in range(60):
            bind_deployment(list(names))
            vocab = active_vocabulary()
            if set(vocab.packs) != set(names):
                errors.append(f"asked for {names}, got {vocab.packs}")

    threads = [threading.Thread(target=flip, args=(n,))
               for n in (("healthcare",), ("finance",), ("government",))]
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        bind_deployment(None)
    # Each thread rebinds and reads under a process-global binding, so interleaving means a thread
    # can legitimately observe another's binding. What must NEVER happen is a *cached* answer that
    # outlives its binding — asserted below, after the threads are done and the binding is stable.
    assert active_vocabulary().packs == configured_packs()


def test_a_rebinding_advances_the_generation_so_a_stale_snapshot_is_detectable():
    """FINDING 7 (Medium). Nothing can stop a caller holding an old `Vocabulary`; what was missing
    was any way to NOTICE. A resolver that captured one at import kept enforcing the old pack's
    subject policy while believing it was current."""
    before = active_vocabulary()
    assert before.is_current()
    with deployment("finance"):
        assert not before.is_current()
        inner = active_vocabulary()
        assert inner.is_current()
    assert not inner.is_current()
    assert binding_generation() > before.generation


def test_clearing_caches_advances_the_generation_too():
    snapshot = active_vocabulary()
    assert snapshot.is_current()
    clear_caches()
    assert not snapshot.is_current()


# --- FINDING 6 (High): an edited pack file is not served from cache -------------------------
def test_editing_an_ontology_invalidates_its_cached_parse(tmp_path):
    """`_PACK_CACHE` was keyed on the path alone, so an edit was invisible for the life of the
    process and only the externally-callable `clear_caches()` refreshed it. The key now carries the
    file's size and mtime, which costs one stat at LOAD time and nothing on the hot path."""
    root = tmp_path / "packs"
    _write_pack(root, "editable", _MINIMAL.format(name="editable"))
    first = load_pack("editable", root)
    assert "thing" in first.node_kinds and "second_thing" not in first.node_kinds

    (root / "editable" / "ontology.yaml").write_text(textwrap.dedent("""
        pack: editable
        version: "1.1"
        node_kinds:
          - name: thing
            binds_to: entity
            description: A thing.
          - name: second_thing
            binds_to: document
            description: Added after the first load.
        edge_kinds:
          - name: references
            binds_to: references
            description: a cites b.
    """), encoding="utf-8")
    # `os.stat` mtime granularity can be coarse; bump it explicitly so the test is about the cache
    # key rather than about the filesystem's clock.
    path = root / "editable" / "ontology.yaml"
    os.utime(path, (path.stat().st_atime + 5, path.stat().st_mtime + 5))

    second = load_pack("editable", root)
    assert "second_thing" in second.node_kinds
    assert second.version == "1.1"


def test_the_shipped_packs_all_still_load_under_the_stricter_loader():
    """The strictness above is only worth having if the real packs satisfy it — a loader nobody can
    load a pack with is a loader that gets relaxed."""
    for name in ("healthcare", "finance", "government"):
        pack = load_pack(name, packs_root())
        assert pack.node_kinds and pack.edge_kinds
