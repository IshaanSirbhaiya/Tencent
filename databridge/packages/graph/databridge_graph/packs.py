"""Layers 1 and 2 of the three-layer schema (`00_SPINE.md` §4) — the **pack loader** and the
**deployment binding**.

Layer 0 (`meta.py`) is domain-free. This module reads Layer 1 — one `ontology.yaml` per pack under
`packs/<name>/` — validates it against the meta-model, and computes Layer 2: the *closed* union of
the enabled packs' kinds, which is the only vocabulary extraction may emit.

Everything identity-shaped is derived here rather than written down in engine code. In particular
`Vocabulary.never_fuzzy_merge` is **computed from the enabled packs' `subject_kinds`** (spine D4b),
not a literal. That is the whole point of the layering: the rule "a subject is never merged by name
similarity" is engine behaviour; *which* kind is a subject is a pack declaration.

And the corollary D4b spells out, enforced here rather than assumed: **a pack that declares no
subject kind gets no identity-resolution capability at all.** It does not silently get an unguarded
one. `Vocabulary.identity_grant_for(kind)` returns `None` for such a kind, and there is no branch
that turns `None` into a permission.

Reading order for a validation failure: the loader refuses the *pack*, at load time, with the file
and the offending key named. A malformed pack never partially binds.

Configuration
-------------
``packs/deployment.yaml``      the default binding for this checkout (``enabled_packs:``)
``DATABRIDGE_PACKS``           comma-separated override, e.g. ``finance,government``
``DATABRIDGE_PACKS_ROOT``      override the packs directory (used by tests)
``bind_deployment(...)``       programmatic override; ``deployment(...)`` is its context manager
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping, Optional, Sequence

import yaml

from databridge_core import SourcePointer, Trust

from .meta import (PERSON_ACTOR_META_EDGES, PERSON_META_KIND, SUBJECT_ELIGIBLE_META_KINDS,
                   EdgeMetaKind, NodeMetaKind)

# packs/ at the databridge root: parents[0]=databridge_graph, [1]=graph, [2]=packages,
# [3]=databridge/ (the same walk `databridge_core.config` uses for config/models.yaml).
DEFAULT_PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"
DEPLOYMENT_FILE = "deployment.yaml"

#: A kind name is a lowercase snake-case identifier. It becomes the node-id prefix
#: (``f"{kind}:{slug}"``), so it may not contain the separator or any case ambiguity.
_KIND_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: What a human must do before a subject candidate may be selected. Closed — a pack inventing its
#: own attestation mode would be inventing its own identity guarantee.
ATTESTATION_MODES = frozenset({"in_person", "verified_channel"})

#: Wildcard accepted in an edge's ``src``/``dst`` lists: "any kind in this pack".
ANY_KIND = "*"

#: A pack NAME. Deliberately narrower than a filesystem name: no separators, no dots, no drive
#: letters, no leading dash. Review (2026-08-16, packs finding 1, Critical) showed why this is not
#: cosmetic — ``load_pack`` resolved ``root / name / "ontology.yaml"`` and never checked the result
#: stayed under ``root``, so ``DATABRIDGE_PACKS=../../somewhere`` or an absolute path loaded an
#: ontology from anywhere on disk. That does not merely widen the vocabulary; it makes "the
#: vocabulary is closed to the enabled packs" false, because *which packs are enabled* stopped
#: being a statement about this tree.
_PACK_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")

#: Top-level keys a pack file may carry. Closed: an unknown key is a refusal, not a shrug. A
#: misspelled ``subject_kind:`` used to load a pack with NO subject protections at all and no
#: complaint (packs finding 8) — the security-relevant typo is exactly the one that must fail.
_PACK_TOP_LEVEL_KEYS = frozenset({
    "pack", "title", "version", "node_kinds", "edge_kinds", "subject_kinds", "identity_rules"})
_NODE_KIND_KEYS = frozenset({"name", "binds_to", "description"})
_EDGE_KIND_KEYS = frozenset({"name", "binds_to", "src", "dst", "description"})
_SUBJECT_KIND_KEYS = frozenset({
    "kind", "identifiers", "never_fuzzy_merge", "attestation", "grant_type", "attested_by",
    "held_by"})
_IDENTITY_RULE_KEYS = frozenset({"kind", "business_keys", "exact_name", "similarity_merge"})

#: The merge rules an `identity_rules` entry can declare. Closed. Each is INERT until a named
#: human approves it for the deployment (`ontology_state.record_identity_rule_decision`): a pack
#: declares which rules MAY be approved, never that one is.
IDENTITY_RULE_TYPES = frozenset({"business_key", "exact_name"})


class PackError(Exception):
    """A pack file is missing, malformed, or inconsistent with the meta-model. Fail loud."""


class UnknownKind(KeyError, ValueError):
    """A kind name that no enabled pack declares. The vocabulary is CLOSED (ADR-0009).

    Both bases are load-bearing. `KeyError` because a vocabulary lookup that misses is a lookup
    failure; `ValueError` because `NodeKind("bogus")` used to be an `Enum` call and callers that
    already guard it with `except ValueError` must keep working — this replaced an enum, and a
    swapped exception type is exactly the kind of silent behaviour change a refactor is supposed
    not to make.
    """

    def __init__(self, axis: str, name: str, known: Sequence[str]) -> None:
        self.axis, self.kind_name = axis, name
        super().__init__(
            f"unknown {axis} kind {name!r}: no enabled pack declares it. "
            f"Enabled vocabulary: {sorted(known)}")


# --- Layer 1 declarations ---------------------------------------------------------
def _decl_pointer(path: Path, section: str, index: int, key: str = "name") -> SourcePointer:
    """A re-executable pointer at the exact YAML entry a declaration came from.

    Packs finding 4: a declaration carrying only a pack NAME cannot be re-executed — "which file,
    which entry?" has no answer, and a value derived from the declaration is therefore grounded in
    a string rather than in a source. The pack file is a table in the sense that matters (a list of
    rows with named fields), so ``table_cell`` is the honest kind: ``table`` is the section,
    ``row_id`` is the ordinal within it, ``column`` is the key.

    Trust is TRUSTED because a pack file is a reviewed, version-controlled artefact of the
    deployment itself — the same class as `config/models.yaml`, not as anything a source wrote.
    """
    return SourcePointer(source_id=str(path), kind="table_cell", table=section,
                         column=key, row_id=str(index), trust=Trust.TRUSTED)


@dataclass(frozen=True)
class NodeKindDecl:
    """One concrete node kind declared by a pack, bound to a domain-free meta-kind."""

    name: str
    binds_to: NodeMetaKind
    pack: str
    description: str = ""
    sources: tuple[SourcePointer, ...] = ()
    """Where this declaration was read from — one pointer per contributing pack file. A tuple
    rather than a single pointer because a merged declaration has more than one origin, and
    attributing a composite to whichever pack happened to be first is the misattribution packs
    finding 4 names."""

    @property
    def symbol(self) -> str:
        """The attribute name the engine exposes it under (``document`` → ``DOCUMENT``)."""
        return self.name.upper()


@dataclass(frozen=True)
class EdgeKindDecl:
    """One concrete edge kind. ``src``/``dst`` are not a constraint the STORE enforces — the
    store's job is grounding and bi-temporality, not schema policing — and the loader checks they
    name declared kinds. The writers that build edges from data (``extractor.extract_graph`` and
    ``estate_seed``) DO enforce them, through :meth:`allows`: an edge whose endpoint kinds the
    ontology does not declare is refused/skipped and counted, never written."""

    name: str
    binds_to: EdgeMetaKind
    pack: str
    src: tuple[str, ...] = ()
    dst: tuple[str, ...] = ()
    description: str = ""
    sources: tuple[SourcePointer, ...] = ()
    packs: tuple[str, ...] = ()
    """Every pack that contributed to this declaration, in binding order.

    `pack` keeps naming the FIRST declarer so existing readers are unchanged; `packs` is what a
    merged endpoint union is actually attributable to. `references` and `supersedes` are shared
    across packs on purpose, and their merged `src`/`dst` genuinely come from several files."""

    def __post_init__(self) -> None:
        if not self.packs:
            object.__setattr__(self, "packs", (self.pack,))

    @property
    def symbol(self) -> str:
        return self.name.upper()

    def allows(self, end: str, kind: str) -> bool:
        """Whether a node of ``kind`` may sit at ``end`` (``"src"`` or ``"dst"``) of this edge.

        The store does not police this (see the class docstring); *writers* that turn untrusted
        output into edges — the prose extractor, the seed builder — do. ``ANY_KIND`` matches every
        bound kind. An EMPTY list matches nothing: the vocabulary is closed, and "no endpoint was
        declared" is not the same statement as "any endpoint is fine" — a pack that means the
        latter writes ``["*"]``.
        """
        if end not in ("src", "dst"):
            raise ValueError(f"end must be 'src' or 'dst', not {end!r}")
        names = self.src if end == "src" else self.dst
        return ANY_KIND in names or str(kind) in names


@dataclass(frozen=True)
class SubjectKind:
    """A pack's declaration that one of its node kinds is a **subject** (spine D4b).

    A subject is the entity whose identity the system may never auto-confirm. The engine's
    behaviour hangs off this declaration and nothing else: candidates are presented, a named
    human attests, `requires_human_selection` stays `Literal[True]`, and name similarity never
    merges two of them.
    """

    kind: str
    identifiers: tuple[str, ...]
    never_fuzzy_merge: bool
    attestation: str
    grant_type: str
    pack: str
    sources: tuple[SourcePointer, ...] = ()
    attested_by: Optional[str] = None
    """WORDING only (FIN-09): who performs the attestation, as the identity caveats say it
    (e.g. "a named member of the onboarding team"). None: the engine's neutral wording. Not a
    security field."""
    held_by: Optional[str] = None
    """WORDING only (FIN-09): who holds the register a subject row sits in (e.g. "the bank")."""

    def security_fields(self) -> tuple:
        """The parts two packs must agree on exactly before the same subject kind may be shared.

        Which identifiers a human may check, which act satisfies the attestation, and which
        capability it mints. Packs finding 5: `setdefault` silently kept whichever pack came
        first, so the identity guarantee for a shared kind depended on the ORDER of
        `enabled_packs`. That is a list deciding an identity-safety question.
        """
        return (self.kind, self.identifiers, self.never_fuzzy_merge, self.attestation,
                self.grant_type)


@dataclass(frozen=True)
class IdentityRule:
    """A pack's declaration of how records of one NON-subject kind may be recognised as one
    record (FIN-04, `wiki/20-decisions/D-004`). The counterpart of `SubjectKind`, and mutually
    exclusive with it: the loader refuses an identity rule on any kind a bound pack declares a
    subject, so a person can never be merged by one.

    * ``business_keys`` -- attribute names whose EXACT value (grounded by its own pointer) names
      one record: a registration number, an LEI;
    * ``exact_name`` -- two records whose names are EXACTLY equal after normalisation (case,
      punctuation and whitespace only; the legal form is kept) may be one;
    * ``similarity_merge`` -- whether the engine's default name-SIMILARITY merge still applies to
      the kind. Required and explicit.

    Both merge rules are INERT until a named human approves them (`rule_types` lists what may be
    approved); `similarity_merge: false` takes effect at once, because it only ever merges less.
    """

    kind: str
    business_keys: tuple[str, ...]
    exact_name: bool
    similarity_merge: bool
    pack: str
    sources: tuple[SourcePointer, ...] = ()

    @property
    def rule_types(self) -> tuple[str, ...]:
        return tuple(t for t, on in (("business_key", bool(self.business_keys)),
                                     ("exact_name", self.exact_name)) if on)

    def security_fields(self) -> tuple:
        return (self.kind, self.business_keys, self.exact_name, self.similarity_merge)


def _sealed(mapping: Optional[Mapping]) -> Mapping:
    """A read-only view over a **copy** of ``mapping``. The closure of the vocabulary, enforced.

    `Pack` and `Vocabulary` are frozen dataclasses, which freezes the *bindings* and not the
    dictionaries they point at. Review (2026-08-16, finding A6) showed what that costs:

        active_vocabulary().node_kinds["invented"] = NodeKindDecl(...)
        NodeKind("invented")            # now accepted — the closed vocabulary just widened
        del active_vocabulary().subject_kinds["<a subject>"]
        # `never_fuzzy_merge` just shrank, and that subject may now be merged by name similarity

    Both mutations hit the process-wide CACHED vocabulary, so they persist for every later caller.
    The copy matters as much as the proxy: wrapping the caller's own dict would leave them holding
    a live handle to the interior, and `MappingProxyType` is a view, not a snapshot.
    """
    return MappingProxyType(dict(mapping or {}))


def person_like_kinds(node_kinds: Mapping[str, "NodeKindDecl"],
                      edge_kinds: Mapping[str, "EdgeKindDecl"]) -> frozenset[str]:
    """E2E run 5 F-04: the kinds whose records are NATURAL PERSONS, derived from the declarations'
    STRUCTURE (`meta.PERSON_ACTOR_META_EDGES`), never from a name: an ENTITY-bound kind the pack
    names explicitly as the actor (``src``) of a role-holding or artefact-issuing edge. A wildcard
    endpoint names no kind and proves nothing."""
    out = set()
    for decl in edge_kinds.values():
        if decl.binds_to not in PERSON_ACTOR_META_EDGES:
            continue
        for kind in decl.src:
            node = node_kinds.get(kind)
            if node is not None and node.binds_to is PERSON_META_KIND:
                out.add(kind)
    return frozenset(out)


@dataclass(frozen=True)
class Pack:
    """One loaded, validated pack — Layer 1. Its mappings are SEALED; see `_sealed`."""

    name: str
    title: str
    version: str
    path: Path
    node_kinds: Mapping[str, NodeKindDecl] = field(default_factory=dict)
    edge_kinds: Mapping[str, EdgeKindDecl] = field(default_factory=dict)
    subject_kinds: Mapping[str, SubjectKind] = field(default_factory=dict)
    identity_rules: Mapping[str, IdentityRule] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for f in ("node_kinds", "edge_kinds", "subject_kinds", "identity_rules"):
            object.__setattr__(self, f, _sealed(getattr(self, f)))


# --- Layer 2: the deployment binding ----------------------------------------------
@dataclass(frozen=True)
class Vocabulary:
    """The closed vocabulary of the enabled packs — Layer 2.

    Extraction, the store and the merge rules all read this and nothing else. A kind absent here
    is skipped, never coined on the fly.
    """

    packs: tuple[str, ...]
    node_kinds: Mapping[str, NodeKindDecl]
    edge_kinds: Mapping[str, EdgeKindDecl]
    subject_kinds: Mapping[str, SubjectKind]
    generation: int = 0
    identity_rules: Mapping[str, IdentityRule] = field(default_factory=dict)
    """FIN-04: the declared merge rules of NON-subject kinds (see `IdentityRule`). Never holds a
    subject kind: `build_vocabulary` refuses the combination."""
    """The binding generation this snapshot was built under (`binding_generation()`).

    Packs finding 7: a resolver that captures `active_vocabulary()` at import keeps using the old
    subject policy after a rebind, believing it is current. This cannot stop that — a caller may
    hold any object it likes — but it makes the staleness *detectable*, which is the difference
    between a bug that can be asserted against and one that can only be reasoned about. See
    `is_current()`.
    """

    def is_current(self) -> bool:
        """Whether this snapshot is still the bound one. `False` means a rebind happened after it
        was taken, and any identity decision made from it is being made under the wrong pack."""
        return self.generation == binding_generation()

    def __post_init__(self) -> None:
        # Sealed for the reason `_sealed` documents, and it matters most HERE: this object is
        # cached process-wide, so one mutation of it re-opens the closed vocabulary for every
        # later caller, and deleting a subject silently disables `never_fuzzy_merge` for it.
        object.__setattr__(self, "packs", tuple(self.packs))
        for f in ("node_kinds", "edge_kinds", "subject_kinds", "identity_rules"):
            object.__setattr__(self, f, _sealed(getattr(self, f)))

    # --- node/edge lookup ---
    def node(self, name: str) -> NodeKindDecl:
        try:
            return self.node_kinds[name]
        except KeyError:
            raise UnknownKind("node", name, list(self.node_kinds)) from None

    def edge(self, name: str) -> EdgeKindDecl:
        try:
            return self.edge_kinds[name]
        except KeyError:
            raise UnknownKind("edge", name, list(self.edge_kinds)) from None

    def node_symbol(self, symbol: str) -> Optional[NodeKindDecl]:
        return self._by_symbol(self.node_kinds, symbol)

    def edge_symbol(self, symbol: str) -> Optional[EdgeKindDecl]:
        return self._by_symbol(self.edge_kinds, symbol)

    @staticmethod
    def _by_symbol(decls: Mapping[str, object], symbol: str):
        name = symbol.lower()
        return decls.get(name)

    # --- the identity surface, all of it derived (spine D4b) ---
    @property
    def never_fuzzy_merge(self) -> frozenset[str]:
        """Kind names that name similarity must NEVER merge. Computed from the enabled packs'
        `subject_kinds` AND from every kind whose records are natural persons (`person_kinds`)
        — never a literal in engine code. E2E run 5 F-04: a person kind a pack forgot to declare
        a subject (finance `contact`) used to lose this protection silently."""
        return frozenset(s.kind for s in self.subject_kinds.values()
                         if s.never_fuzzy_merge) | self.person_kinds

    @cached_property
    def person_kinds(self) -> frozenset[str]:
        """Kinds whose records stand for PEOPLE: every declared subject, plus every kind the bound
        declarations mark structurally as a natural person (`person_like_kinds`). None of them is
        ever merged by name, similarity, prefix or an identity rule, in any pack (hard
        constraint 1): a mention of one is a candidate for a named human, never a join."""
        return frozenset(self.subject_kinds) | person_like_kinds(self.node_kinds,
                                                                 self.edge_kinds)

    def is_person(self, kind: str) -> bool:
        return str(kind) in self.person_kinds

    def is_subject(self, kind: str) -> bool:
        return str(kind) in self.subject_kinds

    def identity_rule_for(self, kind: str) -> Optional[IdentityRule]:
        """The declared merge rules of a NON-subject, NON-person kind, or ``None`` (FIN-04). A
        subject or a person kind never has one, whatever any mapping says."""
        if self.is_subject(str(kind)) or self.is_person(str(kind)):
            return None
        return self.identity_rules.get(str(kind))

    def is_document_kind(self, kind: str) -> bool:
        """Whether ``kind`` binds to the DOCUMENT meta-kind (FIN-07: a document's identity is its
        source item, never its title)."""
        d = self.node_kinds.get(str(kind))
        return d is not None and d.binds_to is NodeMetaKind.DOCUMENT

    def identity_grant_for(self, kind: str) -> Optional[str]:
        """The capability minted when a human attests to a candidate of this kind, or ``None``.

        ``None`` means **no identity-resolution capability** — the D4b default for a kind no pack
        declared a subject. There is deliberately no fallback grant.
        """
        s = self.subject_kinds.get(str(kind))
        return s.grant_type if s else None

    def attestation_for(self, kind: str) -> Optional[str]:
        s = self.subject_kinds.get(str(kind))
        return s.attestation if s else None

    def subject_wording(self, kind: str) -> tuple[Optional[str], Optional[str]]:
        """``(attested_by, held_by)`` the pack words a subject kind's caveats with (FIN-09)."""
        s = self.subject_kinds.get(str(kind))
        return (s.attested_by, s.held_by) if s else (None, None)

    def identifiers_for(self, kind: str) -> tuple[str, ...]:
        s = self.subject_kinds.get(str(kind))
        return s.identifiers if s else ()

    # --- meta-kind projections (how domain-free code reasons about a pack it never saw) ---
    def nodes_binding_to(self, meta: NodeMetaKind) -> tuple[str, ...]:
        return tuple(sorted(n for n, d in self.node_kinds.items() if d.binds_to is meta))

    def edges_binding_to(self, meta: EdgeMetaKind) -> tuple[str, ...]:
        return tuple(sorted(n for n, d in self.edge_kinds.items() if d.binds_to is meta))


