"""What the `/intelligence` surface is allowed to say — one place, applied to every payload.

`REMEDIATION.md` §3.1 gives this page one contract: **structural counts only, no candidate naming
or ranking, no minutes, no money.** Until now that contract was enforced by each producer
separately — the tools declined to return candidates, the queue summary returned a
`candidate_count` and no names — and a scoped review found the obvious consequence: the page's
*drill-down* had never been held to it. `/intelligence/subgraph/{id}` ran a generic depth-1
traversal, so a citation on a document walked its `for_patient` edges and returned registry patient
nodes, their `date_of_birth` / `sex` / `nationality` attributes, and the edge's own `match_score`
and `matched_on`. One click from a business-intelligence page to named, scored candidates is hard
constraint 1 broken on the surface judges look at.

So the rule moved here, and it is stated once for everything this page emits.

## Two decisions, and why they are the ones taken

**1. A dedicated structural traversal, not a filtered generic one.** `brain_neighbors` keeps
serving `/brain`, which has a different contract and a different audience. Filtering its *result*
for this consumer would be a subtractive control: it works by remembering to subtract, so every
node kind added to the vocabulary later is exposed by default and stays exposed until somebody
notices. :func:`structural_neighbors` is additive instead — it walks an **allow-list** of edge
kinds toward an **allow-list** of node kinds, so a new `NodeKind` is invisible on this page until a
human puts it in `LABEL_KINDS` or `HANDLE_KINDS` and writes down why. Default closed, in the
direction that costs a feature rather than a patient's name.

**2. Free text that came out of a document is never rendered here.** A seeded `DOCUMENT` label is
the supplied filename, so `Alice_Tan_referral.pdf` is an *ordinary* label — not injection-shaped,
not caught by any content screen, rendered unchanged, and repeated in the entity id and in every
nested pointer. The same is true of an `ORG` node minted from a chit's employer field and a
`CLINIC` node minted from its appointment-location line. Filtering content cannot fix this,
because the problem is not what the text says; it is where the text came from.

The rule is therefore about **provenance, not content**: a label may be printed only if every
pointer grounding it is a cell in this repo's own `coverage_rules.yaml`. Issuers, packages, tests
and coverage rules are catalogue entries somebody committed to git, and they print. Anything a
document put in the graph is reported as an **opaque handle** — stable, deterministic, and
drillable, because it resolves back through :func:`resolve` — plus, for a document, its
`document_type` from the closed `DocumentType` enum.

### What that trade costs, honestly

A completely opaque handle would be useless to staff, and this stops one step short of that: a row
reads `insurer_referral_letter · doc-6f21a0c3b4` rather than `Alice_Tan_referral.pdf`. A member of
staff on this page can see *what kind* of document is blocked, how many, from which issuer, and can
click through to the traversal — but **cannot read the filename, the employer printed on a chit, or
the appointment location**. To get those they open the intake on `/frontdesk` or `/intake`, where
the document is on screen, the person is at the counter, and the whole point of the surface is to
work one patient at a time. That is the right split: this page is for counting, and counting does
not need to name.

The handle is not a secret. It is a hash of an id somebody who can already read the graph could
compute; it protects the *page*, which is a screen visible across a front desk and quoted into
slides, not the database.

## Bounds that refuse rather than truncate

Every read here was unbounded: analytics materialises the full node and edge sets, the traversal
scanned every open edge, and `depth` limited hops but not degree or response size. Silent
truncation is the one degrade this page must never have — a truncated count is a **wrong number
presented as a right one**, and every figure on this page is a count. So the bounds refuse:
:func:`check_graph_bounds` at the door, and a result-size check inside the traversal, both raising
:class:`SurfaceTooLarge` which the routes turn into a refusal with a code.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import deque
from typing import Any, Iterable, Optional

from databridge_core import SourcePointer

# --- bounds -----------------------------------------------------------------------------
#: The envelope this surface is designed for: one clinic's graph. Beyond it the page refuses
#: rather than truncating, because a truncated count is a wrong number, not a partial one.
MAX_GRAPH_NODES = 5_000
MAX_GRAPH_EDGES = 20_000
#: One drill-down. `depth` bounds hops; these bound degree and response size, which it does not.
MAX_SUBGRAPH_NODES = 250
MAX_SUBGRAPH_EDGES = 750


class SurfaceTooLarge(RuntimeError):
    """A read that would exceed this surface's bounds. Carries staff-facing text and a code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- the vocabulary this page may render -------------------------------------------------
