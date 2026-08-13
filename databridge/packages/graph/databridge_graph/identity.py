"""The identity caveat, worded once — and the rule that a NAME never identifies a person.

Two things live here because they are the same constraint (CLAUDE.md §Hard constraints 1) reached
from two directions, and because keeping them in one module is what stops either drifting:

* **the caveat** every surface must attach to a patient answer, sheet or tool result, and
* **``same_label_node_ids`` / ``ambiguous_refusal``** — the refusal to pick one of several nodes
  that print the same label.

## Why this module exists at all

Both rules were implemented, and both were implemented *on the edge path*. ``query.nl_query``
emitted its caveat inside the loop over incoming ``FOR_PATIENT`` edges, and ``node_sheet``
rendered its caveat block only ``if claims``. That was invisible while the clinic seed held only
the ~11 patients some document names, because then every patient node had such an edge. Seeding
the whole registry (``clinic_seed``, 300 rows) made 289 patients edge-less, and those 289 answered
with a bare name and a date of birth — no caveat anywhere.

**Registry presence is not identification.** A row on file says the clinic has heard of somebody
by that name; it says nothing about who is standing at the counter. So the caveat is now attached
to the PATIENT KIND, not to the presence of a document claim, and there is no branch that can drop
it.

## Why the name rule is here too

The registry holds three people called "Loh Wei Ming", four "Rahman Zhi Hao" and four "Tan Mei
Ling" (see ``merge.py``, which refuses to fuzzy-merge patients for exactly this reason). A lookup
by name that returns ONE of them has decided which person the caller meant — silently, by SQLite
rowid order or by whichever scored first among BM25 equals, with nothing in the response saying a
choice was made. That is the system picking between people who share a name, which is the
determination reserved for a human, in person.

The refusal is not a dead end: it hands back the candidate ids, and asking again with an id works,
because an id is an identifier and a caller who has one has already made the choice this layer
will not make for them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: Attached to a patient answer a document DOES name. A claim on a letter, and nothing more.
IDENTITY_CLAIMED_ON_DOCUMENT = (
    "A document naming a patient is a claim on the document, not a verified identity; "
    "staff confirm identity in person.")

#: Attached to a patient the clinic merely holds on file. This is the wording the 289 unreferenced
#: registry rows get, and the reason this module exists.
IDENTITY_REGISTRY_ONLY = (
    "No document on file names this patient; this is a registry row, not a verified identity. "
    "Names are not unique in the registry, and staff confirm identity in person.")


def identity_caveat(*, claimed: bool) -> str:
    """The caveat for a patient, whether or not any document names them. Never ``""``."""
    return IDENTITY_CLAIMED_ON_DOCUMENT if claimed else IDENTITY_REGISTRY_ONLY


def same_label_node_ids(store, node_id: str) -> list[str]:
    """Every open node of the same kind carrying the same label as ``node_id`` — itself included.

    Length > 1 means a NAME does not identify this node.

    Scoped to the same ``kind`` on purpose: an ORG and a TEST that happen to print the same string
    are not rival answers to "who is this person", and refusing them would be noise.
    """
    row = store.open_node_row(node_id)
    if row is None:
        return []
    label = (row["label"] or "").strip().lower()
    if not label:
        return [node_id]
    return sorted(r["id"] for r in store.open_nodes()
                  if r["kind"] == row["kind"]
                  and (r["label"] or "").strip().lower() == label)


def ambiguous_refusal(ref: str, candidates: list[str] | tuple[str, ...]) -> str:
    """The one wording for "this names more than one entity and I will not choose".

    It names the way forward, because a refusal without one is just a dead end.
    """
    return (f"{ref!r} matches {len(candidates)} entities and this system will not choose "
            f"between them: people share names, and picking one would be an identity "
            f"determination (CLAUDE.md constraint 1). Ask again with one of the ids in "
            f"`candidates`.")


@dataclass(frozen=True)
class EntityMatch:
    """The outcome of resolving a caller's reference to a node. THREE outcomes, not two.

    ``resolved`` (``node_id`` set), ``not found``, and — the one this type exists for —
    **``ambiguous``**: the reference names more than one node, and this layer refuses to pick.
    ``candidates`` carries the ids so the caller can ask again unambiguously.
    """

    node_id: Optional[str] = None
    error: Optional[str] = None
    candidates: tuple[str, ...] = ()

    @classmethod
    def ambiguous(cls, ref: str, candidates: list[str] | tuple[str, ...]) -> "EntityMatch":
        return cls(error=ambiguous_refusal(ref, candidates), candidates=tuple(candidates))


def resolve_entity(store, ref: str) -> EntityMatch:
    """Accept a node id OR a natural-language name; return an :class:`EntityMatch`.

    ``link_entities`` deliberately skips DOCUMENT nodes — they are provenance, never the answer
    entity — so a document referred to by filename would otherwise fall through to whichever
    unrelated node happened to share a token. Documents are therefore resolved by an explicit
    exact lookup on label / canonical_id first, which is what makes ``brain_neighbors("chit_01…")``
    work at all.

    **A MISSPELLED filename is still a known gap.** It matches nothing exactly, so it reaches
    ``link_entities`` like any other free text and can come back as some node that shares a token.
    There is no reliable "this string is a filename" test to guard it with, and inventing one
    would refuse legitimate questions; the honest thing is to say so here rather than to claim a
    guard the code does not implement.

    **A node id always wins and is never ambiguous** — it is an identifier, and a caller who has
    one has already made the choice this function will not make for them. That is also what keeps
    the ambiguity check from being a dead end: the refusal hands back ids, and asking again with
    one of them works.

    The ambiguity check is applied to the RESULT, not to the exact-match branch alone, because the
    ranker cannot tell duplicates apart either — with four identically labelled nodes,
    ``link_entities(..., top_k=1)`` returns whichever scored first among equals, which is the same
    silent choice by a longer route.
    """
    from .query import link_entities            # local: query imports this module

    if store.open_node_row(ref) is not None:
        return EntityMatch(node_id=ref)
    needle = ref.strip().lower()
    exact = sorted(row["id"] for row in store.open_nodes()
                   if (row["label"] or "").strip().lower() == needle
                   or (row["canonical_id"] or "").strip().lower() == needle)
    if len(exact) > 1:
        return EntityMatch.ambiguous(ref, exact)
    if exact:
        candidate = exact[0]
    else:
        linked = link_entities(store, ref, top_k=1)
        if not linked:
            return EntityMatch(error=f"no entity matched {ref!r}")
        candidate = linked[0].id
    shared = same_label_node_ids(store, candidate)
    if len(shared) > 1:
        return EntityMatch.ambiguous(ref, shared)
    return EntityMatch(node_id=candidate)


__all__ = [
    "EntityMatch",
    "IDENTITY_CLAIMED_ON_DOCUMENT",
    "IDENTITY_REGISTRY_ONLY",
    "ambiguous_refusal",
    "identity_caveat",
    "resolve_entity",
    "same_label_node_ids",
]