# --- loading ----------------------------------------------------------------------
_LOCK = threading.RLock()
# The binding GENERATION. Advanced by every `bind_deployment()` and by `clear_caches()`, and part
# of the vocabulary cache key.
#
# Packs finding 6 (staleness) and finding 2 (cache poisoning) both come back to the same missing
# thing: nothing identified *which binding* a cached vocabulary belonged to beyond a value that
# could change between reading it and using it. A monotonically advancing counter, read and
# written only under `_LOCK`, gives every snapshot an identity — so a rebind cannot leave a stale
# entry reachable, and a racing build cannot publish under a key that no longer describes it.
#
# What this deliberately does NOT do is watch `deployment.yaml` for edits. A long-running process
# whose vocabulary changed because somebody saved a file would be worse, not better: the closed
# vocabulary is a property of a deployment, and a deployment that mutates underneath a running
# resolver cannot answer "what did we believe on March 1". `deployment.yaml` is re-read whenever
# the generation advances — which is what `bind_deployment(None)` and `clear_caches()` do — so the
# refresh is explicit and has a caller.
_GENERATION = 0
_PACK_CACHE: dict[tuple, Pack] = {}
_BINDING: Optional[tuple[str, ...]] = None      # set by bind_deployment(); None ⇒ read config
_DEPLOY_CACHE: dict[tuple, tuple[str, ...]] = {}   # deployment.yaml parse, by (root, generation)
_VOCAB_CACHE: dict[tuple, Vocabulary] = {}