#: Node kinds whose label MAY be printed — subject to the provenance rule below. Everything in
#: this set is a catalogue entity: an organisation, a package, a test, a coverage rule.
LABEL_KINDS = frozenset({"org", "package", "test", "coverage_rule"})

#: Node kinds that exist on this page but are reported by an opaque handle, never by label.
HANDLE_KINDS = frozenset({"document", "visit", "clinic"})

#: Node kinds that do not appear on this page at all, in any form.
#:
#: A registry row is a *person*. This page reports how many candidates a document matched and that
#: a human settles it; it never names, orders, scores or links to one (CLAUDE.md §Hard constraints
#: 1). There is no handle for a patient either — a stable pseudonym is still a way to follow one
#: person across a page whose contract says it does not know who they are.
WITHHELD_KINDS = frozenset({"patient"})

#: Edge kinds the drill-down may traverse. `for_patient`, `employed_by` and `covered_by` are
#: absent: all three end at a registry row, and `for_patient` additionally carries `match_score`
#: and `matched_on` on the edge itself — a ranking, on an edge, one click from a page whose
#: contract forbids one.
STRUCTURAL_EDGE_KINDS = frozenset({
    "issued", "authorises", "requests_test", "includes_test", "at_clinic", "scheduled_as",
    "references"})

#: Source files whose cells this repo authored and committed. A label grounded entirely in one of
#: these is catalogue data, not document text, and prints.
REPO_CONFIG_SOURCES = frozenset({"coverage_rules.yaml"})

#: Pointer kinds that reach into something an outsider wrote.
DOCUMENT_POINTER_KINDS = frozenset({"doc_span", "ocr_bbox", "email_part", "transcript_span"})

_KIND_PREFIX = {"document": "doc", "org": "org", "visit": "vis", "clinic": "loc",
                "package": "pkg", "test": "tst", "coverage_rule": "rul"}
_HANDLE = re.compile(r"^[a-z]{3}-[0-9a-f]{10}$")
_HANDLE_CHARS = 10


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HANDLE_CHARS]


def entity_handle(entity_id: str) -> str:
    """A stable, opaque, kind-prefixed handle for a graph entity id.

    Deterministic and stateless on purpose: a handle in an answer must still resolve after a
    restart, and a server-side alias table would be one more thing to keep and to leak.
    """
    kind, sep, slug = (entity_id or "").partition(":")
    if not sep:
        kind, slug = "ref", entity_id or ""
    return f"{_KIND_PREFIX.get(kind, 'ent')}-{_digest(f'{kind}:{slug}')}"


def document_handle(document_id: str) -> str:
    """The handle a `doc_span` pointer's `document_id` gets — the SAME one its node gets.

    A citation whose document handle did not match the node handle it cites would read as two
    different documents, which is a worse failure than the leak this replaces.
    """
    return entity_handle(f"document:{document_id}")


def looks_like_handle(token: str) -> bool:
    return bool(_HANDLE.match(token or ""))


def _pointer_touches_a_document(pointer: SourcePointer) -> bool:
    """Does this pointer, or anything it derives from, reach into a document?"""
    if pointer.kind in DOCUMENT_POINTER_KINDS:
        return True
    children: list[SourcePointer] = list(pointer.derived_from or [])
    children.extend(pointer.facts or [])
    if pointer.child is not None:
        children.append(pointer.child)
    return any(_pointer_touches_a_document(child) for child in children)


def _pointer_is_repo_config(pointer: SourcePointer) -> bool:
    """Is every leaf of this pointer a cell in a config file this repo committed?"""
    if pointer.kind == "table_cell":
        return pointer.source_id in REPO_CONFIG_SOURCES
    children: list[SourcePointer] = list(pointer.derived_from or [])
    children.extend(pointer.facts or [])
    if pointer.child is not None:
        children.append(pointer.child)
    return bool(children) and all(_pointer_is_repo_config(child) for child in children)


