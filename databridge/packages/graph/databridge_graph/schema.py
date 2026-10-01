"""Knowledge-graph schema — the domain-free node/edge record shapes (ADR-0003, ADR-0009).

The graph is a **typed, bi-temporal** knowledge graph. Two axes, both MANDATORY (ADR-0003):

  valid_time  (valid_from, valid_to)   — when the fact was TRUE in the real world
  system_time (system_from, system_to) — when DataBridge LEARNED it

`*_to = None` means "still open" on that axis (still true / still believed). New conflicting
information **invalidates** a prior version (close its open `*_to`) rather than deleting it — so
"what did we believe on March 1" and "what was actually true on March 1" are both answerable.

Anti-fabrication (P4, the load-bearing invariant): **a node or edge with no grounded
SourcePointer is REFUSED at construction** — never stored. `sources` is non-empty by validator.

**And the same wall applies one level down, per attribute.** `sources` grounds the ENTITY — that
this thing exists and was read from somewhere. It says nothing about the individual values in
`attrs`, and a review found sheets printing several fields under a `Sources` heading listing only
two pointers: one citation appearing to vouch for values it had never seen. So `attr_sources`
carries a pointer PER ATTRIBUTE, and `_attrs_are_grounded` refuses construction if any attribute
has a value but no pointer of its own.

An attribute whose value is `None` is the honest way to say "not found", and needs no pointer —
there is nothing to ground. `unresolved` lists exactly those keys, so a renderer can show the
question that was asked and left unanswered rather than omitting it silently.

**The vocabulary is closed, and it is no longer written here.** `NodeKind` / `EdgeKind` were a
pair of enums holding one vertical's terms. They are now handles on the vocabulary the *enabled
packs* declare (`kinds.py` → `packs.py` → `packs/<name>/ontology.yaml`), bound to the domain-free
meta-model in `meta.py`. The *principle* from ADR-0008/0009 is unchanged and is the point: an
unrecognised kind is skipped, never coined on the fly — a graph that invents its own node types to
fit incoming data cannot be reasoned about, and "what kinds of thing are in here?" stops having an
answer. What changed is only WHERE the closure is declared, and therefore that adding an industry
is adding a directory rather than editing this file.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from databridge_core import SourcePointer

from .attestation import Attestation, enforce_attestation
from .kinds import EdgeKind, NodeKind

#: Top-level node fields that are IDENTITY-BEARING, and therefore grounded per-attribute through
#: `attr_sources` exactly like an entry in `attrs`.
#:
#: `canonical_id` is the exact-merge key: a node carrying one that matches an existing node's is
#: merged into it. Review (2026-08-16, finding A4) found it escaped the per-attribute wall
#: entirely — it is a top-level field, so it needed no pointer, and an INVENTED identifier could
#: therefore drive an exact identity merge while the entity-level `sources` vouched for something
#: else. `label` is here for the same reason one rung down: it is what name-similarity merges on,
#: and it is the string a human reads as "who this is".
#:
#: Naming them here rather than requiring a pointer unconditionally is deliberate — see
#: `GraphNode._identity_values_are_grounded` for exactly how far the requirement currently
#: reaches and what still has to change to close the rest.
IDENTITY_BEARING_FIELDS: tuple[str, ...] = ("canonical_id", "label")


def _attrs_are_grounded(attrs: dict, attr_sources: dict, what: str,
                        identity_fields: Optional[Mapping[str, object]] = None) -> None:
    """The per-attribute grounding wall. Raises rather than storing an uncited value.

    Three rules, and the second is the one a reviewer found broken:

    1. a **non-None** attribute must carry its own pointer — the entity's `sources` do not vouch
       for it (CLAUDE.md §Hard constraints 2);
    2. a **None** attribute must NOT carry one — a pointer beside "not found" is a citation for
       nothing, and reads to both a person and the model as though the absence were established;
    3. a pointer may not name an attribute that is not there, which is how a value gets deleted
       while its citation stays behind and later attaches to a different value.

    ``identity_fields`` names TOP-LEVEL fields that are grounded through ``attr_sources`` like an
    attribute even though they are not entries in ``attrs`` — `canonical_id` and `label`
    (`IDENTITY_BEARING_FIELDS`). Review found that `canonical_id`, the exact-merge key, escaped
    this wall entirely: it needed no pointer of its own, so an invented identifier could drive an
    exact identity merge while the entity-level `sources` vouched for something else. Passing them
    here is what makes grounding them POSSIBLE; rules 2 and 3 then apply to them unchanged, so a
    pointer for an absent `canonical_id` is still a citation for nothing.
    """
    identity = dict(identity_fields or {})
    overlap = sorted(set(identity) & set(attrs))
    if overlap:
        raise ValueError(
            f"{what} REFUSED: attribute(s) {overlap} shadow a top-level identity-bearing field. "
            f"One name would then have two values and one pointer")
    uncited = sorted(k for k, v in attrs.items() if v is not None and k not in attr_sources)
    if uncited:
        raise ValueError(
            f"{what} REFUSED: attribute(s) {uncited} carry a value with no SourcePointer of their "
            f"own. Ground each one, or set it to None and let `unresolved` name it "
            f"(anti-fabrication wall, P4)")
    known = dict(attrs)
    known.update(identity)
    cited_but_absent = sorted(k for k in attr_sources if k not in known)
    if cited_but_absent:
        raise ValueError(
            f"{what} REFUSED: pointer(s) for attribute(s) {cited_but_absent} that do not exist")
    cited_but_none = sorted(k for k in attr_sources if known.get(k) is None)
    if cited_but_none:
        raise ValueError(
            f"{what} REFUSED: attribute(s) {cited_but_none} are None but carry a SourcePointer — "
            f"a citation for an absent value")


class _Grounded(BaseModel):
    """Shared by GraphNode and GraphEdge: `unresolved`, and the per-attribute wall."""

    def identity_values(self) -> dict:
        """Top-level fields grounded through `attr_sources` like an attribute. Empty by default;
        `GraphNode` overrides it with `IDENTITY_BEARING_FIELDS`. Declared here so the store's
        re-check (`GraphStore._recheck_grounding`) has one call to make for either shape — a
        second code path that forgot the identity fields is how the first version of this wall
        passed at construction and raised at the store."""
        return {}

    @property
    def unresolved(self) -> list[str]:
        """Attribute names asked for and not found. Never guessed, never defaulted."""
        return sorted(k for k, v in self.attrs.items() if v is None)

    def source_for(self, attr: str) -> Optional[SourcePointer]:
        """The pointer for one attribute, or None if the value itself is absent."""
        return self.attr_sources.get(attr)


# --- in-memory record shapes (the store persists these to SQLite) -----------------
class GraphNode(_Grounded):
    """One versioned node. ``id`` is stable across versions (``f"{kind}:{slug}"``); each
    bi-temporal version is a distinct row in ``nodes`` sharing this ``id``."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    label: str
    canonical_id: Optional[str] = None       # email / domain / employee-id — exact-merge key
    attrs: dict = Field(default_factory=dict)
    attr_sources: dict[str, SourcePointer] = Field(default_factory=dict)
    sources: tuple[SourcePointer, ...]        # REQUIRED — see `_must_be_grounded`
    valid_from: datetime
    valid_to: Optional[datetime] = None
    system_from: datetime
    system_to: Optional[datetime] = None

    @field_validator("sources")
    @classmethod
    def _must_be_grounded(cls, v: tuple[SourcePointer, ...]) -> tuple[SourcePointer, ...]:
        # P4 / anti-fabrication wall: no SourcePointer ⇒ refused, at CONSTRUCTION.
        #
        # Two holes were open here until 2026-08-16, both found by review and both reproduced
        # before being fixed:
        #
        #   1. the field carried `Field(default_factory=list)`, and **Pydantic v2 does not
        #      validate defaults**. `GraphNode(id=..., kind=..., ...)` with `sources` simply
        #      OMITTED constructed an ungrounded node — this validator never ran. The field is
        #      now REQUIRED, so omission is a construction error rather than a silent default.
        #   2. `frozen=True` is SHALLOW. `node.sources.clear()` emptied a validated node's
        #      grounding in place. The field is now a tuple, which has no mutators.
        #
        # The wall is only a wall if it cannot be walked around, and for two years it could be.
        if not v:
            raise ValueError("GraphNode REFUSED: no SourcePointer (anti-fabrication wall, P4)")
        return v

    @model_validator(mode="after")
    def _attrs_must_be_grounded(self) -> "GraphNode":
        # Re-assert entity grounding here as well as in the field validator: an after-validator
        # runs on paths a field validator can miss, and the cost of the check is a `len`.
        if not self.sources:
            raise ValueError(
                f"GraphNode REFUSED: no SourcePointer (anti-fabrication wall, P4)")
        _attrs_are_grounded(self.attrs, self.attr_sources, f"node {self.id!r}",
                            identity_fields=self.identity_values())
        return self

    # --- identity-bearing values (review finding A4) -----------------------------------
    def identity_values(self) -> dict:
        """The top-level fields that are grounded per-attribute — `IDENTITY_BEARING_FIELDS`."""
        return {f: getattr(self, f) for f in IDENTITY_BEARING_FIELDS}

    def identity_field_is_grounded(self, field: str) -> bool:
        """Whether one identity-bearing field carries a SourcePointer **of its own**.

        The entity-level `sources` do not count, and that is the entire point: they ground the
        claim that this thing exists and was read from somewhere, not the claim that its
        identifier is this string. `merge._resolve_existing` asks this before it will let a
        `canonical_id` decide that two records are the same subject.
        """
        if field not in IDENTITY_BEARING_FIELDS:
            raise KeyError(f"{field!r} is not an identity-bearing field "
                           f"({list(IDENTITY_BEARING_FIELDS)})")
        return getattr(self, field) is not None and field in self.attr_sources


