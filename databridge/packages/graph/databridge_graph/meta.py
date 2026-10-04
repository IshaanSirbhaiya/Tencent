"""Layer 0 of the three-layer schema (`00_SPINE.md` §4) — the **domain-free meta-model**.

Three layers, and the whole point is that only the middle one knows any vocabulary:

1. **meta-model** (this file) — node/edge meta-kinds with *no* domain semantics. It is closed,
   small, and changes only by ADR. Nothing here names an industry, a document type or a role.
2. **pack** (`packs/<name>/ontology.yaml`) — a vertical's concrete kinds, each one *binding to* a
   meta-kind, plus its `subject_kinds` (spine D4b). Adding an industry is adding a directory.
3. **deployment binding** (`packs/deployment.yaml`) — which packs are enabled. Extraction is
   closed to the union of the enabled packs' kinds; a kind no enabled pack declares is skipped,
   never coined.

The meta-kinds answer "what *shape* of thing is this?" so that engine code can reason about a
graph it has never seen the vocabulary for. A resolver does not need to know what a pack calls
the entity it deals in; it needs to know the pack declared that kind a *subject*, and that
subjects are never fuzzy-merged.

Why these members and not others: each is a shape the engine itself has behaviour for.

  ENTITY            a thing with an identity that persists across documents
  DOCUMENT          a captured artefact — the thing pointers resolve into
  EVENT             something that happened at a time (OCEL 2.0 export hangs off this)
  ORGANISATION      a legal/collective actor, distinct from ENTITY because merge rules differ
  LOCATION          a place; the `AT` meta-edge's usual destination
  AGREEMENT         a binding arrangement between parties — what two of them agreed
  RULE              a declarative row that decides something — config as a graph object
  ACTION_TYPE       a declared write path (spine: actions are the only write path)
  AGENT             an agent as an ontology object (spine D10)
  ADAPTER           a source connector as an ontology object
  CAPABILITY_GRANT  a minted capability (spine D11) — what an attestation produces

Edge meta-kinds are deliberately fewer than any pack will want, because an edge meta-kind exists
only where the engine treats the relationship differently:

  REFERENCES    a cites b, with no stronger claim
  PART_OF       containment / composition
  PARTY_TO      an actor participates in an agreement
  ISSUED        an actor produced an artefact
  AUTHORISES    an artefact allows a thing
  ABOUT         an artefact CLAIMS to concern an entity — a claim, never a determination
  AT            something happened at a location
  DERIVED_FROM  b was computed from a (provenance within the graph)
  SUPERSEDES    b replaces a (bi-temporal invalidation's graph-level twin)
  RESOLVED_TO   a candidate was selected — only ever written after a human attestation
  ROLE_OF       an entity holds a role with respect to another

`ABOUT` and `RESOLVED_TO` are the two that carry a hard constraint. `ABOUT` is a *claim*: an
artefact asserting it concerns some entity. Nothing downstream may read a single `ABOUT` edge as
a determination of who the entity is — a document with two equally-scored candidates yields two
`ABOUT` edges and neither is an answer. `RESOLVED_TO` is the determination, and it is written
only on the far side of a named human's attestation (CLAUDE.md §Hard constraints 1).
"""
from __future__ import annotations

from enum import Enum


class NodeMetaKind(str, Enum):
    """The shape of a node, independent of any domain. Closed; extend only by ADR."""

    ENTITY = "entity"
    DOCUMENT = "document"
    EVENT = "event"
    ORGANISATION = "organisation"
    LOCATION = "location"
    AGREEMENT = "agreement"
    RULE = "rule"
    ACTION_TYPE = "action_type"
    AGENT = "agent"
    ADAPTER = "adapter"
    CAPABILITY_GRANT = "capability_grant"


class EdgeMetaKind(str, Enum):
    """The shape of a relationship, independent of any domain. Closed; extend only by ADR."""

    REFERENCES = "references"
    PART_OF = "part_of"
    PARTY_TO = "party_to"
    ISSUED = "issued"
    AUTHORISES = "authorises"
    ABOUT = "about"
    AT = "at"
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"
    RESOLVED_TO = "resolved_to"
    ROLE_OF = "role_of"


#: Meta-kinds a pack may nominate as a *subject* kind (spine D4b). A subject is the entity whose
#: identity the system may never auto-confirm. Only entity-shaped things can be subjects — an
#: AGREEMENT or a RULE has no identity to attest to, and a pack declaring one as a subject is
#: declaring something the engine cannot honour, so the loader refuses it.
SUBJECT_ELIGIBLE_META_KINDS = frozenset({NodeMetaKind.ENTITY, NodeMetaKind.ORGANISATION})

#: E2E run 5 F-04: the STRUCTURAL mark of a natural person, read from a pack's own declarations
#: and never from a kind's name. A kind is person-like when it binds to the ENTITY meta-kind (an
#: organisation binds to ORGANISATION) and the pack names it, explicitly, as the ACTOR of an edge
#: of one of these meta-kinds: it holds a role with respect to something (ROLE_OF) or it produced
#: an artefact (ISSUED). A thing that holds a role and writes letters, and is not an organisation,
#: is a human. Such a kind is never merged by name, similarity or prefix in ANY pack, declared a
#: subject or not (`Vocabulary.person_kinds`), and a pack may not declare an identity rule for it.
PERSON_META_KIND = NodeMetaKind.ENTITY
PERSON_ACTOR_META_EDGES = frozenset({EdgeMetaKind.ROLE_OF, EdgeMetaKind.ISSUED})

#: Meta-edges that record a CLAIM about which entity an artefact concerns. Every surface that
#: renders one must attach the caveat that it is a claim; see `identity.py`.
CLAIM_META_KINDS = frozenset({EdgeMetaKind.ABOUT})

#: Meta-edges that record a DETERMINATION of identity. Writable only behind a human attestation.
ATTESTED_META_KINDS = frozenset({EdgeMetaKind.RESOLVED_TO})


__all__ = [
    "NodeMetaKind",
    "EdgeMetaKind",
    "SUBJECT_ELIGIBLE_META_KINDS",
    "CLAIM_META_KINDS",
    "ATTESTED_META_KINDS",
]