class SurfaceNames:
    """The naming policy, memoised over one request's view of the graph.

    Built from the same three-method read-only view the agent's tools hold, so the policy cannot
    reach anything the tools cannot and there is no second path to the store.
    """

    def __init__(self, view: Any) -> None:
        self._view = view
        self._rows: Optional[dict[str, Any]] = None
        self._display: dict[str, tuple[Optional[str], bool]] = {}
        self._by_handle: Optional[dict[str, str]] = None

    # -- the node table ----------------------------------------------------------------
    def rows(self) -> dict[str, Any]:
        if self._rows is None:
            self._rows = {row["id"]: row for row in self._view.open_nodes()}
        return self._rows

    def kind_of(self, entity_id: str) -> Optional[str]:
        row = self.rows().get(entity_id)
        return None if row is None else row["kind"]

    def is_withheld(self, entity_id: str) -> bool:
        """True for anything this page does not report — including an unclassified node kind.

        The default matters more than the list: a `NodeKind` nobody has classified is withheld,
        so adding one to the vocabulary cannot quietly publish it here.
        """
        kind = self.kind_of(entity_id)
        if kind is None:
            return False              # not a node — a raw pointer id; handled by projection
        return kind in WITHHELD_KINDS or kind not in (LABEL_KINDS | HANDLE_KINDS)

    # -- naming ------------------------------------------------------------------------
    def public_id(self, entity_id: str) -> str:
        """The id this page uses for an entity: its own id when its label prints, else a handle."""
        text, withheld = self.display(entity_id)
        del text
        return entity_handle(entity_id) if withheld else entity_id

    def display(self, entity_id: str) -> tuple[Optional[str], bool]:
        """``(text, is_a_handle)`` — or ``(None, True)`` for an entity this page never reports."""
        if entity_id in self._display:
            return self._display[entity_id]
        result = self._compute_display(entity_id)
        self._display[entity_id] = result
        return result

    def _compute_display(self, entity_id: str) -> tuple[Optional[str], bool]:
        row = self.rows().get(entity_id)
        if row is None:
            return entity_handle(entity_id), True
        kind = row["kind"]
        if self.is_withheld(entity_id):
            return None, True
        handle = entity_handle(entity_id)
        if kind in LABEL_KINDS and self._label_is_repo_authored(entity_id):
            return row["label"], False
        if kind == "document":
            # A handle plus the one attribute that is a closed vocabulary rather than free text.
            # This is the whole of what a business-intelligence reader needs from a document, and
            # it is the most that can be said without repeating something an outsider wrote.
            document_type = self._closed_attr(row, "document_type")
            return (f"{document_type} · {handle}" if document_type else handle), True
        return handle, True

    def _label_is_repo_authored(self, entity_id: str) -> bool:
        try:
            pointers = list(self._view.sources_for(entity_id))
        except Exception:                                            # noqa: BLE001
            return False                                             # cannot prove it — withhold
        if not pointers:
            return False
        return all(_pointer_is_repo_config(p) and not _pointer_touches_a_document(p)
                   for p in pointers)

    @staticmethod
    def _closed_attr(row: Any, name: str) -> Optional[str]:
        """One node attribute, returned only if it is a member of a closed vocabulary.

        `document_type` is validated against the `DocumentType` enum itself rather than against a
        regex: "it looks like a snake_case token" is a guess about shape, and a guess about shape
        is how free text gets through.
        """
        import json

        from databridge_intake.models import DocumentType

        if name != "document_type":
            return None
        keys = set(row.keys())
        raw = row["attrs_json"] if "attrs_json" in keys else (
            row["attrs"] if "attrs" in keys else None)
        try:
            attrs = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            return None
        value = attrs.get(name) if isinstance(attrs, dict) else None
        try:
            return DocumentType(value).value
        except ValueError:
            return None

    # -- pointers ----------------------------------------------------------------------
    def project(self, pointer: SourcePointer) -> SourcePointer:
        """Project a pointer onto the closed shape this page publishes.

        A *projection*, never a redaction pass: a new pointer is built from an allow-list of
        fields, so a `SourcePointer` field added later cannot travel through here unnoticed. The
        alternative — blanking the fields known to be dangerous — publishes every field nobody has
        thought about yet, which is how `document_id` was reaching a browser in the first place.
        """
        if pointer.kind == "derived":
            return SourcePointer(
                source_id=self._pointer_id(pointer.source_id), kind="derived",
                derivation=pointer.derivation,
                derived_from=[self.project(p) for p in (pointer.derived_from or [])])
        if pointer.kind == "doc_span":
            return SourcePointer(
                source_id=document_handle(pointer.source_id), kind="doc_span",
                document_id=document_handle(pointer.document_id or pointer.source_id),
                char_start=pointer.char_start, char_end=pointer.char_end)
        if pointer.kind == "table_cell" and pointer.source_id in REPO_CONFIG_SOURCES:
            return pointer                     # a cell in a file in this repo; nothing to hide
        if pointer.kind == "table_cell":
            # Any other table is a data source — the patient registry among them — so the row is
            # named by handle. The table and column are ours and stay, because "which table" is
            # the part that makes the citation mean anything.
            return SourcePointer(
                source_id=entity_handle(pointer.source_id), kind="table_cell",
                table=pointer.table, column=pointer.column,
                row_id=_digest(str(pointer.row_id)))
        # Default closed. A pointer kind this surface has not classified is reported as the fact
        # that a pointer of that kind exists, and nothing else.
        return SourcePointer(
            source_id=entity_handle(pointer.source_id), kind="derived",
            derivation=f"a {pointer.kind} pointer; this surface publishes only that one exists")

    def _pointer_id(self, source_id: str) -> str:
        if source_id in REPO_CONFIG_SOURCES:
            return source_id
        if source_id in self.rows():
            return self.public_id(source_id)
        return entity_handle(source_id)

    # -- resolving a handle back to an entity ------------------------------------------
    def resolve(self, token: str) -> Optional[str]:
        """A handle or a raw entity id → the entity id, or ``None``.

        Raw ids are still accepted: a bookmark predating the handles, and `/brain`-shaped deep
        links, should keep working. Accepting one leaks nothing — the caller must already know the
        id, and what comes back is sanitised either way.
        """
        token = (token or "").strip()
        if not token:
            return None
        if token in self.rows():
            return token
        if not looks_like_handle(token):
            return None
        if self._by_handle is None:
            # Built once per request, not re-hashed per lookup: a drill-down resolves a handle for
            # every node it returns, and the bounds added alongside this exist precisely so that
            # the work here stays a known quantity.
            self._by_handle = {entity_handle(entity_id): entity_id for entity_id in self.rows()}
        return self._by_handle.get(token)


