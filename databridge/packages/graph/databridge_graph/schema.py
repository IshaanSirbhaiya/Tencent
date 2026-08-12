"""Clinic knowledge-graph schema (closed vocab frozen in ADR-0009, superseding ADR-0008).

The graph is a **typed, bi-temporal** knowledge graph. Two axes, both MANDATORY (ADR-0003):

  valid_time  (valid_from, valid_to)   — when the fact was TRUE in the real world
  system_time (system_from, system_to) — when DataBridge LEARNED it

`*_to = None` means "still open" on that axis (still true / still believed). New conflicting
information **invalidates** a prior version (close its open `*_to`) rather than deleting it — so
"what did we believe on March 1" and "what was actually true on March 1" are both answerable.

Anti-fabrication (P4, the load-bearing invariant): **a node or edge with no grounded
SourcePointer is REFUSED at construction** — never stored. `sources` is non-empty by validator.

**And the same wall applies one level down, per attribute.** `sources` grounds the ENTITY — that
this patient, this document, this rule exists and was read from somewhere. It says nothing about
the individual values in `attrs`, and a review found sheets printing a patient's sex, nationality
and date of birth under a `Sources` heading listing only the name and ID pointers: one citation
appearing to vouch for values it had never seen. So `attr_sources` carries a pointer PER
ATTRIBUTE, and `_attrs_are_grounded` refuses construction if any attribute has a value but no
pointer of its own.

An attribute whose value is `None` is the honest way to say "not found", and needs no pointer —
there is nothing to ground. `unresolved` lists exactly those keys, so a renderer can show the
question that was asked and left unanswered rather than omitting it silently.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from databridge_core import SourcePointer


# --- closed vocabularies (ADR-0009; change ONLY via an ADR amendment) -------------
# The *principle* from ADR-0008 is unchanged and is the point: the vocabulary is CLOSED. An
# unrecognised kind is skipped, never coined on the fly — a graph that invents its own node types
# to fit incoming data cannot be reasoned about, and "what kinds of thing are in here?" stops
# having an answer. ADR-0009 replaces the members (the Acme Corp business-intelligence vocabulary)
# with the clinic's, and nothing else about the model.
class NodeKind(str, Enum):
    PATIENT = "patient"              # a registry row — never an identity assertion
    ORG = "org"                      # insurer, TPA, government scheme, employer
    PACKAGE = "package"              # a named screening package (WELL2, PEE226)
    TEST = "test"                    # a catalogue entry (chest_xray, resting_ecg)
    DOCUMENT = "document"            # a referring chit letter / voucher / authorisation form
    VISIT = "visit"                  # one intake occurrence
    CLINIC = "clinic"                # a Parkway Shenton location
    COVERAGE_RULE = "coverage_rule"  # one row of coverage_rules.yaml


class EdgeKind(str, Enum):
    EMPLOYED_BY = "employed_by"        # patient → org
    COVERED_BY = "covered_by"          # patient → org (the payer named on a document)
    ISSUED = "issued"                  # org → document
    AUTHORISES = "authorises"          # document → package / coverage_rule
    REQUESTS_TEST = "requests_test"    # document → test (printed on the document)
    INCLUDES_TEST = "includes_test"    # package → test (from the rule definition)
    FOR_PATIENT = "for_patient"        # document/visit → patient (CLAIMED subject, see below)
    AT_CLINIC = "at_clinic"            # visit → clinic
    SCHEDULED_AS = "scheduled_as"      # visit → package
    REFERENCES = "references"          # document → any node it cites


# `FOR_PATIENT` is the one edge worth a warning. It records that a document *claims* to be about a
# patient — it is built from the resolver's candidates, so a document with two equally-scored
# candidates yields TWO such edges, and neither is a determination. Nothing downstream may read a
# single FOR_PATIENT edge as "this is who the patient is" (CLAUDE.md §Hard constraints 1).


def _attrs_are_grounded(attrs: dict, attr_sources: dict, what: str) -> None:
    """The per-attribute grounding wall. Raises rather than storing an uncited value.

    Three rules, and the second is the one a reviewer found broken:

    1. a **non-None** attribute must carry its own pointer — the entity's `sources` do not vouch
       for it (CLAUDE.md §Hard constraints 2);
    2. a **None** attribute must NOT carry one — a pointer beside "not found" is a citation for
       nothing, and reads to both a person and the model as though the absence were established;
    3. a pointer may not name an attribute that is not there, which is how a value gets deleted
       while its citation stays behind and later attaches to a different value.
    """
    uncited = sorted(k for k, v in attrs.items() if v is not None and k not in attr_sources)
    if uncited:
        raise ValueError(
            f"{what} REFUSED: attribute(s) {uncited} carry a value with no SourcePointer of their "
            f"own. Ground each one, or set it to None and let `unresolved` name it "
            f"(anti-fabrication wall, P4)")
    cited_but_absent = sorted(k for k in attr_sources if k not in attrs)
    if cited_but_absent:
        raise ValueError(
            f"{what} REFUSED: pointer(s) for attribute(s) {cited_but_absent} that do not exist")
    cited_but_none = sorted(k for k in attr_sources if attrs.get(k) is None)
    if cited_but_none:
        raise ValueError(
            f"{what} REFUSED: attribute(s) {cited_but_none} are None but carry a SourcePointer — "
            f"a citation for an absent value")


class _Grounded(BaseModel):
    """Shared by GraphNode and GraphEdge: `unresolved`, and the per-attribute wall."""

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
    sources: list[SourcePointer] = Field(default_factory=list)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    system_from: datetime
    system_to: Optional[datetime] = None

    @field_validator("sources")
    @classmethod
    def _must_be_grounded(cls, v: list[SourcePointer]) -> list[SourcePointer]:
        # P4 / anti-fabrication wall: no SourcePointer ⇒ refused. This fires at CONSTRUCTION,
        # so a fabricated node can never reach the store. (04_PRD §3: every entity carries a
        # SourcePointer; len() is also the corroboration / quality signal.)
        if not v:
            raise ValueError("GraphNode REFUSED: no SourcePointer (anti-fabrication wall, P4)")
        return v

    @model_validator(mode="after")
    def _attrs_must_be_grounded(self) -> "GraphNode":
        _attrs_are_grounded(self.attrs, self.attr_sources, f"node {self.id!r}")
        return self


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
    sources: list[SourcePointer] = Field(default_factory=list)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    system_from: datetime
    system_to: Optional[datetime] = None

    @field_validator("sources")
    @classmethod
    def _must_be_grounded(cls, v: list[SourcePointer]) -> list[SourcePointer]:
        if not v:
            raise ValueError(
                "GraphEdge REFUSED: a relationship with no grounded SourcePointer is never "
                "stored (anti-fabrication wall, P4 / 04_PRD §3)")
        return v

    @model_validator(mode="after")
    def _attrs_must_be_grounded(self) -> "GraphEdge":
        _attrs_are_grounded(self.attrs, self.attr_sources, f"edge {self.id!r}")
        return self


def node_id(kind: NodeKind, slug: str) -> str:
    """Stable node id = ``f"{kind}:{normalised_slug}"`` (04_PRD §6 Stage-1 ER)."""
    return f"{kind.value}:{slug}"
