"""Clinic knowledge-graph schema (closed vocab frozen in ADR-0009, superseding ADR-0008).

The graph is a **typed, bi-temporal** knowledge graph. Two axes, both MANDATORY (ADR-0003):

  valid_time  (valid_from, valid_to)   — when the fact was TRUE in the real world
  system_time (system_from, system_to) — when DataBridge LEARNED it

`*_to = None` means "still open" on that axis (still true / still believed). New conflicting
information **invalidates** a prior version (close its open `*_to`) rather than deleting it — so
"what did we believe on March 1" and "what was actually true on March 1" are both answerable.

Anti-fabrication (P4, the load-bearing invariant): **a node or edge with no grounded
SourcePointer is REFUSED at construction** — never stored. `sources` is non-empty by validator.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# --- in-memory record shapes (the store persists these to SQLite) -----------------
class GraphNode(BaseModel):
    """One versioned node. ``id`` is stable across versions (``f"{kind}:{slug}"``); each
    bi-temporal version is a distinct row in ``nodes`` sharing this ``id``."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    label: str
    canonical_id: Optional[str] = None       # email / domain / employee-id — exact-merge key
    attrs: dict = Field(default_factory=dict)
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


class GraphEdge(BaseModel):
    """One versioned, typed, bi-temporal relationship. Grounded like a node (P4)."""

    model_config = ConfigDict(frozen=True)

    id: str
    src_id: str
    dst_id: str
    kind: EdgeKind
    weight: float = 1.0
    attrs: dict = Field(default_factory=dict)
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


def node_id(kind: NodeKind, slug: str) -> str:
    """Stable node id = ``f"{kind}:{normalised_slug}"`` (04_PRD §6 Stage-1 ER)."""
    return f"{kind.value}:{slug}"