# --- bounds at the door -------------------------------------------------------------------
def check_graph_bounds(view: Any) -> None:
    """Refuse before any figure is computed if the graph is outside this surface's envelope.

    One loud check rather than five quiet ones. Analytics walks every node and edge, the tools'
    citation lists grow with the documents an issuer sent, and the traversal scans the edge table:
    all three are bounded by the graph, so bounding the graph bounds all three. Refusing here means
    the page never shows a number it had to cut down to fit.
    """
    nodes = len(view.open_nodes())
    edges = len(view.open_edges())
    if nodes > MAX_GRAPH_NODES or edges > MAX_GRAPH_EDGES:
        raise SurfaceTooLarge(
            "GRAPH_TOO_LARGE",
            f"this graph holds {nodes} nodes and {edges} relationships, beyond the "
            f"{MAX_GRAPH_NODES}/{MAX_GRAPH_EDGES} this page is built for. Every figure here is a "
            f"count, so a truncated answer would be a wrong number rather than a short one — it "
            f"is withheld instead.")


# --- the traversal ------------------------------------------------------------------------
def _valid_at(row: Any, as_of: Optional[dt.datetime]) -> bool:
    """The same validity rule `analytics.compute` applies, so a drill-down matches its figure."""
    if as_of is None:
        return True

    def parse(value: Optional[str]) -> Optional[dt.datetime]:
        if not value:
            return None
        parsed = dt.datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)

    valid_from, valid_to = parse(row["valid_from"]), parse(row["valid_to"])
    if valid_from and valid_from > as_of:
        return False
    if valid_to and valid_to <= as_of:
        return False
    return True