class GraphEdge(_Grounded):
    """One versioned, typed, bi-temporal relationship. Grounded like a node (P4)."""

    model_config = ConfigDict(frozen=True)

    id: str
    src_id: str
    dst_id: str
    kind: EdgeKind
    weight: float = 1.0
    attrs: dict = Field(default_factory=dict)
    attr_sources: dict[str, SourcePointer] = Field(default_factory=dict)
    sources: tuple[SourcePointer, ...]        # REQUIRED — see `_must_be_grounded`
    attestation: Optional[Attestation] = None
    """The named human's record, REQUIRED on any edge bound to an `ATTESTED_META_KIND`
    (`RESOLVED_TO` — an identity determination) and REFUSED on every other kind, so that a claim
    edge cannot be promoted into a determination by attaching one. See `attestation.py`; the
    check runs in `_attrs_must_be_grounded` below and again at the store boundary."""
    valid_from: datetime
    valid_to: Optional[datetime] = None
    system_from: datetime
    system_to: Optional[datetime] = None

    @field_validator("sources")
    @classmethod
    def _must_be_grounded(cls, v: tuple[SourcePointer, ...]) -> tuple[SourcePointer, ...]:
        # Required + tuple for the same two reasons documented on `GraphNode._must_be_grounded`:
        # an unvalidated default let omission through, and a shallow freeze let `.clear()` undo
        # a validated edge's grounding.
        if not v:
            raise ValueError(
                "GraphEdge REFUSED: a relationship with no grounded SourcePointer is never "
                "stored (anti-fabrication wall, P4 / 04_PRD §3)")
        return v

    @model_validator(mode="after")
    def _attrs_must_be_grounded(self) -> "GraphEdge":
        # Re-assert entity grounding here as well as in the field validator: an after-validator
        # runs on paths a field validator can miss, and the cost of the check is a `len`.
        if not self.sources:
            raise ValueError(
                f"GraphEdge REFUSED: no SourcePointer (anti-fabrication wall, P4)")
        _attrs_are_grounded(self.attrs, self.attr_sources, f"edge {self.id!r}")
        # The attestation wall (CLAUDE.md §Hard constraints 1, spine D4b/D12). It lives on the
        # MODEL rather than behind a dedicated write API because a private writer beside a public
        # constructor is a lock on one of two doors: until 2026-08-16 `ATTESTED_META_KINDS` was
        # declared and consumed by nothing, so `GraphEdge(...)` bound to `RESOLVED_TO` was an
        # unguarded route to an identity determination.
        #
        # `src_id` is passed so the attestation is checked against the WHOLE edge, not just its
        # destination. Without it a valid attestation for one determination could be attached to
        # any other edge ending at the same subject (attestation finding 1, Critical).
        enforce_attestation(self.kind, self.dst_id, self.attestation,
                            src_id=self.src_id, edge_id=self.id)
        return self


def node_id(kind: NodeKind, slug: str) -> str:
    """Stable node id = ``f"{kind}:{normalised_slug}"`` (04_PRD §6 Stage-1 ER)."""
    return f"{kind.value}:{slug}"