def binding_generation() -> int:
    """The current binding generation. A `Vocabulary` carries the one it was built under."""
    with _LOCK:
        return _GENERATION

# `active_vocabulary()` is on the hot path: every NodeKind/EdgeKind construction resolves through
# it, which is once per row read out of SQLite. It therefore must not touch the filesystem. The
# cache key is the three things that can change the answer — the packs root, the programmatic
# binding, and the env override — all of which are cheap to read.


def packs_root() -> Path:
    """The active packs directory (env override: ``DATABRIDGE_PACKS_ROOT``)."""
    override = os.environ.get("DATABRIDGE_PACKS_ROOT")
    return Path(override) if override else DEFAULT_PACKS_ROOT


def available_packs(root: Optional[Path] = None) -> tuple[str, ...]:
    """Every pack directory on disk that carries an ``ontology.yaml``, sorted."""
    r = Path(root) if root is not None else packs_root()
    if not r.is_dir():
        return ()
    return tuple(sorted(p.name for p in r.iterdir() if (p / "ontology.yaml").is_file()))


def _require(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise PackError(f"{where}: missing required key {key!r}")
    return mapping[key]


def _as_list(value, key: str, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PackError(f"{where}: {key!r} must be a list, got {type(value).__name__}")
    return value


def check_pack_name(name: object) -> str:
    """Refuse anything that is not a bare pack name, BEFORE it is joined to a path.

    Packs finding 1 (Critical). ``../external``, ``/etc/walnut``, a drive-qualified path and
    ``a/b`` are all refused here rather than being resolved and then noticed. Containment is
    checked as well (`_pack_ontology_path`) — this is the cheap first gate, and having both means
    a future caller that skips one still hits the other.
    """
    if not isinstance(name, str) or not _PACK_NAME.match(name):
        raise PackError(
            f"pack name {name!r} is not a bare pack identifier (lowercase, letters/digits/_/-). "
            f"A name is a DIRECTORY under the packs root, never a path: separators, dots and "
            f"absolute paths would let the enabled vocabulary be sourced from outside the "
            f"deployment, which is the closure this layer exists to guarantee")
    return name


def _pack_ontology_path(name: str, root: Path) -> Path:
    """The pack's ``ontology.yaml``, proven to sit under ``root``.

    Both the directory and the file are checked for symlinks. `resolve()` alone is not enough: a
    symlinked pack directory resolves to its target, and the target may be anywhere — so the
    containment test would pass for a path whose *resolved* form is outside the tree only if we
    forgot to compare against the resolved root, and would pass for a symlink INTO the tree while
    the bytes read come from outside it. Refusing the link is simpler than reasoning about it.
    """
    resolved_root = Path(root).resolve()
    pack_dir = resolved_root / name
    ontology = pack_dir / "ontology.yaml"
    for part in (pack_dir, ontology):
        if part.is_symlink():
            raise PackError(
                f"pack {name!r} REFUSED: {part} is a symbolic link. A pack's bytes must come from "
                f"the deployment's own tree; a link makes 'which ontology is enabled?' a question "
                f"about the filesystem rather than about the deployment")
    resolved = ontology.resolve()
    try:
        inside = resolved.is_relative_to(resolved_root)
    except (AttributeError, ValueError):                       # pragma: no cover - py<3.9 / drives
        inside = str(resolved).startswith(str(resolved_root))
    if not inside:
        raise PackError(
            f"pack {name!r} REFUSED: {resolved} is outside the packs root {resolved_root}. The "
            f"deployment binding closes the vocabulary to packs in THIS tree")
    return resolved


def _file_identity(path: Path) -> tuple:
    """(size, mtime_ns) — what makes a cached parse still the file's parse.

    Packs finding 6: `_PACK_CACHE` was keyed on the path alone, so editing an ontology was
    invisible for the life of the process and only the externally-callable `clear_caches()`
    refreshed it. A stat at LOAD time costs nothing; the hot path (`active_vocabulary`) still
    touches no filesystem, because it is keyed on the binding generation instead.
    """
    try:
        st = path.stat()
    except OSError:                                            # pragma: no cover - raced deletion
        return (-1, -1)
    return (st.st_size, st.st_mtime_ns)


def _reject_unknown_keys(mapping: dict, allowed: frozenset, what: str, where: str) -> None:
    """A key nobody declared is a refusal.

    Packs finding 8. The failure this prevents is specific and silent: ``subject_kind:`` instead of
    ``subject_kinds:`` loaded a pack with no subject protections and no error, so the guardrail was
    absent exactly where somebody had tried to write it.
    """
    unknown = sorted(k for k in mapping if k not in allowed)
    if unknown:
        raise PackError(
            f"{where}: {what} carries key(s) {unknown} that no pack schema declares. Valid keys: "
            f"{sorted(allowed)}. An unknown key is refused rather than ignored, because the "
            f"security-relevant typo is the one that would otherwise pass")


def _non_blank_str(value, key: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackError(
            f"{where}: {key!r} must be a non-empty string, got {value!r}. A blank identifier or "
            f"grant satisfies a truthiness check and names nothing")
    return value.strip()


def _check_name(name, key: str, where: str) -> str:
    if not isinstance(name, str) or not _KIND_NAME.match(name):
        raise PackError(
            f"{where}: {key} {name!r} is not a lowercase snake_case identifier "
            f"(kind names become the node-id prefix, so they must be unambiguous)")
    return name


def load_pack(name: str, root: Optional[Path] = None, *, use_cache: bool = True) -> Pack:
    """Load and validate one pack. Raises `PackError` with the file and key named."""
    r = Path(root) if root is not None else packs_root()
    # Order matters, and it is the fix for packs findings 1 and 10: the NAME is validated and the
    # target proven to be inside the root BEFORE any cache lookup, so a traversal spelling or a
    # symlink alias cannot reach an already-cached Pack and inherit its declarations under a
    # different name.
    check_pack_name(name)
    path = _pack_ontology_path(name, r)
    key = (path, _file_identity(path))
    with _LOCK:
        if use_cache and key in _PACK_CACHE:
            cached = _PACK_CACHE[key]
            if cached.name == name:
                return cached
    if not path.is_file():
        raise PackError(
            f"pack {name!r} not found: no ontology.yaml at {path}. "
            f"Available: {list(available_packs(r))}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:                                  # pragma: no cover - yaml detail
        raise PackError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PackError(f"{path}: top level must be a mapping")
    _reject_unknown_keys(data, _PACK_TOP_LEVEL_KEYS, "the pack file", str(path))

    where = str(path)
    declared = _require(data, "pack", where)
    if declared != name:
        raise PackError(
            f"{where}: declares pack {declared!r} but lives in directory {name!r} — the "
            f"directory name is the pack's identity")
    title = str(data.get("title") or declared)
    version = str(_require(data, "version", where))

    node_kinds: dict[str, NodeKindDecl] = {}
    for index, entry in enumerate(_as_list(_require(data, "node_kinds", where),
                                           "node_kinds", where)):
        if not isinstance(entry, dict):
            raise PackError(f"{where}: each node_kinds entry must be a mapping")
        _reject_unknown_keys(entry, _NODE_KIND_KEYS, "a node_kinds entry", where)
        kname = _check_name(_require(entry, "name", where), "node kind", where)
        binds = _require(entry, "binds_to", where)
        try:
            meta = NodeMetaKind(binds)
        except ValueError:
            raise PackError(
                f"{where}: node kind {kname!r} binds_to {binds!r}, which is not a node meta-kind. "
                f"Valid: {[m.value for m in NodeMetaKind]}") from None
        if kname in node_kinds:
            raise PackError(f"{where}: node kind {kname!r} declared twice")
        node_kinds[kname] = NodeKindDecl(
            name=kname, binds_to=meta, pack=name,
            description=str(entry.get("description") or ""),
            sources=(_decl_pointer(path, "node_kinds", index),))

    edge_kinds: dict[str, EdgeKindDecl] = {}
    for index, entry in enumerate(_as_list(_require(data, "edge_kinds", where),
                                           "edge_kinds", where)):
        if not isinstance(entry, dict):
            raise PackError(f"{where}: each edge_kinds entry must be a mapping")
        _reject_unknown_keys(entry, _EDGE_KIND_KEYS, "an edge_kinds entry", where)
        ename = _check_name(_require(entry, "name", where), "edge kind", where)
        binds = _require(entry, "binds_to", where)
        try:
            emeta = EdgeMetaKind(binds)
        except ValueError:
            raise PackError(
                f"{where}: edge kind {ename!r} binds_to {binds!r}, which is not an edge "
                f"meta-kind. Valid: {[m.value for m in EdgeMetaKind]}") from None
        if ename in edge_kinds:
            raise PackError(f"{where}: edge kind {ename!r} declared twice")
        ends = {}
        for end in ("src", "dst"):
            names = tuple(str(v) for v in _as_list(entry.get(end), end, where))
            unknown = [v for v in names if v != ANY_KIND and v not in node_kinds]
            if unknown:
                raise PackError(
                    f"{where}: edge kind {ename!r} names {end} kind(s) {unknown} that this pack "
                    f"does not declare")
            ends[end] = names
        edge_kinds[ename] = EdgeKindDecl(
            name=ename, binds_to=emeta, pack=name, src=ends["src"], dst=ends["dst"],
            description=str(entry.get("description") or ""),
            sources=(_decl_pointer(path, "edge_kinds", index),), packs=(name,))

    subject_kinds: dict[str, SubjectKind] = {}
    for index, entry in enumerate(_as_list(data.get("subject_kinds"), "subject_kinds", where)):
        if not isinstance(entry, dict):
            raise PackError(f"{where}: each subject_kinds entry must be a mapping")
        _reject_unknown_keys(entry, _SUBJECT_KIND_KEYS, "a subject_kinds entry", where)
        skind = str(_require(entry, "kind", where))
        if skind not in node_kinds:
            raise PackError(
                f"{where}: subject kind {skind!r} is not a node kind this pack declares")
        meta = node_kinds[skind].binds_to
        if meta not in SUBJECT_ELIGIBLE_META_KINDS:
            raise PackError(
                f"{where}: subject kind {skind!r} binds to {meta.value!r}, which has no identity "
                f"to attest to. Subjects must bind to one of "
                f"{sorted(m.value for m in SUBJECT_ELIGIBLE_META_KINDS)}")
        identifiers = tuple(
            _non_blank_str(v, f"subject kind {skind!r} identifier", where)
            for v in _as_list(_require(entry, "identifiers", where), "identifiers", where))
        if len(set(identifiers)) != len(identifiers):
            raise PackError(
                f"{where}: subject kind {skind!r} lists an identifier twice; a duplicate makes "
                f"the declared set smaller than it reads")
        if not identifiers:
            raise PackError(
                f"{where}: subject kind {skind!r} declares no identifiers — there would be "
                f"nothing for a human to check the person against")
        never_fuzzy = entry.get("never_fuzzy_merge", True)
        if never_fuzzy is not True:
            raise PackError(
                f"{where}: subject kind {skind!r} sets never_fuzzy_merge={never_fuzzy!r}. A "
                f"subject whose records may be merged by name similarity is not a subject "
                f"(CLAUDE.md §Hard constraints 1); the key exists to be explicit, not optional")
        attestation = str(_require(entry, "attestation", where))
        if attestation not in ATTESTATION_MODES:
            raise PackError(
                f"{where}: subject kind {skind!r} declares attestation {attestation!r}; valid "
                f"modes are {sorted(ATTESTATION_MODES)}")
        grant = _non_blank_str(_require(entry, "grant_type", where),
                               f"subject kind {skind!r} grant_type", where)
        if skind in subject_kinds:
            raise PackError(f"{where}: subject kind {skind!r} declared twice")
        wording = {w: (_non_blank_str(entry[w], f"subject kind {skind!r} {w}", where)
                       if entry.get(w) is not None else None)
                   for w in ("attested_by", "held_by")}
        subject_kinds[skind] = SubjectKind(
            kind=skind, identifiers=identifiers, never_fuzzy_merge=True,
            attestation=attestation, grant_type=grant, pack=name, **wording,
            sources=(_decl_pointer(path, "subject_kinds", index, key="kind"),))

    identity_rules: dict[str, IdentityRule] = {}
    for index, entry in enumerate(_as_list(data.get("identity_rules"), "identity_rules", where)):
        if not isinstance(entry, dict):
            raise PackError(f"{where}: each identity_rules entry must be a mapping")
        _reject_unknown_keys(entry, _IDENTITY_RULE_KEYS, "an identity_rules entry", where)
        rkind = str(_require(entry, "kind", where))
        if rkind not in node_kinds:
            raise PackError(f"{where}: identity rule for {rkind!r}, which is not a node kind this "
                            f"pack declares")
        if rkind in subject_kinds:
            raise PackError(
                f"{where}: identity rule for {rkind!r}, which this pack declares a SUBJECT. A "
                f"subject is never merged by a rule (CLAUDE.md hard constraint 1): its records "
                f"stay candidates for a named human")
        if rkind in person_like_kinds(node_kinds, edge_kinds):
            raise PackError(
                f"{where}: identity rule for {rkind!r}, whose records are natural persons (an "
                f"entity the pack names as the actor of a role or of an artefact). A person is "
                f"never merged by a rule (CLAUDE.md hard constraint 1)")
        if node_kinds[rkind].binds_to not in SUBJECT_ELIGIBLE_META_KINDS:
            raise PackError(
                f"{where}: identity rule for {rkind!r}, which binds to "
                f"{node_kinds[rkind].binds_to.value!r}; identity rules apply to kinds binding to "
                f"{sorted(m.value for m in SUBJECT_ELIGIBLE_META_KINDS)}")
        keys = tuple(_non_blank_str(v, f"identity rule {rkind!r} business key", where)
                     for v in _as_list(entry.get("business_keys"), "business_keys", where))
        if len(set(keys)) != len(keys):
            raise PackError(f"{where}: identity rule {rkind!r} lists a business key twice")
        flags = {}
        for flag in ("exact_name", "similarity_merge"):
            value = entry.get(flag, False if flag == "exact_name" else None)
            if type(value) is not bool:
                raise PackError(
                    f"{where}: identity rule {rkind!r}: {flag!r} must be true or false, not "
                    f"{value!r} (similarity_merge is required: a kind that declares merge rules "
                    f"says explicitly whether the default name-similarity merge stays on)")
            flags[flag] = value
        if not keys and not flags["exact_name"]:
            raise PackError(f"{where}: identity rule {rkind!r} declares no business key and no "
                            f"exact_name rule: nothing a human could approve")
        if rkind in identity_rules:
            raise PackError(f"{where}: identity rule for {rkind!r} declared twice")
        identity_rules[rkind] = IdentityRule(
            kind=rkind, business_keys=keys, exact_name=flags["exact_name"],
            similarity_merge=flags["similarity_merge"], pack=name,
            sources=(_decl_pointer(path, "identity_rules", index, key="kind"),))

    pack = Pack(name=name, title=title, version=version, path=path,
                node_kinds=node_kinds, edge_kinds=edge_kinds, subject_kinds=subject_kinds,
                identity_rules=identity_rules)
    with _LOCK:
        _PACK_CACHE[key] = pack
    return pack


def configured_packs(root: Optional[Path] = None) -> tuple[str, ...]:
    """The deployment binding: ``bind_deployment()`` → ``DATABRIDGE_PACKS`` → ``deployment.yaml``.

    Raises `PackError` if none of the three names a pack — a deployment with no vocabulary can
    extract nothing, and silently binding "all packs on disk" would make the vocabulary open.
    """
    with _LOCK:
        binding, generation = _BINDING, _GENERATION
    if binding is not None:
        return tuple(check_pack_name(n) for n in binding)
    env = os.environ.get("DATABRIDGE_PACKS")
    if env:
        names = tuple(n.strip() for n in env.split(",") if n.strip())
        if names:
            # Validated HERE as well as at load time. `DATABRIDGE_PACKS` is the most
            # externally-controlled of the three sources, and a traversal name reaching the loader
            # at all is one refusal away from being the only refusal (packs finding 1).
            return tuple(check_pack_name(n) for n in names)
    r = Path(root) if root is not None else packs_root()
    cache_key = (str(r), generation)
    with _LOCK:
        cached = _DEPLOY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    cfg = r / DEPLOYMENT_FILE
    if not cfg.is_file():
        raise PackError(
            f"no deployment binding: {cfg} does not exist and neither DATABRIDGE_PACKS nor "
            f"bind_deployment() is set. Available packs: {list(available_packs(r))}")
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PackError(f"{cfg}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        # Packs finding 9: a non-mapping deployment file raised a bare `AttributeError` instead of
        # the promised refusal, from a module whose contract is "fail loud, name the file".
        raise PackError(f"{cfg}: top level must be a mapping with an `enabled_packs:` list")
    names = tuple(check_pack_name(n) for n in
                  _as_list(data.get("enabled_packs"), "enabled_packs", str(cfg)))
    if not names:
        raise PackError(f"{cfg}: enabled_packs is empty — a deployment must enable at least one pack")
    with _LOCK:
        _DEPLOY_CACHE[cache_key] = names
    return names


def build_vocabulary(names: Sequence[str], root: Optional[Path] = None) -> Vocabulary:
    """Merge the named packs into one closed vocabulary (Layer 2).

    Two packs may declare the same kind name only if they bind it to the same meta-kind — node ids
    are ``f"{kind}:{slug}"``, so a name meaning two different shapes would make the id space
    ambiguous and time-travel answers wrong.
    """
    ordered = tuple(dict.fromkeys(names))            # de-duplicate, preserve order
    nodes: dict[str, NodeKindDecl] = {}
    edges: dict[str, EdgeKindDecl] = {}
    subjects: dict[str, SubjectKind] = {}
    rules: dict[str, IdentityRule] = {}
    for pname in ordered:
        pack = load_pack(pname, root)
        for k, d in pack.node_kinds.items():
            prior = nodes.get(k)
            if prior is not None and prior.binds_to is not d.binds_to:
                raise PackError(
                    f"packs {prior.pack!r} and {d.pack!r} both declare node kind {k!r} but bind it "
                    f"to {prior.binds_to.value!r} and {d.binds_to.value!r} — enable only one, or "
                    f"rename")
            nodes.setdefault(k, d)
        for k, e in pack.edge_kinds.items():
            prior_e = edges.get(k)
            if prior_e is None:
                edges[k] = e
                continue
            if prior_e.binds_to is not e.binds_to:
                raise PackError(
                    f"packs {prior_e.pack!r} and {e.pack!r} both declare edge kind {k!r} but bind "
                    f"it to {prior_e.binds_to.value!r} and {e.binds_to.value!r}")
            # Same name, same meta-kind, two packs — `references` and `supersedes` are shared on
            # purpose. Keep the first declaration but UNION the endpoints, so the merged vocabulary
            # does not silently claim the second pack's edge may not touch its own kinds.
            #
            # `packs` and `sources` carry BOTH origins. Attributing the composite to whichever
            # pack came first (packs finding 4) makes it unre-executable: the endpoint union is
            # genuinely not in either file alone, so a reader following `pack` back to one
            # `ontology.yaml` finds a declaration that does not match what the vocabulary holds.
            edges[k] = EdgeKindDecl(
                name=k, binds_to=e.binds_to, pack=prior_e.pack,
                src=tuple(dict.fromkeys(prior_e.src + e.src)),
                dst=tuple(dict.fromkeys(prior_e.dst + e.dst)),
                description=prior_e.description or e.description,
                sources=tuple(dict.fromkeys(prior_e.sources + e.sources)),
                packs=tuple(dict.fromkeys(prior_e.packs + e.packs)))
        for k, s in pack.subject_kinds.items():
            prior_s = subjects.get(k)
            if prior_s is None:
                subjects[k] = s
                continue
            if prior_s.security_fields() != s.security_fields():
                # Packs finding 5. `setdefault` kept the first declaration and discarded the
                # second in silence, so which identifiers a human may check — and which act
                # satisfies the attestation — was decided by the ORDER of `enabled_packs`. An
                # identity guarantee settled by list position is not a guarantee.
                raise PackError(
                    f"packs {prior_s.pack!r} and {s.pack!r} both declare subject kind {k!r} but "
                    f"disagree about its identity guarantee: "
                    f"{prior_s.pack}={{identifiers: {list(prior_s.identifiers)}, "
                    f"attestation: {prior_s.attestation!r}, grant_type: {prior_s.grant_type!r}}} "
                    f"vs {s.pack}={{identifiers: {list(s.identifiers)}, "
                    f"attestation: {s.attestation!r}, grant_type: {s.grant_type!r}}}. Enable only "
                    f"one, or make the declarations identical — the safe resolution is not "
                    f"whichever pack was listed first")
            # Identical declarations still keep both pointers: two packs asserting the same
            # guarantee is two pieces of evidence for it, and dropping one loses the fact that
            # the agreement was checked.
            subjects[k] = replace(prior_s,
                                  sources=tuple(dict.fromkeys(prior_s.sources + s.sources)))
        for k, r in pack.identity_rules.items():
            prior_r = rules.get(k)
            if prior_r is not None and prior_r.security_fields() != r.security_fields():
                raise PackError(
                    f"packs {prior_r.pack!r} and {r.pack!r} both declare identity rules for "
                    f"{k!r} but disagree ({prior_r.security_fields()} vs {r.security_fields()}). "
                    f"Which records may be merged is not decided by list order")
            rules[k] = r if prior_r is None else replace(
                prior_r, sources=tuple(dict.fromkeys(prior_r.sources + r.sources)))
    # FIN-04: a kind ANY bound pack declares a subject is never merged by a rule, whichever pack
    # declared the rule. Refused, not resolved by order.
    clash = sorted(set(rules) & (set(subjects) | person_like_kinds(nodes, edges)))
    if clash:
        raise PackError(
            f"identity rules for {clash} (pack(s) {sorted({rules[k].pack for k in clash})}) clash "
            f"with subject declarations of the same kind(s) (pack(s) "
            f"{sorted({subjects[k].pack for k in clash})}). A subject is never merged by a rule; "
            f"enable only one, or rename the kind")
    with _LOCK:
        generation = _GENERATION
    return Vocabulary(packs=ordered, node_kinds=nodes, edge_kinds=edges, subject_kinds=subjects,
                      generation=generation, identity_rules=rules)


def active_vocabulary(root: Optional[Path] = None) -> Vocabulary:
    """The vocabulary of the currently bound packs. Cached; never reads a file on a cache hit.

    Packs finding 2: the key used to be assembled from `_BINDING` read WITHOUT the lock, so a
    concurrent `bind_deployment()` between reading the key and building the vocabulary published
    binding B's vocabulary under binding A's key — and it stayed there, so every later request for
    A got B. The fix is one locked snapshot of everything that can change the answer, a build
    strictly from that snapshot, and a publish that only happens if the generation is still the
    one the snapshot was taken under.
    """
    r = Path(root) if root is not None else packs_root()
    with _LOCK:
        binding, generation = _BINDING, _GENERATION
    key = (str(r), binding, os.environ.get("DATABRIDGE_PACKS", ""), generation)
    with _LOCK:
        hit = _VOCAB_CACHE.get(key)
    if hit is not None:
        return hit
    names = tuple(binding) if binding is not None else configured_packs(r)
    vocab = build_vocabulary(names, r)
    with _LOCK:
        if _GENERATION == generation:
            _VOCAB_CACHE[key] = vocab
        # If the generation moved while we were building, the result is still CORRECT for the
        # snapshot it was built from — it is simply no longer current, so it is returned to this
        # caller and not published. Publishing it would poison the cache exactly as before.
    return vocab


def bind_deployment(names: Optional[Sequence[str]]) -> None:
    """Set (or clear, with ``None``) the process-wide deployment binding — Layer 2.

    Advances the binding generation, which is what makes the change visible: `deployment.yaml` is
    re-read and no vocabulary cached under an earlier generation can be returned.
    """
    global _BINDING, _GENERATION
    validated = None if names is None else tuple(check_pack_name(n) for n in names)
    with _LOCK:
        _BINDING = validated
        _GENERATION += 1


@contextmanager
def deployment(*names: str) -> Iterator[Vocabulary]:
    """Bind packs for the duration of a block, then restore. Used by tests and by the CLI."""
    global _BINDING
    with _LOCK:
        prior = _BINDING
    bind_deployment(names or None)
    try:
        yield active_vocabulary()
    finally:
        bind_deployment(prior)


def clear_caches() -> None:
    """Drop loaded packs and computed vocabularies, and advance the binding generation.

    The generation advance matters as much as the clearing: any `Vocabulary` a caller is still
    holding now answers `is_current()` with `False`, so a resolver that captured one at import
    can be caught doing so instead of quietly deciding under the wrong pack (packs finding 7).
    """
    global _GENERATION
    with _LOCK:
        _PACK_CACHE.clear()
        _DEPLOY_CACHE.clear()
        _VOCAB_CACHE.clear()
        _GENERATION += 1


#: Convenience for call sites that only want the D4b answer.
def never_fuzzy_merge_kinds() -> frozenset[str]:
    """Kind names name-similarity must never merge, derived from the enabled packs (spine D4b)."""
    return active_vocabulary().never_fuzzy_merge


__all__ = [
    "ANY_KIND", "ATTESTATION_MODES", "DEFAULT_PACKS_ROOT", "binding_generation",
    "check_pack_name",
    "EdgeKindDecl", "IDENTITY_RULE_TYPES", "IdentityRule", "NodeKindDecl", "Pack", "PackError",
    "SubjectKind", "UnknownKind",
    "Vocabulary", "active_vocabulary", "available_packs", "bind_deployment",
    "build_vocabulary", "clear_caches", "configured_packs", "deployment", "load_pack",
    "never_fuzzy_merge_kinds", "packs_root", "person_like_kinds",
]