def structural_neighbors(view: Any, names: SurfaceNames, root_id: str, *, depth: int = 1,
                         as_of: Optional[dt.datetime] = None) -> dict:
    """The drill-down this page serves: structure only, no person, no ranking, bounded.

    Traverses an allow-list of edge kinds between nodes this page reports, so a registry row is not
    reachable from here at any depth and no edge attribute — `match_score` and `matched_on` among
    them — is emitted at all.
    """
    if names.is_withheld(root_id):
        raise SurfaceTooLarge(
            "NOT_ON_THIS_SURFACE",
            "that entity is a registry row. This page counts identity work and never performs "
            "any: it reports how many candidates a document matched, and a member of staff "
            "settles which one at the counter, in person.")

    edges = [row for row in view.open_edges()
             if row["kind"] in STRUCTURAL_EDGE_KINDS and _valid_at(row, as_of)]
    by_node: dict[str, list[Any]] = {}
    for row in edges:
        if names.is_withheld(row["src_id"]) or names.is_withheld(row["dst_id"]):
            continue
        by_node.setdefault(row["src_id"], []).append(row)
        by_node.setdefault(row["dst_id"], []).append(row)

    seen_nodes: set[str] = {root_id}
    seen_edges: set[str] = set()
    out_edges: list[Any] = []
    frontier: deque[tuple[str, int]] = deque([(root_id, 0)])
    while frontier:
        node_id, hops = frontier.popleft()
        if hops >= depth:
            continue
        for row in by_node.get(node_id, []):
            if row["id"] not in seen_edges:
                seen_edges.add(row["id"])
                out_edges.append(row)
            other = row["dst_id"] if row["src_id"] == node_id else row["src_id"]
            if other not in seen_nodes:
                seen_nodes.add(other)
                frontier.append((other, hops + 1))
        _check_subgraph_bounds(len(seen_nodes), len(out_edges), depth)
    _check_subgraph_bounds(len(seen_nodes), len(out_edges), depth)

    node_rows = [row for row in view.open_nodes()
                 if row["id"] in seen_nodes and _valid_at(row, as_of)]
    live = {row["id"] for row in node_rows}
    out_edges = [row for row in out_edges if row["src_id"] in live and row["dst_id"] in live]

    nodes = []
    for row in node_rows:
        text, _ = names.display(row["id"])
        if text is None:                       # unreachable via the allow-list; belt and braces
            continue
        nodes.append({
            "id": names.public_id(row["id"]),
            "kind": row["kind"],
            "label": text,
            # Only when the label itself prints. A document's canonical id IS the filename, which
            # is the thing this module exists to stop repeating.
            "canonical_id": row["canonical_id"] if names.public_id(row["id"]) == row["id"] else None,
            "valid_from": row["valid_from"],
            "n_sources": len(_sources(view, row["id"])),
        })
    return {
        "root": names.public_id(root_id),
        "nodes": nodes,
        # No `attrs`, on either side. A patient node's attributes are a date of birth and a
        # nationality; a `for_patient` edge's are a match score. Neither kind is reachable here
        # any more, and publishing attributes at all is how they were reachable before.
        "edges": [{
            "id": _digest(row["id"]),
            "source": names.public_id(row["src_id"]),
            "target": names.public_id(row["dst_id"]),
            "kind": row["kind"],
            "valid_from": row["valid_from"],
            "n_sources": len(_sources(view, row["id"])),
        } for row in out_edges],
    }


def _check_subgraph_bounds(nodes: int, edges: int, depth: int) -> None:
    if nodes > MAX_SUBGRAPH_NODES or edges > MAX_SUBGRAPH_EDGES:
        raise SurfaceTooLarge(
            "SUBGRAPH_TOO_LARGE",
            f"this traversal reaches more than {MAX_SUBGRAPH_NODES} entities or "
            f"{MAX_SUBGRAPH_EDGES} relationships at depth {depth}. Showing part of it would look "
            f"like the whole of it, so it is refused — ask for depth 1.")


def _sources(view: Any, entity_id: str) -> list[SourcePointer]:
    return list(view.sources_for(entity_id))


def subgraph_sources(view: Any, names: SurfaceNames, subgraph: dict) -> dict[str, list[dict]]:
    """The projected pointers behind every node in a traversal, keyed by its public id."""
    out: dict[str, list[dict]] = {}
    for node in subgraph["nodes"]:
        entity_id = names.resolve(node["id"]) or node["id"]
        out[node["id"]] = [names.project(p).model_dump(mode="json", exclude_none=True)
                           for p in _sources(view, entity_id)]
    return out


# --- the figures --------------------------------------------------------------------------
#: Why a figure can be counted but not listed. Stated in the payload, not only in the UI, so an
#: agent reading this over MCP inherits the reason rather than inferring a bug.
MEMBERS_WITHHELD = ("this figure counts registry rows; listing them would name patients, so the "
                    "count is reported without its members (CLAUDE.md hard constraint 1)")


def sanitise_analytics(payload: dict, names: SurfaceNames) -> dict:
    """Rewrite `analytics.compute`'s payload into what this page may render.

    Applied to the whole payload rather than to the figures known to be risky. `conflicts` counts
    *registry rows*, so its `members` are patient node ids and its `entities` entries are patient
    names — and the page rendered them, because "every figure names the ids it counted" was the
    only rule in play. Both rules are real; when they collide, constraint 1 wins and the collision
    is announced (`members_withheld`) rather than papered over with an empty list.

    `patients` joined that list when it stopped being a bare scalar. It used to pass through here
    untouched simply because it was an `int` with nothing to sanitise — and an `int` was the
    problem, since it silently changed meaning ("seen here" → "on file") the moment the seed grew
    to the whole registry. It is now a `Count` whose members are patient node ids, so it needs the
    same withholding as `conflicts`, for the same reason.
    """
    out = dict(payload)

    def public(ids: Iterable[str]) -> tuple[list[str], int]:
        shown, withheld = [], 0
        for entity_id in ids:
            if names.is_withheld(entity_id):
                withheld += 1
                continue
            shown.append(names.public_id(entity_id))
        return shown, withheld

    for key in ("patients", "ambiguity", "unlinked_documents", "conflicts"):
        figure = out.get(key)
        if not isinstance(figure, dict):
            continue
        shown, withheld = public(figure.get("members") or [])
        figure = dict(figure, members=shown)
        if withheld:
            figure["members_withheld"] = MEMBERS_WITHHELD
        out[key] = figure

    mix = []
    for row in out.get("issuer_mix") or []:
        text, _ = names.display(row["issuer_id"])
        if text is None:
            continue
        members, _withheld = public(row.get("members") or [])
        mix.append(dict(row, issuer_id=names.public_id(row["issuer_id"]), issuer=text,
                        members=members))
    out["issuer_mix"] = mix

    orphans = []
    for entity in out.get("tests_outside_any_package") or []:
        text, _ = names.display(entity["id"])
        if text is None:
            continue
        orphans.append(dict(entity, id=names.public_id(entity["id"]), label=text))
    out["tests_outside_any_package"] = orphans

    entities = {}
    for entity_id, entity in (out.get("entities") or {}).items():
        text, _ = names.display(entity_id)
        if text is None:
            continue
        public_id = names.public_id(entity_id)
        entities[public_id] = dict(entity, id=public_id, label=text)
    out["entities"] = entities
    return out


__all__ = [
    "DOCUMENT_POINTER_KINDS",
    "HANDLE_KINDS",
    "LABEL_KINDS",
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_NODES",
    "MAX_SUBGRAPH_EDGES",
    "MAX_SUBGRAPH_NODES",
    "MEMBERS_WITHHELD",
    "REPO_CONFIG_SOURCES",
    "STRUCTURAL_EDGE_KINDS",
    "SurfaceNames",
    "SurfaceTooLarge",
    "WITHHELD_KINDS",
    "check_graph_bounds",
    "document_handle",
    "entity_handle",
    "looks_like_handle",
    "sanitise_analytics",
    "structural_neighbors",
    "subgraph_sources",
]
